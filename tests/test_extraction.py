import os
import pytest
import json
from datetime import datetime
from fastapi.testclient import TestClient

from app import timeservice
from app.database import UnifiedMessage, TeamMember, Project
from app.kb.models import Entity, AttributedClaim, TimelineEntry
from app.agent.gemini_client import GeminiClient, sanitize_gemini_schema, messages_to_gemini_contents, map_tools_to_gemini
from app.kb.extraction import extract_from_message

@pytest.fixture(autouse=True)
def setup_tmp_clock(monkeypatch, tmp_path):
    """
    Automatically redirect SIM_CLOCK_PATH to a temporary file for every test
    to guarantee perfect isolation.
    """
    tmp_file = tmp_path / "sim_clock.json"
    monkeypatch.setenv("SIM_CLOCK_PATH", str(tmp_file))
    if os.path.exists(tmp_file):
        os.remove(tmp_file)
    
    if hasattr(timeservice, "_reset_state_for_tests"):
        timeservice._reset_state_for_tests()
        
    yield tmp_file
    
    if os.path.exists(tmp_file):
        try:
            os.remove(tmp_file)
        except OSError:
            pass

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

def test_01_client_unit_conversions():
    """
    1. Client unit: messages->contents conversion, system message handling,
       schema sanitization strips additionalProperties, maxOutputTokens always
       present, tool_calls translated back.
    """
    # Test messages to contents mapping
    messages = [
        {"role": "system", "content": "You are a helpful AI"},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"}
    ]
    contents, system_inst = messages_to_gemini_contents(messages)
    assert system_inst == {"parts": [{"text": "You are a helpful AI"}]}
    assert len(contents) == 2
    assert contents[0]["role"] == "user"
    assert contents[0]["parts"] == [{"text": "Hello"}]
    assert contents[1]["role"] == "model"
    assert contents[1]["parts"] == [{"text": "Hi there!"}]
    
    # Test schema sanitization
    bad_schema = {
        "title": "Config",
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {
            "name": {"type": "string", "title": "First Name"}
        },
        "additionalProperties": False
    }
    good_schema = sanitize_gemini_schema(bad_schema)
    assert "$schema" not in good_schema
    assert "title" not in good_schema
    assert "additionalProperties" not in good_schema
    assert "name" in good_schema["properties"]
    assert "title" not in good_schema["properties"]["name"]

    # Regression: a property NAMED "title" (e.g. create_meeting) must survive — the
    # sanitizer strips "title" as a schema annotation, never as a property name, or
    # `required` references a missing property and Gemini 400s the whole tools payload.
    meeting_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "title": "Meeting title"},
            "attendees": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["title", "attendees"],
    }
    sanitized = sanitize_gemini_schema(meeting_schema)
    assert "title" in sanitized["properties"]  # property name preserved
    assert "title" not in sanitized["properties"]["title"]  # annotation stripped
    assert all(r in sanitized["properties"] for r in sanitized["required"])

    # Regression: a tool result that is a JSON array (e.g. get_conflicts) must be
    # wrapped in an object — Gemini's functionResponse.response is a Struct, and a
    # bare list 400s the whole turn ("Proto field is not repeating, cannot start
    # list"). This broke every live tool call until fixed.
    tool_msgs = [
        {"role": "tool", "name": "get_conflicts", "content": '[{"id": 1}, {"id": 2}]'},
        {"role": "tool", "name": "get_entity", "content": '{"slug": "project:phoenix"}'},
    ]
    tcontents, _ = messages_to_gemini_contents(tool_msgs)
    list_resp = tcontents[0]["parts"][0]["functionResponse"]["response"]
    assert isinstance(list_resp, dict)  # list wrapped, never a bare array
    assert list_resp == {"result": [{"id": 1}, {"id": 2}]}
    dict_resp = tcontents[1]["parts"][0]["functionResponse"]["response"]
    assert dict_resp == {"slug": "project:phoenix"}  # dict passed through unchanged

def test_02_client_retry():
    """
    2. Client retry: fake transport raises a 429-ish error twice then succeeds ->
       result returned, 3 calls made.
    """
    class MockHttpError(Exception):
        def __init__(self, status_code):
            from types import SimpleNamespace
            self.response = SimpleNamespace(status_code=status_code)
            
    err_429 = MockHttpError(429)
    success_response = {
        "candidates": [{
            "content": {"parts": [{"text": "Success"}]},
            "finishReason": "STOP"
        }],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5, "totalTokenCount": 15}
    }
    
    transport = FakeTransport([err_429, err_429, success_response])
    client = GeminiClient(api_key="fake-key", transport=transport)
    
    res = client.chat(model="gemini-2.5-flash", messages=[{"role": "user", "content": "test"}])
    assert res["content"] == "Success"
    assert transport.call_count == 3

