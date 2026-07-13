# Task: Autonomous Follow-ups — Harry's two-tier heartbeat

## Context

Repo: `manager-assistant`. Server: `.venv/bin/python3 -m app.main` (port 3003).
Tests: `.venv/bin/python3 -m pytest tests/`.

First read: `CLAUDE.md`, then `app/scheduler.py` (virtual cron loop,
`register_handler`, `catchup_policy`), `app/followups.py`
(`Followup` model, `run_followup_check`, `get_dm_channel_id`), `app/health.py`,
`app/kb/models.py` (claims/conflicts), `app/agent/harness.py`
(`run_agent`), `app/agent/tools.py` (tool registry pattern +
`create_followup_handler`, `send_slack_dm_handler`), `app/agent/prompts.py`,
`app/kb/synthesis.py` (`run_dream_cycle`), `app/outbound.py` (`send_or_hold`).

**This prompt was written after live review. If any detail contradicts the
current code, THE CODE WINS — adapt.**

**Why this feature (the gap):** Today `Followup` rows are created in only TWO
places — `POST /api/followups` and the `create_followup` chat tool — both
MANUAL. The scheduler autonomously *pings/escalates* existing follow-ups, but
nothing ever *decides to create one*. So Harry never autonomously chases a
missed commitment, never pings both parties on a detected conflict (the demo's
Beat 4 is currently unwired), and never nudges an owner when a project degrades.
This step makes Harry decide, on his own, what to follow up on — based on
project state, people's messages, and notes he keeps.

**Architecture (agreed with Shivam): a two-tier heartbeat.**
- **Tier 1 — triage (cheap, frequent, flash):** a lightweight cron assembles a
  deterministic "situation report" in CODE + Harry's recent notes, and asks the
  FLASH model one question: *does anything need autonomous action right now?*
  Returns `{action_needed: bool, reason, focus}`. Cheap enough to run often.
- **Tier 2 — action (smart, only on "yes"):** invoke Harry's real agent loop
  (`run_agent`, SMART model) with a directive to review the KB and take
  follow-up actions via tools (create follow-ups, nudge, escalate), then record
  a note about what he did. Deterministic collectors decide WHAT data Harry
  sees and enforce dedup/valid-targets/quiet-hours; the LLM only judges.

This mirrors the gbrain principle already in the repo (deterministic collectors,
LLM judges) and reuses the flash/smart tiering.

**Project rules:** No agent frameworks. TDD; **tests NEVER hit the network**
(fake every LLM call). Sim time ONLY via `app.timeservice` (guard test). Every
outbound goes through `send_or_hold`. Conflicts are NEVER auto-resolved. Keep
`CLAUDE.md` untouched (the reviewer updates it).

## Subproblem 0 — Spec file

`spec/feature_11_autonomous_followups.md`: the two tiers, the situation-report
collectors, the notes table, tool list, scheduler wiring, idempotency/dedup
rules, and the test plan.

## Subproblem 1 — Agent notes (`app/agent/notes.py` or extend `app/database.py`)

A small persistent memory Harry uses for continuity + dedup.

- Table `agent_notes`: `id`, `created_at` (sim IST, default via timeservice),
  `kind` (String — e.g. `followup_decision` / `escalation` / `observation`),
  `subject_ref` (String, nullable — e.g. `person:U_BOB`, `project:phoenix`,
  `conflict:3`, `followup:5`), `content` (Text).
- Helpers: `record_note(db, kind, content, subject_ref=None) -> AgentNote`
  and `recent_notes(db, limit=30) -> list[AgentNote]` (newest first).
- Register `agent_notes` in `init_db()` metadata (import the model there).

## Subproblem 2 — Situation report collector (`app/agent/situation.py`)

`build_situation(db) -> dict` — ALL deterministic, no LLM. Assemble the signals
that could warrant a follow-up, each already filtered to "actionable and not
already handled":

1. **Unmet commitments/blockers:** active `attributed_claims` with
   `kind in (commitment, blocker)` whose `holder` has sent no inbound message
   since `claimed_at` and where `now - claimed_at >` threshold (default 24 sim-h).
2. **Conflicts needing a nudge:** `conflicts` with `status="open"` that have NO
   open `Followup` referencing either claim's holder for that entity.
3. **Degraded projects:** projects with health `yellow`/`red` + reasons.
4. **Overdue / blocked tasks:** non-completed tasks past `due_date` or
   `status="blocked"`, with assignee.
5. **Gone-quiet owners:** members with ≥1 open commitment who've sent nothing
   inbound in > N sim-days (default 2).
6. **Already-open follow-ups:** every open `Followup` (target, question,
   ping_count) — so Tier 2 can DEDUP and never double-chase.
7. **Recent notes:** `recent_notes(db)` rendered compactly.

Return a dict with a `has_signals` bool (true if 1–5 produced anything) and a
human-readable `digest` string. Keep the digest compact (cap list sizes).

## Subproblem 3 — Tier 1 triage (`app/agent/heartbeat.py`)

`triage(db, client=None) -> dict`:
1. `sit = build_situation(db)`. If `not sit["has_signals"]`: return
   `{"action_needed": False, "reason": "no actionable signals"}` WITHOUT calling
   the LLM (cheap short-circuit — the common case).
2. Else FLASH call (`json_mode=True`) with the digest + notes, asking strictly:
   ```json
   {"action_needed": true, "reason": "Bob's schema-doc commitment is 2 days
    overdue and Alice is blocked; no follow-up exists yet",
    "focus": ["person:U_BOB", "conflict:3"]}
   ```
   Prompt rule: say NO if everything actionable is already covered by an open
   follow-up or a recent note. Validate/parse in code (default to
   `action_needed=False` on parse failure — fail safe, never spam).

