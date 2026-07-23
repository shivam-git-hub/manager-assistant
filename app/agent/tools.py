"""Personal agent tool catalog (step 28 -- prompts/step_28_personal_agent.md
§5). Full rewrite: everything the old file had was either dead-schema
(send_slack_dm/send_email/list|get|create_meeting against v1 TeamMember) or
a Hermes mock placeholder never reachable from either entry point. Deleted
outright rather than kept-unused, per CLAUDE.md's "no half-finished
implementations" rule.

Every handler takes (db, manager_id, run_context, **args): `db` is the
manager's own db.sqlite session, `manager_id` scopes control-plane/project
lookups, `run_context` is a plain dict living for one run_agent() call
(currently only used by `todo` -- see its docstring) and carries the
current run's `candidates` map (kind, ref_key) -> Candidate, used to
validate a `candidate_ref` the model claims to be resolving before trusting
it (see send_message_handler) -- same "hallucinated id silently drops"
discipline used elsewhere in the pipeline (step 23's project_ids/claim_ids
filtering).
"""
import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from app import timeservice
from app.database import AgentActionLog, Meeting, Todo
from app.outbound import send_or_hold
from app.agent.registry import registry

# -----------------------------------------------------------------------------
# send_message
# -----------------------------------------------------------------------------


def _resolve_target_employee_id(manager_id: str, target: str) -> Optional[str]:
    """`target="manager"` resolves to the calling manager's own Employee
    row (matched by email, same convention as project_detail._my_employee_ids);
    anything else is treated as a literal Employee.id."""
    if target != "manager":
        return target
    from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Manager, Employee

    cdb = ControlPlaneSessionLocal()
    try:
        manager = cdb.get(Manager, manager_id)
        if manager is None:
            return None
        emp = cdb.query(Employee).filter(Employee.email.ilike(manager.email)).first()
        return emp.id if emp else None
    finally:
        cdb.close()


def _log_candidate_resolution(db: Session, run_context: Optional[Dict[str, Any]], candidate_kind: Optional[str], candidate_ref_key: Optional[str], detail: str) -> Optional[str]:
    """Validates (kind, ref_key) against this run's actual candidate list
    (never trust the model's claim alone) and, if valid, writes the
    AgentActionLog row plus any candidate-specific side effect (marking a
    Meeting briefed). Returns a short status string for the tool result, or
    None if there was nothing to resolve."""
    if not candidate_kind or not candidate_ref_key:
        return None
    candidates = (run_context or {}).get("candidates", {})
    if (candidate_kind, candidate_ref_key) not in candidates:
        return f"WARNING: candidate ({candidate_kind}, {candidate_ref_key}) was not in this run's candidate list -- ignored, not logged."

    exists = (
        db.query(AgentActionLog)
        .filter(AgentActionLog.action_type == candidate_kind, AgentActionLog.ref_key == candidate_ref_key)
        .first()
    )
    if exists:
        return "already logged (no-op)"

    db.add(AgentActionLog(id=uuid.uuid4().hex, action_type=candidate_kind, ref_key=candidate_ref_key, detail=detail[:500]))

    if candidate_kind == "pre_meeting_brief" and candidate_ref_key.startswith("meeting:"):
        meeting_id = int(candidate_ref_key.split(":", 1)[1])
        meeting = db.get(Meeting, meeting_id)
        if meeting:
            meeting.brief_sent_at = timeservice.now_ist()

    db.commit()
    return "logged"


