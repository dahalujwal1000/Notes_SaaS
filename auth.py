"""Auth utilities: bcrypt password hashing, JWT create/verify, and the
get_current_user() dependency.

Rules (per spec):
- Passwords are only ever stored as bcrypt hashes.
- JWT `sub` claim = user id, expiry = 60 minutes.
- Missing / invalid / expired tokens raise 401.
- SECRET_KEY comes from the environment — never hardcoded.
"""

import json
import os
import secrets
import time
import warnings
import hashlib
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Annotated

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

import mailer
import models
from database import get_db

# --- Settings ----------------------------------------------------------- #
SECRET_KEY = os.environ.get("SECRET_KEY") or None
if not SECRET_KEY:
    # Dev convenience fallback: a random per-process key keeps the repo free
    # of hardcoded secrets, but tokens are invalidated on every restart.
    # Set SECRET_KEY (or a .env file) for anything beyond local dev.
    warnings.warn(
        "SECRET_KEY env var is not set; using an ephemeral random key "
        "(tokens will be invalidated on restart)."
    )
    SECRET_KEY = secrets.token_urlsafe(32)

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

# --- Google OAuth (Sign in with Google) ----------------------------------
# Free to use (no Google billing required). OAuth Client ID + Secret come from
# Google Cloud Console → APIs & Services → Credentials → Create credentials →
# OAuth client ID (Web application). Add the callback URL:
#   http://127.0.0.1:8000/auth/google/callback        (local)
#   https://<your-app>.onrender.com/auth/google/callback  (production)
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID") or ""
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET") or ""

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

# Login-CSRF protection: a per-session nonce is stored in an HttpOnly cookie
# before the redirect, and validated against the `state` Google echoes back.
GOOGLE_OAUTH_STATE_COOKIE = "google_oauth_state"
# One-time sign-in codes live at most 2 minutes (single-use + short expiry,
# so an access token is never placed in the redirect URL).
GOOGLE_EXCHANGE_TTL_SECONDS = 120


def google_oauth_configured() -> bool:
    """True only when both Google credentials are present."""
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def google_callback_url(base_url: str) -> str:
    """The callback this app instance must register in the Google console."""
    return f"{base_url.rstrip('/')}/auth/google/callback"


def google_authorize_url(base_url: str, state: str = "") -> str:
    """Build the Google sign-in consent URL the browser is redirected to.

    `state` is the CSRF nonce the app minted for this attempt; Google echoes
    it back on the callback, where the app compares it to the cookie value.
    """
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": google_callback_url(base_url),
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "online",
        "prompt": "select_account",
    }
    if state:
        params["state"] = state
    return GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode(params)


