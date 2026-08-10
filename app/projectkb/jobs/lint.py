"""Lint job: deterministic integrity + staleness checks over the pipeline's
own output, followed by an OPTIONAL, capped LLM coherence pass. Runs every
LINT_INTERVAL_MINUTES per manager (step 32 scheduler).

Step 37 rewrite of the former no-op stub. Two halves, strictly ordered:

1. Deterministic checks (zero LLM calls, always run): orphaned FK-shaped
   references across the manager db + every owned project db, staleness of
   md synthesis / unprocessed claims / un-dreamed events, and poll-health
   staleness. Every finding is persisted via app.agent.notes.record_note
   (kind="lint_finding") -- the first real caller of that function.
2. An additive coherence pass (app.agent.runner.run_spec, same shape as the
   already-converted heartbeat/dream agents): only runs when the
   deterministic pass found something (see run()'s docstring for why this
   is an AND, not an OR, despite one reading of the spec prose) -- a clean
   deterministic pass makes ZERO LLM calls, full stop. Its tool set is the
   kb_tools.py read probes plus this module's own report_finding write tool
   (kept HERE rather than added to kb_tools.py -- that file is being edited
   concurrently for the step-36 dream rewrite, and report_finding has no
   reason to live there; registry.register() works from any module that's
   imported once, same as this file already is by app.projectkb.scheduler).
   Its job is narrow -- spot contradictions between what the md files claim
   and what the db rows say -- and it must never fix anything, only report.
"""
import json
import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app import timeservice
from app.agent.gemini_client import GeminiClient, get_client
from app.agent.kb_context import build_kb_context
from app.agent.notes import record_note
from app.agent.registry import registry
from app.agent.runner import AgentSpec, run_spec
from app.agent.select import owned_projects_for_manager
from app.config import (
    DREAM_INTERVAL_MINUTES,
    FLASH_MODEL,
    HEARTBEAT_INTERVAL_MINUTES,
    IST,
    KB_AGENT_DEADLINE_SECONDS,
    KB_LINT_MAX_LLM_CALLS,
    LINT_STALE_PROJECT_DAYS,
)
from app.database import Claim, ClaimSource, Event, UnifiedMessage
from app.projectkb.enums import JobName
from app.projectkb.job_schedule import load_job_schedule

