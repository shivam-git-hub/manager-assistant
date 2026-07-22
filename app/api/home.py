"""Home-dashboard backend (step 19 -- prompts/step_19_home_backend.md,
spec/architecture_v2_kb.md §2/§5): user-maintained todos CRUD + the Updates
panel's query over events, per-user db via get_manager_db like the other
manager-scoped routers.

There is no create-event endpoint on purpose -- only jobs (ingest/heartbeat,
later steps) create Event rows; the UI can only read them and flip
ui_state (dismiss/promote/approve/reject). Until those jobs land,
/api/events returns an honest empty state.
"""
import json
import logging
import uuid
from datetime import datetime
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from app import timeservice
from app.controlplane.auth import get_current_manager
from app.controlplane.models import Manager
from app.database import Event, Todo, UnifiedMessage
from app.tenancy.db import get_manager_db

logger = logging.getLogger(__name__)

# ui_state values that mean "resolved, stop showing by default" -- dismiss
# (any event) and approve/reject (request events only, step 24). Distinct
# from "promoted", which always overrides regardless of severity/resolution.
RESOLVED_UI_STATES = {"dismissed", "approved", "rejected"}

router = APIRouter(prefix="/api", tags=["Home Dashboard"])


# ────────────────────────────────────────────────────────
# Schemas
# ────────────────────────────────────────────────────────

class TodoCreateIn(BaseModel):
    text: str
    due: Optional[datetime] = None


class TodoPatchIn(BaseModel):
    text: Optional[str] = None
    due: Optional[datetime] = None
    status: Optional[Literal["open", "done"]] = None


