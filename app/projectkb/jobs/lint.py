import logging

logger = logging.getLogger(__name__)


def run(db, manager_id: str) -> dict:
    """Lint job (weekly): deterministic integrity checks over the past week's
    new information -- citation links resolve, no orphaned conflict/todo
    references, staleness (files untouched in N days). Optional LLM
    coherence pass is separate from these checks, not a replacement.

    Not yet implemented -- stub for the scheduler skeleton. TODO:
      1. Per project: verify every claim.source_message_id resolves to a
         real unified_messages row.
      2. Verify conflict/todo rows reference existing claims/people.
      3. Flag files (project.md, timeline.md) not updated within an
         expected window.
    """
    logger.info("[projectkb.lint] run() called -- stub, no-op")
    return {"issues_found": 0}
