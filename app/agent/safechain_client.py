"""SafeChain-backed LLM client -- the company-laptop replacement for
GeminiClient. Selected by `get_client()` (app.agent.gemini_client) when
LLM_PROVIDER=safechain, so nothing downstream changes: every caller
(app.agent.runner's run_spec loop, all five agents including cos_agent.py's
Chief of Staff, the projectkb jobs) keeps calling `.chat(model, messages,
tools=None, temperature=..., max_output_tokens=..., json_mode=False)` and
reading back `{content, tool_calls, finish_reason, usage}` -- the exact
shape GeminiClient returns.

Internally this goes through the enterprise LangChain stack via
`safechain.lcel.model`, which resolves a chat model from the active YAML
config (config/config_e<N>.yaml, selected by the CONFIG_PATH/DEPLOY_ENV env
vars safechain's own `ee_config.Config.from_env()` reads -- NOT anything
this app sets) and authenticates via IDaaS using the CIBIS_* creds in
.env. The `model` argument every caller passes (a Gemini model name like
"gemini-3.5-flash") is IGNORED here -- the concrete deployment is chosen by
SAFECHAIN_MODEL_INDEX against the YAML `models:` catalog instead (verified
against config_e1.yaml: index "1" -> a Llama-3.3-70B deployment behind an
OpenAI-compatible shim). Practically this means SMART_MODEL/FLASH_MODEL
tiering collapses onto whatever SAFECHAIN_MODEL_INDEX points at unless a
second, cheaper catalog entry is added and mapped in separately later.

Tool calling: verified against the real gateway (index "1") to return
genuine `tool_calls`, not just prose describing what it wanted to do -- see
the project's context notes. `bind_tools()` takes the same OpenAI-tool-dict
shape app.agent.runner builds, no sanitizer needed on this backend (unlike
Gemini's schema quirks, see CLAUDE.md critical bug #1).

All safechain/langchain_core imports are LAZY (inside functions, not at
module top): safechain is a conda-env-only package on the company laptop,
never in this repo's requirements.txt, so importing this module on a
machine without it installed must not blow up just because
app.agent.gemini_client (imported almost everywhere) references it -- it's
only actually imported when LLM_PROVIDER=safechain routes a real call here.
"""

import json
import logging
import os
import uuid
from typing import Any, Dict, List, Optional

from app.agent.rate_limit import get_rate_limiter

logger = logging.getLogger(__name__)

# Which entry in the YAML `models:` catalog to use -- see config_e1.yaml's
# `models:` block. Overridable so a deploy can point at a different gateway
# deployment (or a second, cheaper index once one exists) without a code
# change.
SAFECHAIN_MODEL_INDEX = os.getenv("SAFECHAIN_MODEL_INDEX", "1")


def _json_parse_if_string(val: Any) -> Any:
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return {"output": val}
    return val


def _to_langchain_messages(messages: List[Dict[str, Any]]) -> List[Any]:
    """OpenAI-shaped message dicts (the same shape app.agent.runner builds
    for GeminiClient) -> LangChain message objects.

    Tool-result turns are the one subtlety: run_spec (app/agent/runner.py)
    appends `{"role": "tool", "tool_call_id": tc["id"], "name": tc_name,
    "content": result_str}` after executing a tool call -- LangChain's
    ToolMessage needs that `tool_call_id` (not `id`) to line the result back
    up with the AIMessage.tool_calls entry that requested it, or the model
    can't tell which call a result answers."""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    lc_messages: List[Any] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content") or ""

        if role == "system":
            lc_messages.append(SystemMessage(content=content))
        elif role == "user":
            lc_messages.append(HumanMessage(content=content))
        elif role == "assistant":
            tool_calls = []
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function", tc)
                tool_calls.append({
                    "id": tc.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                    "name": fn.get("name"),
                    "args": _json_parse_if_string(fn.get("arguments") or {}),
                })
            lc_messages.append(AIMessage(content=content, tool_calls=tool_calls))
        elif role == "tool":
            lc_messages.append(ToolMessage(
                content=content,
                tool_call_id=msg.get("tool_call_id") or msg.get("id") or "",
            ))
        else:
            # Unknown role -- treat as a user turn so nothing is silently dropped.
            lc_messages.append(HumanMessage(content=content))

    return lc_messages


def _dump_payload(messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]], truncate: bool = True) -> str:
    """Render the exact outgoing request in a human-readable, greppable form
    so a gateway/firewall rejection can be traced to the offending section.
    Each message is labelled with its role (and name/tool_calls when
    present); content is shown in full unless `truncate` is set."""
    limit = 4000 if truncate else None
    lines: List[str] = []
    for i, msg in enumerate(messages):
        role = msg.get("role", "?")
        content = msg.get("content") or ""
        if limit and len(content) > limit:
            content = content[:limit] + f"... [truncated {len(content) - limit} chars]"
        header = f"[{i}] role={role}"
        if msg.get("name"):
            header += f" name={msg['name']}"
        if msg.get("tool_calls"):
            tc_names = [(tc.get("function", tc) or {}).get("name") for tc in msg["tool_calls"]]
            header += f" tool_calls={tc_names}"
        lines.append(f"{header}\n{content}")
    tool_names = [(t.get("function", t) or {}).get("name") for t in (tools or [])]
    lines.append(f"[tools] {tool_names}")
    lines.append(
        f"[stats] messages={len(messages)} "
        f"total_content_chars={sum(len(m.get('content') or '') for m in messages)}"
    )
    return "\n".join(lines)


