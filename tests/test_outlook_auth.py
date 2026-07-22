import msal
import msal.authority
import pytest

from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Manager, OutlookInstallation
from app.controlplane.outlook_auth import _sign_state, _verify_state


@pytest.fixture(autouse=True)
def outlook_env(monkeypatch):
    monkeypatch.setenv("MS_GRAPH_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("MS_GRAPH_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("MS_GRAPH_TENANT_ID", "test-tenant-id")

    # MSAL's ConfidentialClientApplication always does a live OIDC tenant
    # discovery call on construction, even just to build the authorize URL --
    # there's no flag to skip it. Stub it with a canned response so tests
    # never touch the real network, same "no live calls in tests" convention
    # as the rest of app/integrations/.
    def fake_tenant_discovery(tenant_discovery_endpoint, http_client, **kwargs):
        return {
            "authorization_endpoint": "https://login.microsoftonline.com/test-tenant-id/oauth2/v2.0/authorize",
            "token_endpoint": "https://login.microsoftonline.com/test-tenant-id/oauth2/v2.0/token",
            "issuer": "https://login.microsoftonline.com/test-tenant-id/v2.0",
        }

    monkeypatch.setattr(msal.authority, "tenant_discovery", fake_tenant_discovery)


def test_login_redirects_to_microsoft_authorize_endpoint(client):
    r = client.get("/auth/outlook/login", follow_redirects=False)
    assert r.status_code in (302, 307)
    location = r.headers["location"]
    assert location.startswith("https://login.microsoftonline.com/test-tenant-id/oauth2/v2.0/authorize")
    assert "client_id=test-client-id" in location
    assert "state=" in location
    # Login only requests base scopes -- Mail.Send is a later, optional grant.
    assert "Mail.Send" not in location


def test_state_roundtrip():
    state = _sign_state("login")
    purpose, manager_id = _verify_state(state)
    assert purpose == "login"
    assert manager_id is None

    state2 = _sign_state("enable_send", "mgr-123")
    purpose2, manager_id2 = _verify_state(state2)
    assert purpose2 == "enable_send"
    assert manager_id2 == "mgr-123"


def test_callback_mismatched_state_is_400(client):
    db = ControlPlaneSessionLocal()
    try:
        managers_before = db.query(Manager).count()
    finally:
        db.close()

    r = client.get("/auth/outlook/callback", params={"code": "abc123", "state": "not-a-real-signed-state"})
    assert r.status_code == 400

    db = ControlPlaneSessionLocal()
    try:
        # No NEW manager created by the tampered callback attempt -- the
        # `client` fixture's own throwaway manager (from dev-login) already
        # accounts for managers_before.
        assert db.query(Manager).count() == managers_before
    finally:
        db.close()


def test_callback_missing_state_is_400(client):
    r = client.get("/auth/outlook/callback", params={"code": "abc123"})
    assert r.status_code == 400


def test_callback_provider_error_is_400(client):
    r = client.get("/auth/outlook/callback", params={"error": "access_denied", "error_description": "user cancelled"})
    assert r.status_code == 400


def _mock_successful_exchange(monkeypatch, email="alice@company.com", name="Alice"):
    def fake_acquire(self, code, scopes=None, redirect_uri=None):
        return {"access_token": "fake-access-token", "refresh_token": "fake-refresh-token"}

    monkeypatch.setattr(msal.ConfidentialClientApplication, "acquire_token_by_authorization_code", fake_acquire)

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"mail": email, "displayName": name}

        text = "{}"

    import app.controlplane.outlook_auth as outlook_auth_module
    monkeypatch.setattr(outlook_auth_module.httpx, "get", lambda *a, **kw: FakeResponse())


