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
import uuid
from fastapi.testclient import TestClient

from app.main import app


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

    from app.tenancy.paths import manager_dir

    shutil.rmtree(manager_dir(test_client.manager_id), ignore_errors=True)


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
