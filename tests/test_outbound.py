import os
import pytest
from datetime import datetime
import pytz
from fastapi.testclient import TestClient

from app.config import IST
from app import timeservice
from app.database import UnifiedMessage, TeamMember
from app.outbound import is_quiet_hours, next_work_morning, send_or_hold

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

def test_01_harry_seeded_automatically(db_session):
    """
    1. Harry exists after init_db() on a fresh test DB (id U_HARRY).
    """
    # init_db is called inside the db_session fixture, so we just check if U_HARRY exists in the DB session.
    harry = db_session.query(TeamMember).filter(TeamMember.id == "U_HARRY").first()
    assert harry is not None
    assert harry.name == "Harry"
    assert harry.role == "AI Assistant"
    assert harry.slack_handle == "U_HARRY"
    assert harry.outlook_email == "harry.assistant@company.com"

def test_02_slack_send_success(db_session):
    """
    2. Slack connector.send(): DB row has direction="outbound",
       sender_mapped_name="Harry", correct channel, ok result.
    """
    from app.integrations.slack import connector as slack_connector

    anchor_time = datetime(2026, 7, 15, 12, 0, 0)
    timeservice.set_time(anchor_time)

    result = slack_connector.send(db_session, "C_GENERAL", "Hi Alice, any update on the schema?")
    assert result.ok is True
    assert result.platform_msg_id

    msg = db_session.query(UnifiedMessage).filter(UnifiedMessage.channel_raw_id == "C_GENERAL").first()
    assert msg is not None
    assert msg.direction == "outbound"
    assert msg.sender_raw_id == "U_HARRY"
    assert msg.sender_mapped_name == "Harry"
    assert msg.content == "Hi Alice, any update on the schema?"
    assert msg.source == "slack"

def test_03_slack_send_empty_text(db_session):
    """
    3. Slack connector.send() with empty text -> ok=False, error="invalid_arguments".
    """
    from app.integrations.slack import connector as slack_connector

    result = slack_connector.send(db_session, "C_GENERAL", "")
    assert result.ok is False
    assert result.error == "invalid_arguments"

def test_04_outlook_send_success(db_session):
    """
    4. Outlook connector.send(): DB row outbound, HTML body cleaned (send
       <style>x{color:red}</style><p>Done</p>, stored content must contain
       "Done" and no CSS), subject stored per convention.
    """
    from app.integrations.outlook import connector as outlook_connector

    anchor_time = datetime(2026, 7, 15, 12, 0, 0)
    timeservice.set_time(anchor_time)

    result = outlook_connector.send(
        db_session, "alice@company.com", "<style>x{color:red}</style><p>Done</p>", "Weekly Update"
    )
    assert result.ok is True

    msg = db_session.query(UnifiedMessage).filter(UnifiedMessage.source == "outlook").first()
    assert msg is not None
    assert msg.direction == "outbound"
    assert msg.sender_raw_id == "harry.assistant@company.com"
    assert msg.sender_mapped_name == "Harry"
    # Conventions must match ingest exactly: plain recipient address as the
    # channel, subject in its own column, content = cleaned body only.
    assert msg.channel_raw_id == "alice@company.com"
    assert msg.subject == "Weekly Update"
    assert "Done" in msg.content
    assert "style" not in msg.content
    assert "color:red" not in msg.content

def test_04b_outlook_send_invalid(db_session):
    """
    Outlook connector.send() validation error -> ok=False, error="invalidRequest".
    """
    from app.integrations.outlook import connector as outlook_connector

    result = outlook_connector.send(db_session, "", "", "Missing recipients/body")
    assert result.ok is False
    assert result.error == "invalidRequest"

