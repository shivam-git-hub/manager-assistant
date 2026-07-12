import os
import pytest
from datetime import datetime
import pytz
from fastapi.testclient import TestClient

from app.config import IST
from app import timeservice
from app.database import UnifiedMessage, TeamMember, init_db, get_db
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

def test_02_slack_send_success(client, db_session):
    """
    2. Slack send: valid body -> response has ok: true, ts string; DB row has
       direction="outbound", sender_mapped_name="Harry", correct channel; message
       appears in GET /api/messages.
    """
    # Ensure timeservice is anchored to a known time
    anchor_time = datetime(2026, 7, 15, 12, 0, 0)
    timeservice.set_time(anchor_time)
    
    payload = {
        "channel": "C_GENERAL",
        "text": "Hi Alice, any update on the schema?"
    }
    
    response = client.post("/api/integrations/slack/send", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "ts" in data
    assert data["channel"] == "C_GENERAL"
    assert data["message"]["user"] == "U_HARRY"
    assert data["message"]["text"] == "Hi Alice, any update on the schema?"
    
    # Check DB row
    msg = db_session.query(UnifiedMessage).filter(UnifiedMessage.channel_raw_id == "C_GENERAL").first()
    assert msg is not None
    assert msg.direction == "outbound"
    assert msg.sender_raw_id == "U_HARRY"
    assert msg.sender_mapped_name == "Harry"
    assert msg.content == "Hi Alice, any update on the schema?"
    assert msg.source == "slack"
    
    # Check GET /api/messages API
    res = client.get("/api/messages")
    assert res.status_code == 200
    messages = res.json()
    # Find our sent message
    out_messages = [m for m in messages if m["direction"] == "outbound"]
    assert len(out_messages) >= 1
    assert out_messages[0]["content"] == "Hi Alice, any update on the schema?"
    assert out_messages[0]["sender_mapped_name"] == "Harry"

def test_03_slack_send_empty_text(client):
    """
    3. Slack send with empty text -> {"ok": false, "error": "invalid_arguments"}.
    """
    payload = {
        "channel": "C_GENERAL",
        "text": ""
    }
    response = client.post("/api/integrations/slack/send", json=payload)
    assert response.status_code == 200
    assert response.json() == {"ok": False, "error": "invalid_arguments"}
    
    # Missing fields
    payload_missing = {
        "channel": "C_GENERAL"
    }
    response2 = client.post("/api/integrations/slack/send", json=payload_missing)
    assert response2.status_code == 200
    assert response2.json() == {"ok": False, "error": "invalid_arguments"}

def test_04_outlook_send_success(client, db_session):
    """
    4. Outlook send: valid Graph-shaped body -> 202 empty body; DB row outbound,
       HTML body cleaned (send <style>x{color:red}</style><p>Done</p>, stored
       content must contain "Done" and no CSS), subject stored per convention.
    """
    # Anchor time
    anchor_time = datetime(2026, 7, 15, 12, 0, 0)
    timeservice.set_time(anchor_time)
    
    payload = {
        "message": {
            "subject": "Weekly Update",
            "body": {
                "contentType": "HTML",
                "content": "<style>x{color:red}</style><p>Done</p>"
            },
            "toRecipients": [
                {
                    "emailAddress": {
                        "address": "alice@company.com"
                    }
                }
            ]
        },
        "saveToSentItems": True
    }
    
    response = client.post("/api/integrations/outlook/send", json=payload)
    assert response.status_code == 202
    assert response.text == "" or response.json() is None # Empty body
    
    # Check DB row
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
    assert "Subject:" not in msg.content

def test_04b_outlook_send_invalid(client):
    """
    Outlook send validation error -> 400 with Graph-style error.
    """
    payload = {
        "message": {
            "subject": "Missing recipients/body"
        }
    }
    response = client.post("/api/integrations/outlook/send", json=payload)
    assert response.status_code == 400
    err = response.json()
    assert "error" in err
    assert "code" in err["error"]
    assert "message" in err["error"]

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
    # 1. Create a matching team member first
    member_payload = {
        "id": "U_ALICE_MEMBER",
        "name": "Alice Developer",
        "role": "Backend dev",
        "slack_handle": "U_ALICE_MEMBER",
        "outlook_email": "alice@company.com"
    }
    client.post("/api/team", json=member_payload)

    # 2. Mock a slack event message
    slack_payload = {
        "type": "event_callback",
        "event": {
            "type": "message",
            "client_msg_id": "client_msg_abc_999",
            "user": "U_ALICE_MEMBER",
            "channel": "C_DEV_CHANNEL",
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
