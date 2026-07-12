from fastapi import APIRouter, Depends, HTTPException, status, Response
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from datetime import datetime
from html.parser import HTMLParser
import re
import pytz
from typing import Optional, List
from pydantic import BaseModel

from app.database import get_db, UnifiedMessage, TeamMember
from app.config import IST

router = APIRouter(prefix="/api/integrations/outlook", tags=["Outlook Integration"])

# Simple HTML to Markdown/Plain Text parser
class HTMLToMarkdown(HTMLParser):
    def __init__(self):
        super().__init__()
        self.reset()
        self.fed = []
        self.skip_tags = {"style", "script", "head"}
        self.active_skips = set()

    def handle_starttag(self, tag, attrs):
        lower_tag = tag.lower()
        if lower_tag in self.skip_tags:
            self.active_skips.add(lower_tag)
            return
            
        if self.active_skips:
            return

        if tag in ["p", "div", "tr"]:
            self.fed.append("\n")
        elif tag == "br":
            self.fed.append("\n")
        elif tag in ["h1", "h2", "h3", "h4"]:
            self.fed.append(f"\n\n{'#' * int(tag[1])} ")
        elif tag == "li":
            self.fed.append("\n* ")
        elif tag == "td":
            self.fed.append(" | ")

    def handle_endtag(self, tag):
        lower_tag = tag.lower()
        if lower_tag in self.skip_tags:
            self.active_skips.discard(lower_tag)
            return
            
        if self.active_skips:
            return

        if tag in ["p", "div", "tr"]:
            self.fed.append("\n")
        elif tag in ["h1", "h2", "h3", "h4"]:
            self.fed.append("\n")

    def handle_data(self, data):
        if self.active_skips:
            return
        self.fed.append(data)

    def get_text(self) -> str:
        raw_text = "".join(self.fed)
        # Clean up extra spaces/newlines
        raw_text = re.sub(r'[ \t]+', ' ', raw_text)  # merge spaces
        raw_text = re.sub(r'\n{3,}', '\n\n', raw_text)  # limit consecutive newlines to 2
        return raw_text.strip()

def clean_html(html_content: str) -> str:
    if not html_content:
        return ""
    if "<" not in html_content or ">" not in html_content:
        return html_content
    parser = HTMLToMarkdown()
    parser.feed(html_content)
    return parser.get_text()

# Pydantic schemas mirroring Microsoft Graph API Mail representation
class OutlookEmailBody(BaseModel):
    contentType: str  # "text" or "html"
    content: str

class OutlookEmailSenderAddress(BaseModel):
    address: str
    name: Optional[str] = None

class OutlookEmailSender(BaseModel):
    emailAddress: OutlookEmailSenderAddress

class OutlookEmailRecipientAddress(BaseModel):
    address: str
    name: Optional[str] = None

class OutlookEmailRecipient(BaseModel):
    emailAddress: OutlookEmailRecipientAddress

class OutlookEmailPayload(BaseModel):
    id: str  # Unique Outlook ID
    sender: OutlookEmailSender
    toRecipients: Optional[List[OutlookEmailRecipient]] = None
    subject: str
    body: OutlookEmailBody
    receivedDateTime: str  # ISO 8601 UTC string (e.g., "2026-05-29T10:00:00Z")

@router.post("/mock-ingest", status_code=status.HTTP_201_CREATED)
async def outlook_mock_ingest(payload: OutlookEmailPayload, response: Response, db: Session = Depends(get_db)):
    # Verify uniqueness to guarantee idempotency
    existing = db.scalars(select(UnifiedMessage).where(UnifiedMessage.platform_msg_id == payload.id)).first()
    if existing:
        response.status_code = status.HTTP_200_OK
        return {"status": "ignored", "detail": "duplicate email ID", "message_id": payload.id}
        
    sender_email = payload.sender.emailAddress.address
    
    # Extract recipient email address
    recipient_email = "harry.assistant@company.com"
    if payload.toRecipients and len(payload.toRecipients) > 0:
        recipient_email = payload.toRecipients[0].emailAddress.address
    
    # Resolve sender name from db
    sender_name = None
    sender_member = db.scalars(select(TeamMember).where(
        (TeamMember.outlook_email == sender_email) | (TeamMember.id == sender_email)
    )).first()
    if sender_member:
        sender_name = sender_member.name
        
    # Strip HTML if body content is HTML
    cleaned_content = payload.body.content
    if payload.body.contentType.lower() == "html":
        cleaned_content = clean_html(payload.body.content)
        
    # Parse receivedDateTime (usually ISO format like 2026-05-29T10:00:00Z)
    try:
        dt_utc = datetime.fromisoformat(payload.receivedDateTime.replace("Z", "+00:00"))
        dt_ist = dt_utc.astimezone(IST).replace(tzinfo=None) # Store naive datetime for SQLite
    except Exception:
        dt_ist = datetime.now(IST).replace(tzinfo=None)
        
    # Create unified message
    new_msg = UnifiedMessage(
        platform_msg_id=payload.id,
        source="outlook",
        sender_raw_id=sender_email,
        sender_mapped_name=sender_name,
        channel_raw_id=recipient_email,  # Symmetrical recipient inbox
        thread_id=None,
        subject=payload.subject,
        content=cleaned_content,
        timestamp=dt_ist,
        raw_metadata=payload.model_dump_json(),
        is_processed=False
    )
    
    db.add(new_msg)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        response.status_code = status.HTTP_200_OK
        return {"status": "ignored", "detail": "duplicate email ID (race condition)", "message_id": payload.id}
    
    return {"status": "ok", "message_id": payload.id, "sender_mapped": sender_name}