# Import side-effect: registers kb_tools.py's read probes (search_events/
# get_event/search_claims/get_thread/get_project_state/list_projects) onto
# the shared registry for the coherence pass below -- same explicit-import
# discipline as app.projectkb.jobs.heartbeat's identical comment; must not
# be relied on via whatever else the process happens to import first.
import app.agent.kb_tools  # noqa: F401

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Staleness/poll-health tuning -- module constants, not new env knobs (the
# spec only calls out LINT_STALE_PROJECT_DAYS as configurable), same idiom
# as heartbeat.py's _STARVATION_ATTEMPTS_LIMIT.
# ---------------------------------------------------------------------------

# "claims stuck unprocessed past several heartbeat cycles" (spec check 4).
_HEARTBEAT_STALL_CYCLES = 3
# "events stuck dreamed=False well past a dream cycle" (spec check 4).
_DREAM_STALL_CYCLES = 2
# "stale relative to that job's own interval_minutes" (spec check 5) -- one
# missed tick alone is noise (overlap/jitter is expected, see the pollers'
# own 2x-interval `since` window); several missed ticks in a row is signal.
_POLL_STALE_MULTIPLIER = 3
# Safety ceiling on agent_notes rows written per individual check in one
# run -- a totally broken check (e.g. every claim orphaned) must not flood
# the notes table. issues_found/by_severity below still count the TRUE
# total regardless of this cap -- only the persisted-note volume is capped.
_MAX_NOTES_PER_CHECK = 50

_COHERENCE_MAX_TOOL_CALLS = 25

_COHERENCE_TOOL_NAMES = (
    "search_events",
    "get_event",
    "search_claims",
    "get_thread",
    "get_project_state",
    "list_projects",
    "report_finding",
)

_COHERENCE_INSTRUCTIONS = (
    "You are the KB lint coherence agent. A deterministic pass already checked referential integrity "
    "and staleness this run -- its findings are summarized below. Your job is narrower and purely "
    "additive: spot places where a project's synthesized state (get_project_state's summary_tail) or "
    "recent claims/events (search_events/get_event/search_claims/get_thread) actually CONTRADICT each "
    "other -- e.g. summary.md claims something is done while a recent event says it's still blocked, or "
    "two recent claims disagree about the same fact and neither was ever recorded as a conflict.\n\n"
    "Ground every finding in a real tool result you actually called -- never assert a contradiction you "
    "haven't probed for. Call report_finding once per real contradiction (check, severity, subject_ref, "
    "detail describing what contradicts what). If nothing looks wrong after probing, call nothing "
    "further and stop -- an empty pass is a fine, expected outcome. You must NEVER modify, fix, or "
    "resolve anything; you only report."
)


# ---------------------------------------------------------------------------
# report_finding -- this job's own write tool (see module docstring for why
# it lives here instead of kb_tools.py). Same AgentNote shape record_note
# always writes; run_context also gets a copy so run() can report an exact
# coherence_findings count without re-parsing the tool trace.
# ---------------------------------------------------------------------------

_VALID_SEVERITIES = {"info", "warn", "error"}


def report_finding_handler(
    db: Session,
    manager_id: str,
    run_context: Optional[Dict[str, Any]],
    check: str,
    severity: str,
    detail: str,
    subject_ref: Optional[str] = None,
) -> Dict[str, Any]:
    if severity not in _VALID_SEVERITIES:
        severity = "warn"
    note = record_note(
        db,
        kind="lint_finding",
        content=json.dumps({"check": check, "severity": severity, "detail": detail}),
        subject_ref=subject_ref,
    )
    if run_context is not None:
        run_context.setdefault("llm_findings", []).append(
            {"check": check, "severity": severity, "subject_ref": subject_ref, "detail": detail}
        )
    return {"success": True, "note_id": note.id}


REPORT_FINDING_SCHEMA = {
    "name": "report_finding",
    "description": (
        "Records one coherence finding -- a place where synthesized KB state (a summary.md tail, etc.) "
        "contradicts the underlying claims/events. Narrow scope: report only, never fix."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "check": {"type": "string", "description": "Short slug for what was checked, e.g. 'summary_contradicts_recent_event'."},
            "severity": {"type": "string", "enum": sorted(_VALID_SEVERITIES)},
            "subject_ref": {"type": "string", "description": "The offending row, e.g. 'project:<id>', 'event:<id>', 'claim:<id>'."},
            "detail": {"type": "string", "description": "What contradicts what, concretely -- cite the ids you probed."},
        },
        "required": ["check", "severity", "detail"],
    },
}

registry.register("report_finding", REPORT_FINDING_SCHEMA, report_finding_handler)


# ---------------------------------------------------------------------------
# Deterministic checks
# ---------------------------------------------------------------------------


def _emit(db: Session, tally: Dict[str, int], counters: Dict[str, int], check: str, severity: str, subject_ref: str, detail: str) -> None:
    tally[severity] = tally.get(severity, 0) + 1
    counters[check] = counters.get(check, 0) + 1
    if counters[check] <= _MAX_NOTES_PER_CHECK:
        record_note(db, kind="lint_finding", content=json.dumps({"check": check, "severity": severity, "detail": detail}), subject_ref=subject_ref)


def _check_claim_sources(db: Session, tally: Dict[str, int], counters: Dict[str, int]) -> None:
    """Check 1: every ClaimSource row resolves to a real UnifiedMessage."""
    message_ids = {row[0] for row in db.query(UnifiedMessage.id).all()}
    for cs in db.query(ClaimSource).all():
        if cs.message_id not in message_ids:
            _emit(db, tally, counters, "claim_source_orphan", "error", f"claim:{cs.claim_id}",
                  f"ClaimSource id={cs.id} references missing UnifiedMessage id={cs.message_id}")


