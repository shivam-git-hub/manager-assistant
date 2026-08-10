"""KB probe + heartbeat write tools for the agentic heartbeat (step 35,
Parts 1 & 2) + dream write tools (step 36, Part 3). Registered on the
shared `registry` (import side-effect pattern, same as
app/agent/tools.py:603) -- the jobs that drive these
(app/projectkb/jobs/heartbeat.py, app/projectkb/jobs/dream.py) import this
module for that effect and list an explicit AgentSpec.tool_names
allowlist, same discipline as app.agent.harness.CHAT_TOOL_NAMES.

Part 1 (search_events/get_event/search_claims/get_thread/
get_project_state/list_projects) is strictly read-only -- probing lets the
agent check "is there already an open blocker for this" before writing,
instead of judging blind off a claim batch alone (see the module docstring
in prompts/step_35_agentic_heartbeat.md for the "why").

Part 2 (emit_events/record_conflict/apply_task_transitions/draft_tasks/
link_request_to_task) is where every deterministic guardrail that used to
live inline in heartbeat.py's event-creation loop now lives -- type/
severity/project/claim validation, the general=derived rule, and the
claim.processed=True write, all in the same commit as the rows they
guard. Moving this into tool handlers (rather than trusting the model's
own JSON) is what makes the loop safe to hand to an agent that decides for
itself how many probes to make before writing.

Part 3 (write_memory/append_manager_events/write_project_summary/
append_project_events/add_suggestions/add_concerns/set_health_adjustment)
is the dream job's write surface (step 36) -- same idea, applied to the
durable narrative layer instead of events. The non-blank guards on
write_memory/write_project_summary exist because a bad generation must
never blank a durable file; set_health_adjustment computes the base score
itself via the deterministic rubric in app.projectkb.health_rubric and
only accepts a code-clamped +/-1 nudge from the model, never the final
score.
"""
import json
import uuid
from datetime import timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app import timeservice
from app.agent.registry import registry
from app.database import Claim, Event, UnifiedMessage
from app.projectkb.occurrence import compute_occurred_at

# -----------------------------------------------------------------------------
# Shared validation constants -- moved here from app/projectkb/jobs/heartbeat.py
# (lines ~32-44, ~167-168), which step 35 Part 3 rewrites to call these tools
# instead of owning this logic inline.
# -----------------------------------------------------------------------------

VALID_EVENT_TYPES = {
    "status_update",
    "blocker",
    "clarification",
    "commitment",
    "request",
    "conflict",
    "fyi",
}
# Severity doctrine floor: these types are never allowed to round down to a
# silent 0, even if the model under-scores them -- code-enforced, not left
# to the prompt alone.
FLOOR_SEVERITY_TYPES = {"blocker", "clarification", "conflict"}
VALID_TASK_TRANSITION_STATUSES = {"todo", "in_progress", "blocked", "done"}
VALID_TASK_PRIORITIES = {"low", "medium", "high"}
VALID_CONFLICT_SEVERITIES = {"low", "medium", "high"}

_EVENT_SEARCH_HARD_MAX = 50
_CLAIM_SEARCH_HARD_MAX = 50
_THREAD_HARD_MAX = 50


def _clamp_limit(limit: Optional[int], default: int, hard_max: int) -> int:
    try:
        n = int(limit) if limit is not None else default
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, hard_max))


def _append_lines(path, lines: List[str]) -> int:
    """Moved verbatim from app.projectkb.jobs.dream (pre-step-36) -- shared
    by append_manager_events/append_project_events below. Blank/non-string
    entries are dropped silently rather than rejecting the whole call, same
    tolerance the old dream.py had for a model that pads its list with
    empties. Returns the number of lines actually appended."""
    clean = [line.strip() for line in lines if isinstance(line, str) and line.strip()]
    if not clean:
        return 0
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    addition = "\n".join(f"- {line}" for line in clean)
    with open(path, "a", encoding="utf-8") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write(addition + "\n")
    return len(clean)


# -----------------------------------------------------------------------------
# Part 1 -- read-only KB probes
# -----------------------------------------------------------------------------


def search_events_handler(
    db: Session,
    manager_id: str,
    run_context: Optional[Dict[str, Any]],
    project_id: Optional[str] = None,
    type: Optional[str] = None,
    severity_min: Optional[int] = None,
    since_days: Optional[int] = 14,
    query: Optional[str] = None,
    limit: Optional[int] = 20,
) -> List[Dict[str, Any]]:
    limit = _clamp_limit(limit, 20, _EVENT_SEARCH_HARD_MAX)
    since = timeservice.now_ist() - timedelta(days=since_days if since_days is not None else 14)

    stmt = db.query(Event).filter(func.coalesce(Event.occurred_at, Event.created_at) >= since)
    if type:
        stmt = stmt.filter(Event.type == type)
    if severity_min is not None:
        stmt = stmt.filter(Event.severity >= severity_min)
    if query:
        like = f"%{query}%"
        stmt = stmt.filter(or_(Event.title.ilike(like), Event.body.ilike(like)))
    stmt = stmt.order_by(func.coalesce(Event.occurred_at, Event.created_at).desc())

    out: List[Dict[str, Any]] = []
    # project_id is matched in Python -- Event.project_ids is a JSON-text
    # column, not something SQL can filter directly (same pattern as
    # app.agent.kb_context._project_open_task_and_blocker_counts).
    for e in stmt.all():
        if len(out) >= limit:
            break
        pids = json.loads(e.project_ids) if e.project_ids else []
        if project_id and project_id not in pids:
            continue
        body = e.body or ""
        out.append({
            "id": e.id,
            "type": e.type,
            "severity": e.severity,
            "title": e.title,
            "body": body[:300],
            "project_ids": pids,
            "occurred_at": str(e.occurred_at) if e.occurred_at else None,
            "created_at": str(e.created_at),
            "ui_state": e.ui_state,
        })
    return out


