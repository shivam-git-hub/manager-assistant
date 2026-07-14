import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import TeamMember, Meeting, ActionItem
from app.followups import get_dm_channel_id
from app.outbound import send_or_hold
from app.agent.registry import registry

# -----------------------------------------------------------------------------
# Tool Handlers
# -----------------------------------------------------------------------------

def send_slack_dm_handler(db: Session, member_id: str, text: str) -> Dict[str, Any]:
    member = db.get(TeamMember, member_id)
    if not member:
        return {"error": f"Team member with ID '{member_id}' not found."}

    dm_channel = get_dm_channel_id("U_HARRY", member_id)
    res = send_or_hold("slack", {"channel": dm_channel, "text": text}, db)
    return {
        "success": True,
        "status": res["status"],
        "channel": dm_channel,
        "release_at": res.get("release_at"),
        "message_id": res.get("message_id")
    }


def send_email_handler(db: Session, member_id: str, subject: str, body: str) -> Dict[str, Any]:
    member = db.get(TeamMember, member_id)
    if not member:
        return {"error": f"Team member with ID '{member_id}' not found."}

    if not member.outlook_email:
        return {"error": f"Team member '{member_id}' has no registered outlook_email."}

    payload = {
        "message": {
            "subject": subject,
            "body": {
                "content": body,
                "contentType": "HTML"
            },
            "toRecipients": [
                {
                    "emailAddress": {
                        "address": member.outlook_email
                    }
                }
            ]
        }
    }
    res = send_or_hold("outlook", payload, db)
    return {
        "success": True,
        "status": res["status"],
        "recipient": member.outlook_email,
        "release_at": res.get("release_at"),
        "message_id": res.get("message_id")
    }


# -----------------------------------------------------------------------------
# Tool Schemas (OpenAI Functional Calling / Gemini Declarations compatible)
# -----------------------------------------------------------------------------

SEND_SLACK_DM_SCHEMA = {
    "name": "send_slack_dm",
    "description": (
        "Sends a direct message (DM) to a team member on Slack. "
        "This routes through our outbound queue which automatically enforces working hours. "
        "If called outside working hours or on weekends, the message will be held and queued "
        "for scheduled release next business morning at 09:00 IST. "
        "Always inform the manager if the message was sent immediately or queued."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "member_id": {
                "type": "string",
                "description": "The team member ID to send the message to (e.g., 'U_BOB')."
            },
            "text": {
                "type": "string",
                "description": "The message body text."
            }
        },
        "required": ["member_id", "text"]
    }
}

SEND_EMAIL_SCHEMA = {
    "name": "send_email",
    "description": (
        "Sends an email to a team member's registered Outlook address. "
        "Like Slack, it uses the outbound working-hours gate to hold/queue messages sent "
        "during quiet hours."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "member_id": {
                "type": "string",
                "description": "The team member ID to email (e.g., 'U_BOB')."
            },
            "subject": {
                "type": "string",
                "description": "Email subject line."
            },
            "body": {
                "type": "string",
                "description": "Email body content."
            }
        },
        "required": ["member_id", "subject", "body"]
    }
}

# -----------------------------------------------------------------------------
# Meetings Handlers & Schemas
# -----------------------------------------------------------------------------

