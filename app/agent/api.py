import json
from datetime import datetime
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select, delete
from pydantic import BaseModel

from app.database import ChatMessage, Workflow, CronJob, AgentActionLog
from app.controlplane.auth import get_current_employee
from app.controlplane.models import Employee
from app.tenancy.db import get_manager_db
from app.agent.harness import run_agent

router = APIRouter(prefix="/api/chat", tags=["Agent Chat"])

# -----------------------------------------------------------------------------
# Request & Response Pydantic Models
# -----------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str

class ChatMessageResponse(BaseModel):
    id: int
    role: str
    content: str
    tool_trace: Optional[List[Dict[str, Any]]] = None
    created_at: datetime

    class Config:
        from_attributes = True

class ChatResponse(BaseModel):
    reply: str
    tool_trace: List[Dict[str, Any]]
    created_at: datetime

# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------

@router.post("", response_model=ChatResponse)
def post_chat_message(
    payload: ChatRequest,
    db: Session = Depends(get_manager_db),
    manager: Employee = Depends(get_current_employee),
):
    """
    Submits a message to the Chief of Staff (COS) Agent. Handles unified chat
    compaction, dynamic profile/context building, and persists user/assistant turns.
    """
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    from app.agent.cos_agent import run_cos_agent
    result = run_cos_agent(db, manager.id, payload.message, channel="portal")
    return result



@router.get("/history", response_model=List[ChatMessageResponse])
def get_chat_history(limit: int = Query(50), db: Session = Depends(get_manager_db)):
    """
    Retrieves the chronological history of direct chat messages up to limit.
    """
    stmt = (
        select(ChatMessage)
        .order_by(ChatMessage.id.desc())
        .limit(limit)
    )
    history_reversed = db.scalars(stmt).all()
    history = list(reversed(history_reversed))
    
    resp = []
    for m in history:
        trace = None
        if m.tool_trace:
            try:
                trace = json.loads(m.tool_trace)
            except Exception:
                trace = []
                
        resp.append(ChatMessageResponse(
            id=m.id,
            role=m.role,
            content=m.content,
            tool_trace=trace,
            created_at=m.created_at
        ))
        
    return resp


@router.delete("/history")
def clear_chat_history(db: Session = Depends(get_manager_db)):
    """
    Deletes all records from the direct chat history table (useful for demo resets).
    """
    db.execute(delete(ChatMessage))
    db.commit()
    return {"success": True, "message": "Chat history cleared successfully."}


heartbeat_router = APIRouter(tags=["Agent Heartbeat"])

@heartbeat_router.post("/api/heartbeat/run")
def force_heartbeat(
    db: Session = Depends(get_manager_db),
    manager: Employee = Depends(get_current_employee),
):
    """
    Manually triggers the personal agent's heartbeat tick (candidate
    selection + tool-calling action loop) for the logged-in manager.
    """
    from app.projectkb.enums import JobName
    from app.projectkb.jobs.agent_heartbeat import run as run_agent_heartbeat
    from app.projectkb.scheduler import get_job_lock

    # agent_heartbeat is deliberately NOT in the scheduler's _JOBS (manual-
    # trigger-only), but it's reachable from BOTH this endpoint and the
    # debug page's generic job runner (app.api.dev_tools) -- same lock,
    # same non-blocking-acquire-then-409 guard against a double-run.
    lock = get_job_lock(JobName.AGENT_HEARTBEAT.value)
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="job already running")
    try:
        return run_agent_heartbeat(db, manager.id)
    finally:
        lock.release()

@heartbeat_router.get("/api/agent/notes")
def get_agent_notes(limit: int = Query(30), db: Session = Depends(get_manager_db)):
    """
    Retrieves a chronological list of recent agent memory notes.
    """
    from app.agent.notes import recent_notes
    notes = recent_notes(db, limit=limit)
    return [
        {
            "id": n.id,
            "created_at": n.created_at,
            "kind": n.kind,
            "subject_ref": n.subject_ref,
            "content": n.content
        } for n in notes
    ]


# -----------------------------------------------------------------------------
# Chief of Staff / Agents Tab CRUD APIs
# -----------------------------------------------------------------------------

class CreateWorkflowRequest(BaseModel):
    name: str
    cron_expression: str
    prompt: str
    description: Optional[str] = None
    task_type: str = "custom"


@heartbeat_router.get("/api/agent/workflows")
def list_workflows(
    db: Session = Depends(get_manager_db),
    manager: Employee = Depends(get_current_employee),
):
    wfs = db.scalars(select(Workflow).order_by(Workflow.id.desc())).all()
    return [
        {
            "id": w.id,
            "name": w.name,
            "description": w.description,
            "task_type": w.task_type,
            "cron_expression": w.cron_expression,
            "status": w.status,
            "created_at": w.created_at,
        }
        for w in wfs
    ]


@heartbeat_router.post("/api/agent/workflows")
def create_workflow(
    payload: CreateWorkflowRequest,
    db: Session = Depends(get_manager_db),
    manager: Employee = Depends(get_current_employee),
):
    from app.agent.cos_agent import create_workflow_handler
    res = create_workflow_handler(
        db,
        manager.id,
        run_context={},
        name=payload.name,
        cron_expression=payload.cron_expression,
        prompt=payload.prompt,
        description=payload.description,
        task_type=payload.task_type
    )
    return res


@heartbeat_router.post("/api/agent/workflows/{id}/toggle")
def toggle_workflow(
    id: int,
    db: Session = Depends(get_manager_db),
    manager: Employee = Depends(get_current_employee),
):
    wf = db.get(Workflow, id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    # Toggle workflow status
    new_status = "paused" if wf.status == "active" else "active"
    wf.status = new_status
    
    # Mirror status toggle to the workflow's recurring crons
    crons = db.scalars(select(CronJob).where(CronJob.workflow_id == id)).all()
    for c in crons:
        c.status = "paused" if new_status == "paused" else "pending"
        
    db.commit()
    return {"id": id, "status": wf.status}


@heartbeat_router.delete("/api/agent/workflows/{id}")
def delete_workflow(
    id: int,
    db: Session = Depends(get_manager_db),
    manager: Employee = Depends(get_current_employee),
):
    wf = db.get(Workflow, id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
        
    # Delete associated crons first
    db.execute(delete(CronJob).where(CronJob.workflow_id == id))
    db.delete(wf)
    db.commit()
    return {"status": "deleted", "id": id}


@heartbeat_router.get("/api/agent/followups")
def list_followups(
    db: Session = Depends(get_manager_db),
    manager: Employee = Depends(get_current_employee),
):
    from app.agent.cos_agent import list_active_followups_handler
    return list_active_followups_handler(db, manager.id, run_context={})


@heartbeat_router.get("/api/agent/crons")
def list_crons(
    db: Session = Depends(get_manager_db),
    manager: Employee = Depends(get_current_employee),
):
    from app.agent.cos_agent import list_cron_jobs_handler
    return list_cron_jobs_handler(db, manager.id, run_context={})


@heartbeat_router.get("/api/agent/actions")
def list_actions(
    db: Session = Depends(get_manager_db),
    manager: Employee = Depends(get_current_employee),
):
    logs = db.scalars(select(AgentActionLog).order_by(AgentActionLog.created_at.desc()).limit(50)).all()
    return [
        {
            "id": l.id,
            "action_type": l.action_type,
            "ref_key": l.ref_key,
            "detail": l.detail,
            "created_at": l.created_at,
        }
        for l in logs
    ]