SEARCH_EVENTS_SCHEMA = {
    "name": "search_events",
    "description": (
        "Searches recent events by project, type, minimum severity, and/or a title/body substring. "
        "Use this BEFORE emit_events to check whether a near-duplicate event already exists -- prefer "
        "probing over guessing."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string", "description": "Optional -- restrict to events tagged to this project."},
            "type": {
                "type": "string",
                "enum": sorted(VALID_EVENT_TYPES),
                "description": "Optional -- restrict to one event type.",
            },
            "severity_min": {"type": "integer", "description": "Optional -- minimum severity (0-3)."},
            "since_days": {"type": "integer", "default": 14, "description": "How many days back to search."},
            "query": {"type": "string", "description": "Optional -- substring match over title/body."},
            "limit": {"type": "integer", "default": 20, "description": "Max rows (hard cap 50)."},
        },
    },
}


def get_event_handler(
    db: Session,
    manager_id: str,
    run_context: Optional[Dict[str, Any]],
    event_id: str,
) -> Dict[str, Any]:
    event = db.get(Event, event_id)
    if event is None:
        return {"error": f"Event '{event_id}' not found."}

    claim_ids = json.loads(event.claim_ids) if event.claim_ids else []
    claims_by_id = {}
    if claim_ids:
        for c in db.query(Claim).filter(Claim.id.in_(claim_ids)).all():
            claims_by_id[c.id] = c.text

    return {
        "id": event.id,
        "type": event.type,
        "severity": event.severity,
        "title": event.title,
        "body": event.body,
        "project_ids": json.loads(event.project_ids) if event.project_ids else [],
        "task_ids": json.loads(event.task_ids) if event.task_ids else [],
        "general": event.general,
        "ui_state": event.ui_state,
        "occurred_at": str(event.occurred_at) if event.occurred_at else None,
        "created_at": str(event.created_at),
        "cited_claims": [
            {"id": cid, "text": claims_by_id.get(cid, "(claim not found -- stale citation)")}
            for cid in claim_ids
        ],
    }


GET_EVENT_SCHEMA = {
    "name": "get_event",
    "description": (
        "Fetches one event's full detail PLUS the text of every claim it cites -- the \"why did we "
        "conclude that\" probe. Use before deciding whether a new claim just restates an already-open "
        "event."
    ),
    "parameters": {
        "type": "object",
        "properties": {"event_id": {"type": "string"}},
        "required": ["event_id"],
    },
}


def search_claims_handler(
    db: Session,
    manager_id: str,
    run_context: Optional[Dict[str, Any]],
    query: Optional[str] = None,
    since_days: Optional[int] = 7,
    processed: Optional[bool] = None,
    limit: Optional[int] = 20,
) -> List[Dict[str, Any]]:
    limit = _clamp_limit(limit, 20, _CLAIM_SEARCH_HARD_MAX)
    since = timeservice.now_ist() - timedelta(days=since_days if since_days is not None else 7)

    stmt = db.query(Claim).filter(Claim.created_at >= since)
    if processed is not None:
        stmt = stmt.filter(Claim.processed == processed)
    if query:
        stmt = stmt.filter(Claim.text.ilike(f"%{query}%"))
    rows = stmt.order_by(Claim.created_at.desc()).limit(limit).all()

    return [
        {"id": c.id, "text": c.text, "thread_key": c.thread_key, "created_at": str(c.created_at)}
        for c in rows
    ]


SEARCH_CLAIMS_SCHEMA = {
    "name": "search_claims",
    "description": "Searches recent claims by substring and/or processed state -- use to find prior context for a thread before judging a new claim.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Optional -- substring match over claim text."},
            "since_days": {"type": "integer", "default": 7, "description": "How many days back to search."},
            "processed": {"type": "boolean", "description": "Optional -- filter to already-processed or still-pending claims."},
            "limit": {"type": "integer", "default": 20, "description": "Max rows (hard cap 50)."},
        },
    },
}


def get_thread_handler(
    db: Session,
    manager_id: str,
    run_context: Optional[Dict[str, Any]],
    thread_key: str,
    limit: Optional[int] = 20,
) -> List[Dict[str, Any]]:
    limit = _clamp_limit(limit, 20, _THREAD_HARD_MAX)
    rows = (
        db.query(UnifiedMessage)
        .filter(UnifiedMessage.thread_id == thread_key)
        .order_by(UnifiedMessage.timestamp.asc())
        .limit(limit)
        .all()
    )
    out = []
    for m in rows:
        content = m.content or ""
        out.append({
            "sender": m.sender_mapped_name or m.sender_raw_id,
            "timestamp": str(m.timestamp),
            "content": content[:400],
        })
    return out


GET_THREAD_SCHEMA = {
    "name": "get_thread",
    "description": "Fetches the raw messages behind a thread_key (a claim's thread_key field) in chronological order -- \"what did we actually say last week?\".",
    "parameters": {
        "type": "object",
        "properties": {
            "thread_key": {"type": "string"},
            "limit": {"type": "integer", "default": 20, "description": "Max messages (hard cap 50)."},
        },
        "required": ["thread_key"],
    },
}


