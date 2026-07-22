"""Home-dashboard backend (step 19 -- prompts/step_19_home_backend.md,
spec/architecture_v2_kb.md §2/§5): user-maintained todos CRUD + the Updates
panel's query over events, per-user db via get_manager_db like the other
manager-scoped routers.

There is no create-event endpoint on purpose -- only jobs (ingest/heartbeat,
later steps) create Event rows; the UI can only read them and flip
ui_state (dismiss/promote). Until those jobs land, /api/events returns an
honest empty state.
"""
import json
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
    db: Session = Depends(get_manager_db),
) -> dict:
    """The Updates panel query (spec §2): events at/above the severity
    threshold minus dismissed ones, plus promoted ones regardless of
    severity. include_dismissed=true is the "View All" screen -- dismissed
    rows come back (threshold still applies to non-promoted rows)."""
    visible = or_(Event.severity >= min_severity, Event.ui_state == "promoted")
    query = select(Event).where(visible)
    if not include_dismissed:
        query = query.where(or_(Event.ui_state != "dismissed", Event.ui_state == "promoted"))

    matching = db.scalars(
        query.order_by(Event.severity.desc(), Event.created_at.desc(), Event.id.desc())
    ).all()
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
