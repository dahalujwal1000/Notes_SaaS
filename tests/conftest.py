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
os.environ["MAIL_BACKEND"] = "console"
os.environ["EMAIL_VERIFICATION_REQUIRED"] = "true"  # exercise the hard gate
# Allow the TestClient's "testserver" Host header past TrustedHostMiddleware.
os.environ["TRUSTED_HOSTS"] = "testserver,127.0.0.1,localhost,[::1],*.onrender.com"
# AI assistant: force the offline mock provider so the suite never touches
# the network, even if a real key exists in the developer's .env.
os.environ["AI_PROVIDER"] = "mock"
os.environ["AI_API_KEY"] = ""
os.environ["GEMINI_API_KEY"] = ""
os.environ["MISTRAL_API_KEY"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from database import Base, SessionLocal, engine, get_db  # noqa: E402
from main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _remove_stale_db():
    """Delete leftovers from any previous (possibly crashed) test run."""
    engine.dispose()
    if os.path.exists(_test_db_path):
        os.remove(_test_db_path)


@pytest.fixture()
def db_session():
    """Raw DB session shared with the TestClient via get_db override.

    The session is created *after* the schema exists and is committed/closed
    automatically when the test ends.
    """
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    # Make FastAPI's get_db dependency yield this same session.
    app.dependency_overrides[get_db] = lambda: (yield db)
    try:
        yield db
    finally:
        db.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db_session):
    """TestClient wired to the same DB session as the test (via db_session)."""
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_in_memory_guards():
    """Clear per-process in-memory guard rails between tests so they can't
    leak state: the one-time Google sign-in exchange codes. (DB-backed rate
    limits and email throttles live in tables that are dropped & recreated
    for every test by the ``db_session`` fixture, so they need no cleanup.)"""
    import auth as auth_module

    auth_module._google_exchange_codes.clear()
    yield


# ------------------------- shared API helpers --------------------------- #

def unique_email() -> str:
    import uuid
    return f"user_{uuid.uuid4().hex[:10]}@example.com"


def signup(client, email=None, password="supersecret123"):
    return client.post(
        "/auth/signup",
        json={"email": email or unique_email(), "password": password},
    )


def verify_last_signup(client):
    """Verify the user created by the most recent signup — as a real user
    would — by pulling the token from the captured (console) email."""
    import re
    from mailer import OUTBOX

    html = OUTBOX[-1]["html"]
    match = re.search(r"token=([A-Za-z0-9_-]+)", html)
    assert match, f"no token in email html: {html[:200]}"
    resp = client.post("/auth/verify", json={"token": match.group(1)})
    assert resp.status_code == 200, resp.text


def auth_headers(client, email=None, password="supersecret123"):
    """Create a user, verify their email, log in, and return Bearer headers."""
    email = email or unique_email()
    resp = signup(client, email=email, password=password)
    assert resp.status_code == 201, resp.text
    verify_last_signup(client)
    resp = client.post(
        "/auth/login", data={"username": email, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}
