import pytest
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock
from sqlalchemy.orm import Session

from app.agent.budget import IterationBudget
from app.agent.registry import ToolRegistry, registry
from app.agent.gemini_client import GeminiClient
from app.agent.harness import run_agent
from app.agent.prompts import compile_system_prompt
from app.database import TeamMember, Project, ChatMessage, Task
from app.outbound import OutboundQueue
from app import timeservice

# -----------------------------------------------------------------------------
# 1. Registry Operations Tests
# -----------------------------------------------------------------------------

def test_registry_operations(db_session: Session):
    local_registry = ToolRegistry()
    
    # Happy path
    def mock_handler(db, x: int, y: int):
        return {"result": x + y}
        
    schema = {"name": "add", "description": "Adds two numbers"}
    local_registry.register("add", schema, mock_handler)
    
    defs = local_registry.get_tool_definitions()
    assert len(defs) == 1
    assert defs[0]["name"] == "add"
    
    res = local_registry.execute("add", {"x": 5, "y": 10}, db_session)
    assert json.loads(res) == {"result": 15}

    # Exception safety
    def bad_handler(db, text: str):
        raise ValueError("Something went wrong inside")
        
    local_registry.register("fail_tool", {"name": "fail_tool"}, bad_handler)
    res_fail = local_registry.execute("fail_tool", {"text": "abc"}, db_session)
    assert "ERROR: Something went wrong inside" in res_fail

    # Output Clamping (8000 character limit)
    def heavy_handler(db):
        return "A" * 9000
        
    local_registry.register("heavy", {"name": "heavy"}, heavy_handler)
    res_heavy = local_registry.execute("heavy", {}, db_session)
    assert len(res_heavy) == 8015  # 8000 + "... [truncated]" length (15 chars)
    assert res_heavy.endswith("... [truncated]")


# -----------------------------------------------------------------------------
# 2. Budget Limits Tests
# -----------------------------------------------------------------------------

def test_budget_exhaustion(db_session: Session):
    # Mock client that ALWAYS returns a tool call to run_agent, causing infinite loop
    mock_response = {
        "candidates": [{
            "content": {
                "parts": [{
                    "functionCall": {
                        "name": "get_current_time",
                        "args": {}
                    }
                }]
            },
            "finishReason": "STOP"
        }],
        "usageMetadata": {}
    }
    
    def mock_transport(url, payload):
        return mock_response
        
    mock_client = GeminiClient(api_key="fake-key-for-testing", transport=mock_transport)
    
    # Harness should run out of budget and return best-effort reply
    res = run_agent(db_session, "What time is it?", [], client=mock_client)
    assert "iteration budget exhausted" in res["reply"]
    assert len(res["tool_trace"]) == 20  # Maxed out budget of 20 steps


# -----------------------------------------------------------------------------
# 3. End-to-End Converse Harness Tests
# -----------------------------------------------------------------------------