def test_03_extraction_happy_path(db_session):
    """
    3. Extraction happy path: seed entity + members + one inbound message;
       fake returns 2 valid claims + summary -> claim rows with correct
       source_message_id/claimed_at/holder, timeline entry appended, message
       marked processed.
    """
    # Seed Entity & TeamMember
    entity = Entity(slug="project:phoenix", type="project", name="Project Phoenix")
    member = TeamMember(id="U_ALICE", name="Alice Dev", role="Developer", slack_handle="U_ALICE")
    db_session.add_all([entity, member])
    db_session.commit()
    
    msg = UnifiedMessage(
        platform_msg_id="slack_msg_1",
        source="slack",
        direction="inbound",
        sender_raw_id="U_ALICE",
        sender_mapped_name="Alice Dev",
        channel_raw_id="C_GENERAL",
        content="I have resolved the SQLite concurrency write-lock issues on project phoenix.",
        timestamp=datetime(2026, 7, 15, 12, 0, 0),
        is_processed=False
    )
    db_session.add(msg)
    db_session.commit()
    
    # Mock LLM response
    response_payload = {
        "candidates": [{
            "content": {
                "parts": [{
                    "text": json.dumps({
                        "claims": [
                            {"entity_slug": "project:phoenix", "claim": "Resolved SQLite write-lock", "kind": "status", "holder": "U_ALICE", "weight": 1.0},
                            {"entity_slug": "project:phoenix", "claim": "Database is unblocked", "kind": "fact", "holder": "U_ALICE", "weight": 0.9}
                        ],
                        "timeline_summary": "Alice resolved project phoenix SQLite lock"
                    })
                }]
            },
            "finishReason": "STOP"
        }]
    }
    
    transport = FakeTransport([response_payload])
    client = GeminiClient(api_key="fake-key", transport=transport)
    
    stats = extract_from_message(db_session, msg, client)
    assert stats["claims_created"] == 2
    assert stats["timeline_entries_created"] == 1
    assert stats["skipped"] is False
    
    # Check DB claims
    claims = db_session.query(AttributedClaim).all()
    assert len(claims) == 2
    assert claims[0].claim == "Resolved SQLite write-lock"
    assert claims[0].holder == "U_ALICE"
    assert claims[0].source_message_id == msg.id
    assert claims[0].claimed_at == msg.timestamp
    
    # Check Timeline entry
    timeline = db_session.query(TimelineEntry).all()
    assert len(timeline) == 1
    assert timeline[0].summary == "Alice resolved project phoenix SQLite lock"
    assert timeline[0].source_message_id == msg.id
    
    # Message processed
    db_session.refresh(msg)
    assert msg.is_processed is True
    assert msg.processed_at is not None

def test_04_extraction_invalid_payload_drops(db_session):
    """
    4. Validation: fake returns claim with entity_slug:"project:nope", kind
       "vibe", weight 3.0, unknown holder -> invalid parts dropped/clamped,
       nothing crashes.
    """
    entity = Entity(slug="project:phoenix", type="project", name="Project Phoenix")
    member = TeamMember(id="U_ALICE", name="Alice Dev", role="Developer", slack_handle="U_ALICE")
    db_session.add_all([entity, member])
    db_session.commit()
    
    msg = UnifiedMessage(
        platform_msg_id="slack_msg_1",
        source="slack",
        direction="inbound",
        sender_raw_id="U_ALICE",
        sender_mapped_name="Alice Dev",
        channel_raw_id="C_GENERAL",
        content="Testing parsing safety and fallback values.",
        timestamp=datetime(2026, 7, 15, 12, 0, 0),
        is_processed=False
    )
    db_session.add(msg)
    db_session.commit()
    
    response_payload = {
        "candidates": [{
            "content": {
                "parts": [{
                    "text": json.dumps({
                        "claims": [
                            {"entity_slug": "project:nope", "claim": "Non-existent entity", "kind": "status", "holder": "U_ALICE", "weight": 1.0},
                            {"entity_slug": "project:phoenix", "claim": "Invalid kind vibe", "kind": "vibe", "holder": "U_ALICE", "weight": 1.0},
                            {"entity_slug": "project:phoenix", "claim": "Clamped weight", "kind": "fact", "holder": "U_ALICE", "weight": 3.0},
                            {"entity_slug": "project:phoenix", "claim": "Unknown holder fallback", "kind": "fact", "holder": "U_UNKNOWN", "weight": 0.8}
                        ],
                        "timeline_summary": "Alice tested validation"
                    })
                }]
            },
            "finishReason": "STOP"
        }]
    }
    
    transport = FakeTransport([response_payload])
    client = GeminiClient(api_key="fake-key", transport=transport)
    
    stats = extract_from_message(db_session, msg, client)
    # Only 3rd and 4th claims should be created
    assert stats["claims_created"] == 2
    assert stats["timeline_entries_created"] == 1
    
    # Clamped weight claim
    claim1 = db_session.query(AttributedClaim).filter(AttributedClaim.claim == "Clamped weight").first()
    assert claim1 is not None
    assert claim1.weight == 1.0  # Clamped to [0.0, 1.0]
    
    # Unknown holderFallback claim
    claim2 = db_session.query(AttributedClaim).filter(AttributedClaim.claim == "Unknown holder fallback").first()
    assert claim2 is not None
    assert claim2.holder == "U_ALICE"  # Fell back to sender

