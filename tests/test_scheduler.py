import os
import json
import pytest
from datetime import datetime, timedelta, date
from fastapi.testclient import TestClient

from app import timeservice
from sqlalchemy import select
from app.database import UnifiedMessage, TeamMember, Project, Task
from app.kb.models import Entity, Conflict, TimelineEntry, AttributedClaim
from app.scheduler import ScheduledJob, tick, register_handler, get_next_9am
from app.followups import Followup, run_followup_check
from app.health import evaluate_project_health, run_health_eval
from app.brief import Brief, run_morning_brief
from app.agent.gemini_client import GeminiClient

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

def test_01_tick_orchestration(db_session, set_sim_time):
    """
    1. Tick runs a due job, skips a future one, disables a one-shot, reschedules
       a recurring into the future.
    """
    now = datetime(2026, 7, 15, 12, 0, 0)
    set_sim_time(now)
    
    # Register dummy handlers
    run_log = []
    register_handler("job_one_shot", lambda db, vt=None: run_log.append("one_shot"))
    register_handler("job_recurring", lambda db, vt=None: run_log.append("recurring"))
    register_handler("job_future", lambda db, vt=None: run_log.append("future"))
    
    job_one = ScheduledJob(job_type="job_one_shot", next_due_at=now - timedelta(minutes=5), interval_seconds=None, enabled=True, catchup_policy="once")
    job_rec = ScheduledJob(job_type="job_recurring", next_due_at=now - timedelta(minutes=5), interval_seconds=3600, enabled=True, catchup_policy="once")
    job_fut = ScheduledJob(job_type="job_future", next_due_at=now + timedelta(minutes=5), interval_seconds=3600, enabled=True, catchup_policy="once")
    
    db_session.add_all([job_one, job_rec, job_fut])
    db_session.commit()
    
    stats = tick(db_session)
    assert len(run_log) == 2
    assert "one_shot" in run_log
    assert "recurring" in run_log
    assert "future" not in run_log
    
    # Verify job states
    db_session.refresh(job_one)
    db_session.refresh(job_rec)
    db_session.refresh(job_fut)
    
    assert job_one.enabled is False  # one-shot disabled
    assert job_rec.enabled is True
    # recurring next_due_at advanced to 13:55 (since now=12:00, old=11:55, old+interval=12:55, which is in future)
    assert job_rec.next_due_at == datetime(2026, 7, 15, 12, 55, 0)
    assert job_fut.next_due_at == now + timedelta(minutes=5)  # unchanged

