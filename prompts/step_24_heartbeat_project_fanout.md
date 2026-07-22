# Step 24 — Heartbeat (project fan-out): task updates, drafted subtasks, archive

Authority: `spec/architecture_v2_kb.md` §4.3 (project-fan-out half).
Implementation order item 6. Depends on step 23 landing first (this reads
the events step 23 just created in the SAME heartbeat tick — run this
immediately after the user-level pass, per manager, not as a fully
separate scheduled job).

## 1. Manager-only project truth (reconfirm before writing code)

Fan-out runs ONLY for projects where new events landed AND this manager
is that project's manager (`Project.manager_user_id` in the registry —
`app/controlplane/models.py`). Confirmed accepted limitation: a teammate's
DMs never feed project KB, only the manager's own pipeline does — do not
try to "fix" this by fanning out for member roles too, that reopens a
decision the user already closed.

## 2. Selection

From step 23's just-created `Event` rows this tick: collect distinct
`project_ids` where `general=False` and this manager is the project's
manager. For each touched project, load `projects/<project_id>/db.sqlite`
(`app.projects.db`) and `project.md`/`summary.md` (NOT the whole archive —
spec §4.3 context-explosion valve: "project agents load summary.md +
queried excerpts — never the whole archive").

## 3. Project-scoped agent action

Per touched project, a single structured-output smart-model call (same
non-tool-loop pattern as step 23, same deferral rationale) given:
`summary.md` content, the current open `Task` rows (title/status/
priority/assignee/due — from `app.projects.models.Task`), and this
project's new events (title/body/type/severity). Output:
- task status transitions: `[{"task_id": "...", "new_status": "..."}]`
  (only for tasks whose event evidence clearly supports a transition —
  e.g. a "done" claim/event tied to that task; do not invent transitions
  without event support)
- new task/subtask drafts: `[{"title", "description", "priority",
  "assignee_employee_id"?, "parent_task_id"?}]` — ALWAYS created with
  `status="pending_approval"`, `created_by="agent"` (spec §4.3: "manager
  approves in the UI"). Never auto-approved by this job, no exceptions.
- an archive entry summarizing what changed this tick (kind="heartbeat",
  content = short prose, source_ref = the event id(s) that drove it).

Apply status transitions + insert draft tasks + insert the archive entry
in one transaction against the project db.

## 4. Approvals surface (small frontend addition, spec calls this out
explicitly: "an approvals surface gets added to the project page")

`ProjectDashboard.tsx` (from step 21) already renders a task table with
status chips. Add a filter/section for `status="pending_approval"` rows
with Approve/Reject actions hitting the existing
`PATCH /api/projects/{id}/tasks/{task_id}` endpoint (`app/api/
project_detail.py` already supports approval stamping `approved_by` —
confirm the manager-only guard is in place, wire the button, no new
backend endpoint should be needed unless review finds a gap). Reject =
delete the task row (an agent-drafted task that's rejected has no history
worth keeping) or set a `status="rejected"` if the existing schema/tests
assume statuses are exhaustive of the five listed in
`ProjectTask.schedule_state` — check `_schedule_state` in
`project_detail.py` before deciding which, since it currently only
handles `todo|in_progress|blocked|done|pending_approval`.

## 5. Tests (`tests/test_heartbeat_project_fanout.py`, new)

Mocked GeminiClient. Cover: fan-out only fires for the manager's own
projects, never for projects where they're a member-not-manager; a
general-only event tick (no project_ids) produces zero fan-out calls;
drafted tasks always land as `pending_approval`/`created_by="agent"`
regardless of what the mock returns for status; a status transition
without supporting event evidence is rejected (add a code-level check,
don't trust the model to self-police this); archive entry gets written
per touched project per tick; frontend approve/reject actions round-trip
against the real endpoint (existing `project_detail.py` test file pattern).

## Done criteria

Full pytest run (3 pre-existing failures only), dry-run: seed a manager +
managed project + a heartbeat-23 run that produces project-tagged events,
run this step, inspect task status changes/drafted tasks/archive rows,
screenshot the ProjectDashboard approvals section against a
manually-drafted pending task. Update CLAUDE.md with a "Step 24 DONE"
entry.
