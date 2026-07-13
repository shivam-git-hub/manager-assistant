# Task: Virtual Scheduler + Follow-up Engine + Project Health + Morning Brief

## Context

Repo: `manager-assistant`. Server: `.venv/bin/python3 -m app.main`. Tests:
`.venv/bin/python3 -m pytest tests/`.

First read: `CLAUDE.md`, `app/timeservice.py`, `app/outbound.py`
(`send_or_hold`, release endpoint), `app/kb/synthesis.py` (dream cycle),
`app/kb/models.py`, `app/database.py` (Project/Task), `app/agent/gemini_client.py`.

**This prompt was written ahead of time. If any detail contradicts the
current code, THE CODE WINS — adapt.**

**Why this feature:** time becomes an actor. Crons are VIRTUAL — keyed on
sim time, so jumping the clock +3 days runs everything that came due in the
jumped interval, in order. This is the demo's time-travel centerpiece:
advance the clock and watch follow-ups fire, health degrade, and the morning
brief assemble itself.

**Project rules:** TDD; sim time only; no new deps; LLM faked in tests;
every autonomous outbound goes through `send_or_hold` (quiet-hours gate);
keep `CLAUDE.md` untouched.

## Subproblem 0 — Spec file

`spec/feature_09_scheduler.md`: job table, tick algorithm, the five builtin
jobs, follow-up lifecycle, health rubric, brief composition, test plan.

## Subproblem 1 — `scheduled_jobs` + tick (`app/scheduler.py`)

```
scheduled_jobs: id, job_type (str), payload (Text JSON, nullable),
  next_due_at (DateTime, sim IST), interval_seconds (int, nullable → one-shot),
  enabled (bool default True), last_run_at (nullable), created_at
```

- `tick(db) -> dict`: loop — fetch enabled jobs with `next_due_at <= sim now`
  ordered by `next_due_at`; run each via a `JOB_HANDLERS: dict[str, callable]`
  registry; recurring jobs: advance `next_due_at` by interval REPEATEDLY
  until it's in the future **but run the handler for each missed occurrence
  at most N=1 time per tick EXCEPT `morning_brief` which must run once per
  missed DAY** (a 3-day jump produces 3 briefs — that's the realism we want;
  a 3-day jump must NOT run the hourly dream cycle 72 times). Encode this as
  a per-job `catchup_policy: "once" | "every"` column or constant map.
  One-shot jobs: `enabled=False` after run. A handler exception marks
  `last_run_at` and continues (never wedge the tick). Returns
  `{"ran": [{job_type, count}...]}`.
- **Trigger wiring** (avoid circular imports): add to `app/timeservice.py` a
  tiny hook list — `on_time_change: list[Callable]` + `fire_time_change()`
  called at the END of the `/set` and `/advance` endpoint handlers. In
  `app/main.py` lifespan: register a callback that opens a `SessionLocal`,
  runs `scheduler.tick`, closes. ALSO start an asyncio background task
  (lifespan) ticking every 30 real seconds so live time works when nobody
  touches the clock; cancel it cleanly on shutdown.
- Builtin jobs seeded in `init_db()` (insert-if-missing by job_type):
  `dream_cycle` (hourly, catchup once), `quiet_release` (every 15 min,
  catchup once — calls the outbound release logic), `followup_check`
  (hourly, catchup once), `health_eval` (every 6h, catchup once),
  `morning_brief` (daily 09:00, catchup every).

## Subproblem 2 — Follow-up engine (`app/followups.py`)

```
followups: id, entity_slug (nullable), task_id (nullable), target_member_id,
  question (Text), created_by (str; "harry" or manager id), due_at (sim),
  status ("open"|"answered"|"escalated"|"cancelled"), ping_count (int, 0),
  last_ping_at (nullable), answer_message_id (nullable FK), created_at
```

- API: `POST /api/followups` (create), `GET /api/followups?status=open`,
  `PATCH /api/followups/{id}` (cancel / mark answered manually).
