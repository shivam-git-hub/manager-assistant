"""Step 18 -- global projects registry, employees directory, project
scaffold (prompts/step_18_registry_and_scaffold.md). Follows the isolation
patterns already used by tests/test_agents.py / tests/test_auth.py (direct
ControlPlaneSessionLocal() use for control-plane rows) and
tests/test_tenancy.py (real on-disk dirs, rmtree'd in teardown -- not
env-redirected, same as managers/<id>/).
"""
import json
import shutil
import uuid

import pytest
from sqlalchemy import inspect
from fastapi.testclient import TestClient

from app.main import app
from app.controlplane.models import (
    SessionLocal as ControlPlaneSessionLocal,
    Employee,
    Project as RegistryProject,
    ProjectMember,
)
from app.projects.paths import (
    project_dir,
    vault_dir,
    project_md_path,
    summary_md_path,
    events_md_path,
    notes_md_path,
)
from app.projects.db import get_project_engine


# ────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────

def _login(email, name="Test User"):
    c = TestClient(app)
    r = c.post("/api/auth/dev-login", json={"email": email, "name": name})
    assert r.status_code == 200, r.text
    c.manager_id = r.json()["id"]
    return c


def _make_employee(email, name, slack_id=None, role=None, skills=None):
    db = ControlPlaneSessionLocal()
    try:
        emp = Employee(
            id=uuid.uuid4().hex,
            email=email.lower(),
            name=name,
            slack_id=slack_id,
            role=role,
            skills=json.dumps(skills) if skills else None,
        )
        db.add(emp)
        db.commit()
        db.refresh(emp)
        return {"id": emp.id, "email": emp.email, "name": emp.name}
    finally:
        db.close()


@pytest.fixture
def extra_login():
    """Yields a factory for logging in additional throwaway managers beyond
    the `client` fixture's own one -- needed for the manager/member/stranger
    visibility tests, which need three distinct logged-in identities sharing
    the same control-plane db."""
    created = []

    def _make(email, name="Test User"):
        c = _login(email, name)
        created.append(c.manager_id)
        return c

    yield _make

    from app.tenancy.paths import manager_dir
    for mid in created:
        shutil.rmtree(manager_dir(mid), ignore_errors=True)


# ────────────────────────────────────────────────────────
# 1. Create team project -> registry row, member rows, scaffold on disk
# ────────────────────────────────────────────────────────

def test_create_team_project_scaffolds_everything(client, cleanup_projects):
    emp_a = _make_employee("alice@example.com", "Alice")
    emp_b = _make_employee("bob@example.com", "Bob")

    payload = {
        "name": "Phoenix",
        "description": "Rebuild the checkout flow",
        "kind": "team",
        "member_employee_ids": [
            {"employee_id": emp_a["id"], "role": "engineer"},
            {"employee_id": emp_b["id"]},
        ],
        "supervisors": [emp_a["id"]],
    }
    r = client.post("/api/projects", json=payload)
    assert r.status_code == 201, r.text
    body = r.json()
    project_id = body["id"]
    cleanup_projects.append(project_id)

    assert body["name"] == "Phoenix"
    assert body["kind"] == "team"

    # Registry row + member rows land in the control-plane db.
    db = ControlPlaneSessionLocal()
    try:
        row = db.get(RegistryProject, project_id)
        assert row is not None
        assert row.manager_user_id == client.manager_id
        assert row.kind == "team"

        members = db.query(ProjectMember).filter(ProjectMember.project_id == project_id).all()
        assert {m.employee_id for m in members} == {emp_a["id"], emp_b["id"]}
    finally:
        db.close()

    # Scaffold on disk: all four md files + vault/ + project db with all six tables.
    assert project_md_path(project_id).exists()
    assert summary_md_path(project_id).exists()
    assert events_md_path(project_id).exists()
    assert notes_md_path(project_id).exists()
    assert vault_dir(project_id).is_dir()

    assert "Phoenix" in project_md_path(project_id).read_text(encoding="utf-8")

    engine = get_project_engine(project_id)
    tables = set(inspect(engine).get_table_names())
    assert tables == {"tasks", "archive", "conflicts", "suggestions", "concerns", "health_log"}


# ────────────────────────────────────────────────────────
# 2. GET /api/projects visibility: manager, member (email match), stranger
# ────────────────────────────────────────────────────────

def test_get_projects_visibility_manager_member_stranger(client, extra_login, cleanup_projects):
    member_client = extra_login("carol@example.com", "Carol")
    stranger_client = extra_login("dave@example.com", "Dave")

    emp_carol = _make_employee("carol@example.com", "Carol")

    r = client.post("/api/projects", json={
        "name": "Voyager",
        "kind": "team",
        "member_employee_ids": [{"employee_id": emp_carol["id"], "role": "designer"}],
    })
    assert r.status_code == 201, r.text
    project_id = r.json()["id"]
    cleanup_projects.append(project_id)

    # Manager sees it, is_manager=True.
    r_owner = client.get("/api/projects")
    assert r_owner.status_code == 200
    owner_projects = {p["id"]: p for p in r_owner.json()}
    assert project_id in owner_projects
    assert owner_projects[project_id]["is_manager"] is True

    # Member (email match) sees it, is_manager=False.
    r_member = member_client.get("/api/projects")
    assert r_member.status_code == 200
    member_projects = {p["id"]: p for p in r_member.json()}
    assert project_id in member_projects
    assert member_projects[project_id]["is_manager"] is False

    # Unrelated third user sees nothing.
    r_stranger = stranger_client.get("/api/projects")
    assert r_stranger.status_code == 200
    assert project_id not in {p["id"] for p in r_stranger.json()}


