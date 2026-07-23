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
- All datetimes: naive IST (Asia/Kolkata). In new code, wall-clock reads are
  FORBIDDEN — always use the sim-time service (see below).
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

## Simulated time (core demo trick)

Backend-authoritative anchored live clock: `(anchor_sim_time, anchor_real_time)`
stored server-side; current sim time = anchor_sim + real elapsed. Set/advance
endpoints; simulator top-bar widget drives it. ALL timestamp stamping, agent
system prompts ("Current time: …IST"), and the scheduler read it. Crons are
virtual: `scheduled_jobs` keyed on next-due sim time; advancing the clock runs
everything that came due in the jumped interval, in order. Outbound realism:
send endpoints mirror real Slack `chat.postMessage` / Graph `sendMail` shapes.


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

## Critical bugs (prevent regressions)

1. **Gemini schema sanitizer** — never strip dict key names under `properties`; stripping `title` deletes the property definition while `required` still lists it → Gemini 400. See `app/agent/gemini_client.py`.
2. **Tool result wrapping** — Gemini's `functionResponse.response` must be a struct (dict). Any array result (e.g., `get_conflicts`) → wrap as `{"result": <value>}`. See `messages_to_gemini_contents` in gemini_client.py.

## Known gotchas

- **Scheduler state persistence:** `seed_default_jobs()` reconciles policy/interval on existing `scheduled_jobs` rows (runtime state preserved). Never rewind sim clock — scheduled jobs won't catch up. Start fresh if needed.
- **DB mutation race:** Confirm `ps aux | grep app.main` is empty before manual DB ticks (scheduler loop + manual tick can race).
- **Schema changes now sweep N manager DBs:** every manager's `db.sqlite` gets `init_manager_db`'s migration checks re-run at boot (see `app/main.py`'s lifespan) — a permanent operational cost of the per-manager split, not a one-time thing.
- **Schema changes also sweep N project DBs (fixed 2026-07-23):** the same gap existed one level over for `app/projects/db.py::init_project_db` — it only ran at project-creation time, so `Task.priority` (step 21) never reached any project created before that step, and task creation 500'd on those projects ("no column named priority") until live-tested. `app/main.py`'s lifespan now also loops every registry project (`app.controlplane.models.Project`) and calls `init_project_db` at boot, mirroring the manager-db sweep above.
- **Test fixtures auto-login:** `tests/conftest.py`'s `client` fixture always calls dev-login for a fresh throwaway manager before yielding — a test that specifically needs an *unauthenticated* client must build its own bare `TestClient(app)` (see `tests/test_auth.py::test_me_without_cookie_is_401` for the pattern), not use the shared fixture.
