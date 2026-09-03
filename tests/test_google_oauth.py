"""Tests for "Sign in with Google" (free Google OAuth).

The Google API calls in `auth.google_fetch_profile` are monkeypatched so the
flow is exercised offline against the real app code (routes, state nonce,
one-time exchange codes, JWT issuance, user creation, redirects).

Security behavior under test:
  - login mints a `state` nonce + HttpOnly cookie (login-CSRF protection)
  - the callback rejects a state mismatch before creating any user
  - the callback never puts a JWT in the URL; it issues a single-use
    `google_code` that is redeemed via POST /auth/google/exchange
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


def _begin_google_login(client):
    """Start Sign in with Google; the client cookie jar keeps the state
    cookie, and we return the `state` value the callback must echo back."""
    resp = client.get("/auth/google/login", follow_redirects=False)
    assert resp.status_code == 307
    location = resp.headers["location"]
    parsed = urllib.parse.urlparse(location)
    query = urllib.parse.parse_qs(parsed.query)
    assert "state" in query, "authorize URL must carry the CSRF state nonce"
    return location, query, query["state"][0]


def _complete_google_callback(client, state, code="oauth-code-123"):
    """Hit the callback with Google's echo (code + state)."""
    return client.get(
        f"/auth/google/callback?code={code}&state={state}", follow_redirects=False
    )


def _redeem(client, google_code):
    """POST the one-time code for a JWT."""
    return client.post("/auth/google/exchange", json={"code": google_code})

def test_google_login_unconfigured_returns_503(client, monkeypatch):
    """No credentials configured -> friendly 503 (not a broken redirect)."""
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_ID", "")
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_SECRET", "")
    resp = client.get("/auth/google/login")
    assert resp.status_code == 503
    assert "GOOGLE_CLIENT_ID" in resp.json()["detail"]


def test_google_login_redirects_to_google(client, google_configured):
    """Configured -> 307 redirect to Google's consent screen WITH a state
    nonce and an HttpOnly cookie holding the same nonce."""
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
    # Login-CSRF protection is in place.
    assert query["state"]
    assert auth.GOOGLE_OAUTH_STATE_COOKIE in resp.cookies


def test_google_callback_rejects_state_mismatch(client, google_configured, monkeypatch):
    """A callback whose state doesn't match the cookie is refused BEFORE any
    user is created or token/code issued (login-CSRF protection)."""
    called = {"fetch": False}

    def fake_fetch(code, base_url):
        called["fetch"] = True
        return {"id": "x", "email": "csrf@gmail.com", "verified_email": True}

    monkeypatch.setattr(auth, "google_fetch_profile", fake_fetch)

    _begin_google_login(client)  # stores the real state cookie
    resp = _complete_google_callback(client, state="attacker-controlled-state")

    assert "google-auth-failed" in resp.headers["location"]
    assert called["fetch"] is False, "profile must not be fetched on state mismatch"

    db = SessionLocal()
    assert db.query(models.User).filter(models.User.email == "csrf@gmail.com").first() is None
    db.close()


def test_google_callback_creates_and_verifies_user(client, google_configured, monkeypatch):
    """New Google user -> created as is_verified; one-time code redeems to a
    working JWT; no access token ever appears in the redirect URL."""
    fake_profile = {
        "id": "google-12345",
        "email": "rajesh@gmail.com",
        "verified_email": True,
        "name": "Rajesh Kumar",
    }
    monkeypatch.setattr(
        auth, "google_fetch_profile", lambda code, base_url: fake_profile
    )

    _, _, state = _begin_google_login(client)
    resp = _complete_google_callback(client, state=state)
    assert resp.status_code == 307
    location = resp.headers["location"]
    assert location.startswith("/?")
    query = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
    google_code = query["google_code"][0]
    assert query["email"][0] == "rajesh@gmail.com"
    # Security: the JWT is NOT in the URL — only the short-lived one-time code.
    assert "google_token" not in location and "access_token" not in location

    # Redeem the code for a real JWT.
    redeemed = _redeem(client, google_code)
    assert redeemed.status_code == 200, redeemed.text
    token = redeemed.json()["access_token"]

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

