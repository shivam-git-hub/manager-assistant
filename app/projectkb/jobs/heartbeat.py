"""Heartbeat job (spec/architecture_v2_kb.md §4.3, prompts/
step_23_heartbeat_user.md + step_24_heartbeat_project_fanout.md): claims
-> typed/tagged/severity events (user-level), then a project-scoped
fan-out over the events just created (task status transitions, drafted
subtasks, deterministic archive writes). Runs every
HEARTBEAT_INTERVAL_MINUTES per manager -- both halves happen in the SAME
tick/run(), not as separate scheduled jobs (step 24's design correction).

Deliberate scope cut for BOTH halves (per the user's own sequencing: agent
context-engineering + tool-based KB inspection is designed later,
alongside testing this very job): a single structured-output smart-model
call per phase, not the full tool-loop harness. Don't "fix" this into a
tool loop without that design conversation happening first.
"""
import json
import logging
import uuid
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import timeservice
from app.config import HEARTBEAT_CLAIM_BATCH_SIZE, SMART_MODEL
from app.database import Claim, Event
from app.agent.gemini_client import GeminiClient, get_client
from app.projectkb.llm_json import parse_json_list_field, parse_json_object
from app.projectkb.project_scope import manager_owned_projects_with_events
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
FLOOR_SEVERITY_TYPES = {"blocker", "clarification", "conflict"}
RECENT_EVENTS_CONTEXT_LIMIT = 10


def _manager_projects(manager_id: str) -> List[Dict]:
    """This manager's own + member-of projects (registry rows), just
    id/name/kind -- enough for the model to tag events without inventing
    ids. Mirrors app.api.projects_registry.list_projects' visibility
    logic, its own short-lived control-plane session (jobs run outside
    any request, so there's no Depends(get_controlplane_db) to borrow --
    same pattern as app.integrations.slack's resolve_reader_by_manager)."""
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
    "conflict vs blocker -- a common judgment call, get this right: if two "
    "different people's claims contradict each other about the same fact "
    "(one says they sent/did/confirmed something, another says they never "
    "received it / it never happened / it wasn't confirmed), that is ALWAYS "
    "type=conflict, even though it also happens to be blocking someone's "
    "work -- never downgrade a genuine claim-vs-claim contradiction to a "
    "plain blocker just because one side frames it as being stuck. Reserve "
    "blocker for a single-sided obstacle with no contradicting claim on the "
    "other side (e.g. waiting on an external approval, a missing scope "
    "grant, a dependency that hasn't shipped).\n"
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


# ─── Project fan-out (step 24) ────────────────────────────────────────

VALID_TASK_TRANSITION_STATUSES = {"todo", "in_progress", "blocked", "done"}
VALID_TASK_PRIORITIES = {"low", "medium", "high"}

_FANOUT_SYSTEM_INSTRUCTION = (
    "You are a project-management agent. Given a project's summary, its "
    "current open tasks, and new events tagged to this project, propose "
    "task updates.\n\n"
    'Return strict JSON: {"task_status_transitions": [{"task_id": "...", '
    '"new_status": "todo|in_progress|blocked|done", "event_id": "..."}], '
    '"task_drafts": [{"title": "...", "description": "...", "priority": '
    '"low|medium|high", "assignee_employee_id": null, "parent_task_id": '
    'null}], "request_task_links": [{"event_id": "...", "task_id": "..."}]}.\n\n'
    "task_status_transitions: task_id must be one of the Open Tasks given; "
    "event_id must be one of the New Events given and must actually "
    "support that transition (e.g. an event reporting the task is done) -- "
    "never propose a transition without a citing event.\n"
    "task_drafts: new tasks/subtasks this project's events imply are "
    "needed. These are always proposals for the manager to approve, never "
    "assume they're accepted.\n"
    "request_task_links: for New Events of type=\"request\" that are "
    "literally asking to mark an existing task/subtask as done or "
    "complete, cite which Open Task it refers to. Most requests won't "
    "match any task -- only include a link when it's unambiguous."
)


