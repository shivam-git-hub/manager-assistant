"""Config + last-run bookkeeping for the four fixed projectkb jobs
(ingestion/heartbeat/dream/lint).

This is deliberately narrow: a general-purpose, agent-creatable scheduling
system (Hermes-style "cron" tool) is a separate problem, to be designed
later alongside the agent harness (see the mocked `cronjob` tool in
app/agent/tools.py). This module only knows about these four fixed jobs and
their frequency -- nothing here is meant to generalize yet.

job_schedule.json (repo root, gitignored, see job_schedule.json.example) is
the deployment-wide override surface -- same role as config.json for LLM
model choice. Its keys are the JobName enum values. Anything a job's entry
omits falls back to the env-configurable defaults in app.config (which
themselves fall back to hardcoded constants there). A manager can further
override on top of that via their own managers/<manager_id>/job_schedule.json
(same shape, deep-merged the same way) -- so there are four layers:
hardcoded default -> .env override -> job_schedule.json (deployment-wide)
override -> managers/<id>/job_schedule.json (per-manager) override.

job_state.json (managers/<manager_id>/, step 15) is separate: it's runtime
state (when did each job last actually run, per manager now that the
scheduler iterates every manager's own DB), not configuration, so it lives
alongside that manager's sqlite file rather than next to job_schedule.json
(job_schedule.json itself -- the interval config -- stays a single
deployment-wide file; only the last-run bookkeeping is per-manager).
"""
import json
import logging
from datetime import datetime
from typing import Dict, Optional

from app.config import (
    BASE_DIR,
    INGESTION_INTERVAL_MINUTES,
    INGESTION_MESSAGE_THRESHOLD,
    HEARTBEAT_INTERVAL_MINUTES,
    DREAM_INTERVAL_MINUTES,
    LINT_INTERVAL_MINUTES,
    OUTLOOK_POLL_INTERVAL_MINUTES,
    SLACK_POLL_INTERVAL_MINUTES,
    AGENT_HEARTBEAT_INTERVAL_MINUTES,
)
from app.projectkb.enums import JobName

logger = logging.getLogger(__name__)

JOB_SCHEDULE_PATH = BASE_DIR / "job_schedule.json"

DEFAULT_JOB_SCHEDULE = {
    JobName.INGESTION.value: {
        "interval_minutes": INGESTION_INTERVAL_MINUTES,
        "message_threshold": INGESTION_MESSAGE_THRESHOLD,
    },
    JobName.HEARTBEAT.value: {"interval_minutes": HEARTBEAT_INTERVAL_MINUTES},
    JobName.DREAM.value: {"interval_minutes": DREAM_INTERVAL_MINUTES},
    JobName.LINT.value: {"interval_minutes": LINT_INTERVAL_MINUTES},
    JobName.OUTLOOK_POLL.value: {"interval_minutes": OUTLOOK_POLL_INTERVAL_MINUTES},
    JobName.SLACK_POLL.value: {"interval_minutes": SLACK_POLL_INTERVAL_MINUTES},
    JobName.AGENT_HEARTBEAT.value: {"interval_minutes": AGENT_HEARTBEAT_INTERVAL_MINUTES},
}


def _deep_merge_overrides(base: Dict, path) -> Dict:
    if not path.exists():
        return base
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            merged = dict(base)
            if isinstance(cfg, dict):
                for job_name, job_cfg in cfg.items():
                    merged[job_name] = {**merged.get(job_name, {}), **job_cfg}
            return merged
    except Exception as e:
        logger.warning(f"Failed to load {path}, ignoring: {e}")
        return base


def load_job_schedule(manager_id: Optional[str] = None) -> Dict:
    """Env-backed defaults, deep-merged with job_schedule.json (deployment-
    wide) overrides, then with managers/<manager_id>/job_schedule.json
    (per-manager) overrides if `manager_id` is given and that file exists."""
    merged = _deep_merge_overrides(dict(DEFAULT_JOB_SCHEDULE), JOB_SCHEDULE_PATH)
    if manager_id:
        from app.tenancy.paths import manager_dir

        merged = _deep_merge_overrides(merged, manager_dir(manager_id) / "job_schedule.json")
    return merged


def _load_job_state(manager_id: str) -> Dict[str, str]:
    from app.tenancy.paths import manager_job_state_path

    path = manager_job_state_path(manager_id)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_job_state(manager_id: str, state: Dict[str, str]) -> None:
    from app.tenancy.paths import manager_dir, manager_job_state_path

    manager_dir(manager_id).mkdir(parents=True, exist_ok=True)
    with open(manager_job_state_path(manager_id), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def get_last_run(manager_id: str, job_name: str) -> Optional[datetime]:
    """Last real wall-clock time this job ran for this manager, or None if
    it has never run."""
    state = _load_job_state(manager_id)
    raw = state.get(job_name)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def set_last_run(manager_id: str, job_name: str, when: Optional[datetime] = None) -> None:
    when = when or datetime.now()
    state = _load_job_state(manager_id)
    state[job_name] = when.isoformat()
    _save_job_state(manager_id, state)


def is_due(manager_id: str, job_name: str, interval_minutes: int, now: Optional[datetime] = None) -> bool:
    """A job with no recorded last-run is due immediately (first boot)."""
    now = now or datetime.now()
    last_run = get_last_run(manager_id, job_name)
    if last_run is None:
        return True
    elapsed_minutes = (now - last_run).total_seconds() / 60.0
    return elapsed_minutes >= interval_minutes
