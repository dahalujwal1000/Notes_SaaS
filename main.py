"""App entrypoint: wires routers, creates tables on startup.

Run locally with zero manual DB setup:
    uvicorn main:app --reload
Swagger UI: http://127.0.0.1:8000/docs
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

import models  # noqa: F401 — imported so create_all() sees every model
from database import Base, engine
from routers import notes, users


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
