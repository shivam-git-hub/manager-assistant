"""Step 19 (prompts/step_19_home_backend.md): home-dashboard backend --
user-maintained todos CRUD + the Updates panel's query over events.

Events are only ever created by jobs (ingest/heartbeat, later steps), so
event-read tests insert Event rows directly through the manager's session
(db_session fixture) -- there is deliberately no create-event API.
"""
import json
import shutil
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import timeservice
from app.database import Event
from app.main import app
from app.projects.db import get_project_session
from app.projects.models import Task
from app.projects.paths import project_dir


@pytest.fixture()
def project(client):
    """A team project owned by the client's throwaway manager (step 24's
    approve-with-linked-task tests need a real, scaffolded project db)."""
    r = client.post("/api/projects", json={"name": "Approve Test Project", "kind": "team"})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    yield pid
    shutil.rmtree(project_dir(pid), ignore_errors=True)


# ────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────

def _mk_event(db, *, severity=1, ui_state="shown", type="fyi", title=None,
              created_at=None, general=True):
    ev = Event(
        id=uuid.uuid4().hex,
        type=type,
        severity=severity,
        title=title or f"{type} sev{severity}",
        general=general,
        ui_state=ui_state,
        created_at=created_at or timeservice.now_ist(),
    )
    db.add(ev)
    db.commit()
    return ev


# ────────────────────────────────────────────────────────
# TODOs
# ────────────────────────────────────────────────────────

def test_todo_crud_lifecycle(client):
    # create
    r = client.post("/api/todos", json={"text": "Update my email credentials", "due": "2026-07-30T00:00:00"})
    assert r.status_code == 201, r.text
    todo = r.json()
    assert todo["text"] == "Update my email credentials"
    assert todo["status"] == "open"
    assert todo["due"].startswith("2026-07-30")

    r2 = client.post("/api/todos", json={"text": "Create meeting link with john"})
    assert r2.status_code == 201
    assert r2.json()["due"] is None

    # list (newest first)
    listed = client.get("/api/todos").json()
    assert [t["text"] for t in listed] == ["Create meeting link with john", "Update my email credentials"]

    # patch text + flip status
    r3 = client.patch(f"/api/todos/{todo['id']}", json={"text": "Update credentials NOW", "status": "done"})
    assert r3.status_code == 200
    assert r3.json()["text"] == "Update credentials NOW"
    assert r3.json()["status"] == "done"

    # status filter
    assert [t["id"] for t in client.get("/api/todos?status=open").json()] == [r2.json()["id"]]
    assert [t["id"] for t in client.get("/api/todos?status=done").json()] == [todo["id"]]

    # delete
    assert client.delete(f"/api/todos/{todo['id']}").status_code == 204
    assert all(t["id"] != todo["id"] for t in client.get("/api/todos").json())


def test_todo_404s(client):
    assert client.patch("/api/todos/nonexistent", json={"text": "x"}).status_code == 404
    assert client.delete("/api/todos/nonexistent").status_code == 404


def test_todo_invalid_status_rejected(client):
    r = client.post("/api/todos", json={"text": "a todo"})
    bad = client.patch(f"/api/todos/{r.json()['id']}", json={"status": "banana"})
    assert bad.status_code == 422


# ────────────────────────────────────────────────────────
# Events / Updates panel
# ────────────────────────────────────────────────────────

def test_events_severity_filter_and_ordering(client, db_session):
    now = timeservice.now_ist()
    _mk_event(db_session, severity=0, title="trivial")            # below threshold
    old_hi = _mk_event(db_session, severity=3, title="old critical", created_at=now - timedelta(hours=2))
    new_hi = _mk_event(db_session, severity=3, title="new critical", created_at=now)
    mid = _mk_event(db_session, severity=1, title="low update")

    body = client.get("/api/events?min_severity=1").json()
    titles = [e["title"] for e in body["events"]]
    # severity desc, then created_at desc within a severity band
    assert titles == ["new critical", "old critical", "low update"]
    assert body["total_matching"] == 3
    assert body["max_severity"] == 3
    assert new_hi.id == body["events"][0]["id"] and old_hi.id == body["events"][1]["id"]
    assert mid.id == body["events"][2]["id"]


def test_events_empty_state(client):
    body = client.get("/api/events").json()
    assert body["events"] == []
    assert body["total_matching"] == 0
    assert body["max_severity"] is None


def test_events_dismiss_excludes_from_default_query(client, db_session):
    ev = _mk_event(db_session, severity=2)
    r = client.post(f"/api/events/{ev.id}/dismiss")
    assert r.status_code == 200
    assert client.get("/api/events?min_severity=1").json()["events"] == []
    # but View All still shows it
    all_body = client.get("/api/events?min_severity=1&include_dismissed=true").json()
    assert [e["id"] for e in all_body["events"]] == [ev.id]
    assert all_body["events"][0]["ui_state"] == "dismissed"


def test_events_promote_included_even_below_threshold(client, db_session):
    ev = _mk_event(db_session, severity=0)  # below min_severity=1
    assert client.get("/api/events?min_severity=1").json()["events"] == []
    assert client.post(f"/api/events/{ev.id}/promote").status_code == 200
    body = client.get("/api/events?min_severity=1").json()
    assert [e["id"] for e in body["events"]] == [ev.id]
    assert body["events"][0]["ui_state"] == "promoted"


