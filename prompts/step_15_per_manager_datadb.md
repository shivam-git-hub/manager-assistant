# Task: Per-Manager Data DB Split

## Context

Repo: `manager-assistant`. Depends on steps 12-14 (auth, Outlook connect,
Slack connect) all being done — this step rewires *existing* routes to be
manager-scoped, which only makes sense once there's a `get_current_manager`
to scope them to.

First read: `app/database.py` (everything being split), `app/projectkb/models.py`
+ `app/projectkb/paths.py` (the exact pattern being extended one level up:
`get_project_engine`/`get_project_session`/`init_project_db`), every file
using `Depends(get_db)` (grep for it — this is the full blast radius list:
`app/integrations/*.py`, `app/api/*.py`, `app/kb/*.py`, `app/agent/*.py`,
`app/outbound.py`, `app/scheduler.py`, `app/followups.py`, `app/brief.py`,
`app/health.py`).

**Why this is its own step, not folded into 12-14:** it's the single
largest mechanical change in this whole migration (every route handler's
DB dependency changes) and touches nothing conceptually new — better done
once, after the pieces it depends on (auth, connect flows writing to the
right place) already exist and are tested independently.

**Design, confirmed with the user:** NOT a shared DB with a `manager_id`
column on every table (that means every query needs a `WHERE manager_id=`
filter, easy to forget, easy to leak data across tenants by mistake).
Instead: each manager gets their own directory and their own `db.sqlite`,
exactly like `projects/<slug>/project.db` already works per-project — just
one level up. No cross-manager query filtering needed anywhere; isolation
is structural (different files), not query-discipline-dependent.

## Subproblem 1 — Manager home directory (`app/tenancy/paths.py`)

```
managers/<manager_id>/
  db.sqlite              # TeamMember, UnifiedMessage, Project, Task, Meeting,
                          # ActionItem, Leave, ReassignmentSuggestion, Digest,
                          # ChatMessage -- everything currently in the global
                          # data/db.sqlite except the control-plane tables
  projects/<slug>/        # unchanged internally, just nested now instead of
                          # top-level projects/<slug>/
  job_state.json          # relocated from data/job_state.json
  tracked_contacts.json   # relocated from data/tracked_contacts.json
```

`manager_dir(manager_id)`, `manager_db_path(manager_id)`,
`ensure_manager_scaffold(manager_id)` (creates the dir, initializes
`db.sqlite`, seeds Harry — mirrors `app.database.init_db()`'s Harry-seed,
now per manager). Same idempotent-creation pattern as
`projectkb.paths.ensure_project_scaffold`.

## Subproblem 2 — `get_manager_engine`/`get_manager_session` (`app/tenancy/db.py`)

Directly mirrors `app/projectkb/models.py`'s `_engine_for`/`get_project_engine`/
`get_project_session`/`init_project_db` — same `lru_cache`-per-path pattern,
same `Base.metadata.create_all` idempotent init. The existing `Base`/models
in `app/database.py` (TeamMember, UnifiedMessage, etc.) stay as-is
structurally — only *which engine* they're bound to changes per-request.

## Subproblem 3 — `get_manager_db` FastAPI dependency

Replaces `app.database.get_db` for all manager-scoped routes:
```python
def get_manager_db(manager: Manager = Depends(get_current_manager)):
    session = get_manager_session(manager.id)
    try:
        yield session
    finally:
        session.close()
```
Go through the full blast-radius list from "First read" above and swap
`Depends(get_db)` → `Depends(get_manager_db)`. This is mechanical but
touches many files — do it file by file, run the test suite after each,
don't try it as one giant sed pass.

## Subproblem 4 — `init_db()` restructure

Today's `init_db()` (global schema + Harry seed + entity backfill + job
seed) runs once at boot. Post-split: control-plane db init stays global
(step 12 already does this); the per-manager equivalent
(`ensure_manager_scaffold`) runs at *provisioning* time (when a manager
first logs in via step 13, or connects via step 14) rather than at app
boot. On app boot, iterate all known managers (query the control-plane
`Manager` table) and run any *migration* ALTER-TABLE checks (the existing
PRAGMA-based column-existence checks) against each of their `db.sqlite`
files — call this out as a permanent operational cost of this design: every
future schema change now runs across N manager DBs on startup, not one.

## Subproblem 5 — projectkb paths move too

`app/projectkb/paths.py`'s `PROJECTS_DIR` (currently `BASE_DIR / "projects"`)
becomes manager-relative: `manager_dir(manager_id) / "projects"`. Every
`projectkb` function that takes a `project_name` now also needs a
`manager_id` (or is called from a context that already has one, e.g. a
route with `Depends(get_current_manager)`). `tracked.py`'s
`TRACKED_CONTACTS_PATH` and `job_schedule.py`'s `JOB_STATE_PATH` similarly
become manager-relative (`manager_dir(manager_id) / "tracked_contacts.json"`
/ `"job_state.json"`) — this is what step 16 (scheduler-per-manager) needs
already in place.

## Test plan

- `ensure_manager_scaffold(m1)` and `ensure_manager_scaffold(m2)` create
  fully independent directories/DBs; a `TeamMember` created for `m1` is
  invisible via `m2`'s session.
- Every existing test file that used the global `db_session`/`client`
  fixtures needs updating to create a manager + log in (dev-login from
  step 12) first, then hit manager-scoped routes through that session —
  expect to touch most of `tests/`. Budget real time for this; it's the
  biggest test-fallout step in the whole migration.
- Regression: run the full suite after the full route sweep, not
  incrementally file-by-file for this part (the fixture change is global).
