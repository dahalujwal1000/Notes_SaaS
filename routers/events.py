"""Events routes — the sidebar's Upcoming events backend.

Same ownership rules as notes/tasks: every query filters by the
JWT-derived user_id and foreign rows 404.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

import models
import schemas
from auth import get_current_user
from database import get_db

router = APIRouter(prefix="/events", tags=["events"])

DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[models.User, Depends(get_current_user)]


def _owned_event_or_404(db: Session, current_user: models.User, event_id: int) -> models.Event:
    event = (
        db.query(models.Event)
        .filter(models.Event.id == event_id, models.Event.user_id == current_user.id)
        .first()
    )
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    return event


@router.get("", response_model=list[schemas.EventOut], summary="List my events")
def list_events(
    db: DbSession,
    current_user: CurrentUser,
    date_from: date | None = Query(default=None, description="Only events on/after this date"),
) -> list[models.Event]:
    """The user's events ordered soonest-first."""
    query = db.query(models.Event).filter(models.Event.user_id == current_user.id)
    if date_from:
        query = query.filter(models.Event.event_date >= date_from)
    return query.order_by(models.Event.event_date, models.Event.id).all()


@router.post(
    "",
    response_model=schemas.EventOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create an event",
)
def create_event(
    event_in: schemas.EventCreate,
    db: DbSession,
    current_user: CurrentUser,
) -> models.Event:
    event = models.Event(**event_in.model_dump(), user_id=current_user.id)
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


@router.patch("/{event_id}", response_model=schemas.EventOut, summary="Update an event")
def update_event(
    event_id: int,
    event_in: schemas.EventUpdate,
    db: DbSession,
    current_user: CurrentUser,
) -> models.Event:
    event = _owned_event_or_404(db, current_user, event_id)
    for field, value in event_in.model_dump(exclude_unset=True).items():
        setattr(event, field, value)
    db.commit()
    db.refresh(event)
    return event


@router.delete(
    "/{event_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an event",
)
def delete_event(
    event_id: int,
    db: DbSession,
    current_user: CurrentUser,
) -> None:
    event = _owned_event_or_404(db, current_user, event_id)
    db.delete(event)
    db.commit()
