"""Dream job: per-user md synthesis (memory.md/events.md) + per-owned-project
summary regeneration, suggestions/concerns, and the health rubric. Uses real,
scaffolded projects/<id>/ dirs (same pattern as
tests/test_heartbeat_project_fanout.py), cleaned up via rmtree.

Step 36 rewrite: both phases are now agentic (app.agent.runner.run_spec)
instead of one blind structured-output JSON-blob call, mirroring heartbeat's
step-35 rewrite -- every test here scripts a FakeTransport turn sequence
(write_memory/append_manager_events/write_project_summary/
append_project_events/add_suggestions/add_concerns/set_health_adjustment
tool calls, then a plain-text turn) instead of one JSON blob. dump.md is
gone (step 36 Part 3) -- it had exactly one writer (this job) and nothing
ever read it, so there is no dump.md coverage here."""
import json
import shutil
import uuid
from datetime import timedelta

import pytest

from app import timeservice
from app.agent.gemini_client import GeminiClient
from app.database import Event
from app.projectkb import health_rubric
from app.projectkb.jobs import dream
from app.projects.db import get_project_session
from app.projects.models import Concern, Conflict, HealthLog, Suggestion, Task
from app.projects.paths import events_md_path, project_dir, summary_md_path
from app.tenancy.paths import manager_events_md_path, manager_memory_md_path
from tests.conftest import FakeTransport, _gemini_response_text, _gemini_tool_call_response


@pytest.fixture()
def project(client):
    r = client.post("/api/projects", json={"name": "Dream Project", "kind": "team", "description": "Test project description for automated tests."})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    yield pid
    shutil.rmtree(project_dir(pid), ignore_errors=True)


def _add_event(db_session, **kw) -> Event:
    defaults = dict(id=uuid.uuid4().hex, type="status_update", severity=1, title="Some event", general=True, dreamed=False)
    defaults.update(kw)
    event = Event(**defaults)
    db_session.add(event)
    db_session.commit()
    db_session.refresh(event)
    return event


def _add_task(project_id: str, **kw) -> str:
    defaults = dict(id=uuid.uuid4().hex, title="A task", status="todo", priority="medium", created_by="manager")
    defaults.update(kw)
    session = get_project_session(project_id)
    try:
        task = Task(**defaults)
        session.add(task)
        session.commit()
        return task.id
    finally:
        session.close()


def _user_run(memory_content="Merged memory", events_lines=None):
    """write_memory (+ optional append_manager_events) then a plain-text
    turn that ends the run with stop_reason="final" -- the standard shape
    for a successful user-level dream pass."""
    calls = [_gemini_tool_call_response("write_memory", {"content": memory_content})]
    if events_lines is not None:
        calls.append(_gemini_tool_call_response("append_manager_events", {"lines": events_lines}))
    calls.append(_gemini_response_text("done"))
    return calls


def _no_write_user_run():
    """A user-level run that ends immediately without ever calling
    write_memory -- the agent looked and decided there was nothing to
    write (or a malformed/empty model turn), the analog of the old
    "malformed response" case."""
    return [_gemini_response_text("done")]


def _project_run(
    project_id,
    summary_content="# Summary\n\nAll good.",
    events_lines=None,
    suggestions=None,
    concerns=None,
    health_adjustment=0,
    health_reason=None,
):
    calls = [_gemini_tool_call_response("write_project_summary", {"project_id": project_id, "content": summary_content})]
    if events_lines is not None:
        calls.append(_gemini_tool_call_response("append_project_events", {"project_id": project_id, "lines": events_lines}))
    if suggestions:
        calls.append(_gemini_tool_call_response("add_suggestions", {"project_id": project_id, "texts": suggestions}))
    if concerns:
        calls.append(_gemini_tool_call_response("add_concerns", {"project_id": project_id, "texts": concerns}))
    calls.append(
        _gemini_tool_call_response(
            "set_health_adjustment",
            {"project_id": project_id, "adjustment": health_adjustment, "reason": health_reason},
        )
    )
    calls.append(_gemini_response_text("done"))
    return calls


# ── zero pending / basic flow ───────────────────────────────────────────


