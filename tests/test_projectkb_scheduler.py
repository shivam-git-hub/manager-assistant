import uuid
from datetime import datetime

from app.controlplane.models import (
    SessionLocal as ControlPlaneSessionLocal,
    Employee,
    Agent,
    get_employee_by_manager_id,
)
from app.tenancy.paths import ensure_manager_scaffold, manager_dir
from app.tenancy.db import get_manager_session
from app.projectkb.scheduler import check_and_run_due_jobs


def _make_manager(email_prefix="mgr"):
    manager_id = f"test-{uuid.uuid4().hex}"
    db = ControlPlaneSessionLocal()
    try:
        # Scheduler tests don't exercise login's employee-directory gate --
        # just create the row directly (Employee IS the manager identity,
        # step 30), matching this file's own manager scaffolding (bypasses
        # the real login flow entirely).
        employee = Employee(
            id=manager_id, email=f"{email_prefix}-{manager_id}@test.local", name="Test Manager", is_manager=True,
        )
        db.add(employee)
        db.commit()
    finally:
        db.close()
    ensure_manager_scaffold(manager_id)
    return manager_id


def _cleanup(*manager_ids):
    import shutil
    for mid in manager_ids:
        shutil.rmtree(manager_dir(mid), ignore_errors=True)


def test_job_runs_once_when_due_then_gated_until_interval_elapses(clean_controlplane_db, monkeypatch):
    """Step 32: real cadence gating -- a job runs on the first (always-due)
    tick, is suppressed on an immediate second tick, then runs again once
    its own interval_minutes has elapsed."""
    m1 = _make_manager("always-due")
    try:
        from app import timeservice
        from app.projectkb import scheduler as pkb_scheduler
        from app.projectkb.jobs import ingestion as ingestion_job

        monkeypatch.setattr(pkb_scheduler, "_last_run_at", {})
        monkeypatch.setattr(pkb_scheduler, "_interval_minutes", lambda job_name: 15)

        calls = []
        monkeypatch.setattr(ingestion_job, "run", lambda db, manager_id: calls.append(manager_id) or {"processed": 0})
        monkeypatch.setattr(pkb_scheduler, "_JOBS", {"ingestion": ingestion_job})

        clock = {"now": datetime(2026, 1, 1, 9, 0, 0)}
        monkeypatch.setattr(timeservice, "now_ist", lambda: clock["now"])

        check_and_run_due_jobs()
        assert calls == [m1]

        # Immediate second tick, same instant -- interval hasn't elapsed.
        check_and_run_due_jobs()
        assert calls == [m1]

        # Advance past the 15-minute interval -- due again.
        clock["now"] = datetime(2026, 1, 1, 9, 16, 0)
        check_and_run_due_jobs()
        assert calls == [m1, m1]
    finally:
        _cleanup(m1)


def test_check_and_run_due_jobs_runs_every_manager_in_one_global_pass(clean_controlplane_db, monkeypatch):
    """A due job fans out sequentially over every provisioned manager in a
    single global pass -- not a separate scheduled task per manager."""
    m1 = _make_manager("global-a")
    m2 = _make_manager("global-b")
    try:
        from app.projectkb import scheduler as pkb_scheduler
        from app.projectkb.jobs import ingestion as ingestion_job

        monkeypatch.setattr(pkb_scheduler, "_last_run_at", {})

        calls = []
        monkeypatch.setattr(ingestion_job, "run", lambda db, manager_id: calls.append(manager_id) or {"processed": 0})
        monkeypatch.setattr(pkb_scheduler, "_JOBS", {"ingestion": ingestion_job})

        check_and_run_due_jobs()

        assert set(calls) == {m1, m2}
    finally:
        _cleanup(m1, m2)


def test_outlook_poll_noop_for_manager_without_installation(clean_controlplane_db):
    """A manager who hasn't connected Outlook -> outlook_poll is a no-op,
    not an error."""
    from app.projectkb.jobs import outlook_poll

    m1 = _make_manager("no-outlook")
    try:
        db = get_manager_session(m1)
        try:
            result = outlook_poll.run(db, m1)
        finally:
            db.close()
        assert result == {"fetched": 0, "stored": 0, "skipped": "not_connected"}
    finally:
        _cleanup(m1)