# ────────────────────────────────────────────────────────
# 3. Personal project: no members allowed, private to owner
# ────────────────────────────────────────────────────────

def test_personal_project_is_private_and_rejects_members(client, extra_login, cleanup_projects):
    other_client = extra_login("erin@example.com", "Erin")

    r = client.post("/api/projects", json={"name": "My Task List", "kind": "personal"})
    assert r.status_code == 201, r.text
    project_id = r.json()["id"]
    cleanup_projects.append(project_id)

    r_owner = client.get("/api/projects")
    assert project_id in {p["id"] for p in r_owner.json()}

    r_other = other_client.get("/api/projects")
    assert project_id not in {p["id"] for p in r_other.json()}

    emp = _make_employee("frank@example.com", "Frank")
    r_bad = client.post("/api/projects", json={
        "name": "Bad Personal",
        "kind": "personal",
        "member_employee_ids": [{"employee_id": emp["id"]}],
    })
    assert r_bad.status_code == 400


# ────────────────────────────────────────────────────────
# 4. GET /{id} 404 for non-member; PATCH 403 for non-manager; PATCH member
#    add/remove works for the manager
# ────────────────────────────────────────────────────────

def test_project_detail_404_and_patch_permissions(client, extra_login, cleanup_projects):
    member_client = extra_login("grace@example.com", "Grace")
    stranger_client = extra_login("heidi@example.com", "Heidi")

    emp_grace = _make_employee("grace@example.com", "Grace")
    emp_ivan = _make_employee("ivan@example.com", "Ivan")

    r = client.post("/api/projects", json={
        "name": "Atlas",
        "kind": "team",
        "member_employee_ids": [{"employee_id": emp_grace["id"], "role": "lead"}],
    })
    assert r.status_code == 201, r.text
    project_id = r.json()["id"]
    cleanup_projects.append(project_id)

    # Stranger gets 404 on detail.
    r_404 = stranger_client.get(f"/api/projects/{project_id}")
    assert r_404.status_code == 404

    # Manager and member can both see the detail.
    r_detail = client.get(f"/api/projects/{project_id}")
    assert r_detail.status_code == 200
    member_emails = {m["email"] for m in r_detail.json()["members"]}
    assert "grace@example.com" in member_emails

    r_member_detail = member_client.get(f"/api/projects/{project_id}")
    assert r_member_detail.status_code == 200

    # PATCH by non-manager (member) -> 403.
    r_403 = member_client.patch(f"/api/projects/{project_id}", json={"description": "hijacked"})
    assert r_403.status_code == 403

    # PATCH by manager: add ivan, remove grace.
    r_patch = client.patch(f"/api/projects/{project_id}", json={
        "description": "Updated description",
        "add_member_employee_ids": [{"employee_id": emp_ivan["id"], "role": "eng"}],
        "remove_member_employee_ids": [emp_grace["id"]],
    })
    assert r_patch.status_code == 200, r_patch.text
    body = r_patch.json()
    assert body["description"] == "Updated description"
    member_ids_after = {m["employee_id"] for m in body["members"]}
    assert member_ids_after == {emp_ivan["id"]}


# ────────────────────────────────────────────────────────
# 5. GET /api/employees returns seeded directory
# ────────────────────────────────────────────────────────

def test_get_employees_returns_seeded_directory(client):
    _make_employee("judy@example.com", "Judy", slack_id="U_JUDY", role="PM", skills=["planning"])
    _make_employee("kim@example.com", "Kim", role="Engineer", skills=["python", "sql"])

    r = client.get("/api/employees")
    assert r.status_code == 200
    by_email = {e["email"]: e for e in r.json()}
    assert "judy@example.com" in by_email
    assert by_email["judy@example.com"]["name"] == "Judy"
    assert by_email["judy@example.com"]["slack_id"] == "U_JUDY"
    assert by_email["judy@example.com"]["skills"] == ["planning"]
    assert by_email["kim@example.com"]["skills"] == ["python", "sql"]


# ────────────────────────────────────────────────────────
# 6. Seed script upsert: run twice with an edit -> no duplicates, updated
# ────────────────────────────────────────────────────────

def test_seed_employees_script_upserts_by_email(clean_controlplane_db, tmp_path):
    from scripts.seed_employees import seed

    employees_path = tmp_path / "employees.json"
    employees_path.write_text(json.dumps([
        {"email": "Leo@Example.com", "name": "Leo", "role": "Intern", "skills": ["excel"]},
    ]), encoding="utf-8")

    seed(employees_path)

    db = ControlPlaneSessionLocal()
    try:
        rows = db.query(Employee).filter(Employee.email == "leo@example.com").all()
        assert len(rows) == 1
        assert rows[0].role == "Intern"
    finally:
        db.close()

    # Re-run with an edited role/skills for the same email.
    employees_path.write_text(json.dumps([
        {"email": "Leo@Example.com", "name": "Leo", "role": "Senior Intern", "skills": ["excel", "sql"]},
    ]), encoding="utf-8")

    seed(employees_path)

    db = ControlPlaneSessionLocal()
    try:
        rows = db.query(Employee).filter(Employee.email == "leo@example.com").all()
        assert len(rows) == 1
        assert rows[0].role == "Senior Intern"
        assert json.loads(rows[0].skills) == ["excel", "sql"]
    finally:
        db.close()