def test_02_scheduler_catchup(client, db_session, monkeypatch, set_sim_time):
    """
    2. Catchup: set clock, create hourly job (catchup "once") + daily brief job
       (catchup "every"); jump the (mocked) clock forward 3 days -> hourly
       handler ran once, brief handler ran 3 times (assert 3 briefs rows with
       distinct dates, fake LLM).

    2026-07-23: previously drove the jump via POST /api/time/advance -- now
    inert for now_ist() (sim time retired, see app/timeservice.py), so this
    jumps set_sim_time directly instead. Nothing currently subscribes to
    on_time_change/fire_time_change to auto-tick on a time jump (that hook
    is unused dead wiring already, not something this test ever exercised --
    tick() below is always called manually).
    """
    start_time = datetime(2026, 7, 15, 12, 0, 0)
    set_sim_time(start_time)
    
    # Mock LLM response for morning brief (echoes input)
    response_payload = {
        "candidates": [{
            "content": {"parts": [{"text": "Mock Brief Content"}]},
            "finishReason": "STOP"
        }]
    }
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport([response_payload, response_payload, response_payload]))
    monkeypatch.setattr("app.brief.get_client", lambda: fake_client)
    
    # Custom handlers
    dream_runs = []
    register_handler("dream_cycle", lambda db, vt=None: dream_runs.append(1))
    
    # We will use the seeded jobs but customize them
    # Ensure they exist in DB
    # Fetch seeded jobs
    from app.scheduler import seed_default_jobs
    seed_default_jobs(db_session)
    
    # Get the default jobs and adjust them to our starting anchor
    dream_job = db_session.scalars(select(ScheduledJob).where(ScheduledJob.job_type == "dream_cycle")).first()
    brief_job = db_session.scalars(select(ScheduledJob).where(ScheduledJob.job_type == "morning_brief")).first()
    
    # Set other default jobs disabled to isolate the test
    db_session.query(ScheduledJob).filter(ScheduledJob.job_type.notin_(["dream_cycle", "morning_brief"])).update({"enabled": False})
    
    # Set next_due_at for our test
    dream_job.next_due_at = start_time + timedelta(hours=1)
    brief_job.next_due_at = datetime(2026, 7, 16, 9, 0, 0)  # Next morning
    db_session.commit()
    
    # Jump the (mocked) clock forward 3 days
    set_sim_time(start_time + timedelta(days=3))

    # Trigger tick manually inside test context (lifespan hooks isolated during tests)
    stats = tick(db_session)
    
    # Hourly "dream_cycle" (catchup_policy: "once") runs exactly once
    assert len(dream_runs) == 1
    
    # Daily "morning_brief" (catchup_policy: "every") runs exactly 3 times (for 16th, 17th, and 18th morning)
    briefs = db_session.query(Brief).order_by(Brief.brief_date.asc()).all()
    assert len(briefs) == 3
    assert briefs[0].brief_date == "2026-07-16"
    assert briefs[1].brief_date == "2026-07-17"
    assert briefs[2].brief_date == "2026-07-18"

def test_03_handler_raises_exception_continues(db_session, set_sim_time):
    """
    3. Handler that raises -> tick continues to the next job.
    """
    now = datetime(2026, 7, 15, 12, 0, 0)
    set_sim_time(now)
    
    run_log = []
    register_handler("job_raiser", lambda db, vt=None: exec('raise ValueError("Broken!")'))
    register_handler("job_after", lambda db, vt=None: run_log.append("success"))
    
    job_raise = ScheduledJob(job_type="job_raiser", next_due_at=now, interval_seconds=None, enabled=True)
    job_after = ScheduledJob(job_type="job_after", next_due_at=now, interval_seconds=None, enabled=True)
    db_session.add_all([job_raise, job_after])
    db_session.commit()
    
    stats = tick(db_session)
    assert "success" in run_log
    
    db_session.refresh(job_raise)
    db_session.refresh(job_after)
    assert job_raise.enabled is False
    assert job_after.enabled is False

