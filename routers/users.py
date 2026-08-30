"""Auth routes: signup, login, current user, and email verification."""

import logging
import os
import time
import urllib.parse
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

import auth
import mailer
import models
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


# Simple in-memory resend throttle: at most one verification email per
# address per minute (per-process — adequate for a demo/portfolio app).
_LAST_RESEND: dict[str, float] = {}


def _throttle_resend(email: str) -> None:
    now = time.monotonic()
    last = _LAST_RESEND.get(email)
    if last is not None and now - last < 60:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Please wait a minute before requesting another email",
        )
    _LAST_RESEND[email] = now


@router.post(
    "/signup",
    response_model=schemas.UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user account",
)
def signup(user_in: schemas.UserCreate, request: Request, db: DbSession) -> models.User:
    """Register a user (unverified). Rejects duplicate emails with 400 and
    emails a one-time verification link."""
    existing_user = db.query(models.User).filter(models.User.email == user_in.email).first()
    if existing_user is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    user = models.User(email=user_in.email, hashed_password=hash_password(user_in.password))
    db.add(user)
    db.commit()
    db.refresh(user)

    try:
        auth.send_verification_link(user, db, str(request.base_url))
    except Exception:
        # Never block signup because email delivery failed (e.g. no mail
        # configured, SMTP down) — the user can resend from the dashboard.
        logger.exception("failed to send verification email to %s", user.email)

    return user


@router.post("/login", response_model=schemas.Token, summary="Log in and receive a JWT")
def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: DbSession,
) -> schemas.Token:
    """Verify credentials (form-encoded: username=email, password) and
    return a JWT access token whose `sub` claim is the user id.

    Hard gate: unverified accounts cannot log in — they must click the
    verification link we emailed them first (random emails stay out).
    """
    user = db.query(models.User).filter(models.User.email == form_data.username).first()
    if user is None or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if email_verification_required() and not user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email not verified. Click the verification link we emailed you.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return schemas.Token(access_token=create_access_token(str(user.id)))


@router.get("/google/login", summary="Start Sign in with Google (redirects to Google)")
def google_login(request: Request) -> RedirectResponse:
    """Redirect the browser to Google's consent screen.

    Requires GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET env vars (free from
    Google Cloud Console). Until configured, returns a clear 503 instead of
    a broken redirect.
    """
    if not auth.google_oauth_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Google sign-in isn't configured yet. Set GOOGLE_CLIENT_ID and "
                "GOOGLE_CLIENT_SECRET (see README: Google Cloud Console)."
            ),
        )
    return RedirectResponse(auth.google_authorize_url(str(request.base_url)))


@router.get("/google/callback", summary="Google OAuth callback (issues an app JWT)")
def google_callback(
    request: Request,
    db: DbSession,
    code: str = "",
) -> object:
    """Handle Google's redirect after the user consents.

    Exchanges the `code` for Google's *verified* profile, then either finds or
    creates the user (marked is_verified=True — Google already verified the
    email, so no verification email is needed). Finally redirects back to the
    app UI with a fresh JWT in the query string, which the SPA stores and uses.

    Free + automatic: this is the path that completely removes the
    "verification email never arrives" problem.
    """
    error = request.query_params.get("error")
    if error or not code:
        logger.info("google oauth callback failed: error=%r code=%r", error, code[:6])
        return RedirectResponse("/?error=google-auth-denied")

    try:
        profile = auth.google_fetch_profile(code, str(request.base_url))
    except Exception:
        logger.exception("google oauth token exchange failed")
        return RedirectResponse("/?error=google-auth-failed")

    email = (profile.get("email") or "").strip().lower()
    if not email or not profile.get("verified_email"):
        logger.warning("google oauth: unverified/unusable email %r", email)
        return RedirectResponse("/?error=google-email-not-verified")

    user = db.query(models.User).filter(models.User.email == email).first()
    if user is None:
        # Google already proved ownership of this email — so is_verified=True.
        # hashed_password is a random unrecoverable hash: password login is
        # impossible for a Google-created account (by design, no password).
        user = models.User(
            email=email,
            hashed_password=hash_password(auth.secrets.token_urlsafe(32)),
            is_verified=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        if not user.is_verified:
            user.is_verified = True
            db.commit()

    token = create_access_token(str(user.id))
    query = urllib.parse.urlencode({"google_token": token, "email": email})
    return RedirectResponse(f"/?{query}")


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

    _throttle_resend(current_user.email)
    try:
        auth.send_verification_link(current_user, db, str(request.base_url))
    except Exception:
        logger.exception("failed to resend verification email to %s", current_user.email)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not send the verification email right now",
        )
    return {"message": "Verification email sent"}


@router.post("/resend-verification-email", response_model=dict, summary="Re-send a verification link (public, by email)")
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
        _throttle_resend(user.email)
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
    if user is not None:
        try:
            token = auth.issue_reset_token(user, db)
            reset_url = f"{str(request.base_url)}reset-password.html?token={token}"
            mailer.send_password_reset_email(user.email, reset_url)
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
