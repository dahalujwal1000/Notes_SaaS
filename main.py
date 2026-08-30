"""App entrypoint: wires routers, creates tables on startup.

Run locally with zero manual DB setup:
    uvicorn main:app --reload
Swagger UI: http://127.0.0.1:8000/docs
"""

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

from contextlib import asynccontextmanager  # noqa: E402
from pathlib import Path  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

import models  # noqa: E402, F401 — imported so create_all() sees every model
from database import Base, engine  # noqa: E402
from routers import notes, tasks, users  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Create tables on startup if they don't exist (dev convenience)."""
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Notes SaaS API",
    description="SaaS-style Notes CRUD API with JWT authentication.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(users.router)
app.include_router(notes.router)
app.include_router(tasks.router)


@app.get("/health", tags=["health"])
def health():
    """JSON health probe (the homepage itself is the static app UI)."""
    return {"status": "ok", "docs": "/docs"}


# Static UI last, so API routes always win over the SPA catch-all mount.
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
