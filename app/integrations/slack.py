from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from datetime import datetime
import json
import pytz
import uuid
from pydantic import BaseModel
from typing import Optional

from app.database import get_db, UnifiedMessage, TeamMember
from app.config import IST
from app import timeservice

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
        ts_float = float(event.get("ts", timeservice.now_epoch()))
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


def send_slack_message_internal(channel: Optional[str], text: Optional[str], db: Session) -> dict:
    if not channel or not text or not (channel.startswith("C_") or channel.startswith("DM_")):
        return {"ok": False, "error": "invalid_arguments"}
        
    ts_epoch = timeservice.now_epoch()
    platform_msg_id = f"slack_out_{ts_epoch}"
    
    # Check for collision
    existing = db.scalars(select(UnifiedMessage).where(UnifiedMessage.platform_msg_id == platform_msg_id)).first()
    if existing:
        platform_msg_id = f"slack_out_{ts_epoch}_{uuid.uuid4().hex[:8]}"
        
    new_msg = UnifiedMessage(
        platform_msg_id=platform_msg_id,
        source="slack",
        direction="outbound",
        sender_raw_id="U_HARRY",
        sender_mapped_name="Harry",
        channel_raw_id=channel,
        thread_id=None,
        subject=None,
        content=text,
        timestamp=timeservice.now_ist(),
        is_processed=False,
        raw_metadata=json.dumps({"channel": channel, "text": text})
    )
    
    db.add(new_msg)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # Retry with uuid
        platform_msg_id = f"slack_out_{ts_epoch}_{uuid.uuid4().hex[:8]}"
        new_msg.platform_msg_id = platform_msg_id
        db.add(new_msg)
        db.commit()
        
    return {
        "ok": True,
        "channel": channel,
        "ts": str(ts_epoch),
        "message_id": platform_msg_id,
        "message": {
            "user": "U_HARRY",
            "type": "message",
            "text": text,
            "ts": str(ts_epoch)
        }
    }


class SlackSendPayload(BaseModel):
    channel: Optional[str] = None
    text: Optional[str] = None


@router.post("/send")
async def send_slack_message(payload: SlackSendPayload, db: Session = Depends(get_db)):
    return send_slack_message_internal(payload.channel, payload.text, db)
