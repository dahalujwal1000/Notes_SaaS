"""Tasks routes — the kanban board's backend.

Every route is protected and scoped to the authenticated user (same
ownership rules as notes: filter by the JWT-derived user_id, 404 on
anything not owned).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

import models
import schemas
from auth import get_current_user
from database import get_db

router = APIRouter(prefix="/tasks", tags=["tasks"])

DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[models.User, Depends(get_current_user)]


def _owned_task_or_404(db: Session, current_user: models.User, task_id: int) -> models.Task:
    task = (
        db.query(models.Task)
        .filter(models.Task.id == task_id, models.Task.user_id == current_user.id)
        .first()
    )
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


@router.get("", response_model=list[schemas.TaskOut], summary="List my tasks")
def list_tasks(
    db: DbSession,
    current_user: CurrentUser,
    status_filter: str | None = Query(
        default=None, alias="status", pattern="^(todo|doing|review|done)$"
    ),
) -> list[models.Task]:
    """All of the user's tasks ordered for board rendering, optionally
    filtered to one kanban column."""
    query = db.query(models.Task).filter(models.Task.user_id == current_user.id)
    if status_filter:
        query = query.filter(models.Task.status == status_filter)
    return query.order_by(models.Task.position, models.Task.id).all()


@router.post(
    "",
    response_model=schemas.TaskOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a task",
)
def create_task(
    task_in: schemas.TaskCreate,
    db: DbSession,
    current_user: CurrentUser,
) -> models.Task:
    position = task_in.position
    if position is None:
        # Append to the bottom of the target column.
        max_position = (
            db.query(func.max(models.Task.position))
            .filter(
                models.Task.user_id == current_user.id,
                models.Task.status == task_in.status,
            )
            .scalar()
        )
        position = (max_position or 0) + 1024
    task = models.Task(
        title=task_in.title,
        status=task_in.status,
        position=position,
        user_id=current_user.id,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.patch("/{task_id}", response_model=schemas.TaskOut, summary="Move / rename a task")
def update_task(
    task_id: int,
    task_in: schemas.TaskUpdate,
    db: DbSession,
    current_user: CurrentUser,
) -> models.Task:
    task = _owned_task_or_404(db, current_user, task_id)
    for field, value in task_in.model_dump(exclude_unset=True).items():
        setattr(task, field, value)
    db.commit()
    db.refresh(task)
    return task


@router.delete(
    "/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a task",
)
def delete_task(
    task_id: int,
    db: DbSession,
    current_user: CurrentUser,
) -> None:
    task = _owned_task_or_404(db, current_user, task_id)
    db.delete(task)
    db.commit()
