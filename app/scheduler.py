import json
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Callable
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session, Mapped, mapped_column
from sqlalchemy import select, and_

from app.database import Base, get_db
from app import timeservice

logger = logging.getLogger(__name__)

class ScheduledJob(Base):
    __tablename__ = "scheduled_jobs"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_type: Mapped[str] = mapped_column(unique=True)
    payload: Mapped[Optional[str]] = mapped_column(nullable=True)
    next_due_at: Mapped[datetime] = mapped_column()
    interval_seconds: Mapped[Optional[int]] = mapped_column(nullable=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    catchup_policy: Mapped[str] = mapped_column(default="once")  # "once" | "every"
    created_at: Mapped[datetime] = mapped_column(default=lambda: timeservice.now_ist())


# JOB_HANDLERS Registry
JOB_HANDLERS: Dict[str, Callable[..., Any]] = {}

def register_handler(job_type: str, handler: Callable[..., Any]):
    JOB_HANDLERS[job_type] = handler

def get_next_9am(now: datetime) -> datetime:
    today_9am = datetime(now.year, now.month, now.day, 9, 0, 0)
    if now < today_9am:
        return today_9am
    return today_9am + timedelta(days=1)

def get_next_monday_9_30(now: datetime) -> datetime:
    days_ahead = 0 - now.weekday()
    if days_ahead < 0:
        days_ahead += 7
    elif days_ahead == 0:
        today_9_30 = datetime(now.year, now.month, now.day, 9, 30, 0)
        if now >= today_9_30:
            days_ahead += 7
        else:
            return today_9_30
            
    target_date = now.date() + timedelta(days=days_ahead)
    return datetime(target_date.year, target_date.month, target_date.day, 9, 30, 0)

def seed_default_jobs(db: Session) -> None:
    """
    Seeds the default background cron jobs into scheduled_jobs if missing.
    """
    now = timeservice.now_ist()
    
    defaults = [
        {
            "job_type": "dream_cycle",
            "interval_seconds": 3600,  # 1 hour
            "catchup_policy": "once",
            "next_due_at": now
        },
        {
            "job_type": "quiet_release",
            "interval_seconds": 900,   # 15 mins
            "catchup_policy": "once",
            "next_due_at": now
        },
        {
            "job_type": "followup_check",
            "interval_seconds": 3600,  # 1 hour
            "catchup_policy": "once",
            "next_due_at": now
        },
        {
            "job_type": "health_eval",
            "interval_seconds": 21600, # 6 hours
            "catchup_policy": "once",
            "next_due_at": now
        },
        {
            "job_type": "morning_brief",
            "interval_seconds": 86400, # 24 hours (daily)
            "catchup_policy": "every",
            "next_due_at": get_next_9am(now)
        },
        {
            "job_type": "weekly_digest",
            "interval_seconds": 604800, # 1 week
            "catchup_policy": "once",
            "next_due_at": get_next_monday_9_30(now)
        }
    ]
    
    for item in defaults:
        existing = db.scalars(select(ScheduledJob).where(ScheduledJob.job_type == item["job_type"])).first()
        if not existing:
            job = ScheduledJob(
                job_type=item["job_type"],
                payload=None,
                next_due_at=item["next_due_at"],
                interval_seconds=item["interval_seconds"],
                catchup_policy=item["catchup_policy"],
                enabled=True
            )
            db.add(job)
            
    db.commit()

def tick(db: Session) -> dict:
    """
    Finds and executes due virtual background jobs based on the simulated clock.
    """
    now = timeservice.now_ist()
    stmt = select(ScheduledJob).where(
        and_(ScheduledJob.enabled == True, ScheduledJob.next_due_at <= now)
    ).order_by(ScheduledJob.next_due_at.asc())
    due_jobs = list(db.scalars(stmt).all())
    
    ran_stats = []
    
    for job in due_jobs:
        job_type = job.job_type
        policy = job.catchup_policy
        interval = job.interval_seconds
        
        base_job_type = job_type.split(":")[0] if ":" in job_type else job_type
        handler = JOB_HANDLERS.get(base_job_type)
        if not handler:
            logger.warning(f"No handler registered for job type: {job_type} (base: {base_job_type})")
            job.enabled = False
            db.commit()
            continue
            
        count = 0
        try:
            if interval is None:
                # One-shot
                import inspect
                sig = inspect.signature(handler)
                if "job_type" in sig.parameters:
                    handler(db, job_type=job_type)
                else:
                    handler(db)
                job.enabled = False
                job.last_run_at = now
                job.next_due_at = now
                count = 1
            else:
                # Recurring
                if policy == "once":
                    handler(db)
                    count = 1
                    # Advance next_due_at until it is in the future
                    while job.next_due_at <= now:
                        job.next_due_at += timedelta(seconds=interval)
                    job.last_run_at = now
                elif policy == "every":
                    # Run handler for each missed occurrence
                    while job.next_due_at <= now:
                        # Pass next_due_at as the virtual execution time
                        handler(db, job.next_due_at)
                        count += 1
                        job.next_due_at += timedelta(seconds=interval)
                    job.last_run_at = now
                    
            if count > 0:
                # Find if stats already has it
                found = False
                for item in ran_stats:
                    if item["job_type"] == job_type:
                        item["count"] += count
                        found = True
                        break
                if not found:
                    ran_stats.append({"job_type": job_type, "count": count})
                    
        except Exception as e:
            logger.exception(f"Error executing job handler {job_type}: {e}")
            if interval is not None:
                while job.next_due_at <= now:
                    job.next_due_at += timedelta(seconds=interval)
            else:
                job.enabled = False
            job.last_run_at = now
            
        db.commit()
        
    return {"ran": ran_stats}


# FastAPI Router
router = APIRouter(prefix="/api/scheduler", tags=["Virtual Scheduler"])

@router.post("/tick")
def trigger_tick(db: Session = Depends(get_db)):
    """
    Manually triggers the virtual scheduler loop.
    """
    stats = tick(db)
    return stats


# Import actual handler functions and register them
from app.kb.synthesis import run_dream_cycle
from app.outbound import release_queued_messages_sync
from app.followups import run_followup_check
from app.health import run_health_eval
from app.brief import run_morning_brief

register_handler("dream_cycle", lambda db, vt=None: run_dream_cycle(db))
register_handler("quiet_release", lambda db, vt=None: release_queued_messages_sync(db))
register_handler("followup_check", lambda db, vt=None: run_followup_check(db))
register_handler("health_eval", lambda db, vt=None: run_health_eval(db))
register_handler("morning_brief", lambda db, vt=None: run_morning_brief(db, vt))

# Meetings handlers
def lazy_pre_meeting_brief(db, job_type=None):
    from app.kb.meetings import pre_meeting_brief_handler
    return pre_meeting_brief_handler(db, job_type)

register_handler("pre_meeting_brief", lazy_pre_meeting_brief)

# Weekly Digest handler
def lazy_weekly_digest(db, job_type=None):
    from app.kb.workload import run_weekly_digest
    return run_weekly_digest(db)

register_handler("weekly_digest", lazy_weekly_digest)
