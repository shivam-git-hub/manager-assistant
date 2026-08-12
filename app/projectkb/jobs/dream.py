"""Dream job: daily per-user md synthesis (memory.md/events.md) + per-owned-
project summary regeneration, suggestions/concerns, and the health rubric.
Runs every DREAM_INTERVAL_MINUTES per manager.

Selection uses the Event.dreamed flag, NOT a wall-clock cursor: job
cadence and event timestamps are different clocks, so comparing them would
miscount "new since last dream". `dreamed` has the same shape as
Claim.processed / UnifiedMessage.is_processed.

Step 36 rewrite: both halves are now agentic (app.agent.runner.run_spec)
instead of one blind structured-output LLM call per phase, mirroring
heartbeat.py's step-35 rewrite. The agent can probe the KB (search_events/
get_event/search_claims/get_thread/get_project_state/list_projects, all in
app.agent.kb_tools) before writing, instead of synthesizing off event
titles alone -- that's what made the old prose thin and repetitive. Every
write (write_memory/append_manager_events for the user pass,
write_project_summary/append_project_events/add_suggestions/add_concerns/
set_health_adjustment for the project pass) is a kb_tools.py tool handler,
not something this file writes directly -- this file owns event batching,
the agent runs' budgets/instructions/seed messages, and the mark-dreamed
bookkeeping.

dump.md is gone (step 36 Part 3) -- it had exactly one writer (this job)
and nothing ever read it.
"""
import logging
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.gemini_client import GeminiClient, get_client
from app.agent.kb_context import build_kb_context
from app.agent.registry import registry
from app.agent.runner import AgentRunResult, AgentSpec, run_spec
from app.config import (
    DREAM_EVENT_BATCH_SIZE,
    KB_AGENT_DEADLINE_SECONDS,
    KB_DREAM_MAX_LLM_CALLS,
    KB_DREAM_PROJECT_MAX_LLM_CALLS,
    SMART_MODEL,
)
from app.database import Event
from app.projectkb.project_scope import manager_owned_projects_with_events

# Import side-effect: registers every kb_tools.py handler (the six read
# probes, the five heartbeat write tools, and the seven dream write tools)
# onto the shared registry -- same discipline as heartbeat.py's identical
# import, must be explicit here rather than relied on via whatever else the
# process happens to import first.
import app.agent.kb_tools  # noqa: F401

logger = logging.getLogger(__name__)

# Tool-call ceilings, one per phase -- generous relative to each phase's own
# max_llm_calls (KB_DREAM_MAX_LLM_CALLS / KB_DREAM_PROJECT_MAX_LLM_CALLS) so
# a probe-heavy run (several search_events/get_event calls before the final
# writes) never gets cut short by this before the LLM-call budget would bind
# anyway -- a safety ceiling, not a tuning knob, same role as
# heartbeat.py's _PHASE1_MAX_TOOL_CALLS/_PHASE2_MAX_TOOL_CALLS.
_USER_MAX_TOOL_CALLS = 100
_PROJECT_MAX_TOOL_CALLS = 100

_USER_TOOL_NAMES = (
    "search_events",
    "get_event",
    "search_claims",
    "get_thread",
    "get_project_state",
    "list_projects",
    "write_memory",
    "append_manager_events",
)

_PROJECT_TOOL_NAMES = (
    "search_events",
    "get_event",
    "search_claims",
    "get_thread",
    "get_project_state",
    "list_projects",
    "write_project_summary",
    "append_project_events",
    "add_suggestions",
    "add_concerns",
    "set_health_adjustment",
)

# This is a deliberate widening of the pre-step-36 prompt (dream.py:55-70,
# which only asked for "preferences, commitments, recurring patterns") --
# see prompts/step_36_agentic_dream.md's "why". events.md stays a short,
# separate running log and must not repeat memory.md's content.
_USER_INSTRUCTIONS = (
    "You maintain this person's durable memory.md: a durable picture of this person's working life -- the "
    "projects they're involved in and their role in each, key events and decisions, commitments they made "
    "or are owed, stated preferences and working style, and things inferable from their conversations that "
    "would be costly to forget. Rewrite the whole file each time; merge and dedupe rather than appending; "
    "drop nothing that is still true.\n\n"
    "The events below are just a starting point -- probe search_events/get_event/search_claims/get_thread/"
    "get_project_state/list_projects for detail before writing if the title alone isn't enough to judge "
    "what's durable.\n\n"
    "Call write_memory exactly ONCE with the full rewritten file (it refuses a blank one, so make sure "
    "there's real content). Then call append_manager_events with short one-line entries for today's key "
    "events -- events.md is a running log, NOT a repeat of memory.md's content, keep each line brief."
)