def test_05_is_quiet_hours():
    """
    5. is_quiet_hours: 08:59 -> True, 09:00 -> False, 18:59 -> False, 19:00 -> True;
       any Saturday/Sunday daytime -> True.
    """
    # Weekdays (Wednesday = 2)
    assert is_quiet_hours(datetime(2026, 7, 15, 8, 59, 0)) is True
    assert is_quiet_hours(datetime(2026, 7, 15, 9, 0, 0)) is False
    assert is_quiet_hours(datetime(2026, 7, 15, 18, 59, 59)) is False
    assert is_quiet_hours(datetime(2026, 7, 15, 19, 0, 0)) is True
    assert is_quiet_hours(datetime(2026, 7, 15, 23, 30, 0)) is True
    assert is_quiet_hours(datetime(2026, 7, 15, 4, 15, 0)) is True
    
    # Saturday (5) and Sunday (6)
    assert is_quiet_hours(datetime(2026, 7, 18, 12, 0, 0)) is True # Sat 12pm -> quiet
    assert is_quiet_hours(datetime(2026, 7, 19, 15, 30, 0)) is True # Sun 3:30pm -> quiet

def test_06_next_work_morning():
    """
    6. next_work_morning: Tue 03:00 -> Tue 09:00; Tue 20:00 -> Wed 09:00;
       Fri 22:00 -> Mon 09:00; Sat 11:00 -> Mon 09:00.
    """
    # Tue Jul 14 03:00 IST -> Tue Jul 14 09:00 IST
    tue_early = datetime(2026, 7, 14, 3, 0, 0)
    assert next_work_morning(tue_early) == datetime(2026, 7, 14, 9, 0, 0)
    
    # Tue Jul 14 20:00 IST -> Wed Jul 15 09:00 IST
    tue_late = datetime(2026, 7, 14, 20, 0, 0)
    assert next_work_morning(tue_late) == datetime(2026, 7, 15, 9, 0, 0)
    
    # Fri Jul 17 22:00 IST -> Mon Jul 20 09:00 IST
    fri_late = datetime(2026, 7, 17, 22, 0, 0)
    assert next_work_morning(fri_late) == datetime(2026, 7, 20, 9, 0, 0)
    
    # Sat Jul 18 11:00 IST -> Mon Jul 20 09:00 IST
    sat_day = datetime(2026, 7, 18, 11, 0, 0)
    assert next_work_morning(sat_day) == datetime(2026, 7, 20, 9, 0, 0)

def test_07_send_or_hold_work_hours(db_session):
    """
    7. send_or_hold during work hours (set sim time Wed 11:00) -> sent, row NOT queued,
       message in DB.
    """
    # Wed Jul 15 11:00 (work hours)
    anchor_time = datetime(2026, 7, 15, 11, 0, 0)
    timeservice.set_time(anchor_time)
    
    payload = {
        "channel": "C_GENERAL",
        "text": "Hi team, quick update!"
    }
    
    res = send_or_hold("slack", payload, db_session)
    assert res["status"] == "sent"
    assert "message_id" in res
    
    # Verify in unified_messages
    msg = db_session.query(UnifiedMessage).filter(UnifiedMessage.content == "Hi team, quick update!").first()
    assert msg is not None
    assert msg.direction == "outbound"
    
    # Verify NOT in queue
    from app.outbound import OutboundQueue
    queue_rows = db_session.query(OutboundQueue).all()
    assert len(queue_rows) == 0

