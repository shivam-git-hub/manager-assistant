"""Project-scoped fan-out over the events the user-level heartbeat half
creates -- task status transitions,
drafted subtasks, deterministic (no-LLM) archive writes, request->task
linkage. Uses real, scaffolded projects/<id>/ dirs (same pattern as
tests/test_project_detail.py's `project` fixture), cleaned up via rmtree.

Step 35 rewrite: phase 2 (_fanout_one_project / _run_project_fanout) is now
a tool-calling agent (app.agent.runner.run_spec), just like phase 1 in
tests/test_heartbeat_user.py -- every test here scripts a FakeTransport turn
sequence (apply_task_transitions/draft_tasks/link_request_to_task tool
calls, then a plain-text turn) instead of one JSON blob, and every direct
call to _fanout_one_project/_run_project_fanout now also passes
manager_id and context_text per the real signatures."""
import json
import shutil
import uuid

import pytest

from app.agent.gemini_client import GeminiClient
from app.database import Event
from app.projectkb.jobs import heartbeat
from app.projects.db import get_project_session
from app.projects.models import ArchiveEntry, Task
from app.projects.paths import project_dir
from tests.conftest import FakeTransport, _gemini_response_text, _gemini_tool_call_response

_CONTEXT_TEXT = "test context"


@pytest.fixture()
def project(client):
    r = client.post("/api/projects", json={"name": "Fanout Project", "kind": "team", "description": "Test project description for automated tests."})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    yield pid
    shutil.rmtree(project_dir(pid), ignore_errors=True)


def _add_task(project_id: str, **kw) -> str:
    defaults = dict(
        id=uuid.uuid4().hex,
        title="Existing task",
        status="in_progress",
        priority="medium",
        created_by="manager",
    )
    defaults.update(kw)
    session = get_project_session(project_id)
    try:
        task = Task(**defaults)
        session.add(task)
        session.commit()
        return task.id
    finally:
        session.close()


def _add_event(db_session, **kw) -> Event:
    defaults = dict(
        id=uuid.uuid4().hex,
        type="status_update",
        severity=1,
        title="Some event",
        general=False,
    )
    defaults.update(kw)
    event = Event(**defaults)
    db_session.add(event)
    db_session.commit()
    db_session.refresh(event)
    return event


def _no_writes_run():
    """A phase-2 run that ends immediately with no tool calls at all --
    zero LLM calls beyond the one plain-text turn, stop_reason="final"."""
    return [_gemini_response_text("done")]


def _fanout_run(*tool_calls):
    """One or more scripted tool calls followed by a plain-text turn that
    ends the run with stop_reason="final"."""
    return [*tool_calls, _gemini_response_text("done")]


# ── _run_project_fanout: ownership gating ──────────────────────────────

def test_01_fanout_skips_projects_the_manager_does_not_own(client, db_session):
    other_project_id = uuid.uuid4().hex  # never registered as owned by this manager
    event = _add_event(db_session, project_ids=json.dumps([other_project_id]))
    transport = FakeTransport([])

    stats = heartbeat._run_project_fanout(
        db_session, client.manager_id, [event], GeminiClient(api_key="fake", transport=transport), _CONTEXT_TEXT
    )

    assert stats == {
        "projects_touched": 0,
        "tasks_updated": 0,
        "tasks_drafted": 0,
        "archive_entries": 0,
        "llm_calls": 0,
        "tokens_in": 0,
        "tokens_out": 0,
    }
    assert transport.call_count == 0


def test_02_fanout_runs_for_manager_owned_project(client, db_session, project):
    event = _add_event(db_session, project_ids=json.dumps([project]))
    transport = FakeTransport(_no_writes_run())

    stats = heartbeat._run_project_fanout(
        db_session, client.manager_id, [event], GeminiClient(api_key="fake", transport=transport), _CONTEXT_TEXT
    )

    assert stats["projects_touched"] == 1
    assert transport.call_count == 1


def test_03_empty_tagged_events_short_circuits_without_llm_call(client, db_session):
    transport = FakeTransport([])
    stats = heartbeat._run_project_fanout(
        db_session, client.manager_id, [], GeminiClient(api_key="fake", transport=transport), _CONTEXT_TEXT
    )
    assert stats == {
        "projects_touched": 0,
        "tasks_updated": 0,
        "tasks_drafted": 0,
        "archive_entries": 0,
        "llm_calls": 0,
        "tokens_in": 0,
        "tokens_out": 0,
    }
    assert transport.call_count == 0


