import logging
from datetime import datetime
from typing import Callable, Dict, Any
from sqlalchemy.orm import Session

from app.agent.harness import run_agent
from app.outbound import send_or_hold

logger = logging.getLogger(__name__)

# Maps unified_messages.source -> the label used in the formatted prompt below.
CHANNEL_LABELS = {"slack": "slack", "outlook": "mail", "telegram": "telegram"}


def dm_channel_id(user_a: str, user_b: str) -> str:
    """
    Mirrors app.followups.get_dm_channel_id's convention exactly. Duplicated
    here (rather than imported) to avoid a circular import: followups.py ->
    app.outbound -> app.integrations.{slack,outlook} -> (would-be) this module.
    """
    sorted_users = sorted([user_a, user_b])
    return f"DM_{sorted_users[0]}_{sorted_users[1]}"


def format_direct_message(source: str, sender: str, timestamp: datetime, message: str) -> str:
    """
    Formats an inbound message addressed directly to Harry into the fixed
    prompt shape the agent harness expects for this path. This is distinct
    from /api/chat's history-based format (that endpoint is being redefined).
    """
    channel_label = CHANNEL_LABELS.get(source, source)
    return (
        f"channel: {channel_label}\n"
        f"sender: {sender}\n"
        f"date: {timestamp.strftime('%Y-%m-%d')}\n"
        f"time: {timestamp.strftime('%H:%M:%S')}\n"
        f"message: {message}"
    )


def handle_direct_contact(
    db: Session,
    source: str,
    sender: str,
    timestamp: datetime,
    message_text: str,
    reply_channel_type: str,
    build_reply_payload: Callable[[str], dict],
) -> Dict[str, Any]:
    """
    Routes a message addressed directly to Harry straight into the agent
    harness (bypassing /api/chat) and sends Harry's reply back out through
    the same channel via the quiet-hours gate. The inbound message has
    already been persisted to unified_messages by the caller before this
    runs; this function only handles Harry's live reaction to it.
    """
    formatted = format_direct_message(source, sender, timestamp, message_text)
    result = run_agent(db, formatted, history=[])
    reply_text = result.get("reply") or ""
    if reply_text:
        send_or_hold(reply_channel_type, build_reply_payload(reply_text), db)
    return result
