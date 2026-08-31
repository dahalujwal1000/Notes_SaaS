"""Pydantic v2 schemas: request/response shapes for every route.

`from_attributes = True` (v2 syntax) on the Out models lets FastAPI
build responses directly from SQLAlchemy ORM objects.
"""

from datetime import date, datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

# Plain regex instead of pydantic EmailStr: keeps the project free of the
# extra `email-validator` dependency while still catching obvious typos.
_EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


def _bcrypt_safe(password: str) -> str:
    """Reject passwords over 72 bytes.

    bcrypt only hashes the first 72 bytes, and bcrypt 5.x raises ValueError
    instead of silently truncating — without this check an over-long
    password would crash signup/login/reset with a 500 instead of a 422.
    Byte length (not characters) is what matters: one emoji is 4 bytes.
    """
    if len(password.encode("utf-8")) > 72:
        raise ValueError("Password must be at most 72 bytes long")
    return password


# Shared password type: 8–128 chars AND at most 72 bytes once UTF-8 encoded.
Password = Annotated[
    str, Field(min_length=8, max_length=128, examples=["supersecret123"]), AfterValidator(_bcrypt_safe)
]


# ----------------------------- Auth / Users ----------------------------- #

class UserCreate(BaseModel):
    """POST /auth/signup body."""

    email: str = Field(max_length=255, pattern=_EMAIL_PATTERN, examples=["user@example.com"])
    password: Password


class UserOut(BaseModel):
    """Public user representation — never exposes the password hash."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    is_verified: bool
    created_at: datetime


class VerifyEmail(BaseModel):
    """POST /auth/verify body — the token from the emailed link."""

    token: str = Field(min_length=10, max_length=128)


class ResendVerificationEmail(BaseModel):
    """POST /auth/resend-verification-email body — used from the login
    screen, where the user does not (yet) have an authenticated session."""

    email: str = Field(max_length=255, pattern=_EMAIL_PATTERN)


class ForgotPassword(BaseModel):
    """POST /auth/forgot-password body."""

    email: str = Field(max_length=255, pattern=_EMAIL_PATTERN)


class ResetPassword(BaseModel):
    """POST /auth/reset-password body — token from the emailed reset link."""

    token: str = Field(min_length=10, max_length=128)
    password: Password


class Token(BaseModel):
    """POST /auth/login response."""

    access_token: str
    token_type: str = "bearer"


# -------------------------------- Notes --------------------------------- #

class NoteCreate(BaseModel):
    """POST /notes body."""

    title: str = Field(min_length=1, max_length=255, examples=["Groceries"])
    content: str = Field(default="", max_length=10_000, examples=["milk, eggs"])


class NoteUpdate(BaseModel):
    """PUT /notes/{id} body — partial update; only provided fields change."""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    content: str | None = Field(default=None, max_length=10_000)


class NoteFavorite(BaseModel):
    """POST /notes/{id}/favorite body — set the star on/off."""

    favorite: bool


class NoteOut(BaseModel):
    """Note representation returned by every notes route."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    content: str
    favorite: bool = False
    user_id: int
    created_at: datetime
    updated_at: datetime


# -------------------------------- Tasks --------------------------------- #

# Kanban columns, in board order.
TASK_STATUSES = ("todo", "doing", "review", "done")
_STATUS_PATTERN = "^(todo|doing|review|done)$"


class TaskCreate(BaseModel):
    """POST /tasks body."""

    title: str = Field(min_length=1, max_length=255, examples=["Ship the landing page"])
    status: str = Field(default="todo", pattern=_STATUS_PATTERN)
    position: float | None = Field(
        default=None, description="Ordering key inside the column; server appends if omitted."
    )


class TaskUpdate(BaseModel):
    """PATCH /tasks/{id} body — partial update (status moves, reorders, renames)."""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    status: str | None = Field(default=None, pattern=_STATUS_PATTERN)
    position: float | None = Field(default=None)


class TaskOut(BaseModel):
    """Task representation returned by every tasks route."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    status: str
    position: float
    user_id: int
    created_at: datetime
    updated_at: datetime


# -------------------------------- Events --------------------------------- #

class EventCreate(BaseModel):
    """POST /events body."""

    title: str = Field(min_length=1, max_length=255, examples=["Design review"])
    description: str = Field(default="", max_length=2000)
    event_date: date = Field(examples=["2026-09-15"])


class EventUpdate(BaseModel):
    """PATCH /events/{id} body — partial update."""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    event_date: date | None = None


class EventOut(BaseModel):
    """Event representation returned by every events route."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str
    event_date: date
    user_id: int
    created_at: datetime
    updated_at: datetime
