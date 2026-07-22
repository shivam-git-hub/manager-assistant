"""Step 23 (prompts/step_23_heartbeat_user.md): claims -> typed/tagged/
severity-scored events, user-level half of heartbeat. Do not confuse with
tests/test_heartbeat.py, which is v1 code slated for full replacement."""
import json
import uuid

from app.agent.gemini_client import GeminiClient
from app.config import HEARTBEAT_CLAIM_BATCH_SIZE
from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Project as RegistryProject
from app.database import Claim, Event
from app.projectkb.jobs import heartbeat


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


def _gemini_response(events_obj):
    return {
        "candidates": [
            {
                "content": {"parts": [{"text": json.dumps(events_obj)}]},
                "finishReason": "STOP",
            }
        ]
    }


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
    cdb = ControlPlaneSessionLocal()
    try:
        project = RegistryProject(id=uuid.uuid4().hex, name=name, kind="team", manager_user_id=manager_id)
        cdb.add(project)
        cdb.commit()
        return project.id
    finally:
        cdb.close()


def test_01_zero_pending_claims_short_circuits_without_llm_call(client, db_session):
    transport = FakeTransport([])
    fake_client = GeminiClient(api_key="fake-key", transport=transport)

    stats = heartbeat.run(db_session, client.manager_id, client=fake_client)

    assert stats == {"claims_consumed": 0, "events_created": 0, "users_processed": 1}
    assert transport.call_count == 0


def test_02_routine_progress_keeps_low_severity(client, db_session):
    claim = _add_claim(db_session, text="Alice finished the API refactor")
    response = _gemini_response(
        {"events": [{"type": "status_update", "severity": 0, "title": "API refactor done", "body": None, "general": True, "claim_ids": [claim.id]}]}
    )
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport([response]))

    stats = heartbeat.run(db_session, client.manager_id, client=fake_client)

    assert stats["events_created"] == 1
    event = db_session.query(Event).one()
    assert event.severity == 0
    assert event.type == "status_update"


def test_03_blocker_severity_floor_enforced_in_code(client, db_session):
    claim = _add_claim(db_session, text="Bob is blocked on the staging DB credentials")
    # Model tries to under-score a blocker at 0 -- code must floor it to 1.
    response = _gemini_response(
        {"events": [{"type": "blocker", "severity": 0, "title": "Bob blocked on staging creds", "general": True, "claim_ids": [claim.id]}]}
    )
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport([response]))

    heartbeat.run(db_session, client.manager_id, client=fake_client)

    event = db_session.query(Event).one()
    assert event.type == "blocker"
    assert event.severity == 1


def test_04_consumed_claims_marked_processed_with_event_creation(client, db_session):
    claim = _add_claim(db_session)
    response = _gemini_response(
        {"events": [{"type": "fyi", "severity": 1, "title": "FYI note", "general": True, "claim_ids": [claim.id]}]}
    )
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport([response]))

    stats = heartbeat.run(db_session, client.manager_id, client=fake_client)

    assert stats["claims_consumed"] == 1
    db_session.refresh(claim)
    assert claim.processed is True


def test_05_unknown_project_id_falls_back_to_general(client, db_session):
    claim = _add_claim(db_session, text="Something about a project nobody recognizes")
    response = _gemini_response(
        {
            "events": [
                {
                    "type": "status_update",
                    "severity": 1,
                    "title": "Update on unknown project",
                    "general": False,
                    "project_ids": ["does-not-exist"],
                    "claim_ids": [claim.id],
                }
            ]
        }
    )
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport([response]))

    heartbeat.run(db_session, client.manager_id, client=fake_client)

    event = db_session.query(Event).one()
    assert event.general is True
    assert (json.loads(event.project_ids) if event.project_ids else []) == []


def test_06_known_project_id_tags_event_correctly(client, db_session):
    project_id = _make_owned_project(client.manager_id, name="Phoenix")
    claim = _add_claim(db_session, text="Phoenix rollout is on track")
    response = _gemini_response(
        {
            "events": [
                {
                    "type": "status_update",
                    "severity": 1,
                    "title": "Phoenix rollout on track",
                    "general": False,
                    "project_ids": [project_id],
                    "claim_ids": [claim.id],
                }
            ]
        }
    )
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport([response]))

    heartbeat.run(db_session, client.manager_id, client=fake_client)

    event = db_session.query(Event).one()
    assert event.general is False
    assert json.loads(event.project_ids) == [project_id]


def test_07_batch_size_cap_leaves_excess_for_next_tick(client, db_session):
    claims = [_add_claim(db_session, text=f"routine update {i}") for i in range(HEARTBEAT_CLAIM_BATCH_SIZE + 5)]
    response = _gemini_response({"events": []})
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport([response]))

    stats = heartbeat.run(db_session, client.manager_id, client=fake_client)

    assert stats["claims_consumed"] == HEARTBEAT_CLAIM_BATCH_SIZE
    for c in claims:
        db_session.refresh(c)
    processed_count = sum(1 for c in claims if c.processed)
    assert processed_count == HEARTBEAT_CLAIM_BATCH_SIZE


