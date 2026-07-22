import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Agent
from app.controlplane.slack_auth import _sign_state, _verify_state


def _login(client, email="manager@company.com", name="Manager"):
    r = client.post("/api/auth/dev-login", json={"email": email, "name": name})
    assert r.status_code == 200
    return r.json()["id"]


def _seed_and_claim_agent(client, manager_id, agent_id="atlas", app_id="A_TEST"):
    """Install requires a claimed agent now (step 17 piece 2b) -- seed one
    and claim it via the real claim endpoint."""
    db = ControlPlaneSessionLocal()
    try:
        db.add(Agent(
            id=agent_id, name="Atlas", slack_app_id=app_id,
            slack_client_id="test-slack-client-id", slack_client_secret="test-slack-client-secret",
            slack_signing_secret="test-signing-secret",
        ))
        db.commit()
    finally:
        db.close()

    r = client.post("/api/agents/claim", json={"agent_id": agent_id})
    assert r.status_code == 200
    return agent_id


def test_install_without_session_is_401(clean_controlplane_db):
    bare_client = TestClient(app)
    r = bare_client.get("/auth/slack/install", follow_redirects=False)
    assert r.status_code == 401


def test_install_without_claimed_agent_is_404(client):
    _login(client)
    r = client.get("/auth/slack/install", follow_redirects=False)
    assert r.status_code == 404


def test_install_with_claimed_agent_redirects_with_signed_state(client):
    manager_id = _login(client)
    _seed_and_claim_agent(client, manager_id)

    r = client.get("/auth/slack/install", follow_redirects=False)
    assert r.status_code in (302, 307)
    location = r.headers["location"]
    assert location.startswith("https://slack.com/oauth/v2/authorize")
    assert "client_id=test-slack-client-id" in location
    assert "user_scope=im%3Ahistory%2Cim%3Aread" in location or "user_scope=im:history,im:read" in location
    assert "state=" in location

    state = location.split("state=")[1].split("&")[0]
    assert _verify_state(state, "test-slack-client-secret") == manager_id


def test_callback_tampered_state_is_400(client):
    r = client.get("/auth/slack/callback", params={"code": "abc123", "state": "totally-not-signed"})
    assert r.status_code == 400


def test_callback_missing_code_is_400(client):
    r = client.get("/auth/slack/callback", params={"state": "irrelevant"})
    assert r.status_code == 400


def test_callback_state_for_manager_with_no_claimed_agent_is_400(client):
    manager_id = _login(client)
    # Sign with a bogus secret since there's no claimed agent to fetch a
    # real one from -- the callback should reject on "no agent claimed"
    # before it even gets to signature verification.
    state = _sign_state(manager_id, "whatever")
    r = client.get("/auth/slack/callback", params={"code": "abc123", "state": state})
    assert r.status_code == 400


