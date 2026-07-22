"""Compatibility shim.

app/followups.py was deleted mid-refactor (see git history) while the
projectkb rewrite was starting, but health.py, brief.py, kb/meetings.py,
kb/workload.py, agent/situation.py, agent/tools.py, and scheduler.py still
import from it. This file exists only to keep those imports resolvable and
the app booting -- it is NOT a reintroduction of the old follow-up
ping/escalation feature. That whole area (health/brief/meetings-prep/
situation) is old-architecture and slated to be replaced by projectkb; do
not build on top of this shim.
"""
from datetime import datetime
from typing import Optional
from fastapi import APIRouter
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Integer, String, Text, DateTime, ForeignKey

from app.database import Base
from app import timeservice

router = APIRouter(prefix="/api/followups", tags=["Follow-ups (deprecated)"])


class Followup(Base):
    __tablename__ = "followups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_slug: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    task_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("tasks.id"), nullable=True)
    target_member_id: Mapped[str] = mapped_column(String(100), ForeignKey("team_members.id"))
    question: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(100), default="harry")
    due_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="open")
    ping_count: Mapped[int] = mapped_column(Integer, default=0)
    last_ping_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    answer_message_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("unified_messages.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())


def get_dm_channel_id(user_a: str, user_b: str) -> str:
    sorted_users = sorted([user_a, user_b])
    return f"DM_{sorted_users[0]}_{sorted_users[1]}"


def run_followup_check(db, now: Optional[datetime] = None) -> dict:
    """No-op stub. The real ping/escalate logic was removed with the old
    architecture; projectkb's heartbeat/dream jobs will own this behavior."""
    return {"ran": False, "reason": "followup_check disabled pending projectkb migration"}
