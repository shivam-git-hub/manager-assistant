"""Step 25 (prompts/step_25_dream_job.md): per-user md synthesis
(memory.md/events.md/dump.md) + per-managed-project summary regeneration,
suggestions/concerns, and the health rubric. Uses real, scaffolded
projects/<id>/ dirs (same pattern as tests/test_heartbeat_project_fanout.py),
cleaned up via rmtree."""
import json
import shutil
import uuid
from datetime import timedelta

import pytest

from app import timeservice
from app.agent.gemini_client import GeminiClient
from app.database import Event
from app.projectkb.jobs import dream
from app.projects.db import get_project_session
from app.projects.models import Concern, Conflict, HealthLog, Suggestion, Task
from app.projects.paths import events_md_path, project_dir, summary_md_path
from app.tenancy.paths import manager_dump_md_path, manager_events_md_path, manager_memory_md_path


class FakeTransport:
    def __init__(self, response_dicts):
        self.response_dicts = response_dicts
        self.calls = []
        self.call_count = 0

    def __call__(self, url, json_payload):
        self.calls.append((url, json_payload))
        res = self.response_dicts[self.call_count]
        self.call_count += 1
        if isinstance(res, Exception):
            raise res
        return res


def _gemini_response(obj):
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(obj)}]}, "finishReason": "STOP"}]}


def _user_response(memory_md="Merged memory", events_lines=None, dump_lines=None):
    return _gemini_response(
        {"memory_md": memory_md, "events_lines": events_lines or [], "dump_lines": dump_lines or []}
    )


def _project_response(**overrides):
    base = {
        "summary_md": "# Summary\n\nAll good.",
        "events_lines": [],
        "suggestions": [],
        "concerns": [],
        "health_adjustment": 0,
        "health_reason": None,
    }
    base.update(overrides)
    return _gemini_response(base)


@pytest.fixture()
def project(client):
    r = client.post("/api/projects", json={"name": "Dream Project", "kind": "team"})
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


# ── zero pending / basic flow ───────────────────────────────────────────

def test_01_zero_pending_events_short_circuits_without_llm_call(client, db_session):
    transport = FakeTransport([])
    stats = dream.run(db_session, client.manager_id, client=GeminiClient(api_key="fake", transport=transport))
    assert stats == {"events_dreamed": 0, "projects_synthesized": 0}
    assert transport.call_count == 0


def test_02_general_event_only_runs_user_synthesis_no_project_call(client, db_session):
    _add_event(db_session, general=True)
    fake_client = GeminiClient(api_key="fake", transport=FakeTransport([_user_response()]))

    stats = dream.run(db_session, client.manager_id, client=fake_client)

    assert stats["events_dreamed"] == 1
    assert stats["projects_synthesized"] == 0


def test_03_events_marked_dreamed_after_run(client, db_session):
    ev = _add_event(db_session)
    fake_client = GeminiClient(api_key="fake", transport=FakeTransport([_user_response()]))

    dream.run(db_session, client.manager_id, client=fake_client)

    db_session.refresh(ev)
    assert ev.dreamed is True


def test_04_user_llm_failure_leaves_events_undreamed(client, db_session):
    ev = _add_event(db_session)
    fake_client = GeminiClient(api_key="fake", transport=FakeTransport([RuntimeError("boom")]))

    stats = dream.run(db_session, client.manager_id, client=fake_client)

    assert stats == {"events_dreamed": 0, "projects_synthesized": 0}
    db_session.refresh(ev)
    assert ev.dreamed is False


def test_04b_malformed_user_response_leaves_events_undreamed_not_silently_consumed(client, db_session):
    # A non-JSON-object response doesn't raise inside parse_json_object
    # (it tolerantly returns {}) -- dream must still treat "nothing usable
    # came back" as a failure, not silently mark the batch dreamed with
    # zero synthesis (events are the terminal pipeline stage; there's no
    # later job that could ever recover them).
    ev = _add_event(db_session)
    garbage_response = {"candidates": [{"content": {"parts": [{"text": "not json at all"}]}, "finishReason": "STOP"}]}
    fake_client = GeminiClient(api_key="fake", transport=FakeTransport([garbage_response]))

    stats = dream.run(db_session, client.manager_id, client=fake_client)

    assert stats == {"events_dreamed": 0, "projects_synthesized": 0}
    db_session.refresh(ev)
    assert ev.dreamed is False
    assert manager_memory_md_path(client.manager_id).exists() is False


# ── per-user md files ────────────────────────────────────────────────────

