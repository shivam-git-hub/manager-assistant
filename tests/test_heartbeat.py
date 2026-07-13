import pytest
import json
from datetime import datetime, date, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import select

from app import timeservice
from app.database import TeamMember, Project, Task, UnifiedMessage, Base
from app.kb.models import Entity, AttributedClaim, Conflict
from app.followups import Followup, run_followup_check, get_dm_channel_id
from app.agent.notes import AgentNote, record_note, recent_notes
from app.agent.situation import build_situation
from app.agent.heartbeat import triage, run_heartbeat
from app.agent.gemini_client import GeminiClient
from app.outbound import OutboundQueue
from app.scheduler import ScheduledJob, tick, seed_default_jobs

class FakeTransport:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, url, payload):
        self.calls.append((url, payload))
        if self.responses:
            return self.responses.pop(0)
        return {
            "candidates": [{
                "content": {
                    "parts": [{"text": "{}"}]
                },
                "finishReason": "STOP"
            }],
            "usageMetadata": {}
        }

@pytest.fixture(autouse=True)
def setup_sim_clock():
    # Setup standard daytime
    timeservice.set_time(datetime(2026, 7, 13, 10, 0, 0))
    yield

def test_build_situation(db_session: Session):
    # Seed Alice and Bob
    alice = TeamMember(id="U_ALICE", name="Alice", role="Backend Lead", slack_handle="U_ALICE")
    bob = TeamMember(id="U_BOB", name="Bob", role="Frontend Dev", slack_handle="U_BOB")
    db_session.add_all([alice, bob])
    
    # Seed Project Phoenix
    proj = Project(name="Phoenix", status="active", health="yellow", health_reasons="Alice is blocked.")
    db_session.add(proj)
    db_session.commit()
    
    # Register entities
    ent_proj = Entity(slug="project:phoenix", type="project", name="Phoenix", ref_id=str(proj.id))
    ent_alice = Entity(slug="person:u_alice", type="person", name="Alice", ref_id="U_ALICE")
    ent_bob = Entity(slug="person:u_bob", type="person", name="Bob", ref_id="U_BOB")
    db_session.add_all([ent_proj, ent_alice, ent_bob])
    db_session.commit()
    
    # 1. Unmet commitments
    claim = AttributedClaim(
        entity_id=ent_proj.id,
        claim="Bob promised to send schema document by Friday",
        kind="commitment",
        holder="U_BOB",
        weight=1.0,
        claimed_at=timeservice.now_ist() - timedelta(hours=36),
        active=True
    )
    db_session.add(claim)
    db_session.commit()
    
    # Verify Bob's claim is flagged as unmet commitment
    sit = build_situation(db_session)
    assert sit["has_signals"] is True
    assert len(sit["unmet_claims"]) == 1
    assert sit["unmet_claims"][0]["holder"] == "U_BOB"
    
    # Now simulate Bob sending an inbound message after claimed_at
    msg = UnifiedMessage(
        platform_msg_id="msg_001",
        source="slack",
        direction="inbound",
        sender_raw_id="U_BOB",
        channel_raw_id="DM_U_HARRY_U_BOB",
        content="Hey, working on the schema document right now!",
        timestamp=timeservice.now_ist() - timedelta(hours=2)
    )
    db_session.add(msg)
    db_session.commit()
    
    # Verify Bob's claim is no longer flagged as unmet commitment
    sit = build_situation(db_session)
    assert len(sit["unmet_claims"]) == 0

    # 2. Open Conflict
    claim_a = AttributedClaim(
        entity_id=ent_proj.id,
        claim="Alice claims Bob did not send schema",
        kind="status",
        holder="U_ALICE",
        weight=1.0,
        claimed_at=timeservice.now_ist() - timedelta(hours=2),
        active=True
    )
    claim_b = AttributedClaim(
        entity_id=ent_proj.id,
        claim="Bob claims he sent schema",
        kind="status",
        holder="U_BOB",
        weight=1.0,
        claimed_at=timeservice.now_ist() - timedelta(hours=1),
        active=True
    )
    db_session.add_all([claim_a, claim_b])
    db_session.commit()
    
    conflict = Conflict(
        entity_id=ent_proj.id,
        claim_a_id=claim_a.id,
        claim_b_id=claim_b.id,
        severity="high",
        description="Contradiction on schema delivery",
        status="open",
        detected_at=timeservice.now_ist()
    )
    db_session.add(conflict)
    db_session.commit()
    
    # Conflict is unnudged and should be listed
    sit = build_situation(db_session)
    assert len(sit["unnudged_conflicts"]) == 1
    
    # Now add an open followup to Bob for project:phoenix
    followup = Followup(
        entity_slug="project:phoenix",
        target_member_id="U_BOB",
        question="Did you send the schema?",
        due_at=timeservice.now_ist() + timedelta(hours=24),
        status="open",
        created_at=timeservice.now_ist()
    )
    db_session.add(followup)
    db_session.commit()
    
    # Verify conflict is now filtered out (since Bob is actively being followed up on Phoenix)
    sit = build_situation(db_session)
    assert len(sit["unnudged_conflicts"]) == 0

