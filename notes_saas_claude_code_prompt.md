# Project Prompt: Notes SaaS API (FastAPI Backend)

## Context
Build backend API for a Notes app — SaaS-style CRUD application. Purpose: portfolio project for internship/job applications. Priority: clean, correct, production-style code that demonstrates real backend skill (auth, DB design, REST API, security basics). Not a toy script.

## Tech Stack
- **Language:** Python 3.10+
- **Framework:** FastAPI
- **ORM:** SQLAlchemy 2.0+ (use `sqlalchemy.orm.declarative_base`, not deprecated `sqlalchemy.ext.declarative`)
- **Validation:** Pydantic v2
- **DB (dev):** SQLite (`notes.db`)
- **DB (prod, optional):** PostgreSQL — connection string swap only, no other changes
- **Auth:** JWT via `python-jose[cryptography]`
- **Password hashing:** `passlib[bcrypt]`
- **Server:** Uvicorn
- **Form/file handling:** `python-multipart` (needed for OAuth2 login form)

## Architecture
Layered structure, single responsibility per file:

```
Notes_SaaS/
├── main.py              # App entrypoint, wires routers, creates tables
├── database.py          # Engine, SessionLocal, Base, get_db() dependency
├── models.py            # SQLAlchemy ORM models: User, Note
├── schemas.py           # Pydantic schemas: request/response shapes
├── auth.py              # Password hashing, JWT create/verify, get_current_user()
└── routers/
    ├── __init__.py
    ├── users.py         # /auth/signup, /auth/login
    └── notes.py         # /notes CRUD, all routes protected by auth
```

## Data Model
**User**
- id (PK)
- email (unique, indexed)
- hashed_password
- created_at

**Note**
- id (PK)
- title
- content
- user_id (FK -> users.id)
- created_at
- updated_at
- Relationship: one User has many Notes. Notes only visible/editable by owning user.

## Auth Rules
- Passwords never stored plaintext — bcrypt hash only.
- Login returns JWT access token (`sub` claim = user id, expiry ~60 min).
- All `/notes` routes require valid JWT via `Authorization: Bearer <token>` header.
- Invalid/missing/expired token → 401 Unauthorized.
- Users can only access their own notes — enforce via `user_id` filter on every query, never trust a client-supplied user id.

## API Endpoints
```
POST   /auth/signup     -> create user, return id + email
POST   /auth/login      -> verify credentials, return JWT

POST   /notes           -> create note (auth required)
GET    /notes           -> list current user's notes
GET    /notes/{id}      -> get one note (must belong to current user, else 404)
PUT    /notes/{id}      -> update note (must belong to current user)
DELETE /notes/{id}      -> delete note (must belong to current user)
```

## Build Phases

**Phase 1 — Foundation**
- Set up `database.py` (engine, session, Base)
- Define models in `models.py`
- Confirm tables can be created via `Base.metadata.create_all()`

**Phase 2 — Schemas**
- `UserCreate`, `UserOut`, `UserLogin`
- `NoteCreate`, `NoteUpdate`, `NoteOut`
- Use Pydantic `from_attributes = True` (v2 syntax) for ORM compatibility

**Phase 3 — Auth**
- Password hash/verify functions
- JWT create/decode functions
- `get_current_user()` FastAPI dependency, raises 401 on failure
- Secret key loaded from environment variable, never hardcoded

**Phase 4 — Routers**
- `routers/users.py`: signup (reject duplicate email), login (verify + issue token)
- `routers/notes.py`: full CRUD, every route scoped to `current_user`

**Phase 5 — Wiring**
- `main.py`: create FastAPI app, include both routers, run `create_all()` on startup
- Test every endpoint via `/docs` (Swagger UI) before moving on

**Phase 6 — Polish (optional, do after core works)**
- Search notes by title/content (query param)
- Pagination (`skip`, `limit` query params)
- Proper error messages and status codes throughout

**Phase 7 — Deployment**
- `.env` file for secrets (SECRET_KEY, DB URL), load via `python-dotenv`
- `requirements.txt` (pin versions used)
- Deploy to Render or Railway free tier
- Write README: setup steps, endpoint list, example requests, screenshot of Swagger docs

## Rules / Constraints
- No hardcoded secrets in code — use `.env` + `os.environ`
- Every note-related route must filter by `user_id` — no cross-user data leaks
- Use dependency injection (`Depends`) for DB session and current user everywhere, don't reinvent per-route
- Keep responses consistent — use Pydantic response_model on every route
- Prefer explicit HTTPException with correct status codes (400, 401, 404) over generic errors
- Code should run locally via `uvicorn main:app --reload` with zero manual DB setup (auto-create tables on first run)

## Deliverable
Working FastAPI project matching structure above, runnable locally, all endpoints functional and testable via Swagger UI, ready to deploy and add to resume/portfolio.
