import os

# Must happen before any app.* import -- app.controlplane.models reads this
# env var at module-import time to decide which sqlite file to bind its
# engine to. Without this, the test suite's per-test wipe-and-recreate cycle
# (see clean_controlplane_db below) runs against the SAME file a real dev/
# prod run would use, silently deleting real signed-in sessions and OAuth
# installations every time the suite runs.
os.environ["CONTROLPLANE_DB_FILENAME"] = "test_controlplane.sqlite"
os.environ["JOB_STATE_FILENAME"] = "test_job_state.json"
# 0 = disabled (app.agent.rate_limit.RateLimiter is a no-op below 1) -- the
# test suite makes hundreds of fake-transport LLM calls per run and must
# never actually sleep waiting for a rate-limit slot.
os.environ["LLM_MAX_CALLS_PER_MINUTE"] = "0"

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
    """Every manager-scoped route needs a logged-in manager (per-manager
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
    from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Employee
    from app.tenancy.paths import manager_dir

    db = ControlPlaneSessionLocal()
    try:
        manager_ids = [e.id for e in db.query(Employee.id).filter(Employee.is_manager == True).all()]  # noqa: E712
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
    """The Employee row matching the logged-in test manager's own email --
    needed by any test that exercises the personal agent's target='manager'
    resolution (app.agent.tools._resolve_target_employee_id), which looks
    up the manager's own Employee row by email the same way
    app.api.project_detail._my_employee_ids does. dev-login
    (permissive, test/bootstrap-only unlike real Outlook login) upserts
    this row itself -- this fixture just fills in the role/slack_id fields
    that login doesn't set, rather than inserting a fresh row (which would
    collide on the unique email)."""
    from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, get_employee_by_manager_id

    db = ControlPlaneSessionLocal()
    try:
        emp = get_employee_by_manager_id(db, client.manager_id)
        emp.role = "Manager"
        emp.slack_id = "U_TEST_MANAGER"
        db.commit()
        return emp.id
    finally:
        db.close()


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


# -----------------------------------------------------------------------------
# Step 38: shared fake-Gemini-transport idiom for the agentic KB job tests
# (heartbeat/dream/lint/synthesis). Previously copy-pasted verbatim into
# test_heartbeat_user.py/test_heartbeat_project_fanout.py/test_dream_job.py --
# centralized here so every job test file scripts the same shape the same
# way. `GeminiClient(api_key="fake-key", transport=FakeTransport([...]))` is
# the injection point; a `response_dicts` entry that IS an Exception instance
# is raised instead of returned, for LLM-failure tests.
# -----------------------------------------------------------------------------


class FakeTransport:
    def __init__(self, response_dicts):
        self.response_dicts = response_dicts
        self.calls = []
        self.call_count = 0

    def __call__(self, url, json_payload):
        self.calls.append((url, json_payload))
        res = self.response_dicts[self.call_count]
        self.call_count += 1
        if isinstance(res, Exception):
            raise res
        return res


def _gemini_response_text(text):
    """A plain-text turn -- when this is the LAST scripted response in a
    run, the agent loop ends with stop_reason="final" and .reply == text."""
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]}


def _gemini_tool_call_response(name, args):
    """A turn that calls exactly one tool. The runner loop keeps going after
    this (echoes the call, executes the tool, sends the functionResponse
    back for the next turn) -- a script needs a following response (usually
    _gemini_response_text("done")) to end the run, or another tool call to
    chain a second write."""
    return {
        "candidates": [
            {
                "content": {"parts": [{"functionCall": {"name": name, "args": args}}]},
                "finishReason": "STOP",
            }
        ]
    }
