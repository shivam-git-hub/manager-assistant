import pytest
import json
from datetime import datetime, timedelta, date
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.database import Meeting, ActionItem, Task, TeamMember, Project, UnifiedMessage, Leave, ReassignmentSuggestion, Digest
from app.kb.models import Entity, TimelineEntry, AttributedClaim
from app.outbound import OutboundQueue
from app.scheduler import ScheduledJob, tick
from app.agent.gemini_client import GeminiClient
from app import timeservice
from app.kb.workload import run_weekly_digest

# -----------------------------------------------------------------------------
# 1. Setup & Fixtures
# -----------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def set_sim_clock():
    # Freeze sim clock at standard datetime (Sunday July 12, 2026)
    timeservice.set_time(datetime(2026, 7, 12, 10, 0, 0))


# -----------------------------------------------------------------------------
# 2. Test Cases
# -----------------------------------------------------------------------------

def test_leave_creation_generates_suggestions(client, db_session: Session):
    # Setup team members
    manager = TeamMember(id="U_SHIVAM", name="Shivam", role="Manager", slack_handle="U_SHIVAM")
    alice = TeamMember(id="U_ALICE", name="Alice Developer", role="Developer", slack_handle="U_ALICE")
    bob = TeamMember(id="U_BOB", name="Bob Designer", role="Designer", slack_handle="U_BOB")
    charlie = TeamMember(id="U_CHARLIE", name="Charlie QA", role="QA", slack_handle="U_CHARLIE")
    db_session.add_all([manager, alice, bob, charlie])
    db_session.commit()

    # Setup a project and tasks
    p = Project(id=1, name="Phoenix", status="active", health="green")
    db_session.add(p)
    db_session.commit()

    # Alice has 3 tasks:
    # 1. Overdue (due yesterday)
    # 2. Due inside window (due Wednesday, Jul 15)
    # 3. Due outside window (due next week, Jul 22)
    # 4. Already completed inside window (due Jul 15 but completed)
    t1 = Task(id=1, project_id=1, title="Task 1", assignee_id="U_ALICE", due_date=date(2026, 7, 11), status="pending")
    t2 = Task(id=2, project_id=1, title="Task 2", assignee_id="U_ALICE", due_date=date(2026, 7, 15), status="in_progress")
    t3 = Task(id=3, project_id=1, title="Task 3", assignee_id="U_ALICE", due_date=date(2026, 7, 22), status="pending")
    t4 = Task(id=4, project_id=1, title="Task 4", assignee_id="U_ALICE", due_date=date(2026, 7, 15), status="completed")
    
    # Give Bob 1 open task, Charlie has 0 open tasks. Charlie should be chosen for overdue task
    t_bob = Task(id=5, project_id=1, title="Bob Task", assignee_id="U_BOB", status="pending")
    
    db_session.add_all([t1, t2, t3, t4, t_bob])
    db_session.commit()

    # Set up a Mock Gemini Client for rationale
    class MockGeminiClient:
        def chat(self, model, messages, temperature=0.1, json_mode=False):
            return {"content": "Bob has less load and is available."}
            
    from app.agent import gemini_client
    original_client = gemini_client._client_singleton
    gemini_client._client_singleton = MockGeminiClient()

    try:
        # Create Leave for Alice: starts_on Monday Jul 13, ends_on Wednesday Jul 15
        res = client.post("/api/workload/leaves", json={
            "member_id": "U_ALICE",
            "starts_on": "2026-07-13",
            "ends_on": "2026-07-15",
            "reason": "Vacation"
        })
        
        assert res.status_code == 201
        data = res.json()
        assert data["member_id"] == "U_ALICE"
        assert data["starts_on"] == "2026-07-13"
        assert data["ends_on"] == "2026-07-15"

        # Verify suggestions generated in database
        suggestions = db_session.scalars(select(ReassignmentSuggestion)).all()
        # Should generate suggestions for t1 (overdue) and t2 (due Jul 15) but NOT t3 (due Jul 22) or t4 (completed)
        assert len(suggestions) == 2
        
        # Verify suggestions attributes
        s1 = [s for s in suggestions if s.task_id == 1][0]
        s2 = [s for s in suggestions if s.task_id == 2][0]

        # For s1 (overdue, target_date = starts_on = Jul 13):
        # Charlie (0 tasks) is less loaded than Bob (1 task), so Charlie is chosen
        assert s1.to_member_id == "U_CHARLIE"
        assert s1.rationale == "Bob has less load and is available."
        assert s1.status == "suggested"

        # Bob has 1 task. Let's make sure Charlie got t1, so Charlie's load becomes 1 too.
        # Wait, the suggestions are processed one by one, but their active load is read from the db.
        # Since suggestions are NOT auto-applied to tasks yet (tasks are still assigned to Alice in the DB),
        # Charlie still has 0 committed tasks in DB during s2 evaluation, so Charlie should be chosen for both!
        assert s2.to_member_id == "U_CHARLIE"

        # Harry should DM the manager
        dm = db_session.scalars(select(OutboundQueue).where(OutboundQueue.channel_type == "slack")).all()
        assert len(dm) == 1
        payload_dict = json.loads(dm[0].payload)
        assert "Alice Developer is out" in payload_dict["text"]
        assert "2 tasks land in that window" in payload_dict["text"]

    finally:
        gemini_client._client_singleton = original_client


