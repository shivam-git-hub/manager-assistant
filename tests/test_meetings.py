import pytest
import json
from datetime import datetime, timedelta, date
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.database import Meeting, ActionItem, Task, TeamMember, Project, UnifiedMessage
from app.kb.models import Entity, TimelineEntry, AttributedClaim
from app.outbound import OutboundQueue
from app.scheduler import ScheduledJob
from app.agent.gemini_client import GeminiClient
from app import timeservice

# -----------------------------------------------------------------------------
# 1. Setup & Fixtures
# -----------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def set_sim_clock():
    # Freeze sim clock at standard datetime
    timeservice.set_time(datetime(2026, 7, 12, 10, 0, 0))


# -----------------------------------------------------------------------------
# 2. Test Cases
# -----------------------------------------------------------------------------

def test_create_meeting_schedules_brief_job(client, db_session: Session):
    # Setup a project
    p = Project(id=1, name="Phoenix", status="active", health="green")
    db_session.add(p)
    db_session.commit()
    
    # Happy Path: starts_at is tomorrow (future)
    future_time = datetime(2026, 7, 13, 10, 0, 0) # Tomorrow
    res = client.post("/api/meetings", json={
        "title": "Phoenix Alignment",
        "starts_at": future_time.strftime("%Y-%m-%d %H:%M:%S"),
        "attendees": ["U_ALICE", "U_BOB"],
        "project_id": 1
    })
    
    assert res.status_code == 201
    data = res.json()
    meeting_id = data["id"]
    assert data["title"] == "Phoenix Alignment"
    assert data["attendees"] == ["U_ALICE", "U_BOB"]
    assert data["project_id"] == 1
    assert data["status"] == "scheduled"
    
    # Meeting Entity should exist
    entity = db_session.scalars(select(Entity).where(Entity.slug == f"meeting:{meeting_id}")).first()
    assert entity is not None
    assert entity.name == "Phoenix Alignment"
    assert entity.type == "meeting"
    
    # One-shot brief job should be scheduled tomorrow at starts_at - 30m = 09:30 AM
    job = db_session.scalars(
        select(ScheduledJob)
        .where(ScheduledJob.job_type == f"pre_meeting_brief:{meeting_id}")
    ).first()
    
    assert job is not None
    assert job.next_due_at == datetime(2026, 7, 13, 9, 30, 0)
    assert job.enabled is True
    assert job.interval_seconds is None # One-shot
    
    # Past Meeting check: starts_at was yesterday (past)
    past_time = datetime(2026, 7, 11, 10, 0, 0)
    res_past = client.post("/api/meetings", json={
        "title": "Phoenix Retrospective",
        "starts_at": past_time.strftime("%Y-%m-%d %H:%M:%S"),
        "attendees": ["U_ALICE"],
        "project_id": 1
    })
    
    assert res_past.status_code == 201
    past_id = res_past.json()["id"]
    
    # Past meeting should NOT schedule a briefing job
    job_past = db_session.scalars(
        select(ScheduledJob)
        .where(ScheduledJob.job_type == f"pre_meeting_brief:{past_id}")
    ).first()
    assert job_past is None


