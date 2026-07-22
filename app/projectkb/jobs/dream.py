"""Dream job (spec/architecture_v2_kb.md §4.4, prompts/step_25_dream_job.md):
daily per-user md synthesis (memory.md/events.md/dump.md) + per-managed-
project summary regeneration, suggestions/concerns, and the health
rubric. Runs every DREAM_INTERVAL_MINUTES per manager.

Selection uses Event.dreamed, NOT a wall-clock cursor: job_schedule.py's
get_last_run/set_last_run track REAL wall-clock time -- this job's own
scheduling cadence, deliberately independent of sim time (see
app/projectkb/scheduler.py's docstring) -- but Event.created_at is
stamped in SIM time. Comparing the two would silently miscount "new since
last dream" whenever the sim clock jumps. `dreamed` is the flag actually
meant for this (added step 19), same shape as Claim.processed /
UnifiedMessage.is_processed.

Deliberate scope cut, same rationale as steps 23/24: one structured-
output SMART_MODEL call per phase, not a tool loop.
"""
import json
import logging
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import timeservice
from app.config import DREAM_EVENT_BATCH_SIZE, SMART_MODEL
from app.database import Event
from app.agent.gemini_client import GeminiClient, get_client
from app.projectkb.llm_json import parse_json_object
from app.projectkb.project_scope import manager_owned_projects_with_events
from app.tenancy.paths import manager_dump_md_path, manager_events_md_path, manager_memory_md_path

logger = logging.getLogger(__name__)

PROGRESS_EVENT_TYPES = {"status_update", "commitment"}
# No progress event ever recorded for a project -- treated as "a long
# time," but still bounded by the rubric's own 30-day cap below, so this
# just needs to be large enough to always saturate that cap.
DAYS_SINCE_PROGRESS_SENTINEL = 999