def test_check_and_run_due_jobs_isolates_failures_across_managers(clean_controlplane_db, monkeypatch):
    """A job failing for one manager doesn't stop the loop from processing
    the next manager."""
    m1 = _make_manager("failing")
    m2 = _make_manager("healthy")
    try:
        from app.projectkb import scheduler as pkb_scheduler
        from app.projectkb.jobs import ingestion as ingestion_job

        calls = []

        def fake_run(db, manager_id):
            calls.append(manager_id)
            if manager_id == m1:
                raise RuntimeError("boom")
            return {"processed": 0}

        monkeypatch.setattr(ingestion_job, "run", fake_run)
        monkeypatch.setattr(pkb_scheduler, "_JOBS", {"ingestion": ingestion_job})
        monkeypatch.setattr(pkb_scheduler, "_last_run_at", {})

        check_and_run_due_jobs()

        # Both managers got a call despite m1's job raising.
        assert m1 in calls
        assert m2 in calls
    finally:
        _cleanup(m1, m2)


def test_outlook_poll_uses_specific_manager_token(clean_controlplane_db, monkeypatch):
    """outlook_poll for a manager WITH a connected Outlook mailbox calls
    fetch_since scoped to that manager_id specifically."""
    from app.projectkb.jobs import outlook_poll
    from app.integrations import outlook as outlook_module

    m1 = _make_manager("has-outlook")
    try:
        cp_db = ControlPlaneSessionLocal()
        try:
            employee = get_employee_by_manager_id(cp_db, m1)
            employee.outlook_mailbox_email = "m1@test.local"
            cp_db.commit()
        finally:
            cp_db.close()

        seen = {}

        def fake_fetch_since(since, manager_id=None):
            seen["manager_id"] = manager_id
            return []

        monkeypatch.setattr(outlook_module.connector, "fetch_since", fake_fetch_since)

        db = get_manager_session(m1)
        try:
            result = outlook_poll.run(db, m1)
        finally:
            db.close()

        assert seen["manager_id"] == m1
        assert result["fetched"] == 0

        cp_db = ControlPlaneSessionLocal()
        try:
            employee = get_employee_by_manager_id(cp_db, m1)
            assert employee.outlook_poll_last_success_at is not None
        finally:
            cp_db.close()
    finally:
        _cleanup(m1)


def test_slack_poll_noop_for_manager_without_user_grant(clean_controlplane_db):
    """A manager who hasn't granted a Slack user-token scope -> slack_poll
    is a no-op, not an error (mirrors outlook_poll's no-installation case)."""
    from app.projectkb.jobs import slack_poll

    m1 = _make_manager("no-slack")
    try:
        db = get_manager_session(m1)
        try:
            result = slack_poll.run(db, m1)
        finally:
            db.close()
        assert result == {"fetched": 0, "stored": 0, "skipped": "not_connected"}
    finally:
        _cleanup(m1)


