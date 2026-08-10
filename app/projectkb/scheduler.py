"""Real wall-clock global scheduler for the fixed projectkb jobs (step 32).

One background loop, ticking every PROJECTKB_POLL_SECONDS (default 60s).
Each tick checks every job in _JOBS against its OWN interval_minutes
(job_schedule.py) and, if due, runs it once, globally, sequentially fanning
out over every provisioned manager (Employee rows with is_manager=True) --
NOT a separate scheduled task per manager. A job that's due always
processes every manager in that single pass before the next job is
considered; a manager whose own job invocation raises is caught and logged,
which doesn't stop the remaining managers or the remaining jobs this tick.

Cadence is now real (step 32 fixes the previously-documented "no cadence
gating" gap): outlook_poll/slack_poll default to 5m, ingestion 15m,
heartbeat 60m, dream 1440m (24h), lint 10080m (7d) -- see
app.projectkb.job_schedule.DEFAULT_JOB_SCHEDULE / app.config.

Last-run state (_last_run_at) is persisted to JOB_STATE_PATH
(data/job_state.json by default) so a process restart doesn't make every
job -- including dream (24h) and lint (7d) -- look immediately due again
and burn LLM budget. Loaded once at import time, re-saved after every job
that actually runs. The write is atomic (temp file + os.replace()) so a
crash mid-write can never leave a truncated/corrupt file behind; a load
failure on an existing-but-corrupt file is treated as "every known job just
ran" (one delayed cycle) rather than "no state at all" (which would make
every job due at once) -- see _load_last_run_at.

Each job name also gets a process-wide threading.Lock (_job_locks / get_job_
lock), held for the duration of that job's entire global pass. This is what
stops POST /api/dev/jobs/{name}/run or POST /api/heartbeat/run (both
manual, single-manager triggers) from running the same job concurrently
with a scheduler pass already mid-flight for it: the scheduler skips (logs)
a job whose lock is already held rather than blocking the tick, and the two
manual endpoints acquire non-blocking and return HTTP 409 rather than
hanging a request for however long a global pass takes.

Scope note: this is intentionally just the fixed job-list runner, not a
general-purpose/agent-creatable scheduler.
"""
import asyncio
import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timedelta
from typing import Dict, Optional

from app import timeservice
from app.config import JOB_STATE_PATH, PROJECTKB_POLL_SECONDS
from app.projectkb.enums import JobName
from app.projectkb.jobs import ingestion, heartbeat, dream, lint, outlook_poll, slack_poll

logger = logging.getLogger(__name__)

_JOBS = {
    JobName.INGESTION.value: ingestion,
    JobName.HEARTBEAT.value: heartbeat,
    JobName.DREAM.value: dream,
    JobName.LINT.value: lint,
    JobName.OUTLOOK_POLL.value: outlook_poll,
    JobName.SLACK_POLL.value: slack_poll,
}

# One lock per job name, pre-populated for every _JOBS entry PLUS
# agent_heartbeat -- the latter is deliberately absent from _JOBS (manual-
# trigger-only, see app.projectkb.jobs.agent_heartbeat's docstring) but is
# still reachable from two different manual endpoints that must not be
# allowed to double-run it concurrently for the same manager.
_job_locks: Dict[str, threading.Lock] = {
    name: threading.Lock() for name in list(_JOBS.keys()) + [JobName.AGENT_HEARTBEAT.value]
}


def get_job_lock(job_name: str) -> threading.Lock:
    """The process-wide lock for this job name, shared by the scheduler's
    own pass and both manual-trigger endpoints. setdefault so a job name
    not pre-populated above (shouldn't happen for any real job) still gets
    a lock lazily rather than raising KeyError."""
    return _job_locks.setdefault(job_name, threading.Lock())


