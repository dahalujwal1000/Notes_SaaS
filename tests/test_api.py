"""In-process API tests for the Notes SaaS backend.

Run:
    venv\\Scripts\\python -m pytest tests -v

No running server and no ./notes.db needed — conftest.py swaps in an
isolated SQLite test database and resets the schema for every test.
"""

INVALID_AUTH = {"Authorization": "Bearer not-a-valid-token"}

# Helpers live in conftest.py so both test modules share them.
from conftest import auth_headers, signup, unique_email, verify_last_signup  # noqa: E402, F401


# -------------------------------- auth ----------------------------------- #

def test_health_and_homepage(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    # The homepage is now the static single-page UI.
    home = client.get("/")
    assert home.status_code == 200
    assert "text/html" in home.headers["content-type"]


def test_signup_returns_201_with_public_fields_only(client):
    email = unique_email()
    resp = signup(client, email=email)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["id"] == 1  # fresh schema per test -> first user is id 1
    assert body["email"] == email
    assert "created_at" in body
    assert "password" not in body
    assert "hashed_password" not in body  # hash never leaves the server


def test_signup_rejects_duplicate_email(client):
    email = unique_email()
    assert signup(client, email=email).status_code == 201
    resp = signup(client, email=email)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Email already registered"


def test_signup_rejects_short_password(client):
    assert signup(client, password="short").status_code == 422


def test_signup_rejects_overlong_password(client):
    # bcrypt hashes at most 72 bytes, and bcrypt 5.x raises instead of
    # truncating — an >72-byte password must be rejected with 422 before
    # hashing, not crash signup with a 500.
    assert signup(client, password="x" * 80).status_code == 422


def test_signup_rejects_invalid_email(client):
    assert signup(client, email="not-an-email").status_code == 422


def test_login_returns_bearer_token(client):
    email = unique_email()
    assert signup(client, email=email).status_code == 201
    # Hard gate: the account must be verified before login succeeds.
    verify_last_signup(client)
    resp = client.post(
        "/auth/login", data={"username": email, "password": "supersecret123"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


def test_login_rejects_unverified_user(client):
    email = unique_email()
    signup(client, email=email)
    resp = client.post(
        "/auth/login", data={"username": email, "password": "supersecret123"}
    )
    assert resp.status_code == 401


def test_login_rejects_wrong_password(client):
    email = unique_email()
    signup(client, email=email)
    resp = client.post(
        "/auth/login", data={"username": email, "password": "totally-wrong"}
    )
    assert resp.status_code == 401


def test_login_rejects_unknown_email(client):
    resp = client.post(
        "/auth/login",
        data={"username": "ghost@example.com", "password": "whatever123"},
    )
    assert resp.status_code == 401


# -------------------------------- notes ---------------------------------- #

def test_notes_endpoints_require_auth(client):
    assert client.get("/notes").status_code == 401
    assert client.post("/notes", json={"title": "t"}).status_code == 401
    assert client.get("/notes/1").status_code == 401
    assert client.put("/notes/1", json={"title": "t"}).status_code == 401
    assert client.delete("/notes/1").status_code == 401


def test_notes_reject_invalid_token(client):
    assert client.get("/notes", headers=INVALID_AUTH).status_code == 401


def test_create_and_read_note(client):
    headers = auth_headers(client)
    resp = client.post(
        "/notes",
        json={"title": "Groceries", "content": "milk, eggs"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    note = resp.json()
    assert note["id"] == 1
    assert note["title"] == "Groceries"
    assert note["content"] == "milk, eggs"
    assert note["user_id"] == 1
    assert note["created_at"] == note["updated_at"]

    got = client.get(f"/notes/{note['id']}", headers=headers)
    assert got.status_code == 200
    assert got.json()["title"] == "Groceries"


def test_note_title_cannot_be_empty(client):
    headers = auth_headers(client)
    resp = client.post(
        "/notes", json={"title": "", "content": "x"}, headers=headers
    )
    assert resp.status_code == 422


def test_update_note_is_partial(client):
    headers = auth_headers(client)
    note = client.post(
        "/notes", json={"title": "Original", "content": "keep me"}, headers=headers
    ).json()
    resp = client.put(
        f"/notes/{note['id']}", json={"title": "Edited"}, headers=headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Edited"
    assert body["content"] == "keep me"  # untouched field survives


def test_delete_note_returns_204_then_404(client):
    headers = auth_headers(client)
    note = client.post(
        "/notes", json={"title": "Doomed"}, headers=headers
    ).json()
    assert client.delete(f"/notes/{note['id']}", headers=headers).status_code == 204
    assert client.get(f"/notes/{note['id']}", headers=headers).status_code == 404


def test_user_cannot_access_other_users_notes(client):
    owner_headers = auth_headers(client)
    intruder_headers = auth_headers(client)
    note = client.post(
        "/notes", json={"title": "Secret"}, headers=owner_headers
    ).json()
    note_id = note["id"]

    # Ownership enforced server-side: intruder gets 404, not 403,
    # so note ids can't be probed.
    assert client.get(f"/notes/{note_id}", headers=intruder_headers).status_code == 404
    assert (
        client.put(
            f"/notes/{note_id}", json={"title": "hacked"}, headers=intruder_headers
        ).status_code
        == 404
    )
    assert client.delete(f"/notes/{note_id}", headers=intruder_headers).status_code == 404
    assert client.get("/notes", headers=intruder_headers).json() == []


def test_search_and_pagination(client):
    headers = auth_headers(client)
    for title in ("alpha plan", "beta plan", "gamma notes"):
        client.post(
            "/notes",
            json={"title": title, "content": f"content of {title}"},
            headers=headers,
        )

    hits = client.get("/notes", params={"search": "alpha"}, headers=headers).json()
    assert [n["title"] for n in hits] == ["alpha plan"]

    # List is newest-first; skip=1 drops the newest, limit=2 caps the page.
    page = client.get(
        "/notes", params={"limit": 2, "skip": 1}, headers=headers
    ).json()
    assert [n["title"] for n in page] == ["beta plan", "alpha plan"]

    everything = client.get("/notes", headers=headers).json()
    assert [n["title"] for n in everything] == [
        "gamma notes",
        "beta plan",
        "alpha plan",
    ]


# ----------------------------- favorites --------------------------------- #

def test_favorite_requires_auth(client):
    assert client.put("/notes/1/favorite", json={"favorite": True}).status_code == 401


def test_favorite_star_and_unstar(client):
    headers = auth_headers(client)
    note = client.post(
        "/notes", json={"title": "Star me", "content": "shine"}, headers=headers
    ).json()

    # New notes start unstarred.
    assert note["favorite"] is False

    starred = client.put(
        f"/notes/{note['id']}/favorite", json={"favorite": True}, headers=headers
    )
    assert starred.status_code == 200, starred.text
    assert starred.json()["favorite"] is True

    # Regular title update must not wipe the favorite flag.
    client.put(
        f"/notes/{note['id']}", json={"title": "Still starred"}, headers=headers
    )
    assert client.get(f"/notes/{note['id']}", headers=headers).json()["favorite"] is True

    unstarred = client.put(
        f"/notes/{note['id']}/favorite", json={"favorite": False}, headers=headers
    )
    assert unstarred.status_code == 200
    assert unstarred.json()["favorite"] is False


def test_favorite_rejects_missing_body(client):
    headers = auth_headers(client)
    note = client.post("/notes", json={"title": "x"}, headers=headers).json()
    assert client.put(f"/notes/{note['id']}/favorite", headers=headers).status_code == 422
    # Lists/objects can't be coerced to bool (unlike "yes"/"1", which lax mode accepts).
    assert client.put(
        f"/notes/{note['id']}/favorite", json={"favorite": ["yes"]}, headers=headers
    ).status_code == 422


def test_favorite_cannot_touch_other_users_notes(client):
    owner_headers = auth_headers(client)
    intruder_headers = auth_headers(client)
    note = client.post("/notes", json={"title": "Secret"}, headers=owner_headers).json()
    assert (
        client.put(
            f"/notes/{note['id']}/favorite",
            json={"favorite": True},
            headers=intruder_headers,
        ).status_code
        == 404
    )


def test_favorite_persists_in_list(client):
    headers = auth_headers(client)
    a = client.post("/notes", json={"title": "A"}, headers=headers).json()
    b = client.post("/notes", json={"title": "B"}, headers=headers).json()
    client.put(f"/notes/{b['id']}/favorite", json={"favorite": True}, headers=headers)
    notes = {n["title"]: n for n in client.get("/notes", headers=headers).json()}
    assert notes["A"]["favorite"] is False
    assert notes["B"]["favorite"] is True
