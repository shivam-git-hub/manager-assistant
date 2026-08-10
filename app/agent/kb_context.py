"""Context engineering for the KB agents (Phase A foundation for the
heartbeat/dream/lint rewrites, not yet wired to any job). Deliberately a
SEPARATE builder from app.agent.context.build_agent_context, which stays
exactly as-is for the chat/Slack-DM agent (a different surface with a
different, already-tuned prompt shape).

The goal here is narrower and more load-bearing: hand a job agent all the
information it needs to decide WHERE TO LOOK, and only that -- never full
project prose. A tool-using agent that starts with full summary.md text for
every project already has the depth it would otherwise spend a tool call
pulling on demand (get_project_state / get_project_doc), which just
bloats the prompt for the common case where it only needs to act on 1-2 of
them. Ids, names, and a health/blocker signal are what it needs to choose;
depth is one tool call away.
"""
import json
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app import timeservice
from app.agent.context import team_roster_for_manager, visible_projects_for_manager
from app.database import Event

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
    + "\n"
    "Severity is 0-3 (0=routine, 3=critical); blocker/clarification are code-floored "
    "to at least 1, never model-supplied below that.\n"
    "`general` on an event is DERIVED from project_ids being empty -- never set it "
    "yourself, it falls out of which project_ids you tag."
)

TRUNCATION_MARKER = "[context truncated to fit budget]"


@dataclass(frozen=True)
class ContextBudget:
    max_total_chars: int = 6000
    max_projects: int = 15
    max_roster: int = 40
    max_memory_chars: int = 1500


def _owner_section(manager_id: str) -> str:
    from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Employee

    cdb = ControlPlaneSessionLocal()
    try:
        employee = cdb.get(Employee, manager_id)
    finally:
        cdb.close()
    name = employee.name if employee else manager_id
    role = (employee.role if employee else None) or "manager"
    return f"## OWNER\n{name} | employee_id={manager_id} | {role}"


def _blocker_counts_by_project(db: Session) -> dict[str, int]:
    """One pass over the manager's open blocker events, tallied per project.

    Counted here rather than per-project because Event.project_ids is a
    JSON-text column that SQL can't filter on, so a per-project count means
    re-scanning every blocker row once per project -- O(projects x events)
    for a section that's rebuilt on every job tick.
    """
    counts: dict[str, int] = {}
    rows = db.query(Event.project_ids).filter(Event.type == "blocker").filter(Event.ui_state != "dismissed").all()
    for row in rows:
        for pid in (json.loads(row.project_ids) if row.project_ids else []):
            counts[pid] = counts.get(pid, 0) + 1
    return counts


def _project_task_counts_and_health(project_id: str) -> tuple[int, Optional[int]]:
    """Open-task count and latest health score in ONE project-db session --
    these are two reads of the same sqlite file, so opening it twice per
    project per context build is pure overhead. Open tasks come from the
    project's own db.sqlite; blockers come from the manager's Event table
    (see _blocker_counts_by_project) -- two different stores, matching how
    the codebase splits project-scoped vs manager-scoped state (CLAUDE.md's
    Data layout section)."""
    from app.projects.db import get_project_session
    from app.projects.models import HealthLog, Task

    pdb = get_project_session(project_id)
    try:
        open_tasks = pdb.query(Task).filter(Task.status != "done").count()
        latest = pdb.query(HealthLog).order_by(HealthLog.ts.desc()).first()
        return open_tasks, (latest.final_score if latest else None)
    finally:
        pdb.close()


def _projects_section(db: Session, manager_id: str, budget: ContextBudget) -> str:
    projects = visible_projects_for_manager(manager_id)
    lines = ["## PROJECTS"]
    rendered = projects[: budget.max_projects]
    overflow = len(projects) - len(rendered)
    blocker_counts = _blocker_counts_by_project(db) if rendered else {}
    for p in rendered:
        role = "manager" if p["is_manager"] else "member"
        open_tasks, health = _project_task_counts_and_health(p["id"])
        blockers = blocker_counts.get(p["id"], 0)
        health_str = str(health) if health is not None else "n/a"
        lines.append(
            f"- id={p['id']} | {p['name']} | {p['kind']} | you are {role} | "
            f"open_tasks={open_tasks} | blockers={blockers} | health={health_str}"
        )
    if overflow > 0:
        lines.append(f"- … {overflow} more projects, use list_projects to see them")
    if len(rendered) == 0 and overflow == 0:
        lines.append("- (none)")
    return "\n".join(lines)