def _todo_dict(t: Todo) -> dict:
    return {
        "id": t.id,
        "text": t.text,
        "due": t.due.isoformat() if t.due else None,
        "status": t.status,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


def _event_dict(e: Event) -> dict:
    return {
        "id": e.id,
        "type": e.type,
        "severity": e.severity,
        "title": e.title,
        "body": e.body,
        "project_ids": json.loads(e.project_ids) if e.project_ids else [],
        "task_ids": json.loads(e.task_ids) if e.task_ids else [],
        "claim_ids": json.loads(e.claim_ids) if e.claim_ids else [],
        "general": e.general,
        "ui_state": e.ui_state,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


# ────────────────────────────────────────────────────────
# TODOs (user-maintained, no LLM)
# ────────────────────────────────────────────────────────

@router.get("/todos")
def list_todos(
    status_filter: Optional[Literal["open", "done"]] = Query(None, alias="status"),
    db: Session = Depends(get_manager_db),
) -> List[dict]:
    query = select(Todo).order_by(Todo.created_at.desc(), Todo.id.desc())
    if status_filter is not None:
        query = query.where(Todo.status == status_filter)
    return [_todo_dict(t) for t in db.scalars(query).all()]


@router.post("/todos", status_code=status.HTTP_201_CREATED)
def create_todo(payload: TodoCreateIn, db: Session = Depends(get_manager_db)) -> dict:
    todo = Todo(id=uuid.uuid4().hex, text=payload.text, due=payload.due)
    db.add(todo)
    db.commit()
    db.refresh(todo)
    return _todo_dict(todo)


@router.patch("/todos/{todo_id}")
def patch_todo(todo_id: str, payload: TodoPatchIn, db: Session = Depends(get_manager_db)) -> dict:
    todo = db.get(Todo, todo_id)
    if todo is None:
        raise HTTPException(status_code=404, detail="Todo not found")
    if payload.text is not None:
        todo.text = payload.text
    if payload.due is not None:
        todo.due = payload.due
    if payload.status is not None:
        todo.status = payload.status
    todo.updated_at = timeservice.now_ist()
    db.commit()
    db.refresh(todo)
    return _todo_dict(todo)


@router.delete("/todos/{todo_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_todo(todo_id: str, db: Session = Depends(get_manager_db)) -> None:
    todo = db.get(Todo, todo_id)
    if todo is None:
        raise HTTPException(status_code=404, detail="Todo not found")
    db.delete(todo)
    db.commit()


# ────────────────────────────────────────────────────────
# Events / Updates panel
# ────────────────────────────────────────────────────────

@router.get("/events")
def list_events(
    min_severity: int = Query(1, ge=0, le=3),
    limit: int = Query(50, ge=1, le=200),
    include_dismissed: bool = False,
    project_id: Optional[str] = None,
    db: Session = Depends(get_manager_db),
) -> dict:
    """The Updates panel query (spec §2): events at/above the severity
    threshold minus resolved ones (dismissed, or approved/rejected --
    step 24 requests), plus promoted ones regardless of severity.
    include_dismissed=true is the "View All" screen -- resolved rows come
    back too (threshold still applies to non-promoted rows; the param
    name predates approve/reject but keeping it avoids a frontend/backend
    rename for the same "show everything" toggle).
    project_id narrows to events tagged with that project (the project
    dashboard's panels, step 21) -- Python-side filter over the JSON list
    column, fine at this scale."""
    visible = or_(Event.severity >= min_severity, Event.ui_state == "promoted")
    query = select(Event).where(visible)
    if not include_dismissed:
        query = query.where(or_(Event.ui_state.notin_(RESOLVED_UI_STATES), Event.ui_state == "promoted"))

    matching = db.scalars(
        query.order_by(Event.severity.desc(), Event.created_at.desc(), Event.id.desc())
    ).all()
    if project_id is not None:
        matching = [
            e for e in matching
            if e.project_ids and project_id in json.loads(e.project_ids)
        ]
    returned = matching[:limit]
    return {
        "events": [_event_dict(e) for e in returned],
        "total_matching": len(matching),
        "max_severity": max((e.severity for e in returned), default=None),
    }


# ────────────────────────────────────────────────────────
# Manual inputs (MoMs, pasted notes) -- spec §3.2
# ────────────────────────────────────────────────────────

class ManualMessageIn(BaseModel):
    content: str
    subject: Optional[str] = None
    # Optional tagging hint for the future ingest/heartbeat jobs -- a MoM
    # added from a project's own page already knows its project.
    project_id: Optional[str] = None


@router.post("/messages/manual", status_code=status.HTTP_201_CREATED)
def add_manual_message(
    payload: ManualMessageIn,
    manager: Manager = Depends(get_current_manager),
    db: Session = Depends(get_manager_db),
) -> dict:
    """Minutes of meetings and pasted notes enter as unified_messages rows
    with source="manual" and flow through the exact same claim -> event
    pipeline as mail/Slack -- no side channel, same citations (spec §3.2).
    The project page's "Add MoM" box posts here."""
    if not payload.content.strip():
        raise HTTPException(status_code=400, detail="content must not be empty")
    now = timeservice.now_ist()
    msg = UnifiedMessage(
        platform_msg_id=f"manual_{uuid.uuid4().hex}",
        source="manual",
        direction="inbound",
        sender_raw_id=manager.email,
        sender_mapped_name=manager.name,
        receiver_raw_id="pulse",
        channel_raw_id="manual",
        thread_id=None,
        subject=payload.subject,
        content=payload.content,
        timestamp=now,
        created_at=now,
        is_processed=False,
        raw_metadata=json.dumps({"project_id": payload.project_id}) if payload.project_id else None,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return {"id": msg.id, "platform_msg_id": msg.platform_msg_id, "source": msg.source}


def _set_event_state(db: Session, event_id: str, ui_state: str) -> dict:
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    event.ui_state = ui_state
    db.commit()
    db.refresh(event)
    return _event_dict(event)


@router.post("/events/{event_id}/dismiss")
def dismiss_event(event_id: str, db: Session = Depends(get_manager_db)) -> dict:
    return _set_event_state(db, event_id, "dismissed")


@router.post("/events/{event_id}/promote")
def promote_event(event_id: str, db: Session = Depends(get_manager_db)) -> dict:
    return _set_event_state(db, event_id, "promoted")


@router.post("/events/{event_id}/approve")
def approve_event(event_id: str, db: Session = Depends(get_manager_db)) -> dict:
    """Requests-panel Approve (step 24, per Shivam 2026-07-23): scoped to
    type="request" events only -- blockers/conflicts get their own resolve
    semantics later, not this. The status flip is always applied and
    always succeeds; if the project fan-out job (heartbeat.py's
    request->task linkage) resolved this request to specific task(s),
    those Tasks also get marked done -- best-effort, deliberately AFTER
    the event's own commit. A stale/deleted task, a locked project db, or
    any other mutation failure is logged and swallowed rather than
    surfaced as a 500: the approval itself must never appear to fail (and
    become confusingly un-retryable, since a second call would just 400
    on "already approved" semantics if we ever add that) just because a
    downstream task couldn't be updated."""
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    if event.type != "request":
        raise HTTPException(status_code=400, detail="Only request events can be approved")

    event.ui_state = "approved"
    db.commit()
    db.refresh(event)

    task_ids = json.loads(event.task_ids) if event.task_ids else []
    project_ids = json.loads(event.project_ids) if event.project_ids else []
    if task_ids and project_ids:
        from app.projects.db import get_project_session
        from app.projects.models import Task

        for task_id in task_ids:
            for project_id in project_ids:
                try:
                    project_session = get_project_session(project_id)
                    try:
                        task = project_session.get(Task, task_id)
                        if task is None:
                            continue  # not in this project, try the next one
                        task.status = "done"
                        task.updated_at = timeservice.now_ist()
                        project_session.commit()
                        break  # found and updated -- stop searching projects for this task_id
                    finally:
                        project_session.close()
                except Exception:
                    logger.exception(
                        f"[home] approved event={event_id}: failed marking task={task_id} "
                        f"done in project={project_id}"
                    )

    return _event_dict(event)


@router.post("/events/{event_id}/reject")
def reject_event(event_id: str, db: Session = Depends(get_manager_db)) -> dict:
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    if event.type != "request":
        raise HTTPException(status_code=400, detail="Only request events can be rejected")
    return _set_event_state(db, event_id, "rejected")