# ── _fanout_one_project: task transitions, drafts, archive, linkage ────

def test_04_task_transition_without_evidence_event_is_rejected(client, db_session, project):
    task_id = _add_task(project)
    event = _add_event(db_session, project_ids=json.dumps([project]))
    tool_call = _gemini_tool_call_response(
        "apply_task_transitions",
        {
            "project_id": project,
            "transitions": [{"task_id": task_id, "new_status": "done", "event_id": "not-a-real-event-id"}],
        },
    )
    fake_client = GeminiClient(api_key="fake", transport=FakeTransport(_fanout_run(tool_call)))

    stats = heartbeat._fanout_one_project(db_session, client.manager_id, project, [event], fake_client, _CONTEXT_TEXT)

    assert stats["tasks_updated"] == 0
    session = get_project_session(project)
    try:
        task = session.get(Task, task_id)
        assert task.status == "in_progress"
    finally:
        session.close()


def test_05_task_transition_with_valid_evidence_is_applied(client, db_session, project):
    task_id = _add_task(project)
    event = _add_event(db_session, project_ids=json.dumps([project]), type="status_update", title="Task finished")
    tool_call = _gemini_tool_call_response(
        "apply_task_transitions",
        {
            "project_id": project,
            "transitions": [{"task_id": task_id, "new_status": "done", "event_id": event.id}],
        },
    )
    fake_client = GeminiClient(api_key="fake", transport=FakeTransport(_fanout_run(tool_call)))

    stats = heartbeat._fanout_one_project(db_session, client.manager_id, project, [event], fake_client, _CONTEXT_TEXT)

    assert stats["tasks_updated"] == 1
    session = get_project_session(project)
    try:
        task = session.get(Task, task_id)
        assert task.status == "done"
    finally:
        session.close()


def test_06_drafted_tasks_always_pending_approval_and_agent_created(client, db_session, project):
    event = _add_event(db_session, project_ids=json.dumps([project]))
    tool_call = _gemini_tool_call_response(
        "draft_tasks",
        {
            "project_id": project,
            "tasks": [{"title": "New subtask", "description": "desc", "priority": "high"}],
        },
    )
    fake_client = GeminiClient(api_key="fake", transport=FakeTransport(_fanout_run(tool_call)))

    stats = heartbeat._fanout_one_project(db_session, client.manager_id, project, [event], fake_client, _CONTEXT_TEXT)

    assert stats["tasks_drafted"] == 1
    session = get_project_session(project)
    try:
        drafted = session.query(Task).filter(Task.title == "New subtask").one()
        assert drafted.status == "pending_approval"
        assert drafted.created_by == "agent"
        assert drafted.priority == "high"
    finally:
        session.close()


def test_07_archive_entries_written_deterministically_no_extra_llm_call(client, db_session, project):
    e1 = _add_event(db_session, project_ids=json.dumps([project]), title="First event")
    e2 = _add_event(db_session, project_ids=json.dumps([project]), title="Second event")
    transport = FakeTransport(_no_writes_run())
    fake_client = GeminiClient(api_key="fake", transport=transport)

    stats = heartbeat._fanout_one_project(db_session, client.manager_id, project, [e1, e2], fake_client, _CONTEXT_TEXT)

    assert stats["archive_entries"] == 2
    assert transport.call_count == 1  # one judgment call, no separate archive-synthesis call
    session = get_project_session(project)
    try:
        entries = session.query(ArchiveEntry).all()
        assert len(entries) == 2
        assert {e.source_ref for e in entries} == {e1.id, e2.id}
        assert all(e.kind == "event" for e in entries)
    finally:
        session.close()