def test_05_memory_md_written_and_events_dump_appended(client, db_session):
    _add_event(db_session, title="Did a thing")
    fake_client = GeminiClient(
        api_key="fake",
        transport=FakeTransport([_user_response(memory_md="User likes async updates.", events_lines=["Did a thing"], dump_lines=["contact: bob@co.com"])]),
    )

    dream.run(db_session, client.manager_id, client=fake_client)

    assert manager_memory_md_path(client.manager_id).read_text() == "User likes async updates."
    assert "Did a thing" in manager_events_md_path(client.manager_id).read_text()
    assert "bob@co.com" in manager_dump_md_path(client.manager_id).read_text()


def test_06_events_md_appends_across_two_runs_without_clobbering(client, db_session):
    _add_event(db_session, title="Day 1 event")
    fake_client_1 = GeminiClient(api_key="fake", transport=FakeTransport([_user_response(events_lines=["Day 1 summary"])]))
    dream.run(db_session, client.manager_id, client=fake_client_1)

    _add_event(db_session, title="Day 2 event")
    fake_client_2 = GeminiClient(api_key="fake", transport=FakeTransport([_user_response(events_lines=["Day 2 summary"])]))
    dream.run(db_session, client.manager_id, client=fake_client_2)

    content = manager_events_md_path(client.manager_id).read_text()
    assert "Day 1 summary" in content
    assert "Day 2 summary" in content


def test_07_memory_md_prompt_carries_prior_content_forward(client, db_session):
    manager_memory_md_path(client.manager_id).write_text("Existing fact: prefers Slack over email.", encoding="utf-8")
    _add_event(db_session)
    transport = FakeTransport([_user_response(memory_md="Existing fact: prefers Slack over email.\nNew fact added.")])
    fake_client = GeminiClient(api_key="fake", transport=transport)

    dream.run(db_session, client.manager_id, client=fake_client)

    sent_prompt = json.dumps(transport.calls[0][1])
    assert "prefers Slack over email" in sent_prompt
    assert "Existing fact: prefers Slack over email.\nNew fact added." in manager_memory_md_path(client.manager_id).read_text()


# ── per-project synthesis ───────────────────────────────────────────────

def test_08_project_synthesis_only_for_manager_owned_and_touched_projects(client, db_session, project):
    ev = _add_event(db_session, project_ids=json.dumps([project]), general=False)
    fake_client = GeminiClient(api_key="fake", transport=FakeTransport([_user_response(), _project_response()]))

    stats = dream.run(db_session, client.manager_id, client=fake_client)

    assert stats["projects_synthesized"] == 1


def test_09_summary_md_full_overwrite(client, db_session, project):
    _add_event(db_session, project_ids=json.dumps([project]), general=False)
    fake_client = GeminiClient(
        api_key="fake",
        transport=FakeTransport([_user_response(), _project_response(summary_md="# Summary\n\nRollout on track.")]),
    )

    dream.run(db_session, client.manager_id, client=fake_client)

    assert summary_md_path(project).read_text() == "# Summary\n\nRollout on track."


def test_10_project_events_md_appended(client, db_session, project):
    _add_event(db_session, project_ids=json.dumps([project]), general=False, title="Phoenix shipped")
    fake_client = GeminiClient(
        api_key="fake", transport=FakeTransport([_user_response(), _project_response(events_lines=["Phoenix shipped"])])
    )

    dream.run(db_session, client.manager_id, client=fake_client)

    assert "Phoenix shipped" in events_md_path(project).read_text()


def test_11_suggestions_and_concerns_created_with_status_open(client, db_session, project):
    _add_event(db_session, project_ids=json.dumps([project]), general=False)
    fake_client = GeminiClient(
        api_key="fake",
        transport=FakeTransport(
            [_user_response(), _project_response(suggestions=["Add more QA capacity"], concerns=["Timeline slipping"])]
        ),
    )

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
    # No dedup against existing open Suggestion/Concern rows -- matches the
    # codebase's general "accumulate, don't dedupe" convention for these
    # append-style tables. Documented here so this isn't mistaken for a bug.
    _add_event(db_session, project_ids=json.dumps([project]), general=False, title="Day 1")
    fake_client_1 = GeminiClient(
        api_key="fake", transport=FakeTransport([_user_response(), _project_response(suggestions=["Add more QA capacity"])])
    )
    dream.run(db_session, client.manager_id, client=fake_client_1)

    _add_event(db_session, project_ids=json.dumps([project]), general=False, title="Day 2")
    fake_client_2 = GeminiClient(
        api_key="fake", transport=FakeTransport([_user_response(), _project_response(suggestions=["Add more QA capacity"])])
    )
    dream.run(db_session, client.manager_id, client=fake_client_2)

    session = get_project_session(project)
    try:
        assert session.query(Suggestion).count() == 2
    finally:
        session.close()