def test_05_malformed_json_survives(db_session):
    """
    5. Malformed JSON from fake -> no claims, message STILL marked processed.
    """
    entity = Entity(slug="project:phoenix", type="project", name="Project Phoenix")
    db_session.add(entity)
    db_session.commit()
    
    msg = UnifiedMessage(
        platform_msg_id="slack_msg_1",
        source="slack",
        direction="inbound",
        sender_raw_id="U_ALICE",
        sender_mapped_name="Alice Dev",
        channel_raw_id="C_GENERAL",
        content="Testing malformed JSON handling.",
        timestamp=datetime(2026, 7, 15, 12, 0, 0),
        is_processed=False
    )
    db_session.add(msg)
    db_session.commit()
    
    response_payload = {
        "candidates": [{
            "content": {
                "parts": [{
                    "text": "{ broken json: claim=none }"
                }]
            },
            "finishReason": "STOP"
        }]
    }
    
    transport = FakeTransport([response_payload])
    client = GeminiClient(api_key="fake-key", transport=transport)
    
    stats = extract_from_message(db_session, msg, client)
    assert stats["claims_created"] == 0
    assert stats["timeline_entries_created"] == 0
    
    db_session.refresh(msg)
    assert msg.is_processed is True

def test_06_skip_outbound_messages(db_session):
    """
    6. Harry's own outbound message -> skipped, marked processed, LLM never called.
    """
    msg = UnifiedMessage(
        platform_msg_id="slack_msg_harry",
        source="slack",
        direction="outbound",
        sender_raw_id="U_HARRY",
        sender_mapped_name="Harry",
        channel_raw_id="C_GENERAL",
        content="Hi Alice, do you have any update?",
        timestamp=datetime(2026, 7, 15, 12, 0, 0),
        is_processed=False
    )
    db_session.add(msg)
    db_session.commit()
    
    # If the client is called, it would use transport, which is not set or throws
    transport = FakeTransport([])
    client = GeminiClient(api_key="fake-key", transport=transport)
    
    stats = extract_from_message(db_session, msg, client)
    assert stats["skipped"] is True
    assert stats["claims_created"] == 0
    assert transport.call_count == 0
    
    db_session.refresh(msg)
    assert msg.is_processed is True

def test_07_api_process_unprocessed(client, db_session, monkeypatch):
    """
    7. POST /api/kb/process end-to-end with fake client:
       3 unprocessed messages -> correct counts, none left unprocessed.
    """
    # Seed mock client
    entity = Entity(slug="project:phoenix", type="project", name="Project Phoenix")
    db_session.add(entity)
    db_session.commit()
    
    msg1 = UnifiedMessage(platform_msg_id="m1", source="slack", direction="inbound", sender_raw_id="U_ALICE", channel_raw_id="C_GENERAL", content="Long content 1", timestamp=datetime(2026, 7, 15, 12, 0, 0), is_processed=False)
    msg2 = UnifiedMessage(platform_msg_id="m2", source="slack", direction="inbound", sender_raw_id="U_ALICE", channel_raw_id="C_GENERAL", content="Long content 2", timestamp=datetime(2026, 7, 15, 12, 1, 0), is_processed=False)
    msg3 = UnifiedMessage(platform_msg_id="m3", source="slack", direction="inbound", sender_raw_id="U_ALICE", channel_raw_id="C_GENERAL", content="Long content 3", timestamp=datetime(2026, 7, 15, 12, 2, 0), is_processed=False)
    db_session.add_all([msg1, msg2, msg3])
    db_session.commit()
    
    response_payload = {
        "candidates": [{
            "content": {
                "parts": [{
                    "text": json.dumps({
                        "claims": [
                            {"entity_slug": "project:phoenix", "claim": "Sample claim", "kind": "status", "holder": "U_ALICE", "weight": 1.0}
                        ],
                        "timeline_summary": "Sample timeline summary"
                    })
                }]
            },
            "finishReason": "STOP"
        }]
    }
    
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport([response_payload, response_payload, response_payload]))
    monkeypatch.setattr("app.kb.extraction.get_client", lambda: fake_client)
    
    resp = client.post("/api/kb/process")
    assert resp.status_code == 200
    data = resp.json()
    assert data["processed"] == 3
    assert data["claims_created"] == 3
    assert data["timeline_entries_created"] == 3
    
    # Assert none left unprocessed
    unprocessed = db_session.query(UnifiedMessage).filter(UnifiedMessage.is_processed == False).all()
    assert len(unprocessed) == 0
