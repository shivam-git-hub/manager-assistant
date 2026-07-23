import os
import time
import pytest
from datetime import datetime, timedelta
import pytz
from fastapi.testclient import TestClient

from app.config import IST
# We will import timeservice within tests or as a dynamic import if it's not yet fully implemented,
# but since we are doing TDD, we can assume it will be there.
from app import timeservice
from app.database import UnifiedMessage, TeamMember

@pytest.fixture(autouse=True)
def setup_tmp_clock(monkeypatch, tmp_path):
    """
    Automatically redirect SIM_CLOCK_PATH to a temporary file for every test
    to guarantee perfect isolation.
    """
    tmp_file = tmp_path / "sim_clock.json"
    monkeypatch.setenv("SIM_CLOCK_PATH", str(tmp_file))
    # Clear any module-level cached paths or state if needed,
    # and ensure the file is deleted so we test initialization.
    if os.path.exists(tmp_file):
        os.remove(tmp_file)
    
    # Also, reset timeservice state so it reloads the file
    if hasattr(timeservice, "_reset_state_for_tests"):
        timeservice._reset_state_for_tests()
        
    yield tmp_file
    
    if os.path.exists(tmp_file):
        try:
            os.remove(tmp_file)
        except OSError:
            pass

def test_01_initialization_defaults(setup_tmp_clock):
    """
    1. now_ist() tracks real wall-clock IST time (2026-07-23: sim time
       retired -- see now_ist()'s own docstring). It no longer touches the
       anchor file at all, so nothing is created just by reading it.
    """
    now = timeservice.now_ist()

    assert not os.path.exists(setup_tmp_clock)

    real_now = datetime.now(IST).replace(tzinfo=None)
    assert abs((now - real_now).total_seconds()) < 2.0

def test_02_set_time_and_advance_are_inert_for_now_ist(setup_tmp_clock):
    """
    2. set_time()/advance() still write the anchor file (the simulator UI's
       clock-jump widget still calls them, see get_state()) but no longer
       affect now_ist() -- production time reads real wall-clock only now.
    """
    target_time = datetime(2026, 7, 15, 10, 30, 0)
    timeservice.set_time(target_time)
    timeservice.advance(3 * 24 * 3600)

    now = timeservice.now_ist()
    real_now = datetime.now(IST).replace(tzinfo=None)
    assert abs((now - real_now).total_seconds()) < 2.0
    assert abs((now - target_time).total_seconds()) > 60

def test_04_iso_and_epoch_agreement(setup_tmp_clock):
    """
    4. now_epoch() / now_utc_iso() agree with now_ist() (IST = UTC+5:30).
    """
    target_time = datetime(2026, 7, 15, 10, 30, 0) # naive IST
    timeservice.set_time(target_time)
    
    epoch = timeservice.now_epoch()
    utc_iso = timeservice.now_utc_iso()
    now_ist_val = timeservice.now_ist()
    
    # Let's convert epoch back to naive IST
    # epoch is seconds since UTC epoch. So we convert to UTC datetime first
    dt_utc = datetime.fromtimestamp(epoch, tz=pytz.UTC)
    dt_ist = dt_utc.astimezone(IST).replace(tzinfo=None)
    assert abs((dt_ist - now_ist_val).total_seconds()) < 1.0
    
    # Check UTC ISO string. 10:30 IST is 05:00 UTC
    assert utc_iso.endswith("Z")
    iso_stripped = utc_iso[:-1]  # remove Z
    dt_from_iso = datetime.fromisoformat(iso_stripped) # UTC naive
    # Add 5.5 hours to get IST
    dt_ist_from_iso = dt_from_iso + timedelta(hours=5, minutes=30)
    assert abs((dt_ist_from_iso - now_ist_val).total_seconds()) < 1.0

def test_05_api_endpoints(client, setup_tmp_clock):
    """
    5. API round-trip: GET/set/advance/reset via FastAPI TestClient still
       succeed (the simulator UI's clock widget still calls these) and
       still write the anchor file (get_state()'s anchor_sim_time reflects
       the request), but 2026-07-23 on, `sim_time_ist` in every response is
       real wall-clock time -- set/advance/reset no longer change it; 422
       still guards zero/negative advance.
    """
    real_now_ist = datetime.now(IST).replace(tzinfo=None)

    # 1. GET /api/time
    res = client.get("/api/time")
    assert res.status_code == 200
    data = res.json()
    assert "sim_time_ist" in data
    assert "epoch" in data
    assert "utc_iso" in data
    assert abs((datetime.fromisoformat(data["sim_time_ist"]) - real_now_ist).total_seconds()) < 2.0

    # 2. POST /api/time/set -- anchor is written, but sim_time_ist stays real
    test_dt_str = "2026-07-20T14:45:00"
    res = client.post("/api/time/set", json={"datetime": test_dt_str})
    assert res.status_code == 200
    data = res.json()
    assert data["anchor_sim_time"] == test_dt_str
    assert abs((datetime.fromisoformat(data["sim_time_ist"]) - real_now_ist).total_seconds()) < 2.0

    # 3. POST /api/time/advance -- same, inert for sim_time_ist
    res = client.post("/api/time/advance", json={"days": 1, "hours": 2, "minutes": 15})
    assert res.status_code == 200
    data = res.json()
    assert abs((datetime.fromisoformat(data["sim_time_ist"]) - real_now_ist).total_seconds()) < 2.0

    # 4. POST /api/time/advance validation: negative/zero total
    res = client.post("/api/time/advance", json={"days": 0, "hours": 0, "minutes": 0})
    assert res.status_code == 422

    res = client.post("/api/time/advance", json={"days": -1, "hours": 0, "minutes": 0})
    assert res.status_code == 422

    # 5. POST /api/time/reset
    res = client.post("/api/time/reset")
    assert res.status_code == 200
    data = res.json()
    parsed_reset_dt = datetime.fromisoformat(data["sim_time_ist"])
    assert abs((parsed_reset_dt - real_now_ist).total_seconds()) < 2.0

