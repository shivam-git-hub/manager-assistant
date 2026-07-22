# Step 21 — Project Drill-Down (wireframes 7.png overview + 6.png dashboard)

Authority: `spec/architecture_v2_kb.md` §3/§5 + wireframes. Implemented
directly by Claude. Two new frontend pages behind the project cards, plus
the backend they need. Pipeline-derived content (events-driven panels,
timeline, conflicts, health) renders honest empty states until the jobs
land — real endpoints, no dummy data.

## Backend

1. **`Task.priority`** (`low|medium|high`, default `medium`) — wireframe 6
   has priority chips; schema lacked it. ALTER-TABLE check in
   `init_project_db` (PRAGMA pattern from tenancy) since project dbs
   already exist on disk.
2. **Tasks API** (new `app/api/project_detail.py`, all routes under
   `/api/projects/{id}`, visibility via the registry `_is_visible` rule;
   writes manager-only):
   - `GET /tasks` → `{tasks, my_tasks}`; tasks nested (subtasks under
     `parent_task_id`), each with computed `schedule_state`
     (`done | blocked | pending_approval | overdue (+days_overdue) |
     on_schedule`) using **sim time** — never the wall clock. `my_tasks` =
     flat list where assignee's employee email == the logged-in user's.
   - `POST /tasks` (manager): title, description?, priority?,
     assignee_employee_id?, due?, parent_task_id? → `created_by="manager"`.
   - `PATCH /tasks/{task_id}` (manager): those fields + `status`;
     approving a `pending_approval` task = PATCH `status="todo"`,
     `approved_by` stamped.
3. **Doc/notes**: `GET /doc` → `{project_md, notes_md}` (raw markdown);
   `PUT /doc` (manager) accepts either/both. Overview/Milestones/KPIs are
   md sections — parsing is a frontend display concern.
4. **Vault**: `GET /vault` (name/size list), `POST /vault` (multipart
   upload, any member; add `python-multipart` to requirements),
   `GET /vault/{filename}` download. Filename sanitization: basename-only,
   reject path separators — no traversal.
5. **Insights bundle**: `GET /insights` → `{conflicts, suggestions,
   concerns, health}` from the project db (all empty today) — the
   dashboard's Conflicts panel + future surfaces read this.
6. **Events project filter**: `GET /api/events?project_id=` — powers the
   Updates / Blockers & Clarifications / Requests panels (typed events
   tagged to the project). Python-side filter over the JSON list column is
   fine at this scale.
7. **`DELETE /api/projects/{id}`** (manager only) — registry row + member
   rows + the `projects/<id>/` dir. Wireframe 6's Delete Project button;
   frontend confirms first.
8. **MoM tagging**: `POST /api/messages/manual` gains optional
   `project_id`, stored in `raw_metadata` JSON as a hint for the future
   ingest/heartbeat tagging.

## Frontend

- Project cards (home rails + grid) become clickable → `/projects/:id`.
- **`/projects/:id` — Overview (7.png)**: name, Overview/Milestones/KPIs
  rendered from `project.md` sections (manager gets an Edit mode writing
  back via PUT /doc); Timeline card = honest empty state until dream
  events exist; Quick Links: Team (members dialog from registry detail),
  Project Vault (list + upload dialog), Add MoM (dialog → manual message
  with project_id); "Project Dashboard" button → dashboard page.
- **`/projects/:id/dashboard` — Dashboard (6.png)**: Tasks table (subtask
  rows indented; priority chip; assignee; due; computed status chip;
  Approve for pending_approval; edit dialog — manager only; "+" add-task);
  My Tasks table; four panels: Updates / Blockers & Clarifications /
  Requests (events by type, project-filtered, dismiss where sensible) /
  Conflicts (insights bundle). Delete Project (confirm) + View Project
  (→ overview).
- Status/priority chip colors from `constants.ts` — extend, don't inline.

## Tests

`tests/test_project_detail.py`: task CRUD + manager-only 403s +
subtask nesting + schedule_state math against sim time + my_tasks
matching; doc get/put + permissions; vault upload/list/download +
traversal rejection; insights empty shape; events project_id filter;
project delete (rows + dir gone, non-manager 403); MoM project_id in
raw_metadata. Full suite green (minus the 3 known v1 failures).
