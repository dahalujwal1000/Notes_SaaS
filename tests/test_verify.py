"""Email verification tests.

conftest sets MAIL_BACKEND=console, so verification emails are captured in
mailer.OUTBOX instead of being sent anywhere.
"""

import re
from datetime import datetime, timedelta, timezone

import models
from conftest import auth_headers, signup, unique_email
from database import SessionLocal
from mailer import OUTBOX


def _token_from_last_email() -> str:
    html = OUTBOX[-1]["html"]
    match = re.search(r"token=([A-Za-z0-9_-]+)", html)
    assert match, f"no token in email html: {html[:200]}"
    return match.group(1)


def _login(client, email):
    resp = client.post(
        "/auth/login", data={"username": email, "password": "supersecret123"}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def setup_module():
    OUTBOX.clear()


def test_signup_sends_verification_email(client):
    email = unique_email()
    signup(client, email=email)
    assert len(OUTBOX) == 1, "signup should queue one verification email"
    assert OUTBOX[0]["to"] == email
    assert "token=" in OUTBOX[0]["html"]


def test_user_starts_unverified(client):
    email = unique_email()
    signup(client, email=email)
    headers = _login(client, email)
    me = client.get("/auth/me", headers=headers).json()
    assert me["is_verified"] is False


def test_me_requires_auth(client):
    assert client.get("/auth/me").status_code == 401


def test_verify_valid_token_marks_verified(client):
    email = unique_email()
    signup(client, email=email)
    token = _token_from_last_email()
    resp = client.post("/auth/verify", json={"token": token})
    assert resp.status_code == 200
    assert resp.json()["is_verified"] is True
    # Single-use: the token hash is cleared, so it cannot be replayed.
    assert client.post("/auth/verify", json={"token": token}).status_code == 400


def test_verify_rejects_unknown_token(client):
    resp = client.post("/auth/verify", json={"token": "definitely-not-a-token"})
    assert resp.status_code == 400


def test_verify_rejects_expired_token(client):
    email = unique_email()
    signup(client, email=email)
    token = _token_from_last_email()

    db = SessionLocal()
    user = db.query(models.User).filter(models.User.email == email).one()
    user.verification_expires = datetime.now(timezone.utc) - timedelta(hours=1)
    db.commit()
    db.close()

    resp = client.post("/auth/verify", json={"token": token})
    assert resp.status_code == 400
    assert "expired" in resp.json()["detail"].lower()


def test_resend_requires_auth_and_sends_new_link(client):
    assert client.post("/auth/resend-verification").status_code == 401

    email = unique_email()
    signup(client, email=email)
    headers = _login(client, email)
    OUTBOX.clear()
    resp = client.post("/auth/resend-verification", headers=headers)
    assert resp.status_code == 200
    assert len(OUTBOX) == 1
    assert OUTBOX[0]["to"] == email


def test_resend_throttled(client):
    email = unique_email()
    signup(client, email=email)
    headers = _login(client, email)
    assert client.post("/auth/resend-verification", headers=headers).status_code == 200
    resp = client.post("/auth/resend-verification", headers=headers)
    assert resp.status_code == 429