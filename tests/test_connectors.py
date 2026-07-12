import pytest
from app.integrations.outlook import clean_html

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
    # 1. Create a matching team member first
    member_payload = {
        "id": "USLACK_ALICE",
        "name": "Alice Developer",
        "role": "Backend dev",
        "slack_handle": "U_ALICE_123",
        "outlook_email": "alice@company.com"
    }
    client.post("/api/team", json=member_payload)

    # 2. Mock a slack event message
    slack_payload = {
        "type": "event_callback",
        "event": {
            "type": "message",
            "client_msg_id": "client_msg_abc_123",
            "user": "U_ALICE_123",
            "channel": "C_DEV_CHANNEL",
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


def test_outlook_ingestion(client):
    # 1. Create matching team member
    member_payload = {
        "id": "UOUTLOOK_BOB",
        "name": "Bob Product",
        "role": "Product Manager",
        "slack_handle": "U_BOB",
        "outlook_email": "bob@company.com"
    }
    client.post("/api/team", json=member_payload)

    # 2. Mock Outlook HTML email payload
    outlook_payload = {
        "id": "outlook_email_xyz_999",
        "sender": {
            "emailAddress": {
                "address": "bob@company.com",
                "name": "Bob Product"
            }
        },
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
