"""Heartbeat job: claims -> typed/tagged/severity events (user-level), then a project-scoped
fan-out over the events just created (task status transitions, drafted
subtasks, deterministic archive writes). Runs every
HEARTBEAT_INTERVAL_MINUTES per manager -- both halves happen in the SAME
tick/run(), not as separate scheduled jobs.

Step 35 rewrite: both halves are now agentic (app.agent.runner.run_spec)
instead of one blind structured-output LLM call per phase. The agent can
probe the KB (search_events/get_event/search_claims/get_thread/
get_project_state/list_projects, all in app.agent.kb_tools) before writing,
so a recurring claim like "still waiting on the Kafka creds" can be
recognised as the same open blocker instead of spawning a near-duplicate
every tick. Every deterministic guardrail the old blind-call version had
inline (type/severity/project/claim validation, the general=derived rule,
ownership checks on project writes) now lives in the kb_tools.py tool
handlers themselves, not here -- this file owns claim batching, the agent
run's budgets/instructions, claim disposal after the run, and the
non-LLM archive writes.
"""
import logging
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import timeservice
from app.agent.gemini_client import GeminiClient, get_client
from app.agent.kb_context import build_kb_context
from app.agent.registry import registry
from app.agent.runner import AgentRunResult, AgentSpec, run_spec
from app.config import (
    HEARTBEAT_CLAIM_BATCH_SIZE,
    KB_AGENT_DEADLINE_SECONDS,
    KB_HEARTBEAT_MAX_LLM_CALLS,
    KB_HEARTBEAT_PROJECT_MAX_LLM_CALLS,
    SMART_MODEL,
)
from app.database import Claim, Event
from app.projectkb.project_scope import manager_owned_projects_with_events

# Import side-effect: registers every kb_tools.py handler (the six read
# probes plus emit_events/record_conflict/apply_task_transitions/
# draft_tasks/link_request_to_task) onto the shared registry -- without
# this import the registry never has these names, exactly the failure mode
# app.agent.harness's own identical import-and-comment guards against for
# the chat tools. Must be explicit here rather than relied on via whatever
# else the process happens to import first.
import app.agent.kb_tools  # noqa: F401

logger = logging.getLogger(__name__)

# Claim starvation guard (spec step 5): a claim that lands in a batch
# without ever being cited by emit_events, run after run, is force-marked
# processed once it's been offered this many times -- otherwise a claim the
# model keeps declining to use is re-sent to the LLM forever.
_STARVATION_ATTEMPTS_LIMIT = 3

# Tool-call ceilings, one per phase. Generous relative to each phase's own
# max_llm_calls (KB_HEARTBEAT_MAX_LLM_CALLS / KB_HEARTBEAT_PROJECT_MAX_LLM_CALLS)
# so a probe-heavy run (several search_events/get_event calls before the
# final write) never gets cut short by this before the LLM-call budget
# would bind anyway -- a safety ceiling, not a tuning knob, same role as
# app.agent.harness.CHAT_MAX_TOOL_CALLS.
_PHASE1_MAX_TOOL_CALLS = 40
_PHASE2_MAX_TOOL_CALLS = 30

_PHASE1_TOOL_NAMES = (
    "search_events",
    "get_event",
    "search_claims",
    "get_thread",
    "get_project_state",
    "list_projects",
    "emit_events",
    "record_conflict",
)

_PHASE2_TOOL_NAMES = (
    "search_events",
    "get_event",
    "search_claims",
    "get_thread",
    "get_project_state",
    "list_projects",
    "apply_task_transitions",
    "draft_tasks",
    "link_request_to_task",
)

