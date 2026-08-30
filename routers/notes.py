"""Notes CRUD routes — every route is protected and scoped to the
authenticated user. Ownership is enforced by filtering every query with
the user_id taken from the verified JWT, never from client input."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

import models
import schemas
from auth import get_current_user
from database import get_db

router = APIRouter(prefix="/notes", tags=["notes"])

DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[models.User, Depends(get_current_user)]


def _get_owned_note_or_404(db: Session, current_user: models.User, note_id: int) -> models.Note:
    """Fetch a note only if it belongs to current_user; 404 otherwise
    (404 — not 403 — avoids leaking which note ids exist)."""
    note = (
        db.query(models.Note)
        .filter(models.Note.id == note_id, models.Note.user_id == current_user.id)
        .first()
    )
    if note is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Note not found",
        )
    return note


@router.post(
    "",
    response_model=schemas.NoteOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a note",
)
def create_note(
    note_in: schemas.NoteCreate,
    db: DbSession,
    current_user: CurrentUser,
) -> models.Note:
    note = models.Note(**note_in.model_dump(), user_id=current_user.id)
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


@router.get("", response_model=list[schemas.NoteOut], summary="List my notes")
def list_notes(
    db: DbSession,
    current_user: CurrentUser,
    skip: int = Query(default=0, ge=0, description="Number of notes to skip (pagination)"),
    limit: int = Query(default=50, ge=1, le=100, description="Max notes to return (pagination)"),
    search: str | None = Query(default=None, max_length=255, description="Search title/content"),
) -> list[models.Note]:
    """List the current user's notes, newest first, with optional
    pagination (skip/limit) and search across title and content."""
    query = db.query(models.Note).filter(models.Note.user_id == current_user.id)
    if search:
        pattern = f"%{search}%"
        query = query.filter(
            models.Note.title.ilike(pattern) | models.Note.content.ilike(pattern)
        )
    return (
        query.order_by(models.Note.created_at.desc(), models.Note.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.get("/{note_id}", response_model=schemas.NoteOut, summary="Get one of my notes")
def get_note(
    note_id: int,
    db: DbSession,
    current_user: CurrentUser,
) -> models.Note:
    return _get_owned_note_or_404(db, current_user, note_id)


@router.put("/{note_id}", response_model=schemas.NoteOut, summary="Update one of my notes")
def update_note(
    note_id: int,
    note_in: schemas.NoteUpdate,
    db: DbSession,
    current_user: CurrentUser,
) -> models.Note:
    note = _get_owned_note_or_404(db, current_user, note_id)
    for field, value in note_in.model_dump(exclude_unset=True).items():
        setattr(note, field, value)
    db.commit()
    db.refresh(note)
    return note


@router.delete(
    "/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete one of my notes",
)
def delete_note(
    note_id: int,
    db: DbSession,
    current_user: CurrentUser,
) -> None:
    note = _get_owned_note_or_404(db, current_user, note_id)
    db.delete(note)
    db.commit()
