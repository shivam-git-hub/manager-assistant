"""Manual job triggers + message/claim/event chain visualization for local
testing -- NOT part of the manager-facing
product surface. Backs app/static/debug.html and app/static/admin.html, plain no-build test pages in
the same spirit as login.html/connect.html. Every route is
manager-scoped (cookie auth via get_current_employee/get_manager_db) --
manually running a job runs it against the LOGGED-IN manager's own data,
same as the existing POST /api/heartbeat/run.
"""
import json
import os
from typing import Dict, List, Optional, Any
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.controlplane.auth import get_current_employee
from app.controlplane.models import Employee, get_controlplane_db
from app.database import Claim, ClaimSource, UnifiedMessage, Workflow, CronJob, FollowupAgent, TeamMember
from app.tenancy.db import get_manager_db
from app.tenancy.paths import manager_dir, manager_memory_md_path
from app.projectkb.enums import JobName
from app.projectkb.jobs import ingestion, heartbeat, dream, lint, outlook_poll, slack_poll, agent_heartbeat
from app.projectkb.job_schedule import load_job_schedule

router = APIRouter(prefix="/api/dev", tags=["Dev Tools"])

# Same run(db, manager_id, client=None) convention every projectkb job
# module follows (app.projectkb.scheduler._JOBS mirrors this list) -- kept
# as a separate literal dict here rather than importing that private one,
# since this is a standalone debug surface, not scheduler internals.
JOB_MODULES = {
    JobName.OUTLOOK_POLL.value: outlook_poll,
    JobName.SLACK_POLL.value: slack_poll,
    JobName.INGESTION.value: ingestion,
    JobName.HEARTBEAT.value: heartbeat,
    JobName.DREAM.value: dream,
    JobName.LINT.value: lint,
    JobName.AGENT_HEARTBEAT.value: agent_heartbeat,
}


@router.get("/jobs")
def list_jobs(manager: Employee = Depends(get_current_employee)) -> dict:
    """Job names + their configured interval (env -> job_schedule.json ->
    per-manager override), for the debug page to render without hardcoding frequencies."""
    cfg = load_job_schedule(manager.id)
    return {
        name: {"interval_minutes": cfg.get(name, {}).get("interval_minutes")}
        for name in JOB_MODULES
    }


