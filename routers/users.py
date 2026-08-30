"""Auth routes: signup, login, current user, and email verification."""

import logging
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

import auth
import models
import schemas
from auth import create_access_token, get_current_user, hash_password, verify_password
from database import get_db

logger = logging.getLogger("users")

router = APIRouter(prefix="/auth", tags=["auth"])

DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[models.User, Depends(get_current_user)]

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
    return a JWT access token whose `sub` claim is the user id."""
    user = db.query(models.User).filter(models.User.email == form_data.username).first()
    if user is None or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return schemas.Token(access_token=create_access_token(str(user.id)))


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
    throttled to once per minute."""
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