# Lifted from the pre-step-35 _SYSTEM_INSTRUCTION (heartbeat.py:99-108,
# tuned wording preserved) and extended with the probe-first directive that
# is the entire point of this rewrite -- see prompts/step_35_agentic_heartbeat.md.
_PHASE1_INSTRUCTIONS = (
    "You are the KB heartbeat agent. Convert the numbered Pending Claims below into typed, tagged, "
    "severity-scored events for a dashboard Updates panel. PROBE FIRST: check for an existing open "
    "event before creating a near-duplicate -- prefer probing (search_events/get_event/search_claims/"
    "get_thread/get_project_state) over guessing. When you're done judging, call emit_events exactly "
    "ONCE with your full batch.\n\n"
    "type must be exactly one of: status_update, blocker, clarification, commitment, request, "
    "conflict, fyi.\n\n"
    "conflict vs blocker -- a common judgment call, get this right: if two different people's claims "
    "contradict each other about the same fact (one says they sent/did/confirmed something, another "
    "says they never received it / it never happened / it wasn't confirmed), that is ALWAYS "
    "type=conflict, even though it also happens to be blocking someone's work -- never downgrade a "
    "genuine claim-vs-claim contradiction to a plain blocker just because one side frames it as being "
    "stuck. Reserve blocker for a single-sided obstacle with no contradicting claim on the other side "
    "(e.g. waiting on an external approval, a missing scope grant, a dependency that hasn't shipped).\n\n"
    "When you find a genuine conflict, also call record_conflict with both claim ids. list_projects "
    "tells you whether your role on a project is 'manager' (owned) or 'member' -- record_conflict only "
    "accepts a project you manage; still emit the conflict EVENT either way, just skip record_conflict "
    "for a project you don't own. record_conflict's severity argument is low|medium|high -- a "
    "DIFFERENT scale from the event severity below (0-3), do not reuse the event's number there.\n\n"
    "project_ids: only ids you've seen from list_projects or the PROJECTS section of your context, "
    "verbatim -- never invent one. If a claim isn't clearly about one of those projects, leave "
    "project_ids empty (general=true follows automatically, do not set it yourself).\n\n"
    "claim_ids on each event must cite the claim_id(s) (from the Pending Claims list below) it was "
    "derived from -- ids outside that list are never accepted.\n\n"
    "Severity doctrine -- follow exactly, this controls what interrupts the user's day:\n"
    "- Routine progress (\"x finished y\") -> severity 0-1, UNLESS a probe shows this thread was "
    "already flagged important.\n"
    "- blocker or clarification -> always at least severity 1 (code-enforced floor, you cannot go "
    "lower).\n"
    "- An approval being granted -> severity 1.\n"
    "- Urgent pending training, or repeated unanswered outreach -> severity 3.\n"
    "Multiple claims may combine into one event, or produce none, if nothing is dashboard-worthy -- do "
    "not force an event per claim."
)

# Lifted from the pre-step-35 _FANOUT_SYSTEM_INSTRUCTION, extended with the
# ownership + "cite an event from THIS run" reminders that used to be
# enforced silently by the job and are now enforced by the tool handlers
# themselves (app.agent.kb_tools._require_owned_project /
# apply_task_transitions_handler's known_event_ids restriction) -- restated
# here so the agent doesn't waste a call attempting something the tool will
# reject anyway.
_PHASE2_INSTRUCTIONS = (
    "You are the project fan-out agent for ONE project, already confirmed one you manage (own) -- "
    "these tools only work for that project_id, never attempt a different one. Given this project's "
    "Open Tasks and the New Events tagged to it this run (both below), propose task updates.\n\n"
    "apply_task_transitions: task_id must be one of the Open Tasks listed below -- never a task "
    "outside that list, and never a task you believe is already done (there is no Open Task for it). "
    "event_id must be one of the New Events listed below and must actually support that transition "
    "(e.g. an event reporting the task is done) -- never propose a transition without a citing event "
    "from THIS run; an older or unrelated event_id is rejected.\n\n"
    "draft_tasks: new tasks/subtasks this project's events imply are needed. Always created as "
    "status=pending_approval for the manager to approve -- never assume acceptance.\n\n"
    "link_request_to_task: for New Events of type=\"request\" that are literally asking to mark an "
    "existing task/subtask as done or complete, cite which Open Task it refers to. Most requests won't "
    "match any task -- only link when it's unambiguous.\n\n"
    "Probe search_events/get_event/get_project_state for more context if useful before deciding."
)