def test_mom_ingestion_happy_path(client, db_session: Session):
    # Setup team, project, and meeting
    manager = TeamMember(id="U_SHIVAM", name="Shivam", role="Product Manager")
    alice = TeamMember(id="U_ALICE", name="Alice", role="Developer")
    bob = TeamMember(id="U_BOB", name="Bob", role="Developer")
    p = Project(id=1, name="Phoenix", status="active", health="green")
    
    db_session.add_all([manager, alice, bob, p])
    db_session.commit()
    
    # Add project entity
    from app.kb.models import get_or_create_entity
    get_or_create_entity(db_session, slug="project:phoenix", type="project", name="Phoenix", ref_id="1")
    
    meet_res = client.post("/api/meetings", json={
        "title": "Phoenix Alignment",
        "starts_at": "2026-07-13 10:00:00",
        "attendees": ["U_ALICE", "U_BOB"],
        "project_id": 1
    })
    meeting_id = meet_res.json()["id"]
    
    # Mock LLM Extracted Response
    mock_extracted = {
        "summary": "Database schema was approved and actions assigned.",
        "decisions": [
            "We will adopt Bob's MySQL migrations model."
        ],
        "action_items": [
            {
                "description": "Send MySQL migrations schema to Alice.",
                "owner_member_id": "U_BOB",
                "due_date": "2026-07-15",
                "project_slug": "project:phoenix"
            }
        ],
        "project_slugs": ["project:phoenix"]
    }
    
    # Mock the GeminiClient singleton
    from app.agent import gemini_client
    original_client = gemini_client._client_singleton
    
    mock_client = GeminiClient(
        api_key="fake-key",
        transport=lambda url, payload: {"candidates": [{"content": {"parts": [{"text": json.dumps(mock_extracted)}]}}]}
    )
    gemini_client._client_singleton = mock_client
    
    try:
        mom_text = "Bob showed the MySQL migrations schema. We approved adopting it. Bob will send schema to Alice by Wednesday."
        res = client.post(f"/api/meetings/{meeting_id}/mom", json={"text": mom_text})
        
        assert res.status_code == 200
        data = res.json()
        assert data["meeting"]["status"] == "completed"
        assert data["meeting"]["mom_raw"] == mom_text
        assert len(data["action_items"]) == 1
        
        # Verify ActionItem & Task exist in database
        ai_row = db_session.scalars(select(ActionItem).where(ActionItem.meeting_id == meeting_id)).first()
        assert ai_row is not None
        assert ai_row.description == "Send MySQL migrations schema to Alice."
        assert ai_row.owner_member_id == "U_BOB"
        assert ai_row.due_date == date(2026, 7, 15)
        
        task_row = db_session.get(Task, ai_row.task_id)
        assert task_row is not None
        assert task_row.project_id == 1
        assert task_row.title == ai_row.description
        assert task_row.assignee_id == "U_BOB"
        
        # Verify timeline entry on project page & claim propagated
        p_entity = db_session.scalars(select(Entity).where(Entity.slug == "project:phoenix")).first()
        assert p_entity.truth_updated_at is None  # Marked dirty
        
        t_entry = db_session.scalars(
            select(TimelineEntry)
            .where(TimelineEntry.entity_id == p_entity.id)
            .order_by(TimelineEntry.id.desc())
        ).first()
        assert t_entry is not None
        assert t_entry.summary == "Decided in meeting 'Phoenix Alignment'"
        assert t_entry.detail == "We will adopt Bob's MySQL migrations model."
        assert t_entry.source_message_id == data["meeting"]["mom_message_id"]
        
        claim_row = db_session.scalars(
            select(AttributedClaim)
            .where(AttributedClaim.entity_id == p_entity.id)
            .order_by(AttributedClaim.id.desc())
        ).first()
        assert claim_row is not None
        assert "Shivam decided: We will adopt Bob's MySQL migrations model." in claim_row.claim
        assert claim_row.holder == "U_SHIVAM"
        assert claim_row.weight == 1.0
        
        # Verify Slack DM to owner was queued
        queue_row = db_session.scalars(
            select(OutboundQueue)
            .where(OutboundQueue.channel_type == "slack")
            .order_by(OutboundQueue.id.desc())
        ).first()
        assert queue_row is not None
        assert "From meeting 'Phoenix Alignment': Send MySQL migrations schema to Alice." in queue_row.payload
        
    finally:
        gemini_client._client_singleton = original_client


def test_mom_ingestion_validations_graceful_fallbacks(client, db_session: Session):
    # Setup project and meeting (no team members)
    p = Project(id=1, name="Phoenix", status="active", health="green")
    db_session.add(p)
    db_session.commit()
    
    meet_res = client.post("/api/meetings", json={
        "title": "Phoenix Alignment",
        "starts_at": "2026-07-13 10:00:00",
        "attendees": ["U_ALICE"],
        "project_id": 1
    })
    meeting_id = meet_res.json()["id"]
    
    # 1. Invalid owner id: mock returns extraction pointing to 'U_UNKNOWN_USER'
    mock_extracted = {
        "summary": "Meeting with an unknown attendee.",
        "decisions": ["Approved."],
        "action_items": [
            {
                "description": "Send database schema",
                "owner_member_id": "U_UNKNOWN_USER", # NOT in DB
                "project_slug": "project:phoenix"
            }
        ],
        "project_slugs": ["project:phoenix"]
    }
    
    from app.agent import gemini_client
    original_client = gemini_client._client_singleton
    
    mock_client = GeminiClient(
        api_key="fake-key",
        transport=lambda url, payload: {"candidates": [{"content": {"parts": [{"text": json.dumps(mock_extracted)}]}}]}
    )
    gemini_client._client_singleton = mock_client
    
    try:
        res = client.post(f"/api/meetings/{meeting_id}/mom", json={"text": "Unknown user test"})
        assert res.status_code == 200
        # No action items should be saved since 'U_UNKNOWN_USER' was invalid and dropped
        assert len(res.json()["action_items"]) == 0
        
        # 2. Malformed JSON fallback check
        bad_client = GeminiClient(
            api_key="fake-key",
            transport=lambda url, payload: {"candidates": [{"content": {"parts": [{"text": "BOGUS RAW TEXT NOT JSON"}]}}]}
        )
        gemini_client._client_singleton = bad_client
        
        res_bad = client.post(f"/api/meetings/{meeting_id}/mom", json={"text": "Bad JSON test"})
        assert res_bad.status_code == 200
        # Should complete and save mom_raw without crashing, returning default fallbacks
        data_bad = res_bad.json()
        assert data_bad["meeting"]["status"] == "completed"
        assert data_bad["meeting"]["mom_raw"] == "Bad JSON test"
        
    finally:
        gemini_client._client_singleton = original_client