## Subproblem 4 — Tier 2 action (`app/agent/heartbeat.py`)

`run_heartbeat(db, client=None) -> dict` (this is the cron entry point):
1. `t = triage(db, client)`. If not `action_needed` → return
   `{"triaged": True, "acted": False, "reason": t["reason"]}` (record nothing,
   or a light `observation` note — your call, keep it quiet).
2. Else invoke the SMART agent. REUSE `run_agent` (do not fork a second loop):
   build a directive user message like *"HEARTBEAT: your triage flagged action
   may be needed — {reason}. Focus: {focus}. Here is the current situation:
   {digest}. Create follow-ups for anyone who owes an update or is silent on an
   open commitment, ping both holders of any open conflict that has no
   follow-up yet, and escalate to the manager only per the usual rules. DO NOT
   duplicate anything already listed as an open follow-up. After acting, call
   record_note summarizing what you did."* Pass `history=[]`.
3. The agent acts through tools (Subproblem 5). Return
   `{"triaged": True, "acted": True, "reply": ..., "tool_trace": ...}`.

Idempotency is enforced three ways: the digest lists existing open follow-ups;
the create-followup path must reject a duplicate (same target + same open
subject) — add that guard in the handler or a helper; and Harry's notes record
what he already did. A second heartbeat over unchanged state must create nothing.

## Subproblem 5 — New agent tools (extend `app/agent/tools.py`)

Register alongside the existing ones (they auto-load because `harness.py`
imports this module):
1. `record_note(kind, content, subject_ref?)` — writes an `agent_notes` row.
2. `list_open_followups(target_member_id?)` — so the agent can self-check dedup
   mid-loop, not only from the digest.
3. (stretch) `schedule_recheck(subject_ref, remind_at)` — insert a one-shot
   `scheduled_jobs` row `heartbeat_recheck:<subject>` that just forces a triage
   later (for "look at this again in 2 days" without pinging now). Only if easy;
   `create_followup(due_at=…)` already covers most "timely" needs.

Harden `create_followup_handler` (or add a helper it calls) to NO-OP + report
"already following up" when an open follow-up with the same
`target_member_id` and same `entity_slug`/question already exists.

## Subproblem 6 — Scheduler wiring + make the lifecycle time-travel correctly

- Register `heartbeat` in `seed_default_jobs`: `interval_seconds=7200`
  (2 sim-h), `catchup_policy="once"`, `next_due_at=now`. Register handler:
  `register_handler("heartbeat", lambda db, vt=None: run_heartbeat(db))`.
- **Related fix the demo needs (do this too):** `run_followup_check` currently
  reads `timeservice.now_ist()` and its job uses `catchup_policy="once"`, so on
  a multi-day clock jump the ping→re-ping→escalate lifecycle does NOT play out
  (it evaluates only the final instant). Make `run_followup_check(db, now=None)`
  accept an explicit evaluation time, register it as
  `catchup_policy="every"` passing the virtual slot time
  (`lambda db, vt=None: run_followup_check(db, now=vt)`), and default to
  `now_ist()` when `vt` is None. Verify escalation now unfolds across a jump.
  (Leave `send_or_hold`'s real-time quiet-hours check as-is — that reads the
  actual clock by design.)

## Subproblem 7 — API (small, for the dashboard + manual demo)

In a suitable router (e.g. extend `app/followups.py` or a new
`app/agent/heartbeat_api.py`):
- `POST /api/heartbeat/run` → runs `run_heartbeat(db)`, returns its dict
  (manual demo trigger + test hook).
- `GET /api/agent/notes?limit=30` → recent notes (newest first).

## Subproblem 8 — Tests (`tests/test_heartbeat.py`, write FIRST, LLM faked)

Use a `FakeGeminiClient` (scripted flash triage + scripted smart tool calls),
seeding data via the models directly. Cover:
1. `build_situation`: seed an overdue commitment + an open conflict with no
   follow-up → digest lists both, `has_signals=True`; seed nothing actionable →
   `has_signals=False`.
2. Triage short-circuit: no signals → LLM NEVER called, `action_needed=False`.
3. Triage yes: fake flash returns `action_needed=True` → Tier 2 invoked.
4. Tier 2 happy path: fake smart scripts `create_followup` for U_BOB then a
   final note → a `Followup` row exists for U_BOB, an `agent_notes` row exists,
   trace recorded.
5. **Idempotency:** run heartbeat twice over the SAME state (fake proposes the
   same follow-up) → exactly ONE follow-up row (dup rejected), no duplicate
   pings.
6. Conflict coverage: open conflict + no follow-up → after heartbeat, follow-ups
   exist for BOTH holders (drives demo Beat 4).
7. Quiet-hours: heartbeat at 23:00 that pings → message HELD in `outbound_queue`
   (gate respected end-to-end).
8. Lifecycle over time: create a follow-up, advance sim clock across 3 days via
   the scheduler with `followup_check` catchup="every" → ping, then escalation
   DM to the manager fired (assert the escalation happened, not just one ping).
9. Full existing suite green; wall-clock guard clean.

## Definition of done

- [ ] Spec written; full suite green; guard clean
- [ ] Manual live check WITH real key (`GEMINI_API_KEY` in gitignored
      `config.json`): reset, set clock in work hours, create a project, ingest a
      Slack message where Bob commits to something with a deadline, advance the
      clock past the deadline with no reply from Bob, then `POST /api/heartbeat/run`
      → a follow-up to Bob is created and pinged (verify in the simulator +
      `GET /api/followups?status=open` + `GET /api/agent/notes`). Ingest the
      Bob-vs-Alice conflict, run a dream cycle, then heartbeat → both holders get
      follow-ups. Advance +3 days → escalation DM to the manager. Paste
      transcripts/queue state in your summary.
- [ ] Commit with a clear message
```