def google_fetch_profile(code: str, base_url: str) -> dict:
    """Exchange the OAuth code for Google's verified profile via stdlib-only.

    Steps:
      1. code + client_id + secret  ->  access_token  (POST /token)
      2. access_token               ->  {email, verified_email, name, ...}
                                         (GET /oauth2/v2/userinfo)

    Raises on any HTTP/network failure so callers can surface a friendly
    message. No external library needed — urllib keeps the dependency list
    exactly as-is.
    """
    token_body = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": google_callback_url(base_url),
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")

    token_req = urllib.request.Request(
        GOOGLE_TOKEN_URL,
        data=token_body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(token_req, timeout=20) as resp:
        token_data = json.loads(resp.read().decode("utf-8"))
    access_token = token_data["access_token"]

    profile_req = urllib.request.Request(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(profile_req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


# --- One-time Google sign-in exchange codes ------------------------------ #
# After the OAuth callback verifies the user, instead of redirecting with an
# access token in the URL (which leaks into history / server logs), the app
# mints a single-use, short-lived code. The SPA POSTs it to
# POST /auth/google/exchange to redeem it for a real JWT over a normal
# JSON request. In-memory store is acceptable for a single-process deploy;
# codes are short-lived (GOOGLE_EXCHANGE_TTL_SECONDS) and burned on use.
_google_exchange_codes: dict[str, tuple[int, float]] = {}


def _prune_google_exchanges() -> None:
    now = time.monotonic()
    for code, (_, issued_at) in list(_google_exchange_codes.items()):
        if now - issued_at > GOOGLE_EXCHANGE_TTL_SECONDS:
            del _google_exchange_codes[code]


def issue_google_exchange(user_id: int) -> str:
    """Mint a one-time sign-in code redeemable for a JWT (2-minute TTL)."""
    _prune_google_exchanges()
    code = secrets.token_urlsafe(32)
    _google_exchange_codes[code] = (user_id, time.monotonic())
    return code


def consume_google_exchange(code: str) -> int:
    """Redeem a one-time sign-in code; raises 400 if missing/used/expired.

    The code is always removed from the store (single-use even on failure)
    so a leaked code cannot be replayed.
    """
    _prune_google_exchanges()
    entry = _google_exchange_codes.pop(code, None)
    if entry is None:
        raise HTTPException(
            status_code=400, detail="This sign-in link is invalid or has expired — please sign in again."
        )
    return entry[0]


# --- Password hashing (bcrypt, used directly) ---------------------------- #
# Note: the spec originally suggested passlib[bcrypt], but passlib 1.7.4 is
# hard-incompatible with bcrypt 5.x (bcrypt 5.0 no longer silently truncates
# >72-byte inputs, which crashes passlib's internal self-test). Per the
# user's decision we call bcrypt directly — same algorithm, zero extra deps.

def hash_password(plain_password: str) -> str:
    """Hash a plaintext password with bcrypt; returns the UTF-8 hash string."""
    hashed = bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Check a plaintext password against a stored bcrypt hash.
    Malformed stored hashes count as a failed login (False), not a crash."""
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"), hashed_password.encode("utf-8")
        )
    except ValueError:
        return False


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


# --- JWT ---------------------------------------------------------------- #
def create_access_token(subject: str) -> str:
    """Create a signed JWT; `subject` is the user id (the `sub` claim)."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


# --- Current-user dependency --------------------------------------------- #
def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> models.User:
    """Resolve the JWT bearer token to the authenticated User, else 401."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        subject = payload.get("sub")
        if subject is None:
            raise credentials_exception
        user_id = int(subject)
    except (JWTError, ValueError):
        raise credentials_exception

    user = db.get(models.User, user_id)
    if user is None:
        raise credentials_exception
    return user


# --- Email verification --------------------------------------------------- #
# We store only a SHA-256 hash of the verification token (never the raw
# token), so a database leak can't be replayed as verification links.
# Tokens expire after 24 hours.

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_verification_token(user: models.User, db: Session) -> str:
    """Generate a fresh verification token and store its hash + expiry."""
    token = secrets.token_urlsafe(32)
    user.verification_token_hash = _hash_token(token)
    user.verification_expires = datetime.now(timezone.utc) + timedelta(hours=24)
    db.commit()
    return token


def send_verification_link(user: models.User, db: Session, base_url: str) -> None:
    """Create a token and email the verification link to the user."""
    token = issue_verification_token(user, db)
    verify_url = f"{base_url}verify.html?token={token}"
    mailer.send_verification_email(user.email, verify_url)


def verify_email_token(token: str, db: Session) -> models.User:
    """Validate a verification token: mark the user verified (single-use)."""
    digest = _hash_token(token)
    user = (
        db.query(models.User)
        .filter(models.User.verification_token_hash == digest)
        .first()
    )
    if user is None:
        raise HTTPException(status_code=400, detail="Invalid verification link")
    if user.is_verified:
        return user

    expires = user.verification_expires
    if expires is None:
        raise HTTPException(status_code=400, detail="Verification link has expired")
    # Normalize naive timestamps (SQLite returns naive) to UTC for comparison.
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Verification link has expired")

    user.is_verified = True
    user.verification_token_hash = None
    user.verification_expires = None
    db.commit()
    db.refresh(user)
    return user


# --- Password reset ------------------------------------------------ #
# Same pattern as email verification: store a hash of the reset token
# (never the raw token), expire after 24 hours, single-use.

def issue_reset_token(user: models.User, db: Session) -> str:
    """Generate a fresh password-reset token and store its hash + expiry."""
    token = secrets.token_urlsafe(32)
    user.reset_token_hash = _hash_token(token)
    user.reset_expires = datetime.now(timezone.utc) + timedelta(hours=24)
    db.commit()
    return token


def reset_password_with_token(token: str, new_password: str, db: Session) -> models.User:
    """Validate a reset token and update the user's password (single-use)."""
    digest = _hash_token(token)
    user = (
        db.query(models.User)
        .filter(models.User.reset_token_hash == digest)
        .first()
    )
    if user is None:
        raise HTTPException(status_code=400, detail="Invalid or expired reset link")

    expires = user.reset_expires
    if expires is None:
        raise HTTPException(status_code=400, detail="Invalid or expired reset link")
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Invalid or expired reset link")

    user.hashed_password = hash_password(new_password)
    # Burn the token so it can't be reused.
    user.reset_token_hash = None
    user.reset_expires = None
    db.commit()
    db.refresh(user)
    return user
