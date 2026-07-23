# Step 28 — Personal Agent: Context, Tools, Heartbeat, Chat

**Decisions confirmed with Shivam 2026-07-23** (resolving the three open
forks below): (1) pre-meeting briefs go to the manager on **Slack DM**, not
portal — §6 updated. (2) Meetings are **inferred from conversation**, not
manually created — the agent notices mentions like "let's connect at 5pm"
in recent claims and creates the `Meeting` row itself — §3/§5/§9 updated.
(3) Chat scope **includes Slack DM replies to Harry**, not just the
dashboard — §6/§9 updated, `direct_contact.py` is rewritten (kept), not
deleted.

Spec-first per CLAUDE.md. This replaces `app/agent/*` wholesale — every file in
there (`tools.py`, `situation.py`, `prompts.py`, `heartbeat.py`, `harness.py`'s
context assumptions) is written against the dead v1 schema (`TeamMember`,
v1 `Project.health`, `AttributedClaim`, `Conflict`, `Followup`, `Entity` from
`app/kb/models.py`) that architecture v2 superseded. None of it runs against
real data today. This step rebuilds the agent on the actual v2 tables:
`app.database.{Todo, Claim, Event, Meeting}`, `app.controlplane.models.
{Employee, Project as RegistryProject, ProjectMember}`, `app.projects.models.
{Task, ArchiveEntry, Conflict, Suggestion, Concern}`.

## 1. Where this sits in the pipeline

```
ingest (15m)  -> claims
heartbeat(1h) -> events (user-level) + task transitions/drafts (project fan-out)
dream (24h)   -> memory.md / summary.md / events.md / suggestions / concerns / health
agent (NEW)   -> ACTS on what the above already detected: messages people,
                 drafts/updates dashboard state, escalates to the manager.
```

The agent heartbeat is a 5th tick, not a re-detection pass. It does not
re-derive blockers/conflicts/health itself — `heartbeat`/`dream` already do
that judgment. The agent's job is narrower and more deterministic: given
already-typed `Event` rows and already-computed task/meeting state, decide
whether a communication or dashboard action is due, and if so, take it.
This mirrors the codebase's standing rule (ingestion/heartbeat/dream all
follow it): **deterministic candidate selection in code, LLM only drafts
judgment/text** — not "hand the LLM everything and let it decide what's
urgent," which is what the old `app/agent/heartbeat.py` triage step did.

New `JobName.AGENT_HEARTBEAT`, registered in `app/projectkb/scheduler.py`'s
`_JOBS` dict and `job_schedule.py`'s defaults, same per-manager fan-out as
the other five. Suggested default interval: 30 min (config knob
`AGENT_HEARTBEAT_INTERVAL_MINUTES`) — frequent enough to catch the 2-hour
meeting window without drifting, cheap because most ticks will have zero
candidates and cost one skipped LLM call.

## 2. Data model additions (per-manager `db.sqlite`, `app/database.py`)