def _check_event_claim_ids(db: Session, tally: Dict[str, int], counters: Dict[str, int]) -> None:
    """Check 2: every Event.claim_ids entry resolves to a real Claim."""
    claim_ids = {row[0] for row in db.query(Claim.id).all()}
    for event in db.query(Event).filter(Event.claim_ids.isnot(None)).all():
        cited = json.loads(event.claim_ids) if event.claim_ids else []
        missing = [cid for cid in cited if cid not in claim_ids]
        if missing:
            _emit(db, tally, counters, "event_claim_orphan", "error", f"event:{event.id}",
                  f"Event cites missing claim id(s): {missing}")


def _check_project_orphans(db: Session, owned_project_ids: List[str], tally: Dict[str, int], counters: Dict[str, int]) -> None:
    """Check 3: no orphaned refs in any OWNED project db -- Task.parent_task_id
    pointing at a missing task, Conflict.claim_a_ref/claim_b_ref not
    resolving to real Claim rows (checked against THIS manager's own claims
    table -- conflicts are project truth written only by the owning
    manager's pipeline, so their claim refs are always this manager's
    claims), Event.task_ids pointing at tasks that no longer exist."""
    from app.projects.db import get_project_session
    from app.projects.models import Conflict, Task

    if not owned_project_ids:
        return

    claim_ids = {row[0] for row in db.query(Claim.id).all()}
    tagged_events = db.query(Event).filter(Event.project_ids.isnot(None)).all()

    for project_id in owned_project_ids:
        pdb = get_project_session(project_id)
        try:
            tasks = pdb.query(Task).all()
            task_ids = {t.id for t in tasks}

            for t in tasks:
                if t.parent_task_id and t.parent_task_id not in task_ids:
                    _emit(db, tally, counters, "task_orphan_parent", "error", f"project:{project_id}",
                          f"Task id={t.id} parent_task_id={t.parent_task_id} not found in project {project_id}")

            for c in pdb.query(Conflict).all():
                missing = [ref for ref in (c.claim_a_ref, c.claim_b_ref) if ref not in claim_ids]
                if missing:
                    _emit(db, tally, counters, "conflict_orphan_claim", "error", f"project:{project_id}",
                          f"Conflict id={c.id} in project {project_id} references missing claim id(s): {missing}")

            for event in tagged_events:
                if not event.task_ids:
                    continue
                pids = json.loads(event.project_ids) if event.project_ids else []
                if project_id not in pids:
                    continue
                tids = json.loads(event.task_ids)
                missing_tasks = [tid for tid in tids if tid not in task_ids]
                if missing_tasks:
                    _emit(db, tally, counters, "event_task_orphan", "error", f"event:{event.id}",
                          f"Event task_ids references missing task id(s) in project {project_id}: {missing_tasks}")
        finally:
            pdb.close()


def _epoch_of(dt) -> float:
    """Same UTC-epoch conversion as app.timeservice.now_epoch(), applied to
    an arbitrary naive-IST datetime rather than "now" -- lets a file mtime
    (a real Unix epoch) and a claim/event timestamp be compared without a
    system-local-timezone assumption creeping in."""
    return IST.localize(dt).timestamp()


