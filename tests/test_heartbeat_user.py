"""Heartbeat, phase-1 (user-level) half: pending claims -> typed/tagged/
severity-scored Event rows via the agentic emit_events tool call. Step 38
rewrite: step 35 replaced the old single-blind-JSON-call heartbeat with a
tool-calling agent (app.agent.runner.run_spec) -- every test here scripts a
FakeTransport turn sequence (emit_events tool call, then a plain-text turn
that ends the run) instead of one JSON blob. Do not confuse with
tests/test_heartbeat.py, which is v1 code slated for full replacement, or
tests/test_heartbeat_project_fanout.py, which covers phase 2."""
import json
import uuid
from datetime import timedelta

import pytest

from app import timeservice
from app.agent.gemini_client import GeminiClient
from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Project as RegistryProject
from app.database import Claim, ClaimSource, Event, UnifiedMessage
from app.projectkb.jobs import heartbeat
from tests.conftest import FakeTransport, _gemini_response_text, _gemini_tool_call_response


def _add_claim(db_session, **kw):
    defaults = dict(
        id=uuid.uuid4().hex,
        text="Bob will finish the migration by Friday",
        thread_key=None,
        content_hash=uuid.uuid4().hex,
        processed=False,
    )
    defaults.update(kw)
    claim = Claim(**defaults)
    db_session.add(claim)
    db_session.commit()
    db_session.refresh(claim)
    return claim


def _make_owned_project(manager_id, name="Phoenix") -> str:
    """A raw registry row is not enough on its own -- build_kb_context (now
    called unconditionally by heartbeat.run for both phases) opens every
    visible project's own db.sqlite for task/blocker counts, so the project
    dir must actually be scaffolded (init_project_db), same as the real
    POST /api/projects path does."""
    from app.projects.db import init_project_db

    cdb = ControlPlaneSessionLocal()
    try:
        project = RegistryProject(id=uuid.uuid4().hex, name=name, kind="team", manager_user_id=manager_id)
        cdb.add(project)
        cdb.commit()
        pid = project.id
    finally:
        cdb.close()
    init_project_db(pid)
    return pid


def _emit_events_run(events):
    """One emit_events tool call followed by a plain-text turn that ends
    the run with stop_reason="final" -- the standard shape for a phase-1
    run that judges its whole batch in one write."""
    return [
        _gemini_tool_call_response("emit_events", {"events": events}),
        _gemini_response_text("done"),
    ]


def test_01_zero_pending_claims_short_circuits_without_llm_call(client, db_session):
    transport = FakeTransport([])
    fake_client = GeminiClient(api_key="fake-key", transport=transport)

    stats = heartbeat.run(db_session, client.manager_id, client=fake_client)

    assert stats["claims_consumed"] == 0
    assert stats["events_created"] == 0
    assert stats["users_processed"] == 1
    assert stats["stop_reason"] == "no_pending_claims"
    assert transport.call_count == 0


def test_02_emit_events_creates_event_and_marks_claim_processed(client, db_session):
    claim = _add_claim(db_session, text="Alice finished the API refactor")
    responses = _emit_events_run(
        [{"type": "status_update", "severity": 1, "title": "API refactor done", "general": True, "claim_ids": [claim.id]}]
    )
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport(responses))

    stats = heartbeat.run(db_session, client.manager_id, client=fake_client)

    assert stats["events_created"] == 1
    assert stats["claims_consumed"] == 1
    event = db_session.query(Event).one()
    assert event.type == "status_update"
    assert event.severity == 1
    assert event.general is True
    db_session.refresh(claim)
    assert claim.processed is True


@pytest.mark.parametrize("etype", ["blocker", "clarification", "conflict"])
def test_03_severity_floor_enforced_for_floor_types(client, db_session, etype):
    claim = _add_claim(db_session, text=f"needs {etype} handling")
    # Model tries to under-score a floor type at 0 -- the emit_events tool
    # handler must floor it to 1, code-enforced independent of the model.
    responses = _emit_events_run(
        [{"type": etype, "severity": 0, "title": f"{etype} title", "general": True, "claim_ids": [claim.id]}]
    )
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport(responses))

    heartbeat.run(db_session, client.manager_id, client=fake_client)

    event = db_session.query(Event).one()
    assert event.type == etype
    assert event.severity == 1


