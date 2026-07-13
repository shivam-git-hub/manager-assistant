import os
import json
import logging
import uuid
from datetime import datetime, date, timedelta
from sqlalchemy import select, delete
from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends, HTTPException, status

from app.database import Base, get_db, SessionLocal, TeamMember, Project, Task, UnifiedMessage, ChatMessage, Leave, ReassignmentSuggestion, Digest, Meeting, ActionItem
from app.kb.models import Entity, TimelineEntry, AttributedClaim, Conflict, get_or_create_entity, slugify
from app.kb.extraction import extract_from_message
from app.kb.synthesis import run_dream_cycle
from app.kb.meetings import ingest_mom, MomRequest
from app.brief import Brief
from app.followups import Followup, get_dm_channel_id
from app.outbound import OutboundQueue
from app.scheduler import ScheduledJob, seed_default_jobs, get_next_monday_9_30
from app import timeservice

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/demo", tags=["Demo Seeding"])

# -----------------------------------------------------------------------------
# Seeding Logic
# -----------------------------------------------------------------------------

def clear_database(db: Session):
    """
    Completely resets the database, clearing all dynamic status fields, KB logs,
    and outbounds, preparing the environment for a fresh presentation seed.
    """
    db.execute(delete(UnifiedMessage))
    db.execute(delete(Task))
    db.execute(delete(Project))
    db.execute(delete(TimelineEntry))
    db.execute(delete(AttributedClaim))
    db.execute(delete(Conflict))
    db.execute(delete(Entity))
    db.execute(delete(Leave))
    db.execute(delete(ReassignmentSuggestion))
    db.execute(delete(Digest))
    db.execute(delete(Meeting))
    db.execute(delete(ActionItem))
    db.execute(delete(ChatMessage))
    db.execute(delete(Brief))
    db.execute(delete(Followup))
    db.execute(delete(OutboundQueue))
    db.execute(delete(ScheduledJob))
    db.execute(delete(TeamMember))
    db.commit()

def ingest_slack(db: Session, channel: str, user_id: str, text: str, sim_time: datetime) -> UnifiedMessage:
    timeservice.set_time(sim_time)
    
    sender_member = db.scalars(select(TeamMember).where(TeamMember.id == user_id)).first()
    sender_name = sender_member.name if sender_member else None
    
    msg_id = f"slack_seed_{uuid.uuid4().hex[:12]}"
    new_msg = UnifiedMessage(
        platform_msg_id=msg_id,
        source="slack",
        direction="inbound",
        sender_raw_id=user_id,
        sender_mapped_name=sender_name,
        channel_raw_id=channel,
        thread_id=None,
        content=text,
        timestamp=sim_time,
        is_processed=False
    )
    db.add(new_msg)
    db.commit()
    db.refresh(new_msg)
    
    extract_from_message(db, new_msg)
    return new_msg

def ingest_outlook(db: Session, sender_email: str, to_recipient: str, subject: str, text: str, sim_time: datetime) -> UnifiedMessage:
    timeservice.set_time(sim_time)
    
    sender_member = db.scalars(select(TeamMember).where(TeamMember.outlook_email == sender_email)).first()
    sender_name = sender_member.name if sender_member else None
    
    msg_id = f"outlook_seed_{uuid.uuid4().hex[:12]}"
    new_msg = UnifiedMessage(
        platform_msg_id=msg_id,
        source="outlook",
        direction="inbound",
        sender_raw_id=sender_email,
        sender_mapped_name=sender_name,
        channel_raw_id=to_recipient,
        thread_id=None,
        subject=subject,
        content=text,
        timestamp=sim_time,
        is_processed=False
    )
    db.add(new_msg)
    db.commit()
    db.refresh(new_msg)
    
    extract_from_message(db, new_msg)
    return new_msg

