import logging

logger = logging.getLogger(__name__)


def count_pending_tracked_messages(db) -> int:
    """TODO: query unified_messages for rows where sender/recipient matches
    app.projectkb.tracked.is_tracked() and is_processed=False. Stubbed at 0
    until the tracked-message filter is wired into ingestion/outlook webhooks."""
    return 0


def run(db, manager_id: str) -> dict:
    """Ingestion job: fast/flash model extracts structured Claim rows (with
    citation + timestamp) from newly arrived tracked messages.

    Not yet implemented -- this is a stub so the scheduler skeleton has
    something real to call. TODO:
      1. Fetch unprocessed unified_messages where sender/recipient is tracked
         (app.projectkb.tracked.is_tracked) and direction indicates manager
         was a party (to/from), batched (15 min or message_threshold).
      2. Deterministic cleaning per message.
      3. Flash-model extraction -> per-project Claim rows via
         app.projectkb.models (citation = unified_messages.id).
      4. Mark messages processed.
    """
    logger.info("[projectkb.ingestion] run() called -- stub, no-op")
    return {"processed": 0}
