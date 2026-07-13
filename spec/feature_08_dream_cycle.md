# Feature 08: Dream-Cycle Synthesis and Contradiction Probe

This specification defines the periodic batch processing pipeline (the "Dream Cycle") that compiles entity truth summaries, performs claims supersession passes, and executes conflict/contradiction probes.

---

## 1. Motivation

Per-message claim extraction (Feature 07) operates real-time but only captures fragmented points of data. To give managers a high-level overview, Harry needs a periodic, comprehensive reasoning pass. Following gbrain, this "Dream Cycle" executes:
1.  **Supersession Pass**: Retires outdated status updates or commitments from the same person (e.g., if Alice claimed "working on design" on Monday, and "finished design" on Wednesday, the older claim is archived).
2.  **Compiled-Truth Synthesis**: Aggregates all active claims and timeline records into a readable, citation-backed prose synthesis.
3.  **Contradiction Probe**: Inspects claims across different owners to detect and flag deadlock conflicts (e.g., Bob claims "doc sent", Alice claims "doc not received") for manual resolution.

---

## 2. Supersession Pass

### API helper: `run_supersession_pass(db, entity, client) -> int`

1.  **Selection**: Gathers active claims for the specified entity with the same `holder` and the same `kind`, ordered older vs newer.
2.  **API Call (FLASH_MODEL)**: Sends pairs of claims to the Flash model (up to 10 pairs per entity) to judge if the newer claim is an update or replacement for the older one.
3.  **Prompt Instruction**: Returns `true` if and only if both statements describe the same task/status and the newer statement renders the older one obsolete.
4.  **Action**: If judged `true`, sets `older.active = False` and `older.superseded_by = newer.id` in the database.

---

## 3. Compiled-Truth Synthesis

### API helper: `synthesize_entity(db, entity, client) -> str | None`

1.  **Dirty Check**: Synthesis is skipped unless there are new timeline entries or claims created after `entity.truth_updated_at`.
2.  **Assembly**: Assembles active claims, recent timeline entries (newest first, cap 40), prior `compiled_truth`, and the current sim IST time.
3.  **API Call (SMART_MODEL)**: Instructs the Smart model to generate 1-3 concise factual paragraphs summarizing current state.
4.  **Citations**: The summary must cite timelines by embedding markers like `[T12]` at the end of factual assertions.
5.  **Sanitization**: Scans generated text with regex. Any citation referencing a non-existent timeline ID is stripped. Saves truth text and sets `truth_updated_at = timeservice.now_ist()`.

---

## 4. Contradiction Probe

### API helper: `run_contradiction_probe(db, entity, client) -> list[Conflict]`

1.  **Selection**: Pairs active claims on the same entity belonging to *different* holders (kinds ∈ `{fact, status, commitment, blocker}`). Excludes pairs with an existing open or resolved conflict. Cap 15 pairs/entity per run.
2.  **API Call (FLASH_MODEL)**: Evaluates pairs for logical contradictions.
3.  **Severity Levels**: Categorizes contradiction severity:
    *   `low`: Minor naming/wording discrepancies.
    *   `medium`: Out-of-date or stale value mismatches.
    *   `high`: Direct logical/factual deadlocks (e.g., Alice: "Task blocked by Bob", Bob: "Task is unblocked").
4.  **Action**: If `contradicts` is `true`, inserts an open `Conflict` record. Harry never auto-resolves or updates the claims himself.

---

## 5. Dream-Cycle Orchestration & Endpoints

### Endpoint: `POST /api/kb/dream`
Executes a full processing cycle:
1.  Ingests any remaining unprocessed messages (limit 50).
2.  For each active entity: runs claims supersession $\rightarrow$ rewrites compiled truth $\rightarrow$ searches for conflicts.
3.  **Response Payload**:
    ```json
    {
      "messages_processed": 5,
      "entities_synthesized": 1,
      "claims_superseded": 2,
      "conflicts_found": 1
    }
    ```

---

## 6. Test Plan

We implement `tests/test_dream_cycle.py` to validate:
1.  Determining if claim updates correctly trigger supersession links under same holder-kind categories.
2.  Synthesis dirty checks bypass execution if no new updates exist.
3.  Citations regex filtering removes bogus indices while preserving valid timeline IDs.
4.  The contradiction probe flags multi-user claim deadlocks with appropriate severity ratings, avoiding duplicates.
5.  Resolved conflicts are ignored during subsequent evaluations.
6.  The `POST /api/kb/dream` orchestrator triggers all stages in order and formats output statistics correctly.
