import os
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from pathlib import Path

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app import timeservice
from app.database import Event, Claim
from app.controlplane.models import (
    SessionLocal as ControlPlaneSessionLocal,
    Project as RegistryProject,
    Employee,
    get_member_list,
)
from app.projects.db import get_project_session
from app.projects.models import Task, Conflict, HealthLog
from app.projects.paths import project_md_path, summary_md_path, notes_md_path
from app.tenancy.paths import manager_memory_md_path

logger = logging.getLogger(__name__)

EVENT_TYPE_DOCS = (
    ("status_update", "routine progress, no action implied"),
    ("blocker", "someone is stuck -- severity floor 1"),
    ("clarification", "a question needing an answer"),
    ("commitment", "someone committed to doing something by when"),
    ("request", "an ask that can be approved/rejected"),
    ("conflict", "two claims contradict -- NEVER auto-resolved"),
    ("fyi", "informational, no action implied"),
)

KB_CONVENTIONS_TEXT = (
    "## KB CONVENTIONS\n"
    "Event types:\n"
    + "\n".join(f"- {t}: {d}" for t, d in EVENT_TYPE_DOCS)
    + "\n\n"
    "Severity is 0-3 (0=routine, 3=critical); blocker/clarification are code-floored "
    "to at least 1, never model-supplied below that.\n"
    "`general` on an event is DERIVED from project_ids being empty -- never set it "
    "yourself, it falls out of which project_ids you tag."
)


@dataclass(frozen=True)
class ContextBudget:
    max_total_chars: int = 30000
    max_memory_chars: int = 5000
    max_project_md_chars: int = 3000
    max_summary_md_chars: int = 5000
    max_notes_md_chars: int = 3000
    max_events_chars: int = 3000
    max_events_count: int = 20


def _read_if_exists(path: Any, max_chars: int) -> str:
    if not path or not Path(path).exists():
        return ""
    try:
        content = Path(path).read_text(encoding="utf-8")
        if len(content) > max_chars:
            return content[:max_chars] + "\n... [content truncated to fit budget]"
        return content
    except Exception as e:
        logger.warning(f"Error reading path {path}: {e}")
        return ""