def get_project_state_handler(
    db: Session,
    manager_id: str,
    run_context: Optional[Dict[str, Any]],
    project_id: str,
) -> Dict[str, Any]:
    from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Project as RegistryProject, get_member_list
    from app.projects.db import get_project_session
    from app.projects.models import Conflict, HealthLog, Task
    from app.projects.paths import summary_md_path

    visibility_error = _require_visible_project(manager_id, project_id)
    if visibility_error is not None:
        return visibility_error

    cdb = ControlPlaneSessionLocal()
    try:
        project = cdb.get(RegistryProject, project_id)
        if project is None:
            return {"error": f"Project '{project_id}' not found."}
        name, kind = project.name, project.kind
        member_count = len(get_member_list(project))
    finally:
        cdb.close()

    now = timeservice.now_ist()
    pdb = get_project_session(project_id)
    try:
        tasks = pdb.query(Task).all()
        open_tasks = sum(1 for t in tasks if t.status != "done")
        blocked_tasks = sum(1 for t in tasks if t.status == "blocked")
        overdue_tasks = sum(
            1 for t in tasks
            if t.status not in ("done", "blocked", "pending_approval") and t.due and t.due < now
        )
        latest_health = pdb.query(HealthLog).order_by(HealthLog.ts.desc()).first()
        open_conflicts = pdb.query(Conflict).filter(Conflict.status == "open").count()
    finally:
        pdb.close()

    summary_path = summary_md_path(project_id)
    summary_text = summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""

    return {
        "id": project_id,
        "name": name,
        "kind": kind,
        "member_count": member_count,
        "open_tasks": open_tasks,
        "blocked_tasks": blocked_tasks,
        "overdue_tasks": overdue_tasks,
        "health": {"score": latest_health.final_score, "reason": latest_health.reason} if latest_health else None,
        "open_conflicts": open_conflicts,
        "summary_tail": summary_text[-800:],
    }


GET_PROJECT_STATE_SCHEMA = {
    "name": "get_project_state",
    "description": "Fetches one project's current snapshot: task counts, latest health score, open conflict count, and a summary.md tail. Use before tagging a claim to a project, or before proposing a task transition.",
    "parameters": {
        "type": "object",
        "properties": {"project_id": {"type": "string"}},
        "required": ["project_id"],
    },
}


def list_projects_handler(
    db: Session,
    manager_id: str,
    run_context: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    from app.agent.context import visible_projects_for_manager

    projects = visible_projects_for_manager(manager_id)
    return [
        {"id": p["id"], "name": p["name"], "kind": p["kind"], "role": "manager" if p["is_manager"] else "member"}
        for p in projects
    ]


LIST_PROJECTS_SCHEMA = {
    "name": "list_projects",
    "description": "Lists every project visible to you (owned or member-of) with id/name/kind/role -- use this to know which project_ids are valid before tagging an event.",
    "parameters": {"type": "object", "properties": {}},
}

# -----------------------------------------------------------------------------
# Part 2 -- heartbeat write tools
# -----------------------------------------------------------------------------


def emit_events_handler(
    db: Session,
    manager_id: str,
    run_context: Optional[Dict[str, Any]],
    events: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """The agent's only way to create events -- one batch call per run.
    Carries every deterministic guardrail that used to live inline in
    app/projectkb/jobs/heartbeat.py's event-creation loop (type/title/
    severity/project_id/claim_id validation, derived `general`,
    computed `occurred_at`), plus the anti-loop invariant: every claim
    actually cited by a created event is marked processed=True in the
    SAME commit as the events themselves, so a run that dies right after
    this call can never re-emit what it already wrote.

    `claim_ids` on each item are restricted to run_context["offered_claim_ids"]
    when the caller populated it (the batch of claim ids this run was
    actually seeded with -- set by the heartbeat job before starting the
    agent run), mirroring the existing "hallucinated id silently drops"
    discipline used by send_message's candidate_ref_key. Without that key
    (e.g. a direct call outside a heartbeat run) the looser fallback is
    "must at least resolve to a real Claim row".
    """
    from app.agent.context import visible_projects_for_manager

    known_project_ids = {p["id"] for p in visible_projects_for_manager(manager_id)}
    offered_claim_ids = None
    if run_context is not None and run_context.get("offered_claim_ids") is not None:
        offered_claim_ids = set(run_context["offered_claim_ids"])

    report: List[Dict[str, Any]] = []
    created_events: List[Event] = []
    all_cited_claim_ids: set = set()

    # The whole build phase is one try block: a mid-loop exception (e.g. a
    # malformed claim_id tripping compute_occurred_at) must not leave
    # half-built Event rows pending on this session for some unrelated
    # later db.commit() in the same run to accidentally flush -- roll back
    # and propagate so the registry's own exception handling reports it as
    # a failed call instead.
    try:
        for idx, raw in enumerate(events or []):
            etype = raw.get("type")
            if etype not in VALID_EVENT_TYPES:
                report.append({"index": idx, "status": "skipped", "reason": f"invalid type '{etype}'"})
                continue

            title = (raw.get("title") or "").strip()
            if not title:
                report.append({"index": idx, "status": "skipped", "reason": "empty title"})
                continue

            try:
                severity = int(raw.get("severity", 0))
            except (TypeError, ValueError):
                severity = 0
            severity = max(0, min(3, severity))
            if etype in FLOOR_SEVERITY_TYPES:
                severity = max(severity, 1)

            # Invented project ids are dropped, not rejected outright -- a
            # claim that fails to resolve correctly falls back to
            # general=true rather than silently going untagged.
            project_ids = [pid for pid in (raw.get("project_ids") or []) if pid in known_project_ids]

            raw_claim_ids = raw.get("claim_ids") or []
            if offered_claim_ids is not None:
                claim_ids = [cid for cid in raw_claim_ids if cid in offered_claim_ids]
            else:
                existing = {
                    row.id for row in db.query(Claim.id).filter(Claim.id.in_(raw_claim_ids)).all()
                }
                claim_ids = [cid for cid in raw_claim_ids if cid in existing]

            # general is DERIVED, never taken from the model -- an event
            # with a resolved project_id can never be general=true.
            general = not project_ids

            event = Event(
                id=uuid.uuid4().hex,
                type=etype,
                severity=severity,
                title=title,
                body=raw.get("body"),
                project_ids=json.dumps(project_ids) if project_ids else None,
                task_ids=None,
                claim_ids=json.dumps(claim_ids) if claim_ids else None,
                general=general,
                dreamed=False,
                ui_state="shown",
                occurred_at=compute_occurred_at(db, claim_ids),
            )
            db.add(event)
            created_events.append(event)
            all_cited_claim_ids.update(claim_ids)
            report.append({
                "index": idx,
                "status": "created",
                "event_id": event.id,
                "type": etype,
                "severity": severity,
                "general": general,
                "project_ids": project_ids,
                "claim_ids": claim_ids,
            })

        if all_cited_claim_ids:
            for claim in db.query(Claim).filter(Claim.id.in_(all_cited_claim_ids)).all():
                claim.processed = True

        db.commit()
    except Exception:
        db.rollback()
        raise

    return {"created": len(created_events), "event_ids": [e.id for e in created_events], "report": report}


EMIT_EVENTS_SCHEMA = {
    "name": "emit_events",
    "description": (
        "Creates one or more typed/tagged/severity-scored events from claims you've judged. This is "
        "the ONLY way to write events -- call it once per run, in a batch, after probing for existing "
        "near-duplicates. Every cited claim_id is marked consumed so it is never re-offered next tick. "
        "Returns a per-item accept/skip report -- check it, an item can be silently skipped (bad type, "
        "empty title) and you may want to retry it corrected."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "events": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": sorted(VALID_EVENT_TYPES)},
                        "severity": {"type": "integer", "description": "0-3; blocker/clarification/conflict are floored to at least 1."},
                        "title": {"type": "string"},
                        "body": {"type": "string"},
                        "project_ids": {"type": "array", "items": {"type": "string"}, "description": "Only ids from list_projects -- invented ids are dropped."},
                        "claim_ids": {"type": "array", "items": {"type": "string"}, "description": "The claim id(s) this event was derived from."},
                    },
                    "required": ["type", "title"],
                },
            },
        },
        "required": ["events"],
    },
}