def test_08_request_task_link_only_applies_to_known_task_and_request_type(client, db_session, project):
    task_id = _add_task(project)
    request_event = _add_event(db_session, project_ids=json.dumps([project]), type="request", title="Mark subtask done")
    non_request_event = _add_event(db_session, project_ids=json.dumps([project]), type="status_update", title="FYI")
    # Only the valid link (request_event -> task_id) is scripted -- the
    # other two cases (wrong event type, unknown task) are enforced by the
    # tool handler itself and are exercised as "the agent never calls the
    # tool for them" here (mirrors the old test's intent: only the
    # unambiguous match survives).
    tool_call = _gemini_tool_call_response(
        "link_request_to_task",
        {"event_id": request_event.id, "project_id": project, "task_id": task_id},
    )
    fake_client = GeminiClient(api_key="fake", transport=FakeTransport(_fanout_run(tool_call)))

    heartbeat._fanout_one_project(
        db_session, client.manager_id, project, [request_event, non_request_event], fake_client, _CONTEXT_TEXT
    )

    db_session.refresh(request_event)
    db_session.refresh(non_request_event)
    assert json.loads(request_event.task_ids) == [task_id]
    assert non_request_event.task_ids is None


def test_08b_request_tagged_to_two_owned_projects_accumulates_task_ids(client, db_session, project):
    r2 = client.post("/api/projects", json={"name": "Second Fanout Project", "kind": "team", "description": "Test project description for automated tests."})
    assert r2.status_code == 201, r2.text
    project_2 = r2.json()["id"]
    try:
        task_a = _add_task(project, title="Task in project A")
        task_b = _add_task(project_2, title="Task in project B")
        # One event tagged to BOTH manager-owned projects -- each project's
        # own fan-out pass links a different task to the same event; the
        # second pass must not clobber the first's task_ids entry.
        request_event = _add_event(
            db_session, project_ids=json.dumps([project, project_2]), type="request", title="Mark both done"
        )

        tool_call_a = _gemini_tool_call_response(
            "link_request_to_task", {"event_id": request_event.id, "project_id": project, "task_id": task_a}
        )
        heartbeat._fanout_one_project(
            db_session,
            client.manager_id,
            project,
            [request_event],
            GeminiClient(api_key="fake", transport=FakeTransport(_fanout_run(tool_call_a))),
            _CONTEXT_TEXT,
        )
        db_session.refresh(request_event)
        assert json.loads(request_event.task_ids) == [task_a]

        tool_call_b = _gemini_tool_call_response(
            "link_request_to_task", {"event_id": request_event.id, "project_id": project_2, "task_id": task_b}
        )
        heartbeat._fanout_one_project(
            db_session,
            client.manager_id,
            project_2,
            [request_event],
            GeminiClient(api_key="fake", transport=FakeTransport(_fanout_run(tool_call_b))),
            _CONTEXT_TEXT,
        )
        db_session.refresh(request_event)
        assert set(json.loads(request_event.task_ids)) == {task_a, task_b}
    finally:
        shutil.rmtree(project_dir(project_2), ignore_errors=True)


# ── end-to-end through run() ────────────────────────────────────────────

def test_09_end_to_end_run_creates_event_drafts_task_and_archive(client, db_session, project):
    from app.database import Claim

    task_id = _add_task(project, title="Migration subtask")
    claim = Claim(id=uuid.uuid4().hex, text="Migration subtask is done", content_hash=uuid.uuid4().hex, processed=False)
    db_session.add(claim)
    db_session.commit()

    phase1_tool_call = _gemini_tool_call_response(
        "emit_events",
        {
            "events": [
                {
                    "type": "status_update",
                    "severity": 1,
                    "title": "Migration subtask done",
                    "general": False,
                    "project_ids": [project],
                    "claim_ids": [claim.id],
                }
            ]
        },
    )
    phase2_tool_call = _gemini_tool_call_response(
        "draft_tasks",
        {"project_id": project, "tasks": [{"title": "Follow-up cleanup", "priority": "low"}]},
    )
    responses = [
        phase1_tool_call,
        _gemini_response_text("done"),
        phase2_tool_call,
        _gemini_response_text("done"),
    ]
    fake_client = GeminiClient(api_key="fake", transport=FakeTransport(responses))

    stats = heartbeat.run(db_session, client.manager_id, client=fake_client)

    assert stats["events_created"] == 1
    assert stats["projects_touched"] == 1
    assert stats["tasks_drafted"] == 1
    assert stats["archive_entries"] == 1

    session = get_project_session(project)
    try:
        assert session.query(Task).filter(Task.title == "Follow-up cleanup", Task.status == "pending_approval").count() == 1
        assert session.query(ArchiveEntry).count() == 1
    finally:
        session.close()