@router.post("/jobs/{job_name}/run")
def run_job(
    job_name: str,
    manager: Employee = Depends(get_current_employee),
    db: Session = Depends(get_manager_db),
) -> dict:
    module = JOB_MODULES.get(job_name)
    if module is None:
        raise HTTPException(status_code=404, detail=f"Unknown job '{job_name}'. Must be one of {list(JOB_MODULES)}")

    from app.projectkb.scheduler import get_job_lock

    lock = get_job_lock(job_name)
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="job already running")
    try:
        result = module.run(db, manager.id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Job '{job_name}' raised: {e}")
    finally:
        lock.release()
    return {"job": job_name, "result": result}


def _message_preview(db: Session, message_id: int) -> Optional[dict]:
    msg = db.get(UnifiedMessage, message_id)
    if not msg:
        return None
    return {
        "id": msg.id,
        "source": msg.source,
        "sender": msg.sender_mapped_name or msg.sender_raw_id,
        "content": (msg.content or "")[:200],
        "timestamp": str(msg.timestamp),
        "thread_id": msg.thread_id,
    }


@router.get("/claims")
def list_claims(limit: int = 100, db: Session = Depends(get_manager_db)) -> List[dict]:
    """Claims + which messages they cite + processed flag -- the middle
    link in the message -> claim -> event chain."""
    claims = db.query(Claim).order_by(Claim.created_at.desc()).limit(limit).all()
    out = []
    for c in claims:
        source_rows = db.query(ClaimSource).filter(ClaimSource.claim_id == c.id).all()
        sources = [_message_preview(db, s.message_id) for s in source_rows]
        out.append({
            "id": c.id,
            "text": c.text,
            "thread_key": c.thread_key,
            "processed": c.processed,
            "created_at": str(c.created_at),
            "sources": [s for s in sources if s],
        })
    return out


# -----------------------------------------------------------------------------
# New Admin/Dashboard CRUD + Visualization endpoints
# -----------------------------------------------------------------------------

class EmployeePayload(BaseModel):
    id: str
    email: str
    name: str
    role: Optional[str] = None
    is_manager: bool = False
    slack_id: Optional[str] = None

class TeamMemberPayload(BaseModel):
    id: str
    name: str
    role: str
    slack_handle: Optional[str] = None
    outlook_email: Optional[str] = None
    timezone: str = "Asia/Kolkata"

class FilePayload(BaseModel):
    filename: str  # "user.md" or "memory.md"
    content: str


@router.get("/employees")
def dev_list_employees(
    cp_db: Session = Depends(get_controlplane_db),
    manager: Employee = Depends(get_current_employee)
):
    employees = cp_db.query(Employee).order_by(Employee.name).all()
    return [
        {
            "id": e.id,
            "email": e.email,
            "name": e.name,
            "role": e.role,
            "is_manager": e.is_manager,
            "slack_id": e.slack_id,
            "created_at": str(e.created_at)
        }
        for e in employees
    ]


@router.post("/employees")
def dev_create_employee(
    payload: EmployeePayload,
    cp_db: Session = Depends(get_controlplane_db),
    manager: Employee = Depends(get_current_employee)
):
    existing = cp_db.query(Employee).filter(Employee.id == payload.id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Employee with this ID already exists.")
    
    existing_email = cp_db.query(Employee).filter(Employee.email == payload.email.lower()).first()
    if existing_email:
        raise HTTPException(status_code=400, detail="Employee with this email already exists.")
    
    new_emp = Employee(
        id=payload.id,
        email=payload.email.lower(),
        name=payload.name,
        role=payload.role,
        is_manager=payload.is_manager,
        slack_id=payload.slack_id
    )
    cp_db.add(new_emp)
    cp_db.commit()
    return {"success": True, "id": new_emp.id}


@router.put("/employees/{employee_id}")
def dev_update_employee(
    employee_id: str,
    payload: EmployeePayload,
    cp_db: Session = Depends(get_controlplane_db),
    manager: Employee = Depends(get_current_employee)
):
    emp = cp_db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    
    emp.email = payload.email.lower()
    emp.name = payload.name
    emp.role = payload.role
    emp.is_manager = payload.is_manager
    emp.slack_id = payload.slack_id
    cp_db.commit()
    return {"success": True}


@router.delete("/employees/{employee_id}")
def dev_delete_employee(
    employee_id: str,
    cp_db: Session = Depends(get_controlplane_db),
    manager: Employee = Depends(get_current_employee)
):
    emp = cp_db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    cp_db.delete(emp)
    cp_db.commit()
    return {"success": True}


@router.get("/teammembers")
def dev_list_teammembers(
    db: Session = Depends(get_manager_db),
    manager: Employee = Depends(get_current_employee)
):
    members = db.query(TeamMember).order_by(TeamMember.name).all()
    return [
        {
            "id": m.id,
            "name": m.name,
            "role": m.role,
            "slack_handle": m.slack_handle,
            "outlook_email": m.outlook_email,
            "timezone": m.timezone,
            "created_at": str(m.created_at) if hasattr(m, "created_at") else None
        }
        for m in members
    ]


@router.post("/teammembers")
def dev_create_teammember(
    payload: TeamMemberPayload,
    db: Session = Depends(get_manager_db),
    manager: Employee = Depends(get_current_employee)
):
    existing = db.query(TeamMember).filter(TeamMember.id == payload.id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Team member with this ID already exists.")
    
    new_member = TeamMember(
        id=payload.id,
        name=payload.name,
        role=payload.role,
        slack_handle=payload.slack_handle,
        outlook_email=payload.outlook_email,
        timezone=payload.timezone
    )
    db.add(new_member)
    db.commit()
    return {"success": True, "id": new_member.id}


@router.put("/teammembers/{member_id}")
def dev_update_teammember(
    member_id: str,
    payload: TeamMemberPayload,
    db: Session = Depends(get_manager_db),
    manager: Employee = Depends(get_current_employee)
):
    member = db.query(TeamMember).filter(TeamMember.id == member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Team member not found")
    
    member.name = payload.name
    member.role = payload.role
    member.slack_handle = payload.slack_handle
    member.outlook_email = payload.outlook_email
    member.timezone = payload.timezone
    db.commit()
    return {"success": True}


@router.delete("/teammembers/{member_id}")
def dev_delete_teammember(
    member_id: str,
    db: Session = Depends(get_manager_db),
    manager: Employee = Depends(get_current_employee)
):
    member = db.query(TeamMember).filter(TeamMember.id == member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Team member not found")
    db.delete(member)
    db.commit()
    return {"success": True}


@router.get("/files")
def dev_get_files(
    manager: Employee = Depends(get_current_employee)
):
    md_dir = manager_dir(manager.id)
    user_md_path = md_dir / "user.md"
    memory_md_path = md_dir / "memory.md"
    events_md_path = md_dir / "events.md"
    
    user_content = ""
    memory_content = ""
    events_content = ""
    
    if os.path.exists(user_md_path):
        with open(user_md_path, "r", encoding="utf-8") as f:
            user_content = f.read()
            
    if os.path.exists(memory_md_path):
        with open(memory_md_path, "r", encoding="utf-8") as f:
            memory_content = f.read()
            
    if os.path.exists(events_md_path):
        with open(events_md_path, "r", encoding="utf-8") as f:
            events_content = f.read()
            
    return {
        "user_md": user_content,
        "memory_md": memory_content,
        "events_md": events_content
    }


@router.post("/files")
def dev_save_file(
    payload: FilePayload,
    manager: Employee = Depends(get_current_employee)
):
    if payload.filename not in ["user.md", "memory.md"]:
        raise HTTPException(status_code=400, detail="Only user.md and memory.md can be edited.")
        
    md_dir = manager_dir(manager.id)
    md_dir.mkdir(parents=True, exist_ok=True)
    file_path = md_dir / payload.filename
    
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(payload.content)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tenant-state")
def dev_get_tenant_state(
    db: Session = Depends(get_manager_db),
    manager: Employee = Depends(get_current_employee)
):
    workflows = db.query(Workflow).all()
    cron_jobs = db.query(CronJob).all()
    followup_agents = db.query(FollowupAgent).all()
    
    return {
        "workflows": [
            {
                "id": w.id,
                "name": w.name,
                "description": w.description,
                "task_type": w.task_type,
                "cron_expression": w.cron_expression,
                "status": w.status,
                "created_at": str(w.created_at)
            }
            for w in workflows
        ],
        "cron_jobs": [
            {
                "id": c.id,
                "workflow_id": c.workflow_id,
                "task_type": c.task_type,
                "prompt": c.prompt[:200] + "..." if len(c.prompt) > 200 else c.prompt,
                "schedule": c.schedule,
                "is_recurring": c.is_recurring,
                "next_run_at": str(c.next_run_at),
                "last_run_at": str(c.last_run_at) if c.last_run_at else None,
                "status": c.status,
                "created_at": str(c.created_at)
            }
            for c in cron_jobs
        ],
        "followup_agents": [
            {
                "id": f.id,
                "recipient_employee_id": f.recipient_employee_id,
                "scoped_project_ids": f.scoped_project_ids,
                "instructions": f.instructions,
                "status": f.status,
                "last_message_sent_at": str(f.last_message_sent_at) if f.last_message_sent_at else None,
                "last_message_received_at": str(f.last_message_received_at) if f.last_message_received_at else None,
                "chat_history": f.chat_history,
                "created_at": str(f.created_at)
            }
            for f in followup_agents
        ]
    }
