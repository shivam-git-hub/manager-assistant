import json
import logging
from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import APIRouter, Depends, Query, status, HTTPException
from sqlalchemy.orm import Session, Mapped, mapped_column
from sqlalchemy import select, and_, or_, Integer, String, Text, DateTime, ForeignKey, Boolean

from app.database import Base, get_db, TeamMember, UnifiedMessage, Task
from app import timeservice
from app.outbound import send_or_hold

logger = logging.getLogger(__name__)

class Followup(Base):
    __tablename__ = "followups"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_slug: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    task_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("tasks.id"), nullable=True)
    target_member_id: Mapped[str] = mapped_column(String(100), ForeignKey("team_members.id"))
    question: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(100), default="harry")  # "harry" or manager ID
    due_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="open")  # "open" | "answered" | "escalated" | "cancelled"
    ping_count: Mapped[int] = mapped_column(Integer, default=0)
    last_ping_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    answer_message_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("unified_messages.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())


def get_dm_channel_id(user_a: str, user_b: str) -> str:
    sorted_users = sorted([user_a, user_b])
    return f"DM_{sorted_users[0]}_{sorted_users[1]}"


def run_followup_check(db: Session, now: Optional[datetime] = None) -> dict:
    """
    Automated check of open followups: answers detection, pinging due items, and escalation.
    """
    if now is None:
        now = timeservice.now_ist()
    
    # 1. Answer Detection
    open_pings = list(db.scalars(
        select(Followup).where((Followup.status == "open") & (Followup.ping_count >= 1))
    ).all())
    
    answered_count = 0
    for f in open_pings:
        # Generate Harry-DM channel ID
        dm_channel = get_dm_channel_id("U_HARRY", f.target_member_id)
        
        # Look for any inbound message from target in this DM channel newer than last_ping_at
        stmt = select(UnifiedMessage).where(
            (UnifiedMessage.channel_raw_id == dm_channel) &
            (UnifiedMessage.sender_raw_id == f.target_member_id) &
            (UnifiedMessage.direction == "inbound") &
            (UnifiedMessage.timestamp > f.last_ping_at)
        ).order_by(UnifiedMessage.timestamp.desc())
        
        reply = db.scalars(stmt).first()
        if reply:
            f.status = "answered"
            f.answer_message_id = reply.id
            answered_count += 1
            
    db.commit()
    
    # Fetch all open followups again (some might have been marked answered)
    open_followups = list(db.scalars(
        select(Followup).where(Followup.status == "open")
    ).all())
    
    pinged_count = 0
    escalated_count = 0
    
    # Find the manager for escalations
    manager = db.scalars(
        select(TeamMember).where(TeamMember.role.ilike("%manager%"))
    ).first()
    
    for f in open_followups:
        # Check if due for pinging
        is_due = f.due_at <= now
        is_fresh = f.ping_count == 0
        is_unanswered_24h = (f.ping_count >= 1 and f.last_ping_at and (now - f.last_ping_at) >= timedelta(hours=24))
        
        # 3. Escalation Check: 24h after 2nd ping
        if f.ping_count >= 2 and is_unanswered_24h:
            f.status = "escalated"
            escalated_count += 1
            
            # Ping manager
            if manager:
                target_member = db.get(TeamMember, f.target_member_id)
                target_name = target_member.name if target_member else f.target_member_id
                manager_dm = get_dm_channel_id("U_HARRY", manager.id)
                escalation_text = f"I've asked {target_name} twice about '{f.question}' with no reply — you may want to step in."
                send_or_hold("slack", {"channel": manager_dm, "text": escalation_text}, db)
                
        # 2. Pinging Check
        elif is_due and (is_fresh or is_unanswered_24h):
            dm_channel = get_dm_channel_id("U_HARRY", f.target_member_id)
            send_or_hold("slack", {"channel": dm_channel, "text": f.question}, db)
            
            f.ping_count += 1
            f.last_ping_at = now
            pinged_count += 1
            
    db.commit()
    
    return {
        "answered": answered_count,
        "pinged": pinged_count,
        "escalated": escalated_count
    }


# Pydantic Schemas for Followup API
from pydantic import BaseModel, ConfigDict

class FollowupCreate(BaseModel):
    entity_slug: Optional[str] = None
    task_id: Optional[int] = None
    target_member_id: str
    question: str
    created_by: Optional[str] = "harry"
    due_at: datetime


class FollowupResponse(BaseModel):
    id: int
    entity_slug: Optional[str] = None
    task_id: Optional[int] = None
    target_member_id: str
    question: str
    created_by: str
    due_at: datetime
    status: str
    ping_count: int
    last_ping_at: Optional[datetime] = None
    answer_message_id: Optional[int] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FollowupUpdate(BaseModel):
    status: Optional[str] = None
    due_at: Optional[datetime] = None


# FastAPI Router
router = APIRouter(prefix="/api/followups", tags=["Follow-up Engine"])

@router.post("", response_model=FollowupResponse, status_code=status.HTTP_201_CREATED)
def create_followup(payload: FollowupCreate, db: Session = Depends(get_db)):
    # Verify target exists
    target = db.get(TeamMember, payload.target_member_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target team member not found")
        
    if payload.task_id:
        t = db.get(Task, payload.task_id)
        if not t:
            raise HTTPException(status_code=404, detail="Task not found")
            
    new_f = Followup(
        entity_slug=payload.entity_slug,
        task_id=payload.task_id,
        target_member_id=payload.target_member_id,
        question=payload.question,
        created_by=payload.created_by or "harry",
        due_at=payload.due_at,
        status="open",
        ping_count=0
    )
    db.add(new_f)
    db.commit()
    db.refresh(new_f)
    return new_f

@router.get("", response_model=List[FollowupResponse])
def get_followups(status: Optional[str] = Query(None), db: Session = Depends(get_db)):
    stmt = select(Followup)
    if status:
        stmt = stmt.where(Followup.status == status)
    return list(db.scalars(stmt).all())

@router.patch("/{id}", response_model=FollowupResponse)
def update_followup(id: int, payload: FollowupUpdate, db: Session = Depends(get_db)):
    f = db.get(Followup, id)
    if not f:
        raise HTTPException(status_code=404, detail="Followup not found")
        
    if payload.status:
        allowed = {"open", "answered", "escalated", "cancelled"}
        if payload.status not in allowed:
            raise HTTPException(status_code=422, detail=f"Status must be one of {allowed}")
        f.status = payload.status
        
    if payload.due_at:
        f.due_at = payload.due_at
        
    db.commit()
    db.refresh(f)
    return f
