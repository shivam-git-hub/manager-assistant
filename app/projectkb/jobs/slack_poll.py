import logging
from datetime import timedelta

from app.config import SLACK_POLL_INTERVAL_MINUTES
from app import timeservice

logger = logging.getLogger(__name__)


def run(db, manager_id: str) -> dict:
    """User-token DM/channel polling (reads the manager's own Slack grant
    from their Employee row, independent of whether they've claimed an
    Agent), distinct from the bot webhook (which only sees
    conversations sent directly to a claimed bot). Mirrors outlook_poll.py's
    shape: no-ops (not an error) if this manager hasn't connected Slack
    reading yet."""
    from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, get_employee_by_manager_id
    from app.integrations.slack import connector
    from app.integrations.base import ingest

    reader = connector.resolve_reader_by_manager(manager_id)
    if reader is None or not reader.slack_user_token:
        logger.debug(f"[projectkb.slack_poll] manager={manager_id} has no Slack user-token grant, skipping")
        return {"fetched": 0, "stored": 0, "skipped": "not_connected"}

    since = timeservice.now_ist() - timedelta(minutes=SLACK_POLL_INTERVAL_MINUTES * 2)
    raw_messages = connector.fetch_since(since, manager_id=manager_id)

    stored = 0
    for raw in raw_messages:
        _, ingest_status = ingest(connector, raw, db, manager_id)
        if ingest_status == "ok":
            stored += 1

    # Audit stamp -- reader (above) came back detached from its own closed
    # session, so re-fetch on a fresh session to write the timestamp.
    cp_db = ControlPlaneSessionLocal()
    try:
        employee = get_employee_by_manager_id(cp_db, manager_id)
        if employee is not None:
            employee.slack_poll_last_success_at = timeservice.now_ist()
            cp_db.commit()
    finally:
        cp_db.close()

    logger.info(f"[projectkb.slack_poll] manager={manager_id} fetched={len(raw_messages)} stored={stored}")
    return {"fetched": len(raw_messages), "stored": stored}