def _build_fanout_prompt(summary_md: str, open_tasks: List, events: List[Event]) -> str:
    tasks_context = (
        "\n".join(f"- id={t.id} | title={t.title} | status={t.status} | priority={t.priority}" for t in open_tasks)
        or "(none)"
    )
    events_context = (
        "\n".join(f"- id={e.id} | type={e.type} | severity={e.severity} | {e.title}" for e in events) or "(none)"
    )
    summary_section = summary_md.strip() or "(no summary.md yet)"
    return (
        f"### summary.md:\n{summary_section}\n\n"
        f"### Open Tasks:\n{tasks_context}\n\n"
        f"### New Events (this project):\n{events_context}\n\n"
        "Produce the JSON now."
    )


def _call_fanout_llm(client: GeminiClient, summary_md: str, open_tasks: List, events: List[Event]) -> Dict:
    res = client.chat(
        model=SMART_MODEL,
        messages=[
            {"role": "system", "content": _FANOUT_SYSTEM_INSTRUCTION},
            {"role": "user", "content": _build_fanout_prompt(summary_md, open_tasks, events)},
        ],
        json_mode=True,
    )
    return parse_json_object(res, "[projectkb.heartbeat.fanout]")


def _fanout_one_project(db: Session, project_id: str, events: List[Event], client: GeminiClient) -> Dict:
    """Applies task transitions/drafts + deterministic archive writes for
    one project's newly-tagged events. Runs against two databases: the
    project's own (tasks/archive) and the manager's own `db` (Event.task_ids
    backfill for request->task links) -- committed separately, in that
    order, since a failure backfilling task_ids must not undo already-
    applied task changes."""
    from app.projects.db import get_project_session
    from app.projects.models import ArchiveEntry, Task
    from app.projects.paths import summary_md_path

    summary_path = summary_md_path(project_id)
    summary_md = summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""

    project_db = get_project_session(project_id)
    try:
        open_tasks = project_db.query(Task).filter(Task.status != "done").all()
        tasks_by_id = {t.id: t for t in open_tasks}
        known_event_ids = {e.id for e in events}

        result = _call_fanout_llm(client, summary_md, open_tasks, events)

        tasks_updated = 0
        for transition in result.get("task_status_transitions") or []:
            task_id = transition.get("task_id")
            new_status = transition.get("new_status")
            event_id = transition.get("event_id")
            # Evidence requirement: the transition must cite one of THIS
            # run's events for this project -- never trust the model to
            # self-police "did I actually see support for this."
            if task_id not in tasks_by_id or new_status not in VALID_TASK_TRANSITION_STATUSES:
                continue
            if event_id not in known_event_ids:
                continue
            task = tasks_by_id[task_id]
            task.status = new_status
            task.updated_at = timeservice.now_ist()
            tasks_updated += 1

        tasks_drafted = 0
        for draft in result.get("task_drafts") or []:
            title = (draft.get("title") or "").strip()
            if not title:
                continue
            parent_task_id = draft.get("parent_task_id")
            priority = draft.get("priority") if draft.get("priority") in VALID_TASK_PRIORITIES else "medium"
            project_db.add(
                Task(
                    id=uuid.uuid4().hex,
                    parent_task_id=parent_task_id if parent_task_id in tasks_by_id else None,
                    title=title,
                    description=draft.get("description"),
                    assignee_employee_id=draft.get("assignee_employee_id"),
                    status="pending_approval",
                    priority=priority,
                    created_by="agent",
                )
            )
            tasks_drafted += 1

        # Archive: deterministic, one row per event, NO synthesis LLM call
        # (spec correction 2026-07-23) -- just log what happened.
        for event in events:
            content = f"[{event.type}] {event.title}"
            if event.body:
                content += f" -- {event.body}"
            project_db.add(
                ArchiveEntry(ts=timeservice.now_ist(), kind="event", content=content, source_ref=event.id)
            )

        project_db.commit()

        # Second, separate commit against the MANAGER's own db (Event lives
        # there, not in this project db) -- best-effort: if the process
        # dies between the two commits, the task changes above are
        # already durable and this linkage is simply never applied. The
        # consequence is bounded (that request event just won't auto-mark
        # a task done on Approve) and this run never retries the same
        # events again, so there's no duplicate-write risk either way.
        request_links_applied = 0
        for link in result.get("request_task_links") or []:
            event_id = link.get("event_id")
            task_id = link.get("task_id")
            if task_id not in tasks_by_id:
                continue
            event = next((e for e in events if e.id == event_id and e.type == "request"), None)
            if event is None:
                continue
            # A single event can be tagged to multiple owned projects (each
            # gets its own fan-out pass) -- accumulate task_ids rather than
            # overwrite, so an earlier project's link isn't clobbered.
            existing_task_ids = json.loads(event.task_ids) if event.task_ids else []
            if task_id not in existing_task_ids:
                existing_task_ids.append(task_id)
                event.task_ids = json.dumps(existing_task_ids)
                request_links_applied += 1
        if request_links_applied:
            db.commit()

        return {"tasks_updated": tasks_updated, "tasks_drafted": tasks_drafted, "archive_entries": len(events)}
    finally:
        project_db.close()


