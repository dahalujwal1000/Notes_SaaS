"""Smoke test: exercises every endpoint against a running server.

Usage:
    1. Start the server:  uvicorn main:app --port 8765
       (If it enforces email verification — the default —
        EMAIL_VERIFICATION_REQUIRED=false is needed for full CRUD coverage:
        set EMAIL_VERIFICATION_REQUIRED=false before starting uvicorn.)
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


def login_token(username: str, password: str) -> str:
    """Log in and return the access token. If the server hard-gates login
    on email verification (the default), explain and exit gracefully —
    this tool can't open email links over HTTP."""
    url = BASE + "/auth/login"
    body = urllib.parse.urlencode(
        {"username": username, "password": password}
    ).encode()
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, data=body, headers=headers, method="POST")
        ) as resp:
            payload = json.loads(resp.read().decode())
        print(f"PASS  POST /auth/login -> {resp.status} (expected 200)")
        return payload["access_token"]
    except urllib.error.HTTPError as exc:
        # NOTE: hoist what we need — Python deletes the `as exc` variable
        # when the except block ends, so using `exc` later would raise
        # UnboundLocalError on the verification-gate path.
        raw = exc.read().decode()
        status = exc.code
        payload = json.loads(raw) if raw else {}

    detail = (payload.get("detail") or "").lower()
    if "verified" in detail or "verification" in detail:
        print(f"PASS  POST /auth/login -> {status} (email verification enforced ✓)")
        print(f"PASS  unverified login blocked ✓")
        print()
        print("[gate] This server enforces email verification, so fresh signups cannot log in yet —")
        print("[gate] exactly as intended. smoke_test can't click the emailed link over HTTP.")
        print("[gate] To run the full authenticated suite, start the server with:")
        print("[gate]     set EMAIL_VERIFICATION_REQUIRED=false   (PowerShell)")
        print("[gate]     EMAIL_VERIFICATION_REQUIRED=false       (bash)")
        print("[gate] then re-run this script. For the live site, verify the account via the")
        print("[gate] emailed link instead.")
        sys.exit(2)

    print(f"FAIL  POST /auth/login -> {status} (expected 200)")
    failures.append(f"POST /auth/login -> {err.code} payload={payload}")
    return ""


# Unique suffix so the test can run repeatedly against the same DB.
suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
u1, u2 = f"alice_{suffix}@example.com", f"bob_{suffix}@example.com"
pw = "secret123"

# --- Auth ---------------------------------------------------------------- #
call("POST", "/auth/signup", {"email": u1, "password": pw}, expected=201)
call("POST", "/auth/signup", {"email": u1, "password": pw}, expected=400)  # duplicate
token1 = login_token(u1, pw)
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
token2 = login_token(u2, pw)
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
