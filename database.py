"""Database engine, session factory, and declarative base.

Dev DB is SQLite (notes.db, auto-created on first run).
For production, set DATABASE_URL to a PostgreSQL connection string —
no other code changes are needed.
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./notes.db")

# check_same_thread=False is required only for SQLite (FastAPI uses threads).
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Per the spec: sqlalchemy.orm.declarative_base (not the deprecated
# sqlalchemy.ext.declarative module).
Base = declarative_base()


def get_db():
    """FastAPI dependency: yield a DB session, always close it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