def test_harness_happy_path(db_session: Session):
    # Setup some test team members and projects
    db_session.add(TeamMember(id="U_BOB", name="Bob", role="Developer", slack_handle="U_BOB", outlook_email="bob@co.com"))
    db_session.add(Project(id=1, name="Phoenix", status="active", health="yellow", health_reasons="Alice is out."))
    db_session.commit()
    
    # Mock sequence: 
    # Turn 1: request get_current_time
    # Turn 2: final answer citing [T5]
    responses = [
        {
            "candidates": [{
                "content": {
                    "parts": [{
                        "functionCall": {
                            "name": "get_current_time",
                            "args": {}
                        }
                    }]
                }
            }]
        },
        {
            "candidates": [{
                "content": {
                    "parts": [{
                        "text": "The time is currently 10:30 IST. Phoenix is in Yellow state [T5]."
                    }]
                }
            }]
        }
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
    
    # Run harness
    res = run_agent(db_session, "Status report please.", [], client=mock_client)
    
    assert "The time is currently 10:30 IST" in res["reply"]
    assert len(res["tool_trace"]) == 1
    assert res["tool_trace"][0]["name"] == "get_current_time"
    
    # Verify Symmetrical Turn ordering and format passed back to the LLM
    assert len(payload_inspect) == 2
    
    # Check messages array in second payload
    messages_sent = payload_inspect[1]["contents"]
    assert len(messages_sent) == 3 # user message + echoed tool_call + tool response
    
    # Check user message
    assert messages_sent[0]["role"] == "user"
    assert messages_sent[0]["parts"][0]["text"] == "Status report please."
    
    # Check echoed assistant tool_call (model turn)
    assert messages_sent[1]["role"] == "model"
    assert "functionCall" in messages_sent[1]["parts"][0]
    assert messages_sent[1]["parts"][0]["functionCall"]["name"] == "get_current_time"
    
    # Check tool response (user turn)
    assert messages_sent[2]["role"] == "user"
    assert "functionResponse" in messages_sent[2]["parts"][0]
    assert messages_sent[2]["parts"][0]["functionResponse"]["name"] == "get_current_time"


# -----------------------------------------------------------------------------
# 4. Simulated Time Integration Tests
# -----------------------------------------------------------------------------

def test_simulated_time_prompt_volatile(db_session: Session):
    # Set clock anchor
    timeservice.set_time(datetime(2026, 7, 12, 15, 30, 0))
    
    # Compile prompt
    prompt = compile_system_prompt(db_session)
    assert "2026-07-12 15:30:00 IST" in prompt


# -----------------------------------------------------------------------------
# 5. Outbound Gating Constraints via Tools
# -----------------------------------------------------------------------------

def test_outbound_gating_via_tools(db_session: Session):
    # Setup recipient
    db_session.add(TeamMember(id="U_BOB", name="Bob", role="Dev", slack_handle="U_BOB", outlook_email="bob@co.com"))
    db_session.commit()

    # Move simulated clock to midnight (quiet hours)
    timeservice.set_time(datetime(2026, 7, 12, 23, 0, 0))
    
    # Call send_slack_dm tool handler
    from app.agent.tools import send_slack_dm_handler
    res = send_slack_dm_handler(db_session, "U_BOB", "Check the server logs.")
    
    # Output must show message is held
    assert res["success"] is True
    assert res["status"] == "held"
    assert res["release_at"] == datetime(2026, 7, 13, 9, 0, 0) # Next morning 9:00 AM

    # Row must exist in outbound queue
    queue_row = db_session.query(OutboundQueue).first()
    assert queue_row is not None
    assert queue_row.status == "held"
    assert queue_row.channel_type == "slack"
    assert "Check the server logs." in queue_row.payload


# -----------------------------------------------------------------------------
# 6. History Recalls via API Endpoint
# -----------------------------------------------------------------------------

def test_chat_history_api_persistence(client):
    # Mock the singleton client in gemini_client
    mock_resp = {
        "candidates": [{
            "content": {
                "parts": [{
                    "text": "Hello Shivam, I am Harry."
                }]
            }
        }]
    }
    
    from app.agent import gemini_client
    original_client = gemini_client._client_singleton
    
    mock_client = GeminiClient(api_key="fake-key-for-testing", transport=lambda url, payload: mock_resp)
    gemini_client._client_singleton = mock_client
    
    try:
        # Submit first chat message
        resp1 = client.post("/api/chat", json={"message": "Who are you?"})
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1["reply"] == "Hello Shivam, I am Harry."
        
        # Verify history is populated
        history_resp = client.get("/api/chat/history")
        assert history_resp.status_code == 200
        history = history_resp.json()
        assert len(history) == 2  # user + assistant
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "Who are you?"
        assert history[1]["role"] == "assistant"
        assert history[1]["content"] == "Hello Shivam, I am Harry."
        
        # Clear history endpoint check
        clear_resp = client.delete("/api/chat/history")
        assert clear_resp.status_code == 200
        
        # History must now be empty
        history_resp2 = client.get("/api/chat/history")
        assert len(history_resp2.json()) == 0
        
    finally:
        gemini_client._client_singleton = original_client
