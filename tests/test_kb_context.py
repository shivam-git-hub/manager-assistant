"""app.agent.kb_context.build_kb_context -- the bounded job-agent context
(Phase A foundation, not yet wired to any job). Covers: section order with
## NOW always last, the NOW timestamp reflecting a monkeypatched now_ist,
the budget/overflow-marker behavior for a manager with far more projects
than fit, and that team ids are never silently truncated under the cap."""
import uuid
from datetime import datetime

import pytest

from app import timeservice
from app.agent import kb_context as kb_context_module
from app.agent.kb_context import ContextBudget, build_kb_context
from app.controlplane.models import (
    SessionLocal as ControlPlaneSessionLocal,
    Project as RegistryProject,
    Employee,
    get_member_list,
    set_member_list,
)


def _make_owned_project(manager_id: str, name: str, scaffold: bool = True) -> str:
    """`scaffold=False` skips creating the project's own db.sqlite -- only
    safe when the caller also monkeypatches the per-project lookups in
    kb_context (see test_200_projects_stays_under_budget_with_overflow_marker),
    since kb_context._project_open_task_and_blocker_counts/_project_health
    otherwise hit a real, uninitialized project db."""
    cdb = ControlPlaneSessionLocal()
    try:
        project = RegistryProject(id=uuid.uuid4().hex, name=name, kind="team", manager_user_id=manager_id)
        cdb.add(project)
        cdb.commit()
        pid = project.id
    finally:
        cdb.close()
    if scaffold:
        from app.projects.paths import ensure_project_scaffold

        ensure_project_scaffold(pid, name)
    return pid


def _make_employee(name: str, slack_id=None) -> str:
    cdb = ControlPlaneSessionLocal()
    try:
        emp = Employee(id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex}@test.local", name=name, slack_id=slack_id)
        cdb.add(emp)
        cdb.commit()
        return emp.id
    finally:
        cdb.close()


def _add_member(project_id: str, employee_id: str):
    cdb = ControlPlaneSessionLocal()
    try:
        project = cdb.get(RegistryProject, project_id)
        members = get_member_list(project)
        members.append({"employee_id": employee_id, "role": None})
        set_member_list(project, members)
        cdb.commit()
    finally:
        cdb.close()


# -----------------------------------------------------------------------------
# Section order / NOW is last / timestamp reflects monkeypatched now_ist
# -----------------------------------------------------------------------------


def test_now_is_last_and_reflects_monkeypatched_time(db_session, client, monkeypatch):
    fixed = datetime(2026, 7, 12, 15, 30, 0)
    monkeypatch.setattr(timeservice, "now_ist", lambda: fixed)

    text = build_kb_context(db_session, client.manager_id)

    lines = [l for l in text.split("\n") if l.strip()]
    assert lines[-2] == "## NOW"
    assert lines[-1] == f"Current date and time: {fixed.strftime('%Y-%m-%d %H:%M:%S')} IST ({fixed.strftime('%A')})"


def test_section_order_owner_projects_team_then_now(db_session, client):
    text = build_kb_context(db_session, client.manager_id)
    idx_owner = text.index("## OWNER")
    idx_projects = text.index("## PROJECTS")
    idx_team = text.index("## TEAM")
    idx_now = text.index("## NOW")
    assert idx_owner < idx_projects < idx_team < idx_now
    # NOW must be the literal last section header in the string.
    assert text.rindex("## NOW") == idx_now
    assert idx_now > text.rindex("## TEAM")


def test_conventions_section_present_and_before_now_when_included(db_session, client):
    text = build_kb_context(db_session, client.manager_id, include_conventions=True)
    assert "## KB CONVENTIONS" in text
    assert text.index("## KB CONVENTIONS") < text.index("## NOW")
    assert len(kb_context_module.KB_CONVENTIONS_TEXT) < 900


def test_conventions_section_omitted_when_excluded(db_session, client):
    text = build_kb_context(db_session, client.manager_id, include_conventions=False)
    assert "## KB CONVENTIONS" not in text


def test_memory_section_omitted_when_file_missing_or_empty(db_session, client):
    text = build_kb_context(db_session, client.manager_id, include_memory=True)
    assert "## YOUR DURABLE MEMORY" not in text  # fresh manager, no memory.md content yet


def test_memory_section_present_and_tail_truncated(db_session, client):
    from app.tenancy.paths import manager_memory_md_path

    path = manager_memory_md_path(client.manager_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("OLD STUFF " * 500 + "FRESHEST TAIL CONTENT", encoding="utf-8")

    budget = ContextBudget(max_memory_chars=200)
    text = build_kb_context(db_session, client.manager_id, budget=budget, include_memory=True)

    assert "## YOUR DURABLE MEMORY" in text
    assert text.rstrip().endswith("FRESHEST TAIL CONTENT") is False  # NOW/conventions still follow it
    assert "FRESHEST TAIL CONTENT" in text
    memory_section = text.split("## YOUR DURABLE MEMORY\n", 1)[1].split("\n\n## ", 1)[0]
    assert len(memory_section) <= budget.max_memory_chars
    assert memory_section.endswith("FRESHEST TAIL CONTENT")  # tail, not head, survives


# -----------------------------------------------------------------------------
# Budget / overflow for a manager with far more projects than fit
# -----------------------------------------------------------------------------


def test_200_projects_stays_under_budget_with_overflow_marker(db_session, client, monkeypatch, cleanup_projects):
    # Only the first `max_projects` rendered rows ever touch a real
    # per-project db.sqlite (see kb_context._projects_section) -- patch the
    # per-project lookup to avoid materializing 200 sqlite files in a unit
    # test that's about context-budget behavior, not per-project counting
    # (that's covered by test_kb_pipeline... / dedicated task-count tests).
    monkeypatch.setattr(kb_context_module, "_project_task_counts_and_health", lambda pid: (0, None))

    for i in range(200):
        pid = _make_owned_project(client.manager_id, f"Project {i}", scaffold=False)
        cleanup_projects.append(pid)

    budget = ContextBudget()  # default max_projects=15, max_total_chars=6000
    text = build_kb_context(db_session, client.manager_id, budget=budget)

    assert len(text) <= budget.max_total_chars
    assert "more projects, use list_projects to see them" in text
    assert text.rindex("## NOW") == text.index("## NOW")
    assert "## NOW" in text[-100:]  # still the tail of the prompt


# -----------------------------------------------------------------------------
# Team ids: complete-or-explicitly-truncated, never silently short
# -----------------------------------------------------------------------------


def test_team_ids_appear_in_full_when_under_cap(db_session, client, cleanup_projects):
    pid = _make_owned_project(client.manager_id, "Roster Project")
    cleanup_projects.append(pid)
    employee_ids = []
    for i in range(5):
        eid = _make_employee(f"Person {i}")
        _add_member(pid, eid)
        employee_ids.append(eid)

    text = build_kb_context(db_session, client.manager_id)

    assert "more teammates" not in text
    for eid in employee_ids:
        assert eid in text


def test_team_overflow_marker_when_over_cap(db_session, client, cleanup_projects):
    pid = _make_owned_project(client.manager_id, "Big Roster Project")
    cleanup_projects.append(pid)
    for i in range(45):
        eid = _make_employee(f"Person {i}")
        _add_member(pid, eid)

    budget = ContextBudget(max_roster=40)
    text = build_kb_context(db_session, client.manager_id, budget=budget)

    assert "more teammates, use list_team to see them" in text
