import logging
from datetime import timedelta

from app.config import SLACK_POLL_INTERVAL_MINUTES
from app import timeservice

logger = logging.getLogger(__name__)


def run(db, manager_id: str) -> dict:
    """User-token DM/channel polling (redesigned 2026-07-23 -- reads the
    manager's own SlackReaderInstallation, independent of whether they've
    claimed an Agent), distinct from the bot webhook (which only sees
    conversations sent directly to a claimed bot). Mirrors outlook_poll.py's
    shape: no-ops (not an error) if this manager hasn't connected Slack
    reading yet."""
    from app.integrations.slack import connector
    from app.integrations.base import ingest

    reader = connector.resolve_reader_by_manager(manager_id)
    if reader is None or not reader.user_token:
        logger.debug(f"[projectkb.slack_poll] manager={manager_id} has no Slack user-token grant, skipping")
        return {"fetched": 0, "stored": 0, "skipped": "not_connected"}

    since = timeservice.now_ist() - timedelta(minutes=SLACK_POLL_INTERVAL_MINUTES * 2)
    raw_messages = connector.fetch_since(since, manager_id=manager_id)

    stored = 0
    for raw in raw_messages:
        _, ingest_status = ingest(connector, raw, db, manager_id)
        if ingest_status == "ok":
            stored += 1

    logger.info(f"[projectkb.slack_poll] manager={manager_id} fetched={len(raw_messages)} stored={stored}")
    return {"fetched": len(raw_messages), "stored": stored}