def list_meetings_handler(
    db: Session,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None
) -> List[Dict[str, Any]]:
    stmt = select(Meeting)
    if from_date:
        try:
            from_dt = datetime.strptime(from_date, "%Y-%m-%d")
            stmt = stmt.where(Meeting.starts_at >= from_dt)
        except ValueError:
            return [{"error": "Invalid from_date format. Must be YYYY-MM-DD"}]
    if to_date:
        try:
            to_dt = datetime.strptime(to_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
            stmt = stmt.where(Meeting.starts_at <= to_dt)
        except ValueError:
            return [{"error": "Invalid to_date format. Must be YYYY-MM-DD"}]

    meetings = db.scalars(stmt.order_by(Meeting.starts_at.asc())).all()
    resp = []
    for m in meetings:
        try:
            atts = json.loads(m.attendees)
        except Exception:
            atts = []
        resp.append({
            "id": m.id,
            "title": m.title,
            "starts_at": m.starts_at.strftime("%Y-%m-%d %H:%M:%S"),
            "ends_at": m.ends_at.strftime("%Y-%m-%d %H:%M:%S") if m.ends_at else None,
            "attendees": atts,
            "project_id": m.project_id,
            "status": m.status
        })
    return resp

def get_meeting_handler(db: Session, meeting_id: int) -> Dict[str, Any]:
    meeting = db.get(Meeting, meeting_id)
    if not meeting:
        return {"error": f"Meeting {meeting_id} not found."}

    try:
        atts = json.loads(meeting.attendees)
    except Exception:
        atts = []

    items = db.scalars(select(ActionItem).where(ActionItem.meeting_id == meeting_id)).all()
    action_items = [
        {
            "id": item.id,
            "description": item.description,
            "owner_member_id": item.owner_member_id,
            "due_date": item.due_date.strftime("%Y-%m-%d") if item.due_date else None
        }
        for item in items
    ]

    return {
        "id": meeting.id,
        "title": meeting.title,
        "starts_at": meeting.starts_at.strftime("%Y-%m-%d %H:%M:%S"),
        "ends_at": meeting.ends_at.strftime("%Y-%m-%d %H:%M:%S") if meeting.ends_at else None,
        "attendees": atts,
        "project_id": meeting.project_id,
        "status": meeting.status,
        "mom_raw": meeting.mom_raw,
        "action_items": action_items
    }

def create_meeting_handler(
    db: Session,
    title: str,
    starts_at: str,
    attendees: List[str],
    project_id: Optional[int] = None
) -> Dict[str, Any]:
    try:
        parsed_starts = datetime.strptime(starts_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            parsed_starts = datetime.strptime(starts_at, "%Y-%m-%d")
        except ValueError:
            return {"error": "Invalid starts_at format. Must be YYYY-MM-DD HH:MM:SS"}

    from app.kb.meetings import create_meeting as create_meeting_internal, MeetingCreate
    payload = MeetingCreate(
        title=title,
        starts_at=parsed_starts,
        attendees=attendees,
        project_id=project_id
    )
    res = create_meeting_internal(payload, db)
    return {
        "success": True,
        "message": f"Meeting '{title}' created successfully with ID {res.id}.",
        "meeting": {
            "id": res.id,
            "title": res.title,
            "starts_at": res.starts_at.strftime("%Y-%m-%d %H:%M:%S"),
            "attendees": res.attendees
        }
    }


LIST_MEETINGS_SCHEMA = {
    "name": "list_meetings",
    "description": "Lists calendar entries for the range of dates. Helpful to retrieve what meetings are scheduled or completed.",
    "parameters": {
        "type": "object",
        "properties": {
            "from_date": {
                "type": "string",
                "description": "Optional start date in YYYY-MM-DD format."
            },
            "to_date": {
                "type": "string",
                "description": "Optional end date in YYYY-MM-DD format."
            }
        }
    }
}

GET_MEETING_SCHEMA = {
    "name": "get_meeting",
    "description": "Fetches a specific meeting with its attendees, raw minutes, and extracted action items list by ID.",
    "parameters": {
        "type": "object",
        "properties": {
            "meeting_id": {
                "type": "integer",
                "description": "The integer ID of the meeting to fetch."
            }
        },
        "required": ["meeting_id"]
    }
}

CREATE_MEETING_SCHEMA = {
    "name": "create_meeting",
    "description": "Schedules a new meeting in the calendar, registers its meeting Entity, and schedules its pre-meeting brief. Harry or the manager can call this tool from chat.",
    "parameters": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "The title or subject of the meeting."
            },
            "starts_at": {
                "type": "string",
                "description": "The starting date-time in 'YYYY-MM-DD HH:MM:SS' format."
            },
            "attendees": {
                "type": "array",
                "items": { "type": "string" },
                "description": "A list of participating team member IDs, e.g., ['U_ALICE', 'U_BOB']."
            },
            "project_id": {
                "type": "integer",
                "description": "Optional associated project integer ID."
            }
        },
        "required": ["title", "starts_at", "attendees"]
    }
}

# -----------------------------------------------------------------------------
# Tool Registration
# -----------------------------------------------------------------------------

def register_tools() -> None:
    registry.register("send_slack_dm", SEND_SLACK_DM_SCHEMA, send_slack_dm_handler)
    registry.register("send_email", SEND_EMAIL_SCHEMA, send_email_handler)

    # Meetings Tools
    registry.register("list_meetings", LIST_MEETINGS_SCHEMA, list_meetings_handler)
    registry.register("get_meeting", GET_MEETING_SCHEMA, get_meeting_handler)
    registry.register("create_meeting", CREATE_MEETING_SCHEMA, create_meeting_handler)

# Auto register on import
register_tools()
