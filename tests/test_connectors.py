import pytest
from app.integrations.outlook import clean_html


def _seed_slack_installation(client, team_id="T_TEST", app_id="A_TEST"):
    """An installed Agent is required for webhook routing (step 17 piece 2b)
    to resolve which manager's db.sqlite an event belongs to -- routed by
    api_app_id, not team_id (see slack_payload fixtures below)."""
    from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Agent

    db = ControlPlaneSessionLocal()
    db.add(Agent(
        id=f"agent-{client.manager_id}", name="Test Agent", slack_app_id=app_id,
        slack_client_id="cid", slack_client_secret="csecret", slack_signing_secret="ssecret",
        manager_id=client.manager_id, team_id=team_id, bot_token="xoxb-fake",
    ))
    db.commit()
    db.close()

def test_html_cleaner():
    # Test basic stripping
    html = "<div><p>Hello World</p><br>This is a new line.</div>"
    assert "Hello World" in clean_html(html)
    assert "This is a new line" in clean_html(html)

    # Test list formatting
    html_list = "<ul><li>Task A</li><li>Task B</li></ul>"
    cleaned = clean_html(html_list)
    assert "* Task A" in cleaned
    assert "* Task B" in cleaned

    # Test headers
    html_header = "<h1>Main Heading</h1><h2>Sub Heading</h2>"
    cleaned_header = clean_html(html_header)
    assert "# Main Heading" in cleaned_header
    assert "## Sub Heading" in cleaned_header


def test_team_crud(client):
    # Create member
    payload = {
        "id": "U12345",
        "name": "Shivam",
        "role": "Lead Architect",
        "slack_handle": "U12345",
        "outlook_email": "shivam@company.com",
        "timezone": "Asia/Kolkata"
    }
    response = client.post("/api/team", json=payload)
    assert response.status_code == 201
    assert response.json()["name"] == "Shivam"
    assert response.json()["role"] == "Lead Architect"

    # Get team
    response = client.get("/api/team")
    assert response.status_code == 200
    members = response.json()
    assert len(members) >= 1
    shivam_members = [m for m in members if m["id"] == "U12345"]
    assert len(shivam_members) == 1
    assert shivam_members[0]["name"] == "Shivam"


def test_slack_webhook_verification(client):
    # Test slack URL verification challenge
    payload = {
        "token": "Jhj5dZrVa7pa7bEecqgS65v8",
        "challenge": "3eZbrw1aBm2rZg4S3n6z7v5m",
        "type": "url_verification"
    }
    response = client.post("/api/integrations/slack/webhook", json=payload)
    assert response.status_code == 200
    assert response.text == "3eZbrw1aBm2rZg4S3n6z7v5m"


def test_slack_webhook_ingestion(client):
    # 0. A manager TeamMember is still needed for DM counterpart resolution
    # (normalize's short-circuit); the tracked-contacts allowlist is gone
    # (step 20 -- everything gets stored).
    client.post("/api/team", json={
        "id": "U_MANAGER", "name": "Shivam", "role": "Manager", "slack_handle": "U_MANAGER"
    })
    _seed_slack_installation(client)

    # 1. Create a matching team member
    member_payload = {
        "id": "USLACK_ALICE",
        "name": "Alice Developer",
        "role": "Backend dev",
        "slack_handle": "U_ALICE_123",
        "outlook_email": "alice@company.com"
    }
    client.post("/api/team", json=member_payload)

    # 2. Mock a real Slack DM event (channel_type "im" is how Slack marks a
    # 1:1 DM; a public/private channel post never involves the manager the
    # way a DM does, so it wouldn't be stored)
    slack_payload = {
        "team_id": "T_TEST",
        "api_app_id": "A_TEST",
        "type": "event_callback",
        "event": {
            "type": "message",
            "client_msg_id": "client_msg_abc_123",
            "user": "U_ALICE_123",
            "channel": "D_ALICE_MANAGER",
            "channel_type": "im",
            "text": "Finished the SQLite schema setup!",
            "ts": "1789025345.000000"
        }
    }

    response = client.post("/api/integrations/slack/webhook", json=slack_payload)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["sender_mapped"] == "Alice Developer"

    # 3. Check message exists in unified table
    msg_response = client.get("/api/messages?source=slack")
    assert msg_response.status_code == 200
    messages = msg_response.json()
    assert len(messages) == 1
    assert messages[0]["content"] == "Finished the SQLite schema setup!"
    assert messages[0]["sender_mapped_name"] == "Alice Developer"
    assert messages[0]["source"] == "slack"

    # 4. Assert idempotency (resending same slack webhook is ignored)
    dup_response = client.post("/api/integrations/slack/webhook", json=slack_payload)
    assert dup_response.status_code == 200
    assert dup_response.json()["status"] == "ignored"
    assert "duplicate" in dup_response.json()["detail"]