def test_callback_creates_manager_and_installation(client, monkeypatch):
    _mock_successful_exchange(monkeypatch, email="alice@company.com", name="Alice")

    state = _sign_state("login")
    r = client.get("/auth/outlook/callback", params={"code": "abc123", "state": state}, follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "harry_session" in r.headers.get("set-cookie", "")

    db = ControlPlaneSessionLocal()
    try:
        manager = db.query(Manager).filter(Manager.email == "alice@company.com").first()
        assert manager is not None
        assert manager.name == "Alice"

        installation = db.get(OutlookInstallation, manager.id)
        assert installation is not None
        assert installation.mailbox_email == "alice@company.com"
        assert installation.token_cache_json is not None
        assert installation.granted_scopes == "Mail.Read,User.Read"
    finally:
        db.close()


def test_callback_twice_reuses_same_manager(client, monkeypatch):
    _mock_successful_exchange(monkeypatch, email="bob@company.com", name="Bob")

    r1 = client.get("/auth/outlook/callback", params={"code": "abc123", "state": _sign_state("login")}, follow_redirects=False)
    assert r1.status_code in (302, 307)

    r2 = client.get("/auth/outlook/callback", params={"code": "def456", "state": _sign_state("login")}, follow_redirects=False)
    assert r2.status_code in (302, 307)

    db = ControlPlaneSessionLocal()
    try:
        managers = db.query(Manager).filter(Manager.email == "bob@company.com").all()
        assert len(managers) == 1
        installations = db.query(OutlookInstallation).filter(OutlookInstallation.manager_id == managers[0].id).all()
        assert len(installations) == 1
    finally:
        db.close()


def test_enable_send_without_installation_is_400(client):
    r = client.get("/auth/outlook/enable-send", follow_redirects=False)
    assert r.status_code == 400


def test_enable_send_flow_grants_mail_send(client, monkeypatch):
    _mock_successful_exchange(monkeypatch, email="carol@company.com", name="Carol")
    r = client.get("/auth/outlook/callback", params={"code": "abc123", "state": _sign_state("login")}, follow_redirects=False)
    assert r.status_code in (302, 307)

    db = ControlPlaneSessionLocal()
    try:
        manager = db.query(Manager).filter(Manager.email == "carol@company.com").first()
    finally:
        db.close()

    r2 = client.get("/auth/outlook/enable-send", follow_redirects=False)
    assert r2.status_code in (302, 307)
    assert "Mail.Send" in r2.headers["location"]
    # prompt=consent forces the actual permission screen to show even when
    # a prior consent exists (step 20 follow-up, Shivam's report)
    assert "prompt=consent" in r2.headers["location"]

    r3 = client.get(
        "/auth/outlook/callback",
        params={"code": "send-code", "state": _sign_state("enable_send", manager.id)},
        follow_redirects=False,
    )
    assert r3.status_code in (302, 307)
    assert "send_enabled=1" in r3.headers["location"]

    db = ControlPlaneSessionLocal()
    try:
        installation = db.get(OutlookInstallation, manager.id)
        assert installation.granted_scopes == "Mail.Read,User.Read,Mail.Send"
    finally:
        db.close()


def test_revoke_send_drops_scope_and_gates_sending(client, monkeypatch):
    """Our-side send revoke: Mail.Send leaves granted_scopes, the
    connections API reports send_enabled=False again, and the outbound
    send gate (OutlookConnector.send_allowed) closes -- read untouched."""
    _mock_successful_exchange(monkeypatch, email="erin@company.com", name="Erin")
    r = client.get("/auth/outlook/callback", params={"code": "abc123", "state": _sign_state("login")}, follow_redirects=False)
    assert r.status_code in (302, 307)

    db = ControlPlaneSessionLocal()
    try:
        manager = db.query(Manager).filter(Manager.email == "erin@company.com").first()
    finally:
        db.close()

    r2 = client.get(
        "/auth/outlook/callback",
        params={"code": "send-code", "state": _sign_state("enable_send", manager.id)},
        follow_redirects=False,
    )
    assert r2.status_code in (302, 307)

    from app.integrations.outlook import connector as outlook_connector
    assert outlook_connector.send_allowed(manager.id) is True

    r3 = client.post("/auth/outlook/revoke-send")
    assert r3.status_code == 200

    db = ControlPlaneSessionLocal()
    try:
        installation = db.get(OutlookInstallation, manager.id)
        assert installation.granted_scopes == "Mail.Read,User.Read"
    finally:
        db.close()
    assert outlook_connector.send_allowed(manager.id) is False

    conn = client.get("/api/auth/connections").json()
    assert conn["outlook"]["connected"] is True
    assert conn["outlook"]["send_enabled"] is False


def test_revoke_send_without_installation_is_400(client):
    assert client.post("/auth/outlook/revoke-send").status_code == 400


def test_disconnect_removes_installation(client, monkeypatch):
    _mock_successful_exchange(monkeypatch, email="dave@company.com", name="Dave")
    r = client.get("/auth/outlook/callback", params={"code": "abc123", "state": _sign_state("login")}, follow_redirects=False)
    assert r.status_code in (302, 307)

    # Reuse the client's existing session cookie (still the throwaway
    # fixture manager, not dave) is fine here -- disconnect only affects
    # the currently-logged-in manager's own installation, and the fixture
    # manager has none, so this proves disconnect is a no-op when there's
    # nothing to disconnect. A real per-manager disconnect is covered by
    # dev-logging-in as dave separately below.
    db = ControlPlaneSessionLocal()
    try:
        dave = db.query(Manager).filter(Manager.email == "dave@company.com").first()
    finally:
        db.close()

    login_resp = client.post("/api/auth/dev-login", json={"email": "dave@company.com", "name": "Dave"})
    assert login_resp.status_code == 200
    assert login_resp.json()["id"] == dave.id

    r2 = client.post("/auth/outlook/disconnect")
    assert r2.status_code == 200

    db = ControlPlaneSessionLocal()
    try:
        assert db.get(OutlookInstallation, dave.id) is None
    finally:
        db.close()
