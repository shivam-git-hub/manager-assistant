import msal
import msal.authority
import pytest

from app.controlplane.models import (
    SessionLocal as ControlPlaneSessionLocal, Employee, get_employee_by_manager_id,
)
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
    # Login only requests identity (User.Read) -- no mailbox
    # access, no send access, implied by signing in.
    assert "Mail.Send" not in location
    assert "Mail.Read" not in location


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
        managers_before = db.query(Employee).filter(Employee.is_manager == True).count()  # noqa: E712
    finally:
        db.close()

    r = client.get("/auth/outlook/callback", params={"code": "abc123", "state": "not-a-real-signed-state"})
    assert r.status_code == 400

    db = ControlPlaneSessionLocal()
    try:
        # No NEW manager created by the tampered callback attempt -- the
        # `client` fixture's own throwaway manager (from dev-login) already
        # accounts for managers_before.
        assert db.query(Employee).filter(Employee.is_manager == True).count() == managers_before  # noqa: E712
    finally:
        db.close()


def test_callback_missing_state_is_400(client):
    r = client.get("/auth/outlook/callback", params={"code": "abc123"})
    assert r.status_code == 400


def test_callback_provider_error_is_400(client):
    r = client.get("/auth/outlook/callback", params={"error": "access_denied", "error_description": "user cancelled"})
    assert r.status_code == 400


def _seed_employee_if_missing(email: str, name: str) -> None:
    """Real Outlook login rejects any email not already in the
    pre-seeded Employee directory -- tests exercising the login callback
    must seed that row first, same as the admin's manual insert would in
    production."""
    import uuid
    db = ControlPlaneSessionLocal()
    try:
        existing = db.query(Employee).filter(Employee.email == email.lower()).first()
        if existing is None:
            db.add(Employee(id=uuid.uuid4().hex, email=email.lower(), name=name))
            db.commit()
    finally:
        db.close()


def _mock_successful_exchange(monkeypatch, email="alice@company.com", name="Alice"):
    _seed_employee_if_missing(email, name)

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


def test_login_callback_rejects_unrecognized_email(client, monkeypatch):
    """An email not already in the pre-seeded Employee directory
    is rejected outright -- no is_manager flip, no session issued.

    The rejection is a redirect back to the login page carrying
    ?error=not_recognized_employee, NOT a raw 403 JSON body: this endpoint is
    reached by the browser following Microsoft's OAuth redirect, so a JSON
    error has nowhere to render. The frontend Login page reads the query param
    and shows it inline (frontend/src/pages/Login.tsx::ERROR_MESSAGES)."""
    def fake_acquire(self, code, scopes=None, redirect_uri=None):
        return {"access_token": "fake-access-token", "refresh_token": "fake-refresh-token"}

    monkeypatch.setattr(msal.ConfidentialClientApplication, "acquire_token_by_authorization_code", fake_acquire)

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"mail": "stranger@company.com", "displayName": "Stranger"}

        text = "{}"

    import app.controlplane.outlook_auth as outlook_auth_module
    monkeypatch.setattr(outlook_auth_module.httpx, "get", lambda *a, **kw: FakeResponse())

    r = client.get("/auth/outlook/callback", params={"code": "abc123", "state": _sign_state("login")}, follow_redirects=False)
    assert r.status_code == 307
    assert "error=not_recognized_employee" in r.headers["location"]

    db = ControlPlaneSessionLocal()
    try:
        assert db.query(Employee).filter(Employee.email == "stranger@company.com").first() is None
    finally:
        db.close()


