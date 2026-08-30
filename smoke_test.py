"""Smoke test: exercises every endpoint against a running server.

Usage:
    1. Start the server:  uvicorn main:app --port 8765
    2. Run:               python smoke_test.py http://127.0.0.1:8765

Covers: signup, duplicate-email 400, login (form), bad login 401,
missing-token 401, invalid-token 401, note CRUD, ownership isolation
(other user gets 404), search, pagination, and delete-then-404.
Uses only the stdlib (no httpx/requests dependency).
"""

import json
import random
import string
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
failures: list[str] = []


def call(method: str, path: str, data=None, token=None, form=False, expected=200):
    """Send one HTTP request, print PASS/FAIL vs the expected status."""
    url = BASE + path
    headers = {}
    body = None
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            status_code, raw = resp.status, resp.read().decode()
    except urllib.error.HTTPError as err:
        status_code, raw = err.code, err.read().decode()

    payload = json.loads(raw) if raw else None
    ok = status_code == expected
    line = f"{method} {path} -> {status_code} (expected {expected})"
    print(("PASS  " if ok else "FAIL  ") + line)
    if not ok:
        failures.append(f"{line} payload={payload}")
    return payload


# Unique suffix so the test can run repeatedly against the same DB.
suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
u1, u2 = f"alice_{suffix}@example.com", f"bob_{suffix}@example.com"
pw = "secret123"

# --- Auth ---------------------------------------------------------------- #
call("POST", "/auth/signup", {"email": u1, "password": pw}, expected=201)
call("POST", "/auth/signup", {"email": u1, "password": pw}, expected=400)  # duplicate
token1 = call("POST", "/auth/login", {"username": u1, "password": pw}, form=True)["access_token"]
call("POST", "/auth/login", {"username": u1, "password": "wrong-pass"}, form=True, expected=401)

# --- Notes: auth required -------------------------------------------------- #
call("POST", "/notes", {"title": "No token", "content": "x"}, expected=401)
call("GET", "/notes", token="not-a-real-token", expected=401)

# --- Notes: CRUD ----------------------------------------------------------- #
note = call("POST", "/notes", {"title": "Groceries", "content": "milk, eggs"}, token=token1, expected=201)
call("POST", "/notes", {"title": "Work ideas", "content": "ship the API"}, token=token1, expected=201)
listing = call("GET", "/notes", token=token1)
assert len(listing) == 2, f"expected 2 notes, got {len(listing)}"

got = call("GET", f"/notes/{note['id']}", token=token1)
updated = call("PUT", f"/notes/{note['id']}", {"title": "Groceries (updated)"}, token=token1)
assert updated["title"] == "Groceries (updated)", "update did not apply"
assert updated["content"] == "milk, eggs", "partial update lost content"

# --- Search & pagination ---------------------------------------------------- #
hits = call("GET", "/notes?search=milk", token=token1)
assert len(hits) == 1 and hits[0]["id"] == note["id"], "search did not match"
page = call("GET", "/notes?limit=1&skip=1", token=token1)
assert len(page) == 1, "pagination limit/skip failed"

# --- Ownership isolation: bob must not touch alice's notes ------------------ #
call("POST", "/auth/signup", {"email": u2, "password": pw}, expected=201)
token2 = call("POST", "/auth/login", {"username": u2, "password": pw}, form=True)["access_token"]
call("GET", f"/notes/{note['id']}", token=token2, expected=404)
call("PUT", f"/notes/{note['id']}", {"title": "hacked"}, token=token2, expected=404)
call("DELETE", f"/notes/{note['id']}", token=token2, expected=404)
call("GET", "/notes", token=token2)  # bob's own list is empty

# --- Delete ----------------------------------------------------------------- #
call("DELETE", f"/notes/{note['id']}", token=token1, expected=204)
call("GET", f"/notes/{note['id']}", token=token1, expected=404)

print()
if failures:
    print(f"RESULT: {len(failures)} FAILURE(S)")
    for failure in failures:
        print("  -", failure)
    sys.exit(1)
print("RESULT: ALL SMOKE TESTS PASSED")