def test_12_dream_never_fires_for_a_project_the_manager_does_not_manage(client, db_session):
    other_project_id = uuid.uuid4().hex  # never registered as owned by this manager
    _add_event(db_session, project_ids=json.dumps([other_project_id]), general=False)
    fake_client = GeminiClient(api_key="fake", transport=FakeTransport([_user_response()]))

    stats = dream.run(db_session, client.manager_id, client=fake_client)

    assert stats["projects_synthesized"] == 0
    assert fake_client._transport.call_count == 1  # only the user-level call


# ── health rubric ───────────────────────────────────────────────────────

def test_13_base_health_score_pure_function():
    # Clean project: no blockers/overdue/conflicts, recent progress.
    assert dream._compute_base_health_score(0, 0, 0, 0) == 100
    # Capped contributions: well past every cap.
    assert dream._compute_base_health_score(10, 10, 10, 100) == 0
    # A hand-constructed mid case: 2 blockers, 1 overdue, 0 conflicts, 5 days since progress.
    # 100 - 15*2 - 10*1 - 20*0 - 5 = 100 - 30 - 10 - 0 - 5 = 55
    assert dream._compute_base_health_score(2, 1, 0, 5) == 55


def test_14_llm_health_adjustment_clamped_to_one_band(client, db_session, project):
    # A blocker event knocks the base score below 100 so the +1-band nudge
    # (clamped from a hallucinated +3) has visible headroom to apply.
    _add_event(db_session, project_ids=json.dumps([project]), general=False, type="blocker", ui_state="shown")
    fake_client = GeminiClient(
        api_key="fake",
        transport=FakeTransport([_user_response(), _project_response(health_adjustment=3, health_reason="way off")]),
    )

    dream.run(db_session, client.manager_id, client=fake_client)

    session = get_project_session(project)
    try:
        log = session.query(HealthLog).one()
        assert log.llm_adjustment == 1  # clamped from 3
        assert log.base_score < 100
        assert log.final_score == log.base_score + dream.HEALTH_BAND_POINTS
    finally:
        session.close()


def test_15_health_log_row_inserted_even_with_zero_adjustment(client, db_session, project):
    _add_event(db_session, project_ids=json.dumps([project]), general=False)
    fake_client = GeminiClient(api_key="fake", transport=FakeTransport([_user_response(), _project_response(health_adjustment=0)]))

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
    task_id = _add_task(project, status="todo", due=timeservice.now_ist() - timedelta(days=3))
    session = get_project_session(project)
    try:
        session.add(Conflict(claim_a_ref="c1", claim_b_ref="c2", severity="medium", status="open"))
        session.commit()
    finally:
        session.close()

    _add_event(db_session, project_ids=json.dumps([project]), general=False, type="blocker", ui_state="shown")
    trigger_event = _add_event(db_session, project_ids=json.dumps([project]), general=False, title="trigger")
    fake_client = GeminiClient(api_key="fake", transport=FakeTransport([_user_response(), _project_response()]))

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
    fake_client = GeminiClient(
        api_key="fake",
        transport=FakeTransport([_user_response(), _project_response(health_adjustment=1, health_reason=None)]),
    )

    dream.run(db_session, client.manager_id, client=fake_client)

    session = get_project_session(project)
    try:
        log = session.query(HealthLog).one()
        assert log.llm_adjustment == 0
        assert log.reason is None
        assert log.final_score == log.base_score
    finally:
        session.close()


def test_18_project_db_failure_after_llm_call_is_isolated_and_leaves_summary_untouched(client, db_session, project, monkeypatch):
    """If the project db commit itself fails (e.g. a lock), the function
    must not have already overwritten summary.md -- verifies the DB-then-
    files ordering fixed in review. The overall dream() run is unaffected:
    other phases already succeeded, so events still get dreamed (per-
    project failures are isolated, same as step 24's fan-out)."""
    from app.projects.db import get_project_session as real_get_project_session

    original_summary = summary_md_path(project).read_text() if summary_md_path(project).exists() else ""

    def _boom_commit_session(project_id):
        session = real_get_project_session(project_id)
        session.commit = lambda: (_ for _ in ()).throw(RuntimeError("simulated commit failure"))
        return session

    monkeypatch.setattr("app.projects.db.get_project_session", _boom_commit_session)

    ev = _add_event(db_session, project_ids=json.dumps([project]), general=False)
    fake_client = GeminiClient(
        api_key="fake",
        transport=FakeTransport([_user_response(), _project_response(summary_md="# Should never land")]),
    )

    stats = dream.run(db_session, client.manager_id, client=fake_client)

    assert stats["events_dreamed"] == 1  # user-level phase succeeded, so the batch is still dreamed
    assert stats["projects_synthesized"] == 0  # this project's synthesis was skipped
    db_session.refresh(ev)
    assert ev.dreamed is True
    current_summary = summary_md_path(project).read_text() if summary_md_path(project).exists() else ""
    assert current_summary == original_summary  # never overwritten -- commit failed first
