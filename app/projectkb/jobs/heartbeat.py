import logging

logger = logging.getLogger(__name__)


def run(db, manager_id: str) -> dict:
    """Heartbeat job (hourly): reads Claim rows added since last heartbeat and
    updates todos, conflicts, notes.md, and project.md (full regeneration --
    single writer, safe to overwrite). Does NOT touch timeline.md's
    current-state/history tiers -- that's dream's job.

    Not yet implemented -- stub for the scheduler skeleton. TODO:
      1. For each project, fetch active claims created since last heartbeat.
      2. Smart/flash model derives todo/conflict/notes updates.
      3. Regenerate project.md from current claims + existing todos.
    """
    logger.info("[projectkb.heartbeat] run() called -- stub, no-op")
    return {"projects_updated": 0}