_PROJECT_INSTRUCTIONS = (
    "You maintain this project's summary.md: current goals, state, and notes an agent should load to "
    "understand this project (NOT a full history -- that's what the events log is for). Rewrite the whole "
    "file each time.\n\n"
    "The events below are just a starting point -- probe get_project_state/search_events/get_event/"
    "search_claims/get_thread for detail before writing if the title alone isn't enough.\n\n"
    "Call write_project_summary exactly ONCE with the full rewritten file (it refuses a blank one). Then "
    "append_project_events with short one-line log entries (not a repeat of summary.md), add_suggestions/"
    "add_concerns for anything worth flagging to the manager (skip if there's nothing new), and "
    "set_health_adjustment exactly once -- the base health score is computed deterministically for you "
    "from open blockers/overdue tasks/conflicts/days since progress; you only supply a nudge of -1, 0, or "
    "+1 with a reason required whenever it's not 0."
)


def _user_seed_message(batch: List[Event]) -> str:
    lines = []
    for i, e in enumerate(batch):
        body_text = f" | description={e.body}" if e.body else ""
        lines.append(f"{i + 1}. [event_id={e.id}] [{e.type.upper()}] severity={e.severity}: {e.title}{body_text}")
    return (
        "### New Events (since last dream)\n" + "\n".join(lines) + "\n\n"
        "Probe get_event/search_claims/get_thread for detail on any you need, then call write_memory once "
        "with the full rewritten memory.md, and append_manager_events with brief log lines."
    )


def _project_seed_message(project_id: str, events: List[Event]) -> str:
    lines = [
        f"- id={e.id} | type={e.type} | severity={e.severity} | {e.title}" + (f" -- {e.body}" if e.body else "")
        for e in events
    ]
    return (
        f"### New Events tagged to project_id={project_id} (since last dream):\n" + "\n".join(lines) + "\n\n"
        "Probe get_project_state/search_events/get_event for more context if useful, then call "
        "write_project_summary once, append_project_events, add_suggestions/add_concerns if warranted, "
        "and set_health_adjustment once."
    )


def _wrote_memory(result: AgentRunResult) -> bool:
    """Whether the user-level run actually produced a memory.md write --
    the real analog of pre-step-36 dream.py's failure condition
    (dream.py:96-104: raise when there's nothing to write). A run that
    called write_memory successfully and then hit budget/deadline on a
    trailing turn HAS done its job; only a run that never got a successful
    write_memory call in should be treated as having failed. Checked
    against the tool_trace rather than stop_reason alone, since
    stop_reason=="final" only tells you the run ended cleanly, not that it
    ever called write_memory."""
    return any(
        t["name"] == "write_memory" and '"success"' in t["result_preview"] and "error" not in t["result_preview"]
        for t in result.tool_trace
    )


def _run_user_synthesis(db: Session, manager_id: str, batch: List[Event], client: GeminiClient, context_text: str) -> AgentRunResult:
    spec = AgentSpec(
        name="kb_dream_user",
        model=SMART_MODEL,
        instructions=_USER_INSTRUCTIONS,
        tool_names=_USER_TOOL_NAMES,
        max_llm_calls=KB_DREAM_MAX_LLM_CALLS,
        max_tool_calls=_USER_MAX_TOOL_CALLS,
        deadline_seconds=KB_AGENT_DEADLINE_SECONDS,
    )
    return run_spec(
        spec,
        db,
        manager_id,
        _user_seed_message(batch),
        client=client,
        context_text=context_text,
        tool_registry=registry,
    )


def _run_project_synthesis(
    db: Session, manager_id: str, project_id: str, events: List[Event], client: GeminiClient, context_text: str
) -> AgentRunResult:
    spec = AgentSpec(
        name="kb_dream_project",
        model=SMART_MODEL,
        instructions=_PROJECT_INSTRUCTIONS,
        tool_names=_PROJECT_TOOL_NAMES,
        max_llm_calls=KB_DREAM_PROJECT_MAX_LLM_CALLS,
        max_tool_calls=_PROJECT_MAX_TOOL_CALLS,
        deadline_seconds=KB_AGENT_DEADLINE_SECONDS,
    )
    return run_spec(
        spec,
        db,
        manager_id,
        _project_seed_message(project_id, events),
        client=client,
        context_text=context_text,
        tool_registry=registry,
    )