def record_conflict_handler(
    db: Session,
    manager_id: str,
    run_context: Optional[Dict[str, Any]],
    project_id: str,
    claim_a_id: str,
    claim_b_id: str,
    severity: Optional[str] = None,
) -> Dict[str, Any]:
    """Writes the per-project `conflicts` table -- nothing else in this
    codebase writes it today (see CLAUDE.md: it's read by the health
    rubric and the project-detail API and has been permanently empty).
    Mirrors the fan-out's "cite something real" discipline: both claim ids
    must resolve to actual Claim rows in this manager's own db, or the
    call is rejected outright rather than silently dropping one side.
    Never auto-resolved -- status is always "open".

    Owned-projects-only (`_require_owned_project`), same as the other
    three project-phase write tools -- a `conflicts` row is project truth
    (CLAUDE.md's write-authority rule), so even though emit_events may
    legitimately tag an event to a project this manager only belongs to
    (see visible_projects_for_manager there), recording a conflict for
    that same project_id is correctly rejected here. A caller's prompt
    must not tell the agent it can record conflicts on member-of projects.
    """
    from app.projects.db import get_project_session
    from app.projects.models import Conflict

    project_error = _require_owned_project(manager_id, project_id)
    if project_error is not None:
        return project_error

    known = {
        row.id for row in db.query(Claim.id).filter(Claim.id.in_([claim_a_id, claim_b_id])).all()
    }
    if claim_a_id not in known or claim_b_id not in known:
        return {"error": "claim_a_id and claim_b_id must both resolve to real claims."}

    sev = severity if severity in VALID_CONFLICT_SEVERITIES else "medium"

    pdb = get_project_session(project_id)
    try:
        conflict = Conflict(claim_a_ref=claim_a_id, claim_b_ref=claim_b_id, severity=sev, status="open")
        pdb.add(conflict)
        pdb.commit()
        pdb.refresh(conflict)
        return {"success": True, "conflict_id": conflict.id, "project_id": project_id, "severity": sev, "status": "open"}
    finally:
        pdb.close()


RECORD_CONFLICT_SCHEMA = {
    "name": "record_conflict",
    "description": (
        "Records an open conflict between two contradicting claims in a project's conflicts table. "
        "Both claim ids must be real. Conflicts are never auto-resolved -- this only opens one, a human "
        "resolves it later."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string"},
            "claim_a_id": {"type": "string"},
            "claim_b_id": {"type": "string"},
            "severity": {"type": "string", "enum": sorted(VALID_CONFLICT_SEVERITIES), "description": "Optional, defaults to 'medium'."},
        },
        "required": ["project_id", "claim_a_id", "claim_b_id"],
    },
}


