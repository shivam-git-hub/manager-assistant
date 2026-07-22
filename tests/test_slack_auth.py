import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.controlplane.models import (
    SessionLocal as ControlPlaneSessionLocal,
    Agent,
    SlackReaderInstallation,
)
from app.controlplane.slack_auth import _sign_state, _verify_state


@pytest.fixture(autouse=True)
def reader_env(monkeypatch):
    import app.controlplane.slack_auth as slack_auth_module

    monkeypatch.setattr(slack_auth_module, "SLACK_READER_CLIENT_ID", "test-reader-client-id")
    monkeypatch.setattr(slack_auth_module, "SLACK_READER_CLIENT_SECRET", "test-reader-client-secret")


def _login(client, email="manager@company.com", name="Manager"):
    r = client.post("/api/auth/dev-login", json={"email": email, "name": name})
    assert r.status_code == 200
    return r.json()["id"]


def _seed_installed_agent(agent_id="atlas", app_id="A_TEST", manager_id=None):
    """Admin-provisioned agent, bot_token already present -- the new
    reality (redesigned 2026-07-23): installation happens entirely out of
    band (see scripts/seed_agents.py / SLACK.md), never through a
    per-manager OAuth flow."""
    db = ControlPlaneSessionLocal()
    try:
        db.add(Agent(
            id=agent_id, name="Atlas", slack_app_id=app_id,
            slack_client_id="test-slack-client-id", slack_client_secret="test-slack-client-secret",
            slack_signing_secret="test-signing-secret",
            bot_token="xoxb-preinstalled", team_id="T_KNOWN",
            manager_id=manager_id,
        ))
        db.commit()
    finally:
        db.close()
    return agent_id


# ── Reader connect flow ──────────────────────────────────────────────────

def test_install_without_session_is_401(clean_controlplane_db):
    bare_client = TestClient(app)
    r = bare_client.get("/auth/slack/install", follow_redirects=False)
    assert r.status_code == 401


def test_install_redirects_with_signed_state_no_agent_needed(client):
    manager_id = _login(client)

    r = client.get("/auth/slack/install", follow_redirects=False)
    assert r.status_code in (302, 307)
    location = r.headers["location"]
    assert location.startswith("https://slack.com/oauth/v2/authorize")
    assert "client_id=test-reader-client-id" in location
    assert "user_scope=" in location
    assert "&scope=" not in location
    assert "state=" in location

    state = location.split("state=")[1].split("&")[0]
    assert _verify_state(state) == manager_id


def test_callback_tampered_state_is_400(client):
    r = client.get("/auth/slack/callback", params={"code": "abc123", "state": "totally-not-signed"})
    assert r.status_code == 400


def test_callback_missing_code_is_400(client):
    r = client.get("/auth/slack/callback", params={"state": "irrelevant"})
    assert r.status_code == 400


