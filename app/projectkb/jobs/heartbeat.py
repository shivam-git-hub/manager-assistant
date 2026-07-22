"""Heartbeat job, user-level half (spec/architecture_v2_kb.md §4.3,
prompts/step_23_heartbeat_user.md): claims -> typed, tagged, severity-
scored events. Runs every HEARTBEAT_INTERVAL_MINUTES per manager.

The project-scoped fan-out (task status transitions, drafted subtasks,
archive writes) is step 24, deliberately a separate module -- this file
only produces Event rows.

Deliberate scope cut for THIS step (per the user's own sequencing: agent
context-engineering + tool-based KB inspection is designed later,
alongside testing this very job): a single structured-output smart-model
call, not the full tool-loop harness. Don't "fix" this into a tool loop
without that design conversation happening first.
"""
import json
import logging
import uuid
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import HEARTBEAT_CLAIM_BATCH_SIZE, SMART_MODEL
from app.database import Claim, Event
from app.agent.gemini_client import GeminiClient, get_client
from app.projectkb.llm_json import parse_json_list_field
from app.tenancy.paths import manager_memory_md_path

logger = logging.getLogger(__name__)

VALID_EVENT_TYPES = {
    "status_update",
    "blocker",
    "clarification",
    "commitment",
    "request",
    "conflict",
    "fyi",
}
# Severity doctrine floor (spec §4.3): these types are never allowed to
# round down to a silent 0, even if the model under-scores them -- code-
# enforced, not left to the prompt alone.
FLOOR_SEVERITY_TYPES = {"blocker", "clarification"}
RECENT_EVENTS_CONTEXT_LIMIT = 10


def _manager_projects(manager_id: str) -> List[Dict]:
    """This manager's own + member-of projects (registry rows), just
    id/name/kind -- enough for the model to tag events without inventing
    ids. Mirrors app.api.projects_registry.list_projects' visibility
    logic, its own short-lived control-plane session (jobs run outside
    any request, so there's no Depends(get_controlplane_db) to borrow --
    same pattern as app.integrations.slack's resolve_agent_by_manager)."""
    from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Manager, Project as RegistryProject, ProjectMember, Employee

    cdb = ControlPlaneSessionLocal()
    try:
        manager = cdb.get(Manager, manager_id)
        if manager is None:
            return []
        owned = cdb.query(RegistryProject).filter(RegistryProject.manager_user_id == manager_id).all()
        owned_ids = {p.id for p in owned}
        member_project_ids = {
            row.project_id
            for row in (
                cdb.query(ProjectMember.project_id)
                .join(Employee, ProjectMember.employee_id == Employee.id)
                .filter(func.lower(Employee.email) == manager.email.lower())
                .all()
            )
        }
        member_only_ids = member_project_ids - owned_ids
        member_projects = []
        if member_only_ids:
            member_projects = (
                cdb.query(RegistryProject).filter(RegistryProject.id.in_(member_only_ids)).all()
            )
        return [{"id": p.id, "name": p.name, "kind": p.kind} for p in owned + member_projects]
    finally:
        cdb.close()


def _read_memory_md(manager_id: str) -> str:
    path = manager_memory_md_path(manager_id)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _recent_events_context(db: Session) -> List[Event]:
    return list(
        db.scalars(select(Event).order_by(Event.created_at.desc()).limit(RECENT_EVENTS_CONTEXT_LIMIT)).all()
    )


_SYSTEM_INSTRUCTION = (
    "You are a judgment agent for a work knowledge base. Convert the given "
    "claims into typed, tagged, severity-scored events for a dashboard "
    "Updates panel.\n\n"
    'Return strict JSON: {"events": [{"type": "...", "project_ids": [...], '
    '"general": bool, "severity": 0-3, "title": "...", "body": "...", '
    '"claim_ids": [...]}]}.\n\n'
    "type must be exactly one of: status_update, blocker, clarification, "
    "commitment, request, conflict, fyi.\n"
    "project_ids: only use ids from the Candidate Projects list given below, "
    "verbatim -- never invent an id. If a claim isn't clearly about one of "
    "those projects, leave project_ids empty and set general=true.\n"
    "claim_ids must cite the claim_id(s) (from the Pending Claims list) this "
    "event was derived from.\n\n"
    "Severity doctrine -- follow exactly, this controls what interrupts the "
    "user's day:\n"
    "- Routine progress (\"x finished y\") -> severity 0-1, UNLESS recent "
    "history (see Recent Events below) shows this thread was already "
    "flagged important.\n"
    "- blocker or clarification -> always at least severity 1.\n"
    "- An approval being granted -> severity 1.\n"
    "- Urgent pending training, or repeated unanswered outreach -> "
    "severity 3.\n"
    "Multiple claims may combine into one event, or produce none, if "
    "nothing is dashboard-worthy -- do not force an event per claim."
)


