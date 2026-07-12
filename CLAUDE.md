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
3. [ ] KB schema (claims/truths/timeline/conflicts) + query APIs (no LLM)
4. [ ] Gemini client + flash claim extraction (4a); dream-cycle synthesis +
       contradiction probe (4b)
5. [ ] Virtual scheduler: dream cycle, follow-up engine, health evaluation,
       morning brief, quiet-hours release
6. [ ] Agent harness + tools + real-time dashboard chat (Hermes indexed:
       `spec/research/hermes_index.md` — vendor its Gemini adapter, copy
       IterationBudget verbatim, follow its tool-registry pattern)
7. [ ] Project Tracker Dashboard — Vue 3 CDN, code split across plain JS/CSS
       files, hand-built design system (must NOT look AI-generated). Views:
       portfolio health, project detail (truth + hover citations, milestones,
       tasks), conflicts panel, workload, morning-brief inbox, Harry chat dock
8. [ ] Meetings: MoM paste → meeting page + action items + propagation;
       calendar; pre-meeting briefs
9. [ ] Workload/reassignment (leave marking) + training/newsletter suggestions
10. [ ] Seed demo scenario + demo script + end-to-end pass

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
  (env → config.json → defaults); `config.json` is gitignored.
- FastAPI + SQLAlchemy 2.0 + SQLite (`data/db.sqlite`); simulator is
  `app/static/index.html` (Vue 3 CDN single file).
- `unified_messages`: platform_msg_id UNIQUE (idempotency), `is_processed`
  flag = future agent queue, sender resolved at ingest via `team_members`.
- Simulator payloads MUST mirror real service shapes (Slack Events API,
  MS Graph). No processing logic in the simulator — collectors/KB only.