def _require_owned_project(manager_id: str, project_id: str) -> Optional[Dict[str, Any]]:
    """Returns an error dict when project_id doesn't resolve to a registry
    project this manager actually owns, else None. Write authority
    (CLAUDE.md: "only the manager's own pipeline synthesizes project
    truth") is enforced HERE, before any project db.sqlite is touched --
    app.projects.db.get_project_engine mkdir's projects/<id>/ unconditionally
    for any id handed to it, so without this gate a hallucinated project_id
    would silently create a stray directory instead of failing loudly.
    Mirrors app.agent.tools._dashboard_add_project_member's ownership check.
    """
    from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Project as RegistryProject

    cdb = ControlPlaneSessionLocal()
    try:
        project = cdb.get(RegistryProject, project_id)
    finally:
        cdb.close()
    if project is None:
        return {"error": f"Project '{project_id}' not found."}
    if project.manager_user_id != manager_id:
        return {"error": f"Project '{project_id}' is not owned by this manager."}
    return None


def _require_visible_project(manager_id: str, project_id: str) -> Optional[Dict[str, Any]]:
    """Read-scope counterpart to _require_owned_project: owned OR member-of,
    matching app.agent.context.visible_projects_for_manager.

    Every project db lives in one global projects/<id>/ tree, so a handler
    that takes a project_id and goes straight to get_project_session() will
    happily read ANOTHER manager's project. Project ids are uuids, so this
    isn't trivially exploitable, but tenant isolation must be structural,
    not "the id is hard to guess" -- and the same gate stops a hallucinated
    id from mkdir'ing a stray projects/<id>/ directory (see
    _require_owned_project's docstring).
    """
    from app.agent.context import visible_projects_for_manager

    if any(p["id"] == project_id for p in visible_projects_for_manager(manager_id)):
        return None
    return {"error": f"Project '{project_id}' is not visible to this manager."}


def apply_task_transitions_handler(
    db: Session,
    manager_id: str,
    run_context: Optional[Dict[str, Any]],
    project_id: str,
    transitions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    from app.projects.db import get_project_session
    from app.projects.models import Task

    project_error = _require_owned_project(manager_id, project_id)
    if project_error is not None:
        return project_error

    transitions = transitions or []
    candidate_event_ids = [t.get("event_id") for t in transitions if t.get("event_id")]
    # Real Event rows, mapped to their project_ids so each transition can
    # also be checked below against THIS project -- not just any real
    # event. Further restricted to run_context["known_event_ids"] when the
    # caller (the project fan-out run) populated it: "cite a real event_id
    # from THIS run", not any historical event, mirrors heartbeat.py's old
    # known_event_ids restriction to just the events tagged to this
    # project this tick.
    valid_events = {}
    if candidate_event_ids:
        valid_events = {
            row.id: row.project_ids
            for row in db.query(Event.id, Event.project_ids).filter(Event.id.in_(candidate_event_ids)).all()
        }
        if run_context is not None and run_context.get("known_event_ids") is not None:
            allowed = set(run_context["known_event_ids"])
            valid_events = {eid: pids for eid, pids in valid_events.items() if eid in allowed}

    pdb = get_project_session(project_id)
    try:
        report = []
        applied = 0
        for idx, t in enumerate(transitions):
            task_id = t.get("task_id")
            new_status = t.get("new_status")
            event_id = t.get("event_id")
            if new_status not in VALID_TASK_TRANSITION_STATUSES:
                report.append({"index": idx, "status": "skipped", "reason": f"invalid new_status '{new_status}'"})
                continue
            if not event_id or event_id not in valid_events:
                report.append({"index": idx, "status": "skipped", "reason": "event_id does not cite a real event from this run"})
                continue
            event_project_ids = json.loads(valid_events[event_id]) if valid_events[event_id] else []
            if project_id not in event_project_ids:
                report.append({"index": idx, "status": "skipped", "reason": f"event '{event_id}' is not tagged to project '{project_id}'"})
                continue
            task = pdb.get(Task, task_id)
            if task is None:
                report.append({"index": idx, "status": "skipped", "reason": f"task '{task_id}' not found"})
                continue
            # The old inline fan-out only ever OFFERED open tasks as
            # transition targets, so "don't reopen finished work" was
            # structural. Here the agent names the task itself, so the
            # guard has to be explicit -- a stale claim ("waiting on the
            # Kafka creds") must not silently drag a closed task back open.
            if task.status == "done":
                report.append({"index": idx, "status": "skipped", "reason": f"task '{task_id}' is already done -- reopening requires a human"})
                continue
            task.status = new_status
            task.updated_at = timeservice.now_ist()
            applied += 1
            report.append({"index": idx, "status": "applied", "task_id": task_id, "new_status": new_status})
        pdb.commit()
        return {"applied": applied, "report": report}
    finally:
        pdb.close()


APPLY_TASK_TRANSITIONS_SCHEMA = {
    "name": "apply_task_transitions",
    "description": "Applies task status transitions for one project. Each transition must cite the event_id (from this run) that supports it -- never propose a transition without evidence.",
    "parameters": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string"},
            "transitions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string"},
                        "new_status": {"type": "string", "enum": sorted(VALID_TASK_TRANSITION_STATUSES)},
                        "event_id": {"type": "string", "description": "The event from this run that supports this transition."},
                    },
                    "required": ["task_id", "new_status", "event_id"],
                },
            },
        },
        "required": ["project_id", "transitions"],
    },
}