def build_kb_context(
    db: Session,
    manager_id: str,
    *,
    budget: ContextBudget = ContextBudget(),
    include_memory: bool = True,
    include_conventions: bool = True,
) -> str:
    now = timeservice.now_ist()
    one_day_ago = now - timedelta(days=1)

    # 1. OWNER DETAILS
    cdb = ControlPlaneSessionLocal()
    employee_map = {}
    try:
        manager = cdb.get(Employee, manager_id)
        # Cache employees for teammate lookup
        all_employees = cdb.query(Employee).all()
        for emp in all_employees:
            employee_map[emp.id] = emp
    finally:
        cdb.close()

    owner_lines = ["## OWNER DETAILS"]
    if manager:
        owner_lines.append(f"- Name: {manager.name}")
        owner_lines.append(f"- Role: {manager.role or 'no role'}")
        owner_lines.append(f"- Slack ID: {manager.slack_id or 'N/A'}")
        owner_lines.append(f"- Outlook ID: {manager.email or manager.outlook_mailbox_email or 'N/A'}")
    else:
        owner_lines.append(f"- ID: {manager_id}")
    owner_section = "\n".join(owner_lines)

    # 2. MEMORY
    memory_section = ""
    if include_memory:
        mem_path = manager_memory_md_path(manager_id)
        mem_content = _read_if_exists(mem_path, budget.max_memory_chars)
        if mem_content.strip():
            memory_section = f"## YOUR DURABLE MEMORY (memory.md)\n{mem_content}"

    # 3. KB CONVENTIONS
    conventions_section = ""
    if include_conventions:
        conventions_section = KB_CONVENTIONS_TEXT

    # 4. PROJECTS DETAILS
    project_sections = []
    
    # Re-open control plane to query visible projects
    cdb = ControlPlaneSessionLocal()
    try:
        owned = cdb.query(RegistryProject).filter(RegistryProject.manager_user_id == manager_id).all()
        owned_ids = {p.id for p in owned}
        member_ids = {
            p.id for p in cdb.query(RegistryProject).all()
            if any(m["employee_id"] == manager_id for m in get_member_list(p))
        }
        all_ids = owned_ids | member_ids
        projects = cdb.query(RegistryProject).filter(RegistryProject.id.in_(all_ids)).all() if all_ids else []
        
        # Load all blockers and conflicts from manager's DB to filter per project
        db_events = db.query(Event).filter(Event.ui_state != "dismissed").all()
        blockers = [e for e in db_events if e.type == "blocker"]
        conflict_events = [e for e in db_events if e.type == "conflict"]

        for p in projects:
            p_lines = []
            p_lines.append(f"============================================================")
            p_lines.append(f"PROJECT: {p.name} (id={p.id})")
            p_lines.append(f"============================================================")
            p_lines.append(f"Description: {p.description or 'No description'}")
            
            # Teammates
            members = get_member_list(p)
            teammate_lines = []
            for m in members:
                emp_id = m.get("employee_id")
                role = m.get("role") or "Member"
                emp_obj = employee_map.get(emp_id)
                if emp_obj:
                    teammate_lines.append(f"- Name: {emp_obj.name} (ID: {emp_id}, Role: {emp_obj.role or role})")
                else:
                    teammate_lines.append(f"- ID: {emp_id} (Role: {role})")
            p_lines.append("\nTeammates:")
            p_lines.extend(teammate_lines if teammate_lines else ["- (none)"])

            # project.md / summary.md / notes.md
            proj_md = _read_if_exists(project_md_path(p.id), budget.max_project_md_chars)
            summ_md = _read_if_exists(summary_md_path(p.id), budget.max_summary_md_chars)
            nt_md = _read_if_exists(notes_md_path(p.id), budget.max_notes_md_chars)
            
            p_lines.append("\nProject Documents:")
            p_lines.append("--- project.md ---")
            p_lines.append(proj_md.strip() if proj_md.strip() else "(empty)")
            p_lines.append("\n--- summary.md ---")
            p_lines.append(summ_md.strip() if summ_md.strip() else "(empty)")
            p_lines.append("\n--- notes.md ---")
            p_lines.append(nt_md.strip() if nt_md.strip() else "(empty)")

            # Project DB query
            pdb = get_project_session(p.id)
            try:
                # Open Tasks
                open_tasks_query = pdb.query(Task).filter(Task.status != "done")
                open_tasks_count = open_tasks_query.count()
                recent_tasks = open_tasks_query.order_by(Task.created_at.desc()).limit(5).all()
                
                # Conflicts
                open_conflicts_query = pdb.query(Conflict).filter(Conflict.status == "open")
                open_conflicts_count = open_conflicts_query.count()
                recent_conflicts = open_conflicts_query.order_by(Conflict.created_at.desc()).limit(5).all()

                # Health score
                latest_health = pdb.query(HealthLog).order_by(HealthLog.ts.desc()).first()
                health_score = (latest_health.base_score + latest_health.llm_adjustment) if latest_health else None
                health_reason = latest_health.reason if latest_health else "N/A"
            finally:
                pdb.close()

            # Filter blockers
            p_blockers = []
            for b in blockers:
                try:
                    pids = json.loads(b.project_ids or "[]")
                    if p.id in pids:
                        p_blockers.append(b)
                except Exception:
                    pass
            p_blockers_count = len(p_blockers)
            recent_p_blockers = sorted(
                p_blockers, 
                key=lambda x: x.occurred_at or x.created_at, 
                reverse=True
            )[:5]

            p_lines.append("\nProject Backlog & Metrics:")
            p_lines.append(f"- Open Tasks Count: {open_tasks_count}")
            if recent_tasks:
                p_lines.append("  Recent Open Tasks:")
                for t in recent_tasks:
                    p_lines.append(f"    * ID: {t.id} | Title: {t.title} | Status: {t.status} | Priority: {t.priority} | Assignee: {t.assignee_employee_id or 'Unassigned'}")
            
            p_lines.append(f"- Active Blockers Count: {p_blockers_count}")
            if recent_p_blockers:
                p_lines.append("  Recent Active Blockers:")
                for b in recent_p_blockers:
                    p_lines.append(f"    * Severity: {b.severity} | Title: {b.title} | Body: {b.body or 'None'} | Occurred: {b.occurred_at or b.created_at}")

            p_lines.append(f"- Active Conflicts Count: {open_conflicts_count}")
            if recent_conflicts:
                p_lines.append("  Recent Active Conflicts:")
                for c in recent_conflicts:
                    p_lines.append(f"    * ID: {c.id} | Claim A: '{c.claim_a_ref}' | Claim B: '{c.claim_b_ref}' | Severity: {c.severity} | Created At: {c.created_at}")

            # Health Score
            health_str = f"{health_score}" if health_score is not None else "N/A"
            p_lines.append(f"- Project Health Score: {health_str} (Reason: {health_reason})")
            p_lines.append("\n")

            project_sections.append("\n".join(p_lines))
    finally:
        cdb.close()

    projects_section = "## PROJECTS DETAIL\n" + "\n\n".join(project_sections) if project_sections else "## PROJECTS DETAIL\n- (none)"

    # 5. ALL EVENTS FOR LAST DAY FOR THE MANAGER
    last_day_events = (
        db.query(Event)
        .filter(func.coalesce(Event.occurred_at, Event.created_at) >= one_day_ago)
        .order_by(func.coalesce(Event.occurred_at, Event.created_at).desc())
        .limit(budget.max_events_count)
        .all()
    )
    
    event_lines = []
    for e in last_day_events:
        ts = e.occurred_at or e.created_at
        event_lines.append(
            f"- [{ts.strftime('%Y-%m-%d %H:%M:%S')}] [{e.type.upper()}/severity={e.severity}] "
            f"{e.title}: {e.body or 'No detail'}"
        )
    
    events_content = "\n".join(event_lines) if event_lines else "(No events occurred in the last 24 hours)"
    if len(events_content) > budget.max_events_chars:
        events_content = events_content[:budget.max_events_chars] + "\n... [events truncated to fit budget]"
    events_section = f"## RECENT EVENTS (LAST 24 HOURS)\n{events_content}"

    # 6. NOW TIMESTAMP
    now_section = f"## NOW\nCurrent date and time: {now.strftime('%Y-%m-%d %H:%M:%S')} IST ({now.strftime('%A')})"

    # Assembling all sections
    all_parts = [
        owner_section,
        memory_section,
        conventions_section,
        projects_section,
        events_section,
        now_section
    ]
    # Filter empty sections
    all_parts = [p.strip() for p in all_parts if p.strip()]
    final_text = "\n\n".join(all_parts)

    # 7. ENFORCE OVERALL BUDGET LIMIT (30,000 characters)
    if len(final_text) > budget.max_total_chars:
        # If we exceed the budget, truncate and append truncation warning + NOW section at the end
        allowed_len = budget.max_total_chars - len(now_section) - 100
        truncated_body = final_text[:allowed_len]
        final_text = (
            truncated_body + 
            "\n\n... [context truncated to fit overall budget of 30,000 chars]\n\n" + 
            now_section
        )

    return final_text
