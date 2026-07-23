"""Real wall-clock runner for the fixed projectkb jobs -- entirely
independent of the simulator's sim time. What "running a job" means
concretely here: every PROJECTKB_POLL_SECONDS (default 60s), for every
provisioned manager (step 15/16 -- there's no single shared DB to run
against anymore, so each manager's own db.sqlite gets its own scan), check
each job's entry in job_schedule.json against that manager's job_state.json;
if datetime.now() minus that manager's last recorded run for the job
exceeds its configured interval_minutes, call that job module's
run(manager_db, manager_id) and record the current time as its new last-run.

Jobs: ingestion/heartbeat/dream/lint are the KB extraction/synthesis
cadence (ingestion/heartbeat/dream/lint currently log-and-return stubs
except ingestion, which real connectors now actually feed). outlook_poll is
a separate concern -- Outlook's arrival cadence (poll, not push) -- kept
distinct from extraction cadence on purpose, and is genuinely per-manager
(skips managers with no OutlookInstallation, see jobs/outlook_poll.py).

Scope note: this is intentionally just the fixed job-list runner, not a
general-purpose/agent-creatable scheduler -- that's a separate design
problem for later, alongside the agent harness.
"""
import asyncio
import logging
from datetime import datetime

from app.config import PROJECTKB_POLL_SECONDS
from app.projectkb.job_schedule import load_job_schedule, is_due, set_last_run
from app.projectkb.enums import JobName
from app.projectkb.jobs import ingestion, heartbeat, dream, lint, outlook_poll, slack_poll, agent_heartbeat

logger = logging.getLogger(__name__)

_JOBS = {
    JobName.INGESTION.value: ingestion,
    JobName.HEARTBEAT.value: heartbeat,
    JobName.DREAM.value: dream,
    JobName.LINT.value: lint,
    JobName.OUTLOOK_POLL.value: outlook_poll,
    JobName.SLACK_POLL.value: slack_poll,
    JobName.AGENT_HEARTBEAT.value: agent_heartbeat,
}


def _check_and_run_jobs_for_manager(manager, manager_db) -> None:
    """One scan over all five jobs against this manager's job_schedule/
    job_state. A job failing for this manager is caught and logged --
    doesn't stop the other jobs for this manager, and (since the caller
    also catches per-manager) doesn't stop other managers either."""
    cfg = load_job_schedule(manager.id)
    now = datetime.now()

    for job_name, job_module in _JOBS.items():
        job_cfg = cfg.get(job_name, {})
        interval_minutes = job_cfg.get("interval_minutes")
        if interval_minutes is None:
            continue
        if is_due(manager.id, job_name, interval_minutes, now=now):
            try:
                result = job_module.run(manager_db, manager.id)
                logger.info(f"[projectkb.scheduler] ran {job_name} for manager={manager.id}: {result}")
            except Exception:
                logger.exception(f"[projectkb.scheduler] job {job_name} failed for manager={manager.id}")
            finally:
                set_last_run(manager.id, job_name, now)


def check_and_run_due_jobs(controlplane_db) -> None:
    """Fan-out over every provisioned manager, each scanned independently
    against their own db.sqlite/job_state.json. Meant to be called
    periodically by background_loop(), or directly in tests."""
    from app.controlplane.models import list_provisioned_managers
    from app.tenancy.db import get_manager_session

    for manager in list_provisioned_managers(controlplane_db):
        manager_db = get_manager_session(manager.id)
        try:
            _check_and_run_jobs_for_manager(manager, manager_db)
        except Exception:
            logger.exception(f"[projectkb.scheduler] tick failed for manager={manager.id}")
        finally:
            manager_db.close()


async def background_loop() -> None:
    """Started once from app.main's lifespan. Polls every
    PROJECTKB_POLL_SECONDS in real time -- never touches app.timeservice.
    The fan-out across managers happens inside check_and_run_due_jobs; this
    loop just owns the control-plane session used to enumerate them."""
    from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal

    try:
        while True:
            await asyncio.sleep(PROJECTKB_POLL_SECONDS)
            controlplane_db = ControlPlaneSessionLocal()
            try:
                check_and_run_due_jobs(controlplane_db)
            except Exception:
                logger.exception("[projectkb.scheduler] background loop iteration failed")
            finally:
                controlplane_db.close()
    except asyncio.CancelledError:
        pass
