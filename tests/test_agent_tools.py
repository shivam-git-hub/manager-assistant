"""Step 28 §5 -- send_message, dashboard_action, and the AgentActionLog
dedup they write."""
import json
import uuid
from datetime import datetime

import pytest

from app import timeservice
from app.database import AgentActionLog, ChatMessage, Meeting, Todo
from app.agent import tools as agent_tools


@pytest.fixture
def team_project(client, cleanup_projects):
    r = client.post("/api/projects", json={"name": "Phoenix", "kind": "team"})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    cleanup_projects.append(pid)
    return pid


def _make_employee(email, name, slack_id=None):
    from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Employee

    db = ControlPlaneSessionLocal()
    try:
        emp = Employee(id=uuid.uuid4().hex, email=email.lower(), name=name, slack_id=slack_id)
        db.add(emp)
        db.commit()
        return emp.id
    finally:
        db.close()


def test_send_message_portal_writes_chat_message(db_session, client, manager_employee_id):
    res = agent_tools.send_message_handler(
        db=db_session, manager_id=client.manager_id, run_context={}, channel="portal", target="manager", text="Hi there"
    )
    assert res["success"] is True
    assert res["status"] == "sent"
    msg = db_session.query(ChatMessage).filter(ChatMessage.role == "assistant").first()
    assert msg is not None
    assert msg.content == "Hi there"


def test_send_message_slack_no_slack_id_errors(db_session, client):
    emp_id = _make_employee("bob@example.com", "Bob")  # no slack_id
    res = agent_tools.send_message_handler(
        db=db_session, manager_id=client.manager_id, run_context={}, channel="slack", target=emp_id, text="Ping"
    )
    assert "error" in res


def test_send_message_slack_quiet_hours_holds_and_logs_candidate(db_session, client, set_sim_time):
    emp_id = _make_employee("bob@example.com", "Bob", slack_id="U_BOB_SLACK")
    set_sim_time(datetime(2026, 7, 12, 23, 0, 0))

    from app.agent.select import Candidate

    cand = Candidate(kind="followup", ref_key="task:p1:t1:2026-07-12", summary="overdue")
    run_context = {"candidates": {("followup", "task:p1:t1:2026-07-12"): cand}}

    res = agent_tools.send_message_handler(
        db=db_session,
        manager_id=client.manager_id,
        run_context=run_context,
        channel="slack",
        target=emp_id,
        text="Status update please?",
        candidate_kind="followup",
        candidate_ref_key="task:p1:t1:2026-07-12",
    )
    assert res["status"] == "held"
    assert res["log"] == "logged"

    log_row = db_session.query(AgentActionLog).filter(AgentActionLog.action_type == "followup").first()
    assert log_row is not None
    assert log_row.ref_key == "task:p1:t1:2026-07-12"


def test_send_message_unlisted_candidate_ref_is_ignored(db_session, client):
    emp_id = _make_employee("bob@example.com", "Bob", slack_id="U_BOB_SLACK")
    res = agent_tools.send_message_handler(
        db=db_session,
        manager_id=client.manager_id,
        run_context={"candidates": {}},
        channel="slack",
        target=emp_id,
        text="hi",
        candidate_kind="followup",
        candidate_ref_key="task:not:real:2026-07-12",
    )
    assert "WARNING" in res["log"]
    assert db_session.query(AgentActionLog).count() == 0


def test_dashboard_action_create_and_update_task(db_session, client, team_project):
    res = agent_tools.dashboard_action_handler(
        db=db_session, manager_id=client.manager_id, run_context={}, action="create_task",
        project_id=team_project, title="Write tests",
    )
    assert res["success"] is True
    task_id = res["task_id"]

    res2 = agent_tools.dashboard_action_handler(
        db=db_session, manager_id=client.manager_id, run_context={}, action="update_task_status",
        project_id=team_project, task_id=task_id, status="in_progress",
    )
    assert res2["success"] is True
    assert res2["status"] == "in_progress"


def test_dashboard_action_create_todo(db_session, client):
    res = agent_tools.dashboard_action_handler(
        db=db_session, manager_id=client.manager_id, run_context={}, action="create_todo", text="Follow up with Bob",
    )
    assert res["success"] is True
    assert db_session.query(Todo).filter(Todo.text == "Follow up with Bob").first() is not None


def test_dashboard_action_create_meeting_dedups(db_session, client):
    res1 = agent_tools.dashboard_action_handler(
        db=db_session, manager_id=client.manager_id, run_context={}, action="create_meeting",
        title="Sync", starts_at="2026-07-20 17:00:00",
    )
    assert res1["success"] is True
    meeting_id = res1["meeting_id"]

    res2 = agent_tools.dashboard_action_handler(
        db=db_session, manager_id=client.manager_id, run_context={}, action="create_meeting",
        title="Sync", starts_at="2026-07-20 17:05:00",
    )
    assert res2["success"] is True
    assert res2["meeting_id"] == meeting_id
    assert db_session.query(Meeting).count() == 1


def test_list_tasks_excludes_done_and_filters_by_status(db_session, client, team_project):
    created = []
    for title, status in [("A", "todo"), ("B", "blocked"), ("C", "done")]:
        res = agent_tools.dashboard_action_handler(
            db=db_session, manager_id=client.manager_id, run_context={}, action="create_task",
            project_id=team_project, title=title,
        )
        created.append(res["task_id"])
        if status != "todo":
            agent_tools.dashboard_action_handler(
                db=db_session, manager_id=client.manager_id, run_context={}, action="update_task_status",
                project_id=team_project, task_id=res["task_id"], status=status,
            )

    all_open = agent_tools.list_tasks_handler(db=db_session, manager_id=client.manager_id, run_context={}, project_id=team_project)
    titles = {t["title"] for t in all_open}
    assert titles == {"A", "B"}

    blocked_only = agent_tools.list_tasks_handler(db=db_session, manager_id=client.manager_id, run_context={}, project_id=team_project, status="blocked")
    assert [t["title"] for t in blocked_only] == ["B"]


def test_todo_tool_scoped_to_run_context(db_session, client):
    run_context = {}
    res = agent_tools.todo_handler(db=db_session, manager_id=client.manager_id, run_context=run_context, todos=[
        {"id": "1", "content": "brief the manager", "status": "pending"}
    ])
    assert res["todos"][0]["content"] == "brief the manager"
    assert run_context["todos"] == res["todos"]

    read_back = agent_tools.todo_handler(db=db_session, manager_id=client.manager_id, run_context=run_context)
    assert read_back["todos"] == res["todos"]
