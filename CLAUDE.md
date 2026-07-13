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
- No heavy installations: SQLite not Postgres, CDN frontend not build tooling.
- LLM: Gemini via `GEMINI_API_KEY`. Two tiers configured as `smart_model`
  (gemini pro class: agent reasoning, synthesis) and `flash_model` (claim
  extraction, judging) in a config file, env-overridable.

## Multi-agent workflow (IMPORTANT)

Claude (this session) writes detailed step prompts → Shivam feeds them to a
separate coding agent → that agent writes code → Claude reviews and fixes.
Consequence: **files change between turns. Always check `git status` / recent
commits / re-read files before editing or reviewing — never trust stale
context.**

- Step prompts are files: `prompts/step_XX_<name>.md` (never inline in chat).
- **KEEP THIS FILE (CLAUDE.md) UPDATED** — after every step review: tick the
  step checkbox with the date, and record any new conventions, endpoints,
  tables, gotchas, or decisions that future turns need. Claude (reviewer)
  owns CLAUDE.md updates; the coding agent must not edit it.

## Architecture (agreed 2026-07-12)

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

## Step plan (one prompt per step; update status as we go)

1. [x] Sim-time service + smart/flash model config (spec/feature_04) — done 2026-07-12
2. [x] Harry identity + outbound send path + quiet-hours queue + simulator
       render (prompt: `prompts/step_02_harry_outbound.md`) — done 2026-07-12
3. [x] KB schema (claims/truths/timeline/conflicts) + query APIs (no LLM)
       (prompt: `prompts/step_03_kb_schema.md`) — done 2026-07-13
4. [x] Gemini client + flash claim extraction (4a:
       `prompts/step_04a_gemini_extraction.md`); dream-cycle synthesis +
       contradiction probe (4b: `prompts/step_04b_dream_synthesis.md`)
       — done 2026-07-13
5. [x] Virtual scheduler: dream cycle, follow-up engine, health evaluation,
       morning brief, quiet-hours release (prompt: `prompts/step_05_scheduler.md`)
       — done 2026-07-13
6. [x] Agent harness + tools + real-time dashboard chat (Hermes indexed:
       `spec/research/hermes_index.md` — vendor its Gemini adapter, copy
       IterationBudget verbatim, follow its tool-registry pattern)
       (prompt: `prompts/step_06_agent_harness.md`) — done 2026-07-13
7. [x] Project Tracker Dashboard — Vue 3 CDN, code split across plain JS/CSS
       files, hand-built design system (must NOT look AI-generated). Split:
       7a shell/design-system/portfolio (`prompts/step_07a_dashboard_shell.md`),
       7b project detail (truth + hover citations) / conflicts / workload /
       briefs / chat dock (`prompts/step_07b_dashboard_views.md`)
       — done 2026-07-13
8. [x] Meetings: MoM paste → meeting page + action items + propagation;
       calendar; pre-meeting briefs (prompt: `prompts/step_08_meetings.md`)
       — done 2026-07-13 (MoM fix cdaf5f0)
9. [x] Workload/reassignment (leave marking) + training/newsletter
       suggestions (prompt: `prompts/step_09_workload_training.md`)
       — implemented b495173, reviewed 2026-07-13 (no bugs found)
10. [x] Seed demo scenario + end-to-end pass
        (prompt: `prompts/step_10_demo_seed.md`)
        — implemented 4663eaf, reviewed 2026-07-13 (health-eval fix)
11. [x] Autonomous follow-ups — two-tier heartbeat (flash triage → smart
        run_agent acts) + agent_notes memory + virtual-time-aware follow-up
        lifecycle (prompt: `prompts/step_11_autonomous_followups.md`)
        — reviewed + live-verified 2026-07-13

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

## Repo facts

- Run: `.venv/bin/python3 -m app.main` (port 3003; the README's
  `python3 app/main.py` form fails — fixed in Step 2). Tests:
  `.venv/bin/python3 -m pytest tests/`
- Sim clock: `app/timeservice.py`, state in `data/sim_clock.json`
  (`SIM_CLOCK_PATH` env for tests); endpoints `GET/POST /api/time[...]`.
  Wall-clock reads outside timeservice are forbidden (guard test).
