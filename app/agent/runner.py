"""Generic tool-using agent loop (Phase A foundation for the future
heartbeat/dream/lint agent rewrites -- this step wires no job to it yet,
see app.agent.harness.run_agent for the one caller that exists today,
itself now a thin wrapper over this). Generalizes the converse-and-execute
loop that used to live only in harness.py: any AgentSpec (a playbook + an
explicit tool allowlist + budgets) can drive one run, with five
independent, individually-tested stop conditions instead of the old single
iteration-count budget.
"""
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.agent.gemini_client import GeminiClient, get_client
from app.agent.kb_context import build_kb_context
from app.agent.registry import registry as default_registry, ToolRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentSpec:
    """One agent's playbook + hard limits. `tool_names` is an explicit
    allowlist (never "give it everything the registry has") so a future KB
    agent's blast radius is legible from its spec alone -- e.g. the dream
    job's agent should never be handed send_message."""

    name: str
    model: str
    instructions: str
    tool_names: Tuple[str, ...]
    max_llm_calls: int
    max_tool_calls: int
    deadline_seconds: float
    temperature: float = 0.2


@dataclass
class AgentRunResult:
    reply: Optional[str]
    tool_trace: List[Dict[str, Any]]
    llm_calls: int
    tool_calls: int
    tokens_in: int
    tokens_out: int
    stop_reason: str  # final | budget | deadline | tool_cap | empty_response | error
    error: Optional[str] = None


