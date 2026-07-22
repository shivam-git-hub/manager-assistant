"""Shared connector interface + ingestion policy for all channels.

Split of responsibilities:
  - Each connector's normalize() does the channel-specific, objective work:
    dedup-key generation, sender/receiver resolution, content cleaning.
    It does NOT decide whether the manager is involved -- it just describes
    who sent this and who it went to, in this channel's own address space.
    Returns None only if the payload isn't a real message at all (e.g.
    Slack's url_verification, a bot message).
  - ingest() (below) is the one shared policy step every source goes
    through afterwards: is the manager one of sender/receiver, is the OTHER
    party on the tracked-contacts list, is it a duplicate, then insert.
    This is fully generic -- no channel-specific logic here.

This module intentionally has no direct-chat-with-Harry routing -- that
existed before (handle_direct_contact) and has been removed; messages are
stored for later projectkb extraction only. Redesigning how Harry converses
directly is deferred until the agent redesign.
"""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Dict, Any, Protocol, Tuple
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import TeamMember, UnifiedMessage

logger = logging.getLogger(__name__)


@dataclass
class NormalizedMessage:
    platform_msg_id: str
    sender_id: str
    sender_name: Optional[str]
    receiver_id: str          # the other party's id in this source's address space
    receiver_name: Optional[str]
    channel: str              # where this lives for reply purposes (Slack channel id / email "conversation" = recipient address)
    subject: Optional[str]
    content: str
    timestamp: datetime
    raw_metadata: str
    # "dm" | "channel" | "group" (step 17 piece 3). Defaults to "dm" so
    # every existing connector (Outlook is inherently one-counterpart) needs
    # no changes. A DM has exactly one counterpart -> ingest() gates it on
    # tracked-contacts; channel/group has N participants -> gated on
    # tracked-channels instead (see ingest() below).
    conversation_type: str = "dm"


@dataclass
class SendResult:
    ok: bool
    platform_msg_id: Optional[str] = None
    error: Optional[str] = None


# Which TeamMember column holds this source's identifier for a person.
_MANAGER_ID_FIELD = {
    "slack": "slack_handle",
    "outlook": "outlook_email",
}


def get_manager(db: Session) -> Optional[TeamMember]:
    """The one TeamMember whose role marks them as the manager. Same
    convention app/health.py already uses."""
    return db.scalars(select(TeamMember).where(TeamMember.role.ilike("%manager%"))).first()


def manager_identifier(manager: TeamMember, source: str) -> Optional[str]:
    field = _MANAGER_ID_FIELD.get(source)
    return getattr(manager, field, None) if field else None


class ChannelConnector(ABC):
    source: str

    @abstractmethod
    def is_configured(self) -> bool:
        """Do we have real credentials for this channel right now? Lets the
        app boot and degrade gracefully instead of crashing when a channel
        hasn't been set up yet."""
        ...

    @abstractmethod
    def normalize(self, db: Session, raw: Dict[str, Any]) -> Optional[NormalizedMessage]:
        """raw channel payload -> common shape, or None if this payload
        isn't a real message at all (bot message, non-message event, etc).
        Does NOT check manager-involvement or the tracked list -- ingest()
        does that generically."""
        ...

    @abstractmethod
    def send(self, db: Session, to: str, content: str, subject: Optional[str] = None) -> SendResult:
        """Format (to, content, subject) into this channel's real wire
        payload and actually send it. Plain method -- no HTTP endpoint;
        callers are app.outbound (quiet-hours gated) and, later, agent
        tools."""
        ...


class PollableConnector(Protocol):
    """Only channels without push delivery implement this (Outlook)."""

    def fetch_since(self, since: datetime) -> List[Dict[str, Any]]: ...


def ingest(connector: ChannelConnector, raw: Dict[str, Any], db: Session, manager_id: str) -> Tuple[Optional[UnifiedMessage], str]:
    """Shared policy step: manager-involvement check, tracked-list gate,
    dedup, insert. `manager_id` is the control-plane Manager.id whose
    tracked-contacts list (and db.sqlite, via `db`) this message belongs to
    -- NOT the same thing as the per-source manager_addr resolved below
    (that's the manager's Slack handle / email address, used only to decide
    sender-vs-receiver). Returns (row_or_None, status) where status is one of:
      "ok"                            -- newly inserted
      "ignored_duplicate"             -- already existed
      "ignored_not_a_message"         -- normalize() returned None
      "ignored_no_manager_configured" -- no TeamMember has role="manager"
      "ignored_not_manager_message"   -- neither sender nor receiver is the manager
      "ignored_untracked"             -- the other party isn't on the tracked-contacts list
      "ignored_untracked_channel"     -- the channel/group isn't on the tracked-channels list
    """
    from app.projectkb.tracked import is_tracked
    from app.projectkb.tracked_channels import is_channel_tracked

    normalized = connector.normalize(db, raw)
    if normalized is None:
        logger.debug(f"[{connector.source}] ingest: not a real message, skipping")
        return None, "ignored_not_a_message"

    manager = get_manager(db)
    if manager is None:
        logger.warning(f"[{connector.source}] ingest: no TeamMember with role='manager' configured")
        return None, "ignored_no_manager_configured"

    if normalized.conversation_type != "dm":
        # Channel/group: no single counterpart to check against the
        # manager's own identity (everyone in the channel structurally
        # "receives" it) -- gated purely on the channel's own tracked-list
        # membership instead. See app/projectkb/tracked_channels.py.
        if not is_channel_tracked(manager_id, connector.source, normalized.channel):
            logger.debug(f"[{connector.source}] ingest: channel {normalized.channel} not on tracked-channels list, skipping")
            return None, "ignored_untracked_channel"
    else:
        manager_addr = manager_identifier(manager, connector.source)
        if manager_addr and normalized.sender_id == manager_addr:
            counterpart = normalized.receiver_id
        elif manager_addr and normalized.receiver_id == manager_addr:
            counterpart = normalized.sender_id
        else:
            logger.debug(
                f"[{connector.source}] ingest: neither sender={normalized.sender_id} nor "
                f"receiver={normalized.receiver_id} is the manager ({manager_addr}), skipping"
            )
            return None, "ignored_not_manager_message"

        if not is_tracked(manager_id, connector.source, counterpart):
            logger.debug(f"[{connector.source}] ingest: counterpart {counterpart} not on tracked list, skipping")
            return None, "ignored_untracked"

    existing = db.scalars(
        select(UnifiedMessage).where(UnifiedMessage.platform_msg_id == normalized.platform_msg_id)
    ).first()
    if existing:
        logger.debug(f"[{connector.source}] ingest: duplicate platform_msg_id={normalized.platform_msg_id}")
        return existing, "ignored_duplicate"

    msg = UnifiedMessage(
        platform_msg_id=normalized.platform_msg_id,
        source=connector.source,
        sender_raw_id=normalized.sender_id,
        sender_mapped_name=normalized.sender_name,
        receiver_raw_id=normalized.receiver_id,
        receiver_mapped_name=normalized.receiver_name,
        channel_raw_id=normalized.channel,
        subject=normalized.subject,
        content=normalized.content,
        timestamp=normalized.timestamp,
        created_at=datetime.now(),
        raw_metadata=normalized.raw_metadata,
        is_processed=False,
    )
    db.add(msg)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalars(
            select(UnifiedMessage).where(UnifiedMessage.platform_msg_id == normalized.platform_msg_id)
        ).first()
        return existing, "ignored_duplicate"

    logger.info(f"[{connector.source}] ingest: stored message {msg.id} ({msg.platform_msg_id})")
    return msg, "ok"