def _load_last_run_at() -> Dict[str, datetime]:
    """Absent file (real first boot) -> {} -- every job correctly looks due
    immediately, today's intended behaviour. A file that EXISTS but fails
    to parse (corrupt/truncated, e.g. a crash mid-write before the atomic-
    write fix below existed, or external tampering) must NOT collapse to
    that same {} -- that would make every job in _JOBS due on the same
    tick (a stampede across every provisioned manager at once). Instead,
    stamp every known job as having just run: a damaged file costs one
    delayed cycle instead."""
    if not JOB_STATE_PATH.exists():
        return {}
    try:
        with open(JOB_STATE_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return {k: datetime.fromisoformat(v) for k, v in raw.items()}
    except Exception:
        logger.error(f"[projectkb.scheduler] {JOB_STATE_PATH} exists but failed to parse -- "
                      "treating every job as having just run to avoid a due-jobs stampede")
        now = timeservice.now_ist()
        return {job_name: now for job_name in _JOBS}


def _save_last_run_at() -> None:
    """Write to a temp file in the same directory, then os.replace() --
    atomic on POSIX, so a crash/failure mid-write can never leave
    JOB_STATE_PATH itself truncated (which is exactly what used to feed
    _load_last_run_at's corrupt-file case above)."""
    try:
        JOB_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=JOB_STATE_PATH.parent, prefix=f".{JOB_STATE_PATH.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({k: v.isoformat() for k, v in _last_run_at.items()}, f)
            os.replace(tmp_path, JOB_STATE_PATH)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception:
        logger.exception(f"[projectkb.scheduler] failed to persist {JOB_STATE_PATH}")


# job_name -> datetime of the last time this job's global pass STARTED.
# Loaded once at import time from JOB_STATE_PATH; tests reset it directly
# (monkeypatch.setattr(scheduler, "_last_run_at", {})) rather than needing a
# file-cleanup fixture -- JOB_STATE_FILENAME is test-isolated (conftest.py)
# so a monkeypatched dict never collides with the real file anyway.
_last_run_at: Dict[str, datetime] = _load_last_run_at()


# Fallback interval (minutes) for a job name missing from job_schedule's
# output -- should never happen for a real registered job, but the old
# behaviour (return 0, i.e. always-due) turned one missing config entry
# into an every-60-second-tick LLM budget leak, silently, forever. An hour
# is a safe, unsurprising default; the logger.error (not .warning) below is
# meant to actually get noticed, since this is a config bug.
_FALLBACK_INTERVAL_MINUTES = 60


def _interval_minutes(job_name: str) -> float:
    from app.projectkb.job_schedule import load_job_schedule

    schedule = load_job_schedule()
    if job_name not in schedule:
        logger.error(f"[projectkb.scheduler] {job_name} missing from job_schedule -- "
                      f"falling back to {_FALLBACK_INTERVAL_MINUTES}m instead of treating as always-due")
        return _FALLBACK_INTERVAL_MINUTES
    # A job name present but with a missing/zero/unparseable interval is the
    # same budget leak as an absent one (0 == due on every 60s tick), and is
    # the easier config mistake to make -- an override file that names a job
    # but omits interval_minutes. Same fallback, same loud error.
    raw = schedule[job_name].get("interval_minutes")
    try:
        interval = float(raw)
    except (TypeError, ValueError):
        interval = 0
    if interval <= 0:
        logger.error(f"[projectkb.scheduler] {job_name} has interval_minutes={raw!r} -- "
                      f"falling back to {_FALLBACK_INTERVAL_MINUTES}m instead of treating as always-due")
        return _FALLBACK_INTERVAL_MINUTES
    return interval


def _is_due(job_name: str) -> bool:
    last = _last_run_at.get(job_name)
    if last is None:
        return True
    return timeservice.now_ist() - last >= timedelta(minutes=_interval_minutes(job_name))


def _run_job_for_all_managers(job_name: str, job_module) -> None:
    """One global pass of this job across every provisioned manager,
    sequentially -- not a separate scheduled task per manager."""
    from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, list_provisioned_managers
    from app.tenancy.db import get_manager_session

    cp_db = ControlPlaneSessionLocal()
    try:
        managers = list_provisioned_managers(cp_db)
    finally:
        cp_db.close()

    for manager in managers:
        manager_db = get_manager_session(manager.id)
        try:
            result = job_module.run(manager_db, manager.id)
            logger.info(f"[projectkb.scheduler] ran {job_name} for manager={manager.id}: {result}")
        except Exception:
            logger.exception(f"[projectkb.scheduler] job {job_name} failed for manager={manager.id}")
        finally:
            manager_db.close()


def check_and_run_due_jobs(controlplane_db: Optional[object] = None) -> None:
    """Called periodically by background_loop(), or directly in tests.
    `controlplane_db` is accepted (and ignored) for backwards compatibility
    with call sites/tests that still pass a session in -- each due job opens
    its own short-lived session via _run_job_for_all_managers instead, since
    a single global pass may take long enough that holding one session open
    across every job/manager isn't worth it."""
    # 1. Run manager-scoped cron jobs/reminders
    try:
        from app.agent.cos_agent import check_and_run_manager_crons
        check_and_run_manager_crons()
    except Exception:
        logger.exception("[projectkb.scheduler] Failed to run manager crons")

    # 2. Run fixed background jobs
    for job_name, job_module in _JOBS.items():
        if not _is_due(job_name):
            continue
        lock = get_job_lock(job_name)
        if not lock.acquire(blocking=False):
            # Held by a manual trigger (dev_tools/agent_heartbeat's
            # POST endpoints) already mid-run for this job -- skip this
            # tick rather than running the same job concurrently against
            # the same manager DBs; it'll be picked up next tick.
            logger.warning(f"[projectkb.scheduler] {job_name} lock already held -- skipping this tick")
            continue
        try:
            _last_run_at[job_name] = timeservice.now_ist()
            _save_last_run_at()
            _run_job_for_all_managers(job_name, job_module)
        finally:
            lock.release()


async def background_loop() -> None:
    """Started once from app.main's lifespan. Polls every
    PROJECTKB_POLL_SECONDS in real time -- never touches app.timeservice
    for the sleep itself, only for interval comparisons.

    check_and_run_due_jobs() is synchronous (plain SQLAlchemy sessions, plain
    Gemini HTTP calls for heartbeat/dream) -- calling it directly here would
    block this process's single event loop for the whole duration of a due
    pass, stalling every other coroutine (incoming HTTP requests included)
    until it returns. asyncio.to_thread offloads it to a worker thread so the
    event loop stays responsive; safe because every engine in this codebase
    is created with check_same_thread=False specifically for this reason."""
    try:
        while True:
            await asyncio.sleep(PROJECTKB_POLL_SECONDS)
            try:
                await asyncio.to_thread(check_and_run_due_jobs)
            except Exception:
                logger.exception("[projectkb.scheduler] background loop iteration failed")
    except asyncio.CancelledError:
        pass
