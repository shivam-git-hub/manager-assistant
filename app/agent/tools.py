import json
from datetime import datetime, timedelta, date
from typing import Optional, List, Dict, Any
from sqlalchemy import select, or_, and_
from sqlalchemy.orm import Session

from app.database import Task, Project, TeamMember, UnifiedMessage
from app.kb.models import Entity, TimelineEntry, AttributedClaim, Conflict, slugify
from app.followups import Followup, get_dm_channel_id
from app.outbound import send_or_hold
from app import timeservice
from app.agent.registry import registry

# -----------------------------------------------------------------------------
# Tool Handlers
# -----------------------------------------------------------------------------

def kb_search_handler(db: Session, q: str) -> Dict[str, Any]:
    term = f"%{q}%"
    
    # 1. Search entities: name or compiled_truth
    stmt_ent = (
        select(Entity)
        .where(or_(Entity.name.ilike(term), Entity.compiled_truth.ilike(term)))
        .limit(20)
    )
    entities = db.scalars(stmt_ent).all()
    
    # 2. Search claims: claim text
    stmt_claims = (
        select(AttributedClaim, Entity.slug)
        .join(Entity, Entity.id == AttributedClaim.entity_id)
        .where(and_(AttributedClaim.claim.ilike(term), AttributedClaim.active == True))
        .limit(20)
    )
    claims_res = db.execute(stmt_claims).all()
    
    # 3. Search timeline: summary or detail
    stmt_time = (
        select(TimelineEntry, Entity.slug)
        .join(Entity, Entity.id == TimelineEntry.entity_id)
        .where(or_(TimelineEntry.summary.ilike(term), TimelineEntry.detail.ilike(term)))
        .limit(20)
    )
    timeline_res = db.execute(stmt_time).all()
    
    return {
        "entities": [
            {"slug": e.slug, "type": e.type, "name": e.name, "compiled_truth": e.compiled_truth}
            for e in entities
        ],
        "claims": [
            {"id": c.AttributedClaim.id, "entity_slug": c.slug, "claim": c.AttributedClaim.claim, "holder": c.AttributedClaim.holder}
            for c in claims_res
        ],
        "timeline": [
            {"id": t.TimelineEntry.id, "entity_slug": t.slug, "summary": t.TimelineEntry.summary, "detail": t.TimelineEntry.detail}
            for t in timeline_res
        ]
    }


def get_entity_handler(db: Session, slug: str) -> Dict[str, Any]:
    entity = db.scalars(select(Entity).where(Entity.slug == slug)).first()
    if not entity:
        return {"error": f"Entity with slug '{slug}' not found."}
        
    timeline = db.scalars(
        select(TimelineEntry)
        .where(TimelineEntry.entity_id == entity.id)
        .order_by(TimelineEntry.happened_at.desc(), TimelineEntry.id.desc())
    ).all()

    claims = db.scalars(
        select(AttributedClaim)
        .where((AttributedClaim.entity_id == entity.id) & (AttributedClaim.active == True))
    ).all()
    
    conflicts = db.scalars(
        select(Conflict)
        .where((Conflict.entity_id == entity.id) & (Conflict.status == "open"))
    ).all()
    
    return {
        "entity": {
            "slug": entity.slug,
            "type": entity.type,
            "name": entity.name,
            "compiled_truth": entity.compiled_truth,
            "truth_updated_at": entity.truth_updated_at
        },
        "timeline": [
            {"id": t.id, "happened_at": t.happened_at, "summary": t.summary, "detail": t.detail}
            for t in timeline
        ],
        "claims": [
            {"id": c.id, "claim": c.claim, "kind": c.kind, "holder": c.holder, "weight": c.weight, "claimed_at": c.claimed_at}
            for c in claims
        ],
        "conflicts": [
            {"id": x.id, "claim_a_id": x.claim_a_id, "claim_b_id": x.claim_b_id, "severity": x.severity, "description": x.description}
            for x in conflicts
        ]
    }


def list_projects_handler(db: Session) -> List[Dict[str, Any]]:
    projects = db.scalars(select(Project).order_by(Project.id.asc())).all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "manager_id": p.manager_id,
            "status": p.status,
            "health": p.health,
            "health_reasons": p.health_reasons,
            "health_updated_at": p.health_updated_at
        }
        for p in projects
    ]


def list_tasks_handler(
    db: Session,
    project_id: Optional[int] = None,
    assignee_id: Optional[str] = None,
    status: Optional[str] = None
) -> List[Dict[str, Any]]:
    stmt = select(Task)
    if project_id is not None:
        stmt = stmt.where(Task.project_id == project_id)
    if assignee_id is not None:
        stmt = stmt.where(Task.assignee_id == assignee_id)
    if status is not None:
        stmt = stmt.where(Task.status == status)
        
    tasks = db.scalars(stmt.order_by(Task.id.asc())).all()
    return [
        {
            "id": t.id,
            "project_id": t.project_id,
            "title": t.title,
            "description": t.description,
            "assignee_id": t.assignee_id,
            "status": t.status,
            "blockage_reason": t.blockage_reason,
            "due_date": t.due_date,
            "completed_at": t.completed_at
        }
        for t in tasks
    ]