**`Meeting` gap — resolved: agent infers meetings from conversation, not
manual entry or calendar polling.** v2 has no calendar ingestion —
`Meeting`/`ActionItem` are v1 tables with a dead creation path
(`app/kb/meetings.py`, never wired into the v2 frontend). Rather than a
manager-facing create-meeting form or a real Outlook calendar connector
(both explicitly out of scope for this step), the agent itself watches
recent `Claim` text for meeting mentions ("let's connect at 5pm", "sync
tomorrow 3pm") and creates the `Meeting` row via `dashboard_action` when it
spots one not already tracked. This is judgment (natural-language time/
attendee extraction), so — unlike the deterministic candidate selection in
§3 — it's explicitly an LLM-driven step, same precedent as ingestion's own
flash-model claim extraction from raw messages. See §5's `list_meetings`
probe tool and §6's context feed.

- `Meeting` gets one new column: `brief_sent_at: Optional[datetime]` —
  set the moment the agent sends a pre-meeting brief, so a later tick in
  the same 2-hour window never re-sends it. (`ActionItem` stays unused by
  this step.)
- New table `AgentActionLog` — the idempotency ledger the deterministic
  selector reads before creating a candidate, and the tool handlers write
  to after acting:
  ```
  id: str (uuid4 hex)
  action_type: str   # "pre_meeting_brief" | "followup" | "conflict_contact" | "conflict_escalate"
  ref_key: str        # e.g. "meeting:<id>", "task:<project_id>:<task_id>:<date>", "event:<event_id>"
  detail: Optional[str]   # short free text, e.g. who was messaged
  created_at: datetime (sim-time)
  ```
  Unique on `(action_type, ref_key)`. Follow-up `ref_key`s bucket by
  sim-date (`task:{project_id}:{task_id}:{YYYY-MM-DD}`) so an overdue task
  gets nudged at most once/day, not once ever. Conflict `ref_key`s don't
  bucket by date — `conflict_contact` fires once per conflict-event, and
  `conflict_escalate` only fires after a contact row exists and enough
  time (config `CONFLICT_ESCALATE_AFTER_HOURS`, default 24) has passed
  with the conflict event still open.

No new project-db tables needed — task/conflict state already lives in
`app/projects/models.py`.

## 3. Deterministic candidate selection (code, not LLM)

New `app/agent/select.py::build_candidates(db, manager_id) -> List[Candidate]`,
one dataclass per use case:

- **Pre-meeting brief**: `Meeting` rows, `status="scheduled"`,
  `brief_sent_at IS NULL`, `starts_at` between `now_ist()` and `now_ist() +
  2h`.
- **Follow-up (schedule slip)**: for each project this manager owns/is
  member of, open (non-`done`) `Task` rows that are `status="blocked"` OR
  `due` in the past — skip if an `AgentActionLog` row already exists for
  today's bucket.
- **Conflict**: `Event` rows `type="conflict"`, `ui_state` not in
  `{dismissed, approved, rejected}`, tagged to a project this manager
  owns. Two candidate sub-kinds picked by AgentActionLog state: no
  `conflict_contact` row yet -> "contact both parties"; a `conflict_contact`
  row older than the escalate threshold and the event still open -> "escalate
  to manager".

`build_candidates` returns plain data (ids, titles, involved people) — no
LLM call yet. If the list is empty, the job returns immediately (same
early-exit shape as the old triage step, but now the "should I bother"
decision is a DB query, not a flash-model call).

## 4. Context assembly for the LLM call(s)

Two consumption modes need the same context builder,
`app/agent/context.py::build_agent_context(db, manager_id) -> str`:

- **Owner profile**: manager name/email (control-plane `Manager`), their
  `Employee` row (role).
- **Projects**: this manager's owned + member `RegistryProject` rows —
  name, id, kind. For each, read that project's `summary.md` (dream's
  rewritten synthesis) — NOT `project.md` (manager-authored, slower-moving)
  as the primary signal; `project.md` is available via a probe tool if the
  agent wants milestones/KPIs.
  Also today's tail of that project's `events.md`.
- **User-level**: this manager's own `memory.md` (durable facts) and
  today's `Event` rows (`app.database.Event`, general + project-tagged) —
  the same signals `situation.py` used to hand-roll, now sourced from the
  real pipeline output instead of dead v1 tables.
- **Team roster**: `Employee` rows for anyone who is a member of any
  project this manager touches (not the whole org) — id, name,
  slack_id presence.
- **Current sim time**: `timeservice.now_ist()`, stamped explicitly in the
  prompt (this is how the agent "knows" a meeting is in 2 hours — never a
  wall-clock read, per CLAUDE.md).

This is a text digest, same spirit as the old `situation.py['digest']`, but
built from tables that actually have rows in them.

## 5. Tools

Everything in `app/agent/tools.py` today is either dead-schema (`send_slack_dm`,
`send_email`, `list/get/create_meeting`) or a Hermes mock placeholder
(`skill_manage`, `skills_list`, `skill_view`, `memory`, `session_search`,
`read_file`, `write_file`, `patch`, `send_message` (mock), `terminal`,
`cronjob`, `delegate_task`). Plan: **delete all of it**, keep only `todo`
(made real, see below), and add a small, purpose-built set:

