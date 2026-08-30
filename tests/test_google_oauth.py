"""Tests for "Sign in with Google" (free Google OAuth).

The Google API calls in `auth.google_fetch_profile` are monkeypatched so the
flow is exercised offline against the real app code (routes, JWT issuance,
user creation, redirects).
"""

import urllib.parse

import pytest

import auth
import models
from database import SessionLocal


@pytest.fixture()
def google_configured(monkeypatch):
    """Simulate a server with valid Google OAuth credentials configured."""
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_SECRET", "test-client-secret")
    yield


def test_google_login_unconfigured_returns_503(client, monkeypatch):
    """No credentials configured -> friendly 503 (not a broken redirect)."""
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_ID", "")
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_SECRET", "")
    resp = client.get("/auth/google/login")
    assert resp.status_code == 503
    assert "GOOGLE_CLIENT_ID" in resp.json()["detail"]


def test_google_login_redirects_to_google(client, google_configured):
    """Configured -> 307 redirect to Google's consent screen."""
    resp = client.get("/auth/google/login", follow_redirects=False)
    assert resp.status_code == 307
    location = resp.headers["location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth")
    parsed = urllib.parse.urlparse(location)
    query = urllib.parse.parse_qs(parsed.query)
    assert query["response_type"] == ["code"]
    assert query["scope"] == ["openid email profile"]
    # The redirect_uri must be the exact URI we register in the Google console.
    assert query["redirect_uri"][0].endswith("/auth/google/callback")


def test_google_callback_creates_and_verifies_user(client, google_configured, monkeypatch):
    """New Google user -> created as is_verified, JWT issued, token works."""
    fake_profile = {
        "id": "google-12345",
        "email": "rajesh@gmail.com",
        "verified_email": True,
        "name": "Rajesh Kumar",
    }
    monkeypatch.setattr(
        auth, "google_fetch_profile", lambda code, base_url: fake_profile
    )

    resp = client.get("/auth/google/callback?code=oauth-code-123", follow_redirects=False)
    assert resp.status_code == 307
    location = resp.headers["location"]
    assert "/?" in location
    query = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
    token = query["google_token"][0]
    assert query["email"][0] == "rajesh@gmail.com"

    # The issued token is a valid app JWT (hard gated account, but verified).
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "rajesh@gmail.com"
    assert me.json()["is_verified"] is True

    # No verification email should ever be needed for Google accounts.
    db = SessionLocal()
    user = db.query(models.User).filter(models.User.email == "rajesh@gmail.com").first()
    assert user is not None
    assert user.is_verified is True
    assert user.verification_token_hash is None
    db.close()


def test_google_callback_second_login_uses_existing_user(client, google_configured, monkeypatch):
    """Logging in with Google again must not create a duplicate user."""
    fake_profile = {
        "id": "google-12345",
        "email": "priya@gmail.com",
        "verified_email": True,
    }
    monkeypatch.setattr(auth, "google_fetch_profile", lambda code, base_url: fake_profile)

    first = client.get("/auth/google/callback?code=code-one", follow_redirects=False)
    second = client.get("/auth/google/callback?code=code-two", follow_redirects=False)

    db = SessionLocal()
    users = db.query(models.User).filter(models.User.email == "priya@gmail.com").all()
    db.close()
    assert len(users) == 1

    token1 = urllib.parse.parse_qs(
        urllib.parse.urlparse(first.headers["location"]).query
    )["google_token"][0]
    token2 = urllib.parse.parse_qs(
        urllib.parse.urlparse(second.headers["location"]).query
    )["google_token"][0]

    # The two tokens must carry the same `sub` (user id) — they're issued at
    # different instants so they won't be byte-identical JWTs, but they must
    # reference the same user.
    from jose import jwt as _jwt

    sub1 = _jwt.decode(token1, "test-secret-key-not-for-production", algorithms=["HS256"])["sub"]
    sub2 = _jwt.decode(token2, "test-secret-key-not-for-production", algorithms=["HS256"])["sub"]
    assert sub1 == sub2  # same user id => same sub claim


def test_google_callback_unverified_email_rejected(client, google_configured, monkeypatch):
    """Google says the email isn't verified -> refuse, don't create a user."""
    fake_profile = {"id": "x", "email": "weird@gmail.com", "verified_email": False}
    monkeypatch.setattr(auth, "google_fetch_profile", lambda code, base_url: fake_profile)

    resp = client.get("/auth/google/callback?code=somecode", follow_redirects=False)
    assert "google-email-not-verified" in resp.headers["location"]

    db = SessionLocal()
    assert db.query(models.User).filter(models.User.email == "weird@gmail.com").first() is None
    db.close()


def test_google_callback_error_param(client, google_configured):
    """Google refuses the user (e.g. user cancelled) -> redirect with error."""
    resp = client.get("/auth/google/callback?error=access_denied&code=", follow_redirects=False)
    assert "google-auth-denied" in resp.headers["location"]


def test_google_callback_bad_exchange(client, google_configured, monkeypatch):
    """Token exchange failure (network, bad code) -> friendly redirect."""
    def boom(code, base_url):
        raise RuntimeError("google is on fire")
    monkeypatch.setattr(auth, "google_fetch_profile", boom)

    resp = client.get("/auth/google/callback?code=badcode", follow_redirects=False)
    assert "google-auth-failed" in resp.headers["location"]