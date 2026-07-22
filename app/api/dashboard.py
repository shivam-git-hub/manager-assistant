from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select, delete, update
from datetime import datetime
import uuid
from typing import List, Optional

from app.database import UnifiedMessage, TeamMember, Project, Task
from app.tenancy.db import get_manager_db
from app.kb.schemas import (
    UnifiedMessageResponse,
    TaskCreate, TaskResponse, TaskUpdate
)
from app.config import IST
from app import timeservice

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
        
    # Parse custom timestamp if provided (for simulator date-time)
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
    
    # Reset KB tables
    from app.kb.models import TimelineEntry, AttributedClaim, Conflict, Entity
    db.execute(delete(TimelineEntry))
    db.execute(delete(AttributedClaim))
    db.execute(delete(Conflict))
    db.execute(delete(Entity))
    db.commit()
    
    # Re-backfill team member entities (including Harry)
    from app.database import TeamMember
    members = db.scalars(select(TeamMember)).all()
    for m in members:
        from app.kb.models import get_or_create_entity
        get_or_create_entity(db, slug=f"person:{m.id}", type="person", name=m.name, ref_id=m.id)
        
    return {"status": "ok", "detail": "Messages, projects, and tasks have been reset successfully."}


# ────────────────────────────────────────────────────────
# 2. PROJECTS (KB) -- v1 GET/POST /api/projects handlers removed (step 18,
#    prompts/step_18_registry_and_scaffold.md §3). /api/projects is now
#    served by app.api.projects_registry (global control-plane registry,
#    per spec/architecture_v2_kb.md §3.1). Grep found only
#    tests/test_kb.py depending on the old handlers -- neither the
#    simulator (app/static/index.html) nor the old Vue dashboard's JS
#    (app/static/dashboard/) ever called GET/POST /api/projects, only
#    /api/tasks and /api/dashboard/portfolio below. The old per-manager
#    app.database.Project table itself is untouched -- /api/tasks and
#    /api/dashboard/portfolio below still read it directly.
# ────────────────────────────────────────────────────────
# 3. TASKS ENDPOINTS (KB)
# ────────────────────────────────────────────────────────

@router.get("/api/tasks", response_model=List[TaskResponse])
def get_tasks(project_id: Optional[int] = None, db: Session = Depends(get_manager_db)):
    query = select(Task)
    if project_id is not None:
        query = query.where(Task.project_id == project_id)
    return list(db.scalars(query.order_by(Task.id)).all())

@router.post("/api/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(task: TaskCreate, db: Session = Depends(get_manager_db)):
    # Verify project exists
    proj = db.get(Project, task.project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
        
    # Verify assignee if set
    if task.assignee_id:
        member = db.get(TeamMember, task.assignee_id)
        if not member:
            raise HTTPException(status_code=404, detail="Assignee team member not found")
            
    new_task = Task(
        project_id=task.project_id,
        title=task.title,
        description=task.description,
        assignee_id=task.assignee_id,
        status=task.status,
        blockage_reason=task.blockage_reason,
        due_date=task.due_date
    )
    
    if task.status == "completed":
        new_task.completed_at = timeservice.now_ist()
        
    db.add(new_task)
    db.commit()
    db.refresh(new_task)
    return new_task

@router.patch("/api/tasks/{task_id}", response_model=TaskResponse)
def update_task(task_id: int, payload: TaskUpdate, db: Session = Depends(get_manager_db)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
        
    if payload.assignee_id is not None:
        if payload.assignee_id != "":
            member = db.get(TeamMember, payload.assignee_id)
            if not member:
                raise HTTPException(status_code=404, detail="Assignee team member not found")
            task.assignee_id = payload.assignee_id
        else:
            task.assignee_id = None

    if payload.status is not None:
        if payload.status == "completed" and task.status != "completed":
            task.completed_at = timeservice.now_ist()
        elif payload.status != "completed":
            task.completed_at = None
        task.status = payload.status
        
    if payload.title is not None:
        task.title = payload.title
    if payload.description is not None:
        task.description = payload.description
    if payload.blockage_reason is not None:
        task.blockage_reason = payload.blockage_reason
    if payload.due_date is not None:
        task.due_date = payload.due_date
            
    db.commit()
    db.refresh(task)
    return task

# ────────────────────────────────────────────────────────
# PORTFOLIO VIEW METADATA AGGREGATION
# ────────────────────────────────────────────────────────
import re
from typing import Dict
from sqlalchemy import func
from pydantic import BaseModel
from app.kb.models import Entity, TimelineEntry, Conflict, slugify

class PortfolioProjectResponse(BaseModel):
    project_id: int
    name: str
    status: str
    health: str
    health_reasons: Optional[str] = None
    conflict_count: int
    task_counts: Dict[str, int]
    last_activity_at: Optional[datetime] = None
    compiled_truth_teaser: Optional[str] = None
    entity_slug: str

@router.get("/api/dashboard/portfolio", response_model=List[PortfolioProjectResponse])
def get_dashboard_portfolio(db: Session = Depends(get_manager_db)):
    projects = db.scalars(select(Project).order_by(Project.id.asc())).all()
    
    resp = []
    for p in projects:
        slug = f"project:{slugify(p.name)}"
        entity = db.scalars(select(Entity).where(Entity.slug == slug)).first()
        
        # 1. Open conflicts count
        conflict_count = 0
        compiled_truth_teaser = None
        last_activity_at = None
        
        if entity:
            conflict_count = db.scalar(
                select(func.count(Conflict.id))
                .where((Conflict.entity_id == entity.id) & (Conflict.status == "open"))
            ) or 0
            
            # Compiled truth first sentence
            if entity.compiled_truth:
                teaser_match = re.split(r'(?<=[.!?])\s+', entity.compiled_truth)
                if teaser_match:
                    compiled_truth_teaser = teaser_match[0]
                    
            # Last activity
            last_entry = db.scalars(
                select(TimelineEntry)
                .where(TimelineEntry.entity_id == entity.id)
                .order_by(TimelineEntry.happened_at.desc(), TimelineEntry.id.desc())
                .limit(1)
            ).first()
            if last_entry:
                last_activity_at = last_entry.happened_at
                
        # 2. Task counts
        tasks = db.scalars(select(Task).where(Task.project_id == p.id)).all()
        task_counts = {"pending": 0, "in_progress": 0, "completed": 0, "blocked": 0}
        for t in tasks:
            if t.status in task_counts:
                task_counts[t.status] += 1
                
        resp.append({
            "project_id": p.id,
            "name": p.name,
            "status": p.status,
            "health": p.health,
            "health_reasons": p.health_reasons,
            "conflict_count": conflict_count,
            "task_counts": task_counts,
            "last_activity_at": last_activity_at,
            "compiled_truth_teaser": compiled_truth_teaser,
            "entity_slug": slug
        })
        
    # Sort: red first, then yellow, then green; secondary by last activity desc
    def sort_key(item):
        health_rank = {"red": 0, "yellow": 1, "green": 2}
        hr = health_rank.get(item["health"].lower(), 3)
        la_epoch = item["last_activity_at"].timestamp() if item["last_activity_at"] else 0
        return (hr, -la_epoch)
        
    return sorted(resp, key=sort_key)