def send_message_handler(
    db: Session,
    manager_id: str,
    run_context: Optional[Dict[str, Any]],
    channel: str,
    target: str,
    text: str,
    candidate_kind: Optional[str] = None,
    candidate_ref_key: Optional[str] = None,
) -> Dict[str, Any]:
    if channel not in ("slack", "portal"):
        return {"error": f"Unknown channel '{channel}'. Must be 'slack' or 'portal'."}

    employee_id = _resolve_target_employee_id(manager_id, target)
    if not employee_id:
        return {"error": f"Could not resolve target '{target}' to a known person."}

    if channel == "portal":
        from app.database import ChatMessage

        msg = ChatMessage(role="assistant", content=text)
        db.add(msg)
        db.commit()
        log_status = _log_candidate_resolution(db, run_context, candidate_kind, candidate_ref_key, f"portal -> {target}")
        return {"success": True, "status": "sent", "channel": "portal", "log": log_status}

    from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Employee
    from app.integrations.slack import connector as slack_connector

    cdb = ControlPlaneSessionLocal()
    try:
        employee = cdb.get(Employee, employee_id)
    finally:
        cdb.close()
    if not employee or not employee.slack_id:
        return {"error": f"'{target}' has no known Slack id to DM."}

    dm_channel = slack_connector.open_dm(employee.slack_id, manager_id)
    if not dm_channel:
        return {"error": f"Could not open a Slack DM with '{target}'."}

    res = send_or_hold("slack", {"channel": dm_channel, "text": text}, db)
    log_status = _log_candidate_resolution(db, run_context, candidate_kind, candidate_ref_key, f"slack -> {target}")
    return {
        "success": True,
        "status": res["status"],
        "channel": dm_channel,
        "release_at": str(res.get("release_at")) if res.get("release_at") else None,
        "message_id": res.get("message_id"),
        "log": log_status,
    }


SEND_MESSAGE_SCHEMA = {
    "name": "send_message",
    "description": (
        "Sends a message to a person. channel='slack' DMs the target on Slack (routes through the "
        "working-hours gate -- may be held until 09:00 IST next business day). channel='portal' posts "
        "into the manager's own dashboard chat thread. If this message resolves one of the candidate "
        "items you were given (a pre-meeting brief, follow-up, or conflict contact/escalation), pass "
        "its exact candidate_kind and candidate_ref_key so it isn't repeated next tick."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "channel": {"type": "string", "enum": ["slack", "portal"], "description": "Delivery channel."},
            "target": {
                "type": "string",
                "description": "The literal string 'manager' for the owner, or an Employee id for a teammate.",
            },
            "text": {"type": "string", "description": "Message body."},
            "candidate_kind": {
                "type": "string",
                "enum": ["pre_meeting_brief", "followup", "conflict_contact", "conflict_escalate"],
                "description": "Optional -- the candidate kind this message resolves, from your instructions.",
            },
            "candidate_ref_key": {
                "type": "string",
                "description": "Optional -- the exact ref_key of the candidate this message resolves.",
            },
        },
        "required": ["channel", "target", "text"],
    },
}

# -----------------------------------------------------------------------------
# dashboard_action
# -----------------------------------------------------------------------------

DASHBOARD_ACTIONS = ("create_task", "update_task_status", "add_project_member", "create_todo", "create_meeting")


def _dashboard_create_task(manager_id, project_id, title, description=None, priority=None, assignee_employee_id=None, due=None, parent_task_id=None) -> Dict[str, Any]:
    from app.projects.db import get_project_session
    from app.projects.models import Task

    pdb = get_project_session(project_id)
    try:
        task = Task(
            id=uuid.uuid4().hex,
            parent_task_id=parent_task_id,
            title=title,
            description=description,
            priority=priority or "medium",
            assignee_employee_id=assignee_employee_id,
            due=datetime.fromisoformat(due) if due else None,
            created_by="agent",
        )
        pdb.add(task)
        pdb.commit()
        return {"success": True, "task_id": task.id, "title": task.title}
    finally:
        pdb.close()


def _dashboard_update_task_status(manager_id, project_id, task_id, status) -> Dict[str, Any]:
    from app.projects.db import get_project_session
    from app.projects.models import Task

    valid = ("todo", "in_progress", "blocked", "done", "pending_approval")
    if status not in valid:
        return {"error": f"status must be one of {valid}"}
    pdb = get_project_session(project_id)
    try:
        task = pdb.get(Task, task_id)
        if not task:
            return {"error": f"Task '{task_id}' not found in project '{project_id}'."}
        task.status = status
        task.updated_at = timeservice.now_ist()
        pdb.commit()
        return {"success": True, "task_id": task.id, "status": task.status}
    finally:
        pdb.close()