def test_callback_creates_installation_on_claimed_agent(client, monkeypatch):
    manager_id = _login(client)
    agent_id = _seed_and_claim_agent(client, manager_id)

    from app.controlplane import slack_auth as slack_auth_module

    class FakeResponse:
        def json(self):
            return {
                "ok": True,
                "access_token": "xoxb-fake",
                "team": {"id": "T12345", "name": "Acme Corp"},
                "authed_user": {"id": "U_MANAGER_SLACK", "access_token": "xoxp-fake", "scope": "im:history,im:read"},
            }

    monkeypatch.setattr(slack_auth_module.httpx, "post", lambda *a, **kw: FakeResponse())

    state = slack_auth_module._sign_state(manager_id, "test-slack-client-secret")
    r = client.get("/auth/slack/callback", params={"code": "abc123", "state": state}, follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "workspace=Acme" in r.headers["location"] or "Acme" in r.headers["location"]

    db = ControlPlaneSessionLocal()
    try:
        agent = db.get(Agent, agent_id)
        assert agent.team_id == "T12345"
        assert agent.bot_token == "xoxb-fake"
        assert agent.user_token == "xoxp-fake"
        assert agent.user_id == "U_MANAGER_SLACK"
        assert agent.installed_at is not None
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

    _seed_and_claim_agent(client, manager_id)

    from app.controlplane import slack_auth as slack_auth_module

    class FakeResponse:
        def json(self):
            return {
                "ok": True, "access_token": "xoxb-fake", "team": {"id": "T_SYNC", "name": "Acme"},
                "authed_user": {"id": "U_MANAGER_REAL", "access_token": "xoxp-fake"},
            }

    monkeypatch.setattr(slack_auth_module.httpx, "post", lambda *a, **kw: FakeResponse())

    state = slack_auth_module._sign_state(manager_id, "test-slack-client-secret")
    client.get("/auth/slack/callback", params={"code": "abc123", "state": state}, follow_redirects=False)

    from app.integrations.base import get_manager
    db_session.expire_all()
    manager_member = get_manager(db_session)
    assert manager_member.slack_handle == "U_MANAGER_REAL"


def test_callback_without_authed_user_leaves_user_token_null(client, monkeypatch):
    manager_id = _login(client)
    agent_id = _seed_and_claim_agent(client, manager_id, agent_id="no-user-grant", app_id="A_NO_USER_GRANT")

    from app.controlplane import slack_auth as slack_auth_module

    class FakeResponse:
        def json(self):
            return {"ok": True, "access_token": "xoxb-fake", "team": {"id": "T_NO_USER_GRANT", "name": "Acme Corp"}}

    monkeypatch.setattr(slack_auth_module.httpx, "post", lambda *a, **kw: FakeResponse())

    state = slack_auth_module._sign_state(manager_id, "test-slack-client-secret")
    client.get("/auth/slack/callback", params={"code": "abc123", "state": state}, follow_redirects=False)

    db = ControlPlaneSessionLocal()
    try:
        agent = db.get(Agent, agent_id)
        assert agent.bot_token == "xoxb-fake"
        assert agent.user_token is None
    finally:
        db.close()


def test_disconnect_clears_install_fields_but_keeps_claim(client, monkeypatch):
    manager_id = _login(client)
    agent_id = _seed_and_claim_agent(client, manager_id)

    db = ControlPlaneSessionLocal()
    try:
        agent = db.get(Agent, agent_id)
        agent.team_id = "T_DISCONNECT_ME"
        agent.bot_token = "xoxb-fake"
        agent.user_token = "xoxp-fake"
        agent.user_id = "U_X"
        db.commit()
    finally:
        db.close()

    from app.controlplane import slack_auth as slack_auth_module
    monkeypatch.setattr(slack_auth_module.httpx, "post", lambda *a, **kw: type("R", (), {"json": lambda self: {"ok": True}})())

    r = client.post("/auth/slack/disconnect")
    assert r.status_code == 200

    db = ControlPlaneSessionLocal()
    try:
        agent = db.get(Agent, agent_id)
        assert agent is not None  # claim itself survives disconnect
        assert agent.manager_id == manager_id
        assert agent.bot_token is None
        assert agent.user_token is None
        assert agent.team_id is None
    finally:
        db.close()


def test_webhook_routes_by_api_app_id(client, monkeypatch):
    manager_id = _login(client)
    _seed_and_claim_agent(client, manager_id, agent_id="atlas", app_id="A_KNOWN")

    db = ControlPlaneSessionLocal()
    try:
        agent = db.get(Agent, "atlas")
        agent.team_id = "T_KNOWN"
        agent.bot_token = "xoxb-fake"
        db.commit()
    finally:
        db.close()

    client.post("/api/team", json={
        "id": "U_MANAGER", "name": "Shivam", "role": "Manager", "slack_handle": "U_MANAGER"
    })
    client.post("/api/projectkb/tracked-contacts", json={
        "label": "Alice Developer", "slack_pattern": "U_ALICE_123"
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