def draft_tasks_handler(
    db: Session,
    manager_id: str,
    run_context: Optional[Dict[str, Any]],
    project_id: str,
    tasks: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    from app.projects.db import get_project_session
    from app.projects.models import Task

    project_error = _require_owned_project(manager_id, project_id)
    if project_error is not None:
        return project_error

    pdb = get_project_session(project_id)
    try:
        existing_task_ids = {row.id for row in pdb.query(Task.id).all()}
        report = []
        drafted: List[Task] = []
        for idx, draft in enumerate(tasks or []):
            title = (draft.get("title") or "").strip()
            if not title:
                report.append({"index": idx, "status": "skipped", "reason": "empty title"})
                continue
            priority = draft.get("priority") if draft.get("priority") in VALID_TASK_PRIORITIES else "medium"
            parent_task_id = draft.get("parent_task_id")
            if parent_task_id and parent_task_id not in existing_task_ids:
                parent_task_id = None
            task = Task(
                id=uuid.uuid4().hex,
                parent_task_id=parent_task_id,
                title=title,
                description=draft.get("description"),
                assignee_employee_id=draft.get("assignee_employee_id"),
                # Always a proposal for the manager to approve -- never
                # assume an agent-drafted task is accepted.
                status="pending_approval",
                priority=priority,
                created_by="agent",
            )
            pdb.add(task)
            drafted.append(task)
            report.append({"index": idx, "status": "drafted", "title": title})
        pdb.commit()
        return {"drafted": len(drafted), "task_ids": [t.id for t in drafted], "report": report}
    finally:
        pdb.close()


DRAFT_TASKS_SCHEMA = {
    "name": "draft_tasks",
    "description": "Proposes new tasks/subtasks for a project. Always created as status=pending_approval for the manager to approve -- never assume acceptance.",
    "parameters": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string"},
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "priority": {"type": "string", "enum": sorted(VALID_TASK_PRIORITIES)},
                        "assignee_employee_id": {"type": "string"},
                        "parent_task_id": {"type": "string", "description": "Optional -- must be an existing task id to nest as a subtask."},
                    },
                    "required": ["title"],
                },
            },
        },
        "required": ["project_id", "tasks"],
    },
}


def link_request_to_task_handler(
    db: Session,
    manager_id: str,
    run_context: Optional[Dict[str, Any]],
    event_id: str,
    project_id: str,
    task_id: str,
) -> Dict[str, Any]:
    from app.projects.db import get_project_session
    from app.projects.models import Task

    project_error = _require_owned_project(manager_id, project_id)
    if project_error is not None:
        return project_error

    event = db.get(Event, event_id)
    if event is None:
        return {"error": f"Event '{event_id}' not found."}
    if event.type != "request":
        return {"error": f"Event '{event_id}' is type={event.type}, not 'request'."}
    event_project_ids = json.loads(event.project_ids) if event.project_ids else []
    if project_id not in event_project_ids:
        return {"error": f"Event '{event_id}' is not tagged to project '{project_id}'."}

    pdb = get_project_session(project_id)
    try:
        task = pdb.get(Task, task_id)
    finally:
        pdb.close()
    if task is None:
        return {"error": f"Task '{task_id}' not found in project '{project_id}'."}

    # Accumulates, never overwrites -- one event can end up linked from
    # multiple owned-project fan-out passes over time.
    existing = json.loads(event.task_ids) if event.task_ids else []
    if task_id in existing:
        return {"success": True, "message": "already linked", "task_ids": existing}
    existing.append(task_id)
    event.task_ids = json.dumps(existing)
    db.commit()
    return {"success": True, "task_ids": existing}


LINK_REQUEST_TO_TASK_SCHEMA = {
    "name": "link_request_to_task",
    "description": "Links a type=request event to the existing task it's asking about (e.g. \"mark the API docs task done\"). Only use when the match is unambiguous -- most requests won't match any task.",
    "parameters": {
        "type": "object",
        "properties": {
            "event_id": {"type": "string"},
            "project_id": {"type": "string"},
            "task_id": {"type": "string"},
        },
        "required": ["event_id", "project_id", "task_id"],
    },
}

# -----------------------------------------------------------------------------
# Part 3 -- dream write tools (step 36)
# -----------------------------------------------------------------------------


def write_memory_handler(
    db: Session,
    manager_id: str,
    run_context: Optional[Dict[str, Any]],
    content: Optional[str] = None,
) -> Dict[str, Any]:
    """Full overwrite of this manager's durable memory.md. Rejects a
    blank/whitespace-only content instead of writing it -- mirrors the
    guard pre-step-36 dream.py had inline (dream.py:106-108): a bad or
    empty generation must never blank the file that's supposed to survive
    across days."""
    from app.tenancy.paths import manager_memory_md_path

    if not isinstance(content, str) or not content.strip():
        return {"error": "content is blank -- refusing to overwrite memory.md with nothing."}

    manager_memory_md_path(manager_id).write_text(content, encoding="utf-8")
    return {"success": True, "chars_written": len(content)}


WRITE_MEMORY_SCHEMA = {
    "name": "write_memory",
    "description": (
        "Full overwrite of your durable memory.md -- pass the WHOLE rewritten file, not a diff. "
        "Merge new facts into what's already there and dedupe rather than appending; drop nothing that "
        "is still true. Refuses to write a blank/whitespace-only content."
    ),
    "parameters": {
        "type": "object",
        "properties": {"content": {"type": "string", "description": "The full rewritten memory.md content."}},
        "required": ["content"],
    },
}


def append_manager_events_handler(
    db: Session,
    manager_id: str,
    run_context: Optional[Dict[str, Any]],
    lines: Optional[List[str]] = None,
) -> Dict[str, Any]:
    from app.tenancy.paths import manager_events_md_path

    appended = _append_lines(manager_events_md_path(manager_id), lines or [])
    return {"appended": appended}