- `followup_check` handler, all deterministic:
  1. **Answer detection**: for each open followup with `ping_count >= 1`, any
     inbound message from `target_member_id` in the Harry-DM channel
     (`DM_` id containing both handles) newer than `last_ping_at` → status
     "answered", store `answer_message_id`. (LLM-quality answer judging comes
     later via the agent; keep this step deterministic.)
  2. **Pinging**: open followups with `due_at <= now` and (never pinged, or
     last ping > 24 sim-hours ago) → Harry DMs the target the question via
     `send_or_hold("slack", ...)`; increment `ping_count`, set `last_ping_at`.
  3. **Escalation**: `ping_count >= 2` and still no answer 24h after the 2nd
     ping → status "escalated" + Harry DMs the manager (first TeamMember with
     role containing "Manager"): "I've asked {name} twice about '{question}'
     with no reply — you may want to step in." (via send_or_hold).

## Subproblem 3 — Project health (`app/health.py`)

- Migrate `projects` (ALTER TABLE pattern from Step 2): `health`
  (String(10), default "green"), `health_reasons` (Text JSON), `health_updated_at`.
- `evaluate_project_health(db, project) -> tuple[str, list[str]]` —
  DETERMINISTIC rubric (no LLM): overdue open tasks (each: +2), tasks blocked
  (+2 each), open conflicts on the project entity (+3 each), no inbound
  activity on the project entity for >3 sim days (+2), escalated followups
  touching the project (+2). Score 0-2 green, 3-5 yellow, ≥6 red. Reasons =
  human strings ("2 tasks overdue", "1 open conflict: schema doc dispute").
- `health_eval` handler: evaluate all active projects; on DEGRADE (green→
  yellow, yellow→red, green→red) Harry DMs the manager with the reasons (via
  send_or_hold). Improvement: update silently.

## Subproblem 4 — Morning brief (`app/brief.py`)

`morning_brief` handler (daily 09:00; for catchup, run per missed day):
1. First call the quiet-hours release (held pings go out; collect what was
   released).
2. Assemble deterministic sections in code: released-overnight pings, open
   conflicts, projects by health (worst first) with reasons, followups
   awaiting answers, tasks due today / overdue (per assignee).
3. SMART_MODEL rewrites the assembled facts into a crisp brief (2-3 short
   sections, bullet-y, no fluff; keep every fact, add nothing). On LLM
   failure fall back to the plain deterministic text — the brief must NEVER
   fail to send.
4. Deliver: store as a `briefs` table row (id, brief_date UNIQUE, content,
   created_at) AND Slack-DM the manager a 2-line teaser via the RAW slack
   send (it's 09:00 — inside work hours by construction, no gate needed;
   still fine to use send_or_hold). `GET /api/briefs?limit=7` +
   `GET /api/briefs/{date}` for the dashboard.

## Subproblem 5 — Tests (`tests/test_scheduler.py`, write FIRST, LLM faked)

1. Tick runs a due job, skips a future one, disables a one-shot, reschedules
   a recurring into the future.
2. Catchup: set clock, create hourly job (catchup "once") + daily brief job
   (catchup "every"); advance 3 days via the API → hourly handler ran once,
   brief handler ran 3 times (assert 3 `briefs` rows with distinct dates,
   fake LLM). Verify the time-change hook fired tick automatically.
3. Handler that raises → tick continues to the next job.
4. Followups: create one due yesterday → check pings once (queue/DB has
   Harry's DM), 24h later pings again, 24h after that escalates (manager DM
   exists, status escalated). Simulate an answer (webhook message from
   target in the DM channel) → status answered.
5. Health: project with 2 overdue tasks + 1 open conflict → red/…; assert
   manager DM on degrade, silence on repeat eval (no change).
6. Morning brief content: seed a held 23:00 ping, an open conflict, an
   overdue task; run brief handler at 09:00 → held ping released, brief row
   contains all three facts (use fake LLM that echoes input facts).
7. Full suite green; wall-clock guard clean (the 30s background loop must
   use `asyncio.sleep`, which is fine).

## Definition of done

- [ ] Spec written; suite green; guard clean
- [ ] Manual check: seed a followup due tomorrow 10:00 + an 11 PM held ping;
      `POST /api/time/advance {"days":1}` → followup DM visible in
      simulator, held ping released ~09:00, morning brief row exists and
      reads well. Paste the brief in your summary.
- [ ] Commit with a clear message