def test_01_zero_pending_events_short_circuits_without_llm_call(client, db_session):
    transport = FakeTransport([])
    stats = dream.run(db_session, client.manager_id, client=GeminiClient(api_key="fake", transport=transport))
    assert stats["events_dreamed"] == 0
    assert stats["projects_synthesized"] == 0
    assert stats["stop_reason"] == "no_pending_events"
    assert transport.call_count == 0


def test_02_general_event_only_runs_user_synthesis_no_project_call(client, db_session):
    _add_event(db_session, general=True)
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport(_user_run()))

    stats = dream.run(db_session, client.manager_id, client=fake_client)

    assert stats["events_dreamed"] == 1
    assert stats["projects_synthesized"] == 0


def test_03_events_marked_dreamed_after_run(client, db_session):
    ev = _add_event(db_session)
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport(_user_run()))

    dream.run(db_session, client.manager_id, client=fake_client)

    db_session.refresh(ev)
    assert ev.dreamed is True


def test_04_user_run_that_never_writes_memory_leaves_events_undreamed(client, db_session):
    # The agent runs to completion (stop_reason="final") but never calls
    # write_memory -- must not be treated as success, or a whole batch's
    # memory synthesis silently vanishes with no retry.
    ev = _add_event(db_session)
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport(_no_write_user_run()))

    stats = dream.run(db_session, client.manager_id, client=fake_client)

    assert stats["events_dreamed"] == 0
    assert stats["projects_synthesized"] == 0
    db_session.refresh(ev)
    assert ev.dreamed is False
    assert manager_memory_md_path(client.manager_id).exists() is False


def test_04b_blank_write_memory_content_leaves_events_undreamed(client, db_session):
    # write_memory_handler itself rejects blank/whitespace-only content --
    # the tool call happens but the handler returns {"error": ...} rather
    # than {"success": True}, so _wrote_memory must still read this as "no
    # real write happened."
    ev = _add_event(db_session)
    responses = [_gemini_tool_call_response("write_memory", {"content": "   "}), _gemini_response_text("done")]
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport(responses))

    stats = dream.run(db_session, client.manager_id, client=fake_client)

    assert stats["events_dreamed"] == 0
    db_session.refresh(ev)
    assert ev.dreamed is False
    assert manager_memory_md_path(client.manager_id).exists() is False


def test_04c_user_agent_run_failing_to_start_leaves_events_undreamed(client, db_session):
    ev = _add_event(db_session)
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport([RuntimeError("boom")]))

    stats = dream.run(db_session, client.manager_id, client=fake_client)

    assert stats["events_dreamed"] == 0
    assert stats["stop_reason"] == "error"
    db_session.refresh(ev)
    assert ev.dreamed is False


# ── per-user md files ────────────────────────────────────────────────────


def test_05_memory_md_written_and_events_md_appended(client, db_session):
    _add_event(db_session, title="Did a thing")
    fake_client = GeminiClient(
        api_key="fake-key",
        transport=FakeTransport(_user_run(memory_content="User likes async updates.", events_lines=["Did a thing"])),
    )

    dream.run(db_session, client.manager_id, client=fake_client)

    assert manager_memory_md_path(client.manager_id).read_text() == "User likes async updates."
    assert "Did a thing" in manager_events_md_path(client.manager_id).read_text()


def test_06_events_md_appends_across_two_runs_without_clobbering(client, db_session):
    _add_event(db_session, title="Day 1 event")
    fake_client_1 = GeminiClient(api_key="fake-key", transport=FakeTransport(_user_run(events_lines=["Day 1 summary"])))
    dream.run(db_session, client.manager_id, client=fake_client_1)

    _add_event(db_session, title="Day 2 event")
    fake_client_2 = GeminiClient(api_key="fake-key", transport=FakeTransport(_user_run(events_lines=["Day 2 summary"])))
    dream.run(db_session, client.manager_id, client=fake_client_2)

    content = manager_events_md_path(client.manager_id).read_text()
    assert "Day 1 summary" in content
    assert "Day 2 summary" in content


def test_07_memory_md_prior_content_is_available_in_the_prompt(client, db_session):
    manager_memory_md_path(client.manager_id).write_text("Existing fact: prefers Slack over email.", encoding="utf-8")
    _add_event(db_session)
    transport = FakeTransport(_user_run(memory_content="Existing fact: prefers Slack over email.\nNew fact added."))
    fake_client = GeminiClient(api_key="fake-key", transport=transport)

    dream.run(db_session, client.manager_id, client=fake_client)

    sent_prompt = json.dumps(transport.calls[0][1])
    assert "prefers Slack over email" in sent_prompt
    assert (
        manager_memory_md_path(client.manager_id).read_text()
        == "Existing fact: prefers Slack over email.\nNew fact added."
    )