def _empty_result(stop_reason: str, *, llm_calls: int = 0, tokens_in: int = 0, tokens_out: int = 0) -> Dict:
    return {
        "events_dreamed": 0,
        "projects_synthesized": 0,
        "llm_calls": llm_calls,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "stop_reason": stop_reason,
    }


def run(db: Session, manager_id: str, client: Optional[GeminiClient] = None) -> Dict:
    """Agentic dream: un-dreamed Event rows -> one user-level agent run
    (memory.md/events.md) + one agent run per owned project with events in
    this batch (summary.md/events.md/suggestions/concerns/health). Zero LLM
    calls when there are no un-dreamed events."""
    real_client = client if client is not None else get_client()

    pending = list(
        db.scalars(
            select(Event).where(Event.dreamed.is_(False)).order_by(func.coalesce(Event.occurred_at, Event.created_at))
        ).all()
    )
    if not pending:
        return _empty_result("no_pending_events")

    batch = pending[:DREAM_EVENT_BATCH_SIZE]

    # Built once and reused for both the user-level run and every
    # per-project run -- build_kb_context is manager-scoped (not
    # project-scoped) and opens every visible project's own db.sqlite for
    # task/health counts, so rebuilding it per project would be
    # O(projects^2) file opens in a single tick for no benefit (same
    # reasoning as heartbeat.py's identical context_text reuse).
    context_text = build_kb_context(db, manager_id, include_conventions=True)

    try:
        user_result = _run_user_synthesis(db, manager_id, batch, real_client, context_text)
    except Exception:
        logger.exception(
            f"[projectkb.dream] user-level agent run failed to start for manager={manager_id}; "
            "leaving events undreamed"
        )
        return _empty_result("error")

    if not _wrote_memory(user_result):
        # Mirrors pre-step-36 dream.py's hard-fail-on-user-synthesis
        # semantics (dream.py:96-104, dream.py:318-322): the user-level
        # pass is the terminal synthesis stage this run's write discipline
        # depends on -- a run that never actually wrote memory.md (whether
        # because the LLM call errored, or it ran out of budget/deadline/
        # tool-cap/turns before ever calling write_memory) must not mark
        # this batch dreamed=True, or a whole day's memory synthesis is
        # silently skipped with no retry. Leave dreamed=False so next tick
        # retries the same batch.
        logger.error(
            f"[projectkb.dream] user-level agent run for manager={manager_id} did not write memory.md "
            f"(stop_reason={user_result.stop_reason}, error={user_result.error}); leaving events undreamed"
        )
        return _empty_result(
            user_result.stop_reason,
            llm_calls=user_result.llm_calls,
            tokens_in=user_result.tokens_in,
            tokens_out=user_result.tokens_out,
        )

    # Per-project failures are isolated (logged, skipped) -- same as
    # pre-step-36 dream.py's fan-out (dream.py:333-336) and heartbeat.py's
    # own project loop. The user-level synthesis above already succeeded
    # for this whole batch, so events still get marked dreamed regardless;
    # a failed project just doesn't get a fresh summary/health entry this
    # tick (it picks back up once new events accumulate for it).
    events_by_project = manager_owned_projects_with_events(manager_id, batch)
    projects_synthesized = 0
    project_llm_calls = 0
    project_tokens_in = 0
    project_tokens_out = 0
    for project_id, project_events in events_by_project.items():
        try:
            result = _run_project_synthesis(db, manager_id, project_id, project_events, real_client, context_text)
        except Exception:
            logger.exception(f"[projectkb.dream] project synthesis failed for project={project_id}, manager={manager_id}")
            continue
        if result.stop_reason == "error":
            logger.error(
                f"[projectkb.dream] project agent run for project={project_id}, manager={manager_id} "
                f"ended in error: {result.error}"
            )
        projects_synthesized += 1
        project_llm_calls += result.llm_calls
        project_tokens_in += result.tokens_in
        project_tokens_out += result.tokens_out

    # Mark dreamed anyway (spec, preserved from dream.py:324-328): includes
    # events whose project synthesis failed above -- otherwise a
    # permanently-failing project would re-send its events every dream tick
    # forever. Only the user-synthesis failure branch above skips this.
    for event in batch:
        event.dreamed = True
    db.commit()

    return {
        "events_dreamed": len(batch),
        "projects_synthesized": projects_synthesized,
        "llm_calls": user_result.llm_calls + project_llm_calls,
        "tokens_in": user_result.tokens_in + project_tokens_in,
        "tokens_out": user_result.tokens_out + project_tokens_out,
        "stop_reason": user_result.stop_reason,
    }
