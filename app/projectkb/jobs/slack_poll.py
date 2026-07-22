import logging
from datetime import timedelta

from app.config import SLACK_POLL_INTERVAL_MINUTES
from app import timeservice

logger = logging.getLogger(__name__)


def run(db, manager_id: str) -> dict:
    """User-token DM polling (step 17 piece 1) -- observes the manager's own
    pre-existing DMs, distinct from the bot webhook (which only sees
    conversations sent directly to the bot). Mirrors outlook_poll.py's
    shape: no-ops (not an error) if this manager hasn't granted a user-token
    Slack scope yet, since most managers early on will have one integration
    connected but not the other."""
    from app.integrations.slack import connector
    from app.integrations.base import ingest

    agent = connector.resolve_agent_by_manager(manager_id)
    if agent is None or not agent.user_token:
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