def _build_user_prompt(
    claims: List[Claim], projects: List[Dict], recent_events: List[Event], memory_md: str
) -> str:
    projects_context = "\n".join(f"- id={p['id']} | name={p['name']} | kind={p['kind']}" for p in projects) or "(none)"
    recent_context = "\n".join(f"- [{e.type}] severity={e.severity}: {e.title}" for e in recent_events) or "(none)"
    claims_context = "\n".join(f"- [claim_id={c.id}] {c.text}" for c in claims)
    memory_section = memory_md.strip() or "(no memory.md yet)"
    return (
        f"### Candidate Projects (this user's memberships):\n{projects_context}\n\n"
        f"### Recent Events (most recent {RECENT_EVENTS_CONTEXT_LIMIT}):\n{recent_context}\n\n"
        f"### memory.md:\n{memory_section}\n\n"
        f"### Pending Claims:\n{claims_context}\n\n"
        "Produce the events JSON now."
    )


def _call_llm(
    client: GeminiClient, claims: List[Claim], projects: List[Dict], recent_events: List[Event], memory_md: str
) -> List[Dict]:
    res = client.chat(
        model=SMART_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_INSTRUCTION},
            {"role": "user", "content": _build_user_prompt(claims, projects, recent_events, memory_md)},
        ],
        json_mode=True,
    )
    return parse_json_list_field(res, "events", "[projectkb.heartbeat]")


def run(db: Session, manager_id: str, client: Optional[GeminiClient] = None) -> Dict:
    """Heartbeat job, user-level half: unprocessed Claim rows -> typed/
    tagged/severity-scored Event rows. See prompts/step_23_heartbeat_user.md."""
    real_client = client if client is not None else get_client()

    pending = list(
        db.scalars(select(Claim).where(Claim.processed.is_(False)).order_by(Claim.created_at)).all()
    )
    if not pending:
        return {"claims_consumed": 0, "events_created": 0, "users_processed": 1}

    batch = pending[:HEARTBEAT_CLAIM_BATCH_SIZE]

    # Context assembly (control-plane query, recent-events read, memory.md
    # read) is inside the same guard as the LLM call itself -- a control-
    # plane hiccup must fail this run safe (claims stay unprocessed for
    # next tick) exactly like an LLM failure does, not raise out of run()
    # uncaught.
    try:
        projects = _manager_projects(manager_id)
        recent_events = _recent_events_context(db)
        memory_md = _read_memory_md(manager_id)
        raw_events = _call_llm(real_client, batch, projects, recent_events, memory_md)
    except Exception:
        logger.exception(
            f"[projectkb.heartbeat] judgment run failed for manager={manager_id}; "
            "leaving claims unprocessed for next tick"
        )
        return {"claims_consumed": 0, "events_created": 0, "users_processed": 1}

    known_project_ids = {p["id"] for p in projects}
    known_claim_ids = {c.id for c in batch}

    events_created = 0
    for raw in raw_events:
        etype = raw.get("type")
        if etype not in VALID_EVENT_TYPES:
            continue
        title = (raw.get("title") or "").strip()
        if not title:
            continue

        try:
            severity = int(raw.get("severity", 0))
        except (TypeError, ValueError):
            severity = 0
        severity = max(0, min(3, severity))
        if etype in FLOOR_SEVERITY_TYPES:
            severity = max(severity, 1)

        # project_ids only from the known-projects list handed to the model
        # -- an id it invents (or a claim it left general anyway) never
        # resolves, so the event correctly falls back to general=true
        # rather than silently going untagged.
        project_ids = [pid for pid in (raw.get("project_ids") or []) if pid in known_project_ids]
        claim_ids = [cid for cid in (raw.get("claim_ids") or []) if cid in known_claim_ids]
        # general is DERIVED, not read from the model's own "general" field --
        # Event.general means "not tied to any project/task" (database.py),
        # so an event with a resolved project_id can never be general=true,
        # regardless of what the model claimed.
        general = not project_ids

        db.add(
            Event(
                id=uuid.uuid4().hex,
                type=etype,
                severity=severity,
                title=title,
                body=raw.get("body"),
                project_ids=json.dumps(project_ids) if project_ids else None,
                # task_ids: left unpopulated here on purpose -- task-level
                # tagging needs the project fan-out's own db access (step
                # 24), which owns Task rows; heartbeat-user only tags at
                # the project/general level.
                task_ids=None,
                claim_ids=json.dumps(claim_ids) if claim_ids else None,
                general=general,
                dreamed=False,
                ui_state="shown",
            )
        )
        events_created += 1

    for claim in batch:
        claim.processed = True
    db.commit()

    return {"claims_consumed": len(batch), "events_created": events_created, "users_processed": 1}