def test_08_send_or_hold_quiet_hours_and_release(client, db_session):
    """
    8. send_or_hold at Wed 23:00 -> held with release Thu 09:00; then set sim
       time Thu 09:05, POST /api/outbound/release -> released=1, message now in
       DB with direction="outbound", queue row status="sent".
    """
    # Wed Jul 15 23:00 (quiet hours)
    anchor_time = datetime(2026, 7, 15, 23, 0, 0)
    timeservice.set_time(anchor_time)
    
    payload = {
        "channel": "C_GENERAL",
        "text": "Hold this until morning."
    }
    
    res = send_or_hold("slack", payload, db_session)
    assert res["status"] == "held"
    assert res["release_at"] == datetime(2026, 7, 16, 9, 0, 0)
    
    # Verify not yet in unified_messages
    msg = db_session.query(UnifiedMessage).filter(UnifiedMessage.content == "Hold this until morning.").first()
    assert msg is None
    
    # Verify exists in outbound_queue
    from app.outbound import OutboundQueue
    q_row = db_session.query(OutboundQueue).filter(OutboundQueue.status == "held").first()
    assert q_row is not None
    assert q_row.channel_type == "slack"
    assert q_row.scheduled_release_at == datetime(2026, 7, 16, 9, 0, 0)
    
    # Query API GET /api/outbound/queue?status=held
    resp_q = client.get("/api/outbound/queue?status=held")
    assert resp_q.status_code == 200
    q_list = resp_q.json()
    assert len(q_list) == 1
    assert q_list[0]["id"] == q_row.id
    
    # Advance time to Thu Jul 16 09:05
    new_time = datetime(2026, 7, 16, 9, 5, 0)
    timeservice.set_time(new_time)
    
    # POST /api/outbound/release
    resp_rel = client.post("/api/outbound/release")
    assert resp_rel.status_code == 200
    rel_data = resp_rel.json()
    assert rel_data["released"] == 1
    assert rel_data["remaining_held"] == 0
    
    # Verify message now exists in unified_messages
    msg_after = db_session.query(UnifiedMessage).filter(UnifiedMessage.content == "Hold this until morning.").first()
    assert msg_after is not None
    assert msg_after.direction == "outbound"
    assert msg_after.sender_raw_id == "U_HARRY"
    
    # Verify queue status updated
    db_session.refresh(q_row)
    assert q_row.status == "sent"
    assert q_row.sent_at is not None
    assert q_row.result_message_id == msg_after.platform_msg_id

def test_09_slack_ingest_direction_default(client, db_session):
    """
    9. direction defaults to "inbound" for a normal Slack webhook ingest.
    """
    # 1. Manager, then a matching team member (step 20: no tracked-contacts
    # setup needed anymore -- everything gets stored)
    client.post("/api/team", json={
        "id": "U_MANAGER", "name": "Shivam", "role": "Manager", "slack_handle": "U_MANAGER"
    })
    member_payload = {
        "id": "U_ALICE_MEMBER",
        "name": "Alice Developer",
        "role": "Backend dev",
        "slack_handle": "U_ALICE_MEMBER",
        "outlook_email": "alice@company.com"
    }
    client.post("/api/team", json=member_payload)

    # An installed Agent is required for webhook routing (step 17 piece 2b)
    # to resolve which manager's db.sqlite an event belongs to.
    from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Agent
    cp_db = ControlPlaneSessionLocal()
    cp_db.add(Agent(
        id=f"agent-{client.manager_id}", name="Test Agent", slack_app_id="A_TEST",
        slack_client_id="cid", slack_client_secret="csecret", slack_signing_secret="ssecret",
        manager_id=client.manager_id, team_id="T_TEST", bot_token="xoxb-fake",
    ))
    cp_db.commit()
    cp_db.close()

    # 2. Mock a Slack DM event
    slack_payload = {
        "team_id": "T_TEST",
        "api_app_id": "A_TEST",
        "type": "event_callback",
        "event": {
            "type": "message",
            "client_msg_id": "client_msg_abc_999",
            "user": "U_ALICE_MEMBER",
            "channel": "D_ALICE_MANAGER",
            "channel_type": "im",
            "text": "Finished the schema, Harry!",
            "ts": "1789025345.000000"
        }
    }

    response = client.post("/api/integrations/slack/webhook", json=slack_payload)
    assert response.status_code == 200
    
    # Verify in DB
    msg = db_session.query(UnifiedMessage).filter(UnifiedMessage.platform_msg_id == "client_msg_abc_999").first()
    assert msg is not None
    assert msg.direction == "inbound"