def _team_section(manager_id: str, budget: ContextBudget) -> str:
    roster = team_roster_for_manager(manager_id)
    lines = ["## TEAM"]
    rendered = roster[: budget.max_roster]
    overflow = len(roster) - len(rendered)
    for r in rendered:
        lines.append(f"- {r['id']} | {r['name']} | {r['role'] or 'no role'} | slack:{'yes' if r['has_slack'] else 'no'}")
    if overflow > 0:
        lines.append(f"- … {overflow} more teammates, use list_team to see them")
    if len(rendered) == 0 and overflow == 0:
        lines.append("- (none)")
    return "\n".join(lines)


def _memory_section(manager_id: str, budget: ContextBudget) -> Optional[str]:
    from app.tenancy.paths import manager_memory_md_path

    path = manager_memory_md_path(manager_id)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if not text.strip():
        return None
    tail = text[-budget.max_memory_chars :] if len(text) > budget.max_memory_chars else text
    return f"## YOUR DURABLE MEMORY\n{tail}"


def _now_section() -> str:
    now = timeservice.now_ist()
    return f"## NOW\nCurrent date and time: {now.strftime('%Y-%m-%d %H:%M:%S')} IST ({now.strftime('%A')})"


def build_kb_context(
    db: Session,
    manager_id: str,
    *,
    budget: ContextBudget = ContextBudget(),
    include_memory: bool = True,
    include_conventions: bool = True,
) -> str:
    """Assembles the bounded job-agent context, sections in order:
    OWNER, PROJECTS, TEAM, [YOUR DURABLE MEMORY], [KB CONVENTIONS], NOW.

    `## NOW` is always emitted last, deliberately: recency information
    landing at the very end of the prompt (right before the model has to
    act) is a well-known way to keep it from getting lost in the middle of
    a longer context -- do not reorder this.

    If the assembled string still exceeds `budget.max_total_chars`, sections
    are dropped whole (never blind-sliced -- a half-cut section reads worse
    to the model than an absent one) in priority order: memory, then
    conventions, then re-rendering PROJECTS at a smaller max_projects. A
    `[context truncated to fit budget]` marker is inserted immediately
    before `## NOW` (so NOW still ends up last) whenever anything was
    dropped or shrunk.
    """
    owner = _owner_section(manager_id)
    team = _team_section(manager_id, budget)
    memory = _memory_section(manager_id, budget) if include_memory else None
    conventions = KB_CONVENTIONS_TEXT if include_conventions else None
    now = _now_section()

    def assemble(projects_budget: ContextBudget, keep_memory: bool, keep_conventions: bool, truncated: bool) -> str:
        parts = [owner, _projects_section(db, manager_id, projects_budget), team]
        if keep_memory and memory:
            parts.append(memory)
        if keep_conventions and conventions:
            parts.append(conventions)
        if truncated:
            parts.append(TRUNCATION_MARKER)
        parts.append(now)
        return "\n\n".join(parts)

    text = assemble(budget, True, True, False)
    if len(text) <= budget.max_total_chars:
        return text

    # Drop memory first (recomputable synthesis, least essential to a
    # single job-agent tick), then conventions (static, re-derivable from
    # this same docstring/prompt), then shrink the PROJECTS section itself.
    text = assemble(budget, False, True, True)
    if len(text) <= budget.max_total_chars:
        return text

    text = assemble(budget, False, False, True)
    if len(text) <= budget.max_total_chars:
        return text

    shrink_budget = budget
    while shrink_budget.max_projects > 0:
        shrink_budget = ContextBudget(
            max_total_chars=budget.max_total_chars,
            max_projects=max(shrink_budget.max_projects // 2, 0),
            max_roster=shrink_budget.max_roster,
            max_memory_chars=shrink_budget.max_memory_chars,
        )
        text = assemble(shrink_budget, False, False, True)
        if len(text) <= budget.max_total_chars or shrink_budget.max_projects == 0:
            return text
    return text
