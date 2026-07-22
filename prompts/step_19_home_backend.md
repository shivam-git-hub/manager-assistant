# Step 19 — Home Dashboard Backend (todos + events/updates APIs)

Authority: `spec/architecture_v2_kb.md` §2–§3 (vocabulary + per-user
schema). This step builds the per-user KB tables and the two API surfaces
the home dashboard (wireframe 2.png) needs that don't exist yet: the
user-maintained **TODOs** panel and the **Updates** panel (a query over
events). Events will be EMPTY until the ingest/heartbeat jobs land in later
steps — that's expected; endpoints must be real, no seeded/dummy data.

Ground rules: TDD (tests first). Sim-time service for all timestamps —
wall-clock reads forbidden. Do NOT edit CLAUDE.md, `frontend/`, the
simulator, or `app/static/*.html`. Do not touch `app/projects/`,
`app/api/projects_registry.py`, or `app/controlplane/` (Step 18's
territory, just landed — read-only for you).

## 1. Per-user tables (in `app/database.py` on the shared `Base` — they
live in each manager's `db.sqlite` and are auto-created by
`init_manager_db`; follow how existing tables there are declared)

Create all four now so the schema is settled once; this step's APIs only
use `events` and `todos` (claims get populated by the future ingest step):

- `Todo` — `id` (pk uuid hex), `text`, `due` (nullable datetime),
  `status` (`open|done`, default open), `created_at`, `updated_at`.
- `Claim` — `id` (pk uuid hex, the claim_id), `text`, `thread_key`
  (nullable), `content_hash`, `processed` (bool, default False),
  `created_at`.
- `ClaimSource` — `id` (pk), `claim_id`, `message_id` (→
  `unified_messages.id`). Unique on the pair.
- `Event` — `id` (pk uuid hex), `type`
  (`status_update|blocker|clarification|commitment|request|conflict|fyi`),
  `severity` (int 0–3), `title`, `body` (nullable), `project_ids` (JSON
  text list, nullable), `task_ids` (JSON text list, nullable),
  `claim_ids` (JSON text list, nullable), `general` (bool — true when not
  tied to any project/task), `dreamed` (bool, default False), `ui_state`
  (`shown|dismissed|promoted`, default shown), `created_at`.

## 2. APIs — new router `app/api/home.py`, cookie-auth via
`Depends(get_manager_db)` like the other routers; register in `app/main.py`

TODOs (user-maintained, no LLM anywhere):
- `GET /api/todos?status=` — list, newest first; optional filter.
- `POST /api/todos` `{text, due?}` → created row.
- `PATCH /api/todos/{id}` `{text?, due?, status?}` → updated row; 404 if
  not found in this user's db.
- `DELETE /api/todos/{id}` → 204.

Updates panel (query over events, per spec §2 — notifications are NOT a
table):
- `GET /api/events?min_severity=1&limit=50&include_dismissed=false` —
  returns events where (`severity >= min_severity` AND `ui_state !=
  "dismissed"`) OR `ui_state == "promoted"`; if `include_dismissed=true`,
  dismissed rows come back too (for a "View All" screen). Order: severity
  desc, then created_at desc. Response: `{events: [...], total_matching,
  max_severity}` — `max_severity` of the returned set drives the panel's
  aggregate status icon (red/yellow/green flower in the wireframe).
- `POST /api/events/{id}/dismiss` → `ui_state="dismissed"`.
- `POST /api/events/{id}/promote` → `ui_state="promoted"` (works even on a
  previously dismissed event).

## 3. Tests first — `tests/test_home_backend.py`

Use the existing `client` fixture pattern (auto dev-login, throwaway
manager). For event-read tests, insert Event rows directly through the
manager's session (there is no create-event API on purpose — only jobs
create events).

Cover: todo CRUD lifecycle incl. status flip and 404s; event filtering
(severity threshold, dismissed excluded, promoted included even below
threshold, include_dismissed=true), ordering, max_severity; dismiss →
excluded from default query; promote after dismiss → included again;
isolation — two dev-login users, one's todos/events invisible to the other.

## 4. Done criteria

Full suite green (`.venv/bin/python3 -m pytest tests/`). Report files
changed, pytest summary, and any divergence from this prompt (code wins —
explain).