def update_task_handler(
    db: Session,
    task_id: int,
    status: Optional[str] = None,
    assignee_id: Optional[str] = None,
    due_date: Optional[str] = None
) -> Dict[str, Any]:
    task = db.get(Task, task_id)
    if not task:
        return {"error": f"Task with ID {task_id} not found."}
        
    updates = {}
    if status is not None:
        allowed_statuses = {"pending", "in_progress", "completed", "blocked"}
        if status not in allowed_statuses:
            return {"error": f"Invalid status '{status}'. Must be one of {allowed_statuses}."}
        task.status = status
        updates["status"] = status
        if status == "completed":
            task.completed_at = timeservice.now_ist()
            updates["completed_at"] = task.completed_at
        else:
            task.completed_at = None
            updates["completed_at"] = None
            
    if assignee_id is not None:
        member = db.get(TeamMember, assignee_id)
        if assignee_id != "" and not member:
            return {"error": f"Assignee with team member ID '{assignee_id}' not found."}
        task.assignee_id = assignee_id if assignee_id != "" else None
        updates["assignee_id"] = task.assignee_id
        
    if due_date is not None:
        if due_date == "":
            task.due_date = None
            updates["due_date"] = None
        else:
            try:
                parsed_date = datetime.strptime(due_date, "%Y-%m-%d").date()
                task.due_date = parsed_date
                updates["due_date"] = parsed_date
            except ValueError:
                return {"error": f"Invalid due_date format '{due_date}'. Must be YYYY-MM-DD."}
                
    db.commit()
    db.refresh(task)
    return {
        "success": True,
        "message": f"Task {task_id} updated successfully.",
        "task": {
            "id": task.id,
            "title": task.title,
            "assignee_id": task.assignee_id,
            "status": task.status,
            "due_date": task.due_date,
            "completed_at": task.completed_at
        }
    }


def get_conflicts_handler(db: Session, status: str = "open") -> List[Dict[str, Any]]:
    stmt = select(Conflict).where(Conflict.status == status)
    conflicts = db.scalars(stmt.order_by(Conflict.id.desc())).all()
    return [
        {
            "id": x.id,
            "entity_id": x.entity_id,
            "claim_a_id": x.claim_a_id,
            "claim_b_id": x.claim_b_id,
            "severity": x.severity,
            "description": x.description,
            "status": x.status,
            "detected_at": x.detected_at,
            "resolved_at": x.resolved_at,
            "resolution_note": x.resolution_note
        }
        for x in conflicts
    ]


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


def create_followup_handler(
    db: Session,
    member_id: str,
    question: str,
    due_at: Optional[str] = None,
    entity_slug: Optional[str] = None
) -> Dict[str, Any]:
    member = db.get(TeamMember, member_id)
    if not member:
        return {"error": f"Team member with ID '{member_id}' not found."}
        
    if due_at:
        try:
            parsed_due = datetime.strptime(due_at, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return {"error": f"Invalid due_at format '{due_at}'. Must be 'YYYY-MM-DD HH:MM:SS'."}
    else:
        parsed_due = timeservice.now_ist() + timedelta(hours=24)
        
    followup = Followup(
        entity_slug=entity_slug,
        target_member_id=member_id,
        question=question,
        due_at=parsed_due,
        created_by="harry",
        status="open",
        created_at=timeservice.now_ist()
    )
    db.add(followup)
    db.commit()
    db.refresh(followup)
    
    return {
        "success": True,
        "message": "Follow-up created successfully.",
        "followup": {
            "id": followup.id,
            "target_member_id": followup.target_member_id,
            "question": followup.question,
            "due_at": followup.due_at,
            "status": followup.status
        }
    }


def get_current_time_handler(db: Session) -> str:
    return timeservice.now_ist().strftime("%Y-%m-%d %H:%M:%S") + " IST"


# -----------------------------------------------------------------------------
# Tool Schemas (OpenAI Functional Calling / Gemini Declarations compatible)
# -----------------------------------------------------------------------------

KB_SEARCH_SCHEMA = {
    "name": "kb_search",
    "description": (
        "Searches the project Knowledge Base (KB) for entities (projects, people, meetings), "
        "attributed claims, and historic timeline entries that match the keyword 'q'. "
        "CRITICAL INSTRUCTION: You MUST invoke this tool BEFORE answering any factual questions "
        "about projects, status updates, timelines, or person-specific comments, as it retrieves the "
        "latest recorded context from slack/outlook ingestion."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "q": {
                "type": "string",
                "description": "The search term, project name, team member name, or keyword to match against."
            }
        },
        "required": ["q"]
    }
}