APPEND_MANAGER_EVENTS_SCHEMA = {
    "name": "append_manager_events",
    "description": (
        "Appends short one-line-per-event entries to your events.md running log. This is a log, NOT a "
        "repeat of memory.md's content -- keep entries brief."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "lines": {"type": "array", "items": {"type": "string"}, "description": "One entry per list item; blanks are dropped."},
        },
        "required": ["lines"],
    },
}


def write_project_summary_handler(
    db: Session,
    manager_id: str,
    run_context: Optional[Dict[str, Any]],
    project_id: str,
    content: Optional[str] = None,
) -> Dict[str, Any]:
    """Full overwrite of one project's summary.md. Same non-blank guard as
    write_memory (dream.py:106-108's rule applied to the project side too),
    owned projects only."""
    from app.projects.paths import summary_md_path

    project_error = _require_owned_project(manager_id, project_id)
    if project_error is not None:
        return project_error

    if not isinstance(content, str) or not content.strip():
        return {"error": "content is blank -- refusing to overwrite summary.md with nothing."}

    summary_md_path(project_id).write_text(content, encoding="utf-8")
    return {"success": True, "project_id": project_id, "chars_written": len(content)}


WRITE_PROJECT_SUMMARY_SCHEMA = {
    "name": "write_project_summary",
    "description": (
        "Full overwrite of one project's summary.md -- pass the WHOLE rewritten file: current goals, "
        "state, and notes an agent should load to understand this project (NOT a full history, that's "
        "what the events log is for). Owned projects only. Refuses to write a blank content."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string"},
            "content": {"type": "string", "description": "The full rewritten summary.md content."},
        },
        "required": ["project_id", "content"],
    },
}


def append_project_events_handler(
    db: Session,
    manager_id: str,
    run_context: Optional[Dict[str, Any]],
    project_id: str,
    lines: Optional[List[str]] = None,
) -> Dict[str, Any]:
    from app.projects.paths import events_md_path

    project_error = _require_owned_project(manager_id, project_id)
    if project_error is not None:
        return project_error

    appended = _append_lines(events_md_path(project_id), lines or [])
    return {"appended": appended, "project_id": project_id}


APPEND_PROJECT_EVENTS_SCHEMA = {
    "name": "append_project_events",
    "description": "Appends short one-line-per-event entries to this project's events.md running log. Owned projects only.",
    "parameters": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string"},
            "lines": {"type": "array", "items": {"type": "string"}, "description": "One entry per list item; blanks are dropped."},
        },
        "required": ["project_id", "lines"],
    },
}


def add_suggestions_handler(
    db: Session,
    manager_id: str,
    run_context: Optional[Dict[str, Any]],
    project_id: str,
    texts: Optional[List[str]] = None,
) -> Dict[str, Any]:
    from app.projects.db import get_project_session
    from app.projects.models import Suggestion

    project_error = _require_owned_project(manager_id, project_id)
    if project_error is not None:
        return project_error

    now = timeservice.now_ist()
    pdb = get_project_session(project_id)
    try:
        created = 0
        for text in texts or []:
            if isinstance(text, str) and text.strip():
                pdb.add(Suggestion(ts=now, text=text.strip(), status="open"))
                created += 1
        pdb.commit()
        return {"created": created, "project_id": project_id}
    finally:
        pdb.close()


ADD_SUGGESTIONS_SCHEMA = {
    "name": "add_suggestions",
    "description": "Records constructive suggestions for the manager on one project, status=open. Owned projects only. Blank entries are skipped.",
    "parameters": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string"},
            "texts": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["project_id", "texts"],
    },
}


def add_concerns_handler(
    db: Session,
    manager_id: str,
    run_context: Optional[Dict[str, Any]],
    project_id: str,
    texts: Optional[List[str]] = None,
) -> Dict[str, Any]:
    from app.projects.db import get_project_session
    from app.projects.models import Concern

    project_error = _require_owned_project(manager_id, project_id)
    if project_error is not None:
        return project_error

    now = timeservice.now_ist()
    pdb = get_project_session(project_id)
    try:
        created = 0
        for text in texts or []:
            if isinstance(text, str) and text.strip():
                pdb.add(Concern(ts=now, text=text.strip(), status="open"))
                created += 1
        pdb.commit()
        return {"created": created, "project_id": project_id}
    finally:
        pdb.close()


ADD_CONCERNS_SCHEMA = {
    "name": "add_concerns",
    "description": "Records risks/issues worth flagging on one project, status=open. Owned projects only. Blank entries are skipped.",
    "parameters": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string"},
            "texts": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["project_id", "texts"],
    },
}


def _events_for_project_shown(db: Session, project_id: str) -> List[Event]:
    """open_blockers input to the rubric, same filter pre-step-36 dream.py
    used inline (dream.py:256): type=blocker AND ui_state in
    (shown, promoted) -- a dismissed blocker no longer counts against
    health."""
    from app.projectkb.health_rubric import events_for_project

    return events_for_project(db, project_id, types={"blocker"}, ui_states={"shown", "promoted"})