- LLM config: `SMART_MODEL`/`FLASH_MODEL`/`GEMINI_API_KEY` in `app/config.py`
  (env → config.json → defaults); `config.json` is gitignored. The KEY is read
  from config.json too as of Step 6 review (`gemini_api_key`/`GEMINI_API_KEY`
  accepted) — before that fix it was env-only despite the docs. Live-verified
  models: `gemini-2.5-pro` (smart) / `gemini-2.5-flash` (flash).
- FastAPI + SQLAlchemy 2.0 + SQLite (`data/db.sqlite`); simulator is
  `app/static/index.html` (Vue 3 CDN single file).
- `unified_messages`: platform_msg_id UNIQUE (idempotency), `is_processed`
  flag = future agent queue, sender resolved at ingest via `team_members`,
  `direction` inbound/outbound (Step 2; ALTER TABLE migration in init_db).
- Harry identity: `U_HARRY` / harry.assistant@company.com, seeded
  insert-if-missing in `init_db()`; excluded from the impersonation dropdown.
- Outbound (Step 2): `POST /api/integrations/slack/send` (chat.postMessage
  shape, in-band errors HTTP 200) and `POST /api/integrations/outlook/send`
  (Graph sendMail shape, 202 empty). These are UNGATED transports; the
  quiet-hours gate is ONLY `send_or_hold()` in `app/outbound.py`
  (9:00–19:00 IST, weekends quiet; `outbound_queue` table;
  `POST /api/outbound/release`, `GET /api/outbound/queue?status=held`).
- Outlook storage conventions (ingest AND outbound must match): channel =
  plain recipient address (no prefix), subject in `subject` column, content =
  cleaned body only (no "Subject:" prefix).
- Simulator payloads MUST mirror real service shapes (Slack Events API,
  MS Graph). No processing logic in the simulator — collectors/KB only.
- KB (Step 3): `app/kb/models.py` — `entities` (slug UNIQUE like
  `project:phoenix` / `person:U_ALICE`, `compiled_truth` + `truth_updated_at`),
  `timeline_entries` (APPEND-ONLY, `source_message_id` FK = citation target),
  `attributed_claims` (holder, kind ∈ fact/status/commitment/blocker/opinion,
  weight 0-1, `superseded_by` chain + `active`), `conflicts` (claim pairs,
  open/resolved/dismissed; PATCH is the ONLY way to close — never auto).
  Router `app/kb/api.py` at `/api/kb/*`: entities list/create/page, timeline
  append+list (newest first, id-desc tiebreak), claims create/list/supersede
  (409 if already superseded), conflicts create/list/PATCH, `GET /api/kb/search?q=`
  (LIKE, grouped entities/claims/timeline, capped 20 each).
- Entity collectors are deterministic: `get_or_create_entity()` +
  `slugify()` in `app/kb/models.py` (syncs name/ref_id on change — rename-safe);
  auto-wired into `POST /api/projects` (`project:<slugified-name>`) and
  `POST /api/team` (`person:<id>`); `init_db()` backfills both.
- Citation convention: compiled truth carries inline `[T<timeline_entry_id>]`
  markers; dashboard resolves marker → timeline entry → source message.
- `POST /api/messages/reset` wipes messages/projects/tasks AND all four KB
  tables, then re-backfills person entities (incl. Harry).
- Tests: `tests/conftest.py` seeds Harry; `client` fixture's lifespan still
  runs `init_db()` against the real `data/db.sqlite` (known wart, demo-OK).
