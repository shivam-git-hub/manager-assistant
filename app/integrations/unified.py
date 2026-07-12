from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select, delete, update
from datetime import datetime
import uuid
from typing import List, Optional

from app.database import get_db, UnifiedMessage, TeamMember, Project, Task
from app.kb.schemas import (
    UnifiedMessageResponse, ProjectCreate, ProjectResponse,
    TaskCreate, TaskResponse, TaskUpdate
)
from app.config import IST
from app import timeservice

router = APIRouter(tags=["Unified Core & Dashboard"])

# ────────────────────────────────────────────────────────
# 1. UNIFIED MESSAGES ENDPOINTS
# ────────────────────────────────────────────────────────

@router.get("/api/messages", response_model=List[UnifiedMessageResponse])
def get_unified_messages(source: Optional[str] = None, limit: int = 100, db: Session = Depends(get_db)):
    query = select(UnifiedMessage)
    if source:
        query = query.where(UnifiedMessage.source == source)
    query = query.order_by(UnifiedMessage.timestamp.desc()).limit(limit)
    result = db.scalars(query).all()
    # Reverse to return in standard ascending chronological order for chat UI
    return list(reversed(result))

@router.post("/api/integrations/dashboard/message", response_model=UnifiedMessageResponse, status_code=status.HTTP_201_CREATED)
def dashboard_message_ingest(payload: dict, db: Session = Depends(get_db)):
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
        is_processed=False,
        raw_metadata=None
    )
    
    db.add(new_msg)
    db.commit()
    db.refresh(new_msg)
    return new_msg

@router.post("/api/messages/reset")
def reset_database(db: Session = Depends(get_db)):
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
# 2. PROJECTS ENDPOINTS (KB)
# ────────────────────────────────────────────────────────

@router.get("/api/projects", response_model=List[ProjectResponse])
def get_projects(db: Session = Depends(get_db)):
    return list(db.scalars(select(Project).order_by(Project.name)).all())

@router.post("/api/projects", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(project: ProjectCreate, db: Session = Depends(get_db)):
    existing = db.scalars(select(Project).where(Project.name == project.name)).first()
    if existing:
        raise HTTPException(status_code=400, detail="Project with this name already exists")
        
    new_project = Project(
        name=project.name,
        description=project.description,
        manager_id=project.manager_id,
        status=project.status
    )
    db.add(new_project)
    db.commit()
    db.refresh(new_project)
    
    # Auto-create KB entity
    from app.kb.models import get_or_create_entity, slugify
    get_or_create_entity(
        db=db,
        slug=f"project:{slugify(new_project.name)}",
        type="project",
        name=new_project.name,
        ref_id=str(new_project.id)
    )
    
    return new_project


# ────────────────────────────────────────────────────────
# 3. TASKS ENDPOINTS (KB)
# ────────────────────────────────────────────────────────

@router.get("/api/tasks", response_model=List[TaskResponse])
def get_tasks(project_id: Optional[int] = None, db: Session = Depends(get_db)):
    query = select(Task)
    if project_id is not None:
        query = query.where(Task.project_id == project_id)
    return list(db.scalars(query.order_by(Task.id)).all())

@router.post("/api/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(task: TaskCreate, db: Session = Depends(get_db)):
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
def update_task(task_id: int, payload: TaskUpdate, db: Session = Depends(get_db)):
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
