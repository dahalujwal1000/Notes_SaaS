"""Auth routes: signup, login, current user, and email verification."""

import logging
import os
import secrets
import urllib.parse
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

import auth
import mailer
import models
import ratelimit
import schemas
from auth import create_access_token, get_current_user, hash_password, verify_password
from database import get_db

logger = logging.getLogger("users")

router = APIRouter(prefix="/auth", tags=["auth"])

DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[models.User, Depends(get_current_user)]


def email_verification_required() -> bool:
    """Whether login is gated on a verified email (default: yes).

    Set EMAIL_VERIFICATION_REQUIRED=false only for local/sandbox testing
    where you can't (or don't want to) open real emails.
    """
    return os.environ.get("EMAIL_VERIFICATION_REQUIRED", "true").strip().lower() in (
        "1", "true", "yes", "on",
    )


# ---------------------------------------------------------------------------
# Throttles & brute-force protection — all DB-backed (see ratelimit.py) so
# limits survive restarts and hold across multiple workers.
# ---------------------------------------------------------------------------
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 900  # 15 minutes
SIGNUP_IP_LIMIT_PER_HOUR = 10
FORGOT_IP_LIMIT_PER_HOUR = 10

EMAIL_THROTTLE_SECONDS = 60  # at most one verification/reset email per minute


def _throttle_resend(db: Session, email: str) -> None:
    """At most one verification email per address per minute."""
    if ratelimit.check_and_record(
        db, f"resend:{email.lower()}", 1, EMAIL_THROTTLE_SECONDS
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Please wait a minute before requesting another email",
        )


def _login_attempt_allowed(db: Session, email: str, ip: str) -> bool:
    """True while neither the email nor the IP has exceeded the failure limit."""
    return (
        ratelimit.count_events(db, f"login:email:{email.lower()}", LOGIN_WINDOW_SECONDS)
        < LOGIN_MAX_ATTEMPTS
        and ratelimit.count_events(db, f"login:ip:{ip}", LOGIN_WINDOW_SECONDS)
        < LOGIN_MAX_ATTEMPTS
    )


def _record_login_failure(db: Session, email: str, ip: str) -> None:
    """Count one failed credential check for the email and the IP."""
    ratelimit.record(db, f"login:email:{email.lower()}")
    ratelimit.record(db, f"login:ip:{ip}")


def _reset_login_failures(db: Session, email: str, ip: str) -> None:
    """Clear the failure buckets on a successful login."""
    ratelimit.reset(db, f"login:email:{email.lower()}")
    ratelimit.reset(db, f"login:ip:{ip}")


def _signup_ip_allowed(db: Session, ip: str) -> bool:
    """At most SIGNUP_IP_LIMIT_PER_HOUR new signups per IP — blocks using the
    signup form as a mail-bomb across arbitrary addresses."""
    return not ratelimit.check_and_record(
        db, f"signup:ip:{ip}", SIGNUP_IP_LIMIT_PER_HOUR, 3600
    )


def _forgot_ip_allowed(db: Session, ip: str) -> bool:
    """At most FORGOT_IP_LIMIT_PER_HOUR reset requests per IP."""
    return not ratelimit.check_and_record(
        db, f"forgot:ip:{ip}", FORGOT_IP_LIMIT_PER_HOUR, 3600
    )


def _throttle_reset(db: Session, email: str, ip: str) -> None:
    """At most one reset email per address per minute, and per-IP hourly cap."""
    if ratelimit.check_and_record(
        db, f"reset:{email.lower()}", 1, EMAIL_THROTTLE_SECONDS
    ) or not _forgot_ip_allowed(db, ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Please wait a minute before requesting another email",
        )