def run_seeding(db: Session) -> dict:
    logger.info("Initializing demo seed scenario...")
    
    # 1. Reset everything
    clear_database(db)
    
    # Set the time to seed start
    start_time = datetime(2026, 7, 2, 10, 0, 0)
    timeservice.set_time(start_time)

    # 2. Add Builtin Jobs
    seed_default_jobs(db)

    # 3. Add Cast Members
    shivam = TeamMember(
        id="U_SHIVAM",
        name="Shivam",
        role="General Manager",
        slack_handle="U_SHIVAM",
        outlook_email="shivam@company.com"
    )
    alice = TeamMember(
        id="U_ALICE",
        name="Alice Sharma",
        role="Backend Lead",
        slack_handle="U_ALICE",
        outlook_email="alice.sharma@company.com"
    )
    bob = TeamMember(
        id="U_BOB",
        name="Bob Verma",
        role="Product Engineer",
        slack_handle="U_BOB",
        outlook_email="bob.verma@company.com"
    )
    carol = TeamMember(
        id="U_CAROL",
        name="Carol Iyer",
        role="Frontend Engineer",
        slack_handle="U_CAROL",
        outlook_email="carol.iyer@company.com"
    )
    harry = TeamMember(
        id="U_HARRY",
        name="Harry",
        role="AI Assistant",
        slack_handle="U_HARRY",
        outlook_email="harry.assistant@company.com"
    )
    db.add_all([shivam, alice, bob, carol, harry])
    db.commit()

    # Backfill Entity pages for team members
    for m in [shivam, alice, bob, carol, harry]:
        get_or_create_entity(db, slug=f"person:{m.id}", type="person", name=m.name, ref_id=m.id)

    # 4. Create Projects
    phoenix = Project(
        id=1,
        name="Phoenix",
        description="Payment gateway revamp and Stripe/UPI migrations.",
        manager_id="U_SHIVAM",
        status="active",
        health="green"
    )
    atlas = Project(
        id=2,
        name="Atlas",
        description="Internal analytics portal and log aggregation.",
        manager_id="U_SHIVAM",
        status="active",
        health="green"
    )
    db.add_all([phoenix, atlas])
    db.commit()

    # Ensure Entities exist for projects
    get_or_create_entity(db, slug="project:phoenix", type="project", name="Phoenix", ref_id="1")
    get_or_create_entity(db, slug="project:atlas", type="project", name="Atlas", ref_id="2")

    # -------------------------------------------------------------------------
    # Day 0: Thursday, 2026-07-02 (Kickoff & Setup)
    # -------------------------------------------------------------------------
    t_day0 = datetime(2026, 7, 2, 10, 0, 0)
    ingest_slack(db, "C_PHOENIX", "U_CAROL", "Alice, did we finalize the payment gateway design spec? I need to start on the checkout UI mocks.", t_day0)
    ingest_slack(db, "C_PHOENIX", "U_ALICE", "Yes, Carol. The design spec is finalized. The new flow uses Stripe for credit cards and UPI. Here is the link to the design doc: https://wiki.company.com/phoenix-spec", t_day0 + timedelta(minutes=5))
    ingest_slack(db, "C_PHOENIX", "U_SHIVAM", "Excellent. Alice and Bob, let's get the schema defined next week so we can review before development.", t_day0 + timedelta(minutes=10))
    
    ingest_slack(db, "C_ATLAS", "U_BOB", "Starting structure of the Atlas reporting engine today. I will use Clickhouse for log aggregation.", t_day0 + timedelta(minutes=15))
    ingest_slack(db, "C_ATLAS", "U_SHIVAM", "Sounds good Bob. Keep it lightweight and efficient.", t_day0 + timedelta(minutes=20))
    
    # Process Day 0 dream
    timeservice.set_time(t_day0 + timedelta(hours=6))
    run_dream_cycle(db)

    # -------------------------------------------------------------------------
    # Day 3: Sunday, 2026-07-05 (Atlas Progress)
    # -------------------------------------------------------------------------
    t_day3 = datetime(2026, 7, 5, 11, 0, 0)
    ingest_slack(db, "C_ATLAS", "U_BOB", "Drafted analytics dashboard layout. Carol, let know if you have feedback on the charts.", t_day3)
    ingest_slack(db, "C_ATLAS", "U_CAROL", "Layout looks very clean Bob. Simple line charts are perfect for the summaries.", t_day3 + timedelta(minutes=15))
    
    timeservice.set_time(t_day3 + timedelta(hours=2))
    run_dream_cycle(db)

    # -------------------------------------------------------------------------
    # Day 5: Tuesday, 2026-07-07 (Phoenix Meeting Review & Action Seeding)
    # -------------------------------------------------------------------------
    t_day5 = datetime(2026, 7, 7, 10, 0, 0)
    timeservice.set_time(t_day5)
    
    # Create the design review meeting
    meeting = Meeting(
        title="Phoenix Kickoff & Design Review",
        starts_at=t_day5,
        ends_at=t_day5 + timedelta(hours=1),
        attendees=json.dumps(["U_SHIVAM", "U_ALICE", "U_BOB", "U_CAROL"]),
        project_id=1,
        status="completed"
    )
    db.add(meeting)
    db.commit()
    db.refresh(meeting)

    # Ingest minutes of meeting to generate actions via propagation pipeline
    mom_text = (
        "Meeting: Phoenix Kickoff & Design Review\n"
        "Date: 2026-07-07 10:00 - 11:00 IST\n"
        "Attendees: Shivam, Alice Sharma, Bob Verma, Carol Iyer\n\n"
        "Decisions:\n"
        "- We will proceed with the unified payment schema migration.\n"
        "- Alice is in charge of backend architecture and migrations.\n\n"
        "Action Items:\n"
        "- Carol Iyer to draft checkout UI mocks by 2026-07-10.\n"
        "- Bob Verma to define API schema contracts by 2026-07-12.\n"
        "- Alice Sharma to review schema migration doc by 2026-07-15."
    )
    
    # Call standard ingest MOM pipeline
    ingest_mom(id=meeting.id, payload=MomRequest(text=mom_text), db=db)
    
    # Process Day 5 dream
    timeservice.set_time(t_day5 + timedelta(hours=4))
    run_dream_cycle(db)

    # -------------------------------------------------------------------------
    # Day 6: Wednesday, 2026-07-08 (Atlas Staging Live)
    # -------------------------------------------------------------------------
    t_day6 = datetime(2026, 7, 8, 14, 0, 0)
    ingest_slack(db, "C_ATLAS", "U_BOB", "Staging analytics portal is now live at http://atlas-staging.company.com. Carol, we are ready to integrate user tracking.", t_day6)
    
    timeservice.set_time(t_day6 + timedelta(hours=3))
    run_dream_cycle(db)

    # -------------------------------------------------------------------------
    # Day 8: Friday, 2026-07-10 (Carol Blocked on Staging Bug)
    # -------------------------------------------------------------------------
    t_day8 = datetime(2026, 7, 10, 11, 0, 0)
    
    # Carol finishes UI mocks but reports 500 error block
    ingest_slack(db, "C_PHOENIX", "U_CAROL", "Finished drafting the first set of payment checkout UI mocks in Figma. But staging checkout flow is throwing 500 errors on load. I am blocked on Carol's checkout mocks review.", t_day8)
    ingest_slack(db, "C_PHOENIX", "U_ALICE", "Ah, looking into it. Looks like database migration issue on our checkout table.", t_day8 + timedelta(minutes=20))
    
    # Mark Carol's draft task as in_progress (simulate status update)
    carol_task = db.scalars(select(Task).where(Task.assignee_id == "U_CAROL")).first()
    if carol_task:
        carol_task.status = "in_progress"
        db.commit()

    # Process Day 8 dream
    timeservice.set_time(t_day8 + timedelta(hours=4))
    run_dream_cycle(db)

    # -------------------------------------------------------------------------
    # Day 9: Saturday, 2026-07-11 (Atlas Alpha Launch)
    # -------------------------------------------------------------------------
    t_day9 = datetime(2026, 7, 11, 10, 0, 0)
    ingest_slack(db, "C_ATLAS", "U_BOB", "Atlas user tracking integrated. Carol, we are ready to launch internal alpha.", t_day9)
    ingest_slack(db, "C_CAROL", "U_CAROL", "Launched alpha, team is checking clickstream data now. Looks highly accurate.", t_day9 + timedelta(minutes=40))

    # -------------------------------------------------------------------------
    # Day 10: Sunday, 2026-07-12 (Bug Resolved & Task Completed)
    # -------------------------------------------------------------------------
    t_day10 = datetime(2026, 7, 12, 10, 0, 0)
    ingest_slack(db, "C_PHOENIX", "U_ALICE", "Staging database migration fixed. Checkout flow should work now.", t_day10)
    ingest_slack(db, "C_PHOENIX", "U_CAROL", "Awesome, staging is green! Tested and confirmed, checkout flow works. Marking UI mocks task completed.", t_day10 + timedelta(minutes=15))

    # Mark Carol's task as completed
    if carol_task:
        carol_task.status = "completed"
        carol_task.completed_at = t_day10 + timedelta(minutes=15)
        db.commit()

    # Mark Bob's contract task as completed too since Day 10 is due
    bob_task = db.scalars(select(Task).where(Task.assignee_id == "U_BOB")).first()
    if bob_task:
        bob_task.status = "completed"
        bob_task.completed_at = t_day10
        db.commit()

    # Process Day 10 dream
    timeservice.set_time(t_day10 + timedelta(hours=4))
    run_dream_cycle(db)

    # -------------------------------------------------------------------------
    # Day 12: Tuesday, 2026-07-14 (Atlas Feedback)
    # -------------------------------------------------------------------------
    t_day12 = datetime(2026, 7, 14, 15, 0, 0)
    ingest_slack(db, "C_ATLAS", "U_BOB", "Alpha feedback was extremely positive. We are on track for public release of Atlas next week.", t_day12)
    
    timeservice.set_time(t_day12 + timedelta(hours=3))
    run_dream_cycle(db)

    # -------------------------------------------------------------------------
    # Day 14: Wednesday, 2026-07-15 (The Deadlock Spark)
    # -------------------------------------------------------------------------
    t_day14 = datetime(2026, 7, 15, 15, 0, 0)
    
    # 1. Bob claims he sent the document in Slack
    ingest_slack(db, "C_PHOENIX", "U_BOB", "Sent the schema doc to Alice on Monday, waiting on her review. We are on track.", t_day14)
    
    # 2. Alice claims next morning in Outlook that she never received it
    t_day14_email = datetime(2026, 7, 15, 9, 15, 0)
    ingest_outlook(db, "alice.sharma@company.com", "harry.assistant@company.com", "Phoenix DB review blocked", "Hi Shivam, I still haven't received any schema doc from Bob. Phoenix migration is currently blocked on it.", t_day14_email)

    # Alice's task is overdue/blocked (due Jul 15)
    alice_task = db.scalars(select(Task).where(Task.assignee_id == "U_ALICE")).first()
    if alice_task:
        alice_task.status = "blocked"
        alice_task.blockage_reason = "Overdue schema review: Alice says never received Bob's schema document."
        db.commit()

    # Run Day 14 dream cycle -> This will trigger claims conflict organically between Bob's Slack assertion and Alice's Email assertion
    timeservice.set_time(t_day14 + timedelta(hours=5))
    run_dream_cycle(db)

    # -------------------------------------------------------------------------
    # Day 15: Thursday, 2026-07-15 (Carol's Late Night Safari Bug Block)
    # -------------------------------------------------------------------------
    t_day15 = datetime(2026, 7, 15, 23, 5, 0)
    
    # Carol reports staging bug in the middle of the night
    ingest_slack(db, "C_PHOENIX", "U_CAROL", "Seeing checkout integration bug on Safari browser. It works fine on Chrome, but checkout button is unresponsive on Safari.", t_day15)

    # Setup followups and quiet hours checks
    manager_dm = get_dm_channel_id("U_HARRY", "U_SHIVAM")
    held_alert = OutboundQueue(
        channel_type="slack",
        payload=json.dumps({"channel": manager_dm, "text": "ALERT: Carol reported a checkout bug on Safari browser at 11:05 PM. Holding ping until working hours."}),
        status="held",
        created_at=t_day15,
        scheduled_release_at=datetime(2026, 7, 16, 9, 0, 0) # Next morning 9:00
    )
    db.add(held_alert)
    db.commit()

    # -------------------------------------------------------------------------
    # Day 16: Thursday, 2026-07-16 (Demo Starting Anchor State)
    # -------------------------------------------------------------------------
    t_day16 = datetime(2026, 7, 16, 10, 0, 0)
    timeservice.set_time(t_day16)

    # Schedule Go/No-Go meeting for tomorrow
    future_meeting = Meeting(
        title="Phoenix Go/No-Go Alignment",
        starts_at=datetime(2026, 7, 17, 11, 0, 0),
        ends_at=datetime(2026, 7, 17, 12, 0, 0),
        attendees=json.dumps(["U_SHIVAM", "U_ALICE", "U_BOB"]),
        project_id=1,
        status="scheduled"
    )
    db.add(future_meeting)
    db.commit()

    # Trigger final dream cycle to establish starting presentation state
    run_dream_cycle(db)

    # Build morning brief
    from app.brief import generate_brief_sync
    generate_brief_sync(db, date(2026, 7, 16))

    # Compute and print checklist
    checklist = check_demo_readiness(db)
    print_checklist(checklist)
    
    return checklist