def _repeat_key(name: str, args: Dict[str, Any]) -> str:
    payload = name + json.dumps(args, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _echo_assistant_turn(content: Optional[str], tool_calls: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Gemini turn symmetry (harness.py:81-97, load-bearing, preserved
    verbatim here): every functionCall the model made in a turn must be
    echoed back as that turn's assistant message before the matching
    functionResponse turns follow, or the next request's contents are
    malformed from Gemini's point of view."""
    return {
        "role": "assistant",
        "content": content or "",
        "tool_calls": [
            {
                "id": tc["id"],
                "type": "function",
                "function": {"name": tc["name"], "arguments": tc["arguments"]},
                "thought_signature": tc.get("thought_signature"),
            }
            for tc in tool_calls
        ],
    }


def run_spec(
    spec: AgentSpec,
    db: Session,
    manager_id: str,
    seed_message: str,
    *,
    client: Optional[GeminiClient] = None,
    history: Optional[List[Dict[str, Any]]] = None,
    run_context: Optional[Dict[str, Any]] = None,
    context_text: Optional[str] = None,
    tool_registry: Optional[ToolRegistry] = None,
) -> AgentRunResult:
    """Runs one agent to completion against `spec`'s budgets. System prompt
    is `spec.instructions + "\\n\\n" + (context_text or build_kb_context(...))`
    -- since build_kb_context always emits `## NOW` last, the current
    timestamp lands at the very end of the system prompt either way.

    Five independent stop conditions, checked in this order each iteration
    (deadline and the llm-call budget before ever calling the model again;
    the tool-cap and repeat-guard while executing that turn's tool calls;
    empty_response after a turn that requested nothing and said nothing):
    1. max_llm_calls reached -> "budget"
    2. deadline_seconds elapsed (time.monotonic(), checked before each LLM
       call) -> "deadline"
    3. max_tool_calls reached cumulatively -> "tool_cap"
    4. repeat-call guard: the 3rd+ identical (name, args) call (keyed by
       sha256 of name+sorted-json-args) is NOT re-executed -- the cached
       prior result is returned instead, prefixed so the model is told to
       stop repeating rather than move on with an "ERROR:"-shaped string
       (which tends to make models retry harder, not less).
    5. a turn with neither text content nor tool calls -> "empty_response"
       (previously silently fell through to the "ran out of steps" text
       even when the real cause was a dead turn, not budget exhaustion).

    An exception from client.chat() is caught into stop_reason="error" /
    `error` (not re-raised here) so a caller that wants to inspect a
    partial tool_trace after a mid-run failure can. Wrapper callers that
    must preserve "an LLM error surfaces as an exception" (see
    app.agent.harness.run_agent) re-raise using `error` themselves.
    """
    tool_registry = tool_registry or default_registry
    client = client or get_client()

    unknown = [n for n in spec.tool_names if n not in tool_registry.known_names()]
    if unknown:
        raise ValueError(f"AgentSpec '{spec.name}' references unknown tool name(s): {unknown}")

    resolved_context = context_text if context_text is not None else build_kb_context(db, manager_id)
    # Empty instructions (the chat-harness wrapper's case, where
    # compile_system_prompt's own output IS the whole prompt) means the
    # prompt is the context alone, byte-identical to calling
    # compile_system_prompt() directly -- no stray leading "\n\n".
    system_prompt = resolved_context if not spec.instructions else spec.instructions + "\n\n" + resolved_context

    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for msg in history or []:
        messages.append({
            "role": msg["role"],
            "content": msg["content"],
            **({"tool_calls": msg["tool_calls"]} if "tool_calls" in msg else {}),
        })
    messages.append({"role": "user", "content": seed_message})

    tool_defs = tool_registry.get_tool_definitions(names=spec.tool_names)
    openai_tools = [{"type": "function", "function": t} for t in tool_defs] if tool_defs else None

    tool_trace: List[Dict[str, Any]] = []
    repeat_cache: Dict[str, Dict[str, Any]] = {}
    llm_calls = 0
    tool_calls = 0
    tokens_in = 0
    tokens_out = 0
    final_reply: Optional[str] = None
    stop_reason: Optional[str] = None
    error: Optional[str] = None

    deadline_at = time.monotonic() + spec.deadline_seconds

    while True:
        if time.monotonic() >= deadline_at:
            stop_reason = "deadline"
            break
        if llm_calls >= spec.max_llm_calls:
            stop_reason = "budget"
            break

        try:
            response = client.chat(model=spec.model, messages=messages, tools=openai_tools, temperature=spec.temperature)
        except Exception as e:
            logger.error(f"AgentSpec '{spec.name}' LLM call failed: {e}", exc_info=True)
            error = str(e)
            stop_reason = "error"
            break
        llm_calls += 1

        usage = response.get("usage") or {}
        tokens_in += usage.get("prompt_tokens", 0)
        tokens_out += usage.get("completion_tokens", 0)

        content = response.get("content")
        turn_tool_calls = response.get("tool_calls") or []

        if not content and not turn_tool_calls:
            stop_reason = "empty_response"
            break

        if not turn_tool_calls:
            final_reply = content
            stop_reason = "final"
            break

        messages.append(_echo_assistant_turn(content, turn_tool_calls))

        hit_tool_cap = False
        for tc in turn_tool_calls:
            if tool_calls >= spec.max_tool_calls:
                hit_tool_cap = True
                break

            tc_name, tc_args = tc["name"], tc["arguments"]
            key = _repeat_key(tc_name, tc_args)
            cached = repeat_cache.get(key)

            if cached and cached["count"] >= 2:
                result_str = (
                    "[repeat] You already called this with identical arguments. Do not call it "
                    "again -- use the result below and move on.\n" + cached["result"]
                )
                cached["count"] += 1
            else:
                result_str = tool_registry.execute(
                    tc_name, tc_args, db, manager_id=manager_id, run_context=run_context, allowed_names=spec.tool_names
                )
                if cached is None:
                    repeat_cache[key] = {"result": result_str, "count": 1}
                else:
                    cached["result"] = result_str
                    cached["count"] += 1

            tool_calls += 1
            messages.append({"role": "tool", "tool_call_id": tc["id"], "name": tc_name, "content": result_str})
            tool_trace.append({
                "name": tc_name,
                "args": tc_args,
                "result_preview": result_str[:200] + ("..." if len(result_str) > 200 else ""),
            })

        if hit_tool_cap:
            stop_reason = "tool_cap"
            break
        # else: loop again for the next LLM call

    return AgentRunResult(
        reply=final_reply,
        tool_trace=tool_trace,
        llm_calls=llm_calls,
        tool_calls=tool_calls,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        stop_reason=stop_reason,
        error=error,
    )
