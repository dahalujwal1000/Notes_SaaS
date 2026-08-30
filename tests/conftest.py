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
