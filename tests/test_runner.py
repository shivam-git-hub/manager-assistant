"""app.agent.runner.run_spec -- the generic tool-using agent loop (Phase A
foundation, not yet wired to any job). Each of the five independent stop
conditions gets its own test asserting both stop_reason and how many LLM
calls the fake transport actually saw, per the step's spec. Uses the same
GeminiClient(transport=...) injection idiom as tests/test_agent.py and the
FakeTransport idiom from tests/test_heartbeat_user.py."""
import json
import time

import pytest

from app import timeservice
from app.agent import runner as runner_module
from app.agent.gemini_client import GeminiClient
from app.agent.registry import ToolRegistry
from app.agent.runner import AgentSpec, run_spec


def _text_response(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def _tool_call_response(name, args=None):
    return {"candidates": [{"content": {"parts": [{"functionCall": {"name": name, "args": args or {}}}]}}]}


def _empty_response():
    return {"candidates": [{"content": {"parts": []}}]}


class SequenceTransport:
    """Returns each canned response in order; raises IndexError (visible as
    a loud test failure, not a silent hang) if the loop asks for more than
    scripted."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.call_count = 0
        self.payloads = []

    def __call__(self, url, payload):
        self.payloads.append(payload)
        resp = self.responses[self.call_count]
        self.call_count += 1
        return resp


def _spec(**overrides):
    defaults = dict(
        name="test-agent",
        model="gemini-3.5-flash",
        instructions="You are a test agent.",
        tool_names=("list_team", "list_meetings"),
        max_llm_calls=20,
        max_tool_calls=20,
        deadline_seconds=60.0,
    )
    defaults.update(overrides)
    return AgentSpec(**defaults)


# -----------------------------------------------------------------------------
# 1. max_llm_calls -> "budget"
# -----------------------------------------------------------------------------


def test_max_llm_calls_stop_reason_budget(db_session, client):
    transport = SequenceTransport([_tool_call_response("list_team")] * 3)
    gclient = GeminiClient(api_key="fake", transport=transport)
    spec = _spec(max_llm_calls=3, tool_names=("list_team",))

    result = run_spec(spec, db_session, client.manager_id, "go", client=gclient)

    assert result.stop_reason == "budget"
    assert transport.call_count == 3
    assert result.llm_calls == 3


# -----------------------------------------------------------------------------
# 2. deadline_seconds -> "deadline", checked BEFORE each LLM call
# -----------------------------------------------------------------------------


def test_deadline_zero_stops_before_any_llm_call(db_session, client):
    transport = SequenceTransport([_text_response("should never be reached")])
    gclient = GeminiClient(api_key="fake", transport=transport)
    spec = _spec(deadline_seconds=0.0)

    result = run_spec(spec, db_session, client.manager_id, "go", client=gclient)

    assert result.stop_reason == "deadline"
    assert transport.call_count == 0
    assert result.llm_calls == 0


def test_deadline_mid_run_stops_after_a_few_calls(db_session, client, monkeypatch):
    # Fake clock: each time.monotonic() call advances 100s AFTER the 2nd
    # read, so the deadline (set from the 1st read) is still in the future
    # for the first couple of loop-top checks and blown by the 3rd.
    reads = {"n": 0}
    real_monotonic = time.monotonic

    def fake_monotonic():
        reads["n"] += 1
        if reads["n"] <= 3:
            return 0.0
        return 1000.0

    monkeypatch.setattr(runner_module.time, "monotonic", fake_monotonic)

    transport = SequenceTransport([_tool_call_response("list_team")] * 5)
    gclient = GeminiClient(api_key="fake", transport=transport)
    spec = _spec(deadline_seconds=10.0, tool_names=("list_team",))

    result = run_spec(spec, db_session, client.manager_id, "go", client=gclient)

    assert result.stop_reason == "deadline"
    assert transport.call_count < 5  # stopped early, not by exhausting the script


# -----------------------------------------------------------------------------
# 3. max_tool_calls cumulative -> "tool_cap"
# -----------------------------------------------------------------------------


def test_max_tool_calls_stop_reason_tool_cap(db_session, client):
    two_calls_one_turn = {
        "candidates": [{
            "content": {
                "parts": [
                    {"functionCall": {"name": "list_team", "args": {}}},
                    {"functionCall": {"name": "list_meetings", "args": {}}},
                ]
            }
        }]
    }
    transport = SequenceTransport([two_calls_one_turn])
    gclient = GeminiClient(api_key="fake", transport=transport)
    spec = _spec(max_tool_calls=1, tool_names=("list_team", "list_meetings"))

    result = run_spec(spec, db_session, client.manager_id, "go", client=gclient)

    assert result.stop_reason == "tool_cap"
    assert result.tool_calls == 1
    assert len(result.tool_trace) == 1
    assert result.tool_trace[0]["name"] == "list_team"


# -----------------------------------------------------------------------------
# 4. Repeat-call guard: 3rd+ identical call is not re-executed, loop
#    continues (not a stop condition), cached result is returned.
# -----------------------------------------------------------------------------


def test_repeat_call_guard_skips_reexecution_from_third_call(db_session, client):
    calls = []
    local_registry = ToolRegistry()

    def counted_handler(db, manager_id, run_context):
        calls.append(1)
        return {"n": len(calls)}

    local_registry.register("counted", {"name": "counted", "description": "", "parameters": {"type": "object", "properties": {}}}, counted_handler)

    responses = [_tool_call_response("counted")] * 4 + [_text_response("done")]
    transport = SequenceTransport(responses)
    gclient = GeminiClient(api_key="fake", transport=transport)
    spec = _spec(tool_names=("counted",), max_llm_calls=10, max_tool_calls=10)

    result = run_spec(spec, db_session, client.manager_id, "go", client=gclient, tool_registry=local_registry)

    assert result.stop_reason == "final"
    assert result.reply == "done"
    assert len(calls) == 2  # handler only actually ran twice
    assert len(result.tool_trace) == 4  # but all 4 attempts are traced
    assert "[repeat]" not in result.tool_trace[0]["result_preview"]
    assert "[repeat]" not in result.tool_trace[1]["result_preview"]
    assert "[repeat]" in result.tool_trace[2]["result_preview"]
    assert "[repeat]" in result.tool_trace[3]["result_preview"]


# -----------------------------------------------------------------------------
# 5. Dead turn (no content, no tool calls) -> "empty_response"
# -----------------------------------------------------------------------------


def test_empty_turn_stop_reason_empty_response(db_session, client):
    transport = SequenceTransport([_empty_response()])
    gclient = GeminiClient(api_key="fake", transport=transport)
    spec = _spec()

    result = run_spec(spec, db_session, client.manager_id, "go", client=gclient)

    assert result.stop_reason == "empty_response"
    assert result.reply is None
    assert transport.call_count == 1


# -----------------------------------------------------------------------------
# Token accounting
# -----------------------------------------------------------------------------


def test_token_usage_accumulates_across_calls(db_session, client):
    resp1 = _tool_call_response("list_team")
    resp1["usageMetadata"] = {"promptTokenCount": 100, "candidatesTokenCount": 10}
    resp2 = _text_response("ok")
    resp2["usageMetadata"] = {"promptTokenCount": 150, "candidatesTokenCount": 5}

    transport = SequenceTransport([resp1, resp2])
    gclient = GeminiClient(api_key="fake", transport=transport)
    spec = _spec(tool_names=("list_team",))

    result = run_spec(spec, db_session, client.manager_id, "go", client=gclient)

    assert result.tokens_in == 250
    assert result.tokens_out == 15


# -----------------------------------------------------------------------------
# Tool allowlist: only allowed tool schemas sent, out-of-allowlist calls
# rejected with a correctable (not crashing) message.
# -----------------------------------------------------------------------------


def test_only_allowlisted_tool_schemas_sent_to_model(db_session, client):
    transport = SequenceTransport([_text_response("ok")])
    gclient = GeminiClient(api_key="fake", transport=transport)
    spec = _spec(tool_names=("list_team",))

    run_spec(spec, db_session, client.manager_id, "go", client=gclient)

    sent_tools = transport.payloads[0]["tools"][0]["functionDeclarations"]
    assert [t["name"] for t in sent_tools] == ["list_team"]


def test_call_to_tool_outside_allowlist_is_rejected_not_crashed(db_session, client):
    # A canned response can claim any tool name regardless of what schemas
    # were actually offered -- this is the defense-in-depth check on
    # registry.execute's allowed_names, exercised through the runner.
    responses = [_tool_call_response("list_meetings"), _text_response("recovered")]
    transport = SequenceTransport(responses)
    gclient = GeminiClient(api_key="fake", transport=transport)
    spec = _spec(tool_names=("list_team",))  # list_meetings deliberately excluded

    result = run_spec(spec, db_session, client.manager_id, "go", client=gclient)

    assert result.stop_reason == "final"
    assert result.reply == "recovered"
    assert "ERROR" in result.tool_trace[0]["result_preview"]
    assert "not in this agent's allowed tool list" in result.tool_trace[0]["result_preview"]


def test_unknown_tool_name_in_spec_raises_before_any_call(db_session, client):
    transport = SequenceTransport([_text_response("unreachable")])
    gclient = GeminiClient(api_key="fake", transport=transport)
    spec = _spec(tool_names=("totally_fake_tool_name",))

    with pytest.raises(ValueError, match="totally_fake_tool_name"):
        run_spec(spec, db_session, client.manager_id, "go", client=gclient)

    assert transport.call_count == 0


# -----------------------------------------------------------------------------
# json_mode / tools invariant, exercised at the runner's own call site.
# -----------------------------------------------------------------------------


def test_run_spec_never_sets_json_mode_alongside_tools(db_session, client):
    transport = SequenceTransport([_text_response("ok")])
    gclient = GeminiClient(api_key="fake", transport=transport)
    spec = _spec(tool_names=("list_team",))

    run_spec(spec, db_session, client.manager_id, "go", client=gclient)

    config = transport.payloads[0]["generationConfig"]
    assert "responseMimeType" not in config
    assert "tools" in transport.payloads[0]


# -----------------------------------------------------------------------------
# context_text: default builds from build_kb_context; explicit override
# bypasses it entirely (chat harness compatibility path).
# -----------------------------------------------------------------------------


def test_default_context_uses_build_kb_context(db_session, client, monkeypatch):
    monkeypatch.setattr(runner_module, "build_kb_context", lambda db, manager_id: "SENTINEL CONTEXT")
    transport = SequenceTransport([_text_response("ok")])
    gclient = GeminiClient(api_key="fake", transport=transport)
    spec = _spec(tool_names=())

    run_spec(spec, db_session, client.manager_id, "go", client=gclient)

    system_text = transport.payloads[0]["systemInstruction"]["parts"][0]["text"]
    assert "SENTINEL CONTEXT" in system_text


def test_explicit_context_text_bypasses_build_kb_context(db_session, client, monkeypatch):
    def boom(db, manager_id):
        raise AssertionError("build_kb_context must not be called when context_text is given")

    monkeypatch.setattr(runner_module, "build_kb_context", boom)
    transport = SequenceTransport([_text_response("ok")])
    gclient = GeminiClient(api_key="fake", transport=transport)
    spec = _spec(tool_names=())

    result = run_spec(spec, db_session, client.manager_id, "go", client=gclient, context_text="EXPLICIT")

    assert result.stop_reason == "final"
    system_text = transport.payloads[0]["systemInstruction"]["parts"][0]["text"]
    assert system_text == spec.instructions + "\n\n" + "EXPLICIT"


# -----------------------------------------------------------------------------
# LLM error -> stop_reason="error", caught not raised (wrapper's job to
# re-raise if it needs to preserve pre-step-33 exception behaviour).
# -----------------------------------------------------------------------------


def test_llm_error_caught_as_stop_reason_error(db_session, client):
    def boom(url, payload):
        raise RuntimeError("transport exploded")

    gclient = GeminiClient(api_key="fake", transport=boom)
    spec = _spec(tool_names=())

    result = run_spec(spec, db_session, client.manager_id, "go", client=gclient)

    assert result.stop_reason == "error"
    assert result.error == "transport exploded"
    assert result.reply is None
