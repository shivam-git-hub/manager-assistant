# Step 24 — Heartbeat (project fan-out): task updates, drafted subtasks, archive

Authority: `spec/architecture_v2_kb.md` §4.3 (project-fan-out half),
refined 2026-07-23 in direct chat with Shivam (see the two corrections in
§3 and §5 below — this file supersedes the original wording on those two
points, kept here rather than only in chat per the project's spec-first
rule). Implementation order item 6. Depends on step 23 landing first
(this reads the events step 23 just created in the SAME heartbeat tick —
run this immediately after the user-level pass, per manager, not as a
fully separate scheduled job).

## 0. What this step is, restated plainly

ingest → claims (step 22). heartbeat user-level → claims become typed/
tagged/severity events (step 23, done). THIS step is a second agent pass,
per project the manager owns, that reads those same events and does
three things: (a) derives/updates project-level insight surfaces —
Blockers & Clarifications and Requests panels on `ProjectDashboard.tsx`
today just filter the raw events by type, this step doesn't need to
change that, but (b) drafts new tasks/subtasks (always
`pending_approval`) and proposes status transitions on existing tasks
where event evidence supports it, and (c) appends to the project archive
— deterministically, see §3, NOT via a second LLM call.

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

## 3. Project-scoped agent action (task drafts/transitions + request→task linkage)

Per touched project, a single structured-output smart-model call (same
non-tool-loop pattern as step 23, same deferral rationale) given:
`summary.md` content, the current open `Task` rows (id/title/status/
priority/assignee/due — from `app.projects.models.Task`), and this
project's new events (id/title/body/type/severity). Output:
- task status transitions: `[{"task_id": "...", "new_status": "..."}]`
  (only for tasks whose event evidence clearly supports a transition —
  e.g. a "done" claim/event tied to that task; do not invent transitions
  without event support — code-level check required, don't trust the
  model to self-police this)
- new task/subtask drafts: `[{"title", "description", "priority",
  "assignee_employee_id"?, "parent_task_id"?}]` — ALWAYS created with
  `status="pending_approval"`, `created_by="agent"` (spec §4.3: "manager
  approves in the UI"). Never auto-approved by this job, no exceptions.
- **request→task linkage (new, from 2026-07-23 chat):** for events of
  `type="request"` tagged to this project, ask the model whether the
  request is literally about completing/marking done a specific existing
  task (e.g. "Bob is asking to mark the migration subtask done") — if so,
  return `{"event_id": "...", "task_id": "..."}` pairs (task_id must be
  one of the open Task ids handed to it, else drop the pair). Back in
  code, for each pair, set that `Event.task_ids = json.dumps([task_id])`.
  This is what lets the Requests-panel Approve button (§4) know which
  task to flip. Most requests won't match anything — that's fine, leave
  `task_ids` null, same as step 23's default.

Apply status transitions + insert draft tasks + the request→task_ids
backfill in one transaction against the relevant db(s) (task
transitions/drafts against the project db; the `task_ids` backfill
against the manager's own db, since `Event` lives there).

## 4. Archive writes — DETERMINISTIC, no LLM (correction from original spec wording)

**Do NOT synthesize an archive entry with an LLM call.** Per Shivam
directly: "regarding archive writes, you don't need to further synthesise
event(s) using an LLM call. put all the events that happened,
corresponding to the project, in projects archive, with appropriate
timestamp." So: for every project-tagged event selected in §2, insert one
`ArchiveEntry` row (`app.projects.models`) with `ts=timeservice.now_ist()`,
`kind="event"`, `content=f"[{event.type}] {event.title}" + optional body`,
`source_ref=event.id`. One archive row per event, not one per tick —
plain deterministic logging, no synthesis, no extra model call.

## 5. Approve/Reject on Requests (new — from 2026-07-23 chat, replaces the
"Approvals surface" section of the original prompt, which conflated this
with task-draft approval; keep both, they're different things)

Two distinct approval flows exist, don't merge them:
- **Drafted-task approval** (already exists from step 21):
  `PATCH /api/projects/{id}/tasks/{task_id}` on a `pending_approval` row.
  Nothing new needed here except confirming it still works once step 24
  actually produces `pending_approval` rows for real (it only had
  manually-seeded test data before).
- **Request-event approval** (new): `type="request"` events in the
  project's Requests panel get Approve/Reject buttons. Per Shivam: "just
  a status flip is fine, but in cases when the request is about marking a
  task/subtask completed, it should also update the db and change the
  status of the task." Implementation:
  - Extend `Event.ui_state` to also allow `"approved"`/`"rejected"`
    (currently `shown|dismissed|promoted` — comment/docstring update in
    `app/database.py`, no migration needed, it's a plain string column).
  - New endpoints in `app/api/home.py` (next to the existing
    dismiss/promote): `POST /api/events/{id}/approve`,
    `POST /api/events/{id}/reject`. 400 if `event.type != "request"` —
    this action is scoped to requests, not blockers/conflicts/etc (those
    get their own resolve semantics later, out of scope here).
  - `approve`: set `ui_state="approved"`. If `event.task_ids` is
    non-empty (populated by §3's linkage step), open the project db for
    `event.project_ids[0]` and set that `Task.status="done"`
    (`updated_at` touched too). If task lookup fails (deleted task,
    project id missing), still approve the event but log a warning —
    don't 500 on a stale reference.
  - `reject`: set `ui_state="rejected"`. No task mutation.
  - `GET /api/events`'s default (non-`include_dismissed`) visibility
    query currently excludes only `dismissed`. Extend it to also exclude
    `approved`/`rejected` by default (a resolved request shouldn't keep
    cluttering the Updates panel/Requests panel) — `promoted` still
    overrides as before.
  - Frontend: `ProjectDashboard.tsx`'s `EventList`/`InsightPanel` for the
    Requests panel gets Approve/Reject buttons (calling the two new
    endpoints) alongside the existing dismiss `×`; reload on click, same
    pattern as `handleDismiss`. Blockers/Conflicts panels are unaffected
    (still dismiss-only, per the existing comment in that file noting
    their resolve semantics are a separate future step).

## 6. Tests (`tests/test_heartbeat_project_fanout.py`, new)

Mocked GeminiClient. Cover: fan-out only fires for the manager's own
projects, never for projects where they're a member-not-manager; a
general-only event tick (no project_ids) produces zero fan-out calls;
drafted tasks always land as `pending_approval`/`created_by="agent"`
regardless of what the mock returns for status; a status transition
without supporting event evidence is rejected; one `ArchiveEntry` row is
written per project-tagged event, with no LLM call involved in producing
it (assert the mock's call count only reflects the task/linkage judgment
call, not an extra archive call); a request→task_id pair only applies
when the task_id is among the project's open tasks, else dropped;
approving a request-with-linked-task flips both the event's ui_state and
the task's status in one action; approving a request with no linked task
only flips ui_state; rejecting never touches a task; approve/reject on a
non-"request" event 400s; a resolved (approved/rejected) event drops out
of the default `GET /api/events` view but still appears with
`include_dismissed=true`.

## Done criteria

Full pytest run (3 pre-existing failures only), dry-run: seed a manager +
managed project + a heartbeat-23 run that produces project-tagged events
including a request tied to a real task, run this step, inspect task
status changes/drafted tasks/archive rows, hit the new approve endpoint
and confirm the task flips, screenshot the ProjectDashboard Requests
panel's new Approve/Reject buttons. Update CLAUDE.md with a "Step 24
DONE" entry.