- Extraction/dream cycle (Step 4a/4b): `app/agent/gemini_client.py`
  (`GeminiClient.chat(model, messages, tools?, json_mode?)` → OpenAI-shaped
  `{content, tool_calls:[{id,name,arguments}], finish_reason, usage}`;
  `transport` injectable for tests, `get_client()` cached singleton, key
  resolved at CALL time; retries 429/5xx with real `time.sleep`).
  `app/kb/extraction.py::extract_from_message` (flash, per inbound msg; skips
  Harry-outbound/<10 chars; validates+clamps; `is_processed=True` ALWAYS in a
  `finally`). `app/kb/synthesis.py`: `run_supersession_pass` +
  `synthesize_entity` (smart; dirty-check on truth_updated_at; re-validates
  `[T#]` markers against this entity's timeline, strips unknown) +
  `run_contradiction_probe` (flash; skips pairs with an existing conflict in
  ANY status) + `run_dream_cycle` orchestrator. Endpoints: `POST /api/kb/process`
  (extraction only), `POST /api/kb/dream` (full cycle).
- Scheduler (Step 5): `app/scheduler.py` — `scheduled_jobs` (job_type UNIQUE,
  `catchup_policy` once|every), `seed_default_jobs()` called from `init_db()`
  seeds 5 crons (dream_cycle, quiet_release, followup_check, health_eval,
  morning_brief@9am/every). `tick(db)` runs everything due ≤ sim now; `every`
  policy replays each missed slot passing the virtual slot time. Driven by a
  30s asyncio loop + `timeservice.on_time_change` hook, BOTH gated off under
  pytest (`"pytest" not in sys.modules` in `app/main.py` lifespan). Manual
  `POST /api/scheduler/tick`. `app/followups.py` (Followup model, DM channel
  `DM_<sorted ids>`, ping→2nd ping→escalate-to-manager), `app/health.py`
  (score→green/yellow/red, DMs manager on degrade), `app/brief.py`
  (`briefs` table brief_date UNIQUE, smart-model prose w/ deterministic
  fallback; `GET /api/briefs`). All autonomous sends go through `send_or_hold`.
- Agent harness (Step 6): `app/agent/` — `budget.py` IterationBudget
  (limit 20), `registry.py` self-registering `ToolRegistry` (`execute` catches
  handler errors → `"ERROR: …"`, caps result 8000 chars), `tools.py` 10 tools
  (kb_search/get_entity/list_projects/list_tasks/update_task/get_conflicts/
  send_slack_dm/send_email/create_followup/get_current_time; auto-registered
  on import), `prompts.py` 3-tier system prompt (stable/context/volatile; sim
  time + open-conflict COUNT in volatile), `harness.py::run_agent`. GOTCHA
  (fixed at review): `tools.py` self-registers on import but nothing in the
  runtime chain imported it — the registry was empty in the live app and Harry
  hallucinated with no tools (tests passed only because test_agent.py imported
  it). `harness.py` now imports `app.agent.tools`; keep that import. Loop honors
  the Gemini round-trip contract: echoes the assistant `tool_calls` turn before
  results, and each `{role:"tool", tool_call_id, name, content}` carries `name`
  (Gemini keys functionResponse by NAME). `app/agent/api.py`: `POST /api/chat`
  (last 20 msgs as history), `GET/DELETE /api/chat/history`; `chat_messages`
  table (created_at sim IST).
- Dashboard (Step 7): served at `/dashboard/` via StaticFiles(html=True);
  `app/static/dashboard/` split into `js/{app,router}.js`,
  `js/views/{portfolio,project,conflicts,workload,briefs}.js`,
  `js/components/chat_dock.js`, `css/{theme,components}.css`. Hash router.
  Aggregate endpoint `GET /api/dashboard/portfolio` (red-first sort). Project
  view prefetches cited messages and renders `[T#]` chips → hover popover shows
  the source `unified_message` (THE trust moment). Chat dock → `POST /api/chat`
  with a "used N tools" trace expander.
- Workload (Step 9): `app/kb/workload.py` — `Leave`/`ReassignmentSuggestion`/
  `Digest` tables. `POST /api/workload/leaves` deterministically drafts
  reassignments (least-loaded eligible member, flash only for the rationale w/
  fallback) + DMs the manager; `approve_reassignment` reassigns + timeline +
  DMs both parties; `run_weekly_digest` (weekly_digest cron, Mon 09:30, dedup on
  week_start) flash json_mode training suggestions validated against the roster.
  Tools: `mark_leave`, `list_reassignment_suggestions`, `approve_reassignment`.
  Reviewed clean (minor: one flash call per task in create_leave; digest teaser
  hardcodes "Hi Shivam").
- Demo seed (Step 10): `app/seed_demo.py` — `run_seeding()` builds a 16-day
  Phoenix/Atlas narrative (ingest → dream cycles → organic Bob-vs-Alice
  deadlock → held 11 PM ping), `clear_database()` wipes ALL tables incl.
  scheduled_jobs and re-seeds (so the seed path avoids the clock-rewind cron
  stall). `POST /api/demo/seed {confirm:true}` + CLI `python -m app.seed_demo`.
  `check_demo_readiness()` returns the 5-item checklist. FIX at review: seeding
  now calls the deterministic `run_health_eval(db)` before the checklist — the
  seed never evaluated health, so Phoenix stayed "green" until a background tick,
  and the synchronous checklist under-reported `phoenix_degraded`. NOTE: a full
  live seed is SLOW (~10 min: per-message extraction + many pro-model dream
  cycles) — budget for it. REVIEW LESSON: a stray `app.main` server (survived a
  failed pkill) kept its 30s scheduler loop mutating `data/db.sqlite` and raced
  manual ticks — always confirm `ps aux | grep app.main` is empty before live DB
  checks.
- Autonomous follow-ups (Step 11): two-tier heartbeat. `app/agent/situation.py`
  `build_situation(db)` — deterministic collector (unmet commitments/blockers
  where holder silent >24h, open conflicts with no covering follow-up, degraded
  projects, overdue/blocked tasks, gone-quiet owners >2d, open follow-ups for
  dedup, recent notes) → `{has_signals, digest, ...}`. `app/agent/heartbeat.py`
  `triage(db,client)` short-circuits (NO llm) when `has_signals` False, else
  FLASH json_mode → `{action_needed,reason,focus}`; `run_heartbeat(db,client)`
  on yes REUSES `run_agent` (smart) with a HEARTBEAT directive to create
  follow-ups / ping both conflict holders / escalate + `record_note`.
  `app/agent/notes.py` `agent_notes` table + `record_note`/`recent_notes`
  (registered in `init_db`). New tools: `record_note`, `list_open_followups`;
  `create_followup_handler` has a dup guard (same target + (same non-null
  entity_slug OR same normalized question) → no-op). Endpoints
  `POST /api/heartbeat/run`, `GET /api/agent/notes`. Scheduler seeds `heartbeat`
  (7200s, catchup once) and flips `followup_check` to catchup "every" +
  `run_followup_check(db, now=vt)` so ping→escalate replays across time-jumps.
  Live-verified end-to-end (real key): overdue commitment → triage yes → agent
  creates+records a follow-up; 2nd heartbeat idempotent (triage says "already
  covered", acts nothing); ping fires; escalation DM over a +3d jump (unit test).
- CRITICAL cross-cutting bug fixed at Step 11 review (regressed at Step 8, broke
  ALL live tool calls): `sanitize_gemini_schema` stripped every dict key named
  `title`, deleting the `title` PROPERTY of `create_meeting` while `required`
  still listed it → Gemini 400 on the whole tools payload → Harry's chat AND
  heartbeat 500'd live (tests passed — they don't hit the network). Fixed to
  never strip property NAMES under `properties` (regression test in
  test_extraction.py). If a new tool 400s the loop, suspect this again.
- GOTCHA (Step 11 review): `seed_default_jobs` was insert-if-missing only, so
  policy/interval changes never reached the persistent `data/db.sqlite` (the
  followup_check "every" flip was inert on the demo DB — only fresh test DBs saw
  it). Now reconciles `catchup_policy`/`interval_seconds` on existing rows
  (runtime state next_due/last_run/enabled preserved). SEPARATE hazard: setting
  the sim clock BACKWARD leaves `scheduled_jobs.next_due_at` in the future, so
  crons silently stall until sim time catches up; reset does not clear
  scheduled_jobs. Demo goes forward — don't rewind, or start from a fresh DB.
- Known demo-time gaps (not blockers, flag if touched): conflicts strip in the
  project view shows severity+description but no per-claim source hover (demo
  Beat 4 narrates hovering both claims — fall back to the Claims Matrix);
  `send_or_hold` inside catch-up `morning_brief` reads the live sim time, not
  the replayed 9am slot, so a multi-day jump can hold the teaser. Live manual
  checks WERE run at Step 11 review with a real `GEMINI_API_KEY` (gitignored
  `config.json`): extraction, dream synthesis, agent tool loop, and the
  autonomous heartbeat all verified end-to-end.
