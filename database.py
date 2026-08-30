"""Database engine, session factory, and declarative base.

Dev DB is SQLite (notes.db, auto-created on first run).
For production, set DATABASE_URL to a PostgreSQL connection string —
no other code changes are needed.
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./notes.db")

# Hosting platforms (Render/Railway) hand out plain `postgresql://` URLs,
# which SQLAlchemy maps to the legacy psycopg2 driver. This project ships
# psycopg 3, so upgrade the scheme automatically — the env var stays
# copy-pasteable from the dashboard with zero manual editing.
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

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
