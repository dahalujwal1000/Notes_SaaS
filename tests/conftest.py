"""Pytest fixtures: isolated test database + API client.

conftest.py is imported by pytest BEFORE any test module, so the DATABASE_URL
and SECRET_KEY environment variables are set first — the app then binds to a
throwaway SQLite file in the temp directory instead of ./notes.db.
"""

import os
import tempfile

# --- Must run before database.py is imported anywhere ------------------- #
_test_db_path = os.path.join(tempfile.gettempdir(), "notes_saas_test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _test_db_path
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from database import Base, engine  # noqa: E402
from main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _remove_stale_db():
    """Delete leftovers from any previous (possibly crashed) test run."""
    engine.dispose()
    if os.path.exists(_test_db_path):
        os.remove(_test_db_path)


@pytest.fixture()
def client():
    """TestClient with a clean schema per test — no HTTP server needed."""
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)
    engine.dispose()