def _check_staleness(db: Session, manager_id: str, owned_project_ids: List[str], tally: Dict[str, int], counters: Dict[str, int]) -> None:
    """Check 4, three sub-checks: (a) owned projects whose summary.md/
    events.md haven't been touched in LINT_STALE_PROJECT_DAYS while events
    kept arriving for them, (b) claims stuck unprocessed past several
    heartbeat cycles, (c) events stuck dreamed=False well past a dream
    cycle."""
    from app.projects.paths import events_md_path, summary_md_path

    now = timeservice.now_ist()
    now_epoch = timeservice.now_epoch()
    threshold_seconds = LINT_STALE_PROJECT_DAYS * 86400

    if owned_project_ids:
        tagged_events = db.query(Event).filter(Event.project_ids.isnot(None)).all()
        for project_id in owned_project_ids:
            events_for_project = []
            for e in tagged_events:
                pids = json.loads(e.project_ids) if e.project_ids else []
                if project_id in pids:
                    events_for_project.append(e)

            for label, path in (("summary.md", summary_md_path(project_id)), ("events.md", events_md_path(project_id))):
                if not path.exists():
                    continue
                mtime = path.stat().st_mtime
                age_seconds = now_epoch - mtime
                if age_seconds <= threshold_seconds:
                    continue
                newer_events = [e for e in events_for_project if _epoch_of(e.occurred_at or e.created_at) > mtime]
                if newer_events:
                    _emit(db, tally, counters, "project_doc_stale", "warn", f"project:{project_id}",
                          f"{label} not regenerated in {age_seconds / 86400:.1f}d while {len(newer_events)} new event(s) arrived for project {project_id} since")

    heartbeat_cutoff = now - timedelta(minutes=HEARTBEAT_INTERVAL_MINUTES * _HEARTBEAT_STALL_CYCLES)
    stuck_claims = db.query(Claim).filter(Claim.processed.is_(False), Claim.created_at < heartbeat_cutoff).all()
    for claim in stuck_claims:
        _emit(db, tally, counters, "claim_stuck_unprocessed", "warn", f"claim:{claim.id}",
              f"Created {claim.created_at}, still unprocessed after ~{_HEARTBEAT_STALL_CYCLES} heartbeat cycles ({HEARTBEAT_INTERVAL_MINUTES}m each)")

    dream_cutoff = now - timedelta(minutes=DREAM_INTERVAL_MINUTES * _DREAM_STALL_CYCLES)
    stuck_events = db.query(Event).filter(Event.dreamed.is_(False), Event.created_at < dream_cutoff).all()
    for event in stuck_events:
        _emit(db, tally, counters, "event_stuck_undreamed", "warn", f"event:{event.id}",
              f"Created {event.created_at}, still dreamed=False after ~{_DREAM_STALL_CYCLES} dream cycles ({DREAM_INTERVAL_MINUTES}m each)")


def _check_poll_health(db: Session, manager_id: str, tally: Dict[str, int], counters: Dict[str, int]) -> None:
    """Check 5: Employee.outlook_poll_last_success_at / slack_poll_last_success_at
    stale relative to that job's own interval_minutes -- per CLAUDE.md, that
    staleness IS the audit signal polling is silently failing. Only checked
    for a connector this employee has actually configured (mirrors
    outlook_poll.py's own outlook_mailbox_email gate / slack_poll.py's own
    slack_user_token gate) -- an unconnected connector's NULL timestamp is
    expected, not a finding."""
    from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, get_employee_by_manager_id

    schedule = load_job_schedule()
    outlook_interval = schedule[JobName.OUTLOOK_POLL.value]["interval_minutes"]
    slack_interval = schedule[JobName.SLACK_POLL.value]["interval_minutes"]
    now = timeservice.now_ist()

    cdb = ControlPlaneSessionLocal()
    try:
        employee = get_employee_by_manager_id(cdb, manager_id)
        if employee is None:
            return

        if employee.outlook_mailbox_email:
            if employee.outlook_poll_last_success_at is None:
                _emit(db, tally, counters, "outlook_poll_never_succeeded", "warn", f"employee:{manager_id}",
                      "Outlook mailbox is connected but outlook_poll has never recorded a success")
            else:
                threshold = timedelta(minutes=outlook_interval * _POLL_STALE_MULTIPLIER)
                if now - employee.outlook_poll_last_success_at > threshold:
                    _emit(db, tally, counters, "outlook_poll_stale", "warn", f"employee:{manager_id}",
                          f"Last outlook_poll success {employee.outlook_poll_last_success_at}, stale relative to its {outlook_interval}m interval")

        if employee.slack_user_token:
            if employee.slack_poll_last_success_at is None:
                _emit(db, tally, counters, "slack_poll_never_succeeded", "warn", f"employee:{manager_id}",
                      "Slack reading is connected but slack_poll has never recorded a success")
            else:
                threshold = timedelta(minutes=slack_interval * _POLL_STALE_MULTIPLIER)
                if now - employee.slack_poll_last_success_at > threshold:
                    _emit(db, tally, counters, "slack_poll_stale", "warn", f"employee:{manager_id}",
                          f"Last slack_poll success {employee.slack_poll_last_success_at}, stale relative to its {slack_interval}m interval")
    finally:
        cdb.close()


