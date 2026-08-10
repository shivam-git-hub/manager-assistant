# Step 30 — remove `Manager` table; `Employee.id` is the identity key

## Why

Step 29 made `Employee` the credential/identity source of truth but left
`Manager` as a parallel auth-identity table, so every logged-in person has
two ids (`Manager.id` for sessions/ownership FKs, `Employee.id` for
everything else). Explore-agent survey confirmed every consumer of
`Manager`/`manager_id` only reads `.id`/`.email`/`.name` (all present on
`Employee`) and the tenancy directory-scaffold layer already treats the id
as an opaque string — so collapsing to one id is mechanical everywhere
except four FK columns.

## Changes

1. `app/controlplane/models.py`: delete `Manager` class. Retarget
   `AuthSession.manager_id`, `Agent.manager_id`, `Project.manager_user_id`,
   `Portfolio.manager_user_id` FKs to `employees.id` (column names
   unchanged). Delete `Employee.manager_id` (self-referential once Manager
   is gone) and `link_employee_to_manager`; keep `Employee.is_manager`.
   `list_provisioned_managers()` → `Employee.is_manager == True`.
2. `app/controlplane/auth.py`: `get_current_manager` → renamed
   `get_current_employee`, returns `Employee`, `db.get(Employee,
   session.manager_id)`. `create_session(db, employee_id)` unchanged shape.
3. Login flows (`api.py::dev_login`, `outlook_auth.py::
   _handle_login_callback`): collapse find-or-create-Manager +
   link-Employee-to-it into a single find/upsert-Employee → flip
   `is_manager=True` → `ensure_manager_scaffold(employee.id)` if first time
   → `create_session(db, employee.id)`.
4. Every route `Depends(get_current_manager)` → `Depends(get_current_employee)`
   across controlplane/agents.py, controlplane/outlook_auth.py,
   controlplane/slack_auth.py, controlplane/api.py, api/dev_tools.py,
   api/home.py, api/project_detail.py, api/projects_registry.py,
   agent/api.py, projectkb/api.py, tenancy/db.py::get_manager_db.
5. No changes needed to `app/agent/*`, `app/integrations/*`,
   `app/tenancy/paths.py`, `app/projectkb/jobs/heartbeat.py` — they already
   take `manager_id: str` as an opaque key; the value fed in becomes
   `employee.id` automatically via #4.
6. New project-creation validation (`app/api/projects_registry.py::
   create_project`): `name` required non-empty (stripped) and unique
   case-insensitively across `Project`; `description` required, >=20 chars
   stripped; `supervisors` ids validated against `Employee` (same pattern
   as `member_employee_ids`).
7. Tests: rewrite `test_outlook_auth.py`, `test_projectkb_scheduler.py`,
   `test_auth.py`, `conftest.py` to key assertions on `Employee`/
   `is_manager` instead of `Manager` rows/count.
8. `data/controlplane.sqlite` / `test_controlplane.sqlite`: no migration —
   pre-production, wipe on next boot (decided 2026-08-09).

## Verification

- Full pytest suite, 0 failures.
- Manual dry-run boot.