def set_health_adjustment_handler(
    db: Session,
    manager_id: str,
    run_context: Optional[Dict[str, Any]],
    project_id: str,
    adjustment: Optional[int] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Computes the deterministic base score itself (app.projectkb.health_rubric,
    moved there verbatim from pre-step-36 dream.py so both this tool and --
    were it ever needed again -- a job could import it without a cycle) and
    writes the HealthLog row; the model supplies only the +/-1 nudge and a
    reason. adjustment is clamped to [-1, 1]; a non-zero adjustment without
    a non-blank reason is floored back to 0 (dream.py:264-268's rule) so an
    unexplained health swing can never land.

    Note on ordering: pre-step-36 dream.py deliberately committed the
    Suggestion/Concern/HealthLog DB rows BEFORE writing summary.md/events.md
    to disk, so a mid-run crash could never leave a freshly-written
    summary.md with no DB rows behind it (dream.py:233-241). Splitting the
    write surface into independent tools means the AGENT now chooses call
    order -- it may call write_project_summary before set_health_adjustment
    -- so that ordering guarantee is no longer enforceable from here. The
    residual risk is the same one the old docstring already accepted for
    the DB-commit-to-file-write gap (self-heals next dream tick once more
    events accumulate), just slightly widened to cover tool-call order too.
    """
    from app.projects.db import get_project_session
    from app.projects.models import Conflict, HealthLog, Task
    from app.projectkb.health_rubric import HEALTH_BAND_POINTS, compute_base_health_score, days_since_last_progress, is_overdue

    project_error = _require_owned_project(manager_id, project_id)
    if project_error is not None:
        return project_error

    try:
        adjustment = int(adjustment) if adjustment is not None else 0
    except (TypeError, ValueError):
        adjustment = 0
    adjustment = max(-1, min(1, adjustment))
    has_reason = isinstance(reason, str) and bool(reason.strip())
    if adjustment != 0 and not has_reason:
        adjustment = 0
    reason = reason.strip() if (adjustment != 0 and has_reason) else None

    now = timeservice.now_ist()
    open_blockers = len(_events_for_project_shown(db, project_id))

    pdb = get_project_session(project_id)
    try:
        tasks = pdb.query(Task).all()
        overdue_tasks = sum(1 for t in tasks if is_overdue(t, now))
        open_conflicts = pdb.query(Conflict).filter(Conflict.status == "open").count()
        days_since_progress = days_since_last_progress(db, project_id, now)
        base_score = compute_base_health_score(open_blockers, overdue_tasks, open_conflicts, days_since_progress)
        final_score = max(0, min(100, base_score + adjustment * HEALTH_BAND_POINTS))

        pdb.add(
            HealthLog(
                ts=now,
                rubric_inputs=json.dumps({
                    "open_blockers": open_blockers,
                    "overdue_tasks": overdue_tasks,
                    "open_conflicts": open_conflicts,
                    "days_since_progress": days_since_progress,
                }),
                base_score=base_score,
                llm_adjustment=adjustment,
                reason=reason,
                final_score=final_score,
            )
        )
        pdb.commit()
        return {
            "success": True,
            "project_id": project_id,
            "base_score": base_score,
            "adjustment": adjustment,
            "final_score": final_score,
            "reason": reason,
        }
    finally:
        pdb.close()


SET_HEALTH_ADJUSTMENT_SCHEMA = {
    "name": "set_health_adjustment",
    "description": (
        "Records your qualitative nudge to this project's health score. The base score is computed "
        "deterministically for you from open blockers/overdue tasks/conflicts/days since progress -- you "
        "never compute or supply the final score, only a nudge of -1 (worse), 0, or +1 (better) for "
        "signals those counts miss (e.g. milestone slippage visible in the project notes). reason is "
        "REQUIRED whenever adjustment is not 0 -- an unexplained nudge is floored back to 0. Owned "
        "projects only; call this once per project per run."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string"},
            "adjustment": {"type": "integer", "enum": [-1, 0, 1]},
            "reason": {"type": "string", "description": "Required whenever adjustment is not 0."},
        },
        "required": ["project_id", "adjustment"],
    },
}

# -----------------------------------------------------------------------------
# Registration
# -----------------------------------------------------------------------------


def register_tools() -> None:
    registry.register("search_events", SEARCH_EVENTS_SCHEMA, search_events_handler)
    registry.register("get_event", GET_EVENT_SCHEMA, get_event_handler, max_result_chars=6000)
    registry.register("search_claims", SEARCH_CLAIMS_SCHEMA, search_claims_handler)
    registry.register("get_thread", GET_THREAD_SCHEMA, get_thread_handler)
    registry.register("get_project_state", GET_PROJECT_STATE_SCHEMA, get_project_state_handler, max_result_chars=6000)
    registry.register("list_projects", LIST_PROJECTS_SCHEMA, list_projects_handler)
    registry.register("emit_events", EMIT_EVENTS_SCHEMA, emit_events_handler)
    registry.register("record_conflict", RECORD_CONFLICT_SCHEMA, record_conflict_handler)
    registry.register("apply_task_transitions", APPLY_TASK_TRANSITIONS_SCHEMA, apply_task_transitions_handler)
    registry.register("draft_tasks", DRAFT_TASKS_SCHEMA, draft_tasks_handler)
    registry.register("link_request_to_task", LINK_REQUEST_TO_TASK_SCHEMA, link_request_to_task_handler)
    registry.register("write_memory", WRITE_MEMORY_SCHEMA, write_memory_handler)
    registry.register("append_manager_events", APPEND_MANAGER_EVENTS_SCHEMA, append_manager_events_handler)
    registry.register("write_project_summary", WRITE_PROJECT_SUMMARY_SCHEMA, write_project_summary_handler)
    registry.register("append_project_events", APPEND_PROJECT_EVENTS_SCHEMA, append_project_events_handler)
    registry.register("add_suggestions", ADD_SUGGESTIONS_SCHEMA, add_suggestions_handler)
    registry.register("add_concerns", ADD_CONCERNS_SCHEMA, add_concerns_handler)
    registry.register("set_health_adjustment", SET_HEALTH_ADJUSTMENT_SCHEMA, set_health_adjustment_handler)


# Auto register on import
register_tools()