@router.post(
    "/signup",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user account (non-enumerating)",
)
def signup(user_in: schemas.UserCreate, request: Request, db: DbSession) -> dict:
    """Register a user (unverified) and email a one-time verification link.

    Non-enumerating: the response is identical — same 201 + same message —
    whether the address is new, already registered (unverified), or already
    verified. A caller cannot learn whether an email has an account.
    """
    existing_user = db.query(models.User).filter(models.User.email == user_in.email).first()
    ip = request.client.host if request.client else "unknown"
    # Per-IP cap so the signup form can't be used as a mail-bomb: once the IP
    # has created enough accounts this hour, we stop emailing (still 201, so
    # the non-enumerating contract and the attacker's visibility are unchanged).
    if not _signup_ip_allowed(db, ip):
        logger.warning("signup mail-bomb guard: IP throttled %s", ip)
    elif existing_user is None:
        user = models.User(
            email=user_in.email, hashed_password=hash_password(user_in.password)
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        try:
            auth.send_verification_link(user, db, str(request.base_url))
        except Exception:
            # Never block signup because email delivery failed (e.g. no mail
            # configured, SMTP down) — the user can resend from the dashboard.
            logger.exception("failed to send verification email to %s", user.email)
    elif not existing_user.is_verified:
        # Account already exists but is unverified: resend the link, throttled
        # so a repeating signup can't be used to spam the inbox. A throttle
        # hit is swallowed — the generic message keeps the endpoint
        # non-enumerating and the attacker learns nothing.
        try:
            _throttle_resend(db, existing_user.email)
            auth.send_verification_link(existing_user, db, str(request.base_url))
        except HTTPException:
            pass
        except Exception:
            logger.exception(
                "failed to resend verification email to %s", existing_user.email
            )
    # Verified accounts: nothing to do — same 201 to everyone.

    return {
        "message": "If that address isn't already registered, a verification link is on its way."
    }


@router.post("/login", response_model=schemas.Token, summary="Log in and receive a JWT")
def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    request: Request,
    db: DbSession,
) -> schemas.Token:
    """Verify credentials (form-encoded: username=email, password) and
    return a JWT access token whose `sub` claim is the user id.

    Hard gate: unverified accounts cannot log in — they must click the
    verification link we emailed them first (random emails stay out).

    Brute-force protection: after LOGIN_MAX_ATTEMPTS bad-password tries from
    one email or one IP inside the window, further attempts get a 429.
    """
    ip = request.client.host if request.client else "unknown"
    if not _login_attempt_allowed(db, form_data.username, ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed sign-in attempts — please wait a few minutes and try again.",
        )

    user = db.query(models.User).filter(models.User.email == form_data.username).first()
    if user is None or not verify_password(form_data.password, user.hashed_password):
        _record_login_failure(db, form_data.username, ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if email_verification_required() and not user.is_verified:
        # A known correct password for an unverified account is not a
        # credential failure — don't let it count toward the lockout.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email not verified. Click the verification link we emailed you.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    _reset_login_failures(db, form_data.username, ip)
    return schemas.Token(access_token=create_access_token(str(user.id)))


@router.get("/google/login", summary="Start Sign in with Google (redirects to Google)")
def google_login(request: Request) -> RedirectResponse:
    """Redirect the browser to Google's consent screen.

    Requires GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET env vars (free from
    Google Cloud Console). Until configured, returns a clear 503 instead of
    a broken redirect.

    Login-CSRF protection: a random `state` nonce is stored in an HttpOnly
    cookie, and the same value is embedded in the Google URL. The callback
    compares Google's echo against the cookie before signing anyone in.
    """
    if not auth.google_oauth_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Google sign-in isn't configured yet. Set GOOGLE_CLIENT_ID and "
                "GOOGLE_CLIENT_SECRET (see README: Google Cloud Console)."
            ),
        )
    state = secrets.token_urlsafe(16)
    response = RedirectResponse(
        auth.google_authorize_url(str(request.base_url), state=state)
    )
    response.set_cookie(
        auth.GOOGLE_OAUTH_STATE_COOKIE,
        state,
        max_age=600,  # 10 minutes — the OAuth round-trip should take seconds
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )
    return response