def test_04_invented_project_id_is_dropped_and_falls_back_to_general(client, db_session):
    claim = _add_claim(db_session, text="Something about a project nobody recognizes")
    responses = _emit_events_run(
        [
            {
                "type": "status_update",
                "severity": 1,
                "title": "Update on unknown project",
                "general": False,
                "project_ids": ["does-not-exist"],
                "claim_ids": [claim.id],
            }
        ]
    )
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport(responses))

    heartbeat.run(db_session, client.manager_id, client=fake_client)

    event = db_session.query(Event).one()
    assert event.general is True
    assert (json.loads(event.project_ids) if event.project_ids else []) == []


def test_05_known_project_id_tags_event_correctly(client, db_session, cleanup_projects):
    project_id = _make_owned_project(client.manager_id, name="Phoenix")
    cleanup_projects.append(project_id)
    claim = _add_claim(db_session, text="Phoenix rollout is on track")
    responses = _emit_events_run(
        [
            {
                "type": "status_update",
                "severity": 1,
                "title": "Phoenix rollout on track",
                "general": False,
                "project_ids": [project_id],
                "claim_ids": [claim.id],
            }
        ]
    )
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport(responses))

    heartbeat.run(db_session, client.manager_id, client=fake_client)

    event = db_session.query(Event).one()
    assert event.general is False
    assert json.loads(event.project_ids) == [project_id]


def test_06_invented_claim_id_is_dropped(client, db_session):
    claim = _add_claim(db_session, text="A real claim")
    responses = _emit_events_run(
        [{"type": "fyi", "severity": 0, "title": "note", "general": True, "claim_ids": [claim.id, "hallucinated-claim-id"]}]
    )
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport(responses))

    heartbeat.run(db_session, client.manager_id, client=fake_client)

    event = db_session.query(Event).one()
    assert json.loads(event.claim_ids) == [claim.id]


def test_07_multiple_claims_combine_into_one_event(client, db_session):
    claim_a = _add_claim(db_session, text="Alice agreed to the new deadline")
    claim_b = _add_claim(db_session, text="Bob confirmed the same new deadline")
    responses = _emit_events_run(
        [{"type": "commitment", "severity": 1, "title": "New deadline agreed by both", "general": True, "claim_ids": [claim_a.id, claim_b.id]}]
    )
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport(responses))

    stats = heartbeat.run(db_session, client.manager_id, client=fake_client)

    assert stats["claims_consumed"] == 2
    assert stats["events_created"] == 1
    event = db_session.query(Event).one()
    assert set(json.loads(event.claim_ids)) == {claim_a.id, claim_b.id}
    db_session.refresh(claim_a)
    db_session.refresh(claim_b)
    assert claim_a.processed is True
    assert claim_b.processed is True


def test_08_occurred_at_derived_from_source_message_timestamp(client, db_session):
    """The created Event's occurred_at is the max timestamp of the
    UnifiedMessage rows behind its cited claim, NOT when heartbeat inserted
    the row -- so a claim from a message sent hours ago doesn't get stamped
    with "now"."""
    message_time = timeservice.now_ist() - timedelta(hours=3)
    msg = UnifiedMessage(
        platform_msg_id=f"m_{uuid.uuid4().hex}",
        source="outlook",
        sender_raw_id="bob@company.com",
        channel_raw_id="me@company.com",
        content="Bob: I'll finish the migration by Friday.",
        timestamp=message_time,
    )
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)

    claim = _add_claim(db_session, text="Bob will finish the migration by Friday")
    db_session.add(ClaimSource(claim_id=claim.id, message_id=msg.id))
    db_session.commit()

    responses = _emit_events_run(
        [{"type": "commitment", "severity": 1, "title": "Migration commitment", "general": True, "claim_ids": [claim.id]}]
    )
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport(responses))

    heartbeat.run(db_session, client.manager_id, client=fake_client)

    event = db_session.query(Event).one()
    assert event.occurred_at is not None
    assert abs((event.occurred_at - message_time).total_seconds()) < 1.0
    assert event.created_at is not None


def test_09_occurred_at_none_when_claim_unsourced(client, db_session):
    claim = _add_claim(db_session, text="orphaned claim with no source")
    responses = _emit_events_run(
        [{"type": "fyi", "severity": 0, "title": "noted", "general": True, "claim_ids": [claim.id]}]
    )
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport(responses))

    heartbeat.run(db_session, client.manager_id, client=fake_client)

    event = db_session.query(Event).one()
    assert event.occurred_at is None


