import uuid
from datetime import datetime, timedelta

from app.controlplane.models import (
    SessionLocal as ControlPlaneSessionLocal,
    Manager,
    OutlookInstallation,
    Agent,
)
from app.tenancy.paths import ensure_manager_scaffold, manager_dir
from app.tenancy.db import get_manager_session
from app.projectkb.scheduler import check_and_run_due_jobs
from app.projectkb.job_schedule import get_last_run, set_last_run, is_due


def _make_manager(email_prefix="mgr"):
    manager_id = f"test-{uuid.uuid4().hex}"
    db = ControlPlaneSessionLocal()
    try:
        db.add(Manager(id=manager_id, email=f"{email_prefix}-{manager_id}@test.local", name="Test Manager"))
        db.commit()
    finally:
        db.close()
    ensure_manager_scaffold(manager_id)
    return manager_id


def _cleanup(*manager_ids):
    import shutil
    for mid in manager_ids:
        shutil.rmtree(manager_dir(mid), ignore_errors=True)


def test_job_state_is_isolated_per_manager(clean_controlplane_db):
    """Two managers, each with their own job_state.json -- advancing one's
    last-run doesn't affect the other's due-check."""
    m1 = _make_manager("m1")
    m2 = _make_manager("m2")
    try:
        now = datetime.now()
        set_last_run(m1, "ingestion", now)

        assert get_last_run(m1, "ingestion") == now
        assert get_last_run(m2, "ingestion") is None

        # m1's ingestion just ran -> not due again for a 15-min interval.
        assert is_due(m1, "ingestion", 15, now=now) is False
        # m2 has never run it -> due immediately.
        assert is_due(m2, "ingestion", 15, now=now) is True
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

        cp_db = ControlPlaneSessionLocal()
        try:
            check_and_run_due_jobs(cp_db)
        finally:
            cp_db.close()

        # Both managers got a call despite m1's job raising.
        assert m1 in calls
        assert m2 in calls
    finally:
        _cleanup(m1, m2)


def test_outlook_poll_uses_specific_manager_token(clean_controlplane_db, monkeypatch):
    """outlook_poll for a manager WITH an OutlookInstallation calls
    fetch_since scoped to that manager_id specifically."""
    from app.projectkb.jobs import outlook_poll
    from app.integrations import outlook as outlook_module

    m1 = _make_manager("has-outlook")
    try:
        cp_db = ControlPlaneSessionLocal()
        try:
            cp_db.add(OutlookInstallation(manager_id=m1, mailbox_email="m1@test.local", token_cache_json=None))
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
    """slack_poll for a manager WITH a user-token grant calls fetch_since
    scoped to that manager_id specifically -- a bot-token-only agent
    (no user_token) should be treated the same as no installation at all."""
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
                user_token="xoxp-fake", user_id="U_M1",
            ))
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
    finally:
        _cleanup(m1)


def test_slack_poll_noop_for_bot_only_installation(clean_controlplane_db):
    """An Agent with a bot_token but no user_token (manager approved the bot
    scope, denied the user-scope consent) -> slack_poll still no-ops,
    doesn't error trying to poll with a missing token."""
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