def test_slack_poll_uses_specific_manager_user_token(clean_controlplane_db, monkeypatch):
    """slack_poll for a manager WITH a reader user-token grant calls
    fetch_since scoped to that manager_id specifically -- independent of any
    claimed Agent (a claimed-but-unrelated Agent must not affect this)."""
    from app.projectkb.jobs import slack_poll
    from app.integrations import slack as slack_module

    m1 = _make_manager("has-slack")
    try:
        cp_db = ControlPlaneSessionLocal()
        try:
            cp_db.add(Agent(
                id=f"agent-{m1}", name="Test Agent", slack_app_id=f"A_{m1}",
                slack_client_id="cid", slack_client_secret="csecret", slack_signing_secret="ssecret",
                manager_id=m1, team_id=f"T_{m1}", bot_token="xoxb-fake",
            ))
            employee = get_employee_by_manager_id(cp_db, m1)
            employee.slack_team_id = f"T_{m1}"
            employee.slack_team_name = "Test Workspace"
            employee.slack_user_token = "xoxp-fake"
            employee.slack_id = "U_M1"
            cp_db.commit()
        finally:
            cp_db.close()

        seen = {}

        def fake_fetch_since(since, manager_id=None):
            seen["manager_id"] = manager_id
            return []

        monkeypatch.setattr(slack_module.connector, "fetch_since", fake_fetch_since)

        db = get_manager_session(m1)
        try:
            result = slack_poll.run(db, m1)
        finally:
            db.close()

        assert seen["manager_id"] == m1
        assert result["fetched"] == 0

        cp_db = ControlPlaneSessionLocal()
        try:
            employee = get_employee_by_manager_id(cp_db, m1)
            assert employee.slack_poll_last_success_at is not None
        finally:
            cp_db.close()
    finally:
        _cleanup(m1)


# ─── Step 34: scheduler hardening ───────────────────────────────────────

def test_interval_minutes_returns_safe_fallback_for_unregistered_job(monkeypatch):
    """Defect 1: a job name missing from job_schedule.py's output used to
    return 0 (always due, every 60s tick) behind a single warning -- one
    missing schedule entry silently burned LLM budget forever. Must fall
    back to a safe 60-minute interval instead."""
    from app.projectkb import job_schedule as job_schedule_module
    from app.projectkb import scheduler as pkb_scheduler

    monkeypatch.setattr(job_schedule_module, "load_job_schedule", lambda manager_id=None: {})

    assert pkb_scheduler._interval_minutes("totally_unregistered_job") == 60


def test_load_last_run_at_absent_file_is_first_boot_returns_empty(monkeypatch, tmp_path):
    """A genuinely absent state file (real first boot) must still make
    every job due immediately -- distinct from a CORRUPT file (defect 2)."""
    from app.projectkb import scheduler as pkb_scheduler

    monkeypatch.setattr(pkb_scheduler, "JOB_STATE_PATH", tmp_path / "does_not_exist.json")

    assert pkb_scheduler._load_last_run_at() == {}


def test_load_last_run_at_corrupt_file_stamps_every_job_as_just_run(monkeypatch, tmp_path):
    """Defect 2: a corrupt/truncated job_state.json used to return {} --
    identical to first-boot -- which made _is_due True for every job at
    once (a six-job stampede). A load failure must instead stamp every
    known job as having just run, so a damaged file costs one delayed
    cycle, not a stampede."""
    from app import timeservice
    from app.projectkb import scheduler as pkb_scheduler

    corrupt_path = tmp_path / "corrupt.json"
    corrupt_path.write_text("{not valid json at all", encoding="utf-8")
    monkeypatch.setattr(pkb_scheduler, "JOB_STATE_PATH", corrupt_path)

    clock = datetime(2026, 1, 1, 9, 0, 0)
    monkeypatch.setattr(timeservice, "now_ist", lambda: clock)

    result = pkb_scheduler._load_last_run_at()

    assert set(result.keys()) == set(pkb_scheduler._JOBS.keys())
    assert all(v == clock for v in result.values())


def test_save_last_run_at_is_atomic_on_write_failure(monkeypatch, tmp_path):
    """Defect 3: a crash mid-write used to truncate job_state.json in
    place, feeding defect 2 next boot. Writing to a temp file + os.replace()
    means a failed write leaves the original file completely untouched and
    no stray temp file behind."""
    import json as json_module

    from app.projectkb import scheduler as pkb_scheduler

    state_path = tmp_path / "state.json"
    original_contents = json_module.dumps({"ingestion": "2026-01-01T09:00:00"})
    state_path.write_text(original_contents, encoding="utf-8")

    monkeypatch.setattr(pkb_scheduler, "JOB_STATE_PATH", state_path)
    monkeypatch.setattr(pkb_scheduler, "_last_run_at", {"ingestion": datetime(2026, 1, 1, 10, 0, 0)})

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated disk failure mid-write")

    monkeypatch.setattr(pkb_scheduler.json, "dump", _boom)

    pkb_scheduler._save_last_run_at()  # must not raise -- caught + logged

    assert state_path.read_text(encoding="utf-8") == original_contents
    # No stray .tmp file left behind in the same directory.
    assert list(tmp_path.iterdir()) == [state_path]


