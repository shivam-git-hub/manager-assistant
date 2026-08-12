import json
import os
import shutil
import pytest
from datetime import datetime, timedelta

from app import timeservice
from app.database import ChatMessage, Workflow, CronJob, FollowupAgent, AgentActionLog
from app.agent.cos_agent import (
    get_compacted_history,
    run_cos_agent,
    handle_team_member_reply,
    check_and_run_manager_crons,
    calculate_next_run,
    get_chat_summary_path
)
from app.agent.gemini_client import GeminiClient
from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Employee

def _text_response(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}

def _tool_call_response(name, args=None):
    return {"candidates": [{"content": {"parts": [{"functionCall": {"name": name, "args": args or {}}}]}}]}

class SequenceTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.call_count = 0
        self.payloads = []

    def __call__(self, url, payload):
        self.payloads.append(payload)
        resp = self.responses[self.call_count]
        self.call_count += 1
        return resp

# -----------------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------------

def test_cos_agent_compaction(db_session, client, monkeypatch):
    """
    Adds 25 messages, triggers compaction, verifies older messages are deleted,
    and summary is stored and loaded successfully.
    """
    manager_id = client.manager_id
    
    # Clean old summary
    summary_path = get_chat_summary_path(manager_id)
    if os.path.exists(summary_path):
        os.remove(summary_path)

    # 1. Add 25 chat messages
    for i in range(25):
        msg = ChatMessage(
            role="user" if i % 2 == 0 else "assistant",
            content=f"Message {i}"
        )
        db_session.add(msg)
    db_session.commit()

    # Verify we currently have 25 messages
    count = db_session.query(ChatMessage).count()
    assert count == 25

    # 2. Mock GeminiClient to return a compacted summary text
    transport = SequenceTransport([_text_response("Compacted Summary text here.")])
    gclient = GeminiClient(api_key="fake", transport=transport)
    monkeypatch.setattr("app.agent.cos_agent.get_client", lambda: gclient)

    # 3. Retrieve history (should trigger compaction)
    history = get_compacted_history(db_session, manager_id)

    # 4. Verify compaction results
    new_count = db_session.query(ChatMessage).count()
    # Should keep exactly the last 5 messages in DB
    assert new_count == 5

    # File chat_summary.json should be written
    assert os.path.exists(summary_path)
    with open(summary_path, "r", encoding="utf-8") as f:
        summary_data = json.load(f)
        assert summary_data["summary"] == "Compacted Summary text here."

    # History returned should have 6 items: system-turn (summary) + 5 messages
    assert len(history) == 6
    assert history[0]["role"] == "system"
    assert "Compacted Summary text here." in history[0]["content"]
    assert history[-1]["content"] == "Message 24"


def test_cos_agent_crons_and_workflows(db_session, client, monkeypatch):
    """
    Tests creating a workflow and recurring cron, and executing crons via check_and_run_manager_crons.
    """
    manager_id = client.manager_id
    
    # 1. Register a Workflow via handler (also schedules its first CronJob)
    from app.agent.cos_agent import create_workflow_handler
    res = create_workflow_handler(
        db_session,
        manager_id,
        run_context={},
        name="Daily Brief",
        cron_expression="30m",
        prompt="Tell me my daily briefing",
        task_type="morning_brief"
    )
    assert res["status"] == "workflow_created"
    assert "workflow_id" in res
    assert "cron_job_id" in res
    
    # 2. Verify Workflow and CronJob are in DB
    wf = db_session.get(Workflow, res["workflow_id"])
    assert wf is not None
    assert wf.status == "active"
    
    cron = db_session.get(CronJob, res["cron_job_id"])
    assert cron is not None
    assert cron.status == "pending"
    assert cron.is_recurring is True
    
    # 3. Force cron to be due (move next_run_at back by 1 hour)
    cron.next_run_at = timeservice.now_ist() - timedelta(hours=1)
    db_session.commit()

    # Mock LLM response for run_cos_agent (invoked by cron)
    transport = SequenceTransport([_text_response("Here is your morning brief.")])
    gclient = GeminiClient(api_key="fake", transport=transport)
    monkeypatch.setattr("app.agent.cos_agent.get_client", lambda: gclient)

    # A cron reply's only observable delivery surface is the manager's
    # Slack DM (see run_cos_agent's channel="cron" branch -- it no longer
    # writes ChatMessage, see the assertion below), so give the manager a
    # slack_id and capture what gets sent, the same pattern
    # test_cos_agent_spawning_followups uses below.
    cp_db = ControlPlaneSessionLocal()
    try:
        manager_emp = cp_db.get(Employee, manager_id)
        manager_emp.slack_id = "USLACKMANAGER_CRON"
        cp_db.commit()
    finally:
        cp_db.close()

    slack_sends = []

    def fake_send(db, channel_id, text, subject=None):
        slack_sends.append((channel_id, text))
        from app.integrations.base import SendResult
        return SendResult(ok=True, platform_msg_id="fake_slack_out")

    from app.integrations.slack import connector as slack_connector
    monkeypatch.setattr(slack_connector, "send", fake_send)

    # 4. Run background cron sweeper
    # We mock list_provisioned_managers to return our mock manager
    from app.controlplane.models import list_provisioned_managers
    cp_db = ControlPlaneSessionLocal()
    try:
        mock_managers = [cp_db.get(Employee, manager_id)]
    finally:
        cp_db.close()
    monkeypatch.setattr("app.controlplane.models.list_provisioned_managers", lambda db: mock_managers)
    monkeypatch.setattr("app.tenancy.db.get_manager_session", lambda mid: db_session)
    
    check_and_run_manager_crons()
    
    # 5. Verify the cron was successfully executed and rescheduled
    # Get a fresh reference from DB since db_session was closed/re-opened inside the task
    cron = db_session.get(CronJob, res["cron_job_id"])
    assert cron.status == "pending"  # still pending because it's recurring and gets rescheduled!
    assert cron.last_run_at is not None
    # Next run time should be from_time + 30 minutes
    assert cron.next_run_at > timeservice.now_ist()
    
    # A cron tick's own directive/reply is deliberately NOT recorded in
    # ChatMessage (see run_cos_agent's channel="cron" branch) -- persisting
    # it there would bury the dashboard chat's real history under repeated
    # cron chatter on a fast cadence. Positive evidence the agent actually
    # ran and produced a reply is the Slack delivery instead.
    msgs = db_session.query(ChatMessage).order_by(ChatMessage.id.desc()).all()
    assert msgs == []
    assert slack_sends == [("USLACKMANAGER_CRON", "Here is your morning brief.")]