def test_login_callback_creates_manager_without_mailbox_credentials(client, monkeypatch):
    """Signing in does not imply a mailbox grant -- the Employee
    row exists (is_manager=True) but carries no Outlook credentials until
    connect-mail runs separately."""
    _mock_successful_exchange(monkeypatch, email="alice@company.com", name="Alice")

    state = _sign_state("login")
    r = client.get("/auth/outlook/callback", params={"code": "abc123", "state": state}, follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "harry_session" in r.headers.get("set-cookie", "")

    db = ControlPlaneSessionLocal()
    try:
        employee = db.query(Employee).filter(Employee.email == "alice@company.com").first()
        assert employee is not None
        assert employee.name == "Alice"
        assert employee.is_manager is True
        assert employee.outlook_mailbox_email is None
        assert employee.outlook_token_cache_json is None
    finally:
        db.close()


def test_connect_mail_requires_login():
    from fastapi.testclient import TestClient
    from app.main import app

    bare_client = TestClient(app)
    r = bare_client.get("/auth/outlook/connect-mail", follow_redirects=False)
    assert r.status_code == 401


def test_connect_mail_writes_credentials_onto_existing_employee(client, monkeypatch):
    _mock_successful_exchange(monkeypatch, email="alice@company.com", name="Alice")
    client.get("/auth/outlook/callback", params={"code": "abc123", "state": _sign_state("login")}, follow_redirects=False)

    db = ControlPlaneSessionLocal()
    try:
        manager = db.query(Employee).filter(Employee.email == "alice@company.com").first()
    finally:
        db.close()

    # Log back in as alice (the login callback already sets the session
    # cookie, but re-dev-login is simplest/most explicit for this test).
    client.post("/api/auth/dev-login", json={"email": "alice@company.com", "name": "Alice"})

    r = client.get("/auth/outlook/connect-mail", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "Mail.Read" in r.headers["location"]
    assert "Mail.Send" not in r.headers["location"]

    r2 = client.get(
        "/auth/outlook/callback",
        params={"code": "mail-code", "state": _sign_state("connect_mail", manager.id)},
        follow_redirects=False,
    )
    assert r2.status_code in (302, 307)

    db = ControlPlaneSessionLocal()
    try:
        employee = get_employee_by_manager_id(db, manager.id)
        assert employee.outlook_mailbox_email == "alice@company.com"
        assert employee.outlook_token_cache_json is not None
        assert employee.outlook_granted_scopes == "Mail.Read"
    finally:
        db.close()


def test_callback_mirrors_manager_into_employee_directory(client, monkeypatch):
    _mock_successful_exchange(monkeypatch, email="alice@company.com", name="Alice")

    client.get("/auth/outlook/callback", params={"code": "abc123", "state": _sign_state("login")}, follow_redirects=False)

    db = ControlPlaneSessionLocal()
    try:
        employee = db.query(Employee).filter(Employee.email == "alice@company.com").first()
        assert employee is not None
        assert employee.name == "Alice"
        assert employee.is_manager is True
    finally:
        db.close()


def test_callback_reuses_existing_employee_row_and_updates_name(client, monkeypatch):
    db = ControlPlaneSessionLocal()
    try:
        import uuid
        pre_existing = Employee(id=uuid.uuid4().hex, email="alice@company.com", name="Old Name", role="Backend Engineer")
        db.add(pre_existing)
        db.commit()
        pre_existing_id = pre_existing.id
    finally:
        db.close()

    _mock_successful_exchange(monkeypatch, email="alice@company.com", name="Alice New Name")
    client.get("/auth/outlook/callback", params={"code": "abc123", "state": _sign_state("login")}, follow_redirects=False)

    db = ControlPlaneSessionLocal()
    try:
        matches = db.query(Employee).filter(Employee.email == "alice@company.com").all()
        assert len(matches) == 1
        assert matches[0].id == pre_existing_id
        assert matches[0].name == "Old Name"  # login no longer overwrites name -- only is_manager
        assert matches[0].role == "Backend Engineer"
        assert matches[0].is_manager is True
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
        employees = db.query(Employee).filter(Employee.email == "bob@company.com").all()
        assert len(employees) == 1
        assert employees[0].is_manager is True
    finally:
        db.close()


def test_enable_send_without_mailbox_connected_is_400(client):
    r = client.get("/auth/outlook/enable-send", follow_redirects=False)
    assert r.status_code == 400


def _login_and_connect_mail(client, monkeypatch, email, name):
    """Shared helper: login then connect-mail, returning the Employee row
    (which IS the manager identity -- step 30)."""
    _mock_successful_exchange(monkeypatch, email=email, name=name)
    client.get("/auth/outlook/callback", params={"code": "abc123", "state": _sign_state("login")}, follow_redirects=False)

    db = ControlPlaneSessionLocal()
    try:
        manager = db.query(Employee).filter(Employee.email == email).first()
    finally:
        db.close()

    client.post("/api/auth/dev-login", json={"email": email, "name": name})
    client.get(
        "/auth/outlook/callback",
        params={"code": "mail-code", "state": _sign_state("connect_mail", manager.id)},
        follow_redirects=False,
    )
    return manager


def test_enable_send_flow_grants_mail_send(client, monkeypatch):
    manager = _login_and_connect_mail(client, monkeypatch, "carol@company.com", "Carol")

    r2 = client.get("/auth/outlook/enable-send", follow_redirects=False)
    assert r2.status_code in (302, 307)
    assert "Mail.Send" in r2.headers["location"]
    # prompt=consent forces the actual permission screen to show even when
    # a prior consent exists
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
        employee = get_employee_by_manager_id(db, manager.id)
        assert employee.outlook_granted_scopes == "Mail.Read,Mail.Send"
    finally:
        db.close()


def test_revoke_send_drops_scope_and_gates_sending(client, monkeypatch):
    """Our-side send revoke: Mail.Send leaves granted_scopes, the
    connections API reports send_enabled=False again, and the outbound
    send gate (OutlookConnector.send_allowed) closes -- read untouched."""
    manager = _login_and_connect_mail(client, monkeypatch, "erin@company.com", "Erin")

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
        employee = get_employee_by_manager_id(db, manager.id)
        assert employee.outlook_granted_scopes == "Mail.Read"
    finally:
        db.close()
    assert outlook_connector.send_allowed(manager.id) is False

    conn = client.get("/api/auth/connections").json()
    assert conn["outlook"]["connected"] is True
    assert conn["outlook"]["send_enabled"] is False


def test_revoke_send_without_mailbox_connected_is_400(client):
    assert client.post("/auth/outlook/revoke-send").status_code == 400


def test_disconnect_clears_mailbox_credentials(client, monkeypatch):
    dave = _login_and_connect_mail(client, monkeypatch, "dave@company.com", "Dave")

    r2 = client.post("/auth/outlook/disconnect")
    assert r2.status_code == 200

    db = ControlPlaneSessionLocal()
    try:
        employee = get_employee_by_manager_id(db, dave.id)
        assert employee.outlook_mailbox_email is None
        assert employee.outlook_token_cache_json is None
        assert employee.outlook_granted_scopes is None
    finally:
        db.close()