def _read_if_exists(path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _append_lines(path, lines: List[str]) -> None:
    clean = [line.strip() for line in lines if isinstance(line, str) and line.strip()]
    if not clean:
        return
    existing = _read_if_exists(path)
    addition = "\n".join(f"- {line}" for line in clean)
    with open(path, "a", encoding="utf-8") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write(addition + "\n")


# ─── Per-user synthesis (memory.md / events.md / dump.md) ──────────────

_USER_SYSTEM_INSTRUCTION = (
    "You maintain a user's personal knowledge-base files for a work "
    "assistant. Given the current memory.md and the day's new events, "
    "produce strict JSON:\n"
    '{"memory_md": "...", "events_lines": ["..."], "dump_lines": ["..."]}\n\n'
    "memory_md: the FULL rewritten memory.md -- durable facts worth "
    "remembering about this user across days (preferences, commitments, "
    "recurring patterns). Merge new facts into the existing ones; never "
    "restate a fact that's already there in different words -- dedupe "
    "near-duplicates rather than appending a near-identical line. Return "
    "the existing content essentially unchanged if nothing new belongs.\n"
    "events_lines: short one-line summaries of today's key events, "
    "appended to a running log (don't repeat memory_md's content here).\n"
    "dump_lines: literal reference facts worth keeping verbatim for later "
    "lookup (contact info, feature descriptions) -- empty list if none."
)


def _call_user_synthesis(client: GeminiClient, memory_md: str, events: List[Event]) -> Dict:
    events_context = "\n".join(f"- [{e.type}] severity={e.severity}: {e.title}" for e in events) or "(none)"
    prompt = (
        f"### Current memory.md:\n{memory_md.strip() or '(empty)'}\n\n"
        f"### Today's new events:\n{events_context}\n\n"
        "Produce the JSON now."
    )
    res = client.chat(
        model=SMART_MODEL,
        messages=[
            {"role": "system", "content": _USER_SYSTEM_INSTRUCTION},
            {"role": "user", "content": prompt},
        ],
        json_mode=True,
    )
    return parse_json_object(res, "[projectkb.dream.user]")


def _run_user_synthesis(manager_id: str, events: List[Event], client: GeminiClient) -> None:
    memory_path = manager_memory_md_path(manager_id)
    current_memory = _read_if_exists(memory_path)

    result = _call_user_synthesis(client, current_memory, events)
    if not result:
        # parse_json_object() tolerates malformed/off-shape responses by
        # returning {} rather than raising -- correct for jobs where a bad
        # response should just skip a sub-batch, but here events are the
        # TERMINAL synthesis stage: silently proceeding would mark this
        # whole batch dreamed=True with nothing actually synthesized, and
        # it would never be retried. Raise so run()'s except leaves the
        # batch dreamed=False for a real retry next tick.
        raise ValueError("dream user-level synthesis returned an unparseable/empty response")

    memory_md = result.get("memory_md")
    if isinstance(memory_md, str) and memory_md.strip():
        memory_path.write_text(memory_md, encoding="utf-8")

    _append_lines(manager_events_md_path(manager_id), result.get("events_lines") or [])
    _append_lines(manager_dump_md_path(manager_id), result.get("dump_lines") or [])


# ─── Per-project synthesis + health rubric ──────────────────────────────

HEALTH_BAND_POINTS = 15  # score points per "band" -- the LLM nudges by at most one band, spec §4.4

_PROJECT_SYSTEM_INSTRUCTION = (
    "You maintain a project's knowledge-base summary for a work assistant. "
    "Given the project's own project.md (manager-authored), its current "
    "task list, recent archive excerpts, and new events, produce strict "
    "JSON:\n"
    '{"summary_md": "...", "events_lines": ["..."], "suggestions": ["..."], '
    '"concerns": ["..."], "health_adjustment": -1|0|1, "health_reason": "..."}\n\n'
    "summary_md: the FULL rewritten summary.md -- current goals, state, "
    "and notes an agent should load to understand this project (NOT a "
    "full history -- that's what the archive is for).\n"
    "events_lines: short summaries of today's key events for this "
    "project's own log.\n"
    "suggestions: constructive suggestions for the manager, if any -- "
    "empty list if none.\n"
    "concerns: risks/issues worth flagging, if any -- empty list if none.\n"
    "health_adjustment: a deterministic base health score is computed "
    "separately from open blockers/overdue tasks/conflicts/days since "
    "progress. You may nudge it by AT MOST one band -- -1 (worse), 0, or "
    "+1 (better) -- based on qualitative signals those counts miss (e.g. "
    "milestone slippage visible in project.md but not captured by the "
    "counts). health_reason is REQUIRED (a short justification) whenever "
    "health_adjustment is not 0, otherwise leave it null."
)


def _build_project_prompt(project_md: str, tasks: List, recent_archive: List, new_events: List[Event]) -> str:
    tasks_context = "\n".join(f"- id={t.id} | {t.title} | status={t.status}" for t in tasks) or "(none)"
    archive_context = "\n".join(f"- [{a.ts}] {a.kind}: {a.content}" for a in recent_archive) or "(none)"
    events_context = "\n".join(f"- [{e.type}] severity={e.severity}: {e.title}" for e in new_events) or "(none)"
    return (
        f"### project.md:\n{project_md.strip() or '(empty)'}\n\n"
        f"### Current Tasks:\n{tasks_context}\n\n"
        f"### Recent Archive (last 15):\n{archive_context}\n\n"
        f"### New Events:\n{events_context}\n\n"
        "Produce the JSON now."
    )


def _call_project_synthesis(client: GeminiClient, project_md: str, tasks: List, recent_archive: List, new_events: List[Event]) -> Dict:
    res = client.chat(
        model=SMART_MODEL,
        messages=[
            {"role": "system", "content": _PROJECT_SYSTEM_INSTRUCTION},
            {"role": "user", "content": _build_project_prompt(project_md, tasks, recent_archive, new_events)},
        ],
        json_mode=True,
    )
    return parse_json_object(res, "[projectkb.dream.project]")


def _events_for_project(
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
    all_matching = db.scalars(query.order_by(Event.created_at.desc())).all()
    return [e for e in all_matching if e.project_ids and project_id in json.loads(e.project_ids)]


def _days_since_last_progress(db: Session, project_id: str, now) -> int:
    progress_events = _events_for_project(db, project_id, types=PROGRESS_EVENT_TYPES)
    if not progress_events:
        return DAYS_SINCE_PROGRESS_SENTINEL
    return max(0, (now - progress_events[0].created_at).days)


def _is_overdue(task, now) -> bool:
    if task.status in ("done", "blocked", "pending_approval"):
        return False
    return bool(task.due and task.due < now)


def _compute_base_health_score(open_blockers: int, overdue_tasks: int, open_conflicts: int, days_since_progress: int) -> int:
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


def _run_project_synthesis(db: Session, project_id: str, new_events: List[Event], client: GeminiClient) -> Dict:
    from app.projects.db import get_project_session
    from app.projects.models import ArchiveEntry, Concern, Conflict, HealthLog, Suggestion, Task
    from app.projects.paths import events_md_path, project_md_path, summary_md_path

    project_md = _read_if_exists(project_md_path(project_id))
    now = timeservice.now_ist()

    project_db = get_project_session(project_id)
    try:
        recent_archive = project_db.query(ArchiveEntry).order_by(ArchiveEntry.ts.desc()).limit(15).all()
        tasks = project_db.query(Task).all()

        result = _call_project_synthesis(client, project_md, tasks, recent_archive, new_events)

        # DB writes (Suggestion/Concern/HealthLog) are staged and committed
        # FIRST, file writes (summary.md overwrite, events.md append) only
        # AFTER that commit succeeds -- ordered this way (not the more
        # obvious "write then persist") so a mid-function failure can never
        # leave summary.md looking freshly synthesized while the DB rows
        # backing that synthesis silently rolled back. A failure between
        # the commit and the file writes is a much smaller, accepted
        # residual risk (DB ahead of the files by one tick's content,
        # self-heals next tick) rather than the reverse.
        suggestions_created = 0
        for text in result.get("suggestions") or []:
            if isinstance(text, str) and text.strip():
                project_db.add(Suggestion(ts=now, text=text.strip(), status="open"))
                suggestions_created += 1

        concerns_created = 0
        for text in result.get("concerns") or []:
            if isinstance(text, str) and text.strip():
                project_db.add(Concern(ts=now, text=text.strip(), status="open"))
                concerns_created += 1

        # Health rubric: deterministic base (current standing, not just
        # this tick's batch) + a code-clamped LLM nudge.
        open_blockers = len(_events_for_project(db, project_id, types={"blocker"}, ui_states={"shown", "promoted"}))
        open_conflicts = project_db.query(Conflict).filter(Conflict.status == "open").count()
        overdue_tasks = sum(1 for t in tasks if _is_overdue(t, now))
        days_since_progress = _days_since_last_progress(db, project_id, now)
        base_score = _compute_base_health_score(open_blockers, overdue_tasks, open_conflicts, days_since_progress)

        try:
            adjustment = int(result.get("health_adjustment", 0))
        except (TypeError, ValueError):
            adjustment = 0
        adjustment = max(-1, min(1, adjustment))
        reason = result.get("health_reason")
        has_reason = isinstance(reason, str) and bool(reason.strip())
        if adjustment != 0 and not has_reason:
            # Spec requires a written reason for any non-zero nudge -- a
            # model that adjusts without one gets floored back to 0 rather
            # than silently landing an unexplained adjustment.
            adjustment = 0
        reason = reason if (adjustment != 0 and has_reason) else None
        final_score = max(0, min(100, base_score + adjustment * HEALTH_BAND_POINTS))

        project_db.add(
            HealthLog(
                ts=now,
                rubric_inputs=json.dumps(
                    {
                        "open_blockers": open_blockers,
                        "overdue_tasks": overdue_tasks,
                        "open_conflicts": open_conflicts,
                        "days_since_progress": days_since_progress,
                    }
                ),
                base_score=base_score,
                llm_adjustment=adjustment,
                reason=reason,
                final_score=final_score,
            )
        )

        project_db.commit()

        summary_md = result.get("summary_md")
        summary_regenerated = isinstance(summary_md, str) and bool(summary_md.strip())
        if summary_regenerated:
            summary_md_path(project_id).write_text(summary_md, encoding="utf-8")
        _append_lines(events_md_path(project_id), result.get("events_lines") or [])

        return {
            "summary_regenerated": summary_regenerated,
            "suggestions_created": suggestions_created,
            "concerns_created": concerns_created,
        }
    finally:
        project_db.close()


def run(db: Session, manager_id: str, client: Optional[GeminiClient] = None) -> Dict:
    """Dream job: un-dreamed Event rows -> per-user md synthesis +
    per-managed-project summary/suggestions/concerns/health. See
    prompts/step_25_dream_job.md."""
    real_client = client if client is not None else get_client()

    pending = list(db.scalars(select(Event).where(Event.dreamed.is_(False)).order_by(Event.created_at)).all())
    if not pending:
        return {"events_dreamed": 0, "projects_synthesized": 0}

    batch = pending[:DREAM_EVENT_BATCH_SIZE]

    try:
        _run_user_synthesis(manager_id, batch, real_client)
    except Exception:
        logger.exception(f"[projectkb.dream] user-level synthesis failed for manager={manager_id}; leaving events undreamed")
        return {"events_dreamed": 0, "projects_synthesized": 0}

    # Per-project failures are isolated (logged, skipped) same as step
    # 24's fan-out -- the user-level synthesis above already succeeded for
    # this whole batch, so events still get marked dreamed regardless; a
    # failed project just doesn't get a fresh summary/health entry this
    # tick (it'll pick up again once new events accumulate).
    events_by_project = manager_owned_projects_with_events(manager_id, batch)
    projects_synthesized = 0
    for project_id, project_events in events_by_project.items():
        try:
            _run_project_synthesis(db, project_id, project_events, real_client)
        except Exception:
            logger.exception(f"[projectkb.dream] project synthesis failed for project={project_id}, manager={manager_id}")
            continue
        projects_synthesized += 1

    for event in batch:
        event.dreamed = True
    db.commit()

    return {"events_dreamed": len(batch), "projects_synthesized": projects_synthesized}