def test_approve_reject_reassignment(client, db_session: Session):
    # Setup team members
    manager = TeamMember(id="U_SHIVAM", name="Shivam", role="Manager", slack_handle="U_SHIVAM")
    alice = TeamMember(id="U_ALICE", name="Alice Developer", role="Developer", slack_handle="U_ALICE")
    bob = TeamMember(id="U_BOB", name="Bob Designer", role="Designer", slack_handle="U_BOB")
    db_session.add_all([manager, alice, bob])
    db_session.commit()

    # Setup project & task
    p = Project(id=1, name="Phoenix", status="active", health="green")
    db_session.add(p)
    db_session.commit()

    task = Task(id=1, project_id=1, title="Task 1", assignee_id="U_ALICE", status="pending")
    db_session.add(task)
    db_session.commit()

    # Create Alice entity
    from app.kb.models import get_or_create_entity
    get_or_create_entity(db_session, slug="person:u_alice", type="person", name="Alice Developer", ref_id="U_ALICE")
    get_or_create_entity(db_session, slug="person:u_bob", type="person", name="Bob Designer", ref_id="U_BOB")
    get_or_create_entity(db_session, slug="project:phoenix", type="project", name="Phoenix", ref_id="1")

    # Create Leave and Suggestion
    leave = Leave(id=1, member_id="U_ALICE", starts_on=date(2026, 7, 13), ends_on=date(2026, 7, 15))
    sugg1 = ReassignmentSuggestion(id=1, leave_id=1, task_id=1, from_member_id="U_ALICE", to_member_id="U_BOB", rationale="Test", status="suggested")
    sugg2 = ReassignmentSuggestion(id=2, leave_id=1, task_id=1, from_member_id="U_ALICE", to_member_id="U_BOB", rationale="Test", status="suggested")
    db_session.add_all([leave, sugg1, sugg2])
    db_session.commit()

    # 1. Approve Suggestion 1
    res = client.post("/api/workload/reassignments/1/approve")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "approved"

    # Verify task assignee updated
    db_session.refresh(task)
    assert task.assignee_id == "U_BOB"

    # Verify 2 DMs sent (one to Alice, one to Bob)
    dms = db_session.scalars(select(OutboundQueue).where(OutboundQueue.channel_type == "slack")).all()
    assert len(dms) == 2
    from app.followups import get_dm_channel_id
    alice_dm_payload = None
    bob_dm_payload = None
    for d in dms:
        p_load = json.loads(d.payload)
        if p_load.get("channel") == get_dm_channel_id("U_HARRY", "U_ALICE"):
            alice_dm_payload = p_load
        elif p_load.get("channel") == get_dm_channel_id("U_HARRY", "U_BOB"):
            bob_dm_payload = p_load
            
    assert alice_dm_payload is not None
    assert bob_dm_payload is not None
    assert "reassigned to Bob" in alice_dm_payload["text"]
    assert "reassigned to you from Alice" in bob_dm_payload["text"]

    # Verify Project timeline entry created
    timeline = db_session.scalars(select(TimelineEntry)).all()
    assert len(timeline) == 1
    assert "Task 'Task 1' reassigned Alice Developer->Bob Designer during leave" in timeline[0].summary

    # Approve again -> 409
    res_conflict = client.post("/api/workload/reassignments/1/approve")
    assert res_conflict.status_code == 409

    # 2. Reject Suggestion 2
    res_reject = client.post("/api/workload/reassignments/2/reject")
    assert res_reject.status_code == 200
    assert res_reject.json()["status"] == "rejected"


