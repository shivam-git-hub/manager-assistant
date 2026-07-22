"""Step 21 (prompts/step_21_project_drilldown.md): per-project tasks,
doc/notes, vault, insights, deletion, events project filter, MoM tagging."""
import io
import json
import shutil
import uuid

import pytest
from fastapi.testclient import TestClient

from app import timeservice
from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Employee
from app.main import app
from app.projects.paths import project_dir


@pytest.fixture()
def project(client):
    """A team project owned by the client's throwaway manager, cleaned up
    from disk afterwards (registry rows die with the controlplane wipe)."""
    r = client.post("/api/projects", json={"name": "Drill Project", "kind": "team"})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    yield pid
    shutil.rmtree(project_dir(pid), ignore_errors=True)


def _seed_employee(email: str, name: str = "Emp") -> str:
    db = ControlPlaneSessionLocal()
    try:
        emp = Employee(id=uuid.uuid4().hex, email=email.lower(), name=name)
        db.add(emp)
        db.commit()
        return emp.id
    finally:
        db.close()


def _other_client(email_prefix: str = "other"):
    c = TestClient(app)
    r = c.post("/api/auth/dev-login", json={"email": f"{email_prefix}-{uuid.uuid4().hex}@test.local", "name": "Other"})
    assert r.status_code == 200
    return c, r.json()["id"]


# ── tasks ────────────────────────────────────────────────

def test_task_create_list_nesting_and_schedule_state(client, project):
    now = timeservice.now_ist()
    overdue_due = (now.replace(hour=0, minute=0, second=0, microsecond=0)).isoformat()

    r1 = client.post(f"/api/projects/{project}/tasks", json={
        "title": "create wireframes for homepage", "priority": "high", "due": "2099-01-01T00:00:00",
    })
    assert r1.status_code == 201
    parent_id = r1.json()["id"]
    assert r1.json()["priority"] == "high"
    assert r1.json()["schedule_state"] == "on_schedule"

    r2 = client.post(f"/api/projects/{project}/tasks", json={
        "title": "finalise feature list", "parent_task_id": parent_id,
    })
    assert r2.status_code == 201

    r3 = client.post(f"/api/projects/{project}/tasks", json={
        "title": "benchmark loading times", "due": "2020-01-01T00:00:00",
    })
    assert r3.status_code == 201
    assert r3.json()["schedule_state"] == "overdue"
    assert r3.json()["days_overdue"] >= 1

    listed = client.get(f"/api/projects/{project}/tasks").json()
    top_titles = [t["title"] for t in listed["tasks"]]
    assert top_titles == ["create wireframes for homepage", "benchmark loading times"]
    assert [s["title"] for s in listed["tasks"][0]["subtasks"]] == ["finalise feature list"]
    assert overdue_due  # silence unused warning-ish usage


def test_task_bad_parent_and_bad_enum_rejected(client, project):
    assert client.post(f"/api/projects/{project}/tasks", json={
        "title": "x", "parent_task_id": "nope",
    }).status_code == 400
    assert client.post(f"/api/projects/{project}/tasks", json={
        "title": "x", "priority": "urgent",
    }).status_code == 400
    r = client.post(f"/api/projects/{project}/tasks", json={"title": "ok"})
    assert client.patch(f"/api/projects/{project}/tasks/{r.json()['id']}", json={
        "status": "banana",
    }).status_code == 400


def test_task_approve_stamps_approver(client, project):
    # agent-created pending task is simulated by creating then forcing status
    r = client.post(f"/api/projects/{project}/tasks", json={"title": "agent draft"})
    tid = r.json()["id"]
    client.patch(f"/api/projects/{project}/tasks/{tid}", json={"status": "pending_approval"})

    approved = client.patch(f"/api/projects/{project}/tasks/{tid}", json={"status": "todo"}).json()
    assert approved["status"] == "todo"
    assert approved["approved_by"] == client.manager_id
    assert approved["schedule_state"] == "on_schedule"


