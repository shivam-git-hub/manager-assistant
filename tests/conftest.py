import os

# Must happen before any app.* import -- app.controlplane.models reads this
# env var at module-import time to decide which sqlite file to bind its
# engine to. Without this, the test suite's per-test wipe-and-recreate cycle
# (see clean_controlplane_db below) runs against the SAME file a real dev/
# prod run would use, silently deleting real signed-in sessions and OAuth
# installations every time the suite runs.
os.environ["CONTROLPLANE_DB_FILENAME"] = "test_controlplane.sqlite"

import pytest
import shutil
import time
import uuid
from datetime import datetime, timedelta
from fastapi.testclient import TestClient

from app.main import app
from app.config import IST


@pytest.fixture(autouse=True)
def clean_controlplane_db():
    """The control-plane DB (data/controlplane.sqlite) is its own engine,
    not swapped via dependency_overrides -- reset the file and recreate
    tables around every test so managers/sessions don't leak.
    engine.dispose() before unlinking is required: SQLite + a pooled
    connection still holding the deleted file's inode open otherwise
    produces "attempt to write a readonly database" on the next test."""
    from app.controlplane.models import CONTROLPLANE_DB_PATH, engine, init_controlplane_db

    def _remove():
        engine.dispose()
        if CONTROLPLANE_DB_PATH.exists():
            try:
                CONTROLPLANE_DB_PATH.unlink()
            except OSError:
                pass

    _remove()
    init_controlplane_db()
    yield
    _remove()


@pytest.fixture(scope="function")
def client(clean_controlplane_db):
    """Every manager-scoped route needs a logged-in manager as of step 15's
    per-manager DB split -- there's no more global db.sqlite to fall back
    on. This fixture provisions a fresh throwaway manager (unique email per
    test) via the real dev-login endpoint, which also triggers
    ensure_manager_scaffold() and seeds Harry, then yields a TestClient
    already carrying that manager's session cookie. client.manager_id is
    stashed so other fixtures (db_session) and tests that need the raw id
    can find it without re-parsing cookies."""
    test_client = TestClient(app)
    email = f"test-{uuid.uuid4().hex}@test.local"
    r = test_client.post("/api/auth/dev-login", json={"email": email, "name": "Test Manager"})
    assert r.status_code == 200, r.text
    test_client.manager_id = r.json()["id"]

    yield test_client

    # Tests routinely do *additional* dev-logins beyond this fixture's own
    # (e.g. alice/bob/carol/dave in test_auth.py, "other manager" logins for
    # cross-manager scoping tests) -- each creates its own manager + scaffold
    # dir. Cleaning up only test_client.manager_id leaked hundreds of
    # managers/<id>/ directories over time. Query every manager row that
    # exists in THIS test's controlplane db (about to be wiped by
    # clean_controlplane_db's own teardown anyway) and remove all of their
    # scaffold dirs, not just the first one.
    from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Manager
    from app.tenancy.paths import manager_dir

    db = ControlPlaneSessionLocal()
    try:
        manager_ids = [m.id for m in db.query(Manager.id).all()]
    finally:
        db.close()
    for manager_id in manager_ids:
        shutil.rmtree(manager_dir(manager_id), ignore_errors=True)


@pytest.fixture(scope="function")
def db_session(client):
    """A Session bound directly to the same manager's db.sqlite that
    `client`'s requests write into -- for tests that want to assert on rows
    without going through an HTTP round-trip."""
    from app.tenancy.db import get_manager_session

    session = get_manager_session(client.manager_id)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def manager_employee_id(client):
    """Creates an Employee row matching the logged-in test manager's own
    email -- needed by any test that exercises the personal agent's
    target='manager' resolution (app.agent.tools._resolve_target_employee_id),
    which looks up the manager's own Employee row by email the same way
    app.api.project_detail._my_employee_ids does. Real logins get this via
    the Outlook-login Employee-upsert (see CLAUDE.md's 'Live-testing fixes'
    entry); dev-login does not, so tests that need it create it explicitly."""
    import uuid as _uuid
    from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Manager, Employee

    db = ControlPlaneSessionLocal()
    try:
        manager = db.get(Manager, client.manager_id)
        emp = Employee(
            id=_uuid.uuid4().hex,
            email=manager.email.lower(),
            name=manager.name,
            role="Manager",
            slack_id="U_TEST_MANAGER",
        )
        db.add(emp)
        db.commit()
        return emp.id
    finally:
        db.close()


@pytest.fixture
def set_sim_time(monkeypatch):
    """Test-only clock control. Production app.timeservice.now_ist() tracks
    real wall-clock time only (2026-07-23, sim time removed) -- this fixture
    exists so time-dependent business logic (quiet hours, meeting briefs,
    follow-up lifecycles, health decay, etc.) can still be tested
    deterministically. Call set_sim_time(dt) to freeze now_ist() at dt; it
    keeps flowing naturally with real elapsed time after that (same anchor
    behavior the old sim clock had), so a test that sleeps/advances real
    time mid-test still sees time move forward. Every app module reads
    `timeservice.now_ist()` off the module object at call time (`from app
    import timeservice`, never `from app.timeservice import now_ist`), so
    patching the module attribute here reaches every call site."""
    from app import timeservice

    anchor = {"sim": None, "real": None}

    def _now():
        if anchor["sim"] is None:
            return datetime.now(IST).replace(tzinfo=None)
        return anchor["sim"] + timedelta(seconds=time.time() - anchor["real"])

    def _set(dt: datetime):
        anchor["sim"] = dt
        anchor["real"] = time.time()

    monkeypatch.setattr(timeservice, "now_ist", _now)
    return _set


@pytest.fixture
def cleanup_projects():
    """Tracks project ids created during a test and rmtree's their
    projects/<id>/ dir afterwards -- shared across test modules that create
    real registry projects (real on-disk dirs, not env-redirected, same as
    managers/<id>/)."""
    from app.projects.paths import project_dir

    created = []
    yield created
    for pid in created:
        shutil.rmtree(project_dir(pid), ignore_errors=True)
