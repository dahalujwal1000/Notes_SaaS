"""App entrypoint: wires routers, creates tables on startup, serves the UI.

Run locally with zero manual DB setup:
    uvicorn main:app --reload
App UI:     http://127.0.0.1:8000/            (static/ single-page app)
Swagger UI: http://127.0.0.1:8000/docs
"""

from dotenv import load_dotenv

# Load a local .env file before any module reads os.environ (database.py,
# auth.py). Real environment variables always win (override=False default),
# and on hosting platforms env vars come from the dashboard instead.
load_dotenv()

import os  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from hashlib import sha256  # noqa: E402
from pathlib import Path  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402
from starlette.middleware.trustedhost import TrustedHostMiddleware  # noqa: E402

import models  # noqa: E402, F401 — imported so create_all() sees every model
from database import Base, engine  # noqa: E402
from routers import ai, events, notes, tasks, users  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"

# ---------------------------------------------------------------------------
# Security response headers (defense in depth; the SPA already renders all
# dynamic content with textContent, which is XSS-safe).
# ---------------------------------------------------------------------------
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Set hardening headers on every response (incl. static files)."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
        response.headers.setdefault("Content-Security-Policy", _CSP)
        if request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response


# Allowed Host headers. This stops an attacker from issuing requests with a
# forged Host header (e.g. `evil.com`) to make personally-addressed emails
# (verification links) contain a malicious base URL. List custom domains via
# TRUSTED_HOSTS="app.com,www.app.com" (comma-separated, supports *.wildcards).
_TRUSTED_HOSTS = [
    h.strip()
    for h in os.environ.get(
        "TRUSTED_HOSTS",
        "127.0.0.1,localhost,[::1],*.onrender.com",
    ).split(",")
    if h.strip()
]


class NoCacheStaticFiles(StaticFiles):
    """Static assets stay cache-bustable: `Cache-Control: no-cache` forces
    the browser to revalidate via ETag on every load, so a fresh deploy is
    picked up immediately instead of heuristically serving stale CSS/JS."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Create tables on startup if they don't exist, then backfill new
    columns on existing deployments (dev convenience)."""
    Base.metadata.create_all(bind=engine)
    _ensure_columns()
    yield


def _ensure_columns() -> None:
    """Add columns that exist on the model but not yet on a live table.

    `create_all` only creates *missing tables*; it never alters existing
    ones. This tiny migration adds the email-verification columns to the
    `users` table (and the favorite star to `notes`) in place.
    """
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import text

    dialect = engine.dialect.name
    inspector = sa_inspect(engine)
    additions = []

    try:
        user_columns = {c["name"] for c in inspector.get_columns("users")}
    except Exception:
        user_columns = set()  # table not created yet — create_all handles a fresh DB
    if user_columns:
        if "is_verified" not in user_columns:
            default = "0" if dialect == "sqlite" else "FALSE"
            additions.append(
                f"ALTER TABLE users ADD COLUMN is_verified BOOLEAN NOT NULL DEFAULT {default}"
            )
        if "verification_token_hash" not in user_columns:
            additions.append("ALTER TABLE users ADD COLUMN verification_token_hash VARCHAR(64)")
        if "verification_expires" not in user_columns:
            additions.append("ALTER TABLE users ADD COLUMN verification_expires TIMESTAMP")
        if "reset_token_hash" not in user_columns:
            additions.append("ALTER TABLE users ADD COLUMN reset_token_hash VARCHAR(64)")
        if "reset_expires" not in user_columns:
            additions.append("ALTER TABLE users ADD COLUMN reset_expires TIMESTAMP")

    try:
        note_columns = {c["name"] for c in inspector.get_columns("notes")}
    except Exception:
        note_columns = set()
    if note_columns and "favorite" not in note_columns:
        default = "0" if dialect == "sqlite" else "FALSE"
        additions.append(
            f"ALTER TABLE notes ADD COLUMN favorite BOOLEAN NOT NULL DEFAULT {default}"
        )

    if additions:
        with engine.begin() as conn:
            for statement in additions:
                conn.execute(text(statement))


app = FastAPI(
    title="Notes SaaS API",
    description="SaaS-style Notes CRUD API with JWT authentication.",
    version="2.0.0",
    lifespan=lifespan,
)

# Order: TrustedHost runs first (rejects forged Host headers), then the
# security-headers layer wraps every response.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=_TRUSTED_HOSTS, www_redirect=False)
app.add_middleware(SecurityHeadersMiddleware)

app.include_router(users.router)
app.include_router(notes.router)
app.include_router(tasks.router)
app.include_router(events.router)
app.include_router(ai.router)


@app.get("/health", tags=["health"])
def health():
    """JSON health probe (the homepage itself is the static app UI)."""
    return {"status": "ok", "docs": "/docs"}


def _asset_version(path: Path) -> str:
    """Short content hash of a static asset, used to cache-bust it.

    The version changes whenever the file's bytes change, so browsers pick
    up a fresh deploy immediately — no more hand-editing `?v=` query strings.
    """
    try:
        return sha256(path.read_bytes()).hexdigest()[:12]
    except OSError:
        return "0"


@app.get("/", include_in_schema=False, tags=["ui"])
def index() -> HTMLResponse:
    """Serve the SPA with hash-busting versions for app.js / style.css."""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace("__APP_VERSION__", _asset_version(STATIC_DIR / "app.js"))
    html = html.replace("__CSS_VERSION__", _asset_version(STATIC_DIR / "style.css"))
    return HTMLResponse(html)


# Static UI last, so API routes always win over the SPA catch-all mount.
app.mount("/", NoCacheStaticFiles(directory=str(STATIC_DIR), html=True), name="static")
