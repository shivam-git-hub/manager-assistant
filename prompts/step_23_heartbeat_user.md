# Step 23 — Heartbeat (user level): claims → typed, tagged, severity-scored events

Authority: `spec/architecture_v2_kb.md` §4.3 (user-level half only — project
fan-out is step 24, deliberately split so each step stays reviewable).
Implementation order item 5. Depends on step 22 (needs real `Claim` rows
to consume) — check `claims.processed` has a meaningful population before
assuming this step's tests need to seed claims manually (they will, in
unit tests, via direct DB inserts — no need to run the real ingest job).

## 0. What's stale in the current stub

`app/projectkb/jobs/heartbeat.py::run()` is a v1-era stub (mentions
`project.md` regeneration and "todos/conflicts" — that's the OLD
kb/synthesis.py design). Replace the body; the docstring's job-cadence
comment (hourly) still holds.

## 1. Selection

Per manager: fetch `Claim` rows where `processed=False`, ordered by
`created_at`. Cap per run (`HEARTBEAT_CLAIM_BATCH_SIZE` in `app/config.py`,
overflow waits for next tick — spec §4.3 "context-explosion valves").
Skip the LLM entirely if zero pending claims for this user.

## 2. Agent context

Per spec §4.3: "an agent with context (user profile, project memberships,
recent events, memory.md) and KB-inspection tools". Full tool-based
inspection is deferred (spec confirmed: "context-engineering... tackle
problems one by one... work on the agent while testing heartbeat" — the
user explicitly deferred this). For THIS step, build the minimal context
a single non-tool-calling smart-model prompt needs to do the
claims→events judgment correctly:
- the user's project memberships (`ProjectMember` rows for this
  manager_id/employee, via the registry — resolve the employee record
  for this manager first)
- the last N events for this user (recent context so routine claims don't
  re-trigger notifications — severity doctrine below needs this)
- memory.md if it exists yet (it won't until step 25/dream lands — guard
  for missing file, don't error)
- the pending claims themselves (text + which messages they cite)

Do NOT build the full tool-loop agent harness here — that's out of scope
per the user's own sequencing. A single structured-output smart-model call
(`SMART_MODEL`, `json_mode=True`, same client convention as ingest) is
correct for this step; note in the code that tool-based KB inspection is a
deliberate deferral, not an oversight, so the next person doesn't
"fix" it by accident.

## 3. Claims → events

Prompt output: a list of event objects, each:
`{"type": "status_update|blocker|clarification|commitment|request|
conflict|fyi", "project_ids": [...], "task_ids": [...], "general": bool,
"severity": 0-3, "title": "...", "body": "...", "claim_ids": [...]}"`.
project_ids/task_ids resolution: the model can't know internal ids, so
either (a) pass the user's project/task names+ids in the prompt and ask
it to reference ids directly, or (b) have it emit names and resolve to
ids in code with a fuzzy/exact match, falling back to `general=true` on
no match. Prefer (a) — simpler and avoids a matching-quality bug class.

Severity doctrine (spec §4.3, follow exactly — this is the "don't spam
the Updates panel" rule the user cares about): routine progress ("x
finished y") → low/no-notification UNLESS recent history (the last-N
events passed in as context) marks that thread as already flagged
important. Blockers/clarifications → at least severity 1 (low).
Approvals → low. Urgent-pending-training or repeated-unanswered-outreach
→ severity 3 (critical). Encode this doctrine in the system prompt
explicitly, don't leave it to the model's judgment alone.

Insert one `Event` row per returned event (`ui_state="shown"`,
`dreamed=False`); after successful insert, mark the consumed `Claim` rows
`processed=True` in the same transaction (idempotency, same shape as
step 22's message/claim commit pairing).

## 4. Return shape

`run(db, manager_id) -> dict` → `{"claims_consumed": int,
"events_created": int, "users_processed": 1}` (the scheduler already
iterates managers; this function handles one).

## 5. Tests (`tests/test_heartbeat_user.py`, new — do not confuse with the
existing, intentionally-still-failing `tests/test_heartbeat.py`, which is
v1 code slated for full replacement, not this step's file)

Canned/mocked GeminiClient. Cover: zero pending claims short-circuits
(mock not called); a batch of routine-progress claims produces low/no-
notification severity by default; a blocker claim always gets severity
≥1 even if the model tries to under-score it (add a code-level floor,
don't trust the model alone for the floor part of the doctrine); claims
consumed get `processed=True` in the same transaction as event creation;
a claim referencing an unknown project name falls back to `general=true`
rather than erroring; batch-size cap leaves excess claims for next tick;
GET /api/events after a heartbeat run reflects the new events (integration
check against the step-19 home API).

## Done criteria

Full pytest run (3 pre-existing failures only), dry-run: seed a manager +
a handful of claims spanning routine/blocker/urgent cases, call `run()`,
inspect resulting `events` rows and confirm `GET /api/events?min_severity=`
surfaces them correctly in the frontend Updates panel. Update CLAUDE.md
with a "Step 23 DONE" entry.