def test_rationale_fallback_on_llm_failure(client, db_session: Session):
    # Setup team members
    manager = TeamMember(id="U_SHIVAM", name="Shivam", role="Manager", slack_handle="U_SHIVAM")
    alice = TeamMember(id="U_ALICE", name="Alice Developer", role="Developer", slack_handle="U_ALICE")
    bob = TeamMember(id="U_BOB", name="Bob Designer", role="Designer", slack_handle="U_BOB")
    db_session.add_all([manager, alice, bob])
    db_session.commit()

    # Setup project & task
    p = Project(id=1, name="Phoenix", status="active", health="green")
    db_session.add(p)
    db_session.commit()

    task = Task(id=1, project_id=1, title="Task 1", assignee_id="U_ALICE", due_date=date(2026, 7, 14), status="pending")
    db_session.add(task)
    db_session.commit()

    # Set up bad Mock Gemini Client (throws exception)
    class BadGeminiClient:
        def chat(self, model, messages, temperature=0.1, json_mode=False):
            raise Exception("Rate Limit Exceeded")
            
    from app.agent import gemini_client
    original_client = gemini_client._client_singleton
    gemini_client._client_singleton = BadGeminiClient()

    try:
        # Create Leave for Alice
        res = client.post("/api/workload/leaves", json={
            "member_id": "U_ALICE",
            "starts_on": "2026-07-13",
            "ends_on": "2026-07-15"
        })
        assert res.status_code == 201

        # Check that suggestion was created with fallback rationale
        sugg = db_session.scalars(select(ReassignmentSuggestion)).first()
        assert sugg is not None
        assert sugg.to_member_id == "U_BOB"
        assert sugg.rationale == "Assigned to Bob Designer as they currently have the lightest task queue."

    finally:
        gemini_client._client_singleton = original_client


def test_weekly_digest_job(db_session: Session):
    # Setup team members
    manager = TeamMember(id="U_SHIVAM", name="Shivam Manager", role="Manager", slack_handle="U_SHIVAM")
    alice = TeamMember(id="U_ALICE", name="Alice Developer", role="Developer", slack_handle="U_ALICE")
    bob = TeamMember(id="U_BOB", name="Bob Designer", role="Designer", slack_handle="U_BOB")
    db_session.add_all([manager, alice, bob])
    db_session.commit()

    # Add a blocked task, overdue tasks, conflicts
    p = Project(id=1, name="Phoenix", status="active", health="green")
    db_session.add(p)
    db_session.commit()

    t1 = Task(id=1, project_id=1, title="Task 1", assignee_id="U_ALICE", due_date=date(2026, 7, 10), status="blocked", blockage_reason="Missing assets")
    t2 = Task(id=2, project_id=1, title="Task 2", assignee_id="U_BOB", due_date=date(2026, 7, 11), status="pending") # Overdue
    db_session.add_all([t1, t2])
    db_session.commit()

    # Add conflict
    from app.kb.models import Conflict, AttributedClaim, Entity, get_or_create_entity
    get_or_create_entity(db_session, slug="project:phoenix", type="project", name="Phoenix", ref_id="1")
    c = Conflict(
        id=1,
        entity_id=1,
        claim_a_id=1,
        claim_b_id=2,
        severity="high",
        description="Bob claims completed but Alice claims blocked.",
        status="open",
        detected_at=datetime(2026, 7, 12, 10, 0, 0)
    )
    db_session.add(c)
    db_session.commit()

    # Set up Mock Gemini Client for Digest
    class MockGeminiClient:
        def chat(self, model, messages, temperature=0.2, json_mode=False):
            # Output structured list with team and valid user U_ALICE, plus one hallucinated user U_XYZ
            content = [
                {"audience": "team", "suggestion": "Run Git workshop", "reason": "Due to conflicts"},
                {"audience": "U_ALICE", "suggestion": "Take Python course", "reason": "Because blocked"},
                {"audience": "U_XYZ", "suggestion": "Discarded suggestion", "reason": "Hallucinated ID"}
            ]
            return {"content": json.dumps(content)}
            
    from app.agent import gemini_client
    original_client = gemini_client._client_singleton
    gemini_client._client_singleton = MockGeminiClient()

    try:
        # Run Weekly Digest Job
        digest = run_weekly_digest(db_session)
        assert digest is not None
        # Jul 12 2026 is a Sunday. 
        # now.weekday() for Sunday is 6.
        # monday_date = now - timedelta(days=6) = Monday Jul 6.
        assert digest.week_start == date(2026, 7, 6)

        # Verify database record
        db_session.refresh(digest)
        content_parsed = json.loads(digest.content)
        # Should have 2 suggestions (U_XYZ should be dropped)
        assert len(content_parsed) == 2
        assert content_parsed[0]["audience"] == "team"
        assert content_parsed[1]["audience"] == "U_ALICE"

        # Verify manager teaser Slack DM queued
        dm = db_session.scalars(select(OutboundQueue).where(OutboundQueue.channel_type == "slack")).all()
        assert len(dm) == 1
        assert "I have compiled this week's technical digests" in dm[0].payload

        # Re-run same week should return existing digest and NOT duplicate
        digest2 = run_weekly_digest(db_session)
        assert digest2.id == digest.id
        
    finally:
        gemini_client._client_singleton = original_client
