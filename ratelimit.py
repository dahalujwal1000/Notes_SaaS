"""DB-backed sliding-window rate limiting.

Unlike in-memory counters, these:
  - survive process restarts (an attacker can't reset a login lockout by
    forcing a redeploy/restart), and
  - hold globally across multiple uvicorn workers.

Every counted event is one row in the ``rate_limit_events`` table
(``models.RateLimitEvent``), auto-created at startup like all models. Old
rows are pruned lazily on each call. Buckets are simple strings, e.g.
``"login:email:user@example.com"`` or ``"ai:user:7"``.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func
from sqlalchemy.orm import Session

import models


def _cutoff(window_seconds: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=window_seconds)


def count_events(db: Session, bucket: str, window_seconds: int) -> int:
    """Events recorded for `bucket` inside the sliding window (prunes old ones)."""
    cutoff = _cutoff(window_seconds)
    db.execute(
        delete(models.RateLimitEvent).where(models.RateLimitEvent.created_at < cutoff)
    )
    db.commit()
    return (
        db.query(func.count(models.RateLimitEvent.id))
        .filter(
            models.RateLimitEvent.bucket == bucket,
            models.RateLimitEvent.created_at >= cutoff,
        )
        .scalar()
        or 0
    )


def record(db: Session, bucket: str) -> None:
    """Record a new counted event for `bucket`."""
    db.add(models.RateLimitEvent(bucket=bucket, created_at=datetime.now(timezone.utc)))
    db.commit()


def check_and_record(db: Session, bucket: str, limit: int, window_seconds: int) -> bool:
    """Return True when `bucket` is already at/over `limit` (caller should
    refuse the action); otherwise record one event and return False."""
    if count_events(db, bucket, window_seconds) >= limit:
        return True
    record(db, bucket)
    return False


def reset(db: Session, bucket: str) -> None:
    """Clear every event for `bucket` (e.g. after a successful login)."""
    db.execute(
        delete(models.RateLimitEvent).where(models.RateLimitEvent.bucket == bucket)
    )
    db.commit()


def refund_last(db: Session, bucket: str) -> None:
    """Remove the most recent event for `bucket` (e.g. refund an AI quota
    slot when a request failed through no fault of the user)."""
    row = (
        db.query(models.RateLimitEvent)
        .filter(models.RateLimitEvent.bucket == bucket)
        .order_by(models.RateLimitEvent.id.desc())
        .first()
    )
    if row is not None:
        db.delete(row)
        db.commit()
