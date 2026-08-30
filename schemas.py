"""Pydantic v2 schemas: request/response shapes for every route.

`from_attributes = True` (v2 syntax) on the Out models lets FastAPI
build responses directly from SQLAlchemy ORM objects.
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

# Plain regex instead of pydantic EmailStr: keeps the project free of the
# extra `email-validator` dependency while still catching obvious typos.
_EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


# ----------------------------- Auth / Users ----------------------------- #

class UserCreate(BaseModel):
    """POST /auth/signup body."""

    email: str = Field(max_length=255, pattern=_EMAIL_PATTERN, examples=["user@example.com"])
    password: str = Field(min_length=8, max_length=128, examples=["supersecret123"])


class UserLogin(BaseModel):
    """JSON shape of login credentials (the endpoint itself uses the OAuth2
    form so Swagger's Authorize button works out of the box)."""

    email: str = Field(max_length=255)
    password: str = Field(min_length=1, max_length=128)


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
    password: str = Field(min_length=8, max_length=128, examples=["supersecret123"])


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


class NoteOut(BaseModel):
    """Note representation returned by every notes route."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    content: str
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
