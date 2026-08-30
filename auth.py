"""Auth utilities: bcrypt password hashing, JWT create/verify, and the
get_current_user() dependency.

Rules (per spec):
- Passwords are only ever stored as bcrypt hashes.
- JWT `sub` claim = user id, expiry = 60 minutes.
- Missing / invalid / expired tokens raise 401.
- SECRET_KEY comes from the environment — never hardcoded.
"""

import os
import secrets
import warnings
from datetime import datetime, timedelta, timezone
from typing import Annotated

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

import models
from database import get_db

# --- Settings ----------------------------------------------------------- #
SECRET_KEY = os.environ.get("SECRET_KEY")
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