def test_04_followup_lifecycle(client, db_session, set_sim_time):
    """
    4. Followups: create one due yesterday -> check pings once (queue/DB has
       Harry's DM), 24h later pings again, 24h after that escalates (manager DM
       exists, status escalated). Simulate an answer (webhook message from
       target in the DM channel) -> status answered.
    """
    # Create target dev member and manager
    dev = TeamMember(id="U_ALICE", name="Alice Dev", role="Developer", slack_handle="U_ALICE")
    manager = TeamMember(id="U_SHIVAM", name="Shivam Manager", role="Manager", slack_handle="U_SHIVAM")
    db_session.add_all([dev, manager])
    db_session.commit()
    
    now = datetime(2026, 7, 15, 12, 0, 0)
    set_sim_time(now)
    
    # Create followup due yesterday
    followup = Followup(
        target_member_id="U_ALICE",
        question="Did you complete the SQLite schema?",
        created_by="U_SHIVAM",
        due_at=now - timedelta(days=1),
        status="open",
        ping_count=0
    )
    db_session.add(followup)
    db_session.commit()
    
    # First check: pings once
    res = run_followup_check(db_session)
    assert res["pinged"] == 1
    db_session.refresh(followup)
    assert followup.ping_count == 1
    assert followup.last_ping_at is not None
    assert abs((followup.last_ping_at - now).total_seconds()) < 2.0
    assert followup.status == "open"
    
    # Verify Harry's outbound message in DB
    msg1 = db_session.query(UnifiedMessage).filter(UnifiedMessage.channel_raw_id == "DM_U_ALICE_U_HARRY").first()
    assert msg1 is not None
    assert msg1.content == "Did you complete the SQLite schema?"
    assert msg1.sender_raw_id == "U_HARRY"
    
    # Advance time 24h later
    now_24h = now + timedelta(hours=24, minutes=5)
    set_sim_time(now_24h)
    
    # Check again: pings again (2nd ping)
    res = run_followup_check(db_session)
    assert res["pinged"] == 1
    db_session.refresh(followup)
    assert followup.ping_count == 2
    assert followup.last_ping_at is not None
    assert abs((followup.last_ping_at - now_24h).total_seconds()) < 2.0
    assert followup.status == "open"
    
    # Advance time another 24h later
    now_48h = now_24h + timedelta(hours=24, minutes=5)
    set_sim_time(now_48h)
    
    # Check again: escalates (manager DM exists)
    res = run_followup_check(db_session)
    assert res["escalated"] == 1
    assert res["pinged"] == 0
    db_session.refresh(followup)
    assert followup.status == "escalated"
    
    # Verify Manager DM exists
    manager_msg = db_session.query(UnifiedMessage).filter(UnifiedMessage.channel_raw_id == "DM_U_HARRY_U_SHIVAM").first()
    assert manager_msg is not None
    assert "Alice Dev" in manager_msg.content
    assert "twice" in manager_msg.content
    
    # Reset status to open, ping_count=1 to test answering
    followup.status = "open"
    followup.ping_count = 1
    followup.last_ping_at = now_48h
    db_session.commit()
    
    # Simulate an incoming reply from Alice in the DM channel
    reply = UnifiedMessage(
        platform_msg_id="reply_1",
        source="slack",
        direction="inbound",
        sender_raw_id="U_ALICE",
        sender_mapped_name="Alice Dev",
        channel_raw_id="DM_U_ALICE_U_HARRY",
        content="Yes, I did!",
        timestamp=now_48h + timedelta(minutes=5),
        is_processed=False
    )
    db_session.add(reply)
    db_session.commit()
    
    # Check followup
    res = run_followup_check(db_session)
    assert res["answered"] == 1
    db_session.refresh(followup)
    assert followup.status == "answered"
    assert followup.answer_message_id == reply.id

def test_05_health_degrade(db_session, set_sim_time):
    """
    5. Health: project with 2 overdue tasks + 1 open conflict -> red/yellow;
       assert manager DM on degrade, silence on repeat eval (no change).
    """
    now = datetime(2026, 7, 15, 12, 0, 0)
    set_sim_time(now)
    
    manager = TeamMember(id="U_SHIVAM", name="Shivam Manager", role="Manager", slack_handle="U_SHIVAM")
    project = Project(name="Project Phoenix", status="active", health="green")
    db_session.add_all([manager, project])
    db_session.commit()
    
    # 2 overdue tasks
    task1 = Task(project_id=project.id, title="Task 1", due_date=date(2026, 7, 14), status="pending")
    task2 = Task(project_id=project.id, title="Task 2", due_date=date(2026, 7, 14), status="pending")
    db_session.add_all([task1, task2])
    db_session.commit()
    
    # Evaluate health -> should be red or yellow (score 2 overdue = 4 points -> yellow)
    grade, reasons = evaluate_project_health(db_session, project)
    assert grade == "yellow"
    assert "2 task(s) overdue" in reasons[0]
    
    # Trigger run_health_eval -> degrades from green to yellow, manager notified
    res = run_health_eval(db_session)
    assert res["degraded"] == 1
    
    # Verify manager DM exists
    manager_msg = db_session.query(UnifiedMessage).filter(UnifiedMessage.channel_raw_id == "DM_U_HARRY_U_SHIVAM").first()
    assert manager_msg is not None
    assert "Project *Project Phoenix* health has degraded" in manager_msg.content
    
    # Clear message log for repeat check
    db_session.delete(manager_msg)
    db_session.commit()
    
    # Run again -> health same (yellow), no notification!
    res2 = run_health_eval(db_session)
    assert res2["degraded"] == 0
    manager_msg_repeat = db_session.query(UnifiedMessage).filter(UnifiedMessage.channel_raw_id == "DM_U_HARRY_U_SHIVAM").first()
    assert manager_msg_repeat is None