def _run_project_fanout(db: Session, manager_id: str, tagged_events: List[Event], client: GeminiClient) -> Dict:
    """Groups this tick's project-tagged events by project, restricts to
    projects this manager actually manages (spec §4.3: manager-only
    project truth -- a teammate's own DMs never feed project KB), and runs
    the per-project fan-out for each. One project failing (e.g. its own
    LLM call raises) is logged and skipped, never aborts the others."""
    events_by_project = manager_owned_projects_with_events(manager_id, tagged_events)
    if not events_by_project:
        return {"projects_touched": 0, "tasks_updated": 0, "tasks_drafted": 0, "archive_entries": 0}

    totals = {"projects_touched": 0, "tasks_updated": 0, "tasks_drafted": 0, "archive_entries": 0}
    for project_id, project_events in events_by_project.items():
        try:
            stats = _fanout_one_project(db, project_id, project_events, client)
        except Exception:
            logger.exception(f"[projectkb.heartbeat.fanout] failed for project={project_id}, manager={manager_id}")
            continue
        totals["projects_touched"] += 1
        totals["tasks_updated"] += stats["tasks_updated"]
        totals["tasks_drafted"] += stats["tasks_drafted"]
        totals["archive_entries"] += stats["archive_entries"]
    return totals


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
    created_events: List[Event] = []
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

        event = Event(
            id=uuid.uuid4().hex,
            type=etype,
            severity=severity,
            title=title,
            body=raw.get("body"),
            project_ids=json.dumps(project_ids) if project_ids else None,
            # task_ids: left unpopulated here -- the fan-out below (step 24)
            # is what can resolve a request event to a specific task, since
            # it's the one with the project db's Task rows in context.
            task_ids=None,
            claim_ids=json.dumps(claim_ids) if claim_ids else None,
            general=general,
            dreamed=False,
            ui_state="shown",
        )
        db.add(event)
        created_events.append(event)
        events_created += 1

    for claim in batch:
        claim.processed = True
    db.commit()

    fanout_stats = {"projects_touched": 0, "tasks_updated": 0, "tasks_drafted": 0, "archive_entries": 0}
    project_tagged_events = [e for e in created_events if not e.general]
    if project_tagged_events:
        try:
            fanout_stats = _run_project_fanout(db, manager_id, project_tagged_events, real_client)
        except Exception:
            # Events are already committed successfully at this point --
            # a fan-out failure must not be reported as if the whole
            # heartbeat run failed (claims stay processed, events stay).
            logger.exception(f"[projectkb.heartbeat] project fan-out failed for manager={manager_id}")

    return {
        "claims_consumed": len(batch),
        "events_created": events_created,
        "users_processed": 1,
        **fanout_stats,
    }
