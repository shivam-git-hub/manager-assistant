from datetime import datetime, timedelta
import json
from typing import Optional, List
from fastapi import APIRouter, Depends, Query, status, Response, HTTPException
from sqlalchemy import select, String, Text, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column, Session

from app.database import Base, get_db, UnifiedMessage
from app import timeservice
from app.config import WORK_HOURS_START, WORK_HOURS_END
from app.integrations.slack import send_slack_message_internal
from app.integrations.outlook import send_outlook_message_internal

class OutboundQueue(Base):
    __tablename__ = "outbound_queue"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_type: Mapped[str] = mapped_column(String(10))  # "slack" or "outlook"
    payload: Mapped[str] = mapped_column(Text)  # JSON representation of the send body
    status: Mapped[str] = mapped_column(String(10), default="held")  # "held", "sent", "cancelled"
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())
    scheduled_release_at: Mapped[datetime] = mapped_column(DateTime)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    result_message_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

# router definitions
router = APIRouter(prefix="/api/outbound", tags=["Outbound Queue"])

def is_quiet_hours(dt: datetime) -> bool:
    """
    Returns True if dt is outside working hours [WORK_HOURS_START, WORK_HOURS_END)
    or is on Saturday/Sunday.
    """
    if dt.weekday() in (5, 6):  # Saturday=5, Sunday=6
        return True
    if dt.hour < WORK_HOURS_START or dt.hour >= WORK_HOURS_END:
        return True
    return False

def next_work_morning(dt: datetime) -> datetime:
    """
    Returns the next datetime at 09:00 on a weekday strictly after quiet hours begin.
    """
    current = dt
    if current.weekday() < 5 and current.hour < WORK_HOURS_START:
        return datetime(current.year, current.month, current.day, WORK_HOURS_START, 0, 0)
        
    while True:
        current += timedelta(days=1)
        if current.weekday() < 5:
            return datetime(current.year, current.month, current.day, WORK_HOURS_START, 0, 0)

def _dispatch(channel_type: str, payload: dict, db: Session) -> Optional[str]:
    """
    Sends a queued/immediate payload through the matching transport.
    Returns the resulting platform_msg_id, or None if the send failed.
    """
    if channel_type == "slack":
        res = send_slack_message_internal(payload.get("channel"), payload.get("text"), db)
        return res.get("message_id") if res.get("ok") else None
    elif channel_type == "outlook":
        # Extract from the Graph sendMail schema
        msg_payload = payload.get("message", {})
        subject = msg_payload.get("subject", "")
        body_payload = msg_payload.get("body", {})
        body_html = body_payload.get("content", "")
        recipients_list = msg_payload.get("toRecipients", [])
        to_emails = [r.get("emailAddress", {}).get("address") for r in recipients_list if r.get("emailAddress", {}).get("address")]

        try:
            res = send_outlook_message_internal(subject, body_html, to_emails, db)
        except HTTPException:
            return None
        return res.get("message_id")
    return None

def send_or_hold(channel_type: str, payload: dict, db: Session) -> dict:
    """
    Determines if the message should be sent immediately or held based on quiet hours.
    """
    now = timeservice.now_ist()
    if is_quiet_hours(now):
        release_at = next_work_morning(now)
        row = OutboundQueue(
            channel_type=channel_type,
            payload=json.dumps(payload),
            status="held",
            created_at=now,
            scheduled_release_at=release_at
        )
        db.add(row)
        db.commit()
        return {"status": "held", "release_at": release_at}
    else:
        msg_id = _dispatch(channel_type, payload, db)
        return {"status": "sent", "message_id": msg_id}

@router.post("/release")
async def release_queued_messages(db: Session = Depends(get_db)):
    """
    Releases and sends all held messages whose scheduled release time is <= now_ist.
    """
    now = timeservice.now_ist()
    # Find all held messages due to be released
    stmt = select(OutboundQueue).where(
        (OutboundQueue.status == "held") & (OutboundQueue.scheduled_release_at <= now)
    )
    queued_rows = db.scalars(stmt).all()
    
    count = 0
    for row in queued_rows:
        try:
            payload_dict = json.loads(row.payload)
            msg_id = _dispatch(row.channel_type, payload_dict, db)
        except json.JSONDecodeError:
            msg_id = None

        if msg_id is None:
            # Bad payload or transport rejection: don't retry forever, and
            # don't claim it was sent.
            row.status = "cancelled"
        else:
            row.status = "sent"
            row.sent_at = timeservice.now_ist()
            row.result_message_id = msg_id
            count += 1

    db.commit()
    
    # Get total remaining held
    stmt_rem = select(OutboundQueue).where(OutboundQueue.status == "held")
    remaining = len(db.scalars(stmt_rem).all())
    
    return {"released": count, "remaining_held": remaining}

@router.get("/queue")
async def list_outbound_queue(status: Optional[str] = Query(None), db: Session = Depends(get_db)):
    """
    Lists outbound queue rows filtered by status.
    """
    stmt = select(OutboundQueue)
    if status:
        stmt = stmt.where(OutboundQueue.status == status)
    rows = db.scalars(stmt).all()
    
    # Format rows as dict for JSON response
    result = []
    for r in rows:
        result.append({
            "id": r.id,
            "channel_type": r.channel_type,
            "payload": r.payload,
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "scheduled_release_at": r.scheduled_release_at.isoformat() if r.scheduled_release_at else None,
            "sent_at": r.sent_at.isoformat() if r.sent_at else None,
            "result_message_id": r.result_message_id
        })
    return result