def _dashboard_add_project_member(manager_id, project_id, employee_id, role=None) -> Dict[str, Any]:
    from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, ProjectMember, Employee, Project as RegistryProject

    cdb = ControlPlaneSessionLocal()
    try:
        project = cdb.get(RegistryProject, project_id)
        if not project:
            return {"error": f"Project '{project_id}' not found."}
        if project.manager_user_id != manager_id:
            return {"error": "Only the project manager can add members."}
        employee = cdb.get(Employee, employee_id)
        if not employee:
            return {"error": f"Employee '{employee_id}' not found."}
        existing = (
            cdb.query(ProjectMember)
            .filter(ProjectMember.project_id == project_id, ProjectMember.employee_id == employee_id)
            .first()
        )
        if existing:
            return {"success": True, "message": "already a member"}
        cdb.add(ProjectMember(id=uuid.uuid4().hex, project_id=project_id, employee_id=employee_id, role=role))
        cdb.commit()
        return {"success": True, "message": f"Added {employee.name} to {project.name}."}
    finally:
        cdb.close()


def _dashboard_create_todo(manager_id, db, text, due=None) -> Dict[str, Any]:
    todo = Todo(
        id=uuid.uuid4().hex,
        text=text,
        due=datetime.fromisoformat(due) if due else None,
        status="open",
    )
    db.add(todo)
    db.commit()
    return {"success": True, "todo_id": todo.id, "text": todo.text}


def _dashboard_create_meeting(db, title, starts_at, attendees=None, project_id=None) -> Dict[str, Any]:
    try:
        parsed = datetime.strptime(starts_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            parsed = datetime.strptime(starts_at, "%Y-%m-%d %H:%M")
        except ValueError:
            return {"error": "starts_at must be 'YYYY-MM-DD HH:MM[:SS]'."}

    from datetime import timedelta

    window = 30  # minutes -- dedup guard against re-creating the same mention twice
    existing = (
        db.query(Meeting)
        .filter(Meeting.title.ilike(title))
        .filter(Meeting.starts_at >= parsed - timedelta(minutes=window))
        .filter(Meeting.starts_at <= parsed + timedelta(minutes=window))
        .all()
    )
    for m in existing:
        if abs((m.starts_at - parsed).total_seconds()) <= window * 60:
            return {"success": True, "message": "already tracked", "meeting_id": m.id}

    meeting = Meeting(
        title=title,
        starts_at=parsed,
        attendees=json.dumps(attendees or []),
        project_id=project_id,
        status="scheduled",
    )
    db.add(meeting)
    db.commit()
    db.refresh(meeting)
    return {"success": True, "meeting_id": meeting.id, "title": meeting.title, "starts_at": str(meeting.starts_at)}


def dashboard_action_handler(
    db: Session,
    manager_id: str,
    run_context: Optional[Dict[str, Any]],
    action: str,
    project_id: Optional[str] = None,
    task_id: Optional[str] = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    priority: Optional[str] = None,
    assignee_employee_id: Optional[str] = None,
    employee_id: Optional[str] = None,
    role: Optional[str] = None,
    due: Optional[str] = None,
    parent_task_id: Optional[str] = None,
    status: Optional[str] = None,
    text: Optional[str] = None,
    starts_at: Optional[str] = None,
    attendees: Optional[List[str]] = None,
) -> Dict[str, Any]:
    if action not in DASHBOARD_ACTIONS:
        return {"error": f"Unknown action '{action}'. Must be one of {DASHBOARD_ACTIONS}."}

    if action == "create_task":
        if not project_id or not title:
            return {"error": "create_task requires project_id and title."}
        return _dashboard_create_task(manager_id, project_id, title, description, priority, assignee_employee_id, due, parent_task_id)
    if action == "update_task_status":
        if not project_id or not task_id or not status:
            return {"error": "update_task_status requires project_id, task_id, status."}
        return _dashboard_update_task_status(manager_id, project_id, task_id, status)
    if action == "add_project_member":
        if not project_id or not employee_id:
            return {"error": "add_project_member requires project_id and employee_id."}
        return _dashboard_add_project_member(manager_id, project_id, employee_id, role)
    if action == "create_todo":
        if not text:
            return {"error": "create_todo requires text."}
        return _dashboard_create_todo(manager_id, db, text, due)
    if action == "create_meeting":
        if not title or not starts_at:
            return {"error": "create_meeting requires title and starts_at."}
        return _dashboard_create_meeting(db, title, starts_at, attendees, project_id)
    return {"error": "unreachable"}


DASHBOARD_ACTION_SCHEMA = {
    "name": "dashboard_action",
    "description": "Performs a dashboard mutation, chosen by 'action'. Only pass the parameters relevant to the chosen action.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(DASHBOARD_ACTIONS)},
            "project_id": {"type": "string", "description": "Registry project id. Required for all actions except create_todo."},
            "task_id": {"type": "string", "description": "Required for update_task_status."},
            "title": {"type": "string", "description": "Required for create_task/create_meeting."},
            "description": {"type": "string", "description": "Optional, create_task."},
            "priority": {"type": "string", "enum": ["low", "medium", "high"], "description": "Optional, create_task."},
            "assignee_employee_id": {"type": "string", "description": "Optional, create_task."},
            "employee_id": {"type": "string", "description": "Required for add_project_member."},
            "role": {"type": "string", "description": "Optional, add_project_member."},
            "due": {"type": "string", "description": "Optional ISO datetime, create_task/create_todo."},
            "parent_task_id": {"type": "string", "description": "Optional, create_task (subtask)."},
            "status": {"type": "string", "enum": ["todo", "in_progress", "blocked", "done", "pending_approval"], "description": "Required for update_task_status."},
            "text": {"type": "string", "description": "Required for create_todo."},
            "starts_at": {"type": "string", "description": "Required for create_meeting, 'YYYY-MM-DD HH:MM[:SS]'."},
            "attendees": {"type": "array", "items": {"type": "string"}, "description": "Optional, create_meeting."},
        },
        "required": ["action"],
    },
}

