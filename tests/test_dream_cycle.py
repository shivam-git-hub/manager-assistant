import os
import pytest
import json
from datetime import datetime
from fastapi.testclient import TestClient

from app import timeservice
from app.database import UnifiedMessage, TeamMember, Project
from app.kb.models import Entity, AttributedClaim, TimelineEntry, Conflict
from app.agent.gemini_client import GeminiClient
from app.kb.synthesis import run_supersession_pass, synthesize_entity, run_contradiction_probe, run_dream_cycle

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

def test_01_supersession_pass(db_session):
    """
    1. Supersession: same holder+kind pair, fake says true -> old inactive with
       superseded_by set; different holders NEVER sent to the judge.
    """
    entity = Entity(slug="project:phoenix", type="project", name="Project Phoenix")
    db_session.add(entity)
    db_session.commit()
    
    # Seed claims
    claim_old = AttributedClaim(entity_id=entity.id, claim="Old claim", kind="status", holder="U_BOB", weight=1.0, claimed_at=datetime(2026, 7, 15, 10, 0, 0), active=True)
    claim_new = AttributedClaim(entity_id=entity.id, claim="New claim", kind="status", holder="U_BOB", weight=1.0, claimed_at=datetime(2026, 7, 15, 12, 0, 0), active=True)
    claim_alice = AttributedClaim(entity_id=entity.id, claim="Alice claim", kind="status", holder="U_ALICE", weight=1.0, claimed_at=datetime(2026, 7, 15, 12, 0, 0), active=True)
    db_session.add_all([claim_old, claim_new, claim_alice])
    db_session.commit()
    
    response_payload = {
        "candidates": [{
            "content": {
                "parts": [{
                    "text": json.dumps({
                        "results": [
                            {"pair_index": 0, "supersedes": True}
                        ]
                    })
                }]
            },
            "finishReason": "STOP"
        }]
    }
    
    transport = FakeTransport([response_payload])
    client = GeminiClient(api_key="fake-key", transport=transport)
    
    count = run_supersession_pass(db_session, entity, client)
    assert count == 1
    
    # Check that old Bob claim is inactive and points to new
    db_session.refresh(claim_old)
    db_session.refresh(claim_new)
    assert claim_old.active is False
    assert claim_old.superseded_by == claim_new.id
    
    # Alice claim should still be active
    db_session.refresh(claim_alice)
    assert claim_alice.active is True
    
    # Check that Alice's claim was never paired with Bob's claim for the judge
    payload_sent = transport.calls[0][1]
    # The JSON string inside contents has judge_pairs
    payload_contents = payload_sent["contents"][0]["parts"][0]["text"]
    assert "U_ALICE" not in payload_contents

def test_02_synthesis_dirty_check(db_session):
    """
    2. Synthesis dirty-check: entity with no new activity since
       truth_updated_at -> smart model NOT called.
    """
    entity = Entity(slug="project:phoenix", type="project", name="Project Phoenix", compiled_truth="Current state", truth_updated_at=datetime(2026, 7, 15, 12, 0, 0))
    db_session.add(entity)
    db_session.commit()
    
    # Seed timeline entry and claim older than truth_updated_at
    timeline = TimelineEntry(entity_id=entity.id, happened_at=datetime(2026, 7, 15, 11, 0, 0), summary="Old timeline event")
    claim = AttributedClaim(entity_id=entity.id, claim="Old claim", kind="status", holder="U_BOB", weight=1.0, claimed_at=datetime(2026, 7, 15, 11, 0, 0), active=True)
    db_session.add_all([timeline, claim])
    db_session.commit()
    
    transport = FakeTransport([])
    client = GeminiClient(api_key="fake-key", transport=transport)
    
    res = synthesize_entity(db_session, entity, client)
    assert res == "Current state"
    assert transport.call_count == 0

def test_03_synthesis_happy_path_citations_check(db_session, set_sim_time):
    """
    3. Synthesis happy path: fake returns text citing [T{real_id}] and
       [T99999] -> stored truth keeps the real marker, bogus one stripped,
       truth_updated_at = sim now.
    """
    entity = Entity(slug="project:phoenix", type="project", name="Project Phoenix")
    db_session.add(entity)
    db_session.commit()
    
    # Seed timeline
    t = TimelineEntry(entity_id=entity.id, happened_at=datetime(2026, 7, 15, 12, 0, 0), summary="Finished the SQLite DB setup.")
    db_session.add(t)
    db_session.commit()
    
    # Anchor sim time
    anchor_time = datetime(2026, 7, 16, 10, 0, 0)
    set_sim_time(anchor_time)
    
    response_payload = {
        "candidates": [{
            "content": {
                "parts": [{
                    "text": f"The SQLite database setup is successfully completed [T{t.id}]. A bogus citation [T99999] is also present."
                }]
            },
            "finishReason": "STOP"
        }]
    }
    
    transport = FakeTransport([response_payload])
    client = GeminiClient(api_key="fake-key", transport=transport)
    
    res = synthesize_entity(db_session, entity, client)
    # The [T99999] bogus citation should be stripped, and [T{t.id}] preserved!
    assert res is not None
    assert f"[T{t.id}]" in res
    assert "[T99999]" not in res
    
    db_session.refresh(entity)
    assert entity.compiled_truth == res
    assert entity.truth_updated_at is not None
    diff_t = abs((entity.truth_updated_at - anchor_time).total_seconds())
    assert diff_t < 2.0