@router.get("/google/callback", summary="Google OAuth callback (issues a one-time sign-in code)")
def google_callback(
    request: Request,
    db: DbSession,
    code: str = "",
) -> object:
    """Handle Google's redirect after the user consents.

    Exchanges the `code` for Google's *verified* profile, then either finds or
    creates the user (marked is_verified=True — Google already verified the
    email, so no verification email is needed).

    Security:
      - `state` nonce from the login redirect is matched against the HttpOnly
        cookie, blocking login CSRF.
      - No JWT ever appears in the URL: the browser gets a single-use,
        2-minute `google_code` and redeems it for a JWT by POSTing to
        /auth/google/exchange.
    """
    def _bounce(error_flag: str) -> RedirectResponse:
        response = RedirectResponse(f"/?error={error_flag}")
        response.delete_cookie(auth.GOOGLE_OAUTH_STATE_COOKIE, path="/")
        return response

    error = request.query_params.get("error")
    if error or not code:
        logger.info("google oauth callback failed: error=%r code=%r", error, code[:6])
        return _bounce("google-auth-denied")

    # CSRF: the state Google echoes back must match the cookie we set.
    state_param = request.query_params.get("state", "")
    state_cookie = request.cookies.get(auth.GOOGLE_OAUTH_STATE_COOKIE, "")
    if not state_param or not state_cookie or not secrets.compare_digest(
        state_cookie, state_param
    ):
        logger.warning("google oauth callback: state mismatch — login CSRF attempt")
        return _bounce("google-auth-failed")

    try:
        profile = auth.google_fetch_profile(code, str(request.base_url))
    except Exception:
        logger.exception("google oauth token exchange failed")
        return _bounce("google-auth-failed")

    email = (profile.get("email") or "").strip().lower()
    if not email or not profile.get("verified_email"):
        logger.warning("google oauth: unverified/unusable email %r", email)
        return _bounce("google-email-not-verified")

    user = db.query(models.User).filter(models.User.email == email).first()
    if user is None:
        # Google already proved ownership of this email — so is_verified=True.
        # hashed_password is a random unrecoverable hash: password login is
        # impossible for a Google-created account (by design, no password).
        user = models.User(
            email=email,
            hashed_password=hash_password(secrets.token_urlsafe(32)),
            is_verified=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        if not user.is_verified:
            user.is_verified = True
            db.commit()

    exchange_code = auth.issue_google_exchange(user.id)
    query = urllib.parse.urlencode({"google_code": exchange_code, "email": email})
    response = RedirectResponse(f"/?{query}")
    response.delete_cookie(auth.GOOGLE_OAUTH_STATE_COOKIE, path="/")
    return response


@router.post(
    "/google/exchange",
    response_model=schemas.Token,
    summary="Redeem a one-time Google sign-in code for an app JWT",
)
def google_exchange(body: schemas.GoogleExchange) -> schemas.Token:
    """Exchange the single-use code from the OAuth callback redirect for a
    real JWT. The code is burned on first use and expires after 2 minutes,
    so it is safe to pass around in the URL.
    """
    user_id = auth.consume_google_exchange(body.code)
    return schemas.Token(access_token=create_access_token(str(user_id)))


@router.get("/me", response_model=schemas.UserOut, summary="Current user profile")
def me(current_user: CurrentUser) -> models.User:
    """Who am I? Used by the UI to show verification state."""
    return current_user


@router.post("/verify", response_model=schemas.UserOut, summary="Verify email with a token")
def verify_email(body: schemas.VerifyEmail, db: DbSession) -> models.User:
    """Validate the emailed token and mark the account verified."""
    return auth.verify_email_token(body.token, db)


@router.post("/resend-verification", response_model=dict, summary="Email a new verification link")
def resend_verification(
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
) -> dict:
    """Send a fresh verification link to the signed-in (unverified) user,
    throttled to once per minute.
    """
    if current_user.is_verified:
        return {"message": "Email already verified"}

    _throttle_resend(db, current_user.email)
    try:
        auth.send_verification_link(current_user, db, str(request.base_url))
    except Exception:
        logger.exception("failed to resend verification email to %s", current_user.email)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not send the verification email right now",
        ) from None
    return {"message": "Verification email sent"}


@router.post(
    "/resend-verification-email",
    response_model=dict,
    summary="Re-send a verification link (public, by email)",
)
def resend_verification_email_pub(
    request: Request,
    db: DbSession,
    body: schemas.ResendVerificationEmail,
) -> dict:
    """Public variant of resend-verification for the *login screen*, where
    an unverified user cannot get a token yet (login is hard-gated).

    Non-enumerating: identical response for a missing, verified, or
    unverified address. Throttled so an attacker can't spam an inbox.
    """
    user = db.query(models.User).filter(models.User.email == body.email).first()
    if user is not None and not user.is_verified:
        _throttle_resend(db, user.email)
        try:
            auth.send_verification_link(user, db, str(request.base_url))
        except Exception:
            logger.exception("failed to resend verification email to %s", user.email)
    return {"message": "If that email exists and is unverified, a new link has been sent"}


@router.post("/forgot-password", response_model=dict, summary="Email a password-reset link")
def forgot_password(
    request: Request,
    db: DbSession,
    body: schemas.ForgotPassword,
) -> dict:
    """Send a one-time, single-use password-reset link to the given email.

    Always returns the same success message whether or not the email is
    registered — prevents account-enumeration via this endpoint.
    """
    user = db.query(models.User).filter(models.User.email == body.email).first()
    ip = request.client.host if request.client else "unknown"
    if user is not None:
        # Throttled (once per minute per address, plus a per-IP hourly cap) so
        # the endpoint can't be used to flood a victim's inbox. A throttle hit
        # is swallowed and the caller still gets the generic message — staying
        # non-enumerating.
        try:
            _throttle_reset(db, user.email, ip)
            token = auth.issue_reset_token(user, db)
            reset_url = f"{request.base_url!s}reset-password.html?token={token}"
            mailer.send_password_reset_email(user.email, reset_url)
        except HTTPException:
            pass
        except Exception:
            logger.exception("failed to send password-reset email to %s", user.email)
    return {"message": "If that email exists, a reset link has been sent"}


@router.post("/reset-password", response_model=dict, summary="Reset password using a token")
def reset_password(
    db: DbSession,
    body: schemas.ResetPassword,
) -> dict:
    """Submit a new password + reset token to change the user's password.

    The token is single-use and expires in 24 hours.
    """
    auth.reset_password_with_token(body.token, body.password, db)
    return {"message": "Password updated. You can now log in."}
