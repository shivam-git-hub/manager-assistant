# Step 26 — Lint job: deterministic integrity checks, optional LLM coherence pass

Authority: `spec/architecture_v2_kb.md` §4.5. Implementation order item 8.
The lightest of the four jobs — mostly deterministic queries, no schema
changes expected. Depends on steps 22–25 existing (checks their output).

## 0. What's stale in the current stub

`app/projectkb/jobs/lint.py::run()` is a v1-era stub mentioning
`project.md`/`timeline.md` staleness in old-schema terms. Replace per
below; keep the weekly cadence comment.

## 1. Deterministic checks (per manager + per their managed projects)

- Every `Claim`'s `ClaimSource.message_id` resolves to a real
  `unified_messages` row (catches any future manual-DB-edit corruption,
  cheap to check).
- Every `Event`'s claim_ids (JSON list) resolve to real `Claim` rows.
- No orphaned `Conflict`/`Suggestion`/`Concern`/`Task` references (e.g. a
  `Task.parent_task_id` pointing at a deleted task).
- Staleness flags: a managed project whose `summary.md`/`project.md`
  hasn't been touched in N days (config constant) despite having new
  events since — surface as a `Concern` row (reuse the existing table
  rather than inventing a new one) so it shows up on the dashboard through
  the already-built insights panel.
- md-regeneration check: every `[T..]`-style citation marker (if the
  synthesis in step 25 emits any — check what step 25 actually produced
  by the time this is picked up) resolves to a live row; if step 25
  didn't end up using inline citation markers, this specific check is
  moot — verify against the real code, don't implement against this
  prompt's assumption blindly.

Each check returns a list of `{check, entity_ref, detail}` issues; log
them (this job doesn't need its own table — surfacing as Concern rows for
anything dashboard-relevant is enough, per spec "optional LLM coherence
pass is additive, never a replacement" — the deterministic checks are the
load-bearing part).

## 2. Optional LLM coherence pass

Additive only, per spec — e.g. "does summary.md still match the current
task list in spirit" as a soft check. Do not gate anything on this pass's
output; log-only or surfaced as a low-severity Concern, never a hard
failure. If time-constrained when this step is picked up, it's acceptable
to land only the deterministic checks and stub this pass clearly as
TODO — say so explicitly in the code and CLAUDE.md rather than silently
skipping it.

## 3. Tests (`tests/test_lint_job.py`, new)

Pure-function-style where possible (no LLM mock needed for the
deterministic checks). Cover: a dangling ClaimSource.message_id is
caught; a dangling Event.claim_id is caught; a dangling
Task.parent_task_id is caught; a stale project with new events since last
summary.md write produces a Concern; a healthy/clean set of data produces
zero issues; the optional LLM pass (if implemented) is provably
non-blocking (assert normal checks still run and return correctly even if
the mocked LLM call raises).

## Done criteria

Full pytest run (3 pre-existing failures only), dry-run against a
manager with a deliberately-corrupted reference (e.g. delete a Claim
that an Event still cites) to confirm the check fires. Update CLAUDE.md
with a "Step 26 DONE" entry — this is likely the last pure-backend
pipeline step, so also note in CLAUDE.md that all four jobs (ingest/
heartbeat/dream/lint) are now real, closing out spec §4 end to end.
