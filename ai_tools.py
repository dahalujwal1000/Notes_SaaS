"""AI tool layer: the catalogue of actions the assistant may take, the
Pydantic schemas that validate every LLM-emitted call, and the executors
that run them scoped to the authenticated user.

Security rules (the LLM is untrusted input):
- Every DB query filters by the JWT-derived user.id — never by anything
  the model says. Cross-user access is impossible by construction.
- Every tool call is validated against a strict Pydantic schema before
  execution; unknown tools and malformed args are rejected with a
  structured error the agent loop feeds back to the model.
- Destructive tools (deletes) NEVER execute directly: with execute=False
  they only resolve the target and return a preview; the actual deletion
  happens exclusively through a confirmed AIAction (routers/ai.py).
"""

from pydantic import BaseModel, Field, field_validator, model_validator

import models
from schemas import TASK_STATUSES

_STATUS_PATTERN = "^(todo|doing|review|done)$"

# Friendly column labels → kanban status keys (LLMs and users say "In
# progress", the DB stores "doing").
STATUS_ALIASES = {
    "todo": "todo", "to-do": "todo", "to do": "todo", "backlog": "todo",
    "doing": "doing", "in progress": "doing", "in-progress": "doing",
    "wip": "doing", "ongoing": "doing",
    "review": "review", "in review": "review", "in-review": "review",
    "done": "done", "complete": "done", "completed": "done", "finished": "done",
}


def normalize_status(raw: str) -> str | None:
    """Map any human spelling of a column onto a canonical status key."""
    return STATUS_ALIASES.get((raw or "").strip().lower())


COLUMN_LABELS = {
    "todo": "To-do",
    "doing": "In progress",
    "review": "In review",
    "done": "Complete",
}


# --------------------------- arg schemas -------------------------------- #


class ListTasksArgs(BaseModel):
    status: str | None = Field(default=None, description="Filter: todo|doing|review|done")

    @field_validator("status")
    @classmethod
    def _status_ok(cls, v):
        if v is not None and v not in TASK_STATUSES:
            raise ValueError("status must be one of: todo, doing, review, done")
        return v


class SearchNotesArgs(BaseModel):
    query: str | None = Field(default=None, max_length=255, description="Search title/content")
    limit: int = Field(default=10, ge=1, le=20)


class GetNoteArgs(BaseModel):
    note_id: int


class ListEventsArgs(BaseModel):
    limit: int = Field(default=10, ge=1, le=20)