def test_06_ingestion_stamping(client, db_session, setup_tmp_clock):
    """
    6. Ingestion stamping tracks real wall-clock time now (sim time
       retired): POST a dashboard message (/api/integrations/dashboard/
       message) → stored timestamp ~= real now; POST a Slack webhook
       WITHOUT ts → stored timestamp ~= real now too.
    """
    # Pre-populate some team members so we resolve them correctly
    manager = TeamMember(id="dashboard_shivam", name="Shivam", role="Manager", slack_handle="U_MANAGER")
    alice = TeamMember(id="U_ALICE", name="Alice Developer", role="Developer", slack_handle="U_ALICE")
    db_session.add(manager)
    db_session.add(alice)
    db_session.commit()

    # An installed Agent for api_app_id "A_TEST" is required now: webhook
    # routing (step 17 piece 2b) ignores events from an unknown api_app_id.
    from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Agent
    cp_db = ControlPlaneSessionLocal()
    cp_db.add(Agent(
        id=f"agent-{client.manager_id}", name="Test Agent", slack_app_id="A_TEST",
        slack_client_id="cid", slack_client_secret="csecret", slack_signing_secret="ssecret",
        manager_id=client.manager_id, team_id="T_TEST", bot_token="xoxb-fake",
    ))
    cp_db.commit()
    cp_db.close()

    real_now_ist = datetime.now(IST).replace(tzinfo=None)

    # 1. POST a dashboard message (no timestamp in body)
    res = client.post("/api/integrations/dashboard/message", json={
        "user_name": "Shivam",
        "message": "Hello Harry from dashboard sim time check!"
    })
    assert res.status_code == 201
    dash_msg = db_session.query(UnifiedMessage).filter(UnifiedMessage.source == "dashboard").first()
    assert dash_msg is not None
    assert abs((dash_msg.timestamp - real_now_ist).total_seconds()) < 5.0
    
    # 2. POST a Slack webhook without ts
    slack_payload = {
        "token": "verification_token",
        "team_id": "T_TEST",
        "api_app_id": "A_TEST",
        "event": {
            "type": "message",
            "channel": "D_ALICE_MANAGER",
            "channel_type": "im",
            "user": "U_ALICE",
            "text": "Hello, is sim clock on backend active?",
            # "ts" is intentionally missing or None
        },
        "type": "event_callback"
    }
    # Check slack webhook
    res = client.post("/api/integrations/slack/webhook", json=slack_payload)
    assert res.status_code == 200
    
    # Retrieve the ingested message
    slack_msg = db_session.query(UnifiedMessage).filter(UnifiedMessage.source == "slack").first()
    assert slack_msg is not None
    # Timestamp should match real wall-clock time (no ts on the payload)
    assert abs((slack_msg.timestamp - real_now_ist).total_seconds()) < 5.0

def test_07_wall_clock_guard():
    """
    7. Wall-clock guard: walk app/**/*.py, assert no occurrence of
       datetime.now( / datetime.utcnow / time.time() outside app/timeservice.py
       (comment-stripped or simple substring check is fine).

    app/projectkb/ is exempt: unlike the sim-time-anchored legacy pipeline,
    projectkb is real-wall-clock by deliberate design (the simulator is a
    testing concern layered on top later, not something production code
    depends on for this subsystem).

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
    wall-clock (a login shouldn't stay valid or invalid depending on the
    simulator's clock), same reasoning as the other exemptions above.
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
            
        # Strip comments to avoid false positives in comments
        lines = []
        for line in content.splitlines():
            # simple comment stripping
            if "#" in line:
                line = line.split("#", 1)[0]
            lines.append(line)
        cleaned_content = "\n".join(lines)
        
        # We assert that we don't have:
        # - datetime.now(
        # - datetime.utcnow
        # - time.time()
        # Note: we might import pytz or write other things, but actual calls to datetime.now() are banned
        assert "datetime.now(" not in cleaned_content, f"Banned wall-clock read 'datetime.now(' found in {file_path}"
        assert "datetime.utcnow" not in cleaned_content, f"Banned wall-clock read 'datetime.utcnow' found in {file_path}"
        assert "time.time(" not in cleaned_content, f"Banned wall-clock read 'time.time(' found in {file_path}"
