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


def test_redeem_without_session_is_401(clean_controlplane_db):
    from fastapi.testclient import TestClient
    from app.main import app

    bare_client = TestClient(app)
    r = bare_client.post("/api/agents/redeem", json={"code": "test-access-code"})
    assert r.status_code == 401


def test_redeem_wrong_code_is_403(client):
    _login(client)
    r = client.post("/api/agents/redeem", json={"code": "wrong-code"})
    assert r.status_code == 403


def test_redeem_lists_only_unassigned_agents(client):
    manager_id = _login(client)
    _seed_agent("atlas", "Atlas")
    _seed_agent("nova", "Nova", manager_id="some-other-manager")

    r = client.post("/api/agents/redeem", json={"code": "test-access-code"})
    assert r.status_code == 200
    ids = [a["id"] for a in r.json()["agents"]]
    assert ids == ["atlas"]


def test_claim_unknown_agent_is_404(client):
    _login(client)
    r = client.post("/api/agents/claim", json={"agent_id": "does-not-exist"})
    assert r.status_code == 404


def test_claim_sets_manager_id_and_writes_mirror(client, db_session):
    # client is already logged in as its own throwaway manager (see the
    # `client` fixture) -- db_session is bound to THAT manager_id, so this
    # must not re-login as a different email (that would create a second,
    # unrelated manager and db_session would look at the wrong db.sqlite).
    manager_id = client.manager_id
    _seed_agent("atlas", "Atlas")

    r = client.post("/api/agents/claim", json={"agent_id": "atlas"})
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

    r = client.post("/api/agents/claim", json={"agent_id": "atlas"})
    assert r.status_code == 409


def test_claim_second_agent_by_same_manager_is_409(client):
    _login(client)
    _seed_agent("atlas", "Atlas")
    _seed_agent("nova", "Nova")

    r1 = client.post("/api/agents/claim", json={"agent_id": "atlas"})
    assert r1.status_code == 200

    r2 = client.post("/api/agents/claim", json={"agent_id": "nova"})
    assert r2.status_code == 409


def test_reclaiming_own_agent_is_idempotent(client):
    _login(client)
    _seed_agent("atlas", "Atlas")

    r1 = client.post("/api/agents/claim", json={"agent_id": "atlas"})
    assert r1.status_code == 200

    r2 = client.post("/api/agents/claim", json={"agent_id": "atlas"})
    assert r2.status_code == 200
    assert r2.json() == {"agent_id": "atlas", "agent_name": "Atlas"}
