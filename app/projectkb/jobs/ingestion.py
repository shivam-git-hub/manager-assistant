import logging

logger = logging.getLogger(__name__)


def count_pending_tracked_messages(db) -> int:
    """TODO: count unified_messages rows with is_processed=False (the
    step-20 store-everything world has no allowlist anymore). Stubbed at 0
    until the real ingest job (spec §4.2, step 4) lands."""
    return 0


def run(db, manager_id: str) -> dict:
    """Ingestion job: flash model extracts structured Claim rows (with
    citations) from newly arrived messages.

    Not yet implemented -- this is a stub so the scheduler skeleton has
    something real to call. TODO (spec/architecture_v2_kb.md §4.2):
      1. Fetch unprocessed unified_messages; run
         app.projectkb.blocklist.classify_message on each -- non-None means
         mark is_processed=True + skip_reason, no LLM call.
      2. Batch survivors per thread_key.
      3. Flash-model extraction -> Claim + ClaimSource rows
         (app.database), transactional with the processed-flag flip.
      4. Cap batch size per run; skip the LLM entirely on zero pending.
    """
    logger.info("[projectkb.ingestion] run() called -- stub, no-op")
    return {"processed": 0}
