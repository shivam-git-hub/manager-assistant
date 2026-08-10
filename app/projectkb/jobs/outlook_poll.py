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

    Genuinely per-manager: skips (no-op, not an error) when this specific
    manager hasn't connected an Outlook mailbox (checked via
    Employee.outlook_mailbox_email) -- most managers early on will have Slack connected but not Outlook, or
    vice versa, and the scheduler must tolerate that instead of erroring.
    `manager_id` is passed explicitly into fetch_since() so it
    authenticates with THIS manager's token, not whichever manager happened
    to connect most recently (the single-tenant fallback OutlookConnector
    still has for callers that don't know a specific manager, like the
    manual /poll route)."""
    from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, get_employee_by_manager_id
    from app.integrations.outlook import connector
    from app.integrations.base import ingest

    cp_db = ControlPlaneSessionLocal()
    try:
        employee = get_employee_by_manager_id(cp_db, manager_id)
        if employee is None or not employee.outlook_mailbox_email:
            logger.debug(f"[projectkb.outlook_poll] manager={manager_id} has no connected Outlook mailbox, skipping")
            return {"fetched": 0, "stored": 0, "skipped": "not_connected"}

        since = timeservice.now_ist() - timedelta(minutes=OUTLOOK_POLL_INTERVAL_MINUTES * 2)
        raw_messages = connector.fetch_since(since, manager_id=manager_id)

        stored = 0
        for raw in raw_messages:
            _, ingest_status = ingest(connector, raw, db, manager_id)
            if ingest_status == "ok":
                stored += 1

        # Audit stamp -- reaching here means the poll completed without
        # raising (see Employee.outlook_poll_last_success_at's docstring).
        employee.outlook_poll_last_success_at = timeservice.now_ist()
        cp_db.commit()

        logger.info(f"[projectkb.outlook_poll] manager={manager_id} fetched={len(raw_messages)} stored={stored}")
        return {"fetched": len(raw_messages), "stored": stored}
    finally:
        cp_db.close()