# ---------------------------------------------------------------------------
# Optional coherence pass
# ---------------------------------------------------------------------------


def _run_coherence_pass(
    db: Session,
    manager_id: str,
    owned_project_ids: List[str],
    counters: Dict[str, int],
    client: GeminiClient,
    run_context: Dict[str, Any],
):
    context_text = build_kb_context(db, manager_id, include_conventions=True)
    findings_summary = "\n".join(f"- {check}: {count}" for check, count in counters.items()) or "(none)"
    projects_list = "\n".join(f"- {pid}" for pid in owned_project_ids) or "(none)"
    seed = (
        "### Deterministic lint findings this run (check: count):\n" + findings_summary + "\n\n"
        "### Your owned projects:\n" + projects_list + "\n\n"
        "Probe get_project_state/search_events/search_claims/get_thread for any of these projects, "
        "compare against what their synthesized state says, and call report_finding for any real "
        "contradiction. Do not fix anything."
    )
    spec = AgentSpec(
        name="kb_lint_coherence",
        model=FLASH_MODEL,
        instructions=_COHERENCE_INSTRUCTIONS,
        tool_names=_COHERENCE_TOOL_NAMES,
        max_llm_calls=KB_LINT_MAX_LLM_CALLS,
        max_tool_calls=_COHERENCE_MAX_TOOL_CALLS,
        deadline_seconds=KB_AGENT_DEADLINE_SECONDS,
    )
    return run_spec(
        spec,
        db,
        manager_id,
        seed,
        client=client,
        run_context=run_context,
        context_text=context_text,
        tool_registry=registry,
    )


def run(db: Session, manager_id: str, client: Optional[GeminiClient] = None) -> Dict:
    """Deterministic checks first (zero LLM calls, always). The coherence
    pass is additive and ONLY runs when the deterministic pass actually
    found something -- a clean run makes zero LLM calls, full stop. (The
    spec prose also floats "or the manager has owned projects" as a second
    trigger, but that directly contradicts its own "skip entirely when the
    deterministic pass found nothing" sentence a line later, and the
    definition-of-done's "makes zero LLM calls when the deterministic pass
    is clean" -- this implementation follows the DoD.)"""
    tally: Dict[str, int] = {"info": 0, "warn": 0, "error": 0}
    counters: Dict[str, int] = {}

    owned_project_ids = [p["id"] for p in owned_projects_for_manager(manager_id)]

    _check_claim_sources(db, tally, counters)
    _check_event_claim_ids(db, tally, counters)
    _check_project_orphans(db, owned_project_ids, tally, counters)
    _check_staleness(db, manager_id, owned_project_ids, tally, counters)
    _check_poll_health(db, manager_id, tally, counters)

    issues_found = sum(tally.values())

    llm_calls = 0
    tokens_in = 0
    tokens_out = 0
    coherence_findings = 0
    coherence_stop_reason = None

    if issues_found > 0:
        real_client = client if client is not None else get_client()
        coherence_run_context: Dict[str, Any] = {}
        try:
            result = _run_coherence_pass(db, manager_id, owned_project_ids, counters, real_client, coherence_run_context)
        except Exception:
            logger.exception(f"[projectkb.lint] coherence pass failed to start for manager={manager_id}")
        else:
            llm_calls = result.llm_calls
            tokens_in = result.tokens_in
            tokens_out = result.tokens_out
            coherence_stop_reason = result.stop_reason
            coherence_findings = len(coherence_run_context.get("llm_findings", []))
            if result.stop_reason == "error":
                logger.error(f"[projectkb.lint] coherence pass for manager={manager_id} ended in error: {result.error}")

    return {
        "issues_found": issues_found,
        "by_severity": tally,
        "owned_projects_checked": len(owned_project_ids),
        "coherence_ran": issues_found > 0,
        "coherence_findings": coherence_findings,
        "llm_calls": llm_calls,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "stop_reason": coherence_stop_reason,
    }