def _phase1_seed_message(batch: List[Claim]) -> str:
    lines = [f"{i + 1}. [claim_id={c.id}] {c.text}" for i, c in enumerate(batch)]
    return (
        "### Pending Claims\n" + "\n".join(lines) + "\n\n"
        "Probe as needed, then call emit_events once with your full judged batch."
    )


def _phase2_seed_message(project_id: str, open_tasks: List, events: List[Event]) -> str:
    tasks_lines = (
        [f"- id={t.id} | {t.title} | status={t.status} | priority={t.priority}" for t in open_tasks]
        or ["(none)"]
    )
    events_lines = [
        f"- id={e.id} | type={e.type} | severity={e.severity} | {e.title}" + (f" -- {e.body}" if e.body else "")
        for e in events
    ]
    return (
        f"### Open Tasks (project_id={project_id}):\n" + "\n".join(tasks_lines) + "\n\n"
        "### New Events tagged to this project this run:\n" + "\n".join(events_lines) + "\n\n"
        "Probe for more context if useful, then call apply_task_transitions/draft_tasks/"
        "link_request_to_task as appropriate."
    )


def _run_phase1(
    db: Session, manager_id: str, batch: List[Claim], client: GeminiClient, context_text: str
) -> AgentRunResult:
    run_context = {"offered_claim_ids": [c.id for c in batch]}
    spec = AgentSpec(
        name="kb_heartbeat",
        model=SMART_MODEL,
        instructions=_PHASE1_INSTRUCTIONS,
        tool_names=_PHASE1_TOOL_NAMES,
        max_llm_calls=KB_HEARTBEAT_MAX_LLM_CALLS,
        max_tool_calls=_PHASE1_MAX_TOOL_CALLS,
        deadline_seconds=KB_AGENT_DEADLINE_SECONDS,
    )
    return run_spec(
        spec,
        db,
        manager_id,
        _phase1_seed_message(batch),
        client=client,
        run_context=run_context,
        context_text=context_text,
        tool_registry=registry,
    )


def _dispose_claims(db: Session, batch: List[Claim], stop_reason: Optional[str]) -> None:
    """Anti-loop claim bookkeeping after one phase-1 run (spec step 5).
    Claims actually cited by emit_events are already processed=True by that
    tool's own commit (same session, so `claim.processed` here already
    reflects it -- expire-on-commit refreshes the instances we hold). This
    only decides what happens to the ones that were offered but NOT cited:

    - stop_reason == "final": the agent finished and chose not to use them
      -- mark them processed, they are not eternal.
    - budget / deadline / tool_cap / error / empty_response: leave them
      unprocessed, next tick retries them.

    Every claim in the batch gets heartbeat_attempts incremented regardless
    of outcome; any still-unprocessed claim that has now been offered
    _STARVATION_ATTEMPTS_LIMIT times is force-marked processed and logged --
    without this, a claim the model always declines under a budget/deadline
    stop would be re-sent forever.
    """
    for claim in batch:
        claim.heartbeat_attempts = (claim.heartbeat_attempts or 0) + 1

    if stop_reason == "final":
        for claim in batch:
            if not claim.processed:
                claim.processed = True

    for claim in batch:
        if not claim.processed and claim.heartbeat_attempts >= _STARVATION_ATTEMPTS_LIMIT:
            claim.processed = True
            logger.warning(
                f"[projectkb.heartbeat] claim {claim.id} force-marked processed after "
                f"{claim.heartbeat_attempts} heartbeat attempts without being cited"
            )

    db.commit()