**`send_message`** — the one messaging tool, per your ask, parameterized by
channel:
```
send_message(channel: "slack"|"portal", target: str, text: str)
```
- `channel="slack"`: `target` = an `Employee.id`. Resolves `Employee.slack_id`,
  opens/reuses a DM via Slack's `conversations.open` (new small helper —
  today's DM resolution (`get_dm_channel_id`) is v1-keyed on `TeamMember`
  ids and can't be reused as-is), sends through the *existing*
  `send_or_hold` quiet-hours gate (unchanged — this is the one piece of
  v1 infra that's still schema-agnostic and correct).
- `channel="portal"`: `target` = `"manager"` (the only valid value for now
  — there is no other portal recipient). Delivered by inserting a
  `ChatMessage(role="assistant")` row, so it shows up exactly like Harry
  proactively messaged the manager in the same dashboard chat thread the
  manager already uses. No new table.
- Returns `{status: "sent"|"held", release_at?}` same shape as today.

**`dashboard_action`** — one enum-dispatched tool, per your ask:
```
dashboard_action(
  action: "create_task"|"update_task_status"|"add_project_member"|
          "create_todo"|"create_meeting",
  ...action-specific params
)
```
Each arm is a thin wrapper calling the *existing* real functions instead of
reimplementing logic: `create_task`/`update_task_status` call into
`app/api/project_detail.py`'s task-mutation logic (factored so both the
HTTP handler and this tool call the same internal function — small
refactor, not a duplicate implementation); `add_project_member` calls the
existing `PATCH /api/projects/{id}` member-add path; `create_todo` inserts
an `app.database.Todo` row (the manager's own home-panel todos — lets the
agent hand the manager a manual action item it can't safely do itself);
`create_meeting` inserts an `app.database.Meeting` row (closes part of the
meeting-data gap in §2 — the agent, or the manager via chat, can schedule
one; still no calendar *import*).

**Read/probe tools** (read-only, thin wrappers over real queries):
- `get_project_doc(project_id, doc: "project"|"summary"|"notes")`
- `list_team()` — employees relevant to this manager's projects
- `get_task(project_id, task_id)`
- `list_open_conflicts(project_id?)`
- `list_meetings(from_date?, to_date?)` — v2-schema-correct rewrite of the
  old (deleted) tool; lets the agent check "is this mention already
  tracked" before creating a duplicate `Meeting` row.

**`todo`** (made real, not mocked) — session-scoped worklist for a single
heartbeat or chat run, same shape as your own todo tool: `content` +
`status` (`pending`/`in_progress`/`completed`). Not persisted across runs —
it's how the agent visibly plans "I have 3 things to do this tick" and
works them one at a time inside one tool-loop, matching your "create a
todo, then complete one by one" framing. Backed by nothing but the
existing conversation transcript for that run (no new table) — same as
how your own todo tool works in this harness.

Everything else currently in `tools.py` (skills, memory, session_search,
file read/write/patch, terminal, cronjob, delegate_task) gets deleted, not
kept-but-unused — none of it is reachable from either entry point below,
and CLAUDE.md's own convention is "no half-finished implementations."

## 6. Two entry points

**A. Agent heartbeat job** (`app/projectkb/jobs/agent_heartbeat.py::run(db,
manager_id, client=None)`, same signature convention as the other jobs):
1. `candidates = build_candidates(db, manager_id)` — if empty, return
   `{acted: False}` immediately, no LLM call.
2. `context = build_agent_context(db, manager_id)`.
3. One `run_agent`-style tool-calling loop (reusing `app/agent/harness.py`'s
   loop mechanics, new system prompt) seeded with: the context, the
   candidate list (meeting X in 47 min with attendees [...], task Y overdue
   3 days assigned to Z, conflict #N between A and B awaiting contact/escalation),
   recent (last 48h, capped ~20) `Claim` text as "watch for meeting
   mentions" material, and explicit instructions per use case:
   - Pre-meeting brief: `send_message` a concise agenda/status brief to
     the manager on **`channel="slack"`, `target="manager"`'s own
     `Employee` row** (confirmed 2026-07-23 — not portal).
   - Follow-up: `send_message` the assignee on `slack` asking for a status
     update, citing the specific task.
   - Conflict, first contact: `send_message` BOTH claim holders on slack
     asking them to reconcile.
   - Conflict, escalate: `send_message` the manager on `slack` explaining
     the unresolved conflict and that both parties were already contacted.
   - Meeting inference: if a recent claim describes a meeting/call with a
     time that isn't already in `list_meetings`, call
     `dashboard_action(create_meeting, ...)` so it becomes a brief
     candidate on a future tick. This is best-effort, not exhaustive —
     ambiguous mentions are fine to skip.
4. After each real action, the tool handler itself inserts the matching
   `AgentActionLog` row (not left to the model to remember) — same
   "code enforces the invariant the model can't be trusted with" pattern
   as the severity floors in `heartbeat.py`/`dream.py`.

**B. Chat endpoints** — two, both driving the same rewired harness
(`build_agent_context` + new tool set instead of the dead
`compile_system_prompt`/`situation.py`):
- `POST /api/chat` (existing route, unchanged shape) — dashboard chat.
  The HTTP response IS the reply; no self-send tool call needed.
- **Slack DM to Harry** (confirmed in scope 2026-07-23, reversing this
  spec's original "dashboard-only" draft) — `app/integrations/slack.py`'s
  webhook already ingests any DM the manager's claimed bot receives
  (step 20's track-everything inversion; the only existing exclusion is
  the bot's own sender id, to prevent reply loops). New: after a
  successful `ingest()` in `slack_webhook`, if `event.get("channel_type")
  == "im"` and the sender isn't the bot itself, call `run_agent` with that
  single message (no dashboard history — a separate conversation surface)
  and send the reply back via `connector.send(db, event["channel"],
  reply_text)` — the DM channel id is already on the event, no employee
  lookup needed. This replaces `app/agent/direct_contact.py` (rewritten to
  the new harness/tools, not deleted as originally planned).

## 7. System prompt

New `app/agent/prompts.py` (rewritten, not patched): stable tier (identity,
citation discipline dropped — v2 has no timeline-entry citation IDs
anymore, replaced with "reference the project/task/event you're acting on
by name" ), tool-usage rules (working-hours gate reminder, never
auto-resolve conflicts — only relay/escalate), + the dynamic context from
§4 + candidate list from §3 when invoked as a heartbeat.

## 8. Testing plan

- `tests/test_agent_select.py` — `build_candidates` unit tests per use
  case (meeting in window / out of window / already briefed; overdue task
  with and without today's log row; conflict contact vs escalate state
  transitions).
- `tests/test_agent_tools.py` — `send_message` (both channels, quiet-hours
  held vs sent), `dashboard_action` (each arm), `AgentActionLog` dedup.
- `tests/test_agent_heartbeat_job.py` — end-to-end with a fake client:
  seeded meeting/task/conflict fixtures -> correct tool calls -> correct
  log rows -> second tick is a no-op (idempotency).
- `tests/test_chat.py` (extend existing) — smoke test the rewired chat
  path still round-trips through the new context/tools.
- Live dry-run: seed one of each candidate type against the real dev
  server + real Gemini call, run the job once manually
  (`POST /api/heartbeat/run` equivalent, or a one-off script), inspect
  Slack/portal output and `AgentActionLog` rows — same verification bar
  used for every prior step.

## 9. Deletions

`app/agent/tools.py`, `situation.py`, `prompts.py`, `heartbeat.py` get
rewritten/removed; `direct_contact.py` gets **rewritten** (not deleted —
see §6B) to call the new harness/tools instead of the old dead-schema one.
`app/kb/meetings.py`'s `pre_meeting_brief_handler`/scheduler registration
(v1 `app/scheduler.py`) removed since the new job replaces it.
`app/agent/harness.py`'s loop mechanics and `budget.py`/`registry.py`/
`notes.py` (memory notes — still useful, keep) survive as-is.