# -----------------------------------------------------------------------------
# Read/probe tools
# -----------------------------------------------------------------------------


def list_meetings_handler(db: Session, manager_id: str, run_context: Optional[Dict[str, Any]], from_date: Optional[str] = None, to_date: Optional[str] = None) -> List[Dict[str, Any]]:
    stmt = db.query(Meeting)
    if from_date:
        stmt = stmt.filter(Meeting.starts_at >= datetime.strptime(from_date, "%Y-%m-%d"))
    if to_date:
        stmt = stmt.filter(Meeting.starts_at <= datetime.strptime(to_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59))
    rows = stmt.order_by(Meeting.starts_at.asc()).all()
    out = []
    for m in rows:
        try:
            atts = json.loads(m.attendees) if m.attendees else []
        except Exception:
            atts = []
        out.append({"id": m.id, "title": m.title, "starts_at": str(m.starts_at), "attendees": atts, "status": m.status, "brief_sent": bool(m.brief_sent_at)})
    return out


LIST_MEETINGS_SCHEMA = {
    "name": "list_meetings",
    "description": "Lists tracked meetings (optionally filtered by date range). Use before create_meeting to check a mention isn't already tracked.",
    "parameters": {
        "type": "object",
        "properties": {
            "from_date": {"type": "string", "description": "Optional YYYY-MM-DD."},
            "to_date": {"type": "string", "description": "Optional YYYY-MM-DD."},
        },
    },
}


def get_project_doc_handler(db: Session, manager_id: str, run_context: Optional[Dict[str, Any]], project_id: str, doc: str) -> Dict[str, Any]:
    from app.projects.paths import project_md_path, summary_md_path, notes_md_path

    paths = {"project": project_md_path, "summary": summary_md_path, "notes": notes_md_path}
    if doc not in paths:
        return {"error": f"doc must be one of {list(paths)}"}
    path = paths[doc](project_id)
    return {"content": path.read_text(encoding="utf-8") if path.exists() else ""}


GET_PROJECT_DOC_SCHEMA = {
    "name": "get_project_doc",
    "description": "Reads a project's markdown doc: 'project' (manager-authored overview/milestones), 'summary' (dream-synthesized), or 'notes'.",
    "parameters": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string"},
            "doc": {"type": "string", "enum": ["project", "summary", "notes"]},
        },
        "required": ["project_id", "doc"],
    },
}


