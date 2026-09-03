"""Email verification tests.

conftest sets MAIL_BACKEND=console (emails captured in mailer.OUTBOX) and
EMAIL_VERIFICATION_REQUIRED=true (login is hard-gated on verification),
so these tests exercise the real user flow: signup -> email link -> verify
-> login.
"""

import re
from datetime import datetime, timedelta, timezone

from conftest import signup, unique_email

import auth
import models
from database import SessionLocal
from mailer import OUTBOX


def _token_from_last_email() -> str:
    html = OUTBOX[-1]["html"]
    match = re.search(r"token=([A-Za-z0-9_-]+)", html)
    assert match, f"no token in email html: {html[:200]}"
    return match.group(1)


def _login_headers(client, email, expected=200):
    """Attempt login; return Bearer headers if it succeeded."""
    resp = client.post(
        "/auth/login", data={"username": email, "password": "supersecret123"}
    )
    assert resp.status_code == expected, resp.text
    if expected == 200:
        return {"Authorization": f"Bearer {resp.json()['access_token']}"}
    return None


def _unverified_session_headers(client, email):
    """Mint a JWT for an (unverified) user without going through login —
    simulates a session that started before the hard gate existed, which is
    exactly when resend-verification needs to keep working."""
    db = SessionLocal()
    user = db.query(models.User).filter(models.User.email == email).one()
    user_id = user.id
    db.close()
    return {"Authorization": f"Bearer {auth.create_access_token(str(user_id))}"}


def setup_module():
    OUTBOX.clear()


def test_signup_sends_verification_email(client):
    email = unique_email()
    signup(client, email=email)
    assert len(OUTBOX) == 1, "signup should queue one verification email"
    assert OUTBOX[0]["to"] == email
    assert "token=" in OUTBOX[0]["html"]


def test_me_requires_auth(client):
    assert client.get("/auth/me").status_code == 401


def test_login_blocked_until_verified(client):
    """The hard gate: a random/unverified email cannot log in."""
    email = unique_email()
    signup(client, email=email)
    resp = client.post(
        "/auth/login", data={"username": email, "password": "supersecret123"}
    )
    assert resp.status_code == 401
    assert "not verified" in resp.json()["detail"].lower()


def test_login_works_after_verify(client):
    email = unique_email()
    signup(client, email=email)
    token = _token_from_last_email()
    assert client.post("/auth/verify", json={"token": token}).status_code == 200

    headers = _login_headers(client, email, expected=200)
    me = client.get("/auth/me", headers=headers).json()
    assert me["is_verified"] is True


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


def test_resend_requires_auth(client):
    assert client.post("/auth/resend-verification").status_code == 401


def test_resend_sends_a_new_link_for_unverified_session(client):
    email = unique_email()
    signup(client, email=email)
    OUTBOX.clear()
    resp = client.post(
        "/auth/resend-verification", headers=_unverified_session_headers(client, email)
    )
    assert resp.status_code == 200
    assert len(OUTBOX) == 1
    assert OUTBOX[0]["to"] == email


def test_resend_throttled(client):
    email = unique_email()
    signup(client, email=email)
    headers = _unverified_session_headers(client, email)
    assert client.post("/auth/resend-verification", headers=headers).status_code == 200
    resp = client.post("/auth/resend-verification", headers=headers)
    assert resp.status_code == 429


def test_resend_public_sends_link_for_unverified_email(client):
    """Login-screen resend works without a session: unverified -> new link."""
    email = unique_email()
    signup(client, email=email)
    OUTBOX.clear()
    resp = client.post(
        "/auth/resend-verification-email", json={"email": email}
    )
    assert resp.status_code == 200
    assert len(OUTBOX) == 1
    assert OUTBOX[0]["to"] == email


def test_resend_public_non_enumerating_for_unknown_email(client):
    """Unknown / verified / unverified all get the same non-enumerating reply."""
    resp = client.post(
        "/auth/resend-verification-email",
        json={"email": "nobody@example.com"},
    )
    assert resp.status_code == 200
    assert resp.json()["message"] == "If that email exists and is unverified, a new link has been sent"


def test_resend_public_throttled(client):
    """A second request inside the throttle window returns 429."""
    email = unique_email()
    signup(client, email=email)
    assert client.post(
        "/auth/resend-verification-email", json={"email": email}
    ).status_code == 200
    resp = client.post("/auth/resend-verification-email", json={"email": email})
    assert resp.status_code == 429


def test_resend_public_noop_for_verified_user(client):
    """A verified user gets no new email and a no-op success reply."""
    email = unique_email()
    signup(client, email=email)
    token = _token_from_last_email()
    client.post("/auth/verify", json={"token": token})
    OUTBOX.clear()
    resp = client.post(
        "/auth/resend-verification-email", json={"email": email}
    )
    assert resp.status_code == 200
    assert len(OUTBOX) == 0


def test_resend_noop_for_verified_user(client):
    email = unique_email()
    signup(client, email=email)
    _token_from_last_email()
    token = _token_from_last_email()
    client.post("/auth/verify", json={"token": token})
    headers = _login_headers(client, email, expected=200)
    OUTBOX.clear()
    resp = client.post("/auth/resend-verification", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["message"] == "Email already verified"
    assert len(OUTBOX) == 0  # no new email for an already-verified user