# ── per-project synthesis ───────────────────────────────────────────────


def test_08_project_synthesis_only_for_manager_owned_and_touched_projects(client, db_session, project):
    _add_event(db_session, project_ids=json.dumps([project]), general=False)
    responses = _user_run() + _project_run(project)
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport(responses))

    stats = dream.run(db_session, client.manager_id, client=fake_client)

    assert stats["projects_synthesized"] == 1


def test_09_summary_md_full_overwrite(client, db_session, project):
    _add_event(db_session, project_ids=json.dumps([project]), general=False)
    responses = _user_run() + _project_run(project, summary_content="# Summary\n\nRollout on track.")
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport(responses))

    dream.run(db_session, client.manager_id, client=fake_client)

    assert summary_md_path(project).read_text() == "# Summary\n\nRollout on track."


def test_10_project_events_md_appended(client, db_session, project):
    _add_event(db_session, project_ids=json.dumps([project]), general=False, title="Phoenix shipped")
    responses = _user_run() + _project_run(project, events_lines=["Phoenix shipped"])
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport(responses))

    dream.run(db_session, client.manager_id, client=fake_client)

    assert "Phoenix shipped" in events_md_path(project).read_text()


def test_11_suggestions_and_concerns_created_with_status_open(client, db_session, project):
    _add_event(db_session, project_ids=json.dumps([project]), general=False)
    responses = _user_run() + _project_run(
        project, suggestions=["Add more QA capacity"], concerns=["Timeline slipping"]
    )
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport(responses))

    dream.run(db_session, client.manager_id, client=fake_client)

    session = get_project_session(project)
    try:
        suggestion = session.query(Suggestion).one()
        assert suggestion.text == "Add more QA capacity"
        assert suggestion.status == "open"
        concern = session.query(Concern).one()
        assert concern.text == "Timeline slipping"
        assert concern.status == "open"
    finally:
        session.close()


def test_11b_repeated_dream_ticks_accumulate_suggestions_by_design(client, db_session, project):
    # No dedup against existing open Suggestion rows -- matches the
    # codebase's general "accumulate, don't dedupe" convention for these
    # append-style tables. Documented here so this isn't mistaken for a bug.
    _add_event(db_session, project_ids=json.dumps([project]), general=False, title="Day 1")
    responses_1 = _user_run() + _project_run(project, suggestions=["Add more QA capacity"])
    dream.run(db_session, client.manager_id, client=GeminiClient(api_key="fake-key", transport=FakeTransport(responses_1)))

    _add_event(db_session, project_ids=json.dumps([project]), general=False, title="Day 2")
    responses_2 = _user_run() + _project_run(project, suggestions=["Add more QA capacity"])
    dream.run(db_session, client.manager_id, client=GeminiClient(api_key="fake-key", transport=FakeTransport(responses_2)))

    session = get_project_session(project)
    try:
        assert session.query(Suggestion).count() == 2
    finally:
        session.close()


def test_12_dream_never_fires_for_a_project_the_manager_does_not_manage(client, db_session):
    other_project_id = uuid.uuid4().hex  # never registered as owned by this manager
    _add_event(db_session, project_ids=json.dumps([other_project_id]), general=False)
    transport = FakeTransport(_user_run())
    fake_client = GeminiClient(api_key="fake-key", transport=transport)

    stats = dream.run(db_session, client.manager_id, client=fake_client)

    assert stats["projects_synthesized"] == 0
    assert transport.call_count == len(_user_run())  # only the user-level run happened


# ── health rubric ───────────────────────────────────────────────────────


def test_13_base_health_score_pure_function():
    # Clean project: no blockers/overdue/conflicts, recent progress.
    assert health_rubric.compute_base_health_score(0, 0, 0, 0) == 100
    # Capped contributions: well past every cap.
    assert health_rubric.compute_base_health_score(10, 10, 10, 100) == 0
    # A hand-constructed mid case: 2 blockers, 1 overdue, 0 conflicts, 5 days since progress.
    # 100 - 15*2 - 10*1 - 20*0 - 5 = 100 - 30 - 10 - 0 - 5 = 55
    assert health_rubric.compute_base_health_score(2, 1, 0, 5) == 55