def test_pre_meeting_briefing_handler(db_session: Session):
    # Setup team member (Bob), project, meeting, and followup
    bob = TeamMember(id="U_BOB", name="Bob", role="Dev")
    manager = TeamMember(id="U_SHIVAM", name="Shivam", role="Manager")
    p = Project(id=1, name="Phoenix", status="active", health="yellow", health_reasons="Alice is out.")
    
    db_session.add_all([bob, manager, p])
    db_session.commit()
    
    # Entities
    from app.kb.models import get_or_create_entity
    get_or_create_entity(db_session, slug="project:phoenix", type="project", name="Phoenix", ref_id="1")
    
    meeting = Meeting(
        title="Phoenix Alignment Check",
        starts_at=datetime(2026, 7, 13, 10, 0, 0),
        attendees=json.dumps(["U_BOB"]),
        project_id=1,
        status="scheduled"
    )
    db_session.add(meeting)
    db_session.commit()
    db_session.refresh(meeting)
    
    get_or_create_entity(db_session, slug=f"meeting:{meeting.id}", type="meeting", name=meeting.title, ref_id=str(meeting.id))
    
    from app.followups import Followup
    fup = Followup(
        entity_slug="project:phoenix",
        target_member_id="U_BOB",
        question="Did you re-send the schema migrations?",
        due_at=timeservice.now_ist() - timedelta(hours=1),
        status="open",
        created_by="harry"
    )
    db_session.add(fup)
    db_session.commit()
    
    # Mock LLM Synthesis
    mock_resp = {
        "candidates": [{
            "content": {
                "parts": [{
                    "text": "**Pre-meeting Brief**: Phoenix Project is in **yellow** health. Bob has 1 outstanding followup."
                }]
            }
        }]
    }
    
    from app.agent import gemini_client
    original_client = gemini_client._client_singleton
    
    mock_client = GeminiClient(
        api_key="fake-key",
        transport=lambda url, payload: mock_resp
    )
    gemini_client._client_singleton = mock_client
    
    try:
        from app.kb.meetings import pre_meeting_brief_handler
        pre_meeting_brief_handler(db_session, f"pre_meeting_brief:{meeting.id}")
        
        # Verify briefing is saved as timeline entry on the meeting Entity
        m_entity = db_session.scalars(select(Entity).where(Entity.slug == f"meeting:{meeting.id}")).first()
        assert m_entity is not None
        
        t_entry = db_session.scalars(
            select(TimelineEntry)
            .where(TimelineEntry.entity_id == m_entity.id)
            .order_by(TimelineEntry.id.desc())
        ).first()
        assert t_entry is not None
        assert t_entry.summary == "Pre-meeting briefing compiled"
        assert "**Pre-meeting Brief**: Phoenix Project" in t_entry.detail
        
        # Verify briefing Slack DM was queued for Shivam (manager)
        queue_row = db_session.scalars(
            select(OutboundQueue)
            .where(OutboundQueue.channel_type == "slack")
            .order_by(OutboundQueue.id.desc())
        ).first()
        assert queue_row is not None
        assert "**Pre-meeting Brief**: Phoenix Project" in queue_row.payload
        
    finally:
        gemini_client._client_singleton = original_client


def test_calendar_range_query(client, db_session: Session):
    # Seed three meetings on different dates
    m1 = Meeting(title="Meeting A", starts_at=datetime(2026, 7, 10, 10, 0, 0), attendees="[]")
    m2 = Meeting(title="Meeting B", starts_at=datetime(2026, 7, 12, 10, 0, 0), attendees="[]")
    m3 = Meeting(title="Meeting C", starts_at=datetime(2026, 7, 15, 10, 0, 0), attendees="[]")
    db_session.add_all([m1, m2, m3])
    db_session.commit()
    
    # Query range: July 11th to July 13th (should only return B)
    res = client.get("/api/meetings?from=2026-07-11&to=2026-07-13")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    assert data[0]["title"] == "Meeting B"