def test_callback_creates_reader_installation(client, monkeypatch):
    manager_id = _login(client)

    from app.controlplane import slack_auth as slack_auth_module

    class FakeResponse:
        def json(self):
            return {
                "ok": True,
                "team": {"id": "T12345", "name": "Acme Corp"},
                "authed_user": {"id": "U_MANAGER_SLACK", "access_token": "xoxp-fake"},
            }

    monkeypatch.setattr(slack_auth_module.httpx, "post", lambda *a, **kw: FakeResponse())

    state = slack_auth_module._sign_state(manager_id)
    r = client.get("/auth/slack/callback", params={"code": "abc123", "state": state}, follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "Acme" in r.headers["location"]

    db = ControlPlaneSessionLocal()
    try:
        installation = db.get(SlackReaderInstallation, manager_id)
        assert installation is not None
        assert installation.team_id == "T12345"
        assert installation.user_token == "xoxp-fake"
        assert installation.user_id == "U_MANAGER_SLACK"
    finally:
        db.close()


def test_callback_without_authed_user_is_400(client, monkeypatch):
    manager_id = _login(client)

    from app.controlplane import slack_auth as slack_auth_module

    class FakeResponse:
        def json(self):
            return {"ok": True, "team": {"id": "T_NO_USER_GRANT", "name": "Acme Corp"}}

    monkeypatch.setattr(slack_auth_module.httpx, "post", lambda *a, **kw: FakeResponse())

    state = slack_auth_module._sign_state(manager_id)
    r = client.get("/auth/slack/callback", params={"code": "abc123", "state": state})
    assert r.status_code == 400

    db = ControlPlaneSessionLocal()
    try:
        assert db.get(SlackReaderInstallation, manager_id) is None
    finally:
        db.close()


def test_callback_syncs_manager_teammember_slack_handle(client, monkeypatch, db_session):
    """authed_user.id is the manager's own Slack user id -- without writing
    it onto their TeamMember row, DM-participant resolution can never
    recognize the manager (see SlackConnector._resolve_dm_other_participant)."""
    from app.database import TeamMember

    manager_id = client.manager_id
    db_session.add(TeamMember(id="U_HARRY_2", name="Shivam", role="Manager", slack_handle=None))
    db_session.commit()

    from app.controlplane import slack_auth as slack_auth_module

    class FakeResponse:
        def json(self):
            return {
                "ok": True, "team": {"id": "T_SYNC", "name": "Acme"},
                "authed_user": {"id": "U_MANAGER_REAL", "access_token": "xoxp-fake"},
            }

    monkeypatch.setattr(slack_auth_module.httpx, "post", lambda *a, **kw: FakeResponse())

    state = slack_auth_module._sign_state(manager_id)
    client.get("/auth/slack/callback", params={"code": "abc123", "state": state}, follow_redirects=False)

    from app.integrations.base import get_manager
    db_session.expire_all()
    manager_member = get_manager(db_session)
    assert manager_member.slack_handle == "U_MANAGER_REAL"


def test_disconnect_deletes_reader_installation(client, monkeypatch):
    manager_id = _login(client)

    db = ControlPlaneSessionLocal()
    try:
        db.add(SlackReaderInstallation(
            manager_id=manager_id, team_id="T_DISCONNECT_ME", team_name="Acme",
            user_token="xoxp-fake", user_id="U_X",
        ))
        db.commit()
    finally:
        db.close()

    from app.controlplane import slack_auth as slack_auth_module
    monkeypatch.setattr(slack_auth_module.httpx, "post", lambda *a, **kw: type("R", (), {"json": lambda self: {"ok": True}})())

    r = client.post("/auth/slack/disconnect")
    assert r.status_code == 200

    db = ControlPlaneSessionLocal()
    try:
        assert db.get(SlackReaderInstallation, manager_id) is None
    finally:
        db.close()


def test_disconnect_with_no_installation_is_a_noop(client):
    _login(client)
    r = client.post("/auth/slack/disconnect")
    assert r.status_code == 200


# ── Agent claim is independent of reader connect ─────────────────────────

def test_install_does_not_require_a_claimed_agent(client):
    """The core of the 2026-07-23 redesign: reading works whether or not
    this manager has ever claimed a pool bot."""
    manager_id = _login(client)

    from app.controlplane.models import Agent as AgentModel
    db = ControlPlaneSessionLocal()
    try:
        assert db.query(AgentModel).filter(AgentModel.manager_id == manager_id).first() is None
    finally:
        db.close()

    r = client.get("/auth/slack/install", follow_redirects=False)
    assert r.status_code in (302, 307)


# ── Webhook (unaffected by the reader/agent split) ───────────────────────

def test_webhook_routes_by_api_app_id(client):
    manager_id = _login(client)
    _seed_installed_agent(agent_id="atlas", app_id="A_KNOWN", manager_id=manager_id)

    client.post("/api/team", json={
        "id": "U_MANAGER", "name": "Shivam", "role": "Manager", "slack_handle": "U_MANAGER"
    })

    payload = {
        "team_id": "T_KNOWN",
        "api_app_id": "A_KNOWN",
        "type": "event_callback",
        "event": {
            "type": "message",
            "client_msg_id": "client_msg_team_known",
            "user": "U_ALICE_123",
            "channel": "D_ALICE_MANAGER",
            "channel_type": "im",
            "text": "hello from a known workspace",
            "ts": "1789030000.000000",
        },
    }
    r = client.post("/api/integrations/slack/webhook", json=payload)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_webhook_unclaimed_agent_is_ignored(client):
    """An installed-but-unclaimed pool bot must stay inert -- a message to
    it does nothing (no manager_id to route the ingest to)."""
    _seed_installed_agent(agent_id="atlas", app_id="A_UNCLAIMED", manager_id=None)

    payload = {
        "team_id": "T_KNOWN",
        "api_app_id": "A_UNCLAIMED",
        "type": "event_callback",
        "event": {
            "type": "message",
            "client_msg_id": "client_msg_unclaimed",
            "user": "U_ALICE_123",
            "channel": "D_ALICE_BOT",
            "channel_type": "im",
            "text": "hello to an unclaimed bot",
            "ts": "1789030222.000000",
        },
    }
    r = client.post("/api/integrations/slack/webhook", json=payload)
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"
    assert r.json()["detail"] == "unclaimed agent"


def test_webhook_unknown_api_app_id_is_ignored(client):
    payload = {
        "team_id": "T_UNKNOWN",
        "api_app_id": "A_UNKNOWN",
        "type": "event_callback",
        "event": {
            "type": "message",
            "client_msg_id": "client_msg_team_unknown",
            "user": "U_ALICE_123",
            "channel": "D_ALICE_MANAGER",
            "channel_type": "im",
            "text": "hello from an unregistered app",
            "ts": "1789030111.000000",
        },
    }
    r = client.post("/api/integrations/slack/webhook", json=payload)
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"
    assert r.json()["detail"] == "unknown agent"


def test_webhook_missing_api_app_id_is_ignored(client):
    payload = {
        "team_id": "T_KNOWN",
        "type": "event_callback",
        "event": {"type": "message", "user": "U_X", "channel": "D_1", "channel_type": "im", "text": "hi", "ts": "1.0"},
    }
    r = client.post("/api/integrations/slack/webhook", json=payload)
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"
    assert r.json()["detail"] == "missing api_app_id"
