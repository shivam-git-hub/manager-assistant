"""Deterministic project-health rubric (spec Sec 4.4), shared by the dream
job's project-synthesis agent run and the `set_health_adjustment` write tool
(app.agent.kb_tools) that run calls into. Moved out of
app.projectkb.jobs.dream in step 36 so kb_tools.py (which computes and
writes the HealthLog row itself, since the LLM only supplies a nudge + a
reason) can import it without an import cycle -- dream.py imports kb_tools
for its tool-registration side effect, so kb_tools.py cannot import back
from dream.py at module load time.

`compute_base_health_score` must stay exactly as it was pre-step-36 -- the
LLM never computes the final score, only a code-clamped +/-1 nudge with a
required reason (see kb_tools.set_health_adjustment_handler).
"""
import json
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import Event

PROGRESS_EVENT_TYPES = {"status_update", "commitment"}
# No progress event ever recorded for a project -- treated as "a long
# time," but still bounded by the rubric's own 30-day cap below, so this
# just needs to be large enough to always saturate that cap.
DAYS_SINCE_PROGRESS_SENTINEL = 999
HEALTH_BAND_POINTS = 15  # score points per "band" -- the LLM nudges by at most one band, spec §4.4


def events_for_project(
    db: Session, project_id: str, types: Optional[set] = None, ui_states: Optional[set] = None
) -> List[Event]:
    """Events tagged to this project across ALL time (health reflects
    current standing, not just this dream tick's new batch) -- Python-side
    JSON-list filter, same convention as app.api.home's project_id filter."""
    query = select(Event)
    if types:
        query = query.where(Event.type.in_(types))
    if ui_states:
        query = query.where(Event.ui_state.in_(ui_states))
    # COALESCE: occurred_at is NULL for events predating that column or
    # with unsourced claims -- ordering by created_at alone would interleave
    # old and new events wrongly (see Event.occurred_at's docstring).
    all_matching = db.scalars(query.order_by(func.coalesce(Event.occurred_at, Event.created_at).desc())).all()
    return [e for e in all_matching if e.project_ids and project_id in json.loads(e.project_ids)]


def days_since_last_progress(db: Session, project_id: str, now) -> int:
    progress_events = events_for_project(db, project_id, types=PROGRESS_EVENT_TYPES)
    if not progress_events:
        return DAYS_SINCE_PROGRESS_SENTINEL
    most_recent = progress_events[0]
    last_progress_at = most_recent.occurred_at or most_recent.created_at
    return max(0, (now - last_progress_at).days)


def is_overdue(task, now) -> bool:
    if task.status in ("done", "blocked", "pending_approval"):
        return False
    return bool(task.due and task.due < now)


def compute_base_health_score(open_blockers: int, overdue_tasks: int, open_conflicts: int, days_since_progress: int) -> int:
    """Deterministic, auditable health formula (spec §4.4) -- "why is this
    project red" must always have a legible answer, not just a number:
      - 15 pts per open blocker, capped at 4 (max 60 pts)
      - 10 pts per overdue task, capped at 5 (max 50 pts)
      - 20 pts per open conflict, capped at 3 (max 60 pts)
      - 1 pt per day since the last progress-type event, capped at 30
    Clamped to [0, 100]. Bands (for the frontend's green/yellow/red
    glyph): >=70 green, >=40 yellow, else red."""
    score = 100
    score -= 15 * min(open_blockers, 4)
    score -= 10 * min(overdue_tasks, 5)
    score -= 20 * min(open_conflicts, 3)
    score -= min(days_since_progress, 30)
    return max(0, min(100, score))