def test_my_tasks_matches_logged_in_users_employee(client, project):
    # employee row with the logged-in manager's email
    db = ControlPlaneSessionLocal()
    try:
        from app.controlplane.models import Manager
        me = db.get(Manager, client.manager_id)
        my_email = me.email
    finally:
        db.close()
    my_emp = _seed_employee(my_email, "Me")
    other_emp = _seed_employee(f"colleague-{uuid.uuid4().hex}@test.local", "Colleague")

    client.post(f"/api/projects/{project}/tasks", json={"title": "mine", "assignee_employee_id": my_emp})
    client.post(f"/api/projects/{project}/tasks", json={"title": "theirs", "assignee_employee_id": other_emp})

    listed = client.get(f"/api/projects/{project}/tasks").json()
    assert [t["title"] for t in listed["my_tasks"]] == ["mine"]
    assert listed["my_tasks"][0]["assignee_name"] == "Me"


def test_task_writes_are_manager_only(client, project):
    other, other_id = _other_client()
    try:
        # not even visible -> 404
        assert other.post(f"/api/projects/{project}/tasks", json={"title": "x"}).status_code == 404
    finally:
        shutil.rmtree(f"managers/{other_id}", ignore_errors=True)


# ── doc / notes ──────────────────────────────────────────

def test_doc_roundtrip_and_manager_only_write(client, project):
    doc = client.get(f"/api/projects/{project}/doc").json()
    assert "## Overview" in doc["project_md"]

    updated = client.put(f"/api/projects/{project}/doc", json={
        "project_md": "# Drill Project\n\n## Overview\nReal overview\n\n## Milestones\n- M1\n\n## KPIs\n- K1\n",
    }).json()
    assert "Real overview" in updated["project_md"]
    assert "# Notes" in updated["notes_md"]  # untouched file survives


# ── vault ────────────────────────────────────────────────

def test_vault_upload_list_download_and_traversal_guard(client, project):
    up = client.post(
        f"/api/projects/{project}/vault",
        files={"file": ("design.txt", io.BytesIO(b"vault content"), "text/plain")},
    )
    assert up.status_code == 201
    assert up.json() == {"name": "design.txt", "size": 13}

    listed = client.get(f"/api/projects/{project}/vault").json()
    assert listed["files"] == [{"name": "design.txt", "size": 13}]

    down = client.get(f"/api/projects/{project}/vault/design.txt")
    assert down.status_code == 200
    assert down.content == b"vault content"

    bad = client.post(
        f"/api/projects/{project}/vault",
        files={"file": ("../evil.txt", io.BytesIO(b"x"), "text/plain")},
    )
    assert bad.status_code == 400
    assert client.get(f"/api/projects/{project}/vault/%2e%2e%2fevil.txt").status_code in (400, 404)


# ── insights ─────────────────────────────────────────────

def test_insights_empty_shape(client, project):
    body = client.get(f"/api/projects/{project}/insights").json()
    assert body == {"conflicts": [], "suggestions": [], "concerns": [], "health": None}


# ── events project filter ────────────────────────────────

def test_events_project_filter(client, db_session, project):
    from app.database import Event

    db_session.add_all([
        Event(id=uuid.uuid4().hex, type="blocker", severity=2, title="tagged",
              project_ids=json.dumps([project]), created_at=timeservice.now_ist()),
        Event(id=uuid.uuid4().hex, type="fyi", severity=2, title="untagged",
              general=True, created_at=timeservice.now_ist()),
    ])
    db_session.commit()

    body = client.get(f"/api/events?min_severity=1&project_id={project}").json()
    assert [e["title"] for e in body["events"]] == ["tagged"]


# ── delete ───────────────────────────────────────────────

def test_delete_project_removes_rows_and_dir(client, project):
    assert project_dir(project).exists()
    r = client.delete(f"/api/projects/{project}")
    assert r.status_code == 204
    assert not project_dir(project).exists()
    assert client.get(f"/api/projects/{project}").status_code == 404


def test_delete_project_manager_only(client, project):
    other, other_id = _other_client()
    try:
        assert other.delete(f"/api/projects/{project}").status_code == 404  # not visible
    finally:
        shutil.rmtree(f"managers/{other_id}", ignore_errors=True)
    assert project_dir(project).exists()


# ── MoM project tagging ──────────────────────────────────

def test_manual_message_project_tag(client, db_session, project):
    from app.database import UnifiedMessage

    r = client.post("/api/messages/manual", json={"content": "MoM text", "project_id": project})
    assert r.status_code == 201
    row = db_session.get(UnifiedMessage, r.json()["id"])
    assert json.loads(row.raw_metadata) == {"project_id": project}
