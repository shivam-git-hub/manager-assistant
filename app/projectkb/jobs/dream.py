import logging

logger = logging.getLogger(__name__)


def run(db, manager_id: str) -> dict:
    """Dream job (daily): reads timeline.md's current-state view (i.e. the
    day's active claims), produces key-events + summary, archives
    current-state into history, and clears current-state for the next day.
    Also reviews notes/todos/conflicts/project.md if needed.

    Not yet implemented -- stub for the scheduler skeleton. TODO:
      1. Per project: gather claims/current-state since last dream run.
      2. Smart model produces key-events + summary sections.
      3. Append archived entries to timeline.md's history tier.
      4. Clear current-state tier.
    """
    logger.info("[projectkb.dream] run() called -- stub, no-op")
    return {"projects_dreamed": 0}