def test_cos_agent_spawning_followups(db_session, client, monkeypatch):
    """
    Tests spawning a followup chat agent, receiving a reply, running the Chat Agent LLM,
    and transitioning the status to 'reported' with proactively notifying the manager.
    """
    manager_id = client.manager_id
    
    # 1. Create a dummy employee (non-manager) in Control Plane
    # Also explicitly assign a slack_id to our manager Employee so they receive notifications
    cp_db = ControlPlaneSessionLocal()
    dummy_emp = None
    try:
        # Update manager with a slack_id
        manager_emp = cp_db.get(Employee, manager_id)
        if manager_emp:
            manager_emp.slack_id = "USLACKMANAGER"
            
        # Create non-manager Employee Bob
        dummy_emp = Employee(
            id="employee_bob_iyer",
            email="bob.iyer@example.com",
            name="Bob Iyer",
            slack_id="USLACKBOB",
            is_manager=False
        )
        cp_db.add(dummy_emp)
        cp_db.commit()
        cp_db.refresh(dummy_emp)
    except Exception:
        # already exists
        cp_db.rollback()
        dummy_emp = cp_db.get(Employee, "employee_bob_iyer")
    finally:
        cp_db.close()
        
    # Mock direct contact send methods
    slack_sends = []
    def fake_send(db, channel_id, text, subject=None):
        slack_sends.append((channel_id, text))
        from app.integrations.base import SendResult
        return SendResult(ok=True, platform_msg_id="fake_slack_out")
    
    from app.integrations.slack import connector as slack_connector
    monkeypatch.setattr(slack_connector, "send", fake_send)
    
    # Mock LLM for the initial greeting
    transport = SequenceTransport([_text_response("Hi Bob, could you update us on the layout?")])
    gclient = GeminiClient(api_key="fake", transport=transport)
    monkeypatch.setattr("app.agent.cos_agent.get_client", lambda: gclient)
    
    # 2. Spawn a followup chat agent
    from app.agent.cos_agent import spawn_followup_chat_agent_handler
    res = spawn_followup_chat_agent_handler(
        db_session,
        manager_id,
        run_context={},
        recipient_employee_id="employee_bob_iyer",
        scoped_project_ids=["project_pulse_ai"],
        instructions="Ask Bob if the layout is ready"
    )
    assert res["status"] == "spawned_successfully"
    assert "followup_id" in res
    assert res["initial_message"] == "Hi Bob, could you update us on the layout?"
    assert slack_sends[-1] == ("USLACKBOB", "Hi Bob, could you update us on the layout?")
    
    # 3. Simulate team member (Bob) replying on Slack
    # Bob says: "Yes, layout is completed! No blockers."
    # Mock LLM for Chat Agent reply (which outputs a reporting tag)
    agent_reply_with_report = (
        "Thanks Bob, I will report this to Shivam. "
        "[REPORT_COS: Bob says the layout is completed and there are no blockers.]"
    )
    transport_reply = SequenceTransport([_text_response(agent_reply_with_report)])
    gclient_reply = GeminiClient(api_key="fake", transport=transport_reply)
    monkeypatch.setattr("app.agent.cos_agent.get_client", lambda: gclient_reply)
    
    # Clear send tracker
    slack_sends.clear()
    
    # Call handle_team_member_reply
    handle_team_member_reply(
        db_session,
        manager_id=manager_id,
        recipient_employee_id="employee_bob_iyer",
        message_text="Yes, layout is completed! No blockers.",
        source_channel="slack"
    )
    
    # 4. Verify followup agent's status transitioned to 'reported'
    agent = db_session.get(FollowupAgent, res["followup_id"])
    assert agent.status == "reported"
    assert agent.cos_context == "Bob says the layout is completed and there are no blockers."
    
    # Verify the closing text sent back to Bob had the [REPORT_COS] tag stripped!
    # Bob should receive only the clean conversational text.
    assert len(slack_sends) >= 2  # one to Bob (clean reply) and one to Manager (FYI notification)
    
    # Check Bob's message (sent to Bob's slack_id USLACKBOB)
    bob_messages = [txt for cid, txt in slack_sends if cid == "USLACKBOB"]
    assert len(bob_messages) == 1
    assert "Thanks Bob" in bob_messages[0]
    assert "[REPORT_COS" not in bob_messages[0]  # Tag completely stripped!
    
    # Check Manager's message (sent to manager's slack_id USLACKMANAGER)
    mgr_messages = [txt for cid, txt in slack_sends if cid == "USLACKMANAGER"]
    assert len(mgr_messages) == 1
    assert "Bob says the layout is completed" in mgr_messages[0]
