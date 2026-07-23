"""Manual job triggers + message/claim/event chain visualization for local
testing (step 28 follow-up, 2026-07-23) -- NOT part of the manager-facing
product surface. Backs app/static/debug.html, a plain no-build test page in
the same spirit as login.html/connect.html (CLAUDE.md: minimal test-the-flow
UI, doesn't need the wireframe-driven React build). Every route is
manager-scoped (cookie auth via get_current_manager/get_manager_db) --
manually running a job runs it against the LOGGED-IN manager's own data,
same as the existing POST /api/heartbeat/run.
"""
import json
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.controlplane.auth import get_current_manager
from app.controlplane.models import Manager
from app.database import Claim, ClaimSource, UnifiedMessage
from app.tenancy.db import get_manager_db
from app.projectkb.enums import JobName
from app.projectkb.jobs import ingestion, heartbeat, dream, lint, outlook_poll, slack_poll, agent_heartbeat
from app.projectkb.job_schedule import load_job_schedule

router = APIRouter(prefix="/api/dev", tags=["Dev Tools"])

# Same run(db, manager_id, client=None) convention every projectkb job
# module follows (app.projectkb.scheduler._JOBS mirrors this list) -- kept
# as a separate literal dict here rather than importing that private one,
# since this is a standalone debug surface, not scheduler internals.
JOB_MODULES = {
    JobName.OUTLOOK_POLL.value: outlook_poll,
    JobName.SLACK_POLL.value: slack_poll,
    JobName.INGESTION.value: ingestion,
    JobName.HEARTBEAT.value: heartbeat,
    JobName.DREAM.value: dream,
    JobName.LINT.value: lint,
    JobName.AGENT_HEARTBEAT.value: agent_heartbeat,
}


@router.get("/jobs")
def list_jobs(manager: Manager = Depends(get_current_manager)) -> dict:
    """Job names + their currently-effective interval (env -> job_schedule.json
    -> per-manager override, see app.projectkb.job_schedule's docstring),
    for the debug page to render without hardcoding frequencies."""
    cfg = load_job_schedule(manager.id)
    return {
        name: {"interval_minutes": cfg.get(name, {}).get("interval_minutes")}
        for name in JOB_MODULES
    }


@router.post("/jobs/{job_name}/run")
def run_job(
    job_name: str,
    manager: Manager = Depends(get_current_manager),
    db: Session = Depends(get_manager_db),
) -> dict:
    module = JOB_MODULES.get(job_name)
    if module is None:
        raise HTTPException(status_code=404, detail=f"Unknown job '{job_name}'. Must be one of {list(JOB_MODULES)}")
    try:
        result = module.run(db, manager.id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Job '{job_name}' raised: {e}")
    return {"job": job_name, "result": result}


def _message_preview(db: Session, message_id: int) -> Optional[dict]:
    msg = db.get(UnifiedMessage, message_id)
    if not msg:
        return None
    return {
        "id": msg.id,
        "source": msg.source,
        "sender": msg.sender_mapped_name or msg.sender_raw_id,
        "content": (msg.content or "")[:200],
        "timestamp": str(msg.timestamp),
        "thread_id": msg.thread_id,
    }


@router.get("/claims")
def list_claims(limit: int = 100, db: Session = Depends(get_manager_db)) -> List[dict]:
    """Claims + which messages they cite + processed flag -- the middle
    link in the message -> claim -> event chain."""
    claims = db.query(Claim).order_by(Claim.created_at.desc()).limit(limit).all()
    out = []
    for c in claims:
        source_rows = db.query(ClaimSource).filter(ClaimSource.claim_id == c.id).all()
        sources = [_message_preview(db, s.message_id) for s in source_rows]
        out.append({
            "id": c.id,
            "text": c.text,
            "thread_key": c.thread_key,
            "processed": c.processed,
            "created_at": str(c.created_at),
            "sources": [s for s in sources if s],
        })
    return out
