import os
from datetime import datetime, timedelta
import pytz

from app.config import IST
from app import timeservice
from app.database import UnifiedMessage, TeamMember


def test_01_now_ist_tracks_real_wall_clock():
    """now_ist() is real IST wall-clock time -- the one call site every
    other module reads time through."""
    now = timeservice.now_ist()
    real_now = datetime.now(IST).replace(tzinfo=None)
    assert abs((now - real_now).total_seconds()) < 2.0


def test_02_epoch_and_iso_agree_with_now_ist():
    now_ist_val = timeservice.now_ist()
    epoch = timeservice.now_epoch()
    utc_iso = timeservice.now_utc_iso()

    dt_utc = datetime.fromtimestamp(epoch, tz=pytz.UTC)
    dt_ist = dt_utc.astimezone(IST).replace(tzinfo=None)
    assert abs((dt_ist - now_ist_val).total_seconds()) < 1.0

    assert utc_iso.endswith("Z")
    dt_from_iso = datetime.fromisoformat(utc_iso[:-1])  # UTC naive
    dt_ist_from_iso = dt_from_iso + timedelta(hours=5, minutes=30)
    assert abs((dt_ist_from_iso - now_ist_val).total_seconds()) < 1.0


def test_03_api_get_time(client):
    real_now_ist = datetime.now(IST).replace(tzinfo=None)

    res = client.get("/api/time")
    assert res.status_code == 200
    data = res.json()
    assert "sim_time_ist" in data
    assert "epoch" in data
    assert "utc_iso" in data
    assert abs((datetime.fromisoformat(data["sim_time_ist"]) - real_now_ist).total_seconds()) < 2.0


def test_04_ingestion_stamping(client, db_session):
    """Ingestion stamping tracks real wall-clock time: POST a dashboard
    message (/api/integrations/dashboard/message) -> stored timestamp ~=
    real now; POST a Slack webhook WITHOUT ts -> stored timestamp ~= real
    now too."""
    import uuid
    from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Agent, Employee, get_employee_by_manager_id
    cp_db = ControlPlaneSessionLocal()
    get_employee_by_manager_id(cp_db, client.manager_id).slack_id = "U_MANAGER"
    cp_db.add(Employee(id=uuid.uuid4().hex, email="alice-dashboard@company.com", name="Alice Developer", slack_id="U_ALICE"))
    cp_db.commit()
    cp_db.add(Agent(
        id=f"agent-{client.manager_id}", name="Test Agent", slack_app_id="A_TEST",
        slack_client_id="cid", slack_client_secret="csecret", slack_signing_secret="ssecret",
        manager_id=client.manager_id, team_id="T_TEST", bot_token="xoxb-fake",
    ))
    cp_db.commit()
    cp_db.close()

    real_now_ist = datetime.now(IST).replace(tzinfo=None)

    res = client.post("/api/integrations/dashboard/message", json={
        "user_name": "Shivam",
        "message": "Hello Harry from dashboard time check!"
    })
    assert res.status_code == 201
    dash_msg = db_session.query(UnifiedMessage).filter(UnifiedMessage.source == "dashboard").first()
    assert dash_msg is not None
    assert abs((dash_msg.timestamp - real_now_ist).total_seconds()) < 5.0

    slack_payload = {
        "token": "verification_token",
        "team_id": "T_TEST",
        "api_app_id": "A_TEST",
        "event": {
            "type": "message",
            "channel": "D_ALICE_MANAGER",
            "channel_type": "im",
            "user": "U_ALICE",
            "text": "Hello, does this stamp real time?",
        },
        "type": "event_callback"
    }
    res = client.post("/api/integrations/slack/webhook", json=slack_payload)
    assert res.status_code == 200

    slack_msg = db_session.query(UnifiedMessage).filter(UnifiedMessage.source == "slack").first()
    assert slack_msg is not None
    assert abs((slack_msg.timestamp - real_now_ist).total_seconds()) < 5.0


def test_05_wall_clock_guard():
    """Walk app/**/*.py, assert no occurrence of datetime.now(/
    datetime.utcnow/time.time() outside app/timeservice.py.

    app/projectkb/ is exempt: it is real-wall-clock by deliberate design.

    app/integrations/ is also exempt for real-clock reads specifically tied
    to verifying real external requests (e.g. Slack webhook signature replay
    -- window checks against time.time(), not against sim time, since
    Slack's own request timestamps are real wall-clock) and for stamping
    UnifiedMessage.created_at (real DB-insertion time, deliberately distinct
    from `timestamp`, the message's own claimed event time).

    app/api/ is exempt for the same created_at reason -- the dashboard
    channel's ingest handler lives there and needs the same real insertion
    timestamp as the other channels.

    app/controlplane/ is exempt: auth session expiry is inherently real
    wall-clock, same reasoning as the other exemptions above.
    """
    import glob
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_pattern = os.path.join(base_dir, "app", "**", "*.py")

    for file_path in glob.glob(app_pattern, recursive=True):
        if "timeservice.py" in file_path:
            continue
        if f"{os.sep}projectkb{os.sep}" in file_path:
            continue
        if f"{os.sep}controlplane{os.sep}" in file_path:
            continue
        if f"{os.sep}integrations{os.sep}" in file_path:
            continue
        if f"{os.sep}api{os.sep}" in file_path:
            continue

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        lines = []
        for line in content.splitlines():
            if "#" in line:
                line = line.split("#", 1)[0]
            lines.append(line)
        cleaned_content = "\n".join(lines)

        assert "datetime.now(" not in cleaned_content, f"Banned wall-clock read 'datetime.now(' found in {file_path}"
        assert "datetime.utcnow" not in cleaned_content, f"Banned wall-clock read 'datetime.utcnow' found in {file_path}"
        assert "time.time(" not in cleaned_content, f"Banned wall-clock read 'time.time(' found in {file_path}"
