"""Shared connector interface + ingestion policy for all channels.

Split of responsibilities:
  - Each connector's normalize() does the channel-specific, objective work:
    dedup-key generation, sender/receiver resolution, content cleaning.
    It does NOT decide whether the manager is involved -- it just describes
    who sent this and who it went to, in this channel's own address space.
    Returns None only if the payload isn't a real message at all (e.g.
    Slack's url_verification, a bot message).
  - ingest() (below) is the one shared policy step every source goes
    through afterwards: dedup, then insert. Step 20 inverted the old
    allowlist philosophy (spec/architecture_v2_kb.md §4.2): everything the
    pollers fetch is stored (the user's own mailbox/DMs/channels belong to
    them by construction); blocklist + noise filtering happens at
    ingest-JOB selection time via app.projectkb.blocklist.classify_message,
    marking rows with skip_reason instead of dropping them.

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

from app import timeservice
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
    # no changes.
    conversation_type: str = "dm"
    # Groups messages belonging to one conversation (step 20): Outlook's
    # conversationId; Slack's "<channel>:<thread_ts or ts>". The ingest
    # job batches per thread_key so claims get thread context (spec §4.2).
    thread_key: Optional[str] = None


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
    def normalize(self, db: Session, raw: Dict[str, Any], manager_id: Optional[str] = None) -> Optional[NormalizedMessage]:
        """raw channel payload -> common shape, or None if this payload
        isn't a real message at all (bot message, non-message event, etc).
        Purely descriptive -- storage policy (dedup, step 20's
        store-everything) lives in ingest(). `manager_id` is optional and
        unused by most connectors (Outlook doesn't need it) -- Slack needs
        it to resolve the reader token for a live conversations.members
        call when the manager themself is the sender of a DM (see
        SlackConnector._resolve_dm_other_participant)."""
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
    """Shared policy step: normalize, dedup, insert. Stores EVERYTHING
    (step 20's track-everything inversion, spec §4.2) -- what NOT to
    process is decided later, at ingest-job selection time
    (app.projectkb.blocklist.classify_message), by marking rows with a
    skip_reason rather than never storing them, so blocklist edits never
    lose history. `manager_id` names whose db.sqlite (via `db`) and whose
    blocklist this message belongs to. Returns (row_or_None, status):
      "ok"                    -- newly inserted
      "ignored_duplicate"     -- already existed
      "ignored_not_a_message" -- normalize() returned None
    """
    normalized = connector.normalize(db, raw, manager_id)
    if normalized is None:
        logger.debug(f"[{connector.source}] ingest: not a real message, skipping")
        return None, "ignored_not_a_message"

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
        thread_id=normalized.thread_key,
        subject=normalized.subject,
        content=normalized.content,
        timestamp=normalized.timestamp,
        created_at=timeservice.now_ist(),
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