def test_held_lock_makes_scheduler_skip_and_manual_endpoint_409(client, monkeypatch):
    """Defect 4: a per-job-name lock, held for the job's global pass --
    the scheduler skips (and logs) a job whose lock is already held, and
    the manual debug-page endpoint gets a 409 instead of hanging/racing."""
    from app.projectkb import scheduler as pkb_scheduler
    from app.projectkb.jobs import ingestion as ingestion_job

    monkeypatch.setattr(pkb_scheduler, "_last_run_at", {})
    monkeypatch.setattr(pkb_scheduler, "_JOBS", {"ingestion": ingestion_job})

    calls = []
    monkeypatch.setattr(ingestion_job, "run", lambda db, manager_id: calls.append(manager_id) or {"processed": 0})

    lock = pkb_scheduler.get_job_lock("ingestion")
    lock.acquire()
    try:
        pkb_scheduler.check_and_run_due_jobs()
        assert calls == []  # scheduler pass skipped this job entirely

        r = client.post("/api/dev/jobs/ingestion/run")
        assert r.status_code == 409
    finally:
        lock.release()

    # Lock released -- the skipped attempt never touched _last_run_at, so
    # the job is still due; both paths work normally again.
    pkb_scheduler.check_and_run_due_jobs()
    assert calls == [client.manager_id]


def test_agent_heartbeat_manual_endpoint_409_when_locked(client, monkeypatch):
    """Same lock guard applies to POST /api/heartbeat/run -- agent_heartbeat
    is deliberately NOT in the scheduler's _JOBS (manual-trigger-only), but
    it can still be triggered from two different manual endpoints
    (this one and the debug page's generic job runner), so it needs the
    same non-blocking-acquire-then-409 protection against a double-run."""
    from app.projectkb import scheduler as pkb_scheduler
    from app.projectkb.enums import JobName

    lock = pkb_scheduler.get_job_lock(JobName.AGENT_HEARTBEAT.value)
    lock.acquire()
    try:
        r = client.post("/api/heartbeat/run")
        assert r.status_code == 409
    finally:
        lock.release()


def test_lint_run_accepts_client_kwarg(db_session, client):
    """Defect 5: lint.run must accept the same client=None third parameter
    every other job module has. Step 37 gave lint a real (no longer stub)
    implementation, which needs a real db session -- full behavioral
    coverage lives in tests/test_lint_job.py, this just pins the signature."""
    from app.projectkb.jobs import lint

    result = lint.run(db_session, client.manager_id, client=None)

    assert result["issues_found"] == 0
    assert result["coherence_ran"] is False


def test_slack_poll_noop_without_reader_installation(clean_controlplane_db):
    """A claimed, even bot-token-installed, Agent with no Slack reader
    grant -> slack_poll still no-ops -- reading a manager's own messages is
    unrelated to whether they hold a bot."""
    from app.projectkb.jobs import slack_poll

    m1 = _make_manager("bot-only")
    try:
        cp_db = ControlPlaneSessionLocal()
        try:
            cp_db.add(Agent(
                id=f"agent-{m1}", name="Test Agent", slack_app_id=f"A_{m1}",
                slack_client_id="cid", slack_client_secret="csecret", slack_signing_secret="ssecret",
                manager_id=m1, team_id=f"T_{m1}", bot_token="xoxb-fake",
            ))
            cp_db.commit()
        finally:
            cp_db.close()

        db = get_manager_session(m1)
        try:
            result = slack_poll.run(db, m1)
        finally:
            db.close()
        assert result == {"fetched": 0, "stored": 0, "skipped": "not_connected"}
    finally:
        _cleanup(m1)