def test_events_promote_after_dismiss(client, db_session):
    ev = _mk_event(db_session, severity=2)
    client.post(f"/api/events/{ev.id}/dismiss")
    client.post(f"/api/events/{ev.id}/promote")
    body = client.get("/api/events?min_severity=1").json()
    assert [e["id"] for e in body["events"]] == [ev.id]


def test_events_state_404s(client):
    assert client.post("/api/events/nonexistent/dismiss").status_code == 404
    assert client.post("/api/events/nonexistent/promote").status_code == 404


# ── Approve/Reject (step 24 — request events only) ──────────────────────

def test_events_approve_request_excludes_from_default_query(client, db_session):
    ev = _mk_event(db_session, severity=2, type="request")
    r = client.post(f"/api/events/{ev.id}/approve")
    assert r.status_code == 200
    assert r.json()["ui_state"] == "approved"
    assert client.get("/api/events?min_severity=1").json()["events"] == []
    all_body = client.get("/api/events?min_severity=1&include_dismissed=true").json()
    assert [e["id"] for e in all_body["events"]] == [ev.id]


def test_events_reject_request_excludes_from_default_query(client, db_session):
    ev = _mk_event(db_session, severity=2, type="request")
    r = client.post(f"/api/events/{ev.id}/reject")
    assert r.status_code == 200
    assert r.json()["ui_state"] == "rejected"
    assert client.get("/api/events?min_severity=1").json()["events"] == []


def test_events_approve_reject_400_on_non_request_types(client, db_session):
    ev = _mk_event(db_session, severity=2, type="status_update")
    assert client.post(f"/api/events/{ev.id}/approve").status_code == 400
    assert client.post(f"/api/events/{ev.id}/reject").status_code == 400


def test_events_approve_reject_state_404s(client):
    assert client.post("/api/events/nonexistent/approve").status_code == 404
    assert client.post("/api/events/nonexistent/reject").status_code == 404


def test_events_approve_with_linked_task_marks_task_done(client, db_session, project):
    task_id = uuid.uuid4().hex
    session = get_project_session(project)
    try:
        session.add(Task(id=task_id, title="Linked task", status="in_progress", priority="medium", created_by="manager"))
        session.commit()
    finally:
        session.close()

    ev = _mk_event(db_session, severity=1, type="request")
    ev.project_ids = json.dumps([project])
    ev.task_ids = json.dumps([task_id])
    db_session.commit()

    r = client.post(f"/api/events/{ev.id}/approve")
    assert r.status_code == 200

    session = get_project_session(project)
    try:
        task = session.get(Task, task_id)
        assert task.status == "done"
    finally:
        session.close()


def test_events_approve_with_stale_task_reference_still_resolves(client, db_session, project):
    ev = _mk_event(db_session, severity=1, type="request")
    ev.project_ids = json.dumps([project])
    ev.task_ids = json.dumps(["deleted-task-id"])
    db_session.commit()

    r = client.post(f"/api/events/{ev.id}/approve")
    assert r.status_code == 200
    assert r.json()["ui_state"] == "approved"


def test_events_approve_still_resolves_when_task_mutation_raises(client, db_session, project, monkeypatch):
    """A failure while marking the linked task done (e.g. a locked project
    db) must never surface as a 500 -- the event's own approval already
    committed and must stay approved regardless."""
    from app.projects import db as projects_db_module

    def _boom(project_id):
        raise RuntimeError("simulated project db failure")

    monkeypatch.setattr(projects_db_module, "get_project_session", _boom)
    # home.py imports get_project_session inside the function body, so
    # patching the module-level symbol it resolves at call time is enough.

    task_id = uuid.uuid4().hex
    ev = _mk_event(db_session, severity=1, type="request")
    ev.project_ids = json.dumps([project])
    ev.task_ids = json.dumps([task_id])
    db_session.commit()

    r = client.post(f"/api/events/{ev.id}/approve")
    assert r.status_code == 200
    assert r.json()["ui_state"] == "approved"


def test_events_limit(client, db_session):
    for i in range(5):
        _mk_event(db_session, severity=1, title=f"e{i}")
    body = client.get("/api/events?min_severity=1&limit=2").json()
    assert len(body["events"]) == 2
    assert body["total_matching"] == 5  # total ignores limit


# ────────────────────────────────────────────────────────
# Isolation between users
# ────────────────────────────────────────────────────────

def test_isolation_between_users(client, db_session):
    _mk_event(db_session, severity=3, title="mine only")
    client.post("/api/todos", json={"text": "my private todo"})

    other = TestClient(app)
    r = other.post("/api/auth/dev-login", json={"email": f"other-{uuid.uuid4().hex}@test.local", "name": "Other"})
    assert r.status_code == 200
    try:
        assert other.get("/api/todos").json() == []
        body = other.get("/api/events?min_severity=1").json()
        assert body["events"] == [] and body["max_severity"] is None
    finally:
        import shutil
        from app.tenancy.paths import manager_dir
        shutil.rmtree(manager_dir(r.json()["id"]), ignore_errors=True)