def check_demo_readiness(db: Session) -> dict:
    checklist = {}
    
    # 1. Open conflict exists
    from app.kb.models import Conflict
    open_conflicts = db.scalars(select(Conflict).where(Conflict.status == "open")).all()
    checklist["open_conflict_exists"] = len(open_conflicts) > 0
    
    # 2. Phoenix health is Yellow or Red
    phoenix = db.scalars(select(Project).where(Project.name.ilike("%Phoenix%"))).first()
    checklist["phoenix_degraded"] = phoenix is not None and phoenix.health in ("yellow", "red")
    
    # 3. Held pings in queue
    held_pings = db.scalars(select(OutboundQueue).where(OutboundQueue.status == "held")).all()
    checklist["held_pings_exist"] = len(held_pings) > 0
    
    # 4. N briefs exist
    briefs = db.scalars(select(Brief)).all()
    checklist["briefs_exist"] = len(briefs) > 0
    
    # 5. Next meeting scheduled
    future_meetings = db.scalars(select(Meeting).where(Meeting.status == "scheduled")).all()
    checklist["next_meeting_scheduled"] = len(future_meetings) > 0
    
    return checklist

def print_checklist(checklist: dict):
    print("\n" + "="*50)
    print("DEMO SEED READINESS CHECKLIST")
    print("="*50)
    for key, val in checklist.items():
        symbol = "✓" if val else "✗"
        print(f" {symbol} {key.replace('_', ' ').title()}")
    print("="*50 + "\n")

# -----------------------------------------------------------------------------
# REST Endpoint Integration
# -----------------------------------------------------------------------------

@router.post("/seed")
def seed_demo_endpoint(payload: dict, db: Session = Depends(get_db)):
    if not payload.get("confirm"):
        raise HTTPException(status_code=400, detail="Must provide 'confirm': true")
    try:
        results = run_seeding(db)
        return {"status": "ok", "checklist": results}
    except Exception as e:
        logger.exception("Error running demo seed")
        raise HTTPException(status_code=500, detail=f"Error seeding demo: {e}")

# -----------------------------------------------------------------------------
# CLI Entrypoint
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db = SessionLocal()
    try:
        run_seeding(db)
        print("Demo seeding completed successfully.")
    except Exception as e:
        logger.exception(f"Fatal error seeding demo: {e}")
        print(f"Error seeding demo: {e}")
    finally:
        db.close()
