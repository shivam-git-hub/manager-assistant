"""Deterministic candidate selection. Follows
tests/test_projects_registry.py's isolation patterns (real project dirs,
rmtree'd via cleanup_projects)."""
import json
import uuid
from datetime import timedelta

import pytest

from app import timeservice
from app.database import AgentActionLog, Event, Meeting
from app.agent.select import build_candidates


@pytest.fixture
def team_project(client, cleanup_projects):
    r = client.post("/api/projects", json={"name": "Phoenix", "kind": "team", "description": "Test project description for automated tests."})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    cleanup_projects.append(pid)
    return pid


def test_pre_meeting_brief_candidate_in_window(db_session, client):
    now = timeservice.now_ist()
    db_session.add(Meeting(title="Standup", starts_at=now + timedelta(hours=1), attendees=json.dumps(["U_BOB"]), status="scheduled"))
    db_session.commit()

    candidates = build_candidates(db_session, client.manager_id)
    kinds = [c.kind for c in candidates]
    assert "pre_meeting_brief" in kinds


def test_pre_meeting_brief_candidate_out_of_window(db_session, client):
    now = timeservice.now_ist()
    db_session.add(Meeting(title="Next week sync", starts_at=now + timedelta(days=3), attendees="[]", status="scheduled"))
    db_session.commit()

    candidates = build_candidates(db_session, client.manager_id)
    assert not any(c.kind == "pre_meeting_brief" for c in candidates)


def test_pre_meeting_brief_already_sent_is_excluded(db_session, client):
    now = timeservice.now_ist()
    db_session.add(Meeting(title="Standup", starts_at=now + timedelta(minutes=30), attendees="[]", status="scheduled", brief_sent_at=now))
    db_session.commit()

    candidates = build_candidates(db_session, client.manager_id)
    assert not any(c.kind == "pre_meeting_brief" for c in candidates)


def test_followup_candidate_for_overdue_task(db_session, client, team_project):
    from app.projects.db import get_project_session
    from app.projects.models import Task

    pdb = get_project_session(team_project)
    now = timeservice.now_ist()
    task_id = uuid.uuid4().hex
    task = Task(id=task_id, title="Ship it", status="todo", due=now - timedelta(days=2), created_by="manager")
    pdb.add(task)
    pdb.commit()
    pdb.close()

    candidates = build_candidates(db_session, client.manager_id)
    followups = [c for c in candidates if c.kind == "followup"]
    assert len(followups) == 1
    assert followups[0].data["task_id"] == task_id


def test_followup_excludes_pending_approval_tasks(db_session, client, team_project):
    """pending_approval tasks are agent-drafted, awaiting the manager's own
    approval -- there's no approved assignee action to nudge about yet
    (dream.py excludes them from 'open' task counting the same way)."""
    from app.projects.db import get_project_session
    from app.projects.models import Task

    pdb = get_project_session(team_project)
    now = timeservice.now_ist()
    task = Task(
        id=uuid.uuid4().hex, title="Draft by agent", status="pending_approval",
        due=now - timedelta(days=2), created_by="agent",
    )
    pdb.add(task)
    pdb.commit()
    pdb.close()

    candidates = build_candidates(db_session, client.manager_id)
    assert not any(c.kind == "followup" for c in candidates)


def test_followup_candidate_deduped_same_day(db_session, client, team_project):
    from app.projects.db import get_project_session
    from app.projects.models import Task

    pdb = get_project_session(team_project)
    now = timeservice.now_ist()
    task_id = uuid.uuid4().hex
    task = Task(id=task_id, title="Ship it", status="blocked", created_by="manager")
    pdb.add(task)
    pdb.commit()
    pdb.close()

    ref_key = f"task:{team_project}:{task_id}:{now.date().isoformat()}"
    db_session.add(AgentActionLog(id=uuid.uuid4().hex, action_type="followup", ref_key=ref_key))
    db_session.commit()

    candidates = build_candidates(db_session, client.manager_id)
    assert not any(c.kind == "followup" and c.data["task_id"] == task_id for c in candidates)


def test_conflict_contact_then_escalate(db_session, client, team_project):
    event = Event(
        id=uuid.uuid4().hex,
        type="conflict",
        severity=2,
        title="Ship date disagreement",
        body="Alice says done, Bob says not done",
        project_ids=json.dumps([team_project]),
    )
    db_session.add(event)
    db_session.commit()

    candidates = build_candidates(db_session, client.manager_id)
    contact = [c for c in candidates if c.kind == "conflict_contact"]
    assert len(contact) == 1
    assert contact[0].ref_key == f"event:{event.id}"

    # No escalation yet -- only just contacted.
    db_session.add(AgentActionLog(id=uuid.uuid4().hex, action_type="conflict_contact", ref_key=f"event:{event.id}"))
    db_session.commit()
    candidates2 = build_candidates(db_session, client.manager_id)
    assert not any(c.kind == "conflict_contact" for c in candidates2)
    assert not any(c.kind == "conflict_escalate" for c in candidates2)

    # Backdate the contact log past the escalation threshold.
    contact_row = db_session.query(AgentActionLog).filter(AgentActionLog.action_type == "conflict_contact").first()
    contact_row.created_at = timeservice.now_ist() - timedelta(hours=48)
    db_session.commit()

    candidates3 = build_candidates(db_session, client.manager_id)
    escalate = [c for c in candidates3 if c.kind == "conflict_escalate"]
    assert len(escalate) == 1