def _fanout_one_project(
    db: Session, manager_id: str, project_id: str, events: List[Event], client: GeminiClient, context_text: str
) -> Dict:
    """Runs the phase-2 agent for one owned project, then writes the
    deterministic (non-LLM) archive entries -- one per event, exactly
    heartbeat.py's pre-step-35 behaviour, just relocated here since archive
    writes were never something the model should decide. tasks_updated/
    tasks_drafted are computed from a before/after diff of the project's
    Task table rather than parsed out of the tool call trace, so the count
    is correct regardless of how many apply_task_transitions/draft_tasks
    calls the agent made."""
    from app.projects.db import get_project_session
    from app.projects.models import ArchiveEntry, Task

    pdb = get_project_session(project_id)
    try:
        open_tasks = pdb.query(Task).filter(Task.status != "done").all()
        tasks_before = {t.id: t.status for t in pdb.query(Task).all()}
    finally:
        pdb.close()

    run_context = {"known_event_ids": {e.id for e in events}}
    spec = AgentSpec(
        name="kb_heartbeat_fanout",
        model=SMART_MODEL,
        instructions=_PHASE2_INSTRUCTIONS,
        tool_names=_PHASE2_TOOL_NAMES,
        max_llm_calls=KB_HEARTBEAT_PROJECT_MAX_LLM_CALLS,
        max_tool_calls=_PHASE2_MAX_TOOL_CALLS,
        deadline_seconds=KB_AGENT_DEADLINE_SECONDS,
    )
    result = run_spec(
        spec,
        db,
        manager_id,
        _phase2_seed_message(project_id, open_tasks, events),
        client=client,
        run_context=run_context,
        context_text=context_text,
        tool_registry=registry,
    )

    pdb = get_project_session(project_id)
    try:
        tasks_after = pdb.query(Task).all()
        tasks_updated = sum(1 for t in tasks_after if t.id in tasks_before and tasks_before[t.id] != t.status)
        tasks_drafted = sum(1 for t in tasks_after if t.id not in tasks_before)

        # Archive: deterministic, one row per event, NO synthesis LLM call
        # -- just log what happened.
        for event in events:
            content = f"[{event.type}] {event.title}"
            if event.body:
                content += f" -- {event.body}"
            pdb.add(ArchiveEntry(ts=timeservice.now_ist(), kind="event", content=content, source_ref=event.id))
        pdb.commit()
    finally:
        pdb.close()

    return {
        "tasks_updated": tasks_updated,
        "tasks_drafted": tasks_drafted,
        "archive_entries": len(events),
        "llm_calls": result.llm_calls,
        "tokens_in": result.tokens_in,
        "tokens_out": result.tokens_out,
    }


def _run_project_fanout(
    db: Session, manager_id: str, tagged_events: List[Event], client: GeminiClient, context_text: str
) -> Dict:
    """Groups this tick's project-tagged events by project, restricts to
    projects this manager actually manages (manager-only project truth --
    a teammate's own DMs never feed project KB), and runs the phase-2 agent
    for each. One project failing (its own agent run raising, or a bug in
    the grouping/task-diff code) is logged and skipped, never aborts the
    others or the events already committed in phase 1."""
    totals = {
        "projects_touched": 0,
        "tasks_updated": 0,
        "tasks_drafted": 0,
        "archive_entries": 0,
        "llm_calls": 0,
        "tokens_in": 0,
        "tokens_out": 0,
    }
    events_by_project = manager_owned_projects_with_events(manager_id, tagged_events)
    if not events_by_project:
        return totals

    for project_id, project_events in events_by_project.items():
        try:
            stats = _fanout_one_project(db, manager_id, project_id, project_events, client, context_text)
        except Exception:
            logger.exception(f"[projectkb.heartbeat.fanout] failed for project={project_id}, manager={manager_id}")
            continue
        totals["projects_touched"] += 1
        for key in ("tasks_updated", "tasks_drafted", "archive_entries", "llm_calls", "tokens_in", "tokens_out"):
            totals[key] += stats[key]
    return totals


