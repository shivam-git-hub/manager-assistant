import pytest
from datetime import datetime
from sqlalchemy.orm import Session

from app.database import Project, Task, TeamMember, UnifiedMessage
from app.kb.models import Entity, TimelineEntry, Conflict, get_or_create_entity, slugify
from app import timeservice

def test_get_unified_message_by_id(client, db_session: Session):
    # Seed a message
    msg = UnifiedMessage(
        platform_msg_id="test_msg_id_123",
        source="slack",
        direction="inbound",
        sender_raw_id="U_ALICE",
        sender_mapped_name="Alice Developer",
        channel_raw_id="C_GENERAL",
        content="Testing message retrieval",
        timestamp=timeservice.now_ist()
    )
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)
    
    # Retrieval happy path
    res = client.get(f"/api/messages/{msg.id}")
    assert res.status_code == 200
    data = res.json()
    assert data["content"] == "Testing message retrieval"
    assert data["sender_mapped_name"] == "Alice Developer"
    
    # 404 for invalid message ID
    res_404 = client.get("/api/messages/999999")
    assert res_404.status_code == 404


def test_get_dashboard_portfolio(client, db_session: Session):
    # Seed team members
    alice = TeamMember(id="U_ALICE", name="Alice", role="Developer")
    db_session.add(alice)
    
    # Seed 3 projects with different health
    p1 = Project(id=1, name="Phoenix Red", status="active", health="red", health_reasons="Severely blocked.")
    p2 = Project(id=2, name="Phoenix Yellow", status="active", health="yellow", health_reasons="Slightly delayed.")
    p3 = Project(id=3, name="Phoenix Green", status="active", health="green", health_reasons="On track.")
    db_session.add_all([p1, p2, p3])
    db_session.commit()
    
    # Get or create entities
    e1 = get_or_create_entity(db_session, slug=f"project:{slugify(p1.name)}", type="project", name=p1.name, ref_id="1")
    e2 = get_or_create_entity(db_session, slug=f"project:{slugify(p2.name)}", type="project", name=p2.name, ref_id="2")
    e3 = get_or_create_entity(db_session, slug=f"project:{slugify(p3.name)}", type="project", name=p3.name, ref_id="3")
    
    # Setup some compiled truths and timeline entries to establish activity times
    e1.compiled_truth = "This is Phoenix Red truth. It is heavily documented."
    e2.compiled_truth = "This is Phoenix Yellow truth."
    
    # Activity timestamps: Red has oldest, Yellow has newest
    t1 = TimelineEntry(entity_id=e1.id, happened_at=datetime(2026, 7, 12, 10, 0, 0), summary="Red starts")
    t2 = TimelineEntry(entity_id=e2.id, happened_at=datetime(2026, 7, 12, 12, 0, 0), summary="Yellow starts")
    db_session.add_all([t1, t2])
    
    # Seed some tasks
    task1 = Task(project_id=1, title="Red Task 1", status="blocked", assignee_id="U_ALICE")
    task2 = Task(project_id=2, title="Yellow Task 1", status="in_progress", assignee_id="U_ALICE")
    db_session.add_all([task1, task2])
    db_session.commit()
    
    # Call portfolio endpoint
    res = client.get("/api/dashboard/portfolio")
    assert res.status_code == 200
    data = res.json()
    
    # Must have 3 projects
    assert len(data) == 3
    
    # Sorting order must be RED (Phoenix Red), then YELLOW (Phoenix Yellow), then GREEN (Phoenix Green)
    assert data[0]["name"] == "Phoenix Red"
    assert data[0]["health"] == "red"
    assert data[0]["task_counts"]["blocked"] == 1
    assert data[0]["compiled_truth_teaser"] == "This is Phoenix Red truth."
    
    assert data[1]["name"] == "Phoenix Yellow"
    assert data[1]["health"] == "yellow"
    assert data[1]["task_counts"]["in_progress"] == 1
    assert data[1]["compiled_truth_teaser"] == "This is Phoenix Yellow truth."
    
    assert data[2]["name"] == "Phoenix Green"
    assert data[2]["health"] == "green"
    assert data[2]["compiled_truth_teaser"] is None


def test_get_dashboard_html(client):
    # Test directory serving /dashboard/ or /dashboard/index.html
    res = client.get("/dashboard/")
    assert res.status_code == 200
    assert "Harry — Executive Project Tracker" in res.text
    
    # Check that CSS files are served
    css_res = client.get("/dashboard/css/theme.css")
    assert css_res.status_code == 200
    assert "theme.css" in css_res.text or "--bg-paper" in css_res.text