def test_google_exchange_code_is_single_use(client, google_configured, monkeypatch):
    """A redeemed google_code can't be replayed — the second POST is 400."""
    fake_profile = {
        "id": "google-12345",
        "email": "once@gmail.com",
        "verified_email": True,
    }
    monkeypatch.setattr(auth, "google_fetch_profile", lambda code, base_url: fake_profile)

    _, _, state = _begin_google_login(client)
    resp = _complete_google_callback(client, state=state)
    google_code = urllib.parse.parse_qs(
        urllib.parse.urlparse(resp.headers["location"]).query
    )["google_code"][0]

    assert _redeem(client, google_code).status_code == 200
    second = _redeem(client, google_code)
    assert second.status_code == 400


def test_google_callback_second_login_uses_existing_user(client, google_configured, monkeypatch):
    """Logging in with Google again must not create a duplicate user."""
    fake_profile = {
        "id": "google-12345",
        "email": "priya@gmail.com",
        "verified_email": True,
    }
    monkeypatch.setattr(auth, "google_fetch_profile", lambda code, base_url: fake_profile)

    _, _, state = _begin_google_login(client)
    first = _complete_google_callback(client, state=state, code="code-one")
    # The callback burns the state cookie, so a second sign-in needs a fresh
    # login redirect (which mints a fresh nonce) — just like a real user.
    _, _, state2 = _begin_google_login(client)
    second = _complete_google_callback(client, state=state2, code="code-two")

    db = SessionLocal()
    users = db.query(models.User).filter(models.User.email == "priya@gmail.com").all()
    db.close()
    assert len(users) == 1

    code1 = urllib.parse.parse_qs(
        urllib.parse.urlparse(first.headers["location"]).query
    )["google_code"][0]
    code2 = urllib.parse.parse_qs(
        urllib.parse.urlparse(second.headers["location"]).query
    )["google_code"][0]

    token1 = _redeem(client, code1).json()["access_token"]
    token2 = _redeem(client, code2).json()["access_token"]

    from jose import jwt as _jwt

    sub1 = _jwt.decode(token1, "test-secret-key-not-for-production", algorithms=["HS256"])["sub"]
    sub2 = _jwt.decode(token2, "test-secret-key-not-for-production", algorithms=["HS256"])["sub"]
    assert sub1 == sub2  # same user id => same sub claim


def test_google_callback_unverified_email_rejected(client, google_configured, monkeypatch):
    """Google says the email isn't verified -> refuse, don't create a user."""
    fake_profile = {"id": "x", "email": "weird@gmail.com", "verified_email": False}
    monkeypatch.setattr(auth, "google_fetch_profile", lambda code, base_url: fake_profile)

    _, _, state = _begin_google_login(client)
    resp = _complete_google_callback(client, state=state)
    assert "google-email-not-verified" in resp.headers["location"]

    db = SessionLocal()
    assert db.query(models.User).filter(models.User.email == "weird@gmail.com").first() is None
    db.close()


def test_google_callback_error_param(client, google_configured):
    """Google refuses the user (e.g. user cancelled) -> redirect with error."""
    _, _, state = _begin_google_login(client)
    resp = client.get(
        f"/auth/google/callback?error=access_denied&code=&state={state}",
        follow_redirects=False,
    )
    assert "google-auth-denied" in resp.headers["location"]


def test_google_callback_bad_exchange(client, google_configured, monkeypatch):
    """Token exchange failure (network, bad code) -> friendly redirect."""
    def boom(code, base_url):
        raise RuntimeError("google is on fire")
    monkeypatch.setattr(auth, "google_fetch_profile", boom)

    _, _, state = _begin_google_login(client)
    resp = _complete_google_callback(client, state=state)
    assert "google-auth-failed" in resp.headers["location"]
