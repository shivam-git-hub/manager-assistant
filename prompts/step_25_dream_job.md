# Step 25 — Dream job: md synthesis, suggestions/concerns, health rubric

Authority: `spec/architecture_v2_kb.md` §4.4. Implementation order item 7.
Depends on steps 23–24 (needs a day's worth of real events/task/archive
activity to synthesize — tests will seed this directly, no need for a
real multi-day sim run).

## 0. What's stale in the current stub

`app/projectkb/jobs/dream.py::run()` is a v1-era stub referencing
`timeline.md`'s "current-state/history tiers" — that's the OLD
kb/synthesis.py model. Replace per the per-user/per-project md files this
spec actually defines (`app.projects.paths::ensure_project_scaffold`
already writes the template files this step regenerates — reuse it,
don't reinvent the md schema).

## 1. Per-user synthesis

Per manager, gather the day's `Event` rows (since last dream run — track
a `last_dream_at` cursor, e.g. in `job_state.json` per
`app/tenancy/paths.py`'s existing per-manager state file, or a new
scheduler-tracked cursor if that's cleaner given what `job_schedule.py`
already does — check that file before adding a new mechanism). Update:
- `memory.md` — durable facts about the user worth remembering across
  days (Hermes-style long-term memory, spec's explicit inspiration).
  Append/merge, do not blindly overwrite (memory is supposed to
  accumulate) — but avoid unbounded growth: dedupe near-identical facts,
  the LLM call should return a merged version each time, not a append-only
  diff.
- `events.md` — a timestamped log of the day's key events (this one IS a
  straightforward append).
- `dump.md` — literal useful reference dump (contacts, feature
  descriptions, etc.) the agent may RAG over later. Append-style like
  events.md.

## 2. Per-managed-project synthesis

Per project where this manager is the project's manager AND had activity
since last dream: regenerate `summary.md` (full rewrite — spec explicitly
calls this single-writer/safe-to-overwrite, matching `Task`/archive state
+ recent events, NOT the whole history); append key events to project
`events.md`; generate **suggestions** and **concerns** (insert `Suggestion`/
`Concern` rows in `app.projects.models`, `status="open"`).

## 3. Health rubric (spec §4.4 — must be auditable, not vibes)

Deterministic base score in code from: count of open blockers (events
type=blocker, unresolved), count of overdue tasks (`schedule_state ==
"overdue"` per the step-21 computation), count of open `Conflict` rows,
days since the project's last progress event, milestone slippage vs
whatever `project.md` records (best-effort text signal, don't over-engineer
parsing it). Combine into a base score/band via a simple documented
formula (write it in a code comment, this is the "why is this project
red" auditability requirement — the formula itself must be legible, not
just its output).

The LLM may adjust the base score by at most ±1 band, MUST supply a
written `reason` if it does. Insert into `HealthLog`
(`rubric_inputs`=JSON of the counts above, `base_score`, `llm_adjustment`,
`reason`, `final_score`). Never skip inserting a HealthLog row even when
`llm_adjustment=0` — the audit trail needs every tick, not just adjusted
ones.

## 4. Tests (`tests/test_dream_job.py`, new)

Mocked GeminiClient. Cover: memory.md merge doesn't duplicate near-
identical facts across two runs; events.md/dump.md append correctly;
summary.md full-overwrite reflects current task/event state; suggestions/
concerns rows created with status=open; health rubric base score is
correct for a hand-constructed set of blockers/overdue-tasks/conflicts
(pure function, test without the LLM at all); LLM adjustment is clamped
to ±1 even if the mock tries to return ±3; a llm_adjustment=0 tick still
inserts a HealthLog row; dream for a project the manager doesn't manage
never fires.

## Done criteria

Full pytest run (3 pre-existing failures only), dry-run: seed a manager +
managed project with a day of activity (blockers, an overdue task, a
conflict), run dream, read the regenerated md files + inspect
suggestions/concerns/health_log rows, confirm the ProjectOverview page
(step 21) picks up the new summary.md content and ProjectDashboard's
insights panels (already built, currently always-empty) now show real
data. Update CLAUDE.md with a "Step 25 DONE" entry.
