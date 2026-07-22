# Step 22 — Ingest job: blocklist/noise selection → flash-model claims

Authority: `spec/architecture_v2_kb.md` §4.2. Implementation order item 4.
The code wins over this prompt if reality has drifted by the time this is
picked up — re-read `app/projectkb/jobs/ingestion.py`, `app/database.py`
(`Claim`/`ClaimSource`), and `app/projectkb/blocklist.py` first; all three
already exist (steps 19–20) and this step wires them together for real.

## 0. What's stale in the current stub

`app/projectkb/jobs/ingestion.py::run()` is a v1-era no-op stub whose
docstring already sketches the right shape — follow it, don't preserve it.
`count_pending_tracked_messages` references a "tracked" concept that no
longer exists post step-20 inversion; fold its logic into `run()` or drop
it, whichever reads cleaner.

## 1. Selection (deterministic, no LLM)

Per manager (the scheduler already iterates `list_provisioned_manager_ids()`
— see `app/projectkb/scheduler.py`): fetch `UnifiedMessage` rows with
`is_processed=False`, ordered by `timestamp`. For each, call
`app.projectkb.blocklist.classify_message(manager_id, msg)`:
- `"blocked"` or `"noise"` → stamp `is_processed=True`, `skip_reason=<value>`,
  commit, do NOT send to the LLM.
- `None` → survivor, goes to batching.

## 2. Per-thread batching

Group survivors by `thread_id` (spec §4.2: claims like "Alice agreed to the
new deadline" need thread context, a lone message can be ambiguous about
who it responds to). Messages with no `thread_id` (empty/None) each form
their own single-message batch. Cap total messages processed per run via
a config constant (add `INGESTION_BATCH_SIZE` to `app/config.py`, default
~30) — overflow waits for the next tick, do not process unbounded backlog
in one call. Skip the LLM entirely (return early) if there are zero
survivors this run.

## 3. Flash-model extraction

Reuse the calling convention from `app/kb/extraction.py::extract_from_message`
(`GeminiClient.chat(model=FLASH_MODEL, messages=[...], json_mode=True)`,
parse the JSON content, fall back to an empty result on a parse failure —
never raise out of a per-thread batch, log and move on to the next batch).
Per batch, prompt with: each message's sender/receiver, timestamp, subject
(if any), content, and thread_id — the model returns a list of claim
objects: `{"text": "...", "message_ids": [...]}` (message_ids = the subset
of the batch's message ids this claim cites; every claim must cite at
least one). No entity/team-roster context is needed here (that was v1's
Entity-slug design) — ingest's job is pure de-noise + claim extraction,
not attribution.

Insert one `Claim` row per returned claim (`processed=False` — heartbeat's
selection flag, distinct from `UnifiedMessage.is_processed`) plus one
`ClaimSource` row per cited message id. `content_hash` on `Claim`: hash of
the claim text + sorted message_ids, so a retried run against
already-committed claims doesn't duplicate (idempotency requirement from
spec §4, "Jobs are transactionally idempotent").

Transaction shape: mark the batch's source messages `is_processed=True`
in the SAME commit as their resulting Claim/ClaimSource rows (or roll back
both) — a message must never end up processed with no claims to show for
it after an LLM/DB failure mid-batch.

## 4. Return shape

`run(db, manager_id) -> dict` keeps its current signature; return
`{"processed": <messages marked processed>, "claims_created": <int>,
"skipped": <blocked+noise count>}` so scheduler logs stay informative.

## 5. Tests (`tests/test_ingest_job.py`, new)

Canned/mocked `GeminiClient` (do not hit the real Gemini API in tests —
follow whatever mocking pattern `tests/` already uses for the agent
harness/heartbeat tests). Cover: blocked/noise messages skip the LLM and
get skip_reason stamped; survivors batch correctly by thread_id; a batch
producing claims creates Claim+ClaimSource rows with correct FKs; a
retried run against already-processed messages is a no-op (idempotency);
batch-size cap leaves excess messages unprocessed for the next tick;
zero-pending short-circuits without an LLM call (assert the mock wasn't
invoked); a mid-batch LLM failure leaves that batch's messages
unprocessed (not partially committed).

## Done criteria

Full pytest run (only the 3 pre-existing v1 failures remain), dry-run
boot with a manually seeded manager + a few messages spanning blocked/
noise/clean/threaded cases, tick the scheduler or call `run()` directly,
inspect the resulting `claims`/`claim_sources` rows and `skip_reason`
values. Update CLAUDE.md's Architecture v2 section with a "Step 22 DONE"
entry per the established format.
