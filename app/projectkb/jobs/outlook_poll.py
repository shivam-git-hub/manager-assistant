import logging
from datetime import timedelta

from app.config import OUTLOOK_POLL_INTERVAL_MINUTES
from app import timeservice

logger = logging.getLogger(__name__)


def run(db, manager_id: str) -> dict:
    """Outlook's arrival cadence -- distinct from the four KB extraction
    jobs. Graph has no simple webhook, so this polls
    for anything received since the last poll and runs it through the same
    shared ingest() a webhook would use.

    Genuinely per-manager (step 16): skips (no-op, not an error) when this
    specific manager has no OutlookInstallation -- most managers early on
    will have Slack connected but not Outlook, or vice versa, and the
    scheduler must tolerate that instead of erroring. `manager_id` is
    passed explicitly into fetch_since() so it authenticates with THIS
    manager's token, not whichever manager happened to connect most
    recently (the single-tenant fallback OutlookConnector still has for
    callers that don't know a specific manager, like the manual /poll route)."""
    from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, OutlookInstallation
    from app.integrations.outlook import connector
    from app.integrations.base import ingest

    cp_db = ControlPlaneSessionLocal()
    try:
        installation = cp_db.get(OutlookInstallation, manager_id)
    finally:
        cp_db.close()

    if installation is None:
        logger.debug(f"[projectkb.outlook_poll] manager={manager_id} has no OutlookInstallation, skipping")
        return {"fetched": 0, "stored": 0, "skipped": "not_connected"}

    since = timeservice.now_ist() - timedelta(minutes=OUTLOOK_POLL_INTERVAL_MINUTES * 2)
    raw_messages = connector.fetch_since(since, manager_id=manager_id)

    stored = 0
    for raw in raw_messages:
        _, ingest_status = ingest(connector, raw, db, manager_id)
        if ingest_status == "ok":
            stored += 1

    logger.info(f"[projectkb.outlook_poll] manager={manager_id} fetched={len(raw_messages)} stored={stored}")
    return {"fetched": len(raw_messages), "stored": stored}
