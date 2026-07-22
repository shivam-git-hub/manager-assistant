# Step 18 — Global Projects Registry, Employees Directory, Project Scaffold

Authority: `spec/architecture_v2_kb.md` (v2 design, approved). This step is
item 1 of §7 there. Read §1–§3 of the spec before coding. `CLAUDE.md` holds
repo conventions — follow it, but you MUST NOT edit CLAUDE.md itself.

Ground rules (repo-wide, unchanged): TDD — write failing tests first, then
code. Naive-IST datetimes via the sim-time service (`app/timeservice.py`),
never wall-clock. SQLite only. Do not touch `frontend/`, the simulator
(`app/static/index.html`), or `app/static/login.html`/`connect.html`.

## 1. Control-plane additions (`app/controlplane/models.py`)

Three new tables in `data/controlplane.sqlite`, following the existing
model/init pattern used by `Manager`/`Agent` (check how their tables get
created at boot and hook in the same way):

- `Employee` — org directory. Columns: `id` (pk, uuid hex), `email`
  (unique, lowercase), `slack_id` (nullable), `name`, `role` (nullable),
  `skills` (JSON text, nullable), `created_at`.
- `Project` — the global registry. Columns: `id` (pk, uuid hex), `name`,
  `description` (nullable), `kind` (`"team"` | `"personal"`),
  `manager_user_id` (FK-ish to controlplane `Manager.id` — same id space
  as the logged-in user), `supervisors` (JSON list of employee ids,
  nullable), `created_at`.
  Note: `app/database.py` also has an old per-user `Project` class (v1
  philosophy). Leave it alone this step; where both are imported in one
  module, alias the new one (`from app.controlplane.models import Project
  as RegistryProject`).
- `ProjectMember` — `id` (pk), `project_id`, `employee_id`, `role`
  (nullable free text). Unique on (`project_id`, `employee_id`).

## 2. Per-project store (`app/projects/` — NEW package)

Do NOT rework `app/projectkb/models.py`/`paths.py` in place — they're an
orphaned v1 sketch you may read for patterns; they get deleted in a later
step. Build fresh:

- `app/projects/paths.py` — `project_dir(project_id)` →
  `projects/<project_id>/` at the **repo root** (sibling of `managers/`);
  `ensure_project_scaffold(project_id, name, description)` creates the dir,
  `vault/` subdir, the four md templates (only if missing), and calls
  `init_project_db`.
  - `project.md`: title = name, description, then empty `## Overview`,
    `## Milestones`, `## KPIs` sections (manager-owned file).
  - `summary.md`: header + "_No synthesis yet — the dream job writes this._"
  - `events.md`: header + note that dream appends timestamped key events.
  - `notes.md`: header + note that manager/agent keep special instructions here.
- `app/projects/models.py` — separate `ProjectBase` (its own
  `DeclarativeBase`) with the v2 tables from spec §3:
  - `Task`: `id` (pk uuid hex), `parent_task_id` (nullable self-ref for
    subtasks), `title`, `description` (nullable), `assignee_employee_id`
    (nullable), `status` (`todo|in_progress|blocked|done|pending_approval`,
    default `todo`), `due` (nullable datetime), `created_by`
    (`manager|agent`), `approved_by` (nullable), `created_at`, `updated_at`.
  - `ArchiveEntry`: `id`, `ts`, `kind` (free text: event/mom/action/…),
    `content` (text), `source_ref` (nullable). Append-only by convention.
  - `Conflict`: `id`, `claim_a_ref`, `claim_b_ref`, `severity`
    (`low|medium|high`), `status` (`open|resolved_by_human`), `created_at`.
  - `Suggestion` / `Concern`: `id`, `ts`, `text`, `status`
    (`open|dismissed`).
  - `HealthLog`: `id`, `ts`, `rubric_inputs` (JSON text), `base_score`,
    `llm_adjustment` (int, default 0), `reason` (nullable), `final_score`.
- `app/projects/db.py` — `get_project_engine(project_id)` /
  `get_project_session(project_id)` / `init_project_db(project_id)`,
  mirroring the `app/tenancy/db.py` engine-cache pattern.

## 3. APIs (`app/api/projects_registry.py`, cookie-auth via existing deps)

All routes require the logged-in user (same auth dependency style as other
`app/api/` routers). Register the router in `app/main.py`.

- `GET /api/employees` — full directory list (for the member-picker UI).
- `POST /api/projects` — body: `name`, `description?`, `kind`
  (`team|personal`), `member_employee_ids?` (with optional per-member
  `role`), `supervisors?`. Manager = current user. Creates registry row +
  member rows + calls `ensure_project_scaffold`. For `kind="personal"`:
  member list must be empty (400 otherwise) — the owner is implicitly the
  only member.
- `GET /api/projects` — projects visible to the current user:
  `manager_user_id == user.id` OR the user's email matches a member
  employee's email (join `ProjectMember` → `Employee.email`, compare
  lowercase). Personal projects appear only for their owner. Response
  includes `id`, `name`, `description`, `kind`, `is_manager`, member count.
- `GET /api/projects/{id}` — 404 if not visible; detail incl. members
  (employee id/name/email/role) and supervisors.
- `PATCH /api/projects/{id}` — manager only (403 otherwise): update
  name/description/supervisors; add/remove members (team projects only).

**Route collision**: `app/api/dashboard.py` already serves v1
`GET/POST /api/projects` (old per-user table). Grep for what consumes those
(old Vue dashboard `app/static/dashboard/`, simulator, tests). The old Vue
dashboard is being retired; if only it and its tests depend on the old
handlers, delete those two handlers from dashboard.py and fix/remove the
affected tests. If the simulator (`app/static/index.html`) itself calls
them, keep the old handlers but move them to
`/api/legacy/projects` and update the simulator's fetch paths. State in
your final report which case you found and what you did.

## 4. Seed script (`scripts/seed_employees.py`)

Mirror the agent-pool pattern (`scripts/seed_agents.py` +
`agents_pool.json.example`): reads a gitignored `employees.json` (list of
`{email, name, slack_id?, role?, skills?}`), upserts by lowercase email
into the control-plane `Employee` table. Commit an
`employees.json.example` with 3–4 sample rows. Add `employees.json` and
the repo-root `projects/` dir to `.gitignore`.

## 5. Tests (write these FIRST — `tests/test_projects_registry.py`)

Follow the isolation patterns in `tests/conftest.py` / `tests/test_tenancy.py`
(fresh throwaway manager via dev-login fixture; temp data dirs — check how
existing tests redirect `managers/` and controlplane paths, and redirect
the repo-root `projects/` dir the same way so tests never write real dirs).

Cover at minimum:
1. Create team project → registry row, member rows, scaffold on disk (all
   four md files + `vault/` + project db with all six tables present).
2. `GET /api/projects` as manager, as member (email match), and as an
   unrelated third user (sees nothing).
3. Personal project: created with no members; invisible to everyone but
   the owner; POST with members + `kind="personal"` → 400.
4. `GET /api/projects/{id}` 404 for non-member; PATCH 403 for non-manager;
   PATCH member add/remove works for manager.
5. `GET /api/employees` returns seeded directory.
6. Seed script upsert: run twice with an edit → no duplicates, fields
   updated.

## 6. Done criteria

- Full suite green: `.venv/bin/python3 -m pytest tests/`
- Report: what you built, the route-collision finding + resolution, any
  spots where reality diverged from this prompt (the code wins — explain).
