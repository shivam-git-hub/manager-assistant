# CLAUDE.md — Manager Assistant ("Harry" / Pulse.ai)

Manager assistant for Shivam. Harry connects to Slack + Outlook + a dashboard
chat, maintains a knowledge pipeline (`message → claim → event →
notification`) from all messages, and acts autonomously (follow-ups, deadlock
detection, status inference, manager updates). Goal: an end-to-end
**demoable** product — not scalable infra.

Product name **Pulse.ai**; every org member is a user, not just managers.

## Non-negotiable rules

- No agent frameworks (no LangChain/LangGraph). Harness written from scratch,
  Hermes-inspired (lifting their patterns/snippets is fine). **Exception:**
  the company-laptop LLM transport (`app/agent/safechain_client.py`, see
  below) goes through the company's mandated `safechain` package, which is
  itself LangChain-based — this is an external, unavoidable transport-layer
  dependency (same category as MSAL for Outlook), not a repeal of the rule.
  `app.agent.runner`'s tool-calling loop remains the one and only harness;
  nothing routes through LangChain's own agent/chain abstractions.
- All datetimes: naive IST (Asia/Kolkata), always via `app.timeservice.now_ist()`
  — direct `datetime.now()`/`.utcnow()`/`time.time()` reads are FORBIDDEN in
  new code (one call site to swap, consistent stamping across every table/job).
  `now_ist()` returns REAL wall-clock IST time.
- No heavy installations: SQLite not Postgres. Backend and the `app/static/`
  pages stay no-build. **Exception:** the manager-facing product frontend
  (`frontend/`) is React + Vite + build tooling — an explicit, scoped
  override of this rule, not a repeal of it.
- LLM: `app.agent.gemini_client.get_client()` returns one of two clients,
  chosen by `LLM_PROVIDER` (env, default `"gemini"`): Gemini directly via
  `GEMINI_API_KEY` (`GeminiClient`), or — on the company laptop, where
  direct Gemini access isn't available — `LLM_PROVIDER=safechain` routes
  every call through `app/agent/safechain_client.py::SafeChainClient`, an
  enterprise LangChain/`safechain` gateway to an internal Llama-3.3-70B
  deployment (verified: real tool-calling works against it). Both clients
  expose the identical `.chat(model, messages, tools=None, temperature=...,
  max_output_tokens=..., json_mode=False) -> {content, tool_calls,
  finish_reason, usage}` contract, so nothing downstream (runner.py, every
  agent, every projectkb job) needs to know which one is active. The
  `safechain` package itself is conda-env-only (not in requirements.txt);
  every import of it is lazy (inside functions, never at module top) so
  this repo imports and tests cleanly without it installed. Gemini has two
  model tiers, `smart_model` (agent reasoning, synthesis) and `flash_model`
  (claim extraction, judging), env-overridable in `app/config.py`; safechain
  currently has only one YAML `models:` catalog index
  (`SAFECHAIN_MODEL_INDEX`, default `"1"`), so both tiers collapse onto the
  same deployment there until a second, cheaper catalog entry exists.
  safechain's own config (`CONFIG_PATH`, `DEPLOY_ENV`, `CIBIS_*` IDaaS
  creds) is read directly by safechain's `ee_config.Config.from_env()`, not
  by this app — see `.env.example`. `app.projectkb.llm_json.parse_json_object`
  tolerates a `json_mode` response that isn't strict JSON (markdown fence,
  leading/trailing prose) via a best-effort `{...}` substring extraction —
  needed because the Llama-behind-a-gateway backend doesn't always honor
  `json_mode` as strictly as Gemini does; a still-unparseable response still
  degrades to `{}` rather than crashing the job.
- **KEEP THIS FILE UPDATED** — after every change: record new conventions,
  endpoints, tables, gotchas, decisions. Edit the relevant section in place;
  describe current state, never a dated narrative of how it got there.
- Verification bar: code review + full pytest run + dry-run boot (and for
  frontend: `npm run build` + a visual check — see the gotcha below).
- Files may change between turns (Shivam edits too): **always check
  `git status` / re-read before editing — never trust stale context.**

## Vocabulary

```
message  →  claim  →  event  →  notification
```

- **message** — raw item in the unified store (mail, Slack, manual MoM).
  Immutable once stored.
- **claim** — short structured statement extracted from message(s) by the
  ingest job (flash model). FK → source message(s).
- **event** — condensed, *judged* output of the heartbeat job. Typed
  (`status_update | blocker | clarification | commitment | request |
  conflict | fyi`), tagged (`project_ids[] / task_ids[] / general`),
  severity 0–3. FK → claims.
- **notification** — NOT a table: an event with `severity ≥ threshold`, plus
  explicit user promote/dismiss overrides. The Updates panel is a query
  over events.

**The 7 event types** (`VALID_EVENT_TYPES`, `app/agent/kb_tools.py`):

| type | means |
|---|---|
| `status_update` | progress on known work — something moved, shipped, started |
| `blocker` | work is stopped and cannot proceed until something external changes |
| `clarification` | a question was asked, or an ambiguity needs a decision, and it is still open |
| `commitment` | someone stated they will do something — a promise with an owner, usually a deadline |
| `request` | someone asked for work to be done (often becomes/links to a task) |
| `conflict` | two claims contradict each other on a matter of fact |
| `fyi` | worth recording, no action implied — the catch-all for everything the other six don't fit |

Blockers, commitments, clarifications, requests and conflicts are **typed
events**, not separate subsystems. Conflicts additionally pair the two
contradicting claims in the project's `conflicts` table via
`record_conflict` (`app/agent/kb_tools.py`) and are **never auto-resolved**.
Before step 35 nothing in the codebase ever wrote that table — it was read by
the health rubric and the project-detail API and was permanently empty. Now
that it is populated, project health scores legitimately drop where conflicts
exist (the rubric is −20 per open conflict, capped at 3).

`general` is **not** an 8th type — it is a derived boolean on a different
axis (*scope*: is this tied to any known project?), computed as
`general = not project_ids` and never model-supplied. A `blocker` can be
general-scoped just as easily as a `status_update` can.

Two storage rules: **SQLite is the source of truth for anything structured;
markdown is the synthesis/audit layer and is always regenerable from the db**
(a lost md file is a rebuild, never data loss), and **every md file has
exactly one writer job** — no concurrent writers.

**Write authority:** only the *manager's* pipeline synthesizes project truth.
Teammate↔teammate DMs contribute nothing. Only conversations involving the
manager feed the project KB. Known blind spot, accepted.

## Data layout

- `data/controlplane.sqlite` — the one genuinely global DB (`app/controlplane/`).
- `managers/<manager_id>/db.sqlite` + `blocklist.json` + per-manager md
  files (`memory.md`, `events.md`) — one per manager, structurally isolated
  (not a shared DB with a manager_id column). There used to be a third,
  `dump.md`; step 36 removed it (`manager_dump_md_path` is gone) because
  nothing ever read it — dream was its only writer. Existing files on disk
  are left alone, just no longer written.
  `app/tenancy/` owns provisioning (`paths.py`, `db.py::init_manager_db` /
  `get_manager_db` dependency).