def test_triage_short_circuit(db_session: Session):
    # No signals seeded
    sit = build_situation(db_session)
    assert sit["has_signals"] is False
    
    # Run triage. Should short circuit without any Gemini client call
    transport = FakeTransport([])
    client = GeminiClient(api_key="fake-key", transport=transport)
    
    res = triage(db_session, client)
    assert res["action_needed"] is False
    assert "No active signals" in res["reason"]
    assert len(transport.calls) == 0

def test_triage_yes_and_heartbeat(db_session: Session):
    # Seed signals (e.g. overdue blocked task)
    alice = TeamMember(id="U_ALICE", name="Alice", role="Backend Lead", slack_handle="U_ALICE")
    db_session.add(alice)
    
    proj = Project(name="Phoenix", status="active", health="red")
    db_session.add(proj)
    db_session.commit()
    
    task = Task(
        project_id=proj.id,
        title="Deploy Backend",
        status="blocked",
        blockage_reason="Waiting for DB credentials",
        assignee_id="U_ALICE",
        due_date=timeservice.now_ist().date() - timedelta(days=2)
    )
    db_session.add(task)
    db_session.commit()
    
    # Configure fakes
    # 1. Triage flash call returns yes
    triage_response = {
        "candidates": [{
            "content": {
                "parts": [{"text": json.dumps({
                    "action_needed": True,
                    "reason": "Alice's deployment task is blocked and overdue",
                    "focus": ["person:U_ALICE"]
                })}]
            },
            "finishReason": "STOP"
        }],
        "usageMetadata": {}
    }
    
    # 2. Smart action agent turn 1 calls create_followup
    smart_response_1 = {
        "candidates": [{
            "content": {
                "role": "model",
                "parts": [{
                    "functionCall": {
                        "name": "create_followup",
                        "args": {
                            "member_id": "U_ALICE",
                            "question": "Hey Alice, I see the deploy task is blocked. How can we get DB credentials?",
                            "entity_slug": "project:phoenix"
                        }
                    }
                }]
            },
            "finishReason": "STOP"
        }],
        "usageMetadata": {}
    }
    
    # 3. Smart action agent turn 2 calls record_note
    smart_response_2 = {
        "candidates": [{
            "content": {
                "role": "model",
                "parts": [{
                    "functionCall": {
                        "name": "record_note",
                        "args": {
                            "kind": "followup_decision",
                            "content": "Followed up with Alice about database blocker",
                            "subject_ref": "person:U_ALICE"
                        }
                    }
                }]
            },
            "finishReason": "STOP"
        }],
        "usageMetadata": {}
    }
    
    # 4. Smart action agent final answer
    smart_response_3 = {
        "candidates": [{
            "content": {
                "role": "model",
                "parts": [{"text": "I have created a follow-up for Alice Developer."}]
            },
            "finishReason": "STOP"
        }],
        "usageMetadata": {}
    }
    
    transport = FakeTransport([triage_response, smart_response_1, smart_response_2, smart_response_3])
    client = GeminiClient(api_key="fake-key", transport=transport)
    
    # Execute Heartbeat
    res = run_heartbeat(db_session, client)
    assert res["triaged"] is True
    assert res["acted"] is True
    assert "created a follow-up" in res["reply"]
    
    # Verify follow-up was persisted
    f = db_session.scalars(select(Followup).where(Followup.target_member_id == "U_ALICE")).first()
    assert f is not None
    assert "Hey Alice, I see the deploy task is blocked" in f.question
    assert f.entity_slug == "project:phoenix"
    
    # Verify agent note was persisted
    note = db_session.scalars(select(AgentNote).where(AgentNote.subject_ref == "person:U_ALICE")).first()
    assert note is not None
    assert note.kind == "followup_decision"
    assert "Followed up with Alice" in note.content