def _empty_result(stop_reason: str) -> Dict:
    return {
        "claims_consumed": 0,
        "events_created": 0,
        "users_processed": 1,
        "projects_touched": 0,
        "tasks_updated": 0,
        "tasks_drafted": 0,
        "archive_entries": 0,
        "llm_calls": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "stop_reason": stop_reason,
    }


def run(db: Session, manager_id: str, client: Optional[GeminiClient] = None) -> Dict:
    """Agentic heartbeat: unprocessed Claim rows -> typed/tagged/severity-
    scored Event rows (phase 1, one agent run), then a fan-out over this
    tick's owned-project events (phase 2, one agent run per affected
    project). Zero LLM calls when there are no pending claims."""
    real_client = client if client is not None else get_client()

    pending = list(
        db.scalars(select(Claim).where(Claim.processed.is_(False)).order_by(Claim.created_at)).all()
    )
    if not pending:
        return _empty_result("no_pending_claims")

    batch = pending[:HEARTBEAT_CLAIM_BATCH_SIZE]

    # Built once and reused for both phase 1 and every phase-2 project run
    # -- build_kb_context is manager-scoped (not project-scoped) and opens
    # every visible project's own db.sqlite for task/health counts, so
    # rebuilding it per fan-out project would be O(projects^2) file opens
    # in a single tick for no benefit (phase 2 not seeing blocker counts
    # shifted by phase 1's own new events, in the same tick, is immaterial).
    context_text = build_kb_context(db, manager_id, include_conventions=True)

    # A watermark taken right before the phase-1 run starts, not a set of
    # pre-existing event ids: this job processes one manager's own
    # db.sqlite, one job at a time, single-threaded, so nothing else can
    # insert an Event row here between the watermark and the query below --
    # cheaper than diffing the whole events table and avoids an unbounded
    # NOT IN(...) clause on a manager with a large event history.
    run_started_at = timeservice.now_ist()

    try:
        phase1 = _run_phase1(db, manager_id, batch, real_client, context_text)
    except Exception:
        logger.exception(
            f"[projectkb.heartbeat] phase-1 agent run failed to start for manager={manager_id}; "
            "leaving claims unprocessed for next tick"
        )
        return _empty_result("error")

    if phase1.stop_reason == "error":
        logger.error(f"[projectkb.heartbeat] phase-1 agent run for manager={manager_id} ended in error: {phase1.error}")

    created_events = list(
        db.scalars(select(Event).where(Event.created_at >= run_started_at)).all()
    )

    _dispose_claims(db, batch, phase1.stop_reason)
    claims_consumed = sum(1 for c in batch if c.processed)

    project_tagged_events = [e for e in created_events if not e.general]
    fanout_totals = {
        "projects_touched": 0,
        "tasks_updated": 0,
        "tasks_drafted": 0,
        "archive_entries": 0,
        "llm_calls": 0,
        "tokens_in": 0,
        "tokens_out": 0,
    }
    if project_tagged_events:
        try:
            fanout_totals = _run_project_fanout(db, manager_id, project_tagged_events, real_client, context_text)
        except Exception:
            # Phase-1 events are already committed successfully at this
            # point -- a fan-out failure must not be reported as if the
            # whole heartbeat run failed (claims stay disposed, events stay).
            logger.exception(f"[projectkb.heartbeat] project fan-out failed for manager={manager_id}")

    return {
        "claims_consumed": claims_consumed,
        "events_created": len(created_events),
        "users_processed": 1,
        "projects_touched": fanout_totals["projects_touched"],
        "tasks_updated": fanout_totals["tasks_updated"],
        "tasks_drafted": fanout_totals["tasks_drafted"],
        "archive_entries": fanout_totals["archive_entries"],
        "llm_calls": phase1.llm_calls + fanout_totals["llm_calls"],
        "tokens_in": phase1.tokens_in + fanout_totals["tokens_in"],
        "tokens_out": phase1.tokens_out + fanout_totals["tokens_out"],
        "stop_reason": phase1.stop_reason,
    }
