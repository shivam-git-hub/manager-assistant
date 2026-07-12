from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from datetime import datetime
import json
import pytz
import uuid

from app.database import get_db, UnifiedMessage, TeamMember
from app.config import IST

router = APIRouter(prefix="/api/integrations/slack", tags=["Slack Integration"])

@router.post("/webhook")
async def slack_webhook(request: Request, db: Session = Depends(get_db)):
    body_bytes = await request.body()
    body_str = body_bytes.decode("utf-8")
    
    if not body_str:
        return {"status": "error", "message": "empty body"}
    
    try:
        payload = json.loads(body_str)
    except json.JSONDecodeError:
        return {"status": "error", "message": "invalid json"}
        
    # 1. URL Verification Handshake
    if payload.get("type") == "url_verification":
        return PlainTextResponse(payload.get("challenge", ""))
        
    # 2. Process Events
    event = payload.get("event")
    if not event:
        return {"status": "ok", "detail": "no event in payload"}
        
    # We only care about message events that have a user and are not bot messages
    if event.get("type") == "message" and "user" in event and event.get("subtype") != "bot_message":
        user_id = event["user"]
        
        # Don't ingest Slack bot notifications
        if user_id == "USLACKBOT":
            return {"status": "ignored", "detail": "slackbot message"}
            
        # Unique platform ID to ensure idempotency
        # Slack messages have a unique 'client_msg_id' or 'ts'
        ts_val = event.get("ts")
        if not ts_val:
            msg_id = event.get("client_msg_id") or f"slack_missing_ts_{uuid.uuid4()}"
        else:
            msg_id = event.get("client_msg_id") or f"slack_{ts_val}"
        
        # Check if already exists to prevent duplicate processing
        existing = db.scalars(select(UnifiedMessage).where(UnifiedMessage.platform_msg_id == msg_id)).first()
        if existing:
            return {"status": "ignored", "detail": "duplicate message"}
            
        # Resolve sender name from our database
        sender_name = None
        sender_member = db.scalars(select(TeamMember).where(
            (TeamMember.slack_handle == user_id) | (TeamMember.id == user_id)
        )).first()
        if sender_member:
            sender_name = sender_member.name
            
        # Parse timestamp
        ts_float = float(event.get("ts", datetime.now().timestamp()))
        timestamp_utc = datetime.fromtimestamp(ts_float, tz=pytz.UTC)
        timestamp_ist = timestamp_utc.astimezone(IST).replace(tzinfo=None) # Store as naive datetime representing IST
        
        # Create Unified Message
        new_msg = UnifiedMessage(
            platform_msg_id=msg_id,
            source="slack",
            sender_raw_id=user_id,
            sender_mapped_name=sender_name,
            channel_raw_id=event.get("channel", "unknown_channel"),
            thread_id=event.get("thread_ts"),
            content=event.get("text", ""),
            timestamp=timestamp_ist,
            raw_metadata=body_str,
            is_processed=False
        )
        
        db.add(new_msg)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return {"status": "ignored", "detail": "duplicate message (race condition)"}
        return {"status": "ok", "message_id": msg_id, "sender_mapped": sender_name}
        
    return {"status": "ignored", "detail": "unsupported event type"}