GET_ENTITY_SCHEMA = {
    "name": "get_entity",
    "description": (
        "Retrieves the complete profile and timeline log for a specific entity slug. "
        "Returns the compiled_truth prose synthesis, all recorded timeline items (ordered by date), "
        "active claims, and open conflicts. "
        "CRITICAL Citing Convention: When mentioning events or facts, you MUST reference their source "
        "timeline IDs with inline bracket citations like [T17] if the timeline ID is 17. Use this to maintain "
        "strict proof-of-work auditable summaries."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "slug": {
                "type": "string",
                "description": "The unique slug for the entity, e.g. 'project:phoenix' or 'person:U_ALICE'."
            }
        },
        "required": ["slug"]
    }
}

LIST_PROJECTS_SCHEMA = {
    "name": "list_projects",
    "description": (
        "Lists all projects in the database, showing their IDs, names, high-level status, "
        "health status (green, yellow, red), and the bulleted reasons why they are in a degraded "
        "state. Useful for general project overviews."
    ),
    "parameters": {
        "type": "object",
        "properties": {}
    }
}

LIST_TASKS_SCHEMA = {
    "name": "list_tasks",
    "description": (
        "Lists tasks in the system with optional filters for project_id, assignee_id, or task status. "
        "Status values can be 'pending', 'in_progress', 'completed', or 'blocked'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_id": {
                "type": "integer",
                "description": "The integer database ID of the project to filter by."
            },
            "assignee_id": {
                "type": "string",
                "description": "The unique team member ID of the assignee to filter by."
            },
            "status": {
                "type": "string",
                "description": "Filter by status: 'pending', 'in_progress', 'completed', or 'blocked'."
            }
        }
    }
}

UPDATE_TASK_SCHEMA = {
    "name": "update_task",
    "description": (
        "Updates an existing task in the database (status, assignee, due date). "
        "CRITICAL INVARIANT: You MUST ONLY invoke this tool when the manager explicitly instructs you "
        "to update, assign, complete, or reassign a task. Do NOT automatically call this tool based "
        "on conversation updates unless specifically directed by the user."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "integer",
                "description": "The database integer ID of the task to update."
            },
            "status": {
                "type": "string",
                "description": "New status: 'pending', 'in_progress', 'completed', or 'blocked'."
            },
            "assignee_id": {
                "type": "string",
                "description": "The new assignee team member ID. Pass empty string '' to unassign."
            },
            "due_date": {
                "type": "string",
                "description": "New due date in YYYY-MM-DD format. Pass empty string '' to clear."
            }
        },
        "required": ["task_id"]
    }
}

GET_CONFLICTS_SCHEMA = {
    "name": "get_conflicts",
    "description": (
        "Retrieves list of claims contradictions/conflicts detected between different team members, "
        "filtered by status ('open', 'resolved', 'dismissed')."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "description": "Filter by conflict status. Default is 'open'."
            }
        }
    }
}

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

CREATE_FOLLOWUP_SCHEMA = {
    "name": "create_followup",
    "description": (
        "Registers a structured follow-up checkpoint in the database. "
        "The automated background scheduler will track this, pinging the team member on Slack "
        "when overdue and escalating to the manager if they stay silent for 48 hours."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "member_id": {
                "type": "string",
                "description": "The team member ID to follow up with."
            },
            "question": {
                "type": "string",
                "description": "The question to ask them."
            },
            "due_at": {
                "type": "string",
                "description": "Simulated expiration datetime in 'YYYY-MM-DD HH:MM:SS' format. Default is 24 hours from now."
            },
            "entity_slug": {
                "type": "string",
                "description": "Optional associated entity slug, e.g., 'project:phoenix'."
            }
        },
        "required": ["member_id", "question"]
    }
}

GET_CURRENT_TIME_SCHEMA = {
    "name": "get_current_time",
    "description": (
        "Returns the current active simulated date and time in IST (Asia/Kolkata). "
        "Use this tool whenever you need to mention time, deadlines, durations, or evaluate if a task is overdue. "
        "DO NOT use raw host datetime or guess."
    ),
    "parameters": {
        "type": "object",
        "properties": {}
    }
}

# -----------------------------------------------------------------------------
# Tool Registration
# -----------------------------------------------------------------------------

def register_tools() -> None:
    registry.register("kb_search", KB_SEARCH_SCHEMA, kb_search_handler)
    registry.register("get_entity", GET_ENTITY_SCHEMA, get_entity_handler)
    registry.register("list_projects", LIST_PROJECTS_SCHEMA, list_projects_handler)
    registry.register("list_tasks", LIST_TASKS_SCHEMA, list_tasks_handler)
    registry.register("update_task", UPDATE_TASK_SCHEMA, update_task_handler)
    registry.register("get_conflicts", GET_CONFLICTS_SCHEMA, get_conflicts_handler)
    registry.register("send_slack_dm", SEND_SLACK_DM_SCHEMA, send_slack_dm_handler)
    registry.register("send_email", SEND_EMAIL_SCHEMA, send_email_handler)
    registry.register("create_followup", CREATE_FOLLOWUP_SCHEMA, create_followup_handler)
    registry.register("get_current_time", GET_CURRENT_TIME_SCHEMA, get_current_time_handler)

# Auto register on import
register_tools()