def _log_payload(messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]], temperature: float, index: str) -> None:
    """Optional pre-call dump of the outgoing payload, gated on
    LLM_LOG_PAYLOAD ("true"/"1" -> truncated dump, "full" -> untruncated).
    Off by default -- this can contain message content."""
    mode = os.getenv("LLM_LOG_PAYLOAD", "").strip().lower()
    if mode not in ("true", "1", "full", "yes"):
        return
    logger.info(
        "[safechain] outgoing payload (index=%s temp=%s):\n%s",
        index, temperature, _dump_payload(messages, tools, truncate=(mode != "full")),
    )


def _strip_code_fence(text: str) -> str:
    """A gateway-fronted model asked for json_mode may still wrap its
    answer in a markdown code fence (```json ... ```) or a stray sentence
    before/after the JSON -- this is a best-effort cleanup, not a parser;
    the caller (app.projectkb.llm_json) still validates the result."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t.lstrip("`")
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


class SafeChainClient:
    """Drop-in replacement for GeminiClient backed by safechain/LangChain."""

    def __init__(self, index: Optional[str] = None):
        self.index = index or SAFECHAIN_MODEL_INDEX

    def chat(
        self,
        model: str,  # kept for signature parity with GeminiClient; ignored, see module docstring
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.3,
        max_output_tokens: int = 4096,
        json_mode: bool = False,
    ) -> Dict[str, Any]:
        if json_mode and tools:
            raise ValueError(
                "json_mode cannot be combined with tools -- the gateway rejects "
                "response_format alongside function declarations (same constraint "
                "as GeminiClient, see CLAUDE.md critical bug #1's sibling issue)."
            )

        from safechain.lcel import model as safechain_model

        lc_messages = _to_langchain_messages(messages)

        # Process-wide call-rate cap, shared with GeminiClient.
        get_rate_limiter().acquire()

        # Resolve the chat model from the YAML config + mint/refresh the
        # IDaaS token. safechain caches sessions internally, so this is
        # cheap after the first call. Requires CONFIG_PATH/DEPLOY_ENV to be
        # set in the environment -- see .env.example.
        llm = safechain_model(
            self.index,
            model_kwargs={"temperature": temperature, "max_tokens": max_output_tokens},
        )

        if tools:
            llm = llm.bind_tools(tools)
        elif json_mode:
            try:
                llm = llm.bind(response_format={"type": "json_object"})
            except Exception:
                logger.debug("[safechain] gateway does not support response_format; relying on prompt for JSON")

        _log_payload(messages, tools, temperature, self.index)

        try:
            ai = llm.invoke(lc_messages)
        except Exception as e:
            # The Amex AI firewall/DLP layer rejects some payloads outright
            # ("input appears to violate Company policy") -- always dump the
            # exact payload that tripped it so the offending
            # content/section is identifiable, regardless of the specific
            # rejection reason. Re-raised so run_spec's own try/except
            # records stop_reason="error" with this message, same contract
            # GeminiClient's callers already rely on.
            logger.error(
                "[safechain] llm.invoke failed (index=%s, temp=%s, model_kwarg=%s): %s\n"
                "----- REJECTED LLM PAYLOAD BEGIN -----\n%s\n"
                "----- REJECTED LLM PAYLOAD END -----",
                self.index, temperature, model, e,
                _dump_payload(messages, tools, truncate=False),
            )
            raise

        # --- content ---
        content = ai.content
        if isinstance(content, list):
            # Some models return content as a list of parts; join the text bits.
            content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
        text_content = content or None
        if json_mode and text_content:
            text_content = _strip_code_fence(text_content)

        # --- tool calls ---
        tool_calls = []
        for tc in getattr(ai, "tool_calls", None) or []:
            tool_calls.append({
                "id": tc.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                "name": tc.get("name"),
                "arguments": tc.get("args") or {},
            })
        if tool_calls:
            logger.info(f"[safechain] model requested {len(tool_calls)} tool call(s): "
                        f"{[t['name'] for t in tool_calls]}")

        # --- finish_reason ---
        meta = getattr(ai, "response_metadata", None) or {}
        finish_reason = meta.get("finish_reason") or meta.get("stop_reason") or ("TOOL_CALLS" if tool_calls else "STOP")

        # --- usage ---
        usage_meta = getattr(ai, "usage_metadata", None) or {}
        usage = {
            "prompt_tokens": usage_meta.get("input_tokens", 0),
            "completion_tokens": usage_meta.get("output_tokens", 0),
            "total_tokens": usage_meta.get("total_tokens", 0),
        }

        return {
            "content": text_content,
            "tool_calls": tool_calls,
            "finish_reason": finish_reason,
            "usage": usage,
        }


# Cached singleton, mirroring gemini_client.get_client().
_client_singleton: Optional[SafeChainClient] = None


def get_safechain_client() -> SafeChainClient:
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = SafeChainClient()
    return _client_singleton
