"""End-to-end agent heartbeat job with a fake LLM client:
seeded meeting -> a send_message tool call -> AgentActionLog row ->
second tick is a no-op (idempotency)."""
import json
import uuid
from datetime import timedelta

import pytest

from app import timeservice
from app.agent.gemini_client import GeminiClient
from app.database import AgentActionLog, ChatMessage, Meeting
from app.projectkb.jobs import agent_heartbeat


def test_no_candidates_skips_llm_call(db_session, client):
    calls = []

    def transport(url, payload):
        calls.append(payload)
        return {"candidates": [{"content": {"parts": [{"text": "n/a"}]}}]}

    fake_client = GeminiClient(api_key="fake", transport=transport)
    result = agent_heartbeat.run(db_session, client.manager_id, client=fake_client)

    assert result["acted"] is False
    assert calls == []


def test_pre_meeting_brief_tick_sends_and_logs(db_session, client, manager_employee_id):
    now = timeservice.now_ist()
    meeting = Meeting(title="Standup", starts_at=now + timedelta(minutes=45), attendees=json.dumps(["Bob"]), status="scheduled")
    db_session.add(meeting)
    db_session.commit()

    def transport(url, payload):
        # Inspect what candidate ref_key the model was handed, echo it back
        # in the tool call -- mirrors what a real model does per the system
        # prompt's instructions.
        system_text = payload["systemInstruction"]["parts"][0]["text"] if "systemInstruction" in payload else ""
        assert f"meeting:{meeting.id}" in system_text
        return {
            "candidates": [{
                "content": {
                    "parts": [{
                        "functionCall": {
                            "name": "send_message",
                            "args": {
                                "channel": "slack",
                                "target": "manager",
                                "text": "Standup starts in 45 min.",
                                "candidate_kind": "pre_meeting_brief",
                                "candidate_ref_key": f"meeting:{meeting.id}",
                            },
                        }
                    }]
                }
            }]
        }

    call_count = {"n": 0}

    def stateful_transport(url, payload):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return transport(url, payload)
        return {"candidates": [{"content": {"parts": [{"text": "Done."}]}}]}

    fake_client = GeminiClient(api_key="fake", transport=stateful_transport)

    result = agent_heartbeat.run(db_session, client.manager_id, client=fake_client)
    assert result["acted"] is True
    assert result["candidate_count"] == 1

    log_row = db_session.query(AgentActionLog).filter(AgentActionLog.action_type == "pre_meeting_brief").first()
    assert log_row is not None
    assert log_row.ref_key == f"meeting:{meeting.id}"

    db_session.refresh(meeting)
    assert meeting.brief_sent_at is not None

    # Second tick: the meeting is now briefed, no new candidate -> no LLM call.
    calls_second_tick = []

    def transport2(url, payload):
        calls_second_tick.append(payload)
        return {"candidates": [{"content": {"parts": [{"text": "n/a"}]}}]}

    fake_client2 = GeminiClient(api_key="fake", transport=transport2)
    result2 = agent_heartbeat.run(db_session, client.manager_id, client=fake_client2)
    assert result2["acted"] is False
    assert calls_second_tick == []
