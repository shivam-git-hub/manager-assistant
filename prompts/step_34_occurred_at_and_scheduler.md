# Step 34 — `Event.occurred_at` + scheduler hardening (Phase B)

Two independent, small, high-value changes that must land before the KB jobs are
rewritten as agents (steps 35+). Read `CLAUDE.md` first.

**Testing policy for this step: implement the logic only. Do NOT write tests and
do NOT run pytest.** Tests for the whole agentic refactor are written in one
consolidated pass at the end (step 39). This deliberately overrides CLAUDE.md's
TDD rule for this multi-phase push. Your job is correct, reviewable logic.

---

## Part 1 — `Event.occurred_at`

### The problem

`Event.created_at` (`app/database.py:168`, `default=lambda: timeservice.now_ist()`)
is stamped when the **heartbeat job inserts the row**, not when the thing
actually happened. Heartbeat runs every 60 minutes and batches up to
`HEARTBEAT_CLAIM_BATCH_SIZE` (40) claims, so an event about a message sent at
09:05 can be stamped 11:00. That makes `events.md` — which is supposed to be *"a
timestamped log of what happened"* — systematically wrong, and it makes any
"what happened Tuesday" query unreliable.

### The change

Add to `Event` (`app/database.py`):

```python
occurred_at = Column(DateTime, nullable=True)  # when the underlying messages happened
```

- **Computed in code, never LLM-supplied.** Derivation: for the event's cited
  `claim_ids`, join `Claim → ClaimSource → UnifiedMessage` and take
  `max(UnifiedMessage.timestamp)`. Put this in a reusable helper —
  `app/projectkb/occurrence.py::compute_occurred_at(db, claim_ids) -> datetime | None`
  — because both the current heartbeat and the agentic one (step 35) need it.
- Falls back to `None` when there are no claims or no resolvable sources.
  `created_at` keeps its current meaning ("when we noticed") and is **not**
  removed — the gap between the two is a genuinely useful latency signal.
- Wire it into the **existing** `heartbeat.py` event-creation path (around
  `heartbeat.py:406-424`) now, so the column is populated before the agentic
  rewrite lands.

### Migration + read sites (this is the part that's easy to half-do)

- Add the `ALTER TABLE events ADD COLUMN occurred_at DATETIME` PRAGMA-guarded
  migration to `app/tenancy/db.py::init_manager_db`, following the existing
  migration pattern there exactly. Per CLAUDE.md this re-runs for **every**
  provisioned manager DB at boot — that's the intended cost.
- Existing rows get `NULL`. So **every** place that orders or filters events by
  time must become `COALESCE(occurred_at, created_at)`. Grep for
  `Event.created_at` across `app/` and fix each site — at minimum
  `app/api/home.py`, `app/agent/context.py`, `app/agent/kb_context.py`,
  `app/projectkb/jobs/dream.py`, `app/projectkb/jobs/heartbeat.py`. Use
  `sqlalchemy.func.coalesce`. Do not miss one: a half-migrated ordering silently
  interleaves old and new events wrongly.
- Expose `occurred_at` in the event-shaped API responses alongside `created_at`.

---

## Part 2 — Scheduler hardening

`app/projectkb/scheduler.py`. `heartbeat`/`dream`/`lint` **are** correctly
registered in `_JOBS` and `DEFAULT_JOB_SCHEDULE` — that part is fine. These are
the real defects:

1. **`_interval_minutes` returns `0` for an unregistered job** (line 84) — i.e.
   *always due, every 60-second tick*, behind a single `logger.warning`. One
   missing schedule entry silently burns LLM budget forever. → return a safe
   fallback interval (60 min) and `logger.error`.
2. **A corrupt/truncated `job_state.json` returns `{}`** (lines 56-58), which
   makes `_is_due` True for **all six jobs at once** (lines 88-91) — defeating
   the exact restart protection the module docstring claims to provide. → on a
   load failure, treat every job as having *just run* (stamp `now_ist()`), so a
   damaged state file costs one delayed cycle instead of a six-job stampede.
   A genuine first boot (file absent, not corrupt) must keep today's
   run-everything-once behaviour — distinguish the two cases.
3. **Non-atomic state write** (lines 61-67) — a crash mid-write truncates the
   file, feeding defect 2. → write to a temp file in the same directory then
   `os.replace()`.
4. **No in-flight guard.** Nothing stops `POST /api/dev/jobs/{name}/run`
   (`app/api/dev_tools.py:55`) or `POST /api/heartbeat/run`
   (`app/agent/api.py:156`) from running the same job on the same manager DB
   while a scheduler pass is already in it. → a per-job-name `threading.Lock`
   held for the job's global pass. The scheduler skips (and logs) a job whose
   lock is held; the two manual endpoints acquire **non-blocking** and return
   HTTP 409 `"job already running"` rather than hanging a request for minutes.
5. **`lint.run(db, manager_id)`** lacks the `client=None` third parameter every
   other job module has (`heartbeat`/`dream`/`ingestion`/`agent_heartbeat`). →
   align the signature now; step 36 gives lint a real body.
6. **`agent_heartbeat.py:10`'s docstring lies** — it claims the module is
   "registered in app.projectkb.scheduler's `_JOBS` dict", which it is not, and
   `AGENT_HEARTBEAT_INTERVAL_MINUTES` (`app/config.py:133`) is consumed by
   nothing. → fix the docstring to state it is *deliberately* manual-trigger-only
   (it sends real Slack DMs), and mark the config constant as reserved/unused in
   its comment. **Do NOT add it to `_JOBS`.**
7. **`load_job_schedule()` is called without `manager_id`** (line 81), so the
   per-manager `managers/<id>/job_schedule.json` layer that
   `job_schedule.py`'s docstring and CLAUDE.md both document has **no effect** on
   real cadence — it only surfaces on the debug page. The scheduler is
   architecturally one global pass per job, so per-manager intervals cannot fit
   without restructuring. → **fix the documentation, not the code**: update the
   `job_schedule.py` docstring and note it in `CLAUDE.md`'s scheduler section.

---

## Definition of done

- The code compiles and imports cleanly: `.venv/bin/python3 -c "import app.main"`.
  That is the ONLY verification you run — no pytest.
- Existing call sites keep working: nothing that imports `lint.run`,
  `scheduler.check_and_run_due_jobs`, or the `Event` model should need changes
  beyond what the spec names.
- No new `datetime.now()` / `.utcnow()` / `time.time()` in domain code
  (`tests/test_timeservice.py::test_07_wall_clock_guard` enforces this; the
  rate limiter's `time.monotonic()` is already an approved exception).
- Docstrings explain *why*. No narrative "changed X to Y" comments.