def list_team_handler(db: Session, manager_id: str, run_context: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    from app.agent.context import team_roster_for_manager

    return team_roster_for_manager(manager_id)


LIST_TEAM_SCHEMA = {
    "name": "list_team",
    "description": "Lists teammates across all your projects (id, name, role, whether they have Slack).",
    "parameters": {"type": "object", "properties": {}},
}


def get_task_handler(db: Session, manager_id: str, run_context: Optional[Dict[str, Any]], project_id: str, task_id: str) -> Dict[str, Any]:
    from app.projects.db import get_project_session
    from app.projects.models import Task

    pdb = get_project_session(project_id)
    try:
        task = pdb.get(Task, task_id)
        if not task:
            return {"error": f"Task '{task_id}' not found."}
        return {
            "id": task.id,
            "title": task.title,
            "description": task.description,
            "status": task.status,
            "priority": task.priority,
            "assignee_employee_id": task.assignee_employee_id,
            "due": str(task.due) if task.due else None,
        }
    finally:
        pdb.close()


GET_TASK_SCHEMA = {
    "name": "get_task",
    "description": "Fetches a single task's detail.",
    "parameters": {
        "type": "object",
        "properties": {"project_id": {"type": "string"}, "task_id": {"type": "string"}},
        "required": ["project_id", "task_id"],
    },
}


def list_open_conflicts_handler(db: Session, manager_id: str, run_context: Optional[Dict[str, Any]], project_id: Optional[str] = None) -> List[Dict[str, Any]]:
    from app.database import Event

    stmt = db.query(Event).filter(Event.type == "conflict").filter(Event.ui_state.notin_(["dismissed", "approved", "rejected"]))
    rows = stmt.all()
    out = []
    for e in rows:
        pids = json.loads(e.project_ids) if e.project_ids else []
        if project_id and project_id not in pids:
            continue
        out.append({"id": e.id, "title": e.title, "body": e.body, "project_ids": pids, "severity": e.severity})
    return out


LIST_OPEN_CONFLICTS_SCHEMA = {
    "name": "list_open_conflicts",
    "description": "Lists open (un-dismissed) conflict events, optionally scoped to one project.",
    "parameters": {"type": "object", "properties": {"project_id": {"type": "string"}}},
}

# -----------------------------------------------------------------------------
# todo -- real, session-scoped worklist for one run (not persisted)
# -----------------------------------------------------------------------------


def todo_handler(
    db: Session,
    manager_id: str,
    run_context: Optional[Dict[str, Any]],
    todos: Optional[List[Dict[str, Any]]] = None,
    merge: Optional[bool] = False,
) -> Dict[str, Any]:
    if run_context is None:
        return {"error": "no run context available for todo state"}
    current = run_context.setdefault("todos", [])
    if todos is None:
        return {"todos": current}
    if merge:
        by_id = {t["id"]: t for t in current}
        for t in todos:
            by_id[t["id"]] = t
        run_context["todos"] = list(by_id.values())
    else:
        run_context["todos"] = todos
    return {"todos": run_context["todos"]}


TODO_SCHEMA = {
    "name": "todo",
    "description": (
        "Manage your worklist for this run. Use it to plan multiple candidate items before acting on "
        "them one at a time. Call with no 'todos' to read the current list. This list is NOT persisted "
        "across runs -- it's scratch space for this single heartbeat/chat turn."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "content": {"type": "string"},
                        "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "cancelled"]},
                    },
                    "required": ["id", "content", "status"],
                },
                "description": "Task items to write. Omit to read current list.",
            },
            "merge": {"type": "boolean", "default": False, "description": "true: update by id, add new. false: replace whole list."},
        },
    },
}

# -----------------------------------------------------------------------------
# Registration
# -----------------------------------------------------------------------------


def register_tools() -> None:
    registry.register("send_message", SEND_MESSAGE_SCHEMA, send_message_handler)
    registry.register("dashboard_action", DASHBOARD_ACTION_SCHEMA, dashboard_action_handler)
    registry.register("list_meetings", LIST_MEETINGS_SCHEMA, list_meetings_handler)
    registry.register("get_project_doc", GET_PROJECT_DOC_SCHEMA, get_project_doc_handler)
    registry.register("list_team", LIST_TEAM_SCHEMA, list_team_handler)
    registry.register("get_task", GET_TASK_SCHEMA, get_task_handler)
    registry.register("list_open_conflicts", LIST_OPEN_CONFLICTS_SCHEMA, list_open_conflicts_handler)
    registry.register("todo", TODO_SCHEMA, todo_handler)


# Auto register on import
register_tools()
