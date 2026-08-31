# Notes SaaS API

A production-style **Notes CRUD backend** with JWT authentication — built as a portfolio project to demonstrate real backend skills: auth, relational DB design, REST design, and security fundamentals.

> 🔗 **Live app:** [https://notes-saas-001-final.onrender.com](https://notes-saas-001-final.onrender.com) — the full Notion-style UI, deployed on Render (free tier; the first request after ~15 min of inactivity may take ~50 s while the service wakes up). API reference: [`/docs`](https://notes-saas-001-final.onrender.com/docs).

## Features

- **SaaS workspace dashboard (new)** — collapsible #FAFAFA sidebar with **Upcoming events** (add events with name/description/date; shows title, date + "Today / Tomorrow / in Xd" countdown), Agents & tools, Teamspaces / Private groups, browser-tab view switcher, and a **drag-and-drop Kanban board** (To-do / In progress / In review / Complete) with status dots, emoji avatars, per-column counts, timeline view, search & sort, and floating insight cards
- **Notion-style notes** — sidebar + editor, debounced autosave, search, to-do checkboxes (`- [ ]` syntax)
- **JWT auth** — signup/login, bcrypt-hashed passwords, 60-minute access tokens
- **Email verification (hard gate)** — signup queues a one-time, hashed, 24h-expiring verification link via `mailer.py` (Gmail SMTP / Resend / console modes); **unverified accounts cannot log in** (`EMAIL_VERIFICATION_REQUIRED=true` default, toggle off only for local sandbox testing); verified state exposed via `/auth/me`
- **Sign in with Google (free)** — one-click "Continue with Google" on the login card; Google OAuth is free from Google Cloud Console (no billing required) and creates users as **already verified** — so Google logins never depend on email delivery at all. Falls back gracefully to email+password when credentials aren't configured.
- **Password recovery** — "Forgot password?" on the login screen emails a single-use, 24h-expiring reset link (`reset-password.html`), and unverified users stuck at the login screen can resend their verification link without signing in
- **Ownership-scoped notes** — every query filters by the authenticated user's id; no cross-user data leaks (other users' notes return 404, not 403, to avoid id probing)
- **Full CRUD REST API** with consistent Pydantic `response_model`s and explicit status codes (201 / 400 / 401 / 404 / 204)
- **Extras** — search notes by title/content, pagination (`skip`/`limit`)
- **Zero manual DB setup** — SQLite tables auto-create on first run; swap to PostgreSQL via `DATABASE_URL` only
- **Interactive docs** — Swagger UI at `/docs` with one-click Authorize

## Tech Stack

Python 3.10+ · FastAPI · SQLAlchemy 2.0 (`declarative_base`) · Pydantic v2 · SQLite (dev) / PostgreSQL (prod) · python-jose (JWT) · bcrypt · Uvicorn

## Project Structure

```
Notes_SaaS/
├── main.py              # App entrypoint, wires routers, creates tables, serves the UI
├── static/              # Single-page UI: index.html, verify.html, reset-password.html + style.css + app.js (no framework)
├── database.py          # Engine, SessionLocal, Base, get_db() dependency
├── models.py            # SQLAlchemy models: User, Note
├── schemas.py           # Pydantic v2 request/response schemas
├── auth.py              # bcrypt hashing, JWT create/verify, get_current_user()
├── routers/
│   ├── users.py         # POST /auth/signup, POST /auth/login
│   ├── notes.py         # /notes CRUD (all routes auth-protected)
│   └── tasks.py         # /tasks kanban CRUD + move (auth-protected)
├── smoke_test.py        # End-to-end test against a live server (stdlib only)
├── tests/               # pytest suite — API tested in-process, isolated DB
│   ├── conftest.py
│   ├── test_api.py      # auth + notes
│   └── test_tasks.py    # kanban tasks
└── requirements.txt     # Pinned runtime deps (see requirements-dev.txt for tests)
```

## Setup & Run

```bash
python -m venv venv
venv\Scripts\activate            # Windows  (Linux/macOS: source venv/bin/activate)
pip install -r requirements.txt

# Optional but recommended — otherwise an ephemeral random key is used and
# tokens are invalidated on every server restart:
set SECRET_KEY=change-me-to-a-long-random-string

uvicorn main:app --reload
```

Open http://127.0.0.1:8000/docs → click **Authorize** → log in with `email` + `password` → try the endpoints.

## API Endpoints