def test_slack_webhook_unknown_counterpart_now_stored(client):
    """Step 20 inversion proof: a DM from someone with no TeamMember row
    and (in v1 terms) no allowlist entry IS stored now -- track everything,
    the blocklist decides what not to process later."""
    client.post("/api/team", json={
        "id": "U_MANAGER", "name": "Shivam", "role": "Manager", "slack_handle": "U_MANAGER"
    })
    _seed_slack_installation(client)
    slack_payload = {
        "team_id": "T_TEST",
        "api_app_id": "A_TEST",
        "type": "event_callback",
        "event": {
            "type": "message",
            "client_msg_id": "client_msg_untracked_1",
            "user": "U_RANDOM",
            "channel": "D_RANDOM_MANAGER",
            "channel_type": "im",
            "text": "hello",
            "ts": "1789025999.000000"
        }
    }
    response = client.post("/api/integrations/slack/webhook", json=slack_payload)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

    messages = client.get("/api/messages?source=slack").json()
    assert len(messages) == 1
    assert messages[0]["content"] == "hello"


def test_slack_webhook_channel_message_now_stored(client):
    """Step 20: channel/group messages are stored too (v1 dropped them
    unless the channel was on an allowlist)."""
    client.post("/api/team", json={
        "id": "U_MANAGER", "name": "Shivam", "role": "Manager", "slack_handle": "U_MANAGER"
    })
    _seed_slack_installation(client)
    slack_payload = {
        "team_id": "T_TEST",
        "api_app_id": "A_TEST",
        "type": "event_callback",
        "event": {
            "type": "message",
            "client_msg_id": "client_msg_channel_1",
            "user": "U_ALICE_123",
            "channel": "C_DEV_CHANNEL",
            "channel_type": "channel",
            "text": "posted in a public channel",
            "ts": "1789026111.000000"
        }
    }
    response = client.post("/api/integrations/slack/webhook", json=slack_payload)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

    messages = client.get("/api/messages?source=slack").json()
    assert len(messages) == 1
    assert messages[0]["content"] == "posted in a public channel"


def test_outlook_ingestion(client):
    # 0. Manager TeamMember (name mapping only -- no allowlist since step 20).
    client.post("/api/team", json={
        "id": "U_MANAGER", "name": "Shivam", "role": "Manager", "outlook_email": "shivam@company.com"
    })

    # 1. Create matching team member
    member_payload = {
        "id": "UOUTLOOK_BOB",
        "name": "Bob Product",
        "role": "Product Manager",
        "slack_handle": "U_BOB",
        "outlook_email": "bob@company.com"
    }
    client.post("/api/team", json=member_payload)

    # 2. Mock Outlook HTML email payload, addressed to the manager
    outlook_payload = {
        "id": "outlook_email_xyz_999",
        "sender": {
            "emailAddress": {
                "address": "bob@company.com",
                "name": "Bob Product"
            }
        },
        "toRecipients": [
            {"emailAddress": {"address": "shivam@company.com", "name": "Shivam"}}
        ],
        "subject": "Weekly Status Update",
        "body": {
            "contentType": "html",
            "content": "<h1>Status Update</h1><p>Working on <b>Features</b> now.</p><ul><li>Design done</li><li>Schema done</li></ul>"
        },
        "receivedDateTime": "2026-05-29T10:30:00Z"
    }

    response = client.post("/api/integrations/outlook/mock-ingest", json=outlook_payload)
    assert response.status_code == 201
    assert response.json()["status"] == "ok"
    assert response.json()["sender_mapped"] == "Bob Product"

    # 3. Check unified table
    msg_response = client.get("/api/messages?source=outlook")
    assert msg_response.status_code == 200
    messages = msg_response.json()
    assert len(messages) == 1
    assert "# Status Update" in messages[0]["content"]
    assert "* Design done" in messages[0]["content"]
    assert messages[0]["sender_mapped_name"] == "Bob Product"
    assert messages[0]["subject"] == "Weekly Status Update"

    # 4. Assert idempotency
    dup_response = client.post("/api/integrations/outlook/mock-ingest", json=outlook_payload)
    assert dup_response.status_code == 200
    assert dup_response.json()["status"] == "ignored"
    assert "duplicate" in dup_response.json()["detail"]


def test_dashboard_message(client):
    # Create matching team member first
    member_payload = {
        "id": "U_SHIVAM",
        "name": "Shivam",
        "role": "Manager",
        "slack_handle": "U_SHIVAM",
        "outlook_email": "shivam@company.com"
    }
    client.post("/api/team", json=member_payload)

    payload = {
        "user_name": "Shivam",
        "message": "Direct query: How many tasks are blocked?"
    }
    response = client.post("/api/integrations/dashboard/message", json=payload)
    assert response.status_code == 201
    assert response.json()["sender_mapped_name"] == "Shivam"
    assert response.json()["content"] == "Direct query: How many tasks are blocked?"
    assert response.json()["source"] == "dashboard"

    msg_response = client.get("/api/messages?source=dashboard")
    assert msg_response.status_code == 200
    assert len(msg_response.json()) == 1
    assert msg_response.json()[0]["content"] == "Direct query: How many tasks are blocked?"
