# CLAUDE.md — Manager Assistant ("Harry")

Manager assistant for Shivam (the manager). Harry connects to Slack + Outlook +
a dashboard chat, maintains a gbrain-inspired knowledge base from all messages,
and acts autonomously (follow-ups, deadlock detection, status inference,
manager updates). Goal: an end-to-end **demoable** product — not scalable infra.

## Non-negotiable rules (see spec/notes_and_instructions.md)

- Spec first: draft/discuss a spec under `spec/` and get Shivam's explicit
  approval before feature code. TDD for all backend logic.
- No agent frameworks (no LangChain/LangGraph). Harness written from scratch,
  Hermes-inspired (lifting their patterns/snippets is fine).
- All datetimes: naive IST (Asia/Kolkata). In new code, direct wall-clock
  reads (`datetime.now()`/`.utcnow()`/`time.time()`) are FORBIDDEN — always
  go through `app.timeservice.now_ist()`. As of 2026-07-23 that function
  returns REAL wall-clock IST time, not simulated time (see "Simulated
  time" below, now retired) — the rule against calling `datetime.now()`
  directly stands for a different reason now (one call site to swap if
  this ever changes again, consistent stamping across every table/job),
  not because there's a sim clock to respect.
- No heavy installations: SQLite not Postgres. Backend and the simulator UI
  stay CDN/no-build. **Exception (2026-07-22): the manager-facing product
  frontend** (see "Frontend rebuild" below) is React + Vite + build tooling —
  an explicit, scoped override of this rule, not a repeal of it.
- LLM: Gemini via `GEMINI_API_KEY`. Two tiers configured as `smart_model`
  (gemini pro class: agent reasoning, synthesis) and `flash_model` (claim
  extraction, judging) in a config file, env-overridable.

## Workflow (updated 2026-07-22)

Claude codes major tasks DIRECTLY in the main session (Shivam's explicit
instruction 2026-07-22, reversing both the old separate-coding-agent flow
and a brief Sonnet-subagent experiment — the subagent lacked context).
Step prompts are still written first as files (`prompts/step_XX_<name>.md`,
never inline in chat) — they double as the spec for the work. Files may
still change between turns (Shivam edits too): **always check `git status`
/ re-read before editing — never trust stale context.**

- **KEEP THIS FILE (CLAUDE.md) UPDATED** — after every step: record new
  conventions, endpoints, tables, gotchas, decisions.
- Verification bar per step: code review + full pytest run + dry-run boot
  (and for frontend: `npm run build` + headless-Chrome screenshot against
  the wireframe).

## Architecture v2 (2026-07-22 — THE authority: `spec/architecture_v2_kb.md`)

Approved greenfield redesign (product name: **Pulse.ai**; every org member
is a user, not just managers). Vocabulary: `message → claim → event →
notification` (notifications = a QUERY over events, not a table). Global
projects registry (control-plane) + `projects/<project_id>/` dirs (shared
by members; only the manager's pipeline writes project truth). Tasks =
personal projects (`kind="personal"`). Four jobs: ingest 15m / heartbeat
1h / dream 24h / lint 1w (stubs in `app/projectkb/jobs/` await v2
implementations). The v1 architecture below is being superseded per-piece.

- **Step 18 DONE 2026-07-22** (`prompts/step_18_registry_and_scaffold.md`):
  control-plane `Employee`/`Project`(registry)/`ProjectMember`;
  `app/projects/` package (paths/models/db — per-project sqlite with
  tasks/archive/conflicts/suggestions/concerns/health_log + md scaffold +
  vault/); `GET /api/employees`, `POST/GET/PATCH /api/projects` (registry —
  old v1 handlers deleted from dashboard.py); `scripts/seed_employees.py`.
  Gotcha fixed: `.gitignore` `projects/` → `/projects/` (was shadowing
  `app/projects/`!). Import alias convention: `Project as RegistryProject`.
- **Step 19 DONE 2026-07-22** (`prompts/step_19_home_backend.md`): per-user
  tables `todos`/`claims`/`claim_sources`/`events` (app/database.py);
  `app/api/home.py`: todos CRUD + `GET /api/events` (min_severity, plus
  promoted, minus dismissed, `max_severity` aggregate) +
  `POST /api/events/{id}/dismiss|promote`. No create-event API by design —
  only jobs write events.
- **Step 20 DONE 2026-07-22** (`prompts/step_20_poll_completion.md`):
  track-everything inversion — `ingest()` (app/integrations/base.py) is
  now normalize→dedup→store, no allowlist/manager-involvement gates;
  `app/projectkb/tracked.py`+`tracked_channels.py` DELETED, replaced by
  `app/projectkb/blocklist.py` (blocklist.json per manager: contacts +
  channels, fnmatch) + `/api/blocklist` CRUD (old
  `/api/projectkb/tracked-contacts` router gone).
  `classify_message()` = the future ingest job's pre-LLM selection filter
  ("blocked"|"noise"|None; noise = no-reply senders, calendar-stub
  subjects, List-Unsubscribe). `NormalizedMessage.thread_key` →
  `unified_messages.thread_id` (Outlook `conversationId`; Slack
  `"<channel>:<thread_ts or ts>"`); `skip_reason` column added. Slack
  polling now covers im+mpim+private/public channels
  (`_conversation_object_type` maps list-object flags; needs
  groups:/channels:/mpim:history user scopes on the pool app — degrades
  per-channel if unconsented). `POST /api/messages/manual` = MoM/notes
  input (`source="manual"`, same pipeline).
- **Connector/agent fixes (2026-07-23, from Shivam's live testing):**
  (a) `POST /auth/outlook/revoke-send` — our-side send revoke: drops
  Mail.Send from `granted_scopes`; `OutlookConnector.send_allowed()` gates
  the live sendMail call on it (MS keeps the consent record — no app-side
  revoke API exists). (b) enable-send authorize URL now passes
  `prompt="consent"` so the permission screen actually shows on re-grant.
  (c) `_acquire_token` no longer hardcodes `["Mail.Read","Mail.Send"]` for
  silent acquisition — send scope is requested only by the send path
  (silently requesting an unconsented scope can fail the whole silent
  flow and break READING for read-only users). (d) Agent claim flow
  reordered per Shivam: `GET /api/agents/available` (no code) lists
  unclaimed agents; `POST /api/agents/claim {agent_id, code}` — the code
  gates the claim, not the view; `/api/agents/redeem` is gone. Agents page
  = "My agent" section + always-visible available grid. (e) Slack
  disconnect UI copy fixed: disconnect stops BOTH read and send (bot token
  revoked via auth.revoke, user token cleared) — only the claim survives.
- **Step 21 DONE 2026-07-23** (`prompts/step_21_project_drilldown.md`):
  project drill-down. Backend: `Task.priority` (+PRAGMA migration in
  `init_project_db`); `app/api/project_detail.py` under
  `/api/projects/{id}` — tasks CRUD (manager-only writes, subtask nesting,
  `schedule_state` computed vs SIM time, `my_tasks` by employee-email
  match, approve stamps `approved_by`), `/doc` GET/PUT (project.md +
  notes.md raw), `/vault` list/upload/download (python-multipart added;
  basename-only guard), `/insights` bundle, `DELETE` (registry rows +
  dir). `GET /api/events?project_id=` filter; manual MoM takes
  `project_id` → raw_metadata. Frontend: `/projects/:id` overview (7.png —
  md-section render + manager edit, Team/Vault/Add-MoM dialogs, timeline
  empty state) and `/projects/:id/dashboard` (6.png — task table w/
  priority+status chips, My Tasks, four event/insight panels, delete
  confirm); cards clickable. Requests Approve/Reject + blocker Resolve
  semantics arrive with the pipeline that creates those events.
- **Known failing tests (pre-existing, NOT v2 regressions):**
  `test_heartbeat.py` (2) + `test_scheduler.py::test_04_followup_lifecycle`
  — v1 followups/heartbeat code sits mid-refactor uncommitted; these get
  replaced when v2 heartbeat lands.
- **Step 22 DONE 2026-07-23** (`prompts/step_22_ingest_job.md`): real
  ingest job. `app/projectkb/jobs/ingestion.py::run(db, manager_id,
  client=None)` — per manager: `classify_message()` (step 20) stamps
  blocked/noise rows `is_processed=True`+`skip_reason`, no LLM; survivors
  batch by `thread_id` (threadless messages = solo batches); per-batch
  flash-model call (`client` injectable, same convention as
  `app.kb.extraction.extract_from_message`) returns
  `{"claims": [{"text", "message_ids"}]}`; inserts `Claim`+`ClaimSource`,
  flips the batch's messages `is_processed=True` in the SAME commit
  (retry-safe — a crash mid-run never leaves a message processed with no
  claim). `content_hash` (text+sorted message_ids, no DB unique
  constraint) is a secondary pre-insert dedup guard. New
  `INGESTION_BATCH_SIZE` config (default 30, `app/config.py`) caps
  candidate messages per run in **whole-thread units** — overflow waits
  for next tick; gotcha found + fixed in review: slicing mid-thread would
  let a later tick re-extract from a partial conversation and produce
  divergent claims `content_hash` can't catch, so the cap walks batches
  and stops before exceeding it, never splits one. Also fixed in review:
  a non-dict top-level JSON response (Gemini can legally return an array)
  used to raise `AttributeError` outside the `JSONDecodeError` catch,
  permanently stalling that batch's retry — now explicitly guarded.
  `tests/test_ingest_job.py` (10 tests). `count_pending_tracked_messages`
  (dead stub, zero callers, referenced a v1 allowlist concept) deleted.
  Shared `app/projectkb/llm_json.py::parse_json_list_field()` factored out
  (added in step 23's review) — both ingest and heartbeat's "parse the
  model's `{"<field>": [...]}` JSON, tolerate malformed shapes" logic was
  identical.
- **Step 23 DONE 2026-07-23** (`prompts/step_23_heartbeat_user.md`):
  heartbeat, user-level half. `app/projectkb/jobs/heartbeat.py::run(db,
  manager_id, client=None)` — per manager: unprocessed `Claim` rows (cap
  `HEARTBEAT_CLAIM_BATCH_SIZE`, default 40, `app/config.py`) + context
  (this manager's owned/member projects from the control-plane registry,
  last 10 `Event` rows, `memory.md` if it exists — none of step 25's md
  files exist yet, read-if-exists) go into ONE non-tool-calling
  `SMART_MODEL` call (deliberate scope cut — full tool-based KB
  inspection is deferred alongside agent context-engineering, per the
  user's own sequencing; don't "fix" this into a tool loop without that
  design conversation). Output: typed/tagged/severity `Event` rows.
  Code-enforced severity floor (doctrine can't be trusted to the model
  alone): `blocker`/`clarification` always ≥1 regardless of what the
  model returns. `general` is DERIVED (`not project_ids`), never read
  from the model's own `general` field — fixed in review after the
  reviewer caught that trusting the model's field let `general=true`
  coexist with populated `project_ids`, a state `Event.general`'s own
  docstring rules out. project_ids/claim_ids are filtered to only ids
  actually handed to the model (owned/member projects, this run's claim
  batch) — a hallucinated id silently drops. All batch claims marked
  `processed=True` in the same commit as the event inserts (some claims
  legitimately produce zero events — that's fine, they're still
  "consumed"). Context assembly (control-plane query, md read) was moved
  inside the same try/except as the LLM call in review — a control-plane
  hiccup now fails the run safe (claims stay unprocessed) instead of
  raising out of `run()` uncaught. New `manager_memory_md_path()` helper
  in `app/tenancy/paths.py` (step 25 owns writing it). `task_ids` is
  deliberately left unpopulated here — task-level tagging needs direct
  Task-table access, which belongs to step 24's project fan-out.
  `tests/test_heartbeat_user.py` (13 tests) — do not confuse with the
  still-failing v1 `tests/test_heartbeat.py`.
- **Step 24 DONE 2026-07-23** (`prompts/step_24_heartbeat_project_fanout.md`,
  refined mid-step in direct chat with Shivam — the prompt file itself
  was updated to match before coding, per spec-first convention). Project
  fan-out runs INSIDE the same `heartbeat.py::run()` call, right after the
  user-level phase, not as a separate scheduled job: `_run_project_fanout`
  groups this tick's project-tagged (`general=False`) events by
  `project_id`, keeps only projects this manager actually manages
  (registry `manager_user_id` check — manager-only project truth), and
  calls `_fanout_one_project` per touched project. That function opens
  the project's OWN `db.sqlite` (`app.projects.db`), loads its open
  (non-`done`) tasks + `summary.md`, and makes ONE structured-output
  `SMART_MODEL` call for: task status transitions (must cite one of this
  run's event ids as evidence — code-checked, a transition without a
  valid citing event is dropped, never trusted from the model alone),
  new task/subtask drafts (always forced `status="pending_approval"`,
  `created_by="agent"` regardless of what the model returns), and
  `request_task_links` — `{event_id, task_id}` pairs for `type="request"`
  events that are literally asking to mark a task done. **Archive writes
  are deterministic, NOT LLM-synthesized** — corrected mid-step per
  Shivam ("you don't need to further synthesise event(s) using an LLM
  call... put all the events... in the project's archive, with an
  appropriate timestamp"): one `ArchiveEntry` row per event handled, plain
  logging, no second model call. Two separate commits per project — the
  project db (transitions/drafts/archive) first, then the manager's own
  db (Event.task_ids backfill for validated request links) — documented
  as best-effort: a crash between them loses only the linkage, never
  duplicates anything, since this run never re-processes the same events.
  New `POST /api/events/{id}/approve` + `/reject` (`app/api/home.py`,
  step 24's actual "approve/reject" mechanism — scoped to
  `type="request"` events only, 400 otherwise; blockers/conflicts get
  their own resolve semantics later, out of scope here). Approve flips
  `ui_state="approved"` (commits immediately) then, best-effort, marks
  any linked task(s) `done` — failures there are logged and swallowed,
  never a 500, since the approval itself must never appear to fail over
  a downstream task update. `Event.ui_state` now also allows
  `"approved"`/`"rejected"` (still a plain string column); `GET
  /api/events`'s default-visibility exclusion generalized from just
  `"dismissed"` to a `RESOLVED_UI_STATES = {dismissed, approved,
  rejected}` set (still overridden by `"promoted"`). Frontend:
  `ProjectDashboard.tsx`'s Requests panel got Approve/Reject buttons
  (hover-revealed, same convention as the existing dismiss `×`) in place
  of dismiss; Updates/Blockers panels unchanged. Two review-caught fixes:
  (1) the approve endpoint originally 500'd on a task-mutation failure
  (e.g. a locked project db) AFTER already committing the event as
  approved — now wrapped so any mutation failure degrades to "approved,
  task untouched" instead of a confusing 500; (2) an event tagged to
  TWO manager-owned projects had its `task_ids` linkage overwritten by
  whichever project's fan-out ran second — now accumulates instead of
  clobbering. Shared `parse_json_object()` added to
  `app/projectkb/llm_json.py` (used for `_call_fanout_llm`'s multi-field
  response, `parse_json_list_field` now delegates to it).
  `tests/test_heartbeat_project_fanout.py` (10 tests) +
  `tests/test_home_backend.py` additions (6 approve/reject tests, new
  `project` fixture). Live-verified via the running dev backend: seeded a
  real request event linked to a real task, hit `/approve` over HTTP,
  confirmed the task flipped `todo`→`done`.
- **Step 25 DONE 2026-07-23** (`prompts/step_25_dream_job.md`):
  `app/projectkb/jobs/dream.py::run(db, manager_id, client=None)`.
  Selection uses `Event.dreamed` (NOT a wall-clock `job_schedule.py`
  cursor — deliberately: that cursor tracks REAL wall-clock scheduling
  cadence, while `Event.created_at` is stamped in SIM time; comparing the
  two would silently miscount "new since last dream" whenever the sim
  clock jumps demo-days forward). Two phases per tick: (1) per-user —
  ALL pending events (general + project-tagged) go into one `SMART_MODEL`
  call that returns a full-rewrite `memory.md` (model instructed to
  merge/dedupe, not blindly append) plus lines appended to `events.md`/
  `dump.md`; (2) per-managed-project (via the same
  `manager_owned_projects_with_events` scoping used by step 24, now
  factored into `app/projectkb/project_scope.py` and reused by both) —
  one call per touched project returns a full-rewrite `summary.md`,
  appended `events.md` lines, new `Suggestion`/`Concern` rows
  (`status="open"`, no dedup — rows accumulate across ticks by design,
  same convention as everywhere else in the codebase), and a health
  nudge. Health: `_compute_base_health_score()` is a pure, documented
  0–100 formula (capped penalties: 15/blocker up to 4, 10/overdue-task up
  to 5, 20/conflict up to 3, 1/day-since-last-progress-event up to 30);
  the LLM may nudge by at most one band (±`HEALTH_BAND_POINTS`=15,
  clamped in code even if the model tries ±3) and MUST supply a
  `health_reason` or the nudge is floored back to 0 — a `HealthLog` row
  lands every tick regardless. New per-manager md path helpers
  (`manager_events_md_path`/`manager_dump_md_path`,
  `app/tenancy/paths.py`) alongside the existing `manager_memory_md_path`.
  Two review-caught fixes: (1) `parse_json_object()` tolerates malformed
  LLM JSON by returning `{}` rather than raising — correct for the other
  jobs, but events are the pipeline's TERMINAL stage, so a garbage
  user-level response used to silently mark the whole batch
  `dreamed=True` with nothing synthesized and no way to ever recover
  those events; now an empty parse result raises explicitly, leaving the
  batch `dreamed=False` for a real retry. (2) `_run_project_synthesis`
  used to write `summary.md`/`events.md` to disk BEFORE committing the
  project db's Suggestion/Concern/HealthLog rows — a failure in between
  (e.g. the commit itself) left the file looking freshly synthesized
  while the backing DB state silently rolled back; reordered so the DB
  commit happens first and file writes only follow a successful commit.
  `tests/test_dream_job.py` (20 tests). Live-verified end-to-end
  including through the real `GET /projects/{id}/insights` endpoint the
  frontend already consumes — suggestions/concerns/health now show real
  data with no frontend changes needed (that endpoint was built ahead of
  this step, in step 21, always-empty until now).
- **Step 28 DONE 2026-07-23** (`prompts/step_28_personal_agent.md`):
  personal agent rebuilt from scratch — everything under `app/agent/`
  (`tools.py`/`situation.py`/`prompts.py`/`heartbeat.py`) was v1-schema
  dead code (`TeamMember`/`AttributedClaim`/`Conflict`/`Followup`/`Entity`,
  never populated by any v2 pipeline stage) and is now rewritten against
  real v2 tables. `situation.py`/`heartbeat.py` deleted outright; `tools.py`/
  `prompts.py` fully rewritten; `harness.py`/`registry.py`/`budget.py`/
  `notes.py` kept, with `run_agent`/`ToolRegistry.execute` gaining
  `manager_id`/`run_context` threading (every tool handler now takes
  `(db, manager_id, run_context, **args)` — `run_context` is per-call
  scratch state, currently only the `todo` tool's worklist and the current
  tick's candidate whitelist).

  **A 5th pipeline stage, not a re-detection pass:** ingest→claims,
  heartbeat→events+task transitions, dream→synthesis/health, agent
  (new)→ACTS on what those already produced (messaging people, dashboard
  mutations, escalation) — never re-derives blockers/conflicts itself.
  New `app/agent/select.py::build_candidates()` — deterministic (no LLM),
  same "code decides WHAT's due, LLM only drafts text" rule as every prior
  job — three candidate kinds: `pre_meeting_brief` (`Meeting` rows,
  `status=scheduled`, `starts_at` within `PRE_MEETING_BRIEF_WINDOW_HOURS`
  (default 2), `brief_sent_at IS NULL` — new column + migration in
  `app/tenancy/db.py`), `followup` (per-owned-project open `Task` rows that
  are `blocked` or overdue, deduped once/day via ref_key date-bucketing),
  `conflict_contact`/`conflict_escalate` (`Event.type="conflict"` rows —
  first sighting → contact both claim holders; still open after
  `CONFLICT_ESCALATE_AFTER_HOURS` (default 24) since contact → escalate to
  manager). New `app/database.AgentActionLog` (unique on
  `action_type, ref_key`) is the idempotency ledger — written by the TOOL
  HANDLER after a real action, never left to the model to remember (same
  "code enforces the invariant" pattern as heartbeat/dream's severity
  floors); `send_message`'s handler validates any `candidate_kind`/
  `candidate_ref_key` the model claims against `run_context["candidates"]`
  (this tick's actual whitelist) before trusting it — a hallucinated ref
  silently no-ops, same discipline as step 23's project_ids/claim_ids
  filtering.

  **Tools** (`app/agent/tools.py`, all read `db`/`manager_id`/`run_context`):
  `send_message(channel: slack|portal, target, text, candidate_kind?,
  candidate_ref_key?)` — `slack` resolves `target` (an Employee id, or the
  literal `"manager"` → the calling manager's own Employee row by email
  match) to `Employee.slack_id`, opens a DM via new
  `SlackConnector.open_dm()` (conversations.open), sends through the
  existing `send_or_hold` quiet-hours gate unchanged; `portal` inserts a
  `ChatMessage(role="assistant")` so it shows up in the manager's own
  dashboard chat thread. `dashboard_action(action: create_task|
  update_task_status|add_project_member|create_todo|create_meeting, ...)` —
  one enum-dispatched tool per Shivam's ask; `create_meeting` has a 30-min-
  window title+time dedup guard (agent-inferred meetings, see below, must
  not duplicate on every tick). Read-only probes: `list_meetings`,
  `get_project_doc(project_id, doc: project|summary|notes)`, `list_team`,
  `get_task`, `list_open_conflicts`. `todo` — made real (was a Hermes mock
  placeholder): session-scoped worklist backed by `run_context`, never
  persisted, matches the "create a todo, then complete one by one" framing
  from Shivam's design brief.

  **Meeting data gap resolved as agent-inferred, not manual entry or
  calendar polling** (Shivam's explicit call after the spec draft
  originally proposed manual-only): v2 has no calendar ingestion at all
  (`Meeting`/`ActionItem` are orphaned v1 tables, `app/kb/meetings.py`'s
  creation path was never wired into the v2 frontend). Each agent
  heartbeat tick is handed the last `AGENT_MEETING_SCAN_LOOKBACK_HOURS`
  (default 48) of raw `Claim` text and instructed to spot meeting mentions
  ("let's connect at 5pm") not already in `list_meetings` and create them
  via `dashboard_action`. This is the one deliberately LLM-judged step in
  an otherwise deterministic-selection design — natural-language time/
  attendee extraction is inherently a judgment call, same precedent as
  ingestion's own flash-model claim extraction.

  **Two chat entry points, both through the same rewired harness**
  (`app/agent/context.py::build_agent_context()` — owner profile, owned+
  member projects' `summary.md`/`events.md` tail, `memory.md`, today's
  `Event` rows, project-scoped team roster, recent claims, sim time —
  replaces the dead `situation.py` digest): `POST /api/chat` (dashboard,
  unchanged route shape, now passes `manager.id` through) and **Slack DM
  replies to Harry** (confirmed in scope after Shivam's answer reversed
  this spec's original dashboard-only draft) — `app/integrations/
  slack.py`'s webhook, after a successful `ingest()`, now calls
  `app/agent/direct_contact.py::handle_agent_dm()` when
  `channel_type=="im"` and the event carries no `bot_id` (the loop-
  prevention guard — more reliable than `Agent.user_id`, which the
  current admin-seeded install flow never populates), runs the harness on
  that single message, replies via `connector.send()` on the same DM
  channel. Guarded `"pytest" not in sys.modules` so the general test suite
  never makes a live Gemini call from this path (same convention as
  `SlackConnector._skip_live_calls`/`verify_signature`) — covered instead
  by `tests/test_agent_heartbeat_job.py`'s fake-client pattern.
  **Review-caught fix:** the webhook's `return {"message_id": msg.
  platform_msg_id, ...}` sat AFTER `db.close()` — harmless before this
  step (nothing else touched the session in between), but
  `handle_agent_dm`'s own `db.commit()` calls on the SAME session expire
  *every* object in it (SQLAlchemy's `expire_on_commit` default, not just
  objects the commit touched), so accessing `msg`'s fields afterward now
  raised `DetachedInstanceError`; fixed by capturing `message_id`/
  `sender_mapped` into locals immediately after `ingest()`, before any
  further commits.

  New `JobName.AGENT_HEARTBEAT` (`app/projectkb/jobs/agent_heartbeat.py`),
  registered in the scheduler/job_schedule fixed-job dict exactly like the
  other five — real wall-clock cadence, default 30 min
  (`AGENT_HEARTBEAT_INTERVAL_MINUTES`), independent of ingest/heartbeat/
  dream's own cadences (it reads their already-persisted output, doesn't
  chain off them). `POST /api/heartbeat/run` (manual trigger,
  `app/agent/api.py`) now calls this job instead of the deleted v1 triage
  heartbeat. Live-verified end-to-end against the real dev server + real
  Gemini: seeded a meeting 45 min out with no Slack-id'd employee →
  heartbeat correctly attempted Slack first, got a clean error, fell back
  to nothing (per-candidate, not global fallback — the model chose to
  retry via portal itself, both attempts visible in `tool_trace`), logged
  `AgentActionLog` + set `Meeting.brief_sent_at`; second tick correctly
  found zero candidates (idempotency); `/api/chat` correctly answered "what
  meetings do I have" via `list_meetings` in the same session.
  `tests/test_agent_select.py` (7), `tests/test_agent_tools.py` (8),
  `tests/test_agent_heartbeat_job.py` (2), `tests/test_agent.py` rewritten
  (5, was testing the deleted v1 harness/schema). New shared fixtures in
  `tests/conftest.py`: `cleanup_projects` (moved from
  `tests/test_projects_registry.py`, now shared), `manager_employee_id`
  (creates an Employee row matching the test manager's own email + a fake
  `slack_id` — dev-login, unlike real Outlook login, doesn't auto-create
  one). `tests/test_heartbeat.py` (the pre-existing failing v1 test file
  CLAUDE.md previously listed as "known failing, not a v2 regression")
  deleted — it directly imported `app.agent.situation`/`app.agent.
  heartbeat`, both now genuinely gone rather than just untested.
  `tests/test_scheduler.py::test_04_followup_lifecycle` remains the one
  pre-existing failure (v1 followups/heartbeat code mid-refactor,
  unrelated to this step).

- **Pipeline debug tooling 2026-07-23** (not spec'd/wireframed — a test
  utility, same tier as `login.html`/`connect.html`, not the product
  surface): `app/static/debug.html` (plain HTML/vanilla JS, no build, same
  convention as those two pages) — manual "Run now" button per job
  (outlook_poll/slack_poll/ingestion/heartbeat/dream/lint/agent_heartbeat)
  showing its currently-effective interval, plus read-only tables for
  Messages / Claims / Events with click-to-trace chips (a claim's source
  chips highlight+scroll to the cited message rows; an event's claim chips
  do the same into the Claims table) — the message → claim → event chain
  spec/architecture_v2_kb.md describes, made visually inspectable. Backing
  API: new `app/api/dev_tools.py` (`GET/POST /api/dev/jobs`,
  `GET /api/dev/claims`) — manager-scoped like every other route (cookie
  auth), `POST /api/dev/jobs/{job_name}/run` runs that job against the
  LOGGED-IN manager's own data, same as the pre-existing
  `POST /api/heartbeat/run`. `GET /api/dev/claims` was a genuine gap: no
  claims-listing endpoint existed anywhere before this (only messages and
  events did). Live-verified the full chain through these exact endpoints:
  manual message → `ingestion` → 3 claims citing it → `heartbeat` → 1
  blocker event citing all 3 claims, all fields matching what the page's
  JS expects. One real fix along the way: `GET /api/time`'s field is
  `sim_time_ist`, not `current_time`/`sim_time` as first guessed — caught
  by hitting the live endpoint rather than trusting the guess.
  Headless-Chrome screenshot verification was skipped — hit the exact
  documented gotcha above ("headless Chrome hangs on fresh
  `--user-data-dir`"), same as the React frontend's screenshot trick;
  didn't fight it since the actual risk (JS assuming wrong response
  shapes) was already checked directly against the live endpoints via
  curl.

  **Current job frequencies** (defaults from `app/config.py`, overridable
  via `job_schedule.json`/per-manager override — see
  `app/projectkb/job_schedule.py`): outlook_poll 5 min, slack_poll 5 min,
  ingestion 15 min, heartbeat (KB, user+project fan-out) 60 min, dream
  1440 min (24h), lint 10080 min (7 days), agent_heartbeat 30 min.

- **Chat widget enabled 2026-07-23** (frontend, closes the "Chat!" gap left
  disabled since the Connectors-page frontend note): TopNav's disabled
  "Chat!" pill removed; replaced with `frontend/src/components/
  ChatWidget.tsx` — a floating circular orb (bottom-right, fixed, all
  pages) that expands into a chat panel on click, lifted into `App.tsx`
  alongside `QuickAddTodoModal` (same "reachable from anywhere" pattern).
  The orb is a slow-rotating conic-gradient (brand blues/purple —
  `nav`/`sidebarbtn`-family colors, not a flat button) — deliberately the
  one bit of personality against an otherwise buttoned-down page;
  `prefers-reduced-motion` respected. New `getChatHistory`/
  `postChatMessage` in `lib/api.ts` hitting the already-existing
  `GET /api/chat/history` / `POST /api/chat` (both were built with step
  28's agent work but had no frontend consumer until now). Confirmed the
  backend already had both memory layers the widget needed with no
  changes required: working memory is rebuilt fresh per call
  (`app/agent/context.py::build_agent_context` — projects' summary.md +
  today's events + team roster + durable `memory.md`, assembled from real
  DB/file state every turn) and session memory is the last 20
  `ChatMessage` rows persisted to `db.sqlite` and replayed into the
  harness as conversation history on every `/api/chat` call (`app/agent/
  api.py::post_chat_message`) — so a multi-turn conversation genuinely
  carries context, not just a single stateless reply. Live-verified via
  curl against the real dev server: dev-login → empty history → sent "hi
  Harry, what meetings do I have today?" → got a real tool-called reply
  (`list_meetings`, correctly scoped to today's sim date) → `GET
  /api/chat/history` showed both turns persisted in order. `npm run build`
  clean. Headless-Chrome screenshot skipped (same documented
  `--user-data-dir` hang as the pipeline-debug-tooling entry above) — the
  actual integration risk (endpoint shapes, persisted history ordering)
  was already verified directly via curl.

- **Project card health/blockers/actions flowers wired up 2026-07-23**
  (bug: cards showed permanently grey flowers even after real events/
  claims/health were flowing — `frontend/src/components/ProjectCard.tsx`'s
  blockers/actions flowers were hardcoded to `NO_DATA_COLOR` literals, and
  `health` was typed as an optional field `GET /api/projects` never
  actually returned; the "pipeline jobs don't exist yet" comment above
  them was stale post step-24/25). Backend: `GET /api/projects` (and
  `GET/POST/PATCH /api/portfolios[/{id}]`'s embedded project list) now
  carry real `health`/`blockers_count`/`actions_count` per project via new
  `app/api/projects_registry.py::_project_card_stats` — health is the
  latest `HealthLog.final_score` from that project's OWN db.sqlite, banded
  green/yellow/red at the same >=70/>=40 thresholds `dream.py`'s
  `_compute_base_health_score` docstring already defines (`_health_band`,
  no shared constant existed so this is the one place outside dream.py
  that encodes it — flag if a third place ever needs it); blockers/actions
  counts come from the calling manager's OWN db (`Event.type in
  (blocker, clarification)` / `type == request`, unresolved via the same
  `RESOLVED_UI_STATES` set `app/api/home.py`/`app/agent/select.py` already
  use), grouped by `Event.project_ids`. New `mdb: Session =
  Depends(get_manager_db)` param threaded through `list_projects` and all
  four portfolio endpoints (`_portfolio_detail` now takes `mdb` too).
  Known limitation, NOT new — inherited from the existing architecture
  (project truth/events only exist in the OWNING manager's own db, per
  "only the manager's pipeline writes project truth"): a project a manager
  only *belongs to* (not owns) shows real health (health lives in the
  project's own db, ownership-independent) but blockers/actions read 0
  even if the owner's pipeline has real ones, same visibility gap
  `GET /api/events?project_id=` already has for non-owners. One review-
  caught bug before shipping: `_project_card_stats` unconditionally opened
  `get_project_session(p.id)`, which crashed
  (`OperationalError: no such table: health_log`) on registry rows with no
  scaffolded `db.sqlite` — real code paths always scaffold at creation,
  but `tests/test_portfolios.py`'s `_make_project` helper inserts a bare
  `RegistryProject` row directly (a legitimate test shortcut, not a bug to
  fix there); now guarded with a `project_db_path(p.id).exists()` check
  that just leaves that project's health at `None` (same as
  pipeline-hasn't-reached-it-yet) instead of raising.
  Frontend: `ProjectCard.tsx` derives blocker/action flower color from the
  real counts using the app's existing severity-color convention (>0 →
  `SEVERITY_META[3]`/critical for blockers, `SEVERITY_META[2]`/attention
  for actions; 0 → `ALL_CLEAR` green) instead of a flat grey constant.
  `Projects.tsx`'s health sort option (previously `disabled` with a
  "waits on health scoring" tooltip) is now live, worst-first
  (`HEALTH_RANK`, unscored projects sort last, not first — "no data" isn't
  "healthy"). Live-verified via curl against the real dev server + real
  seeded demo data (`shivamk.iitd@outlook.com`'s 6 team projects): green/
  yellow health bands and non-zero blocker counts came back correctly
  differentiated per project, `null` health only on the two projects the
  dream job genuinely hasn't scored yet. `npm run build` clean; full
  `pytest tests/` back to the one pre-existing unrelated failure
  (`test_scheduler.py::test_04_followup_lifecycle`).

- **Chat widget: fixed stuck-top-left bug, made draggable, recolored
  2026-07-23** (`ChatWidget.tsx`, found live during Shivam's manual
  testing pass): the orb was rendering pinned to the top-left of the page
  instead of floating bottom-right. Root cause: the component's own
  inline `<style>` block declared `.orb { position: relative; ... }` (for
  a `::after` pseudo-element highlight trick) using the SAME class the
  button used for Tailwind's `fixed` utility — same CSS specificity, but
  the `<style>` tag renders later in the DOM than Tailwind's stylesheet,
  so the plain-CSS rule won the cascade and silently downgraded the
  button from `position: fixed` back into normal document flow. Fixed by
  never letting a class carry `position` for this element again: the
  button's position is now driven entirely by inline `style={{ position:
  "fixed", left, top }}` (inline always wins the cascade, so a
  same-named/later class can never re-break it), and the gradient
  highlight moved from a `::after` pseudo-element to a real child `<span
  className="orb-fill">` so no CSS class ever needs to touch the button's
  own `position` at all. While fixing it, also added the drag + recolor
  Shivam asked for: pointer-event-based dragging (`onPointerDown/Move/Up`
  on the button, `setPointerCapture` so the drag tracks past the
  button's own bounds, a `DRAG_THRESHOLD`-px movement check to
  distinguish a drag from a click so the widget doesn't pop open every
  time you nudge it), clamped to the viewport, defaulting to bottom-right
  on mount (`defaultPosition()`) — position is plain component state, NOT
  persisted, so reopening the app always resets to bottom-right rather
  than leaving the orb stranded somewhere from a previous session. The
  chat panel now opens anchored to whichever screen quadrant the orb is
  currently in (`openLeft`/`openTop`), so a dragged orb never pops the
  panel off-screen. Recolored from the blue/purple brand gradient to a
  crimson one (`#8B0000` → `#DC143C` → `#FF4D6D`) per Shivam's ask — kept
  isolated to the orb + its header mini-orb, the panel chrome itself
  stays brand-blue (`bg-nav`) to match the rest of the app's UI. Keyboard
  activation (Tab + Enter/Space) needed its own `onKeyDown` handler since
  it never goes through the pointer handlers — deliberately not `onClick`,
  since a mouse click's synthesized `click` event firing after
  `handlePointerUp` had already toggled `open` would otherwise immediately
  toggle it back off. `npm run build` clean.

- **Slack reader missing `:read` scopes -- `slack_poll` silently returned
  zero for every manager 2026-07-23** (bug found on Shivam's very first
  real manual test: Ally DM'd Sam on Slack, `slack_poll` reported
  `fetched: 0`). Root cause: `fetch_since` (`app/integrations/slack.py`)
  calls `conversations.list` with
  `types=im,mpim,private_channel,public_channel` -- Slack requires the
  matching `:read` scope for EVERY conversation type in that call, not
  just `:history` for reading messages once you have a channel id. The
  reader app's `USER_SCOPES` (`app/controlplane/slack_auth.py`) only ever
  granted `im:read`, never `mpim:read`/`groups:read`/`channels:read`, so
  `conversations.list` failed outright with `missing_scope` and
  `fetch_since` returned `[]` before fetching a single message --
  confirmed by calling the real Slack API directly with Sam's actual
  stored token. Fixed: `USER_SCOPES` now includes all three missing
  scopes; `SLACK.md` part 1 step 4 updated to match. **This requires
  every already-connected manager to disconnect + reconnect Slack
  reading** (Connectors page) -- Slack doesn't retroactively widen an
  already-issued token's scope, re-consent is the only path, same as any
  OAuth scope change. `tests/test_slack_auth.py` only asserted
  `user_scope=` is present in the authorize URL (not its exact value), so
  no test needed updating, but this was a real live-testing gap, not a
  false alarm -- flagging in case a stricter scope-content test gets
  added later. **Note for Shivam:** the reader Slack app's own **OAuth &
  Permissions -> User Token Scopes** page (Slack's site, not this repo)
  also needs `mpim:read`/`groups:read`/`channels:read` added -- the code
  change alone doesn't grant new scopes on an app that hasn't had them
  added on Slack's side; add them there first, then disconnect/reconnect
  each manager.

- **Second, deeper bug behind the same symptom: `slack_poll` reported
  `fetched>0` but `stored: 0` even after the scope fix above, 2026-07-23**
  (found on Shivam's very next test after the scope fix). Root cause:
  `SlackConnector._resolve_dm_other_participant`'s fast path (and
  `_sync_manager_slack_handle`, called from the OAuth callback) both
  depend on `app.integrations.base.get_manager()`, which looks up the ONE
  `TeamMember` row (per-manager db) whose `role` contains "manager" --
  but nothing in the entire v2 provisioning flow ever created that row
  (`app.tenancy.db.init_manager_db` only auto-seeds a "Harry" placeholder
  row, never one for the manager themselves). So for every manager,
  `get_manager()` returned `None`, the DM-counterpart fast path never
  fired, normalize() fell through to a live `conversations.members` API
  call that used the wrong token (`_api_call`'s default is
  `self.bot_token` -- an installed pool bot's token, not the reader's own
  user token, and the bot isn't a member of a DM between two humans
  anyway), got nothing back, and returned `None` -- every real DM's
  `normalize()` came back `None` ("ignored_not_a_message"), so
  `ingest()` never stored a single one, no error anywhere. Confirmed by
  querying `managers/<id>/db.sqlite`'s `team_members` table directly:
  only `U_HARRY` existed, no manager row. Fixed in
  `_sync_manager_slack_handle` (`app/controlplane/slack_auth.py`): now
  CREATES the manager's own `TeamMember(role="manager",
  slack_handle=authed_user.id)` row if `get_manager()` finds none,
  instead of only updating an existing one. Deliberately NOT moved into
  `init_manager_db`'s provisioning-time seed (tried that first, reverted
  -- it runs at every `dev-login` too, including the test suite's shared
  `client` fixture, and 6 existing tests across 5 files manually seed
  their OWN manager `TeamMember` row expecting to be the only
  role-matching one `get_manager()`'s `.first()` can find; auto-creating
  a second one at every dev-login broke all 6). Scoping the fix to the
  real OAuth callback path instead means it only fires exactly where the
  bug lived, and every test that doesn't call that route is untouched --
  confirmed via a full pytest run, back to only the one pre-existing
  unrelated failure. This is a real backfill gap for every manager who
  connected Slack reading before this fix (their `TeamMember` row will
  never retroactively appear without either reconnecting, which
  re-triggers the callback, or a manual DB fix) -- Sam's case resolved
  itself by luck (the backend's autoreload picked up an interim draft of
  this fix that also backfilled from `SlackReaderInstallation.user_id`,
  before it got reverted in favor of the narrower final version), so
  his row already exists correctly; anyone else who connected earlier
  will need to disconnect + reconnect once more.

- **Third bug, same family: outbound (manager → teammate) Slack DMs were
  silently dropped too, 2026-07-23** (Shivam noticed after the TeamMember
  fix landed: "outbound messages, that is manager to other person should
  also come up"). Root cause: `_resolve_dm_other_participant`'s fast path
  only covers the INBOUND case (`known_id != manager.slack_handle` — a
  teammate DMed the manager); when the manager is the one who sent the
  message, `known_id == manager.slack_handle`, so the fast path can't
  fire and it always fell to the live `conversations.members` call —
  which defaulted to `self.bot_token` (a pool agent's bot token). The bot
  isn't a member of a DM between two humans, so that call came back
  empty/errored every time, `normalize()` returned `None`, and
  `ingest()` reported `"ignored_not_a_message"` for every manager-sent
  DM, no error surfaced anywhere (same failure shape as the second bug
  above, different code path). Fixed by threading `manager_id` through
  the whole chain that previously stopped at `db`/`raw`:
  `ChannelConnector.normalize(db, raw, manager_id=None)` (base.py
  abstract method + `ingest()`'s call site now pass it; `manager_id` is
  optional/unused for Outlook, added only for parity) →
  `SlackConnector.normalize` → `_resolve_dm_other_participant(db,
  channel_id, known_id, manager_id)`, which now resolves this manager's
  own reader `user_token` (`resolve_reader_by_manager(manager_id)` — the
  same token `fetch_since` already polls with, and which IS a member of
  the manager's own DMs by construction) and uses THAT token for the
  live `conversations.members` call instead of the bot token. No
  `manager_id` (or no reader connected) → skip the live call and return
  `None`, same graceful-degradation shape as before. `tests/
  test_connectors.py`/`test_outbound.py`/`test_poll_completion.py`/
  `test_slack_auth.py`/`test_slack_channels.py` (61 tests, the ones
  touching this code) all still pass; full suite back to the same one
  pre-existing unrelated failure.

- **`debug.html` Messages/Claims/Events tables sorted + capped
  2026-07-23** (Shivam, live-testing UX ask): all three tables were
  rendering in raw DB/API order (oldest first, unbounded), so the row
  you'd just triggered a job to create sat at the bottom of a
  fast-growing list. New `sortDesc(arr, tsField)` helper sorts
  newest-first and slices to `DEBUG_ROW_LIMIT = 20`; each panel's count
  pill now reads `"20 of 47"` (shown vs. total fetched) rather than just
  a raw count, so it's clear the table is capped, not that there are only
  20 rows total.

- **Agent couldn't open a new Slack DM ("could not open a DM with her"),
  2026-07-23** (Shivam, live-testing: the agent replied it was blocked
  trying to reach Ally to schedule the AI-agent-onboarding meeting).
  Backend log showed `[slack] conversations.open failed: missing_scope`.
  Root cause: `SlackConnector.open_dm` (the personal agent's `send_message`
  tool path for messaging a teammate who's never DMed the bot before)
  calls `conversations.open` with the pool bot's own bot token, which
  needs the **`im:write`** Bot Token Scope -- never added to any pool app
  (`SLACK.md` part 2 only ever listed `im:history`/`im:read`/`chat:write`).
  `chat:write` alone lets a bot post into an EXISTING DM but not open a
  brand-new one -- a separate permission. `SLACK.md` updated with the
  missing scope. **Requires admin action, same re-consent pattern as the
  reader-scope bug above:** on each pool app's own OAuth & Permissions
  page (Slack's site), add `im:write` under Bot Token Scopes, then
  **reinstall to workspace** (scope changes need reinstall, not just save)
  -- this issues a NEW bot token, which must be copied into
  `agents_pool.json` and re-seeded via `.venv/bin/python3 -m
  scripts.seed_agents`. Not yet done as of this entry -- flagging so the
  next attempt to have the agent open a new DM is expected to keep failing
  until that's done.

- **Same DM-open error persisted after the `im:write` scope fix + reinstall
  -- real cause was agent resolution, not the scope, 2026-07-23**: even
  after Atlas got `im:write` and a fresh bot token, `conversations.open`
  kept 400ing with `missing_scope`. Root cause:
  `SlackConnector._resolve_active_agent()` picked "whichever pool bot was
  installed most recently" with NO manager scoping at all (the docstring's
  own "single-tenant convenience" concession) -- `kettle-bot`/`nova-bot`
  are unclaimed but were installed more recently than Atlas (the agent
  Sam actually claimed and the only one with the scope fix), so `open_dm`
  kept authenticating as kettle-bot's stale token instead. Confirmed by
  querying `agents.installed_at` directly: kettle-bot/nova-bot both sort
  above Atlas. Fixed: `_resolve_active_agent(manager_id=None)` now prefers
  the `Agent` row actually claimed by `manager_id` (`Agent.manager_id ==
  manager_id`) when one is given, only falling back to the old
  most-recently-installed behavior when no manager_id is available (most
  callers still don't have one -- see `app.outbound`'s callers, untouched,
  same "later refactor" scope as before). `open_dm(slack_user_id,
  manager_id=None)` now threads it through and resolves its own token
  explicitly (no longer relies on the `self.bot_token` property, which
  still has no manager_id path) -- `app/agent/tools.py`'s
  `send_message_handler` (which already had `manager_id` in scope) now
  passes it. Live-verified directly against real Slack after the fix:
  `_resolve_active_agent(sam's manager_id)` correctly returns `atlas`, and
  `open_dm("U0BK5U95VPU", sam's manager_id)` successfully opens a real DM
  channel with Ally. `connector.send()`/`is_configured()`/`bot_token`
  still aren't manager-scoped (unchanged, same pre-existing gap) -- only
  the `open_dm` path used by the agent's `send_message` tool got fixed
  here; if a similar wrong-bot symptom ever shows up through a different
  send path, this is the pattern to apply there too.

- **Pending steps, prompts pre-written 2026-07-23** (spec §7 implementation
  order items 8-9, not yet implemented): `prompts/step_26_lint_job.md`
  (deterministic integrity checks + optional non-blocking LLM coherence
  pass), `prompts/step_27_frontend_remaining_gaps.md` (blocklist settings
  UI, full Create-Project form per wireframe 5.png, workload view — grab-bag
  not sequentially dependent on the others; Portfolios pages 4/8.png done
  2026-07-23, see the "Live-testing fixes" bullet below). `app/projectkb/jobs/lint.py`
  still describes v1-era concepts (timeline.md tiers, todos/conflicts) in
  its docstring — step 26 replaces that docstring along with the code,
  don't preserve the stale wording.
- **Live-testing fixes 2026-07-23** (found by Shivam testing the real
  frontend, out of step-sequence — not part of step 26/27): (a) Outlook
  login (`app/controlplane/outlook_auth.py::_handle_login_callback`) now
  also upserts an `Employee` row (by lowercase email, same convention as
  `scripts/seed_employees.py`) for every signing-in manager — previously
  only a `Manager` row was created, so no manager ever showed up in the
  employee directory / was addable as a project teammate, and the Team
  panel looked permanently empty regardless of seeding. Existing
  `Employee` rows (e.g. hand-seeded via `employees.json`) are updated
  in place (name refreshed), not duplicated. (b) `ProjectOverview.tsx`'s
  Team dialog was read-only (list members, no way to add any) — added an
  employee picker + Add button (manager-only, team-kind-only, filters out
  existing members) and a per-member Remove button, both wired to the
  already-existing but previously frontend-unused
  `PATCH /api/projects/{id}` `add_member_employee_ids`/
  `remove_member_employee_ids` fields (`patchProjectMembers` added to
  `frontend/src/lib/api.ts`) — the backend for this was already complete
  from step 18, only the UI was missing. (c) `employees.json` (gitignored,
  dev-only) seeded with the two real logged-in managers plus the four
  example fake employees, via `scripts/seed_employees.py` — needed since
  the directory was empty (no admin had run the seed script yet).
  `tests/test_outlook_auth.py` +2 (Employee-created-on-login,
  Employee-row-reused-and-name-updated-on-repeat-login).
- **Slack redesign 2026-07-23** (Shivam, live-testing feedback — supersedes
  step 17 piece 2b's install flow; piece 2a's claim logic is untouched):
  the original design wrongly coupled two unrelated concerns into one
  OAuth call — "does this manager's own Slack activity get tracked" and
  "does a bot identity exist for this manager" — because `/auth/slack/
  install` required a CLAIMED agent and, in the same consent screen,
  installed that agent's own Slack app into the workspace AND requested
  the manager's user-token together. Connectors' Slack row was gated on
  "claim an agent first," which made no sense to a user who just wants
  their own messages read. Split into two fully independent flows:
  - **Reading** (`app/controlplane/slack_auth.py`, rewritten): ONE global
    "reader" Slack app (`SLACK_READER_CLIENT_ID`/`SECRET`, same pattern as
    the single global Outlook app), user-token-only OAuth (`user_scope`
    only, no bot `scope` at all — this app never gets a bot presence),
    gated only on being logged in. Writes a new `SlackReaderInstallation`
    row (manager_id PK, team_id/team_name/user_token/user_id) — NOT the
    `Agent` table. `GET /api/auth/connections`'s `slack` key now reflects
    ONLY this (`{"connected": bool, "team_id", "team_name"}`, changed from
    `null` to `{"connected": false}` when absent, matching Outlook's
    shape) — completely unaffected by whether/which Agent is claimed.
    `app/integrations/slack.py::fetch_since` /
    `app/projectkb/jobs/slack_poll.py` now read from
    `resolve_reader_by_manager` (queries `SlackReaderInstallation`)
    instead of the old `resolve_agent_by_manager`/`Agent.user_token`.
  - **Agents** (`app/controlplane/agents.py` — piece 2a's claim logic
    itself was ALREADY pure DB bookkeeping and needed no change): what
    changed is that **installing** a pool agent's Slack app into the
    workspace (getting its `bot_token`) is no longer any OAuth code in
    this repo at all. The admin now does it directly on Slack's own site
    (that app's "OAuth & Permissions → Install to Workspace" page, which
    hands them the Bot User OAuth Token directly) and pastes
    `bot_token`/`team_id` into `agents_pool.json`, re-seeded via
    `scripts/seed_agents.py` (extended to accept those two optional
    fields, setting `installed_at` the first time a token appears). New
    `GET /api/agents/mine` (pure DB read: claimed agent info + whether
    `bot_token is not None`) replaces the old conflated `conn.slack` on
    the Agents page — `Agents.tsx`'s "Install to Slack" button is gone
    entirely, replaced with read-only installed/not-installed text.
  - **Review-caught fix**: the webhook handler
    (`app/integrations/slack.py`'s `/webhook`) previously had no guard for
    `agent.manager_id is None` — a message to an installed-but-unclaimed
    pool bot would have proceeded to `get_manager_session(None)` and
    crashed on `MANAGERS_DIR / None`. Added an explicit early return
    (`{"status": "ignored", "detail": "unclaimed agent"}`) right after
    signature verification, so unclaimed bots are now genuinely inert as
    intended, not crash-on-DM.
  - `Agent.user_token`/`user_id` columns are left in place but vestigial
    (no code writes them anymore) — a SQLite `ALTER TABLE ... DROP
    COLUMN` migration wasn't judged worth it for a dev-stage product.
  - Explicitly OUT of scope for this change (next up, not yet built): a
    claimed bot replying when DMed — the webhook already ingests those
    messages into the KB today, but there's no "personal agent" chat-reply
    endpoint yet.
  - `app/integrations/SLACK.md` rewritten top-to-bottom into two parts
    (Reading / Agents) reflecting the split.
    `tests/test_slack_auth.py` rewritten (install/callback/disconnect no
    longer need a claimed agent; new unclaimed-agent-webhook-is-ignored
    test); `tests/test_auth.py` connections tests split into
    reader-installation vs. claimed-agent-doesn't-affect-connections;
    `tests/test_agents.py` +3 (`GET /api/agents/mine`);
    `tests/test_projectkb_scheduler.py`'s two slack_poll tests moved off
    `Agent.user_token` onto `SlackReaderInstallation`.

## Architecture (v1, agreed 2026-07-12 — being superseded)

```
SIMULATOR (exists)                        HARRY'S PRODUCT (being built)
Slack/Outlook clones ──ingest──▶ unified_messages (raw immutable ledger)
 + global sim clock  ◀─render──      │ flash model, per message
Harry's pings render in UI       attributed_claims (holder, weight, superseded_by)
        ▲                            │ smart model, dream-cycle batch
outbound send APIs ◀──tools──  compiled_truths (rewritten synthesis, cited)
(quiet-hours gate               + timeline_entries (append-only, FK → message)
 9:00–19:00 IST)                + conflicts (claim pairs, never auto-resolved)
                                     ▲
                               Agent harness (Hermes-style tool loop, Gemini)
                               Virtual scheduler (sim-time crons)
                               Project Tracker Dashboard (Vue CDN + chat dock)
```

Key concepts (from gbrain — full research index: `spec/research/gbrain_index.md`):

- **Compiled truth + timeline**: per entity (project/person/meeting), a
  rewritten synthesis on top, an append-only cited evidence trail below. Every
  claim traces to a `unified_messages` row via timeline-entry citations
  (inline markers like `[T12]` in synthesis text).
- **Attributed claims**: "who claims what, weight 0-1, superseded_by chain".
  Substrate for deadlock/contradiction detection. Contradictions are pairs →
  `conflicts` table. Harry surfaces conflicts with severity; NEVER auto-resolves.
- **Deterministic collectors**: parsing/dedup/links in code; LLM only judges.
- **Dream cycle**: cheap per-message extraction real-time; expensive synthesis
  in periodic batch sweeps. Quiet-hours gate on every autonomous ping; held
  pings fold into the 9 AM morning brief.

## Simulated time (RETIRED 2026-07-23 — see "Real time switch" below)

Original core demo trick (kept below for history — no longer how the product
behaves): backend-authoritative anchored live clock:
`(anchor_sim_time, anchor_real_time)` stored server-side; current sim time =
anchor_sim + real elapsed. Set/advance endpoints; simulator top-bar widget
drives it. ALL timestamp stamping, agent system prompts ("Current time:
…IST"), and the scheduler read it. Crons are virtual: `scheduled_jobs` keyed
on next-due sim time; advancing the clock runs everything that came due in
the jumped interval, in order. Outbound realism: send endpoints mirror real
Slack `chat.postMessage` / Graph `sendMail` shapes.

**Real time switch (2026-07-23, Shivam's explicit request):** `app.
timeservice.now_ist()` now returns real wall-clock IST (`datetime.now(IST)`),
full stop — it no longer reads the `(anchor_sim_time, anchor_real_time)`
file at all. Trigger: live-testing hit a case where the simulator's sim
clock had drifted to a stale date whose day-of-week (Sunday) made the
quiet-hours gate correctly-but-confusingly hold an outbound message until
"tomorrow" — prompted the question "why sim time at all," and the product
is past the demo-storyline phase where compressing days into an 8-minute
demo was the point. Scope of the change, deliberately narrow (Shivam chose
"just switch the time source" over also deleting the simulator's clock
UI): `now_ist()` is the only thing that changed. `set_time()`/`advance()`/
`reset_to_real()`/`get_state()` and the `/api/time/set|advance|reset`
endpoints are UNCHANGED code-wise — they still read/write the anchor file
correctly — but are now inert for anything that matters: nothing downstream
reads the anchor anymore, so jumping it no longer moves `now_ist()`, and by
extension no longer moves what jobs consider "due" or what quiet-hours
sees. The simulator UI's clock-jump widget (`app/static/index.html`) still
renders and still succeeds when clicked; it just doesn't do anything
real anymore — flagged here rather than removed, since deleting dead UI
wasn't in scope for this change. `GET /api/time`'s `sim_time_ist` field now
always reflects real time regardless of prior `/set`/`/advance` calls.

**Test impact:** every test that previously called `timeservice.set_time(...)`
to deterministically control time-dependent business logic (quiet hours,
meeting briefs, follow-up lifecycles, health decay, scheduler catchup) broke,
since `set_time` no longer reaches `now_ist()`. Fixed by adding a
`set_sim_time` pytest fixture (`tests/conftest.py`) that monkeypatches
`app.timeservice.now_ist` directly (every consumer does `from app import
timeservice` then calls `timeservice.now_ist()`, never `from app.timeservice
import now_ist`, so patching the module attribute reaches every call site) —
freezes at a given datetime, then keeps flowing naturally with real elapsed
time after that, same anchor behavior the old sim clock had. All
`timeservice.set_time(...)` call sites in tests were mechanically swapped for
`set_sim_time(...)` (fixture, not the retired production path);
`test_scheduler.py::test_02_scheduler_catchup`, which drove its 3-day jump
through the (now-inert) `/api/time/advance` HTTP endpoint, was rewritten to
jump `set_sim_time` directly instead. `tests/test_timeservice.py` itself was
rewritten to assert the new contract (`now_ist()` tracks real time regardless
of `set`/`advance`/`reset` calls; those endpoints still 200 and still write
the anchor file, but no longer affect any stamped timestamp). Full suite
verified back to the one pre-existing unrelated failure
(`test_scheduler.py::test_04_followup_lifecycle`) — 281 passed.


All step prompts 4a–10 were pre-written on 2026-07-13 (before their
predecessors were implemented); each carries a "the code wins" clause and
gets adjusted at review time if reality diverged. Demo delivery script:
`spec/demo_script.md` (refine at Step 10).

## Demo storyline (~8 min)

Dashboard overview → signals arrive in simulator → dream cycle rewrites
compiled truth (hover claim = source message: the trust moment) → deadlock
(Bob "sent it" vs Alice "never got it" → conflict flagged, Harry pings both,
escalates to manager) → time-travel +3 days (follow-ups fire, health degrades,
Harry informs manager) → quiet hours (11 PM ping held → 9 AM release + morning
brief) → ask Harry anything (cited synthesis; meeting prep).

## Essential facts

**Run & test:**
- `.venv/bin/python3 -m app.main` (port 3003)
- `.venv/bin/python3 -m pytest tests/`

**Stack:** FastAPI + SQLAlchemy 2.0 + SQLite; Vue 3 simulator at `app/static/index.html`

**Multi-tenancy (Steps 12-16, done):**
Each manager gets `managers/<manager_id>/db.sqlite` + `projects/` +
`job_state.json` + `tracked_contacts.json` — NOT a shared DB with a
`manager_id` column (isolation is structural, separate SQLite files, not
query-discipline-dependent). `app/tenancy/` — `paths.py` (dir/path helpers +
`ensure_manager_scaffold`), `db.py` (`get_manager_engine`/`get_manager_session`/
`get_manager_db` FastAPI dependency/`init_manager_db`/`list_provisioned_manager_ids`).
`app/database.py`'s old global `engine`/`SessionLocal`/`get_db`/`init_db()`
are retired — it now only holds `Base` + table classes (engine-agnostic).
`app/controlplane/` (`data/controlplane.sqlite`) is the one still-global DB:
`Manager`/`AuthSession`/`SlackInstallation`/`OutlookInstallation`. Login:
dev-login (`POST /api/auth/dev-login`, gated by `DEV_AUTH_ENABLED`) or
Outlook sign-in (`GET /auth/outlook/login`); both call `ensure_manager_scaffold`
on first login. Slack connect: `GET /auth/slack/install` (requires login
first — connect, not login). Cookie-authenticated routes use
`Depends(get_manager_db)`. Two exceptions with no cookie available: the
Slack webhook resolves `manager_id` via `team_id` → `SlackInstallation`
(opens a session directly, not through the dependency); `projectkb`'s
background scheduler (`app/projectkb/scheduler.py`) iterates every
provisioned manager each tick via `list_provisioned_manager_ids()`. Minimal
test-the-flow UI: `app/static/login.html` (LOGIN, Outlook sign-in only) →
`app/static/connect.html` (manage page: connect/disconnect Slack+Outlook,
enable-send toggle), state driven by `GET /api/auth/connections`. Outlook
uses incremental consent: base scopes (`Mail.Read,User.Read`) at login,
`Mail.Send` opt-in later via `GET /auth/outlook/enable-send` — see
`OutlookInstallation.granted_scopes`.

**Step 17 (Agent Pool — spec: `prompts/step_17_agent_pool.md`, DONE + live-
verified 2026-07-22, Install/reading REDESIGNED 2026-07-23 — see the
"Slack redesign 2026-07-23" bullet in Architecture v2 above for what's
current):** the **Claim** bullet below is still accurate as-is (pure DB
bookkeeping, unchanged). The **Install** bullet below describes the
ORIGINAL, now-superseded flow (per-agent OAuth requiring a prior claim,
combined bot+user scopes) — as of 2026-07-23, installing a bot is an
admin-only, Slack-side action with no OAuth code in this repo, and reading
is its own always-available flow unrelated to any claim; keep the history
below for context but don't treat it as current behavior.

Slack's step-14 design (single shared bot, bot-token
only) didn't hold up once multiple managers share a workspace, so Slack is
now split into two concerns: **reading** (a user-token grant, the manager's
own Slack identity, isolated per-person by construction) vs **sending/being
messaged** (a dedicated bot per manager, drawn from a pre-created pool).
`SlackInstallation` is retired, replaced by `Agent` (`app/controlplane/
models.py`) — one row per pool Slack app, `manager_id` unique+nullable
enforces one-bot-per-manager at the schema level, holds its own
`slack_app_id`/`slack_client_id`/`slack_client_secret`/`slack_signing_secret`
plus `team_id`/`bot_token`/`user_token`/`user_id` once installed.

- **Claim** (`app/controlplane/agents.py`): admin seeds the pool
  (`scripts/seed_agents.py` + gitignored `agents_pool.json`). `POST
  /api/agents/redeem {code}` → `POST /api/agents/claim {agent_id}` — atomic
  guarded `UPDATE ... WHERE manager_id IS NULL`, 409 on race/double-claim,
  idempotent re-claim. Mirrors an `AgentAssignment` row into the manager's
  own `db.sqlite` too (`app/database.py`).
- **Install** (`app/controlplane/slack_auth.py`, requires a claimed agent):
  OAuth `state` is HMAC-signed per-agent (that agent's own client_secret).
  On success writes `team_id`/`bot_token`/`user_token`/`user_id` onto the
  `Agent` row. **Critical fix, live-verified:** the callback also upserts
  `TeamMember.slack_handle = authed_user.id` for the manager
  (`_sync_manager_slack_handle`) — without this, DM-participant resolution
  can never recognize the manager in webhook/polled events.
- **Webhook** (`app/integrations/slack.py`) routes by `api_app_id` →
  `Agent.slack_app_id` (not `team_id` — multiple agents can share a
  workspace), verified against that agent's own `slack_signing_secret`.
- **Reading is polled, not pushed** (`app/projectkb/jobs/slack_poll.py`,
  `JobName.SLACK_POLL`) — `conversations.list`/`conversations.history` via
  the manager's own `user_token`, shaped into synthetic Events-API dicts by
  `_history_message_to_event` and fed through the existing `normalize()`/
  `ingest()` pipeline unchanged. Deliberate design choice over a webhook:
  whether Slack pushes events for a user-scope grant on conversations the
  bot isn't in was never verified; polling sidesteps that.
- **`disconnect`** clears install-derived fields but keeps the manager's
  agent *claim* — reinstalling reuses the same bot identity.
- **Channels/groups:** `NormalizedMessage.conversation_type` (`"dm"` default)
  gates DMs on the original tracked-contacts logic, channels/groups on the
  parallel `app/projectkb/tracked_channels.py` list. Data model only —
  actually polling channels/groups is NOT yet implemented (`fetch_since` is
  still DM-only, `types=im`).
- **Live-verified 2026-07-22** end-to-end with a real Slack app ("Atlas"):
  claim → install → OAuth token capture → `slack_handle` sync → manual
  `slack_poll.run()` → real message landed in `UnifiedMessage` with correct
  sender/receiver/content. Direct DMs to the bot itself correctly do NOT get
  ingested (by design — the bot's own Slack ID is never a tracked contact).

**LLM:** Gemini via `GEMINI_API_KEY` in config.json/env: `smart_model` (gemini-2.5-pro) + `flash_model` (gemini-2.5-flash)

**Simulator clock:** `app/timeservice.py` — backend-authoritative, virtual crons react to time jumps

**Harness (Step 6):** `app/agent/` — self-registering ToolRegistry, tools imported by harness, Gemini round-trip respected (echo tool_calls + `{role:"tool", name, content}`)

## Frontend rebuild (started 2026-07-22)

**Built so far:** Login (1.png, live-verified with real Outlook OAuth);
Home (2.png: Updates panel ← `/api/events`, TODOs CRUD panel, Tasks +
Projects card rails, hamburger sidebar 9.png w/ Agents entry added,
Projects nav tab added on Shivam's ask); Projects grid (3.png, minimal
create-modal until 5.png's full form); Connectors (10.png — Read/Send
pills + Grant/Revoke per connector from `/api/auth/connections`; Outlook
send-revoke is disabled-with-tooltip since MS can't revoke send alone;
Slack revoke keeps the agent claim; Teams parked in Coming Soon); Agents
(no wireframe — access-code unlock → available-agents grid → claim →
Install-to-Slack, 403/409 handled). Conventions: runtime knobs in
`frontend/src/constants.ts` (severity palette, nav tabs, panel limits);
structural colors in `tailwind.config.js`; Segoe UI stack; the 4-petal
StatusFlower SVG is the status glyph everywhere; unbuilt nav items render
as plain text, never dead links; no-data states are honest (grey flowers,
empty-state copy) — NEVER dummy data. Screenshot trick: temp page in
app/static/ that fetches dev-login then redirects to :5173 (headless
Chrome hangs on fresh `--user-data-dir` — don't use it).

The manager-facing product UI (`app/static/dashboard/`, Vue 3 CDN) is being
rebuilt from scratch as a professional React app, driven page-by-page by
Shivam's own wireframes. Target look: polished B2B SaaS (Azure portal / Jira
register as the bar), not the demo-simulator aesthetic. **This does NOT
touch the simulator** (`app/static/index.html` — Slack/Outlook clones + sim
clock) or the auth test-flow pages (`login.html`/`connect.html`) — those
stay as-is; this is a new, separate frontend for Harry's actual product
surface.

- **Location:** `frontend/` — Vite + React 18 + TypeScript + Tailwind +
  react-router-dom. `npm run dev` (port 5173) proxies `/api` and `/auth` to
  the FastAPI backend on `:3003` (see `frontend/vite.config.ts`) — no CORS
  config needed, cookie auth just works.
- **Workflow:** wireframes arrive one page at a time. Build the page, then
  verify against the real backend (start `.venv/bin/python3 -m app.main` +
  `npm run dev`, hit the actual endpoints) — fix backend gaps as they turn
  up rather than mocking around them. If a wireframe has an obvious flaw
  (misaligned elements, near-duplicate-but-different colors, inconsistent
  font sizes) fix it rather than reproducing the flaw — use judgment, no
  need to check first unless the fix is a real design decision, not just
  cleanup.
- **Assets:** never invent logos/icons. Reusable source assets live in
  `/assets` (repo root, outside `frontend/`) — copy what's needed into
  `frontend/src/assets/`. A missing basic shape you can draw as plain SVG is
  fine to create; anything else, ask Shivam to paste it in rather than
  guessing at a brand mark.
- **Node version note:** the dev machine's Node (`v20.3.1`) is too old for
  the current `create-vite` CLI (needs `node:util`'s `styleText`, Node
  21/22+) — the scaffold here was written by hand rather than via
  `npm create vite`. If re-scaffolding anything, don't assume the CLI works
  without checking.
- npm cache note: `~/.npm/_cacache` has some root-owned subdirectories left
  over from an earlier `sudo npm` run, which breaks plain `npm install`
  with `EACCES`/`EEXIST` on rename. Workaround used: point at a scratch
  cache dir via `npm_config_cache=<tmp-dir> npm install`. A real fix
  (`sudo chown -R $(whoami) ~/.npm`) needs an interactive terminal Claude
  doesn't have — worth doing once from a real shell.

- **UI corrections + Portfolios 2026-07-23** (Shivam, live-testing feedback,
  out of step-sequence): (a) project cards everywhere (Home rails, Projects
  grid) now navigate straight to `/projects/:id/dashboard` instead of
  `/projects/:id` (Overview) — Overview stays reachable via the reciprocal
  "View Project"/"switch to dashboard" buttons already on each page. (b)
  **Portfolios built** (wireframes 4.png/8.png, closing the step_27 grab-bag
  item) — resolved as a real entity per that prompt's explicit design fork
  (named, independently deletable, projects added/removed one at a time —
  not a saved filter/view): new control-plane tables `Portfolio` +
  `PortfolioProject` (`app/controlplane/models.py`), CRUD at
  `POST/GET/PATCH/DELETE /api/portfolios[/{id}]` (`app/api/
  projects_registry.py`, scoped to the owning manager only, `add_project_ids`
  validated against that manager's own visible-projects set). Frontend:
  `Projects.tsx`'s Portfolios/Projects toggle is now live (was a disabled
  stub); new `PortfolioDetail.tsx` (`/portfolios/:id`) — editable name
  (blur-to-save), delete, member-project grid with per-card remove, "+"
  add-picker restricted to projects not already in the portfolio.
  `tests/test_portfolios.py` (9 tests: CRUD, re-add-is-noop, cross-manager
  scoping, 401/404s). (c) **Team view** added to `ProjectDashboard.tsx`'s
  Tasks table (wireframe 6.png's "switch to: Team view" link, previously
  unbuilt) — flattens the task tree (parents + all nested subtasks, via new
  `flattenTasks()`) and groups by `assignee_employee_id` into one mini task
  table per project member (plus a trailing "Unassigned" group), Assigned
  column dropped since it's implied by the grouping. Fixed a latent column-
  misalignment bug found while building this: `TaskRow` always rendered an
  Assignee `<td>` regardless of the table header, so "My Tasks" (which hid
  the column by blanking `assignee_name` rather than actually omitting the
  cell) was one column short of its own header row — `TaskRow` now takes an
  explicit `showAssignee` prop instead. (d) Small visual fix: circular "+"
  buttons (Projects/Tasks grid floating button, ProjectDashboard's "Add
  task") were missing `flex items-center justify-center`, so the glyph sat
  low in the circle instead of centered. (e) **Tasks got its own grid +
  nav tab** (`Tasks.tsx`, `/tasks`, mirrors `Projects.tsx` filtered to
  `kind="personal"`) — previously personal projects ("Tasks") only lived in
  Home's card rail with no dedicated page, unlike team projects.
  `NAV_TABS` gained a "Tasks" entry alongside the existing "Projects" one.
  (f) **Quick-add-todo popup**: the TopNav "Todo" button used to
  `navigate("/#todos")`, forcing a trip to Home even from deep pages: now
  opens `QuickAddTodoModal` (new component) directly, lifted to `App.tsx`
  so it's reachable from anywhere; Home's now-dead `#todos` hash-scroll
  effect removed. (g) Found and fixed a real bug while live-testing (c):
  see the "Schema changes also sweep N project DBs" gotcha below — task
  creation was 500ing on the one pre-existing real project because its
  db.sqlite predated the `Task.priority` migration and nothing had ever
  re-run it.
- **Reader Slack app credentials set + two more pool bots seeded
  2026-07-23:** `SLACK_READER_CLIENT_ID`/`SECRET` in `.env` now hold real
  values (the reader app created per SLACK.md part 1) — `GET
  /auth/slack/install` no longer 500s. `agents_pool.json` (gitignored)
  grew two more entries (`nova-bot`, `kettle-bot`) alongside `atlas`, none
  with a `bot_token` yet (admin hasn't installed them to a workspace via
  Slack's own site — see the "Slack redesign 2026-07-23" bullet above) —
  seeded via `.venv/bin/python3 -m scripts.seed_agents`, they show up as
  claimable-but-not-installed. New **`POST /api/agents/release`**
  (`app/controlplane/agents.py`) — the Agents tab "Remove" button, requested
  alongside the credentials update. Symmetric with `claim`: clears
  `manager_id`/`claimed_at` on the `Agent` row (freeing it back into the
  available pool) and deletes the manager's own `AgentAssignment` mirror
  row, but deliberately leaves `bot_token`/`team_id`/`user_token`/`user_id`
  alone — same reasoning as `slack_auth.disconnect`, those belong to the
  Slack app installation, not the claim, so re-claiming the same bot later
  reuses its identity rather than needing reinstall. `Agents.tsx` gained a
  "Remove" button next to "My agent" (confirm dialog, mirrors the
  Portfolio-delete confirm pattern) wired to new `releaseAgent()` in
  `lib/api.ts`. `tests/test_agents.py` +4 (401 without session, 404
  without a claim, frees the agent + removes the mirror + is re-claimable,
  install-derived fields survive release).
- **Demo data + live pipeline verification 2026-07-23**
  (`scripts/seed_demo_projects.py`, `scripts/seed_demo_messages.py`, both
  one-off/re-runnable, not part of boot/tests): 6 team projects (2 demo-
  ready — "AI chief of Staff", "Pulse.ai Frontend Rebuild" — with full
  project.md/notes.md/summary.md/events.md, tasks+subtasks across every
  status, archive/suggestions/concerns/health seed rows, and both real
  employees — Shivam, Ally K — as members; 4 lighter projects) + 3
  personal tasks + ~18 seeded `unified_messages` mixing real signal
  (blockers, clarifications, two deliberate "deadlock" pairs — one person
  claims X, another claims the opposite in the same thread — a task-
  completion request, routine progress) with deterministic noise
  (no-reply sender, calendar-accept stub, List-Unsubscribe bulk mail) and
  LLM-layer noise (off-topic banter with zero business content). Then ran
  the real ingest→heartbeat→dream pipeline live end to end against the
  dev server (fast job intervals via a **temporary** `job_schedule.json`
  — deleted after — see below on why that's the right override point,
  not a hack) and inspected every stage's output. Two real prompt/filter
  bugs found and fixed from what came back:
  1. **Noise filter was prefix-only** (`app/projectkb/blocklist.py`
     `classify_message`): `local_part.startswith(p)` let real automated
     senders straight through to the LLM whenever the noise marker wasn't
     at the very start of the local part — `account-security-noreply@
     accountprotection.microsoft.com` and `azure-noreply@microsoft.com`
     (both genuine mail Shivam's real Outlook had already ingested) both
     slipped past and the flash model dutifully extracted claims from
     Microsoft security-alert/Azure-signup boilerplate. Changed to `p in
     local_part` (substring). `tests/test_poll_completion.py` +1.
  2. **Ingestion extraction prompt was too lenient on automated content
     in general** (`app/projectkb/jobs/ingestion.py`
     `_SYSTEM_INSTRUCTION`): even sender-pattern fixes can't catch every
     automated sender (e.g. Slack's own `feedback@slack.com` "X just
     joined your workspace" mail carries no noise-pattern in its
     address). Added an explicit instruction to judge by CONTENT — ignore
     templated system notices/security alerts/marketing/sign-up
     confirmations even when they survive the deterministic filter,
     with a narrow carve-out for genuinely actionable security content.
     Live-reverified after the fix: that exact Slack join-notification
     message now correctly produces zero claims.
  3. **heartbeat's conflict-vs-blocker doctrine was inconsistent**
     (`app/projectkb/jobs/heartbeat.py` `_SYSTEM_INSTRUCTION`): of the two
     planted "deadlock" pairs (contradicting claims from two people), one
     got correctly typed `conflict`, the other got typed `blocker` —
     the model was allowing the "blocked" framing on one side to
     override the fact that it's a claim-vs-claim contradiction. Added an
     explicit rule: any claim-vs-claim contradiction is ALWAYS
     `type=conflict` even when it's also blocking someone, `blocker` is
     reserved for single-sided obstacles with no contradicting claim.
     Also added `conflict` to the code-enforced `FLOOR_SEVERITY_TYPES`
     (alongside `blocker`/`clarification`) — a genuine contradiction
     should never round down to severity 0 regardless of what the model
     scores it. Live-reverified: the previously-mistyped pair now comes
     back as `conflict` after the fix, matching its sibling.
  After both fixes, live-verified the full downstream chain worked
  correctly on real (fixed) data: severity floors applied, project
  fan-out transitioned real tasks (e.g. two tasks flipped to `blocked`
  citing the conflict/blocker events as evidence, one new
  `pending_approval` task drafted), archive entries written
  deterministically, and the dream job produced a sensible rewritten
  `summary.md` (with project.md milestones the events implied were done
  now checked off), non-redundant suggestions/concerns, and a
  correctly-clamped health-score nudge with a real cited reason. Full
  `pytest tests/` still at the same 3 pre-existing failures, no
  regressions from any of the three prompt/filter changes.
  **On `job_schedule.json`:** this is NOT a workaround — it's the
  documented override layer purpose-built for exactly this
  (`app/projectkb/job_schedule.py`'s own docstring: env defaults →
  `job_schedule.json` deployment-wide override → per-manager override).
  It was used instead of `.env` only because `app/config.py` `int()`-casts
  the env vars, which can't express sub-minute intervals — `job_schedule.
  json`'s values skip that cast. It's gitignored (same as `.env`), so it
  never reaches deployment; deleted after this verification pass, restoring
  the normal 15min/60min/24h/7d cadence from `app/config.py` defaults.

- **Agent couldn't act on ad hoc "check blockers and follow up with owners" chat requests
  2026-07-23** (Shivam, live demo: asked the agent, as Sam, to look at pending tasks/blockers
  in a project and follow up with responsible people -- it flatly refused). Root cause was two
  gaps, not a hard-coded refusal: (1) no tool existed for the chat agent to discover which tasks
  are blocked/overdue -- the only task tool was `get_task(project_id, task_id)`, which needs an
  ID you already know; the actual blocked/overdue-detection logic
  (`app/agent/select.py::_select_followups`) is deterministic Python that only runs inside the
  scheduled heartbeat job, never exposed to chat. (2) `HEARTBEAT_INSTRUCTIONS`
  (`app/agent/prompts.py`), the only prompt block that explicitly authorizes proactive
  `send_message` to a task's assignee, is only appended when `candidates` is truthy -- chat calls
  (`app/agent/api.py`'s `/api/chat`) never pass any, so chat-mode Harry never saw that
  authorization at all. `send_message` itself was never gated on candidates for the actual send
  (`candidate_kind`/`candidate_ref_key` are optional, a mismatch only appends a warning string --
  see `_log_candidate_resolution`) -- so this was a capability + prompt-authorization gap, not a
  code-level block. Fixed: new `list_tasks(project_id, status?)` tool
  (`app/agent/tools.py::list_tasks_handler`, excludes `done` by default, optional status filter)
  registered alongside the other read tools; `STABLE_PROMPT` extended with an explicit paragraph
  authorizing the agent to use `list_tasks`/`get_task`/`list_team` + `send_message` for in-
  conversation owner requests like "follow up with whoever owns the blockers", independent of the
  candidate/heartbeat mechanism (still must look up real tasks/people first, never invent them).
  `tests/test_agent_tools.py` +1 (`list_tasks` excludes done, status filter). Full suite still at
  the one pre-existing unrelated failure.

## Critical bugs (prevent regressions)

1. **Gemini schema sanitizer** — never strip dict key names under `properties`; stripping `title` deletes the property definition while `required` still lists it → Gemini 400. See `app/agent/gemini_client.py`.
2. **Tool result wrapping** — Gemini's `functionResponse.response` must be a struct (dict). Any array result (e.g., `get_conflicts`) → wrap as `{"result": <value>}`. See `messages_to_gemini_contents` in gemini_client.py.

## Known gotchas

- **Scheduler state persistence:** `seed_default_jobs()` reconciles policy/interval on existing `scheduled_jobs` rows (runtime state preserved). Never rewind sim clock — scheduled jobs won't catch up. Start fresh if needed.
- **DB mutation race:** Confirm `ps aux | grep app.main` is empty before manual DB ticks (scheduler loop + manual tick can race).
- **Schema changes now sweep N manager DBs:** every manager's `db.sqlite` gets `init_manager_db`'s migration checks re-run at boot (see `app/main.py`'s lifespan) — a permanent operational cost of the per-manager split, not a one-time thing.
- **Schema changes also sweep N project DBs (fixed 2026-07-23):** the same gap existed one level over for `app/projects/db.py::init_project_db` — it only ran at project-creation time, so `Task.priority` (step 21) never reached any project created before that step, and task creation 500'd on those projects ("no column named priority") until live-tested. `app/main.py`'s lifespan now also loops every registry project (`app.controlplane.models.Project`) and calls `init_project_db` at boot, mirroring the manager-db sweep above.
- **Test fixtures auto-login:** `tests/conftest.py`'s `client` fixture always calls dev-login for a fresh throwaway manager before yielding — a test that specifically needs an *unauthenticated* client must build its own bare `TestClient(app)` (see `tests/test_auth.py::test_me_without_cookie_is_401` for the pattern), not use the shared fixture.