| Method | Path              | Auth | Description                        |
|--------|-------------------|------|------------------------------------|
| POST   | `/auth/signup`    | —    | Create user (201; 400 if email taken) |
| POST   | `/auth/login`     | —    | Form login → `{access_token}`      |
| GET    | `/auth/google/login` | —  | Redirect to Google's consent screen (free OAuth) |
| GET    | `/auth/google/callback` | — | Google's reply → creates/finds user (auto-verified) + JWT → redirects to app |
| POST   | `/auth/verify`    | —    | Verify email with emailed token     |
| POST   | `/auth/resend-verification` | JWT | Re-send verification link (in-app) |
| POST   | `/auth/resend-verification-email` | — | Re-send verification link (login screen, by email, throttled, non-enumerating) |
| POST   | `/auth/forgot-password` | —    | Email a password-reset link (non-enumerating) |
| POST   | `/auth/reset-password` | —    | Set new password using emailed token (single-use, 24 h) |
| POST   | `/notes`          | JWT  | Create note (201)                  |
| GET    | `/notes`          | JWT  | List my notes (`?skip=&limit=&search=`) |
| GET    | `/notes/{id}`     | JWT  | Get my note (404 if not mine)      |
| PUT    | `/notes/{id}`     | JWT  | Partial update (404 if not mine)   |
| DELETE | `/notes/{id}`     | JWT  | Delete (204; 404 if not mine)      |
| GET    | `/tasks`          | JWT  | List my tasks (`?status=` filter)  |
| POST   | `/tasks`          | JWT  | Create task (201; appends to column) |
| PATCH  | `/tasks/{id}`     | JWT  | Move / rename (status + position)  |
| DELETE | `/tasks/{id}`     | JWT  | Delete (204; 404 if not mine)      |
| GET    | `/events`         | JWT  | List my events (soonest first)     |
| POST   | `/events`         | JWT  | Create event (name, description, date) |
| PATCH  | `/events/{id}`    | JWT  | Update event                       |
| DELETE | `/events/{id}`    | JWT  | Delete (204; 404 if not mine)      |

### Example requests

```bash
# Signup
curl -X POST http://127.0.0.1:8000/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com", "password": "supersecret123"}'

# Login (form-encoded)
curl -X POST http://127.0.0.1:8000/auth/login \
  -d "username=user@example.com&password=supersecret123"

# Create a note
curl -X POST http://127.0.0.1:8000/notes \
  -H "Authorization: Bearer <ACCESS_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"title": "Groceries", "content": "milk, eggs"}'
```

## Testing

**1. Automated suite (recommended)** — runs the whole API in-process with an
isolated throwaway database; no server and no `notes.db` touched:

```bash
pip install -r requirements-dev.txt
venv\Scripts\python -m pytest tests -v
```

**2. Live smoke test** against a real running server:

```bash
uvicorn main:app --port 8765                 # terminal 1
python smoke_test.py http://127.0.0.1:8765   # terminal 2
```

**3. Interactive:** Swagger UI at `/docs` (green **Authorize** button to log
in), or any HTTP client — Postman, curl, PowerShell `Invoke-RestMethod`.

## Security Notes

- Passwords stored only as bcrypt hashes (`bcrypt.hashpw`/`checkpw`, used directly — passlib 1.7.4 is incompatible with bcrypt 5.x).
- `SECRET_KEY` is read from the environment, never hardcoded.
- JWT `sub` claim carries the user id; note ownership is enforced server-side on every query.
- All DB/session/current-user access goes through FastAPI `Depends` dependency injection.

## Deployment (Render free tier)

1. **New + → PostgreSQL** — create the database, then copy its **Internal Database URL**.
2. **New + → Web Service** — connect this repo:
   - Build command: `pip install -r requirements.txt`
   - Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
3. **Environment → Add from .env** — paste:

   ```ini
   SECRET_KEY=<long-random-string>
   DATABASE_URL=<paste Render's Internal Database URL>
   MAIL_BACKEND=smtp
   MAIL_USER=<your-gmail-address>
   MAIL_APP_PASSWORD=<16-char app password from Google>
   EMAIL_VERIFICATION_REQUIRED=true
   GOOGLE_CLIENT_ID=<your-id.apps.googleusercontent.com>
   GOOGLE_CLIENT_SECRET=<your-secret>
   ```

   Email verification keeps working without the SMTP vars too: `MAIL_BACKEND`
   defaults to `console`, which logs the verification link server-side and
   never sends email. `EMAIL_VERIFICATION_REQUIRED=true` (the default) blocks
   login until the account is verified — set it to `false` only for local
   sandbox testing.

   Plain `postgresql://` URLs are upgraded to SQLAlchemy's psycopg 3 dialect
   automatically — no manual scheme editing required.

### Sign in with Google — free setup (5 minutes)

1. Go to **console.cloud.google.com** → create/select a project.
2. **APIs & Services → OAuth consent screen** → External → fill app name + your email → Save.
3. **APIs & Services → Credentials → Create credentials → OAuth client ID → Web application**.
4. In **Authorized redirect URIs**, add *both*:
   - `http://127.0.0.1:8000/auth/google/callback` (local dev)
   - `https://<your-app>.onrender.com/auth/google/callback` (your Render URL)
5. Click **Create** → copy the **Client ID** + **Client secret** → paste them into Render → **save → redeploy**.

> 💡 Free: Google OAuth sign-in has no billing/usage cost, and works on
> Render's free tier. Google already verified the email, so these accounts
> are `is_verified=true` immediately — no verification email, ever.
4. Deploy. Swagger UI lives at `https://<your-app>.onrender.com/docs`.

Locally, the same variables can live in a `.env` file (loaded via
python-dotenv, gitignored) — real environment variables always win.
