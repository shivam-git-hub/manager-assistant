import json
from datetime import datetime
from sqlalchemy.orm import Session

from app.agent.registry import ToolRegistry
from app.agent.gemini_client import GeminiClient
from app.agent.harness import run_agent
from app.agent.prompts import compile_system_prompt
from app import timeservice

# -----------------------------------------------------------------------------
# 1. Registry Operations Tests
# -----------------------------------------------------------------------------


def test_registry_operations(db_session: Session):
    local_registry = ToolRegistry()

    def mock_handler(db, manager_id, run_context, x: int, y: int):
        return {"result": x + y}

    schema = {"name": "add", "description": "Adds two numbers"}
    local_registry.register("add", schema, mock_handler)

    defs = local_registry.get_tool_definitions()
    assert len(defs) == 1
    assert defs[0]["name"] == "add"

    res = local_registry.execute("add", {"x": 5, "y": 10}, db_session, manager_id="m1", run_context={})
    assert json.loads(res) == {"result": 15}

    def bad_handler(db, manager_id, run_context, text: str):
        raise ValueError("Something went wrong inside")

    local_registry.register("fail_tool", {"name": "fail_tool"}, bad_handler)
    res_fail = local_registry.execute("fail_tool", {"text": "abc"}, db_session, manager_id="m1")
    assert "ERROR: Something went wrong inside" in res_fail

    # Step 33: the old blind str[:8000]+"... [truncated]" clamp was replaced
    # by structure-aware compaction (app.agent.result_compaction) with a
    # per-tool cap (default AGENT_DEFAULT_TOOL_RESULT_CHARS=4000, here
    # unspecified so "heavy" gets that default) -- the guarantee is valid
    # JSON + a hard size ceiling, not a specific byte count. See
    # tests/test_result_compaction.py for the compaction unit tests proper.
    def heavy_handler(db, manager_id, run_context):
        return "A" * 9000

    local_registry.register("heavy", {"name": "heavy"}, heavy_handler)
    res_heavy = local_registry.execute("heavy", {}, db_session, manager_id="m1")
    assert len(res_heavy) <= 4000
    parsed_heavy = json.loads(res_heavy)  # must always be valid JSON now
    assert parsed_heavy["_truncated"] is True


# -----------------------------------------------------------------------------
# 2. Budget Limits Tests
# -----------------------------------------------------------------------------


def test_budget_exhaustion(db_session: Session, client):
    mock_response = {
        "candidates": [{
            "content": {"parts": [{"functionCall": {"name": "list_team", "args": {}}}]},
            "finishReason": "STOP",
        }],
        "usageMetadata": {},
    }

    def mock_transport(url, payload):
        return mock_response

    mock_client = GeminiClient(api_key="fake-key-for-testing", transport=mock_transport)

    res = run_agent(db_session, client.manager_id, "What's going on?", [], client=mock_client)
    assert "iteration budget exhausted" in res["reply"]
    assert len(res["tool_trace"]) == 20


# -----------------------------------------------------------------------------
# 3. End-to-End Converse Harness Tests
# -----------------------------------------------------------------------------


def test_harness_happy_path(db_session: Session, client):
    responses = [
        {"candidates": [{"content": {"parts": [{"functionCall": {"name": "list_team", "args": {}}}]}}]},
        {"candidates": [{"content": {"parts": [{"text": "No teammates yet."}]}}]},
    ]

    call_idx = 0
    payload_inspect = []

    def mock_transport(url, payload):
        nonlocal call_idx
        payload_inspect.append(payload)
        resp = responses[call_idx]
        call_idx += 1
        return resp

    mock_client = GeminiClient(api_key="fake-key-for-testing", transport=mock_transport)

    res = run_agent(db_session, client.manager_id, "Status report please.", [], client=mock_client)

    assert "No teammates yet." in res["reply"]
    assert len(res["tool_trace"]) == 1
    assert res["tool_trace"][0]["name"] == "list_team"

    assert len(payload_inspect) == 2
    messages_sent = payload_inspect[1]["contents"]
    assert len(messages_sent) == 3

    assert messages_sent[0]["role"] == "user"
    assert messages_sent[0]["parts"][0]["text"] == "Status report please."

    assert messages_sent[1]["role"] == "model"
    assert messages_sent[1]["parts"][0]["functionCall"]["name"] == "list_team"

    assert messages_sent[2]["role"] == "user"
    assert "functionResponse" in messages_sent[2]["parts"][0]
    assert messages_sent[2]["parts"][0]["functionResponse"]["name"] == "list_team"


# -----------------------------------------------------------------------------
# 4. Simulated Time Integration Tests
# -----------------------------------------------------------------------------


def test_prompt_reflects_current_time(db_session: Session, client, monkeypatch):
    monkeypatch.setattr(timeservice, "now_ist", lambda: datetime(2026, 7, 12, 15, 30, 0))
    prompt = compile_system_prompt(db_session, client.manager_id)
    assert "2026-07-12 15:30:00 IST" in prompt


# -----------------------------------------------------------------------------
# 5. History Recalls via API Endpoint
# -----------------------------------------------------------------------------


def test_chat_history_api_persistence(client):
    mock_resp = {"candidates": [{"content": {"parts": [{"text": "Hello Shivam, I am Harry."}]}}]}

    from app.agent import gemini_client

    original_client = gemini_client._client_singleton

    mock_client = GeminiClient(api_key="fake-key-for-testing", transport=lambda url, payload: mock_resp)
    gemini_client._client_singleton = mock_client

    try:
        resp1 = client.post("/api/chat", json={"message": "Who are you?"})
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1["reply"] == "Hello Shivam, I am Harry."

        history_resp = client.get("/api/chat/history")
        assert history_resp.status_code == 200
        history = history_resp.json()
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "Who are you?"
        assert history[1]["role"] == "assistant"
        assert history[1]["content"] == "Hello Shivam, I am Harry."

        clear_resp = client.delete("/api/chat/history")
        assert clear_resp.status_code == 200

        history_resp2 = client.get("/api/chat/history")
        assert len(history_resp2.json()) == 0
    finally:
        gemini_client._client_singleton = original_client
