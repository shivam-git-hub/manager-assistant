"""Slack DM replies to Harry (step 28 §6B, confirmed in scope 2026-07-23).
The webhook (app/integrations/slack.py::slack_webhook) already ingests any
DM the manager's claimed bot receives -- this module is what turns that
into a live reply: run the agent harness on the single inbound message (no
dashboard chat history -- a DM to the bot is a separate conversation
surface from /api/chat) and send the reply back to the same DM channel.
"""
import logging
from typing import Any, Dict

from sqlalchemy.orm import Session

from app.agent.harness import run_agent

logger = logging.getLogger(__name__)


def handle_agent_dm(db: Session, manager_id: str, channel_id: str, message_text: str) -> Dict[str, Any]:
    """Called by the Slack webhook right after a successful ingest(), only
    for `channel_type == "im"` events whose sender isn't the bot itself.
    The reply is sent by the caller (slack_webhook) via
    `connector.send(db, channel_id, reply_text)` -- this function just runs
    the harness and returns the result so the caller can log/return it."""
    result = run_agent(db, manager_id, message_text, history=[])
    reply_text = result.get("reply") or ""
    if reply_text:
        from app.integrations.slack import connector as slack_connector

        slack_connector.send(db, channel_id, reply_text)
    return result
