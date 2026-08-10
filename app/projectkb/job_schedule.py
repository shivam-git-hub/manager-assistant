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

Step 32: app/projectkb/scheduler.py DOES gate on interval_minutes now --
load_job_schedule()'s output is the actual cadence each job runs at
globally (one pass across every manager when due), not just what
app/api/dev_tools.py reports on the debug page.

Step 34 caveat -- the per-manager layer is a NO-OP on real cadence: the
scheduler's own _interval_minutes() calls load_job_schedule() with no
manager_id (app/projectkb/scheduler.py), so the deployment-wide layer is
the effective cadence for everyone. A manager's own job_schedule.json only
ever gets read+shown by app/api/dev_tools.py's GET /api/dev/jobs (which
DOES pass manager_id), so it changes what the debug page reports without
changing when the job actually runs. The scheduler is architecturally one
global pass per job across every manager -- per-manager intervals cannot
fit that shape without restructuring it into per-manager scheduled tasks,
which is out of scope here. Load-bearing docs, not a bug to silently
"fix": don't wire manager_id into the scheduler's call without that
restructuring conversation happening first.
"""
import json
import logging
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


