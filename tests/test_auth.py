from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.controlplane.models import init_controlplane_db, get_controlplane_db, Manager, AuthSession, SessionLocal as ControlPlaneSessionLocal
from app.controlplane.auth import SESSION_COOKIE_NAME


def test_dev_login_creates_manager_and_session(client):
    r = client.post("/api/auth/dev-login", json={"email": "shivam@company.com", "name": "Shivam"})
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "shivam@company.com"
    assert body["name"] == "Shivam"
    assert SESSION_COOKIE_NAME in client.cookies


def test_dev_login_twice_reuses_same_manager(client):
    r1 = client.post("/api/auth/dev-login", json={"email": "shivam@company.com", "name": "Shivam"})
    r2 = client.post("/api/auth/dev-login", json={"email": "shivam@company.com", "name": "Shivam"})
    assert r1.json()["id"] == r2.json()["id"]


def test_me_without_cookie_is_401(clean_controlplane_db):
    # A bare TestClient, not the `client` fixture -- `client` auto-logs-in a
    # throwaway manager (step 15: every manager-scoped route needs one), so
    # it always carries a session cookie by the time a test gets it.
    bare_client = TestClient(app)
    r = bare_client.get("/api/auth/me")
    assert r.status_code == 401


def test_me_with_valid_session_returns_manager(client):
    login = client.post("/api/auth/dev-login", json={"email": "alice@company.com", "name": "Alice"})
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["id"] == login.json()["id"]


def test_expired_session_is_401(client):
    login = client.post("/api/auth/dev-login", json={"email": "bob@company.com", "name": "Bob"})
    manager_id = login.json()["id"]

    # Manually expire the session row.
    db = ControlPlaneSessionLocal()
    session = db.query(AuthSession).filter(AuthSession.manager_id == manager_id).first()
    session.expires_at = datetime.now() - timedelta(days=1)
    db.commit()
    db.close()

    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_logout_invalidates_session(client):
    client.post("/api/auth/dev-login", json={"email": "carol@company.com", "name": "Carol"})
    assert client.get("/api/auth/me").status_code == 200

    r = client.post("/api/auth/logout")
    assert r.status_code == 200

    assert client.get("/api/auth/me").status_code == 401


def test_dev_login_disabled_returns_404(client, monkeypatch):
    monkeypatch.setenv("DEV_AUTH_ENABLED", "false")
    r = client.post("/api/auth/dev-login", json={"email": "dave@company.com", "name": "Dave"})
    assert r.status_code == 404


def test_init_controlplane_db_is_idempotent():
    init_controlplane_db()
    init_controlplane_db()  # should not raise


def test_connections_empty_for_fresh_manager(client):
    r = client.get("/api/auth/connections")
    assert r.status_code == 200
    body = r.json()
    assert body["outlook"] == {"connected": False}
    assert body["slack"] == {"connected": False}


def test_connections_reflects_outlook_installation(client):
    from app.controlplane.models import OutlookInstallation

    db = ControlPlaneSessionLocal()
    try:
        db.add(OutlookInstallation(
            manager_id=client.manager_id,
            mailbox_email="me@outlook.com",
            token_cache_json="{}",
            granted_scopes="Mail.Read,User.Read",
        ))
        db.commit()
    finally:
        db.close()

    r = client.get("/api/auth/connections")
    body = r.json()
    assert body["outlook"]["connected"] is True
    assert body["outlook"]["mailbox_email"] == "me@outlook.com"
    assert body["outlook"]["send_enabled"] is False

    db = ControlPlaneSessionLocal()
    try:
        installation = db.get(OutlookInstallation, client.manager_id)
        installation.granted_scopes = "Mail.Read,User.Read,Mail.Send"
        db.commit()
    finally:
        db.close()

    r2 = client.get("/api/auth/connections")
    assert r2.json()["outlook"]["send_enabled"] is True


def test_connections_reflects_slack_reader_installation(client):
    """Redesigned 2026-07-23: connections()'s slack key is ONLY the
    message-tracking grant, entirely independent of any claimed Agent."""
    from app.controlplane.models import SlackReaderInstallation

    db = ControlPlaneSessionLocal()
    try:
        db.add(SlackReaderInstallation(
            manager_id=client.manager_id, team_id="T_1", team_name="Acme",
            user_token="xoxp-fake", user_id="U_X",
        ))
        db.commit()
    finally:
        db.close()

    r = client.get("/api/auth/connections")
    body = r.json()
    assert body["slack"]["connected"] is True
    assert body["slack"]["team_id"] == "T_1"
    assert body["slack"]["team_name"] == "Acme"


def test_connections_slack_unaffected_by_claimed_agent(client):
    """A claimed (even installed) Agent must not make connections()'s
    slack key report connected -- that's a completely separate concern."""
    from app.controlplane.models import Agent

    db = ControlPlaneSessionLocal()
    try:
        db.add(Agent(
            id="atlas", name="Atlas", slack_app_id="A_ATLAS",
            slack_client_id="cid", slack_client_secret="csecret", slack_signing_secret="ssecret",
            manager_id=client.manager_id, team_id="T_1", bot_token="xoxb-fake",
            installed_at=datetime.now(),
        ))
        db.commit()
    finally:
        db.close()

    r = client.get("/api/auth/connections")
    assert r.json()["slack"] == {"connected": False}
