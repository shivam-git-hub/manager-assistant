import pytest

from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Agent


@pytest.fixture(autouse=True)
def access_code_env(monkeypatch):
    monkeypatch.setenv("AGENT_POOL_ACCESS_CODE", "test-access-code")
    import app.controlplane.agents as agents_module
    monkeypatch.setattr(agents_module, "AGENT_POOL_ACCESS_CODE", "test-access-code")


def _seed_agent(agent_id="atlas", name="Atlas", manager_id=None):
    db = ControlPlaneSessionLocal()
    try:
        db.add(Agent(
            id=agent_id, name=name,
            slack_app_id=f"A_{agent_id}", slack_client_id="cid", slack_client_secret="csecret",
            slack_signing_secret="ssecret", manager_id=manager_id,
        ))
        db.commit()
    finally:
        db.close()


def _login(client, email="manager@company.com", name="Manager"):
    r = client.post("/api/auth/dev-login", json={"email": email, "name": name})
    assert r.status_code == 200
    return r.json()["id"]


def _claim(client, agent_id, code="test-access-code"):
    return client.post("/api/agents/claim", json={"agent_id": agent_id, "code": code})


def test_available_without_session_is_401(clean_controlplane_db):
    from fastapi.testclient import TestClient
    from app.main import app

    bare_client = TestClient(app)
    r = bare_client.get("/api/agents/available")
    assert r.status_code == 401


def test_available_lists_only_unassigned_agents_no_code_needed(client):
    """Shivam 2026-07-23: users see the available agents WITHOUT the code;
    the code gates the claim, not the view."""
    _login(client)
    _seed_agent("atlas", "Atlas")
    _seed_agent("nova", "Nova", manager_id="some-other-manager")

    r = client.get("/api/agents/available")
    assert r.status_code == 200
    ids = [a["id"] for a in r.json()["agents"]]
    assert ids == ["atlas"]


def test_claim_wrong_code_is_403(client):
    _login(client)
    _seed_agent("atlas", "Atlas")
    r = _claim(client, "atlas", code="wrong-code")
    assert r.status_code == 403

    # and the agent must remain unclaimed
    db = ControlPlaneSessionLocal()
    try:
        assert db.get(Agent, "atlas").manager_id is None
    finally:
        db.close()


def test_claim_unknown_agent_is_404(client):
    _login(client)
    r = _claim(client, "does-not-exist")
    assert r.status_code == 404


def test_claim_sets_manager_id_and_writes_mirror(client, db_session):
    # client is already logged in as its own throwaway manager (see the
    # `client` fixture) -- db_session is bound to THAT manager_id, so this
    # must not re-login as a different email (that would create a second,
    # unrelated manager and db_session would look at the wrong db.sqlite).
    manager_id = client.manager_id
    _seed_agent("atlas", "Atlas")

    r = _claim(client, "atlas")
    assert r.status_code == 200
    assert r.json() == {"agent_id": "atlas", "agent_name": "Atlas"}

    db = ControlPlaneSessionLocal()
    try:
        agent = db.get(Agent, "atlas")
        assert agent.manager_id == manager_id
        assert agent.claimed_at is not None
    finally:
        db.close()

    from app.database import AgentAssignment
    mirror = db_session.get(AgentAssignment, "atlas")
    assert mirror is not None
    assert mirror.agent_name == "Atlas"


def test_claim_already_claimed_by_someone_else_is_409(client):
    _login(client)
    _seed_agent("atlas", "Atlas", manager_id="already-owns-it")

    r = _claim(client, "atlas")
    assert r.status_code == 409


def test_claim_second_agent_by_same_manager_is_409(client):
    _login(client)
    _seed_agent("atlas", "Atlas")
    _seed_agent("nova", "Nova")

    r1 = _claim(client, "atlas")
    assert r1.status_code == 200

    r2 = _claim(client, "nova")
    assert r2.status_code == 409


def test_reclaiming_own_agent_is_idempotent(client):
    _login(client)
    _seed_agent("atlas", "Atlas")

    r1 = _claim(client, "atlas")
    assert r1.status_code == 200

    r2 = _claim(client, "atlas")
    assert r2.status_code == 200
    assert r2.json() == {"agent_id": "atlas", "agent_name": "Atlas"}


def test_mine_without_session_is_401(clean_controlplane_db):
    from fastapi.testclient import TestClient
    from app.main import app

    bare_client = TestClient(app)
    r = bare_client.get("/api/agents/mine")
    assert r.status_code == 401


def test_mine_is_null_before_claiming(client):
    _login(client)
    r = client.get("/api/agents/mine")
    assert r.status_code == 200
    assert r.json() is None


def test_mine_reflects_claim_and_admin_installation(client):
    _seed_agent("atlas", "Atlas")

    r = _claim(client, "atlas")
    assert r.status_code == 200

    r2 = client.get("/api/agents/mine")
    body = r2.json()
    assert body["agent_id"] == "atlas"
    assert body["agent_name"] == "Atlas"
    assert body["installed"] is False

    # Admin installs it out of band (scripts/seed_agents.py) -- a plain DB
    # write, no OAuth call in this codebase touches this row.
    db = ControlPlaneSessionLocal()
    try:
        agent = db.get(Agent, "atlas")
        agent.bot_token = "xoxb-fake"
        agent.team_id = "T_1"
        db.commit()
    finally:
        db.close()

    r3 = client.get("/api/agents/mine")
    body3 = r3.json()
    assert body3["installed"] is True
    assert body3["team_id"] == "T_1"
