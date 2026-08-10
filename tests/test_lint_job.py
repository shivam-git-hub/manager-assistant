"""Tests for app/projectkb/jobs/lint.py -- net-new job with zero prior
coverage. Two halves: deterministic checks (zero LLM calls, always run) and
an optional coherence pass (only runs when issues_found > 0).

Follows the FakeTransport/_gemini_response_text/_gemini_tool_call_response
idiom shared by test_heartbeat_user.py / test_heartbeat_project_fanout.py
(see tests/conftest.py)."""
import json
import shutil
import uuid
from datetime import timedelta

import pytest

from app import timeservice
from app.agent.gemini_client import GeminiClient
from app.agent.notes import AgentNote
from app.config import DREAM_INTERVAL_MINUTES, HEARTBEAT_INTERVAL_MINUTES
from app.database import Claim, ClaimSource, Event, UnifiedMessage
from app.projectkb.jobs import lint
from app.projects.db import get_project_session
from app.projects.models import Conflict, Task
from app.projects.paths import project_dir
from tests.conftest import FakeTransport, _gemini_response_text, _gemini_tool_call_response


@pytest.fixture()
def project(client):
    r = client.post(
        "/api/projects",
        json={"name": "Lint Project", "kind": "team", "description": "Test project description for automated tests."},
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    yield pid
    shutil.rmtree(project_dir(pid), ignore_errors=True)


def _add_claim(db_session, **kw) -> Claim:
    defaults = dict(
        id=uuid.uuid4().hex,
        text="Some claim text",
        content_hash=uuid.uuid4().hex,
        processed=True,
    )
    defaults.update(kw)
    claim = Claim(**defaults)
    db_session.add(claim)
    db_session.commit()
    db_session.refresh(claim)
    return claim


def _add_event(db_session, **kw) -> Event:
    defaults = dict(
        id=uuid.uuid4().hex,
        type="status_update",
        severity=1,
        title="Some event",
        general=False,
        dreamed=True,
    )
    defaults.update(kw)
    event = Event(**defaults)
    db_session.add(event)
    db_session.commit()
    db_session.refresh(event)
    return event


def _no_llm_run():
    """Should never actually be reached in a clean-run test -- included only
    for tests that DO expect the coherence pass to fire with no findings."""
    return [_gemini_response_text("done")]


def _coherence_run(*tool_calls):
    return [*tool_calls, _gemini_response_text("done")]


# ── clean run: zero issues, zero LLM calls ─────────────────────────────


def test_01_clean_kb_produces_no_issues_and_no_llm_calls(client, db_session):
    transport = FakeTransport([])
    fake_client = GeminiClient(api_key="fake", transport=transport)

    result = lint.run(db_session, client.manager_id, client=fake_client)

    assert result["issues_found"] == 0
    assert result["by_severity"] == {"info": 0, "warn": 0, "error": 0}
    assert result["coherence_ran"] is False
    assert result["coherence_findings"] == 0
    assert result["llm_calls"] == 0
    assert result["stop_reason"] is None
    assert transport.call_count == 0
    assert db_session.query(AgentNote).filter(AgentNote.kind == "lint_finding").count() == 0


def test_02_clean_kb_with_owned_project_and_no_problems_still_zero_issues(client, db_session, project):
    transport = FakeTransport([])
    fake_client = GeminiClient(api_key="fake", transport=transport)

    result = lint.run(db_session, client.manager_id, client=fake_client)

    assert result["issues_found"] == 0
    assert result["owned_projects_checked"] == 1
    assert result["coherence_ran"] is False
    assert transport.call_count == 0


# ── deterministic check 1: orphaned ClaimSource ─────────────────────────


def test_03_orphaned_claim_source_detected(client, db_session):
    claim = _add_claim(db_session)
    orphan = ClaimSource(claim_id=claim.id, message_id="does-not-exist")
    db_session.add(orphan)
    db_session.commit()

    tally: dict = {}
    counters: dict = {}
    lint._check_claim_sources(db_session, tally, counters)

    assert counters["claim_source_orphan"] == 1
    assert tally["error"] == 1
    note = db_session.query(AgentNote).filter(AgentNote.kind == "lint_finding").one()
    content = json.loads(note.content)
    assert content["check"] == "claim_source_orphan"
    assert content["severity"] == "error"


def test_03b_valid_claim_source_not_flagged(client, db_session):
    claim = _add_claim(db_session)
    msg = UnifiedMessage(
        platform_msg_id=uuid.uuid4().hex,
        source="slack",
        direction="inbound",
        sender_raw_id="U1",
        channel_raw_id="C1",
        thread_id="t1",
        content="hi",
        timestamp=timeservice.now_ist(),
        created_at=timeservice.now_ist(),
    )
    db_session.add(msg)
    db_session.commit()
    valid = ClaimSource(claim_id=claim.id, message_id=msg.id)
    db_session.add(valid)
    db_session.commit()

    tally: dict = {}
    counters: dict = {}
    lint._check_claim_sources(db_session, tally, counters)

    assert counters == {}
    assert tally == {}


# ── deterministic check 2: orphaned Event.claim_ids ─────────────────────


def test_04_orphaned_event_claim_ids_detected(client, db_session):
    _add_event(db_session, claim_ids=json.dumps(["missing-claim-id"]))

    tally: dict = {}
    counters: dict = {}
    lint._check_event_claim_ids(db_session, tally, counters)

    assert counters["event_claim_orphan"] == 1
    assert tally["error"] == 1


# ── deterministic check 3: orphaned refs in owned project dbs ───────────


def test_05_orphaned_task_parent_detected(client, db_session, project):
    session = get_project_session(project)
    try:
        task = Task(id=uuid.uuid4().hex, parent_task_id="missing-parent", title="Child", created_by="manager")
        session.add(task)
        session.commit()
    finally:
        session.close()

    tally: dict = {}
    counters: dict = {}
    lint._check_project_orphans(db_session, [project], tally, counters)

    assert counters["task_orphan_parent"] == 1
    assert tally["error"] == 1


def test_06_orphaned_conflict_claim_ref_detected(client, db_session, project):
    session = get_project_session(project)
    try:
        conflict = Conflict(claim_a_ref="missing-a", claim_b_ref="missing-b")
        session.add(conflict)
        session.commit()
    finally:
        session.close()

    tally: dict = {}
    counters: dict = {}
    lint._check_project_orphans(db_session, [project], tally, counters)

    assert counters["conflict_orphan_claim"] == 1
    assert tally["error"] == 1


def test_07_orphaned_event_task_ids_detected(client, db_session, project):
    _add_event(db_session, project_ids=json.dumps([project]), task_ids=json.dumps(["missing-task"]))

    tally: dict = {}
    counters: dict = {}
    lint._check_project_orphans(db_session, [project], tally, counters)

    assert counters["event_task_orphan"] == 1
    assert tally["error"] == 1


def test_07b_not_owned_project_is_skipped(client, db_session):
    other_project_id = uuid.uuid4().hex  # not registered / not owned
    tally: dict = {}
    counters: dict = {}
    # No owned_project_ids passed at all -- mirrors run()'s own gating.
    lint._check_project_orphans(db_session, [], tally, counters)
    assert counters == {}
    assert tally == {}


# ── deterministic check 4: staleness ─────────────────────────────────────


def test_08_stuck_unprocessed_claim_detected(client, db_session):
    old = timeservice.now_ist() - timedelta(minutes=HEARTBEAT_INTERVAL_MINUTES * lint._HEARTBEAT_STALL_CYCLES + 5)
    _add_claim(db_session, processed=False, created_at=old)

    tally: dict = {}
    counters: dict = {}
    lint._check_staleness(db_session, client.manager_id, [], tally, counters)

    assert counters["claim_stuck_unprocessed"] == 1
    assert tally["warn"] == 1


def test_08b_recently_unprocessed_claim_not_flagged(client, db_session):
    # Calculate a duration that is safely less than HEARTBEAT_INTERVAL_MINUTES * _HEARTBEAT_STALL_CYCLES
    # even when HEARTBEAT_INTERVAL_MINUTES is configured to be very small (e.g. 5 seconds in development)
    safe_recent_minutes = min(1.0, HEARTBEAT_INTERVAL_MINUTES * 0.5)
    recent = timeservice.now_ist() - timedelta(minutes=safe_recent_minutes)
    _add_claim(db_session, processed=False, created_at=recent)

    tally: dict = {}
    counters: dict = {}
    lint._check_staleness(db_session, client.manager_id, [], tally, counters)

    assert counters == {}


def test_09_stuck_undreamed_event_detected(client, db_session):
    old = timeservice.now_ist() - timedelta(minutes=DREAM_INTERVAL_MINUTES * lint._DREAM_STALL_CYCLES + 5)
    _add_event(db_session, dreamed=False, created_at=old)

    tally: dict = {}
    counters: dict = {}
    lint._check_staleness(db_session, client.manager_id, [], tally, counters)

    assert counters["event_stuck_undreamed"] == 1
    assert tally["warn"] == 1


# ── deterministic check 5: poll health ───────────────────────────────────


def test_10_stale_outlook_poll_detected(client, db_session):
    from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, get_employee_by_manager_id
    from app.projectkb.job_schedule import load_job_schedule
    from app.projectkb.enums import JobName

    schedule = load_job_schedule()
    outlook_interval = schedule[JobName.OUTLOOK_POLL.value]["interval_minutes"]
    stale_at = timeservice.now_ist() - timedelta(minutes=outlook_interval * lint._POLL_STALE_MULTIPLIER + 5)

    cdb = ControlPlaneSessionLocal()
    try:
        emp = get_employee_by_manager_id(cdb, client.manager_id)
        emp.outlook_mailbox_email = "test@example.com"
        emp.outlook_poll_last_success_at = stale_at
        cdb.commit()
    finally:
        cdb.close()

    tally: dict = {}
    counters: dict = {}
    lint._check_poll_health(db_session, client.manager_id, tally, counters)

    assert counters["outlook_poll_stale"] == 1
    assert tally["warn"] == 1


def test_10b_unconfigured_connector_not_flagged(client, db_session):
    # Neither outlook nor slack connected on a fresh test manager -- no
    # finding expected, NULL timestamps are the expected "never connected"
    # state, not staleness.
    tally: dict = {}
    counters: dict = {}
    lint._check_poll_health(db_session, client.manager_id, tally, counters)
    assert counters == {}


# ── coherence pass gating + counting ─────────────────────────────────────


def test_11_issues_found_triggers_coherence_pass_with_llm_calls(client, db_session):
    _add_event(db_session, claim_ids=json.dumps(["missing-claim-id"]))  # one deterministic finding
    transport = FakeTransport(_coherence_run())
    fake_client = GeminiClient(api_key="fake", transport=transport)

    result = lint.run(db_session, client.manager_id, client=fake_client)

    assert result["issues_found"] == 1
    assert result["coherence_ran"] is True
    assert transport.call_count == 1
    assert result["llm_calls"] == 1
    assert result["stop_reason"] == "final"


def test_12_coherence_pass_report_finding_persisted_and_counted(client, db_session):
    _add_event(db_session, claim_ids=json.dumps(["missing-claim-id"]))  # one deterministic finding
    tool_call = _gemini_tool_call_response(
        "report_finding",
        {
            "check": "summary_contradicts_recent_event",
            "severity": "warn",
            "subject_ref": f"project:{uuid.uuid4().hex}",
            "detail": "summary.md says done but a recent event says blocked",
        },
    )
    transport = FakeTransport(_coherence_run(tool_call))
    fake_client = GeminiClient(api_key="fake", transport=transport)

    result = lint.run(db_session, client.manager_id, client=fake_client)

    assert result["coherence_findings"] == 1
    notes = db_session.query(AgentNote).filter(AgentNote.kind == "lint_finding").all()
    # one deterministic note (event_claim_orphan) + one coherence note
    contents = [json.loads(n.content) for n in notes]
    checks = {c["check"] for c in contents}
    assert "event_claim_orphan" in checks
    assert "summary_contradicts_recent_event" in checks


def test_13_report_finding_invalid_severity_falls_back_to_warn(db_session):
    result = lint.report_finding_handler(
        db_session,
        "some-manager-id",
        None,
        check="bogus_check",
        severity="critical",  # not in _VALID_SEVERITIES
        detail="whatever",
    )
    assert result["success"] is True
    note = db_session.query(AgentNote).filter(AgentNote.id == result["note_id"]).one()
    content = json.loads(note.content)
    assert content["severity"] == "warn"


def test_14_report_finding_populates_run_context_llm_findings(db_session):
    run_context: dict = {}
    lint.report_finding_handler(
        db_session,
        "some-manager-id",
        run_context,
        check="a_check",
        severity="info",
        detail="some detail",
        subject_ref="event:123",
    )
    assert len(run_context["llm_findings"]) == 1
    assert run_context["llm_findings"][0]["check"] == "a_check"
    assert run_context["llm_findings"][0]["severity"] == "info"
    assert run_context["llm_findings"][0]["subject_ref"] == "event:123"


def test_15_end_to_end_multiple_findings_aggregate_severities(client, db_session):
    _add_event(db_session, claim_ids=json.dumps(["missing-1"]))
    _add_event(db_session, claim_ids=json.dumps(["missing-2"]))
    old = timeservice.now_ist() - timedelta(minutes=HEARTBEAT_INTERVAL_MINUTES * lint._HEARTBEAT_STALL_CYCLES + 5)
    _add_claim(db_session, processed=False, created_at=old)

    transport = FakeTransport(_coherence_run())
    fake_client = GeminiClient(api_key="fake", transport=transport)

    result = lint.run(db_session, client.manager_id, client=fake_client)

    assert result["issues_found"] == 3
    assert result["by_severity"]["error"] == 2
    assert result["by_severity"]["warn"] == 1
    assert result["coherence_ran"] is True