def test_14_llm_health_adjustment_clamped_to_one_band(client, db_session, project):
    # A blocker event knocks the base score below 100 so the +1-band nudge
    # (clamped from a hallucinated +3) has visible headroom to apply.
    _add_event(db_session, project_ids=json.dumps([project]), general=False, type="blocker", ui_state="shown")
    responses = _user_run() + _project_run(project, health_adjustment=3, health_reason="way off")
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport(responses))

    dream.run(db_session, client.manager_id, client=fake_client)

    session = get_project_session(project)
    try:
        log = session.query(HealthLog).one()
        assert log.llm_adjustment == 1  # clamped from 3
        assert log.base_score < 100
        assert log.final_score == log.base_score + health_rubric.HEALTH_BAND_POINTS
    finally:
        session.close()


def test_15_health_log_row_inserted_even_with_zero_adjustment(client, db_session, project):
    _add_event(db_session, project_ids=json.dumps([project]), general=False)
    responses = _user_run() + _project_run(project, health_adjustment=0)
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport(responses))

    dream.run(db_session, client.manager_id, client=fake_client)

    session = get_project_session(project)
    try:
        log = session.query(HealthLog).one()
        assert log.llm_adjustment == 0
        assert log.reason is None
        assert log.final_score == log.base_score
    finally:
        session.close()


def test_16_health_rubric_reflects_current_open_blockers_overdue_conflicts(client, db_session, project):
    _add_task(project, status="todo", due=timeservice.now_ist() - timedelta(days=3))
    session = get_project_session(project)
    try:
        session.add(Conflict(claim_a_ref="c1", claim_b_ref="c2", severity="medium", status="open"))
        session.commit()
    finally:
        session.close()

    _add_event(db_session, project_ids=json.dumps([project]), general=False, type="blocker", ui_state="shown")
    _add_event(db_session, project_ids=json.dumps([project]), general=False, title="trigger")
    responses = _user_run() + _project_run(project)
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport(responses))

    dream.run(db_session, client.manager_id, client=fake_client)

    session = get_project_session(project)
    try:
        log = session.query(HealthLog).one()
        inputs = json.loads(log.rubric_inputs)
        assert inputs["open_blockers"] == 1
        assert inputs["overdue_tasks"] == 1
        assert inputs["open_conflicts"] == 1
    finally:
        session.close()


def test_17_nonzero_adjustment_without_reason_is_floored_to_zero(client, db_session, project):
    _add_event(db_session, project_ids=json.dumps([project]), general=False, type="blocker", ui_state="shown")
    responses = _user_run() + _project_run(project, health_adjustment=1, health_reason=None)
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport(responses))

    dream.run(db_session, client.manager_id, client=fake_client)

    session = get_project_session(project)
    try:
        log = session.query(HealthLog).one()
        assert log.llm_adjustment == 0
        assert log.reason is None
        assert log.final_score == log.base_score
    finally:
        session.close()


def test_18_project_synthesis_failure_is_isolated_and_leaves_summary_untouched(client, db_session, project, monkeypatch):
    """If the project-phase agent run itself blows up (e.g. a tool call
    raises), the function must not have already overwritten summary.md and
    the overall dream() run must be unaffected: user-level synthesis
    already succeeded, so events still get marked dreamed (per-project
    failures are isolated, same as heartbeat's fan-out)."""
    original_summary = summary_md_path(project).read_text() if summary_md_path(project).exists() else ""

    def _boom_run_project_synthesis(*args, **kwargs):
        raise RuntimeError("simulated project synthesis failure")

    monkeypatch.setattr(dream, "_run_project_synthesis", _boom_run_project_synthesis)

    ev = _add_event(db_session, project_ids=json.dumps([project]), general=False)
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport(_user_run()))

    stats = dream.run(db_session, client.manager_id, client=fake_client)

    assert stats["events_dreamed"] == 1  # user-level phase succeeded, so the batch is still dreamed
    assert stats["projects_synthesized"] == 0  # this project's synthesis was skipped
    db_session.refresh(ev)
    assert ev.dreamed is True
    current_summary = summary_md_path(project).read_text() if summary_md_path(project).exists() else ""
    assert current_summary == original_summary  # never overwritten -- the run never got there