def test_heartbeat_idempotency(db_session: Session):
    # Seed signals and open followup
    alice = TeamMember(id="U_ALICE", name="Alice", role="Backend Lead", slack_handle="U_ALICE")
    db_session.add(alice)
    db_session.commit()
    
    # First follow-up is already open
    f = Followup(
        entity_slug="project:phoenix",
        target_member_id="U_ALICE",
        question="Hey Alice, status update on DB credentials?",
        due_at=timeservice.now_ist() + timedelta(hours=24),
        status="open",
        created_at=timeservice.now_ist()
    )
    db_session.add(f)
    db_session.commit()
    
    # Run a mock heartbeat that attempts to create the SAME followup again
    triage_response = {
        "candidates": [{
            "content": {
                "parts": [{"text": json.dumps({
                    "action_needed": True,
                    "reason": "Need to follow up",
                    "focus": ["person:U_ALICE"]
                })}]
            },
            "finishReason": "STOP"
        }],
        "usageMetadata": {}
    }
    
    smart_response_1 = {
        "candidates": [{
            "content": {
                "parts": [{
                    "functionCall": {
                        "name": "create_followup",
                        "args": {
                            "member_id": "U_ALICE",
                            "question": "Hey Alice, status update on DB credentials?",
                            "entity_slug": "project:phoenix"
                        }
                    }
                }]
            },
            "finishReason": "STOP"
        }],
        "usageMetadata": {}
    }
    
    smart_response_2 = {
        "candidates": [{
            "content": {
                "parts": [{"text": "I tried to follow up, but it was already tracked."}]
            },
            "finishReason": "STOP"
        }],
        "usageMetadata": {}
    }
    
    transport = FakeTransport([triage_response, smart_response_1, smart_response_2])
    client = GeminiClient(api_key="fake-key", transport=transport)
    
    run_heartbeat(db_session, client)
    
    # Assert exactly 1 Followup exists (none added because of hard duplication guard)
    followups = db_session.scalars(select(Followup).where(Followup.target_member_id == "U_ALICE")).all()
    assert len(followups) == 1

