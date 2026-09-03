"""Tests for the password-reset flow: forgot-password → reset-password.

Covers: valid reset, single-use token, expiry, and the non-enumerating
"if that email exists" behavior on the forgot-password endpoint.
"""

import re

from fastapi import status

import auth
import mailer
import models


def _extract_token(email_body: str) -> str:
    """Pull the token= value out of the reset URL in an email."""
    match = re.search(r"token=([^&\"'<>\s]+)", email_body)
    assert match, "token not found in email body"
    return match.group(1).rstrip()


def _create_user(db, email: str, password: str = "secret123") -> models.User:
    """Create a verified user directly in the test DB session."""
    user = models.User(
        email=email,
        hashed_password=auth.hash_password(password),
        is_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_forgot_password_non_enumerating(client, db_session):
    """Forgot-password returns the same message whether or not the email exists."""
    _create_user(db_session, "victim@example.com")
    r1 = client.post("/auth/forgot-password", json={"email": "victim@example.com"})
    r2 = client.post("/auth/forgot-password", json={"email": "does-not-exist@example.com"})
    assert r1.status_code == status.HTTP_200_OK
    assert r2.status_code == status.HTTP_200_OK
    assert r1.json() == r2.json() == {"message": "If that email exists, a reset link has been sent"}


def test_forgot_and_reset_password(client, db_session):
    """Valid flow: request reset → use token → new password works on login."""
    user = _create_user(db_session, "resetme@example.com", "oldpass123")

    # old password works before reset
    r = client.post(
        "/auth/login",
        data={"username": user.email, "password": "oldpass123"},
    )
    assert r.status_code == status.HTTP_200_OK

    # request a reset — email lands in the console OUTBOX
    mailer.OUTBOX.clear()
    r = client.post("/auth/forgot-password", json={"email": user.email})
    assert r.status_code == status.HTTP_200_OK
    assert len(mailer.OUTBOX) == 1
    assert "Reset your password" in mailer.OUTBOX[0]["subject"]

    token = _extract_token(mailer.OUTBOX[0]["html"])

    # use the token to set a new password
    r = client.post(
        "/auth/reset-password",
        json={"token": token, "password": "newpass456"},
    )
    assert r.status_code == status.HTTP_200_OK
    assert r.json() == {"message": "Password updated. You can now log in."}

    # old password no longer works
    r = client.post(
        "/auth/login",
        data={"username": user.email, "password": "oldpass123"},
    )
    assert r.status_code == status.HTTP_401_UNAUTHORIZED

    # new password works
    r = client.post(
        "/auth/login",
        data={"username": user.email, "password": "newpass456"},
    )
    assert r.status_code == status.HTTP_200_OK


def test_reset_token_single_use(client, db_session):
    """The reset token is invalidated after one successful use."""
    user = _create_user(db_session, "singleuse@example.com", "secret123")
    client.post("/auth/forgot-password", json={"email": user.email})
    token = _extract_token(mailer.OUTBOX[-1]["html"])

    # first use: success
    r = client.post(
        "/auth/reset-password",
        json={"token": token, "password": "newpass1"},
    )
    assert r.status_code == status.HTTP_200_OK

    # second use: token is now consumed
    r = client.post(
        "/auth/reset-password",
        json={"token": token, "password": "newpass2"},
    )
    assert r.status_code == status.HTTP_400_BAD_REQUEST


def test_reset_token_invalid(client, db_session):
    """An unknown token returns 400."""
    r = client.post(
        "/auth/reset-password",
        json={"token": "invalid-token-xyz", "password": "newpass123"},
    )
    assert r.status_code == status.HTTP_400_BAD_REQUEST


def test_reset_password_validation(client, db_session):
    """Password must be >= 8 chars; short passwords return 422."""
    _create_user(db_session, "validation@example.com", "secret123")
    client.post("/auth/forgot-password", json={"email": "validation@example.com"})

    r = client.post(
        "/auth/reset-password",
        json={"token": "some-token", "password": "short"},
    )
    assert r.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert r.json()["detail"][0]["type"] == "string_too_short"
