# Task: Multi-Tenant Foundation — Control-Plane DB + Auth/Session

## Context

Repo: `manager-assistant`. Server: `.venv/bin/python3 -m app.main`. Tests:
`.venv/bin/python3 -m pytest tests/`.

First read: `app/database.py` (current single global db), `app/projectkb/models.py`
+ `app/projectkb/paths.py` (the per-project-db pattern this step reuses one
level up), `app/integrations/base.py`, `app/main.py`.

**Why this step, and why first:** the product is moving from one hardcoded
manager to many. Everything downstream (per-manager data isolation, Slack/
Outlook OAuth, the scheduler) needs a way to answer "who is this request
for?" — that's what this step builds. Do this before the data-DB split
(step 15): if you split first, `get_manager_db` has no session to read a
manager_id from, and you'd retrofit auth into every route twice.

**Scope boundary:** this step does NOT touch `app/database.py`'s existing
tables (TeamMember/UnifiedMessage/Project/etc.) or move them per-manager —
that's step 15, deliberately separate. This step only adds the new
control-plane DB, session mechanism, and a dev-only bootstrap login so it's
independently testable before real OAuth (steps 13-14) exists.

## Subproblem 1 — Control-plane DB (`app/controlplane/`)

New module, new engine/db file: `data/controlplane.sqlite` (own engine,
separate from the existing `data/db.sqlite` — this file is the one truly
global piece of state; everything else becomes per-manager in step 15).

Models (`app/controlplane/models.py`):
- `Manager(id [uuid str, pk], email [unique], name, created_at)`
- `Session(token [pk, random 32+ byte urlsafe], manager_id [fk], created_at, expires_at)`
- `SlackInstallation(team_id [pk], manager_id [fk], bot_token, created_at)` —
  bot token stored plain for now (data/ is gitignored, this is single-machine
  testing; note in a comment that encryption-at-rest is a pre-production
  hardening step, not blocking here).
- `OutlookInstallation(manager_id [pk, fk], mailbox_email, token_cache_json
  [Text — a serialized MSAL SerializableTokenCache blob], created_at,
  updated_at)`.

`init_controlplane_db()`: `create_all`, called from `app.main`'s lifespan
alongside the existing `init_db()`.

## Subproblem 2 — Session mechanism

`app/controlplane/auth.py`:
- `create_session(db, manager_id) -> str` — generate a random token
  (`secrets.token_urlsafe(32)`), insert a `Session` row with a reasonable
  expiry (e.g. 30 days), return the token.
- `get_current_manager(request: Request, db=Depends(get_controlplane_db)) ->
  Manager` — a FastAPI dependency: read the session cookie (name it
  `harry_session`), look up the `Session` row, check not expired, return
  the `Manager`. Raise 401 if missing/invalid/expired.
- Login sets the cookie via `Response.set_cookie(..., httponly=True,
  samesite="lax")` — plain cookie auth, no JWT needed. This is what makes
  it curl/browser-testable: a browser carries the cookie automatically
  after login; curl works with `-c cookies.txt -b cookies.txt`.

## Subproblem 3 — Dev bootstrap login (temporary, clearly marked)

Real login is step 13 (Outlook OAuth doubles as login). Until then, add:

`POST /api/auth/dev-login {"email": "...", "name": "..."}` — find-or-create
a `Manager` by email, create a `Session`, set the cookie, return the
manager. Docstring: "Testing/bootstrap only — real login is Outlook OAuth
(step 13). Do not treat this as a production auth path." Gate it visibly
(e.g. a `DEV_AUTH_ENABLED` env flag defaulting to true for now) so it's
trivial to disable later without deleting the code.

`GET /api/auth/me` — returns the current manager via `get_current_manager`
(good smoke-test endpoint: logged out → 401, logged in → manager JSON).

`POST /api/auth/logout` — delete the `Session` row, clear the cookie.

## Test plan

- `POST /api/auth/dev-login` → 200, cookie set, `Manager` row created.
- `GET /api/auth/me` without a cookie → 401.
- `GET /api/auth/me` with the cookie from dev-login → 200, matches manager.
- Session expiry: a `Session` row with `expires_at` in the past → `GET
  /api/auth/me` → 401.
- `POST /api/auth/logout` → subsequent `/api/auth/me` → 401.
- `init_controlplane_db()` is idempotent (call twice, no error).

Don't wire `get_current_manager` into any *existing* route yet (team.py,
dashboard.py, etc.) — that's step 15's job, once there's real per-manager
data to scope those routes to. This step only proves the auth mechanism
itself works end to end.