class CreateTaskArgs(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    status: str = Field(default="todo", pattern=_STATUS_PATTERN)
    position: float | None = None


class UpdateTaskArgs(BaseModel):
    task_id: int | None = None
    title_search: str | None = Field(default=None, min_length=1, max_length=255)
    status: str | None = Field(default=None, pattern=_STATUS_PATTERN)
    new_title: str | None = Field(default=None, min_length=1, max_length=255)

    @model_validator(mode="after")
    def _needs_target(self):
        if self.task_id is None and not self.title_search:
            raise ValueError("Provide task_id or title_search to identify the task")
        if self.status is None and self.new_title is None:
            raise ValueError("Nothing to update: provide status and/or new_title")
        return self


class DeleteTaskArgs(BaseModel):
    task_id: int | None = None
    title_search: str | None = Field(default=None, min_length=1, max_length=255)

    @model_validator(mode="after")
    def _needs_target(self):
        if self.task_id is None and not self.title_search:
            raise ValueError("Provide task_id or title_search to identify the task")
        return self


class CreateNoteArgs(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(default="", max_length=10_000)


class DeleteNoteArgs(BaseModel):
    note_id: int | None = None
    title_search: str | None = Field(default=None, min_length=1, max_length=255)

    @model_validator(mode="after")
    def _needs_target(self):
        if self.note_id is None and not self.title_search:
            raise ValueError("Provide note_id or title_search to identify the note")
        return self


class ConfirmActionArgs(BaseModel):
    action_id: int


class CancelActionArgs(BaseModel):
    action_id: int


# --------------------------- tool catalogue ------------------------------ #

# "destructive" tools always go through the proposal → confirm flow.
TOOLS = [
    {
        "name": "list_tasks",
        "description": "List the user's kanban tasks (id, title, status), optionally filtered to one column.",
        "args_model": ListTasksArgs,
        "destructive": False,
        "mutating": False,
    },
    {
        "name": "search_notes",
        "description": "Search the user's notes by title/content; returns id, title and a content snippet.",
        "args_model": SearchNotesArgs,
        "destructive": False,
        "mutating": False,
    },
    {
        "name": "get_note",
        "description": "Read one note's full content by id.",
        "args_model": GetNoteArgs,
        "destructive": False,
        "mutating": False,
    },
    {
        "name": "list_events",
        "description": "List the user's upcoming calendar events, soonest first.",
        "args_model": ListEventsArgs,
        "destructive": False,
        "mutating": False,
    },
    {
        "name": "create_task",
        "description": "Create a task on the kanban board (status: todo|doing|review|done).",
        "args_model": CreateTaskArgs,
        "destructive": False,
        "mutating": True,
    },
    {
        "name": "update_task",
        "description": "Move a task to another column (status) and/or rename it. Identify by task_id or title_search.",
        "args_model": UpdateTaskArgs,
        "destructive": False,
        "mutating": True,
    },
    {
        "name": "create_note",
        "description": "Create a new note with a title and optional markdown content.",
        "args_model": CreateNoteArgs,
        "destructive": False,
        "mutating": True,
    },
    {
        "name": "delete_task",
        "description": "Propose deleting a task. This is a DRY RUN: a confirmation is required afterwards.",
        "args_model": DeleteTaskArgs,
        "destructive": True,
        "mutating": True,
    },
    {
        "name": "delete_note",
        "description": "Propose deleting a note. This is a DRY RUN: a confirmation is required afterwards.",
        "args_model": DeleteNoteArgs,
        "destructive": True,
        "mutating": True,
    },
    {
        "name": "confirm_action",
        "description": "Execute a previously proposed destructive action by its action_id (e.g. actually delete the task).",
        "args_model": ConfirmActionArgs,
        "destructive": False,
        "mutating": True,
    },
    {
        "name": "cancel_action",
        "description": "Cancel a previously proposed destructive action; nothing will be deleted.",
        "args_model": CancelActionArgs,
        "destructive": False,
        "mutating": True,
    },
]

_TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}


def get_tool(name: str) -> dict | None:
    return _TOOLS_BY_NAME.get(name)


def tool_catalog_for_prompt() -> str:
    """Compact human-readable catalogue embedded in the system prompt."""
    return "\n".join(f"- {t['name']}: {t['description']}" for t in TOOLS)


# ----------------------------- executors -------------------------------- #
# Every executor returns a plain dict: {"ok": True, ...} or
# {"ok": False, "error": "..."} — never raises for expected failures, so
# the agent loop can feed the outcome back to the LLM as data.


def _error(msg: str) -> dict:
    return {"ok": False, "error": msg}


def _task_row(task: models.Task) -> dict:
    return {"id": task.id, "title": task.title, "status": task.status}


def _resolve_task(db, user, task_id: int | None, title_search: str | None):
    query = db.query(models.Task).filter(models.Task.user_id == user.id)
    if task_id is not None:
        return query.filter(models.Task.id == task_id).first()
    return (
        query.filter(models.Task.title.ilike(f"%{title_search}%"))
        .order_by(models.Task.position, models.Task.id)
        .first()
    )


def _resolve_note(db, user, note_id: int | None, title_search: str | None):
    query = db.query(models.Note).filter(models.Note.user_id == user.id)
    if note_id is not None:
        return query.filter(models.Note.id == note_id).first()
    return (
        query.filter(models.Note.title.ilike(f"%{title_search}%"))
        .order_by(models.Note.created_at.desc(), models.Note.id.desc())
        .first()
    )


def _tool_list_tasks(db, user, args: ListTasksArgs, execute: bool) -> dict:
    query = db.query(models.Task).filter(models.Task.user_id == user.id)
    if args.status:
        query = query.filter(models.Task.status == args.status)
    tasks = query.order_by(models.Task.position, models.Task.id).limit(100).all()
    return {"ok": True, "tasks": [_task_row(t) for t in tasks], "count": len(tasks)}


def _tool_search_notes(db, user, args: SearchNotesArgs, execute: bool) -> dict:
    query = db.query(models.Note).filter(models.Note.user_id == user.id)
    if args.query:
        pattern = f"%{args.query}%"
        query = query.filter(
            models.Note.title.ilike(pattern) | models.Note.content.ilike(pattern)
        )
    notes = (
        query.order_by(models.Note.created_at.desc(), models.Note.id.desc())
        .limit(args.limit)
        .all()
    )
    return {
        "ok": True,
        "notes": [
            {"id": n.id, "title": n.title, "snippet": (n.content or "")[:160]}
            for n in notes
        ],
        "count": len(notes),
    }


def _tool_get_note(db, user, args: GetNoteArgs, execute: bool) -> dict:
    note = (
        db.query(models.Note)
        .filter(models.Note.id == args.note_id, models.Note.user_id == user.id)
        .first()
    )
    if note is None:
        return _error("Note not found")
    return {
        "ok": True,
        "note": {"id": note.id, "title": note.title, "content": note.content[:2000]},
    }


def _tool_list_events(db, user, args: ListEventsArgs, execute: bool) -> dict:
    events = (
        db.query(models.Event)
        .filter(models.Event.user_id == user.id)
        .order_by(models.Event.event_date, models.Event.id)
        .limit(args.limit)
        .all()
    )
    return {
        "ok": True,
        "events": [
            {"id": e.id, "title": e.title, "date": e.event_date.isoformat()} for e in events
        ],
        "count": len(events),
    }


def _tool_create_task(db, user, args: CreateTaskArgs, execute: bool) -> dict:
    from sqlalchemy import func

    max_position = (
        db.query(func.max(models.Task.position))
        .filter(models.Task.user_id == user.id, models.Task.status == args.status)
        .scalar()
    )
    task = models.Task(
        title=args.title,
        status=args.status,
        position=(max_position or 0) + 1024,
        user_id=user.id,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return {
        "ok": True,
        "task": _task_row(task),
        "summary": f"Done — added '{task.title}' to {COLUMN_LABELS[task.status]} ✅",
    }


def _tool_update_task(db, user, args: UpdateTaskArgs, execute: bool) -> dict:
    task = _resolve_task(db, user, args.task_id, args.title_search)
    if task is None:
        return _error("Task not found (check the title or use list_tasks for ids)")
    changed = []
    if args.status and args.status != task.status:
        task.status = args.status
        changed.append(f"moved to {args.status}")
    if args.new_title and args.new_title != task.title:
        task.title = args.new_title
        changed.append("renamed")
    if not changed:
        return {
            "ok": True,
            "task": _task_row(task),
            "summary": "Nothing to change — task already in that state",
        }
    db.commit()
    db.refresh(task)
    return {
        "ok": True,
        "task": _task_row(task),
        "summary": f"Task '{task.title}': {', '.join(changed)}",
    }


def _tool_create_note(db, user, args: CreateNoteArgs, execute: bool) -> dict:
    note = models.Note(title=args.title, content=args.content, user_id=user.id)
    db.add(note)
    db.commit()
    db.refresh(note)
    return {
        "ok": True,
        "note": {"id": note.id, "title": note.title},
        "summary": f"Created note '{note.title}'",
    }


def _tool_delete_task(db, user, args: DeleteTaskArgs, execute: bool) -> dict:
    task = _resolve_task(db, user, args.task_id, args.title_search)
    if task is None:
        return _error("Task not found (check the title or use list_tasks for ids)")
    if not execute:
        # Dry run — nothing is deleted until the action is confirmed.
        return {
            "ok": True,
            "target": {"id": task.id, "title": task.title, "status": task.status},
        }
    task_row = _task_row(task)
    db.delete(task)
    db.commit()
    return {"ok": True, "deleted": task_row, "summary": f"Deleted task '{task_row['title']}'"}


def _tool_delete_note(db, user, args: DeleteNoteArgs, execute: bool) -> dict:
    note = _resolve_note(db, user, args.note_id, args.title_search)
    if note is None:
        return _error("Note not found (check the title or use search_notes for ids)")
    if not execute:
        return {"ok": True, "target": {"id": note.id, "title": note.title}}
    note_row = {"id": note.id, "title": note.title}
    db.delete(note)
    db.commit()
    return {"ok": True, "deleted": note_row, "summary": f"Deleted note '{note_row['title']}'"}


_EXECUTORS = {
    "list_tasks": _tool_list_tasks,
    "search_notes": _tool_search_notes,
    "get_note": _tool_get_note,
    "list_events": _tool_list_events,
    "create_task": _tool_create_task,
    "update_task": _tool_update_task,
    "create_note": _tool_create_note,
    "delete_task": _tool_delete_task,
    "delete_note": _tool_delete_note,
}

# confirm_action / cancel_action are executed only through a confirmed or
# cancelled AIAction row (see routers/ai.py), never straight from a call.
_ACTION_TOOLS = {"confirm_action", "cancel_action"}


def run_tool(name: str, args: BaseModel, db, user, execute: bool = False) -> dict:
    """Validate-and-run a tool call. `execute` matters only for destructive
    tools: False = dry-run preview, True = actually perform the deletion."""
    executor = _EXECUTORS.get(name)
    if executor is None:
        return _error(f"Unknown tool '{name}'")
    try:
        return executor(db, user, args, execute)
    except Exception as exc:  # defensive: never crash the chat on one bad tool
        return _error(f"Tool execution failed: {exc}")