- `projects/<project_id>/db.sqlite` + `project.md`/`notes.md`/`summary.md`/
  `events.md` + `vault/` — one per project (team or personal `kind`), shared
  by members; only the owning manager's pipeline writes project truth.
  `app/projects/` owns this. Projects are global rather than nested under a
  manager so teammates who are members (not owners) can read them, and so
  manager handover is a registry pointer change.

**Control-plane tables** (`app/controlplane/models.py`):
- `Employee` — org directory AND the single identity/credential table.
  **Step 30 removed the separate `Manager` table**: `Employee.id` IS the
  auth identity now, so every session/project-ownership/agent-claim FK that
  used to point at `managers.id` points at `employees.id` directly —
  `manager_id` elsewhere in this codebase means "an `Employee.id`",  not a
  separate table. `AuthSession` — cookie sessions, keyed by employee id.
  `is_manager` is set `True` at login (`POST /api/auth/dev-login` or the
  Outlook login path) and never auto-unset; a row can exist with no
  login/connector activity at all (a teammate who's never logged in).
  Seeded by the admin manually inserting rows into this table — no seed
  script. `get_employee_by_manager_id(db, manager_id)` is a plain PK lookup
  (`db.get(Employee, manager_id)`) kept as a named helper for readability —
  prefer it over `db.get(Employee, ...)` inline. Credential columns:
  `outlook_mailbox_email`, `outlook_token_cache_json` (serialized MSAL
  cache), `outlook_granted_scopes` (incremental consent — `Mail.Read` via
  the Connectors-page `GET /auth/outlook/connect-mail`, `Mail.Send` opt-in
  via `GET /auth/outlook/enable-send`; login itself requests only
  `User.Read` and writes no credential); `slack_team_id`,
  `slack_team_name`, `slack_user_token` (a manager's own Slack user-token
  grant for reading their own messages, written by `GET /auth/slack/install`
  — independent of any bot; `slack_id` doubles as the reader's own Slack
  user id once connected).
- `Agent` — the bot-identity pool (Atlas/Nova/Kettle etc.), pre-created +
  installed to Slack by the admin out of band (a manual row insert into
  this table — no seed script, see app/integrations/SLACK.md), then claimed
  by a manager (`POST /api/agents/claim`, atomic
  `UPDATE ... WHERE manager_id IS NULL`) and released
  (`POST /api/agents/release`). `manager_id` unique+nullable (an
  `Employee.id`). `user_token`/`user_id` on this table are vestigial —
  nothing writes them. `slack_app_token` (added alongside Socket Mode
  ingress) is a DIFFERENT credential from `bot_token` — Socket Mode's
  `xapp-...` app-level token, set the same manual-insert way, null on any
  deployment using the webhook path instead — see Connectors section.
- `Project` (registry) — global projects registry (name, description,
  `kind` team|personal, `manager_user_id`, `supervisors` JSON list of
  employee ids). `member_employee_ids` is a JSON list of
  `{employee_id, role}` dicts directly on the row — not a join table
  (step 31 collapsed the old `ProjectMember` table into this column, since
  every read is "give me this project's members," never "which projects is
  employee X on" at a scale needing an indexed join). Use
  `get_member_list(project)`/`set_member_list(project, members)` from
  `app.controlplane.models` rather than touching the column directly.
  `Portfolio` — a manager's own named grouping of projects (a real entity,
  not a saved filter); `project_ids` is likewise a JSON list of project ids
  directly on the row (step 31 collapsed the old `PortfolioProject` join
  table). Use `get_portfolio_project_ids`/`set_portfolio_project_ids`.
  `DELETE /api/projects/{id}` (`app/api/project_detail.py::delete_project`)
  explicitly strips the deleted id out of every portfolio's `project_ids`
  — nothing cascades automatically now that it's a JSON column, so any new
  project-deleting code path must do the same or ids go stale.

**Per-manager tables** (`app/database.py`): `TeamMember` — a manually
maintained team-view roster (`GET`/`POST /api/team`), no longer read by
connectors/ingestion at all (sender/receiver/manager resolution matches the
control-plane `Employee` directory directly, see Connectors section below).
Kept in sync one-way, Employee → TeamMember, via
`app.tenancy.team_sync.sync_team_member_from_employee()`, called whenever
someone is added as a project member (`POST /api/projects` at creation,
`PATCH /api/projects/{id}`'s add-path, and the agent's
`dashboard_action: add_project_member` tool) — keyed by `employee.id` now,
not the historical "Slack UserID or Email" convention, since nothing
resolves messages against `TeamMember.id` anymore. `UnifiedMessage` (raw immutable ledger),
`Claim`/`ClaimSource`, `Event`, `Todo`, `Meeting` (agent-inferred, see Agent
section), `AgentActionLog` (idempotency ledger), `AgentAssignment` (mirrors
a control-plane `Agent` claim), `ChatMessage`. `Claim.heartbeat_attempts`
(step 35) — incremented for every claim in a heartbeat batch on every run;
a claim reaching 3 attempts without ever being cited by `emit_events` is
force-marked `processed=True`, the starvation guard for the agentic
heartbeat (see below) — a claim the model keeps declining to use must not
be re-offered forever. `Event.occurred_at` (step 34)
is when the underlying messages actually happened — `max(UnifiedMessage.
timestamp)` across the event's cited `claim_ids`, via `Claim → ClaimSource →
UnifiedMessage`, computed by `app/projectkb/occurrence.py::
compute_occurred_at()` and wired into `app/agent/kb_tools.py::
emit_events_handler` (step 35's agentic heartbeat, replacing the old
inline call in `heartbeat.py`) — never LLM-supplied. `Event.created_at`
keeps its original meaning ("when the
heartbeat job noticed and inserted the row"); the gap between the two is a
latency signal. `occurred_at` is `NULL` for events created before this
column existed or whose claims have no resolvable source message, so
**every time-ordering/filtering read of `Event` must
`func.coalesce(Event.occurred_at, Event.created_at)`**, never bare
`Event.created_at` — a bare read silently interleaves old and new events
wrongly. Also `Project`/`Task`
(int-id, single-manager-db — distinct from the registry `Project` and the
per-project-db `Task`), kept only because `app/api/dashboard.py`'s
`/api/messages/reset` still touches them — don't build new features against
these, use the registry/per-project versions.

**Per-project tables** (`app/projects/`): `Task` (string id, `priority`,
subtask nesting, `schedule_state` computed vs. current time), `ArchiveEntry`,
`Conflict`, `Suggestion`, `Concern`, `HealthLog`.

## Connectors

Abstract base: `app/integrations/base.py::ChannelConnector` — three
methods every connector implements: `is_configured()`, `normalize(db, raw,
manager_id=None) -> NormalizedMessage | None`, `send(db, to, content,
subject=None)`. Two connectors: `app/integrations/slack.py::SlackConnector`,
`app/integrations/outlook.py::OutlookConnector`. Shared policy lives outside
both, in `base.py::ingest()`: normalize → dedup (by `platform_msg_id`) →
store. Stores everything — filtering happens later at ingest-job time via
`app.projectkb.blocklist.classify_message` (marks `skip_reason`, never drops),
so editing the blocklist never loses history.

**Credentials** live on the `Employee` row (control-plane), never in a
per-manager DB.

**Slack ingress has two paths.** The Events API webhook
(`POST /api/integrations/slack/webhook`) is the default — Slack pushes
events to a public URL. `app/integrations/slack_socket.py` is the
alternative for a deployment with no reachable public webhook URL (the
company-laptop case: a corp-network laptop can't accept inbound HTTPS, but
CAN hold an outbound websocket): one `slack_bolt` `SocketModeHandler` per
claimed `Agent` that has both `bot_token` and an app-level token
(`Agent.slack_app_token`, a DIFFERENT credential from `bot_token` — Socket
Mode's `xapp-...` grant, set the same manual-DB-insert way; falls back to
the `SLACK_APP_TOKEN`/`SLACK_BOT_TOKEN` env vars for a single-agent demo
when that column is still null). Both paths converge on the same routing
function, `app.integrations.slack::handle_agent_event(agent, event)`
(manager DM → `cos_agent.run_cos_agent`, known-teammate DM → followup
reply, else → normal `ingest()`) — the webhook route is now a thin wrapper
around it, so the two ingress mechanisms can never drift in behavior.
Started/stopped from `app.main`'s lifespan (`slack_socket.start_all()`/
`stop_all()`); loudly logs (not silently no-ops) when slack_bolt isn't
installed, no agent is claimed, or an agent has no resolvable app token.
`requirements.txt` includes `slack_bolt`/`slack_sdk` (public PyPI packages,
unlike `safechain`) for this.

**Corp-network egress**: `app.config.AEXP_PROXY_URL`/`SLACK_INSECURE_SSL`
(both no-ops when unset) are threaded through every Slack HTTP call
(`SlackConnector._api_call`) and the Socket Mode `WebClient`/
`SocketModeHandler`. `_api_call` also logs at ERROR (not DEBUG) whenever
Slack returns `ok: false` — Slack reports an auth/scope failure
(`invalid_auth`, `missing_scope`, ...) as HTTP 200 with `ok: false`, which
`raise_for_status()` never catches, so this was previously invisible
("Slack unauthorized" with no logged cause).

**Sender/receiver/manager resolution** matches the control-plane `Employee`
directory directly — `SlackConnector._resolve_member`/
`OutlookConnector._resolve_member` open their own `ControlPlaneSessionLocal`
and look up `Employee.slack_id`/`Employee.email` (always lowercase); the
per-manager `TeamMember` table is never read in this path. Slack's DM
counterpart fast-path (`_resolve_dm_other_participant`) compares the sender
against the calling manager's own `Employee.slack_id` (kept current at
Slack-connect time, `app/controlplane/slack_auth.py`) rather than the old
`get_manager()`/`TeamMember.role` lookup (removed).

**Slack `thread_key`**: a DM channel (`conversation_type == "dm"`) is always
keyed by its `channel_id` alone — every message in that DM, threaded or
not, batches into the same ingestion thread. This matters because Slack
only sets `thread_ts` on an explicit "reply in thread"; a normal flat
back-and-forth never does, so keying on `thread_ts or ts` would split one
continuous DM into one single-message "thread" per message.

Channel/group messages: an explicit reply-in-thread (`thread_ts` present)
always keys `f"{channel_id}:{thread_ts}"`, grouping under its parent
regardless of elapsed time. A top-level message (no `thread_ts` — Slack
gives no conversation signal for these at all) is bucketed by time
proximity instead (`SlackConnector._resolve_channel_session_thread_key`):
if the channel's own most recently stored message landed within
`SLACK_CHANNEL_SESSION_GAP_MINUTES` (default 30, `app/config.py`), this
message reuses that message's `thread_id`; otherwise it roots a fresh
session at its own ts. A live back-and-forth in a channel batches into one
LLM call the same way a DM does; a topic separated by a real gap starts its
own thread instead of chaining onto whatever the channel's last message
happened to be about.

**Refresh:** Outlook — `load_token_cache_for_manager`/
`save_token_cache_for_manager` (outlook.py) persist an MSAL
`SerializableTokenCache` blob into `Employee.outlook_token_cache_json`;
`_acquire_token()` calls `acquire_token_silent(...)`, MSAL handles the
refresh-token exchange, the cache is re-saved after. Slack — no refresh
needed, tokens are long-lived until revoked.

**Adding a new connector:** implement `ChannelConnector`, add a
`app/projectkb/jobs/<name>_poll.py::run(db, manager_id)` matching the
existing shape (no-op if not connected, `fetch_since` + `ingest()` loop), a
new `JobName` enum value, and one line each in `scheduler.py`'s `_JOBS` dict
and `job_schedule.py`'s `DEFAULT_JOB_SCHEDULE`. The scheduler loop itself is
generic over `_JOBS` — this is registration, not a rewrite.

**Polling** (`app/projectkb/jobs/outlook_poll.py`/`slack_poll.py`): no-op
(not an error) if the manager hasn't connected that channel; `since = now -
2×interval` (deliberate overlap, dedup handles it); Outlook does one Graph
`GET /me/messages?$filter=...` call; Slack does `conversations.list` (every
type the reader token can see) then `conversations.history` per conversation,
skipping per-channel on missing scope rather than aborting.

## Scheduler & jobs

`app/projectkb/scheduler.py::background_loop()` — one real wall-clock loop
(`PROJECTKB_POLL_SECONDS`, default 60s tick resolution). Each tick, every
job in `_JOBS` (`ingestion`, `heartbeat`, `dream`, `lint`, `outlook_poll`,
`slack_poll` — **`agent_heartbeat` is intentionally NOT in this list**, see
below) is checked against its OWN `interval_minutes`
(`job_schedule.py::load_job_schedule()`); if due, that job runs ONCE,
globally, fanning out sequentially over every provisioned manager
(`list_provisioned_managers`, each getting their own `db.sqlite` session) in
that single pass before the next job is checked. This is a single global
job per type, not a scheduled task per manager. A manager's job invocation
failing is caught and logged; it doesn't stop the rest of that pass or the
other jobs this tick (step 32). The whole due-jobs pass runs via
`asyncio.to_thread` (not called directly in the event loop) since the jobs
themselves are synchronous (plain SQLAlchemy sessions, plain Gemini HTTP
calls) — calling them inline would block the single event loop, and with
every process for every incoming HTTP request, for however long a due
heartbeat/dream pass over every manager takes. Safe because every engine in
this codebase (`controlplane`/per-manager/per-project) is created with
`check_same_thread=False`.

**Cadence gating is real, but only the deployment-wide layer** —
`job_schedule.py`'s `load_job_schedule()` is env-default → `job_schedule.json`
(deployment-wide) → per-manager `managers/<id>/job_schedule.json`, deep-merged
in that order. The scheduler's `_interval_minutes()` (step 34) calls
`load_job_schedule()` with **no `manager_id`**, so only the deployment-wide
layer is the real, effective cadence for every manager — this genuinely IS
what the scheduler runs against, not just what `app/api/dev_tools.py`'s debug
page reports. The **per-manager** layer, however, is a no-op on real
cadence: it's only ever read by `GET /api/dev/jobs` (which does pass
`manager_id`), so a manager's own `job_schedule.json` changes what the debug
page *shows* without changing when their jobs actually run. The scheduler is
architecturally one global pass per job across every manager; per-manager
intervals would need restructuring into per-manager scheduled tasks to take
effect, which is out of scope today — don't wire `manager_id` into the
scheduler's call without that restructuring conversation happening first. A
job name missing from the schedule falls back to a safe 60-minute interval
with a logged error, rather than the old "returns 0 → always due every tick"
behavior. Last-run state (`scheduler._last_run_at`) is persisted to
`JOB_STATE_PATH` (`data/job_state.json` by default, env-overridable via
`JOB_STATE_FILENAME` for test isolation) via a temp-file-then-`os.replace()`
atomic write, so a process restart doesn't make every job — including dream
(24h) and lint (7d) — look immediately due again and burn LLM budget; a
`JOB_STATE_PATH` that exists but fails to parse (as an atomic-write crash can
never produce, but external tampering could) is treated as "every known job
just ran" (one delayed cycle) rather than "no state at all" (which would
stampede every job across every manager on the same tick) — a genuine first
boot (file absent) still correctly makes every job due immediately.

**In-flight guard**: each job name (plus `agent_heartbeat`, which isn't in
`_JOBS` but is still manually triggerable) gets its own process-wide
`threading.Lock`, held for that job's entire global pass
(`scheduler.get_job_lock`). The scheduler's own tick skips (and logs) a job
whose lock is already held rather than blocking; `POST
/api/dev/jobs/{name}/run` and `POST /api/heartbeat/run` acquire the same
lock **non-blocking** and return HTTP 409 `"job already running"` instead of
hanging the request — this is what stops a manual trigger from running the
same job concurrently with an in-progress scheduler pass for the same job
name.

**Configured cadences** (real, not just intent): outlook_poll 5m,
slack_poll 5m, ingestion 15m, heartbeat 60m, dream 1440m (24h), lint 10080m
(7d).

**Poll audit columns**: `Employee.outlook_poll_last_success_at` /
`slack_poll_last_success_at` are stamped by `outlook_poll.py`/`slack_poll.py`
at the end of a run that completed without raising for that employee — NOT
bumped when the connector isn't configured (nothing ran). A timestamp
stale relative to the job's own `interval_minutes` is the audit signal that
polling is failing for that employee.

- **ingestion** (`jobs/ingestion.py`): classify_message (user blocklist
  only — the sole skip_reason for now, no LLM) → group survivors by thread
  → chunk each thread by `INGESTION_BATCH_SIZE` (message count) and
  `INGESTION_MAX_CHARS_PER_CALL` (combined content size), never merging
  across threads → one flash-model call per chunk (the model's own prompt
  also ignores automated/system content, no skip_reason recorded for that)
  → `Claim`+`ClaimSource` rows, chunk marked processed in the same commit
  (crash-safe). All chunks from a run's survivors are processed in that
  same run, however many LLM calls it takes, up to a safety ceiling of
  `INGESTION_MAX_CALLS_PER_RUN` — only calls beyond that wait for next tick.
- **heartbeat** (`jobs/heartbeat.py`, step 35 — **agentic**, no longer a
  single blind structured-output call): phase 1 runs one tool-using agent
  (`app.agent.runner.run_spec`) per manager over that tick's unprocessed
  claim batch, with the six read-only KB probes
  (`search_events`/`get_event`/`search_claims`/`get_thread`/
  `get_project_state`/`list_projects`, `app/agent/kb_tools.py`) plus
  `emit_events`/`record_conflict` as its only write tools — the agent is
  instructed to probe for an existing near-duplicate before writing, then
  call `emit_events` once with its judged batch. Every deterministic
  guardrail (`type`/severity-floor/`project_ids`/`claim_ids` validation,
  `general` derived not model-supplied, `occurred_at` computed) now lives in
  `emit_events_handler` itself, which also marks every cited claim
  `processed=True` in the same commit. `heartbeat.py` only handles claim
  *disposal* after the run: `stop_reason=="final"` mass-marks every
  remaining uncited batch claim processed (the agent looked and chose not
  to use them); `budget`/`deadline`/`tool_cap`/`error`/`empty_response`
  leave uncited claims alone for next tick's retry; see
  `Claim.heartbeat_attempts` above for the starvation guard that bounds
  those retries. Phase 2 (project fan-out) groups the run's non-general
  events by project via `project_scope.manager_owned_projects_with_events`
  (owned only) and runs one agent per affected project with the same six
  read probes plus the project-write tools `apply_task_transitions`
  (must cite a real `event_id` from *this* run — enforced via
  `run_context["known_event_ids"]`), `draft_tasks` (forced
  `pending_approval`), `link_request_to_task` — all four project-phase
  write tools (`record_conflict` too) enforce project **ownership**, not
  membership, via `kb_tools._require_owned_project`. `tasks_updated`/
  `tasks_drafted` are computed from a before/after diff of the project's
  `Task` table, not parsed out of the tool-call trace. Archive writes stay
  deterministic and non-LLM (one `ArchiveEntry` per event, written by the
  job itself, only for owned-project fan-out — a general event or an event
  tagged only to a project the manager merely belongs to gets no archive
  entry, same as before this rewrite). `build_kb_context(db, manager_id,
  include_conventions=True)` is built once per tick and reused for phase 1
  and every phase-2 project run (it's manager-scoped, not project-scoped).
  Zero LLM calls when there are no pending claims. Run-cost fields
  (`llm_calls`/`tokens_in`/`tokens_out`) are summed across phase 1 + every
  phase-2 run; `stop_reason` reported is phase 1's.
- **dream** (`jobs/dream.py`): **agentic** (step 36). Selects by the
  `Event.dreamed` flag, not a wall-clock cursor (job cadence and event
  timestamps are different clocks; comparing them would miscount). Two agent
  phases like heartbeat: per-user (`write_memory` full rewrite +
  `append_manager_events`) then per-**owned**-project (`write_project_summary`,
  `append_project_events`, `add_suggestions`/`add_concerns`,
  `set_health_adjustment`). `memory.md`'s remit is deliberately broad — the
  person's projects and role in each, key events/decisions, commitments made
  or owed, stated preferences, and anything inferable that would be costly to
  forget; rewritten whole each run, merged and deduped, never appended.
  Health stays deterministic: `app/projectkb/health_rubric.py::
  compute_base_health_score()` is a 0–100 rubric computed **by the tool
  handler**, and the model may only supply a code-clamped ±1 nudge plus a
  required reason (blank reason ⇒ nudge forced to 0), so "why is this project
  red?" always has a citable answer. `write_memory`/`write_project_summary`
  reject blank content — a bad generation must never blank a durable file.
  Events are marked `dreamed=True` even when a project run failed (else a
  permanently-failing project re-sends its events every 24h forever), but
  **not** when the user synthesis failed. Zero LLM calls when nothing is
  un-dreamed.
- **lint** (`jobs/lint.py`): deterministic integrity checks **first**, with
  zero LLM calls — `ClaimSource`→`UnifiedMessage` and `Event.claim_ids`→
  `Claim` resolution, orphaned `Task.parent_task_id`/`Conflict` claim refs/
  `Event.task_ids` in owned project dbs, staleness (project md files untouched
  while events kept arriving, claims stuck unprocessed, events stuck
  un-dreamed), and poll-health (`Employee.outlook_poll_last_success_at`/
  `slack_poll_last_success_at` stale relative to that job's own interval — per
  the Connectors section, that staleness IS the signal polling is failing
  silently). Findings persist as `AgentNote(kind="lint_finding")` — reusing
  the existing table already exposed at `GET /api/agent/notes`, no new table.
  An LLM coherence pass (flash model, capped) runs **only when the
  deterministic pass found something**, and may report but never fix.
- **agent_heartbeat** (`jobs/agent_heartbeat.py`): the personal agent acting
  on what the above jobs already produced (pre-meeting briefs, follow-ups on
  blocked/overdue tasks, conflict contact/escalate). **Deliberately excluded
  from automatic scheduling** — only reachable via manual trigger
  (`POST /api/heartbeat/run`, the debug page's "Run now" button).
  Deterministic candidate selection (`app/agent/select.py::build_candidates()`,
  no LLM); idempotency via `AgentActionLog` (unique on action_type+ref_key),
  written by the tool handler after a real action, never trusted to the model.

## Agents

There are **six** agents, all driven by the same `run_spec` loop, separated
by their `AgentSpec.tool_names` allowlist — which is the only thing that
bounds what each one can do, so read the spec to know the blast radius.
(Previously documented as five — the Chief of Staff agent below was added
undocumented in an earlier step; backfilled here since Socket Mode ingress
now routes through it too, see Connectors section.)

| Agent | Trigger | Writes |
|---|---|---|
| `kb_heartbeat` (+ `kb_heartbeat_fanout`) | scheduled, 60m | events, conflicts, task transitions/drafts |
| `kb_dream` (+ per-project) | scheduled, 24h | memory.md, summary.md, events.md, suggestions/concerns, health nudge |
| `kb_lint` coherence pass | scheduled, 7d | `AgentNote(kind="lint_finding")` only |
| Knowledge Synthesis | **manual**, `POST /api/kb/ask` | nothing, unless `allow_writes=true` |
| **Chief of Staff** (`cos_agent.py`) | `POST /api/chat` (portal), Slack DM from the manager, scheduled `CronJob` ticks | chat replies, `user.md`/`memory.md`, spawns Followup Chat Agents, crons/workflows |
| personal agent ("Harry", `harness.py`/`tools.py`) | manual heartbeat only (`agent_heartbeat` job) | dashboard mutations, `send_message` |

**The personal-agent table row above is narrower than it used to be.** The
Chief of Staff agent (below) now owns both entry points this row used to
own — `POST /api/chat` and Slack DM replies (`app.agent.api.py` still
imports `harness.run_agent` but never calls it; `app.agent.direct_contact.
handle_agent_dm` has no call sites left anywhere in the codebase, i.e. dead
code, not currently wired to anything). `harness.run_agent`/`tools.py`'s 9
tools are exercised today only via the manual-trigger `agent_heartbeat` job
(pre-meeting briefs, task follow-ups, conflict escalation, deterministic
candidate selection via `app/agent/select.py`) — still real, just narrower
than "the chat/Slack-DM agent" the original docs described it as.

### Chief of Staff agent (`app/agent/cos_agent.py`)

The manager's always-on assistant — distinct from the KB jobs (which
synthesize project truth) and from the personal agent above (which only
acts on `agent_heartbeat`-selected candidates). `run_cos_agent(db,
manager_id, user_message, channel)` compiles a dedicated system prompt
(`compile_cos_system_prompt`: `user.md` + `memory.md` + owned/member
projects + active `Workflow`/`CronJob`/`FollowupAgent` rows), replays
compacted chat history (`get_compacted_history` — beyond 20 `ChatMessage`
rows, the oldest are flash-summarized into `managers/<id>/chat_summary.json`
and deleted, keeping only the summary + last 5 verbatim), and runs
`run_spec` with its own tool allowlist (`COS_TOOL_NAMES`): the six read-only
KB probes (same `kb_tools.py` handlers `kb_heartbeat` uses),
`trigger_knowledge_synthesis_agent`, `web_search` (Brave Search, no-ops
without `BRAVE_SEARCH_API_KEY`), `read_memory`/`update_memory` (writes
`user.md`/`memory.md` directly — this agent has WRITE access to its own
memory files, unlike its read-only KB access), `schedule_one_time_reminder`/
`create_workflow`/`list_cron_jobs`/`delete_cron_job`, and
`spawn_followup_chat_agent`/`list_active_followups`. `channel` is
`"portal"` (`POST /api/chat`), `"slack"` (manager DM), or `"cron"` (a
scheduled tick, see below) — `"slack"` and `"cron"` both additionally
deliver the reply to the manager's Slack DM (`Employee.slack_id`) since a
cron-triggered reply otherwise has no visible surface at all.

**Followup delegation** (`spawn_followup_chat_agent` → a `FollowupAgent`
row, `status: active|reported|completed|failed`): the Chief of Staff never
messages a teammate directly (command directive #1 in its own prompt) — it
delegates to a lightweight, separate chat loop instead. Spawning drafts a
greeting (flash model) and sends it via Slack (falls back to email); a
reply from that teammate (routed by `handle_agent_event`/the webhook, see
Connectors section, keyed on `FollowupAgent.recipient_employee_id` +
`status="active"`) is handled by `handle_team_member_reply`, a small
scoped chat loop instructed to append `[REPORT_COS: ...]` to its own reply
once it has gathered a real update or learned of a blocker. That tag is
stripped before the teammate sees it, sets `FollowupAgent.status="reported"`
+ `cos_context`, writes `AgentActionLog(action_type="followup_reported")`,
and proactively DMs the manager. Both `send()` call sites (spawn + reply)
check `SendResult.ok` explicitly — `SlackConnector.send()`/
`OutlookConnector.send()` do NOT raise on a delivery failure
(missing_scope/invalid_auth/...), only return `ok=False`, so treating "the
call didn't throw" as delivery success (the pre-fix behavior) silently
marked failed followups as sent. `AgentActionLog(action_type=
"followup_initiated")` is written on a successful spawn (Agents-tab
visibility — "followup initiated with X").

**Crons** (`CronJob`, `Workflow`): `check_and_run_manager_crons()`
(`cos_agent.py`) sweeps every provisioned manager for due `CronJob` rows
and runs `run_cos_agent(..., channel="cron")` — called unconditionally on
**every** scheduler tick (`app.projectkb.scheduler.check_and_run_due_jobs`,
step "1. Run manager-scoped cron jobs/reminders"), not gated by
`job_schedule.py`'s per-job interval system the way `_JOBS` is. A
`CronJob.schedule` is `"Nm"`/`"Nh"` (interval) or `"MM HH * * *"` (daily) —
parsed by `calculate_next_run`. `POST /api/dev/seed-demo-cron`
(`dev_tools.py`) creates one recurring `CronJob` directly (demo
convenience — this is the "agent invokes periodically and updates the
manager" tick without needing to ask the agent to schedule itself in chat
first); the admin.html "Agents & Cron" tab has a "Seed Demo Cron" button
for it. `Workflow` rows are a `create_workflow`-tool-created higher-level
grouping (name + `task_type` + `cron_expression` + free-form `config`) —
`check_and_run_manager_crons` itself only reads/executes `CronJob` rows,
not `Workflow` rows directly (a `Workflow` is currently prompt-context and
manual bookkeeping, not itself scheduled).

**`AgentActionLog` writes** (Agents-tab visibility, `GET /api/agent/actions`,
rendered by `frontend/src/pages/Agents.tsx`): `followup_initiated` (spawn),
`followup_reported` (teammate reported back), and `kb_updated` (written by
`app/projectkb/jobs/heartbeat.py` itself, "Knowledge base updated with N
event(s)" — the heartbeat job did not write to this ledger before; only the
personal agent's tool handlers did).

**Knowledge Synthesis agent** (`app/agent/synthesis.py::run_synthesis(db,
manager_id, question, *, allow_writes=True)`, exposed at `POST /api/kb/ask`
with `{question, allow_writes}`) — the ad-hoc counterpart to the three
scheduled KB agents: query the KB in natural language, **and update it**.
Writes are ON by default; ad-hoc correction ("the heartbeat mis-typed that
event, fix it") is half the point of this agent, not an exceptional mode.
`allow_writes=False` gives a provably read-only run — the write tools are
then absent from the allowlist entirely, so it's enforced by the harness,
not by the prompt. It is **never** granted `send_message` under any flag
(asserted at import time and again at call time) — reasoning about the KB
and contacting people are deliberately different surfaces, which is the
whole reason this isn't just the chat agent.

### Tenant scoping — the invariant every agent relies on

A job or agent running for manager A must only ever read/write A's KB. Four
mechanisms, in order of how much they carry:

1. **`manager_id` is never a model-supplied argument.** No tool schema
   exposes it; `ToolRegistry.execute` injects it from the run's own
   `manager_id`. The model cannot target another manager's data even if it
   tries to — this is the load-bearing one.
2. **Per-manager DB is a separate file.** The `db` session handed to a job is
   `managers/<id>/db.sqlite`, so `Event`/`Claim`/`UnifiedMessage`/
   `ChatMessage`/`AgentNote` are scoped structurally, not by a `WHERE`.
3. **Manager md files** (`memory.md`, `events.md`) are addressed via
   `manager_*_md_path(manager_id)` using that injected id.
4. **Projects live in one global `projects/` tree**, so they are the only
   surface needing an explicit gate, and there are two:
   `kb_tools._require_visible_project` (owned ∪ member — **read** scope, used
   by `get_project_state` and by `tools.py`'s `get_project_doc`/`list_tasks`/
   `get_task`) and `kb_tools._require_owned_project` (owned only — **write**
   scope, used by every project write tool, per the write-authority rule
   above). `emit_events` filters tagged `project_ids` to the visible set.
   Job fan-out uses `project_scope.manager_owned_projects_with_events`.
   Both gates also stop a hallucinated project id from `mkdir`-ing a stray
   `projects/<id>/` — `get_project_session` creates unconditionally.

### Personal agent (`app/agent/`)

A 5th pipeline stage that ACTS (messaging, dashboard
mutations, escalation) rather than re-deriving blockers/conflicts itself.
Tools (`tools.py`, every handler takes `db, manager_id, run_context, **args`):
`send_message(channel: slack|portal, target, text, candidate_kind?,
candidate_ref_key?)`, `dashboard_action(action: create_task|
update_task_status|add_project_member|create_todo|create_meeting, ...)`,
read-only probes `list_meetings`/`list_tasks`/`get_task`/`get_project_doc`/
`list_team`/`list_open_conflicts`, `todo` (session-scoped worklist).

Meetings are **agent-inferred** from recent claim text (no calendar
ingestion exists) — the one deliberately LLM-judged step in an otherwise
deterministic-selection design.

Two entry points through the same harness (`app/agent/context.py::
build_agent_context()`): `POST /api/chat` (dashboard) and Slack DM replies to
a claimed bot (`app/agent/direct_contact.py::handle_agent_dm()`, guarded on
`channel_type=="im"` + no `bot_id`, loop-prevention). Session memory = last 20
`ChatMessage` rows replayed as history; working memory = projects'
summary.md/events.md tail + memory.md + today's events, rebuilt fresh per call.

**Agentic runner foundation (step 33, Phase A)** — `app/agent/harness.py::
run_agent` is now a thin wrapper (exact signature/return shape preserved)
over the generic loop in `app/agent/runner.py::run_spec(spec, db,
manager_id, seed_message, ...)`, driven by an `AgentSpec` (name, model,
instructions, an explicit `tool_names` allowlist, `max_llm_calls`,
`max_tool_calls`, `deadline_seconds`, temperature) and returning an
`AgentRunResult` (reply, tool_trace, llm_calls/tool_calls,
tokens_in/tokens_out, `stop_reason` — `final|budget|deadline|tool_cap|
empty_response|error`). The wrapper passes `compile_system_prompt(...)` in
as `context_text` so the chat agent's prompt is unchanged; `context_text`
defaults to the separate `app/agent/kb_context.py::build_kb_context` when
omitted, the bounded (ids/names/counts, never full project prose) context
builder for job agents — wired into `heartbeat.py`, `dream.py`, `lint.py`
and the synthesis agent. Five
independent stop conditions including a repeat-call guard (3rd+ identical
`(name, args)` call returns the cached result instead of re-executing) and
a per-run token-usage accumulation from every `chat()` call's `usage`.
`ToolRegistry.execute` takes an optional `allowed_names` allowlist
(`None` = unrestricted, existing call sites unaffected) and compacts every
result through `app/agent/result_compaction.py::compact_tool_result`
(structure-aware: drops list items from the end / shrinks a dict's largest
string fields / wraps anything else as `{"_raw":...,"_truncated":true}`) —
replaces the old blind `str[:8000]+"...[truncated]"` clamp, which could hand
the model invalid JSON. Per-tool cap is `ToolRegistry.register`'s
`max_result_chars` (default `AGENT_DEFAULT_TOOL_RESULT_CHARS`).
`app/agent/rate_limit.py::get_rate_limiter()` is a process-wide sliding-window
limiter (`LLM_MAX_CALLS_PER_MINUTE`, `0` = disabled — tests set this) wired
into `GeminiClient.chat` immediately before the HTTP attempt loop; retry
backoff honors a `Retry-After` header when present and adds ±25% jitter
otherwise. `GeminiClient.chat` also now rejects `json_mode=True` combined
with `tools` outright (Gemini 400s that combination). KB-agent budget knobs
(`KB_HEARTBEAT_MAX_LLM_CALLS`, `KB_HEARTBEAT_PROJECT_MAX_LLM_CALLS`,
`KB_AGENT_DEADLINE_SECONDS`) are defined in `app/config.py` and consumed by
`heartbeat.py` (step 35); `KB_DREAM_MAX_LLM_CALLS`,
`KB_DREAM_PROJECT_MAX_LLM_CALLS`, `KB_LINT_MAX_LLM_CALLS`,
`KB_SYNTHESIS_MAX_LLM_CALLS` are defined for the still-pending dream/lint
rewrites — nothing reads those four yet.

**KB tools (step 35, `app/agent/kb_tools.py`)** — registered on the same
shared `registry` as `tools.py`'s chat tools (import side-effect, same
pattern), driven today only by `heartbeat.py`'s two `AgentSpec`s. Six
read-only probes — `search_events`/`get_event`/`search_claims`/
`get_thread`/`get_project_state`/`list_projects` — let a job agent check
"is there already an open blocker for this" before writing, ordered on
`func.coalesce(Event.occurred_at, Event.created_at)` like every other
Event time-read site. Five write tools: `emit_events` (the only way to
create `Event` rows — batches every deterministic guardrail the pre-step-35
heartbeat job used to own inline: type/title/severity-floor/`project_ids`/
`claim_ids` validation, `general` derived, `occurred_at` computed, and
marks every cited claim `processed=True` in the same commit — `claim_ids`
on each item are restricted to `run_context["offered_claim_ids"]` when the
caller populates it, else falls back to "must resolve to a real `Claim`
row"), `record_conflict` (writes the per-project `conflicts` table — the
first thing in this codebase that does; `severity` is `low|medium|high`,
a **different scale** from `Event.severity`'s 0-3, never conflate the
two), `apply_task_transitions` (each transition must cite a real
`event_id`, restricted to `run_context["known_event_ids"]` when the caller
populates it — "cite an event from THIS run"), `draft_tasks` (forced
`status="pending_approval"`), `link_request_to_task` (accumulates onto
`Event.task_ids`, never overwrites). `record_conflict` and the three
project-write tools all enforce project **ownership** via
`kb_tools._require_owned_project` — a manager who merely belongs to a
project (not the owner) can have `emit_events` tag an event to it, but
cannot write task transitions/drafts/links/conflicts for it.

## Frontend (`frontend/`)

React 18 + TS + Tailwind + react-router-dom, Vite dev server on :5173
proxying `/api`/`/auth` to backend `:3003` (no CORS config needed, cookie
auth works). Conventions: runtime knobs in `frontend/src/constants.ts`
(severity palette, nav tabs, panel limits); structural colors in
`tailwind.config.js`; Segoe UI stack; 4-petal `StatusFlower` SVG is the
status glyph everywhere; unbuilt nav items render as plain text, never dead
links; no-data states are honest (grey flowers, empty-state copy) — NEVER
dummy data. Never invent logos/icons — reusable assets live in `/assets`
(repo root), copy what's needed into `frontend/src/assets/`; ask Shivam for
anything else.

Separate from the no-build pages in `app/static/`: the auth test-flow pages
(`login.html`/`connect.html`) and the debug page (`debug.html`).

Pages built: Login, Home, Projects grid + drill-down (Overview +
Dashboard), Tasks (personal-projects grid), Connectors, Agents, Portfolios,
a floating draggable chat widget (`ChatWidget.tsx`, all pages).

## Time

`app.timeservice.now_ist()` returns REAL wall-clock IST — always, no
simulated/settable clock anywhere in this codebase (step 32 removed the
old inert `set_time()`/`advance()`/`reset_to_real()` mutators,
`/api/time/set|advance|reset`, and `data/sim_clock.json` entirely; `GET
/api/time` remains as a read-only informational endpoint for the debug
page). Tests that need deterministic time-dependent logic (quiet hours,
follow-up lifecycles, meeting-brief windows) monkeypatch
`app.timeservice.now_ist` directly per-test with pytest's own `monkeypatch`
fixture — e.g. `monkeypatch.setattr(timeservice, "now_ist", lambda:
some_fixed_dt)` — there is no shared test-time-control fixture/abstraction
anymore (the old `set_sim_time` fixture was removed as part of the same
cleanup, for being confusable with the dead sim-clock system despite being
unrelated to it).

## Essential facts

**Run & test:**
- `.venv/bin/python3 -m app.main` (port 3003)
- `.venv/bin/python3 -m pytest tests/` — 371 collected; 9 known-failing on a
  clean tree, not 0: 4 are the documented live-`GEMINI_API_KEY`-required
  judge tests (`test_kb_pipeline_judge.py`, see Pending work), 5 are
  pre-existing/unrelated to any of the areas this file's steps touch
  (`test_heartbeat_user.py::test_13_memory_md_is_included_in_the_prompt`,
  4x `test_kb_context.py` — a stale `ContextBudget`/`_project_task_counts_
  and_health` API mismatch between the test file and `app/agent/
  kb_context.py`, not yet reconciled). Verified via `git stash` that all 9
  fail identically on the unmodified tree. `test_heartbeat_project_fanout.py`
  passes in isolation but can ERROR when run in the same session as the slow
  live-Gemini judge suite — a test-isolation issue in that suite, not a real
  failure (passes standalone: `pytest tests/test_heartbeat_project_fanout.py`).

**Stack:** FastAPI + SQLAlchemy 2.0 + SQLite.

**Login/connect:** dev-login (`POST /api/auth/dev-login`, gated by
`DEV_AUTH_ENABLED`, default on) or Outlook sign-in (`GET /auth/outlook/login`,
identity only — `User.Read`, no mailbox access); both call
`ensure_manager_scaffold` + `ensure_employee_for_manager` on first login.
Login never implies any connector grant — mailbox/Slack access are both
separate, explicit Connectors-page actions: `GET /auth/outlook/connect-mail`
(Mail.Read) and `GET /auth/slack/install` (Slack reading), both require
login first. Real (Outlook) login rejects any email not already in the
seeded Employee directory; dev-login stays permissive — the intended path
on a company laptop where Outlook OAuth can't complete (no reachable Azure
redirect off the corp network). Both `app/static/login.html` (no-build
test page) and `frontend/src/pages/Login.tsx` (the real product page) have
a dev-login form (email + optional name) below the Outlook button; both
`POST /api/auth/dev-login` then land on Connectors
(`frontend/src/lib/api.ts::devLogin`, full page navigation not
client-side `navigate()` — `App.tsx` only fetches `getMe()` once on mount).
`app/static/connect.html`, driven by `GET /api/auth/connections`.

**LLM:** see the Non-negotiable rules section above (`LLM_PROVIDER`
gemini/safechain switch) — `smart_model`/`flash_model` (Gemini:
gemini-3.5-flash / gemini-3.5-flash-lite) only meaningfully separate tiers
when `LLM_PROVIDER=gemini`.

## Critical bugs (prevent regressions)

1. **Gemini schema sanitizer** — never strip dict key names under
   `properties`; stripping `title` deletes the property definition while
   `required` still lists it → Gemini 400. See `app/agent/gemini_client.py`.
2. **Tool result wrapping** — Gemini's `functionResponse.response` must be a
   struct (dict). Any array result → wrap as `{"result": <value>}`. See
   `messages_to_gemini_contents` in gemini_client.py.

## Known gotchas

- **DB mutation race:** Confirm `ps aux | grep app.main` is empty before
  manual DB ticks (scheduler loop + manual tick can race).
- **Schema changes sweep N manager DBs AND N project DBs at boot:**
  `app/main.py`'s lifespan re-runs `init_manager_db`'s migration checks for
  every provisioned manager, and `init_project_db`'s for every registry
  project — a permanent operational cost of the per-manager/per-project
  split, not one-time. A project/manager db created before a schema change
  won't have the new column until the app reboots once.
- **Test fixtures auto-login:** `tests/conftest.py`'s `client` fixture always
  dev-logs-in a fresh throwaway manager before yielding — a test that needs
  an *unauthenticated* client must build its own bare `TestClient(app)`
  (see `tests/test_auth.py::test_me_without_cookie_is_401`).
- **Headless-Chrome screenshot verification hangs** on a fresh
  `--user-data-dir` — don't use it for frontend screenshot checks. Use a
  temp page in `app/static/` that fetches dev-login then redirects to
  `:5173`, verified by curl/direct endpoint hits rather than a real headless
  screenshot when the risk is endpoint shapes, not visual layout.
- **Slack scopes require re-consent on change** — widening `USER_SCOPES` or
  a bot's Bot Token Scopes doesn't retroactively apply to already-issued
  tokens; every affected manager must disconnect + reconnect (reader) or
  the admin must reinstall the pool app to the workspace (bot), which
  issues a new token that must be manually re-written into that agent's row.
  Every conversation type in `conversations.list` needs its own `:read`
  scope, not just `:history`, or the call fails with `missing_scope` and
  polling silently returns nothing.
- **Three different things are named `Project`/`Task`** — `app/database.py`'s
  legacy int-id pair, the control-plane registry `Project`, and the
  per-project-db `Task`. Don't confuse them when reading `app/api/dashboard.py`.
- **App loggers are invisible without `logging.basicConfig`** — every
  `logging.getLogger(__name__)` call in this codebase sits at WARNING with
  no handler until something configures the root logger, so INFO logs
  (which is most of what these jobs/connectors log) silently vanish even
  though the code runs fine. `app/main.py` now calls
  `logging.basicConfig(..., force=True)` at module scope, and
  `uvicorn.run(..., log_config=None)` so uvicorn's own dictConfig doesn't
  re-clobber it. `LOG_LEVEL` env var overrides the level (default INFO).
  If logs go quiet again after some future change, check both of those
  first before assuming a job silently stopped running.
- **Slack `ok: false` is an HTTP 200`** — `_api_call`'s `raise_for_status()`
  never fires on an application-layer Slack rejection (`invalid_auth`,
  `missing_scope`, `channel_not_found`, ...); it logs those at ERROR now
  (see Connectors section) specifically because this was previously the
  invisible cause behind a "Slack unauthorized" symptom with nothing in the
  logs to explain it.
- **`SendResult.ok` must be checked, not just "the call didn't raise"** —
  `SlackConnector.send()`/`OutlookConnector.send()` return
  `SendResult(ok=False, error=...)` on a delivery failure rather than
  raising; code that wraps a `send()` call in `try/except` and treats
  "no exception" as success (the pre-fix bug in `cos_agent.py`, see Agents
  section) will silently mark a failed delivery as sent.

## Pending work

- **Frontend gaps** — blocklist settings UI, full Create-Project form,
  workload view. Nothing surfaces `summary.md` (dream writes it, no read
  endpoint exposes it), lint findings (`GET /api/agent/notes` exists but no
  UI reads it), or `POST /api/kb/ask`.
- **safechain PII redaction** — `safechain.core.models.redaction`/
  `safechain.utils.redaction` exist in the installed package but were
  deliberately not investigated or wired in (explicitly out of scope for
  the initial company-laptop port) — unknown whether safechain already
  redacts PII at the transport layer, or whether that's needed on top for
  the Amex AI firewall/DLP rejections referenced in `safechain_client.py`'s
  `_dump_payload`/`_log_payload` diagnostics. If firewall rejections show
  up in practice, `LLM_LOG_PAYLOAD=true` dumps the exact outgoing payload
  to logs as the first diagnostic step.
- **`Workflow` rows are not scheduled** — only `CronJob` rows are read by
  `check_and_run_manager_crons()`; `create_workflow`'s tool creates a
  `Workflow` row but nothing turns it into an executing `CronJob`
  automatically (see Agents section's Chief-of-Staff subsection).
- **`SafeChainClient.chat` has never executed against the real gateway** —
  `bind_tools`'s round-trip was confirmed working via a standalone script
  during setup (real `tool_calls` come back), but `_to_langchain_messages`'
  `ToolMessage` branch (mapping a tool RESULT back into the conversation,
  keyed on `tool_call_id`) was written from scratch, since the pasted
  reference code for that exact piece was corrupted/inconsistent, and has
  not been exercised against a real multi-turn tool-calling loop. This is
  the single most likely first breakage on the company laptop — check it
  before anything else if `run_spec` misbehaves under `LLM_PROVIDER=safechain`.
- **Socket Mode is unverified against a live corp-network websocket** —
  `AEXP_PROXY_URL` is threaded through `WebClient`/`SocketModeHandler`
  construction per the working reference pattern shared during setup, but
  whether Slack's Socket Mode websocket itself traverses that proxy
  correctly is a real-environment thing that could only be confirmed on
  the actual company laptop, not from this session.
- **Meeting action-items have no home** — no table stores them.
- **Ingestion noise filtering** — `classify_message` is user-blocklist-only,
  so automated mail (calendar accept/decline, Dependabot, invoices, list
  digests) reaches the LLM and gets claims extracted from it. Four
  failing `tests/test_kb_pipeline_judge.py` accuracy tests (classification,
  ingestion claims, heartbeat events, dream synthesis) measure exactly
  this gap; they need a live `GEMINI_API_KEY` to run.
- **`agent_heartbeat` is still manual-only** and its interval config
  (`AGENT_HEARTBEAT_INTERVAL_MINUTES`) is consumed by nothing. Scheduling it
  means auto-sending Slack DMs to real people — a different risk class from
  the KB-writing jobs — so it needs an explicit decision, not a default.
