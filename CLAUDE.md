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
- **Pending steps, prompts pre-written 2026-07-23** (spec §7 implementation
  order items 5-9, not yet implemented): `prompts/step_23_heartbeat_user.md`
  (claims → typed/tagged/severity events, user-level half of heartbeat),
  `prompts/step_24_heartbeat_project_fanout.md` (project-scoped fan-out:
  task status transitions, pending_approval task drafts, archive writes,
  plus a small frontend Approve/Reject addition to ProjectDashboard),
  `prompts/step_25_dream_job.md` (memory.md/events.md/dump.md per user;
  summary.md/events.md/suggestions/concerns/health rubric per managed
  project), `prompts/step_26_lint_job.md` (deterministic integrity checks
  + optional non-blocking LLM coherence pass), `prompts/
  step_27_frontend_remaining_gaps.md` (blocklist settings UI, full
  Create-Project form per wireframe 5.png, Portfolios pages 4/8.png —
  needs a design/spec check first, workload view, grab-bag not
  sequentially dependent on the others). The three remaining job stubs
  (`app/projectkb/jobs/{heartbeat,dream,lint}.py`) still describe v1-era
  concepts (timeline.md tiers, todos/conflicts) in their docstrings —
  steps 23-26 replace those docstrings along with the code, don't
  preserve the stale wording.

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
verified 2026-07-22):** Slack's step-14 design (single shared bot, bot-token
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

## Critical bugs (prevent regressions)

1. **Gemini schema sanitizer** — never strip dict key names under `properties`; stripping `title` deletes the property definition while `required` still lists it → Gemini 400. See `app/agent/gemini_client.py`.
2. **Tool result wrapping** — Gemini's `functionResponse.response` must be a struct (dict). Any array result (e.g., `get_conflicts`) → wrap as `{"result": <value>}`. See `messages_to_gemini_contents` in gemini_client.py.

## Known gotchas

- **Scheduler state persistence:** `seed_default_jobs()` reconciles policy/interval on existing `scheduled_jobs` rows (runtime state preserved). Never rewind sim clock — scheduled jobs won't catch up. Start fresh if needed.
- **DB mutation race:** Confirm `ps aux | grep app.main` is empty before manual DB ticks (scheduler loop + manual tick can race).
- **Schema changes now sweep N manager DBs:** every manager's `db.sqlite` gets `init_manager_db`'s migration checks re-run at boot (see `app/main.py`'s lifespan) — a permanent operational cost of the per-manager split, not a one-time thing.
- **Test fixtures auto-login:** `tests/conftest.py`'s `client` fixture always calls dev-login for a fresh throwaway manager before yielding — a test that specifically needs an *unauthenticated* client must build its own bare `TestClient(app)` (see `tests/test_auth.py::test_me_without_cookie_is_401` for the pattern), not use the shared fixture.