def test_heartbeat_quiet_hours(db_session: Session):
    # Set clock to 23:00 IST (outside work hours 9:00 - 19:00)
    timeservice.set_time(datetime(2026, 7, 13, 23, 0, 0))
    
    # Seed Alice and a signal (e.g., blocked task)
    alice = TeamMember(id="U_ALICE", name="Alice", role="Backend Lead", slack_handle="U_ALICE")
    db_session.add(alice)
    
    proj = Project(name="Phoenix", status="active", health="red")
    db_session.add(proj)
    db_session.commit()
    
    task = Task(
        project_id=proj.id,
        title="Deploy Backend",
        status="blocked",
        blockage_reason="Waiting for DB credentials",
        assignee_id="U_ALICE",
        due_date=timeservice.now_ist().date() - timedelta(days=2)
    )
    db_session.add(task)
    db_session.commit()
    
    triage_response = {
        "candidates": [{
            "content": {
                "parts": [{"text": json.dumps({
                    "action_needed": True,
                    "reason": "Emergency query",
                    "focus": ["person:U_ALICE"]
                })}]
            },
            "finishReason": "STOP"
        }],
        "usageMetadata": {}
    }
    
    # Harry tries to DM Alice on Slack
    smart_response_1 = {
        "candidates": [{
            "content": {
                "parts": [{
                    "functionCall": {
                        "name": "send_slack_dm",
                        "args": {
                            "member_id": "U_ALICE",
                            "text": "URGENT: Are servers down?"
                        }
                    }
                }]
            },
            "finishReason": "STOP"
        }],
        "usageMetadata": {}
    }
    
    smart_response_2 = {
        "candidates": [{
            "content": {
                "parts": [{"text": "DM sent."}]
            },
            "finishReason": "STOP"
        }],
        "usageMetadata": {}
    }
    
    transport = FakeTransport([triage_response, smart_response_1, smart_response_2])
    client = GeminiClient(api_key="fake-key", transport=transport)
    
    run_heartbeat(db_session, client)
    
    # Verify outbound message is in "held" status because of quiet hours
    held_msg = db_session.scalars(select(OutboundQueue)).first()
    assert held_msg is not None
    assert held_msg.status == "held"

def test_lifecycle_time_travel(db_session: Session):
    # Set next_due_at anchors
    now = timeservice.now_ist()
    
    # Seed default scheduler jobs
    seed_default_jobs(db_session)
    
    # Create an open followup to Bob
    manager = TeamMember(id="U_MGR", name="Shivam", role="Manager", slack_handle="U_MGR")
    bob = TeamMember(id="U_BOB", name="Bob", role="Frontend", slack_handle="U_BOB")
    db_session.add_all([manager, bob])
    db_session.commit()
    
    f = Followup(
        entity_slug="project:phoenix",
        target_member_id="U_BOB",
        question="Did you finish schema?",
        due_at=timeservice.now_ist() - timedelta(minutes=5), # slightly in the past
        status="open",
        ping_count=0,
        created_at=timeservice.now_ist() - timedelta(hours=2)
    )
    db_session.add(f)
    db_session.commit()
    
    # 1. Trigger scheduler tick now. It should execute followup_check
    # Since Bob has 0 pings and f.due_at <= now, it should ping Bob once (ping_count -> 1).
    res1 = tick(db_session)
    db_session.refresh(f)
    assert f.ping_count == 1
    assert f.last_ping_at is not None
    
    # Clear the outbound queue for assertions
    db_session.query(OutboundQueue).delete()
    db_session.commit()
    
    # 2. Advance time by 3 Days (72 hours).
    # Since followup_check is seeded as catchup_policy="every" with interval 3600 (1 hour),
    # advancing 72 hours will trigger followup_check virtual ticks sequentially.
    # On the 1st tick of the leap (after 24 hours): Bob hasn't replied, f.ping_count -> 2.
    # On the next tick of the leap (after another 24 hours): Bob still hasn't replied, f.status -> escalated, DMs manager.
    timeservice.advance(72 * 3600)
    
    res2 = tick(db_session)
    db_session.refresh(f)

    # Verify Bob was escalated
    assert f.status == "escalated"

    # Verify manager was notified. The escalation slot replays at a virtual work-hours
    # time, so send_or_hold dispatches immediately (it reads the actual sim clock, by
    # design) and the DM lands in unified_messages, not the held OutboundQueue.
    manager_dm = get_dm_channel_id("U_HARRY", "U_MGR")
    escalation_msgs = db_session.scalars(
        select(UnifiedMessage).where(
            (UnifiedMessage.channel_raw_id == manager_dm) &
            (UnifiedMessage.direction == "outbound")
        )
    ).all()
    assert len(escalation_msgs) > 0
    assert any("asked Bob twice" in m.content for m in escalation_msgs)
