"""Deterministic candidate selection for the personal agent's heartbeat
(step 28 -- prompts/step_28_personal_agent.md §3). Same standing rule as
ingestion/heartbeat/dream: code decides WHAT might be due, the LLM only
drafts WHAT TO SAY and (for meeting inference) judges natural-language
mentions. Nothing here calls an LLM.
"""
import json
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app import timeservice
from app.config import CONFLICT_ESCALATE_AFTER_HOURS, PRE_MEETING_BRIEF_WINDOW_HOURS
from app.database import AgentActionLog, Event, Meeting


def owned_projects_for_manager(manager_id: str) -> List[dict]:
    """Projects this manager actually owns (write authority) -- manager-only
    project truth, same rule app.projectkb.project_scope enforces for the
    heartbeat/dream jobs. Returns plain dicts (id, name) so callers don't
    need to keep a control-plane session open."""
    from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Project as RegistryProject

    cdb = ControlPlaneSessionLocal()
    try:
        rows = cdb.query(RegistryProject).filter(RegistryProject.manager_user_id == manager_id).all()
        return [{"id": p.id, "name": p.name, "kind": p.kind} for p in rows]
    finally:
        cdb.close()


@dataclass
class Candidate:
    kind: str  # "pre_meeting_brief" | "followup" | "conflict_contact" | "conflict_escalate"
    ref_key: str  # AgentActionLog.ref_key for this candidate
    summary: str  # human-readable one-liner for the prompt
    data: Dict[str, Any] = field(default_factory=dict)


def _already_logged(db: Session, action_type: str, ref_key: str) -> bool:
    return (
        db.query(AgentActionLog)
        .filter(AgentActionLog.action_type == action_type, AgentActionLog.ref_key == ref_key)
        .first()
        is not None
    )


def _select_pre_meeting_briefs(db: Session, now) -> List[Candidate]:
    window_end = now + timedelta(hours=PRE_MEETING_BRIEF_WINDOW_HOURS)
    meetings = (
        db.query(Meeting)
        .filter(Meeting.status == "scheduled")
        .filter(Meeting.brief_sent_at.is_(None))
        .filter(Meeting.starts_at >= now, Meeting.starts_at <= window_end)
        .all()
    )
    out = []
    for m in meetings:
        try:
            attendees = json.loads(m.attendees) if m.attendees else []
        except Exception:
            attendees = []
        minutes_away = int((m.starts_at - now).total_seconds() / 60)
        out.append(
            Candidate(
                kind="pre_meeting_brief",
                ref_key=f"meeting:{m.id}",
                summary=f"Meeting '{m.title}' starts in {minutes_away} min (at {m.starts_at.strftime('%H:%M')}), attendees: {attendees}",
                data={"meeting_id": m.id, "title": m.title, "starts_at": m.starts_at.isoformat(), "attendees": attendees},
            )
        )
    return out


def _select_followups(db: Session, manager_id: str, now) -> List[Candidate]:
    from app.projects.db import get_project_session
    from app.projects.models import Task

    today_str = now.date().isoformat()
    out: List[Candidate] = []
    for proj in owned_projects_for_manager(manager_id):
        pdb = get_project_session(proj["id"])
        try:
            tasks = (
                pdb.query(Task)
                # pending_approval tasks are agent-drafted and awaiting the
                # MANAGER's approval (app.projectkb.jobs.heartbeat always
                # forces new agent drafts into this status) -- there's no
                # approved assignee action to nudge about yet, so they're
                # excluded the same way dream.py excludes them from "open"
                # task counting (app/projectkb/jobs/dream.py:196).
                .filter(Task.status.notin_(["done", "pending_approval"]))
                .filter((Task.status == "blocked") | ((Task.due.isnot(None)) & (Task.due < now)))
                .all()
            )
        finally:
            pdb.close()
        for t in tasks:
            ref_key = f"task:{proj['id']}:{t.id}:{today_str}"
            if _already_logged(db, "followup", ref_key):
                continue
            reason = "blocked" if t.status == "blocked" else f"overdue (due {t.due.strftime('%Y-%m-%d')})"
            out.append(
                Candidate(
                    kind="followup",
                    ref_key=ref_key,
                    summary=f"Task '{t.title}' in project '{proj['name']}' is {reason}, assignee_employee_id={t.assignee_employee_id}",
                    data={
                        "project_id": proj["id"],
                        "project_name": proj["name"],
                        "task_id": t.id,
                        "title": t.title,
                        "status": t.status,
                        "assignee_employee_id": t.assignee_employee_id,
                    },
                )
            )
    return out


def _select_conflicts(db: Session, manager_id: str, now) -> List[Candidate]:
    owned_ids = {p["id"] for p in owned_projects_for_manager(manager_id)}
    if not owned_ids:
        return []

    from app.api.home import RESOLVED_UI_STATES

    events = (
        db.query(Event)
        .filter(Event.type == "conflict")
        .filter(Event.ui_state.notin_(RESOLVED_UI_STATES))
        .all()
    )
    out: List[Candidate] = []
    for e in events:
        project_ids = json.loads(e.project_ids) if e.project_ids else []
        if not owned_ids.intersection(project_ids):
            continue

        contact_key = f"event:{e.id}"
        contact_row = (
            db.query(AgentActionLog)
            .filter(AgentActionLog.action_type == "conflict_contact", AgentActionLog.ref_key == contact_key)
            .first()
        )
        if contact_row is None:
            out.append(
                Candidate(
                    kind="conflict_contact",
                    ref_key=contact_key,
                    summary=f"Conflict event '{e.title}': {e.body or ''}",
                    data={"event_id": e.id, "title": e.title, "body": e.body, "project_ids": project_ids},
                )
            )
            continue

        escalate_key = f"event:{e.id}"
        already_escalated = _already_logged(db, "conflict_escalate", escalate_key)
        hours_since_contact = (now - contact_row.created_at).total_seconds() / 3600.0
        if not already_escalated and hours_since_contact >= CONFLICT_ESCALATE_AFTER_HOURS:
            out.append(
                Candidate(
                    kind="conflict_escalate",
                    ref_key=escalate_key,
                    summary=f"Conflict event '{e.title}' still open {hours_since_contact:.0f}h after both parties were contacted: {e.body or ''}",
                    data={"event_id": e.id, "title": e.title, "body": e.body, "project_ids": project_ids},
                )
            )
    return out


def build_candidates(db: Session, manager_id: str) -> List[Candidate]:
    now = timeservice.now_ist()
    candidates: List[Candidate] = []
    candidates.extend(_select_pre_meeting_briefs(db, now))
    candidates.extend(_select_followups(db, manager_id, now))
    candidates.extend(_select_conflicts(db, manager_id, now))
    return candidates