# ── claim disposal (spec step 5) ────────────────────────────────────────


def test_10_final_stop_marks_uncited_claims_processed(client, db_session):
    claim_cited = _add_claim(db_session, text="cited claim")
    claim_uncited = _add_claim(db_session, text="uncited claim")
    responses = _emit_events_run(
        [{"type": "fyi", "severity": 0, "title": "note", "general": True, "claim_ids": [claim_cited.id]}]
    )
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport(responses))

    stats = heartbeat.run(db_session, client.manager_id, client=fake_client)

    assert stats["stop_reason"] == "final"
    db_session.refresh(claim_cited)
    db_session.refresh(claim_uncited)
    assert claim_cited.processed is True
    assert claim_uncited.processed is True  # agent finished and chose not to use it


def test_11_budget_stop_leaves_uncited_claims_unprocessed(client, db_session, monkeypatch):
    # Force the phase-1 budget to exhaust after exactly one LLM call, so the
    # run stops with stop_reason="budget" right after the emit_events call
    # executes -- the uncited claim must NOT be swept up the way a "final"
    # stop would sweep it.
    monkeypatch.setattr(heartbeat, "KB_HEARTBEAT_MAX_LLM_CALLS", 1)
    claim_cited = _add_claim(db_session, text="cited claim")
    claim_uncited = _add_claim(db_session, text="uncited claim")
    tool_call = _gemini_tool_call_response(
        "emit_events", {"events": [{"type": "fyi", "severity": 0, "title": "note", "general": True, "claim_ids": [claim_cited.id]}]}
    )
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport([tool_call]))

    stats = heartbeat.run(db_session, client.manager_id, client=fake_client)

    assert stats["stop_reason"] == "budget"
    db_session.refresh(claim_cited)
    db_session.refresh(claim_uncited)
    assert claim_cited.processed is True  # cited by the tool call itself, already committed
    assert claim_uncited.processed is False  # left for next tick


def test_12_starvation_guard_force_processes_claim_after_three_attempts(client, db_session, monkeypatch):
    # A claim offered 3 times without ever being cited is force-processed --
    # otherwise a claim the model keeps declining would be re-sent forever.
    monkeypatch.setattr(heartbeat, "KB_HEARTBEAT_MAX_LLM_CALLS", 1)
    claim = _add_claim(db_session, heartbeat_attempts=2)
    tool_call = _gemini_tool_call_response("emit_events", {"events": []})
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport([tool_call]))

    stats = heartbeat.run(db_session, client.manager_id, client=fake_client)

    assert stats["stop_reason"] == "budget"
    db_session.refresh(claim)
    assert claim.heartbeat_attempts == 3
    assert claim.processed is True


# ── prompt content ───────────────────────────────────────────────────────


def test_13_memory_md_is_included_in_the_prompt(client, db_session, tmp_path, monkeypatch):
    from app.tenancy import paths as tenancy_paths

    memory_path = tmp_path / "memory.md"
    memory_path.write_text("MEMORY_MARKER: user prefers async updates", encoding="utf-8")
    monkeypatch.setattr(tenancy_paths, "manager_memory_md_path", lambda manager_id: memory_path)

    claim = _add_claim(db_session, text="new claim needing judgment")
    responses = _emit_events_run([{"type": "fyi", "severity": 0, "title": "noted", "general": True, "claim_ids": [claim.id]}])
    transport = FakeTransport(responses)
    fake_client = GeminiClient(api_key="fake-key", transport=transport)

    heartbeat.run(db_session, client.manager_id, client=fake_client)

    assert transport.call_count == 2
    sent_payload = transport.calls[0][1]
    prompt_text = json.dumps(sent_payload)
    assert "MEMORY_MARKER" in prompt_text


# ── end-to-end through the home API ─────────────────────────────────────


def test_14_events_show_up_through_home_api(client, db_session):
    claim = _add_claim(db_session, text="Urgent: repeated unanswered outreach to vendor")
    responses = _emit_events_run([{"type": "request", "severity": 3, "title": "Vendor unresponsive", "general": True, "claim_ids": [claim.id]}])
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport(responses))

    heartbeat.run(db_session, client.manager_id, client=fake_client)

    r = client.get("/api/events?min_severity=0")
    assert r.status_code == 200
    body = r.json()
    assert body["max_severity"] == 3
    titles = [e["title"] for e in body["events"]]
    assert "Vendor unresponsive" in titles
