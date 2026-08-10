from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select, delete
from datetime import datetime
import uuid
from typing import List, Optional
from pydantic import BaseModel, ConfigDict

from app.database import UnifiedMessage, TeamMember, Task, Project
from app.tenancy.db import get_manager_db
from app.config import IST
from app import timeservice


class UnifiedMessageResponse(BaseModel):
    id: int
    platform_msg_id: str
    source: str
    direction: str = "inbound"
    sender_raw_id: str
    sender_mapped_name: Optional[str] = None
    receiver_raw_id: Optional[str] = None
    receiver_mapped_name: Optional[str] = None
    channel_raw_id: str
    thread_id: Optional[str] = None
    subject: Optional[str] = None
    content: str
    timestamp: datetime
    created_at: Optional[datetime] = None
    is_processed: bool = False
    processed_at: Optional[datetime] = None
    raw_metadata: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

# Moved from app/integrations/unified.py: read API over unified_messages,
# plus projects/tasks/portfolio, plus the dashboard channel's own ingest
# endpoint. Not a connector -- dashboard_message_ingest below is
# deliberately NOT built against app.integrations.base.ChannelConnector;
# direct-chat-with-Harry logic (routing into the agent) has been removed
# from here and is deferred until the agent is redesigned.
router = APIRouter(tags=["Unified Core & Dashboard"])

# ────────────────────────────────────────────────────────
# 1. UNIFIED MESSAGES ENDPOINTS
# ────────────────────────────────────────────────────────

@router.get("/api/messages", response_model=List[UnifiedMessageResponse])
def get_unified_messages(source: Optional[str] = None, limit: int = 100, db: Session = Depends(get_manager_db)):
    query = select(UnifiedMessage)
    if source:
        query = query.where(UnifiedMessage.source == source)
    query = query.order_by(UnifiedMessage.timestamp.desc()).limit(limit)
    result = db.scalars(query).all()
    # Reverse to return in standard ascending chronological order for chat UI
    return list(reversed(result))

@router.get("/api/messages/{id}", response_model=UnifiedMessageResponse)
def get_unified_message_by_id(id: int, db: Session = Depends(get_manager_db)):
    msg = db.get(UnifiedMessage, id)
    if not msg:
        raise HTTPException(status_code=404, detail=f"Message with ID {id} not found")
    return msg

@router.post("/api/integrations/dashboard/message", response_model=UnifiedMessageResponse, status_code=status.HTTP_201_CREATED)
def dashboard_message_ingest(payload: dict, db: Session = Depends(get_manager_db)):
    """
    Accepts direct message from Dashboard.
    Format: {"user_name": "Shivam", "message": "Hi Harry"}
    """
    user_name = payload.get("user_name", "Shivam")
    msg_text = payload.get("message", "")
    
    if not msg_text:
        raise HTTPException(status_code=400, detail="message content is required")
        
    msg_id = f"dash_{uuid.uuid4()}"
    sender_raw = f"dashboard_{user_name.lower().replace(' ', '_')}"
    
    # Try to map user_name or sender_raw
    sender_name = None
    member = db.scalars(select(TeamMember).where(
        (TeamMember.name == user_name) | (TeamMember.id == sender_raw)
    )).first()
    if member:
        sender_name = member.name
        
    # Parse custom timestamp if provided
    custom_ts = payload.get("timestamp")
    if custom_ts:
        try:
            dt_utc = datetime.fromisoformat(custom_ts.replace("Z", "+00:00"))
            timestamp_ist = dt_utc.astimezone(IST).replace(tzinfo=None)
        except Exception:
            timestamp_ist = timeservice.now_ist()
    else:
        timestamp_ist = timeservice.now_ist()

    new_msg = UnifiedMessage(
        platform_msg_id=msg_id,
        source="dashboard",
        sender_raw_id=sender_raw,
        sender_mapped_name=sender_name,
        channel_raw_id="dashboard_chat",
        thread_id=None,
        subject=None,
        content=msg_text,
        timestamp=timestamp_ist,
        created_at=datetime.now(),
        is_processed=False,
        raw_metadata=None
    )
    
    db.add(new_msg)
    db.commit()
    db.refresh(new_msg)
    return new_msg

@router.post("/api/messages/reset")
def reset_database(db: Session = Depends(get_manager_db)):
    """
    Resets the database, deleting all unified messages, projects, and tasks.
    Keeps team members intact for testing.
    """
    db.execute(delete(UnifiedMessage))
    db.execute(delete(Task))
    db.execute(delete(Project))
    db.commit()

    return {"status": "ok", "detail": "Messages, projects, and tasks have been reset successfully."}
