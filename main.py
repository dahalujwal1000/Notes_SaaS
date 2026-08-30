"""App entrypoint: wires routers, creates tables on startup.

Run locally with zero manual DB setup:
    uvicorn main:app --reload
Swagger UI: http://127.0.0.1:8000/docs
"""

from dotenv import load_dotenv

# Load a local .env file before any module reads os.environ (database.py,
# auth.py). Real environment variables always win (override=False default),
# and on hosting platforms env vars come from the dashboard instead.
load_dotenv()

from contextlib import asynccontextmanager  # noqa: E402

from fastapi import FastAPI  # noqa: E402

import models  # noqa: E402, F401 — imported so create_all() sees every model
from database import Base, engine  # noqa: E402
from routers import notes, users  # noqa: E402


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


@app.get("/", tags=["health"], include_in_schema=False)
def root():
    return {"status": "ok", "docs": "/docs"}
