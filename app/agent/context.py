"""Context assembly for the personal agent -- shared by both the agent
heartbeat job and the chat entry points (dashboard + Slack DM). Pulls from
pipeline output: summary.md/events.md/memory.md, Event rows, and the
projects registry.
"""
import json
from datetime import timedelta
from typing import List

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app import timeservice
from app.config import AGENT_MEETING_SCAN_LOOKBACK_HOURS, AGENT_MEETING_SCAN_MAX_CLAIMS
from app.database import Claim, Event


def _read_if_exists(path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _tail(text: str, max_chars: int = 1500) -> str:
    return text[-max_chars:] if len(text) > max_chars else text


def visible_projects_for_manager(manager_id: str) -> List[dict]:
    """Owned + member projects (read scope -- broader than
    app.agent.select.owned_projects_for_manager's write scope)."""
    from app.controlplane.models import (
        SessionLocal as ControlPlaneSessionLocal,
        Project as RegistryProject,
        Employee,
        get_member_list,
    )

    cdb = ControlPlaneSessionLocal()
    try:
        manager = cdb.get(Employee, manager_id)
        if manager is None:
            return []
        owned = cdb.query(RegistryProject).filter(RegistryProject.manager_user_id == manager_id).all()
        owned_ids = {p.id for p in owned}
        member_ids = {
            p.id for p in cdb.query(RegistryProject).all()
            if any(m["employee_id"] == manager_id for m in get_member_list(p))
        }
        all_ids = owned_ids | member_ids
        if not all_ids:
            return []
        projects = cdb.query(RegistryProject).filter(RegistryProject.id.in_(all_ids)).all()
        return [{"id": p.id, "name": p.name, "kind": p.kind, "is_manager": p.id in owned_ids} for p in projects]
    finally:
        cdb.close()


def team_roster_for_manager(manager_id: str) -> List[dict]:
    """Employees who are members of any project this manager touches --
    not the whole org (keeps the roster relevant + small)."""
    from app.controlplane.models import (
        SessionLocal as ControlPlaneSessionLocal,
        Employee,
        Project as RegistryProject,
        get_member_list,
    )

    project_ids = [p["id"] for p in visible_projects_for_manager(manager_id)]
    if not project_ids:
        return []
    cdb = ControlPlaneSessionLocal()
    try:
        projects = cdb.query(RegistryProject).filter(RegistryProject.id.in_(project_ids)).all()
        employee_ids = {m["employee_id"] for p in projects for m in get_member_list(p)}
        if not employee_ids:
            return []
        rows = cdb.query(Employee).filter(Employee.id.in_(employee_ids)).all()
        return [{"id": e.id, "name": e.name, "role": e.role, "has_slack": bool(e.slack_id)} for e in rows]
    finally:
        cdb.close()


def build_agent_context(db: Session, manager_id: str) -> str:
    from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Employee
    from app.tenancy.paths import manager_memory_md_path
    from app.projects.paths import summary_md_path, events_md_path

    now = timeservice.now_ist()

    cdb = ControlPlaneSessionLocal()
    try:
        manager = cdb.get(Employee, manager_id)
    finally:
        cdb.close()
    manager_name = manager.name if manager else manager_id

    projects = visible_projects_for_manager(manager_id)
    project_lines = []
    for p in projects:
        role = "manager" if p["is_manager"] else "member"
        summary = _tail(_read_if_exists(summary_md_path(p["id"])))
        events_tail = _tail(_read_if_exists(events_md_path(p["id"])), max_chars=600)
        project_lines.append(
            f"--- Project '{p['name']}' (id={p['id']}, {p['kind']}, you are {role}) ---\n"
            f"Summary:\n{summary or '(no synthesis yet)'}\n"
            f"Recent events:\n{events_tail or '(none)'}"
        )
    projects_block = "\n\n".join(project_lines) if project_lines else "No projects."

    roster = team_roster_for_manager(manager_id)
    roster_lines = [f"- {r['id']}: {r['name']} ({r['role'] or 'no role'}){' [slack]' if r['has_slack'] else ''}" for r in roster]
    roster_block = "\n".join(roster_lines) if roster_lines else "No teammates found."

    memory_md = _read_if_exists(manager_memory_md_path(manager_id))

    today = now.date()
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # "Today" means EITHER it happened today (occurred_at) OR we only
    # noticed it today (created_at, e.g. an unsourced event, or a
    # backdated one whose real occurrence was yesterday but only just got
    # judged) -- an OR of both, not a straight COALESCE-only filter, so a
    # late-arriving event about yesterday doesn't silently vanish (there's
    # no "yesterday" panel to catch it instead).
    todays_events = (
        db.query(Event)
        .filter(or_(func.coalesce(Event.occurred_at, Event.created_at) >= start_of_day, Event.created_at >= start_of_day))
        .order_by(func.coalesce(Event.occurred_at, Event.created_at).desc())
        .limit(30)
        .all()
    )
    event_lines = [f"- [{e.type}/sev{e.severity}] {e.title}: {e.body or ''}" for e in todays_events]
    events_block = "\n".join(event_lines) if event_lines else "No events today."

    lookback = now - timedelta(hours=AGENT_MEETING_SCAN_LOOKBACK_HOURS)
    recent_claims = (
        db.query(Claim)
        .filter(Claim.created_at >= lookback)
        .order_by(Claim.created_at.desc())
        .limit(AGENT_MEETING_SCAN_MAX_CLAIMS)
        .all()
    )
    claim_lines = [f"- ({c.created_at.strftime('%Y-%m-%d %H:%M')}) {c.text}" for c in recent_claims]
    claims_block = "\n".join(claim_lines) if claim_lines else "No recent claims."

    return (
        f"### CURRENT SIM TIME: {now.strftime('%Y-%m-%d %H:%M:%S')} IST\n\n"
        f"### OWNER: {manager_name}\n\n"
        f"### YOUR PROJECTS:\n{projects_block}\n\n"
        f"### TEAM ROSTER (people you can message on slack):\n{roster_block}\n\n"
        f"### YOUR DURABLE MEMORY:\n{memory_md or '(empty)'}\n\n"
        f"### TODAY'S EVENTS:\n{events_block}\n\n"
        f"### RECENT CONVERSATION SNIPPETS (watch for meeting mentions):\n{claims_block}"
    )
