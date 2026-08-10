import logging
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session

from app.agent.registry import registry
from app.agent.prompts import compile_system_prompt
from app.agent.runner import AgentSpec, run_spec
from app.agent.gemini_client import get_client, GeminiClient
from app.config import SMART_MODEL
# Importing tools registers all 9 handlers on the shared registry as an import
# side-effect. Without this, the registry is empty at runtime and Harry answers
# with no tools (hallucinating). Tests happened to pass because test_agent.py
# imports this module directly; the running app never did.
import app.agent.tools  # noqa: F401

logger = logging.getLogger(__name__)

# The 9 tools app/agent/tools.py registers -- the chat agent's allowlist is
# "everything", matching pre-step-33 behaviour (the old loop had no
# allowlist concept at all, it just handed the model every registered
# tool). A future KB agent spec (heartbeat/dream/lint) would instead list a
# narrow, deliberate subset.
CHAT_TOOL_NAMES = (
    "send_message",
    "dashboard_action",
    "list_meetings",
    "get_project_doc",
    "list_team",
    "get_task",
    "list_tasks",
    "list_open_conflicts",
    "todo",
)
# The pre-step-33 loop had no per-turn tool-call cap, only the 20-LLM-call
# budget below -- this is a generous ceiling that should never bind under
# any workload 20 turns could plausibly produce, kept only because
# AgentSpec requires an explicit max_tool_calls (no "unbounded" option, by
# design -- see app.agent.runner.AgentSpec).
CHAT_MAX_TOOL_CALLS = 200
# Likewise: the old loop had no wall-clock deadline at all. Generous enough
# that a live chat turn is never cut short by it in practice; exists only
# because AgentSpec requires a deadline (a genuinely unbounded run is not a
# supported shape).
CHAT_DEADLINE_SECONDS = 300.0


def run_agent(
    db: Session,
    manager_id: str,
    user_message: str,
    history: List[Dict[str, Any]],
    client: Optional[GeminiClient] = None,
    candidates: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """
    Core Hermes-style converse-and-execute loop for Harry. A thin wrapper
    over app.agent.runner.run_spec (step 33's generalized loop) that
    preserves this function's exact pre-step-33 signature and return shape:
    compiles the dynamic system prompt (owner/project/team context, plus a
    candidate worklist when invoked from the agent heartbeat job) via
    compile_system_prompt and hands it to run_spec as `context_text` (never
    app.agent.kb_context.build_kb_context -- that's the separate, bounded
    job-agent context, not this surface's), so the outgoing prompt is
    byte-identical to what this function produced before the rewire.
    `run_context` is fresh per call -- see app/agent/tools.py's `todo`
    handler for why (scratch worklist state, never persisted).
    """
    client = client or get_client()
    run_context: Dict[str, Any] = {
        "candidates": {(c.kind, c.ref_key): c for c in candidates} if candidates else {}
    }

    context_text = compile_system_prompt(db, manager_id, candidates=candidates)

    spec = AgentSpec(
        name="chat",
        model=SMART_MODEL,
        instructions="",  # context_text alone IS the whole prompt, see docstring above
        tool_names=CHAT_TOOL_NAMES,
        max_llm_calls=20,  # today's IterationBudget(limit=20)
        max_tool_calls=CHAT_MAX_TOOL_CALLS,
        deadline_seconds=CHAT_DEADLINE_SECONDS,
        temperature=0.2,
    )

    result = run_spec(
        spec,
        db,
        manager_id,
        user_message,
        client=client,
        history=history,
        run_context=run_context,
        context_text=context_text,
        tool_registry=registry,
    )

    if result.stop_reason == "error":
        # Pre-step-33, an LLM-call exception simply propagated out of this
        # function uncaught (no try/except in the old loop) -- run_spec
        # catches it internally to let a caller inspect a partial
        # tool_trace, so re-raise here to preserve that "surfaces as an
        # exception, never swallowed into an apology reply" contract for
        # /api/chat and the Slack DM path.
        raise RuntimeError(result.error)

    final_reply = result.reply
    if final_reply is None:
        final_reply = "I ran out of steps (iteration budget exhausted) before I could complete your request."

    return {
        "reply": final_reply,
        "tool_trace": result.tool_trace,
    }