def test_04_contradiction_probe(db_session):
    """
    4. Probe: two claims, different holders, fake says contradicts/high ->
       conflict row open with severity high; running the probe AGAIN creates no
       duplicate; claims untouched (still active, no supersession).
    """
    entity = Entity(slug="project:phoenix", type="project", name="Project Phoenix")
    db_session.add(entity)
    db_session.commit()
    
    claim1 = AttributedClaim(entity_id=entity.id, claim="Bob sent the schema doc", kind="status", holder="U_BOB", weight=1.0, claimed_at=datetime(2026, 7, 15, 12, 0, 0), active=True)
    claim2 = AttributedClaim(entity_id=entity.id, claim="Alice has not received the schema doc", kind="status", holder="U_ALICE", weight=1.0, claimed_at=datetime(2026, 7, 15, 12, 1, 0), active=True)
    db_session.add_all([claim1, claim2])
    db_session.commit()
    
    response_payload = {
        "candidates": [{
            "content": {
                "parts": [{
                    "text": json.dumps({
                        "results": [
                            {
                                "pair_index": 0,
                                "contradicts": True,
                                "severity": "high",
                                "description": "Factual deadlock between Bob sending and Alice receiving."
                            }
                        ]
                    })
                }]
            },
            "finishReason": "STOP"
        }]
    }
    
    transport = FakeTransport([response_payload])
    client = GeminiClient(api_key="fake-key", transport=transport)
    
    conflicts = run_contradiction_probe(db_session, entity, client)
    assert len(conflicts) == 1
    assert conflicts[0].severity == "high"
    assert conflicts[0].status == "open"
    assert conflicts[0].claim_a_id == claim1.id
    assert conflicts[0].claim_b_id == claim2.id
    
    # Run again with same faked client -> no duplicates created!
    conflicts_second = run_contradiction_probe(db_session, entity, client)
    assert len(conflicts_second) == 0
    
    # Claims remain untouched (active)
    db_session.refresh(claim1)
    db_session.refresh(claim2)
    assert claim1.active is True
    assert claim2.active is True

def test_05_probe_skips_resolved_conflicts(db_session):
    """
    5. Probe pair with an existing RESOLVED conflict -> not re-judged.
    """
    entity = Entity(slug="project:phoenix", type="project", name="Project Phoenix")
    db_session.add(entity)
    db_session.commit()
    
    claim1 = AttributedClaim(entity_id=entity.id, claim="Bob sent the schema doc", kind="status", holder="U_BOB", weight=1.0, claimed_at=datetime(2026, 7, 15, 12, 0, 0), active=True)
    claim2 = AttributedClaim(entity_id=entity.id, claim="Alice has not received the schema doc", kind="status", holder="U_ALICE", weight=1.0, claimed_at=datetime(2026, 7, 15, 12, 1, 0), active=True)
    db_session.add_all([claim1, claim2])
    db_session.commit()
    
    # Create an existing RESOLVED conflict
    conflict = Conflict(
        entity_id=entity.id,
        claim_a_id=claim1.id,
        claim_b_id=claim2.id,
        severity="high",
        description="Factual deadlock resolved",
        status="resolved",
        detected_at=datetime(2026, 7, 15, 12, 0, 0),
        resolved_at=datetime(2026, 7, 15, 13, 0, 0),
        resolution_note="Manually unblocked"
    )
    db_session.add(conflict)
    db_session.commit()
    
    transport = FakeTransport([])
    client = GeminiClient(api_key="fake-key", transport=transport)
    
    conflicts = run_contradiction_probe(db_session, entity, client)
    assert len(conflicts) == 0
    # Judge should NOT be called at all
    assert transport.call_count == 0

def test_06_api_dream_cycle_end_to_end(client, db_session, monkeypatch):
    """
    6. POST /api/kb/dream end-to-end with fakes: unprocessed message -> claim ->
       truth rewritten -> stats dict correct.
    """
    # Seed entity and team member
    entity = Entity(slug="project:phoenix", type="project", name="Project Phoenix")
    member = TeamMember(id="U_ALICE", name="Alice Dev", role="Developer", slack_handle="U_ALICE")
    db_session.add_all([entity, member])
    db_session.commit()
    
    msg = UnifiedMessage(
        platform_msg_id="m1",
        source="slack",
        direction="inbound",
        sender_raw_id="U_ALICE",
        sender_mapped_name="Alice Dev",
        channel_raw_id="C_GENERAL",
        content="Finished project phoenix SQLite setup successfully.",
        timestamp=datetime(2026, 7, 15, 12, 0, 0),
        is_processed=False
    )
    db_session.add(msg)
    db_session.commit()
    
    # Transport mocks for dream cycle:
    # 1. Message Extraction: returns 1 claim
    extract_response = {
        "candidates": [{
            "content": {
                "parts": [{
                    "text": json.dumps({
                        "claims": [
                            {"entity_slug": "project:phoenix", "claim": "Finished SQLite setup", "kind": "status", "holder": "U_ALICE", "weight": 1.0}
                        ],
                        "timeline_summary": "Finished project phoenix SQLite setup"
                    })
                }]
            },
            "finishReason": "STOP"
        }]
    }
    # 2. Supersession pass: 0 pairs (no previous claims)
    # 3. Synthesis: returns rewritten truth
    synthesis_response = {
        "candidates": [{
            "content": {
                "parts": [{
                    "text": "The SQLite database setup is successfully completed."
                }]
            },
            "finishReason": "STOP"
        }]
    }
    # 4. Probe: 0 pairs (only 1 claim from Alice, different holders required)
    
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport([extract_response, synthesis_response]))
    monkeypatch.setattr("app.kb.synthesis.get_client", lambda: fake_client)
    monkeypatch.setattr("app.kb.extraction.get_client", lambda: fake_client)
    
    resp = client.post("/api/kb/dream")
    assert resp.status_code == 200
    data = resp.json()
    assert data["messages_processed"] == 1
    assert data["entities_synthesized"] == 1
    assert data["claims_superseded"] == 0
    assert data["conflicts_found"] == 0