def test_08_llm_failure_leaves_claims_unprocessed(client, db_session):
    claim = _add_claim(db_session)
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport([RuntimeError("boom")]))

    stats = heartbeat.run(db_session, client.manager_id, client=fake_client)

    assert stats == {"claims_consumed": 0, "events_created": 0, "users_processed": 1}
    db_session.refresh(claim)
    assert claim.processed is False


def test_10_clarification_severity_floor_enforced_in_code(client, db_session):
    claim = _add_claim(db_session, text="Needs clarification on the API contract")
    response = _gemini_response(
        {"events": [{"type": "clarification", "severity": 0, "title": "Clarification needed", "general": True, "claim_ids": [claim.id]}]}
    )
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport([response]))

    heartbeat.run(db_session, client.manager_id, client=fake_client)

    event = db_session.query(Event).one()
    assert event.severity == 1


def test_11_mixed_known_and_unknown_project_ids_keeps_only_known(client, db_session):
    project_id = _make_owned_project(client.manager_id, name="Phoenix")
    claim = _add_claim(db_session, text="Phoenix update, also mentions a made-up project")
    response = _gemini_response(
        {
            "events": [
                {
                    "type": "status_update",
                    "severity": 1,
                    "title": "Phoenix + unknown project",
                    "general": False,
                    "project_ids": [project_id, "hallucinated-id"],
                    "claim_ids": [claim.id],
                }
            ]
        }
    )
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport([response]))

    heartbeat.run(db_session, client.manager_id, client=fake_client)

    event = db_session.query(Event).one()
    assert event.general is False
    assert json.loads(event.project_ids) == [project_id]


def test_12_multiple_claims_combine_into_one_event(client, db_session):
    claim_a = _add_claim(db_session, text="Alice agreed to the new deadline")
    claim_b = _add_claim(db_session, text="Bob confirmed the same new deadline")
    response = _gemini_response(
        {
            "events": [
                {
                    "type": "commitment",
                    "severity": 1,
                    "title": "New deadline agreed by both",
                    "general": True,
                    "claim_ids": [claim_a.id, claim_b.id],
                }
            ]
        }
    )
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport([response]))

    stats = heartbeat.run(db_session, client.manager_id, client=fake_client)

    assert stats["claims_consumed"] == 2
    assert stats["events_created"] == 1
    event = db_session.query(Event).one()
    assert set(json.loads(event.claim_ids)) == {claim_a.id, claim_b.id}
    db_session.refresh(claim_a)
    db_session.refresh(claim_b)
    assert claim_a.processed is True
    assert claim_b.processed is True


def test_13_recent_events_and_memory_md_are_included_in_the_prompt(client, db_session, tmp_path, monkeypatch):
    from app.tenancy import paths as tenancy_paths

    old_claim = _add_claim(db_session, text="already handled thing")
    db_session.add(Event(id=uuid.uuid4().hex, type="fyi", severity=1, title="PRIOR_EVENT_MARKER", general=True))
    db_session.commit()

    memory_path = tmp_path / "memory.md"
    memory_path.write_text("MEMORY_MARKER: user prefers async updates", encoding="utf-8")
    monkeypatch.setattr(tenancy_paths, "manager_memory_md_path", lambda manager_id: memory_path)
    monkeypatch.setattr(heartbeat, "manager_memory_md_path", lambda manager_id: memory_path)

    claim = _add_claim(db_session, text="new claim needing judgment")
    response = _gemini_response(
        {"events": [{"type": "fyi", "severity": 0, "title": "noted", "general": True, "claim_ids": [claim.id]}]}
    )
    transport = FakeTransport([response])
    fake_client = GeminiClient(api_key="fake-key", transport=transport)

    heartbeat.run(db_session, client.manager_id, client=fake_client)

    assert transport.call_count == 1
    sent_payload = transport.calls[0][1]
    prompt_text = json.dumps(sent_payload)
    assert "PRIOR_EVENT_MARKER" in prompt_text
    assert "MEMORY_MARKER" in prompt_text


def test_09_events_show_up_through_home_api(client, db_session):
    claim = _add_claim(db_session, text="Urgent: repeated unanswered outreach to vendor")
    response = _gemini_response(
        {"events": [{"type": "request", "severity": 3, "title": "Vendor unresponsive", "general": True, "claim_ids": [claim.id]}]}
    )
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport([response]))

    heartbeat.run(db_session, client.manager_id, client=fake_client)

    r = client.get("/api/events?min_severity=0")
    assert r.status_code == 200
    body = r.json()
    assert body["max_severity"] == 3
    titles = [e["title"] for e in body["events"]]
    assert "Vendor unresponsive" in titles
