"""Knowledge Synthesis agent (step 37, Part 2) -- a dedicated, ad-hoc agent
for querying AND updating the KB on demand: the manual counterpart to the
three scheduled KB agents (heartbeat/dream/lint). Driven through the same
app.agent.runner harness those already-agentic jobs use.

Writes are ON by default (`allow_writes=True`). Updating the KB ad-hoc is
half this agent's purpose -- "the heartbeat mis-typed that event, fix it" is
as much a reason to reach for it as "what's blocking Phoenix". The flag
exists so a caller that only wants a question answered can hand it a
provably read-only tool set, not because writing is the exceptional case.

Deliberately NOT app.agent.harness.run_agent (the chat/Slack-DM agent) --
that agent's tool set includes send_message, and this one must never be
able to contact anyone, under any flag: it reasons about the KB, it does
not talk to people. That separation is the whole reason this exists as its
own module instead of just being another chat-agent tool combination.
"""
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.agent.gemini_client import GeminiClient, get_client
from app.agent.kb_context import build_kb_context
from app.agent.registry import registry
from app.agent.runner import AgentRunResult, AgentSpec, run_spec
from app.config import KB_AGENT_DEADLINE_SECONDS, KB_SYNTHESIS_MAX_LLM_CALLS, SMART_MODEL

# Import side-effect: registers BOTH tool modules' handlers onto the shared
# registry -- kb_tools.py (the read probes + KB write tools) and tools.py
# (get_project_doc/list_tasks/get_task/list_team). Explicit rather than
# relied on via whatever else the process happens to import first -- same
# discipline app.projectkb.jobs.heartbeat documents for its own identical
# import of kb_tools.
import app.agent.kb_tools  # noqa: F401
import app.agent.tools  # noqa: F401

logger = logging.getLogger(__name__)

_MAX_TOOL_CALLS = 40

_READ_ONLY_TOOL_NAMES = (
    "search_events",
    "get_event",
    "search_claims",
    "get_thread",
    "get_project_state",
    "list_projects",
    "get_project_doc",
    "list_tasks",
    "get_task",
    "list_team",
)

# Additive when allow_writes=True (the default) -- NEVER includes
# send_message. Filtered against the live registry in run_synthesis() rather
# than assumed present, so a tool renamed or removed in kb_tools.py degrades
# this agent's write set instead of raising at spec construction (run_spec
# rejects unknown tool names outright).
_WRITE_TOOL_NAMES = (
    "emit_events",
    "record_conflict",
    "apply_task_transitions",
    "draft_tasks",
    "link_request_to_task",
    "write_memory",
    "append_manager_events",
    "write_project_summary",
    "append_project_events",
    "add_suggestions",
    "add_concerns",
    "set_health_adjustment",
)

# Load-bearing guarantee, not just documentation -- a future edit that adds
# send_message to either tuple above must fail loudly at import time, not
# quietly relax the "this agent cannot contact anyone" invariant the whole
# module exists to enforce.
assert "send_message" not in _READ_ONLY_TOOL_NAMES
assert "send_message" not in _WRITE_TOOL_NAMES

_BASE_INSTRUCTIONS = (
    "You are the Knowledge Synthesis agent -- an ad-hoc, on-demand agent for answering questions about "
    "this manager's own knowledge base (claims, events, projects, tasks, team) and, when asked and "
    "enabled, making the requested KB update. You have NO way to message or notify anyone -- you must "
    "never claim to have contacted, notified, escalated to, or messaged a person; if the question asks "
    "for that, say plainly that this agent cannot send messages and answer with what the KB shows "
    "instead.\n\n"
    "Ground every claim in an actual tool result: probe before asserting anything, and cite the "
    "specific event/claim/task id(s) behind each fact in your final answer. If the KB genuinely doesn't "
    "contain the answer after probing, say so plainly -- never infer or guess to fill a gap.\n\n"
)

_WRITES_ENABLED_CLAUSE = (
    "Writes ARE enabled for this run. If the question calls for a KB update, use the write tools "
    "available to you (event/conflict/task tools, and the memory/summary/suggestion/concern/health "
    "tools where applicable) -- each enforces its own validation, so a rejected write is expected "
    "feedback, not an error to route around. State plainly, in your final answer, exactly what you "
    "wrote and where -- never leave a write unmentioned."
)
_WRITES_DISABLED_CLAUSE = (
    "Writes are NOT enabled for this run -- you are read-only. Answer from what you find; do not claim "
    "to have changed, created, or updated anything."
)


def run_synthesis(
    db: Session,
    manager_id: str,
    question: str,
    *,
    allow_writes: bool = True,
    client: Optional[GeminiClient] = None,
) -> AgentRunResult:
    """Answers `question` against this manager's own KB, and updates it when
    the question calls for that. Pass `allow_writes=False` for a provably
    read-only run (the write tools are then never in the allowlist at all,
    so read-only is enforced by the harness, not by the prompt)."""
    real_client = client if client is not None else get_client()

    tool_names = list(_READ_ONLY_TOOL_NAMES)
    if allow_writes:
        known = registry.known_names()
        tool_names += [name for name in _WRITE_TOOL_NAMES if name in known]

    # Belt-and-braces: even if a future edit widened _WRITE_TOOL_NAMES,
    # send_message could never end up in the actual allowlist handed to
    # run_spec.
    assert "send_message" not in tool_names

    instructions = _BASE_INSTRUCTIONS + (_WRITES_ENABLED_CLAUSE if allow_writes else _WRITES_DISABLED_CLAUSE)
    spec = AgentSpec(
        name="kb_synthesis",
        model=SMART_MODEL,
        instructions=instructions,
        tool_names=tuple(tool_names),
        max_llm_calls=KB_SYNTHESIS_MAX_LLM_CALLS,
        max_tool_calls=_MAX_TOOL_CALLS,
        deadline_seconds=KB_AGENT_DEADLINE_SECONDS,
    )
    context_text = build_kb_context(db, manager_id, include_conventions=True)

    return run_spec(
        spec,
        db,
        manager_id,
        question,
        client=real_client,
        run_context={},
        context_text=context_text,
        tool_registry=registry,
    )