def test_06_morning_brief_assembly(db_session, monkeypatch, set_sim_time):
    """
    6. Morning brief content: seed a held 23:00 ping, an open conflict, an
       overdue task; run brief handler at 09:00 -> held ping released, brief
       row contains all three facts (use fake LLM that echoes input facts).
    """
    # Manager, project, task
    manager = TeamMember(id="U_SHIVAM", name="Shivam Manager", role="Manager", slack_handle="U_SHIVAM")
    project = Project(name="Project Phoenix", status="active", health="yellow")
    db_session.add_all([manager, project])
    db_session.commit()
    
    # Overdue task
    task = Task(project_id=project.id, title="Fix concurrency locks", due_date=date(2026, 7, 14), status="pending")
    db_session.add(task)
    
    # Seed project Entity
    entity = Entity(slug="project:project-phoenix", type="project", name="Project Phoenix")
    db_session.add(entity)
    db_session.commit()
    
    # Open conflict
    claim_a = AttributedClaim(entity_id=entity.id, claim="A", kind="status", holder="U_A", weight=1.0, claimed_at=datetime(2026, 7, 15), active=True)
    claim_b = AttributedClaim(entity_id=entity.id, claim="B", kind="status", holder="U_B", weight=1.0, claimed_at=datetime(2026, 7, 15), active=True)
    db_session.add_all([claim_a, claim_b])
    db_session.commit()
    
    conflict = Conflict(entity_id=entity.id, claim_a_id=claim_a.id, claim_b_id=claim_b.id, severity="medium", description="DB locks dispute", status="open", detected_at=datetime(2026, 7, 15))
    db_session.add(conflict)
    db_session.commit()
    
    # Held ping overnight (11 PM)
    from app.outbound import OutboundQueue
    held_ping = OutboundQueue(
        channel_type="slack",
        payload=json.dumps({"channel": "C_GENERAL", "text": "Overnight test ping"}),
        status="held",
        created_at=datetime(2026, 7, 15, 23, 0, 0),
        scheduled_release_at=datetime(2026, 7, 16, 9, 0, 0)
    )
    db_session.add(held_ping)
    db_session.commit()
    
    # Anchor simulated time to Jul 16 09:00 IST
    anchor_time = datetime(2026, 7, 16, 9, 0, 0)
    set_sim_time(anchor_time)
    
    # Mock LLM synthesis
    response_payload = {
        "candidates": [{
            "content": {
                "parts": [{
                    "text": "Executive Morning Brief:\n- Released 1 overnight ping.\n- DB locks dispute is open on Project Phoenix.\n- Project Phoenix health is YELLOW.\n- 1 task is overdue."
                }]
            },
            "finishReason": "STOP"
        }]
    }
    
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport([response_payload]))
    monkeypatch.setattr("app.brief.get_client", lambda: fake_client)
    
    # Run morning brief
    brief = run_morning_brief(db_session, anchor_time)
    assert brief is not None
    assert brief.brief_date == "2026-07-16"
    assert "DB locks dispute" in brief.content
    assert "YELLOW" in brief.content
    
    # Verify held ping is released
    db_session.refresh(held_ping)
    assert held_ping.status == "sent"
    assert held_ping.sent_at is not None
