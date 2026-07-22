"""Step 20 (prompts/step_20_poll_completion.md): blocklist + noise
classification, thread_key capture for both sources, the /api/blocklist
management API, and the manual-MoM input endpoint."""
import json
import uuid

from fastapi.testclient import TestClient

from app import timeservice
from app.database import TeamMember, UnifiedMessage
from app.integrations.outlook import connector as outlook_connector
from app.main import app
from app.projectkb import blocklist


def _msg(**kw) -> UnifiedMessage:
    """A minimal in-memory UnifiedMessage for classify_message tests --
    never persisted; classification reads attributes only."""
    defaults = dict(
        platform_msg_id=f"m_{uuid.uuid4().hex}",
        source="outlook",
        sender_raw_id="bob@company.com",
        receiver_raw_id="me@company.com",
        channel_raw_id="me@company.com",
        subject="regular subject",
        content="body",
        timestamp=timeservice.now_ist(),
        raw_metadata="{}",
    )
    defaults.update(kw)
    return UnifiedMessage(**defaults)


# --- thread_key capture ----------------------------------------------------

def test_outlook_normalize_captures_conversation_id(client, db_session):
    raw = {
        "id": "mail_1",
        "conversationId": "AAQkConvThread42",
        "sender": {"emailAddress": {"address": "bob@company.com"}},
        "toRecipients": [{"emailAddress": {"address": "me@company.com"}}],
        "subject": "Re: rollout",
        "body": {"contentType": "text", "content": "following up"},
        "receivedDateTime": "2026-07-01T10:00:00Z",
    }
    normalized = outlook_connector.normalize(db_session, raw)
    assert normalized.thread_key == "AAQkConvThread42"


def test_slack_normalize_thread_key_dm(client, db_session):
    from app.integrations.slack import SlackConnector

    db_session.add_all([
        TeamMember(id="U_MANAGER", name="Shivam", role="Manager", slack_handle="U_MANAGER"),
    ])
    db_session.commit()

    connector = SlackConnector()
    top = {"type": "message", "user": "U_A", "channel": "D_1", "channel_type": "im", "text": "root", "ts": "100.5"}
    reply = {"type": "message", "user": "U_A", "channel": "D_1", "channel_type": "im", "text": "reply", "ts": "101.5", "thread_ts": "100.5"}
    assert connector.normalize(db_session, top).thread_key == "D_1:100.5"
    assert connector.normalize(db_session, reply).thread_key == "D_1:100.5"


# --- blocklist module ------------------------------------------------------

def test_blocklist_contact_glob_and_exact(client):
    mid = client.manager_id
    blocklist.add_blocked_contact(mid, "Vendor spam", email_pattern="*@vendor.com")
    blocklist.add_blocked_contact(mid, "Chatty Carl", slack_pattern="U_CARL")

    assert blocklist.is_contact_blocked(mid, "outlook", "sales@vendor.com") is True
    assert blocklist.is_contact_blocked(mid, "outlook", "bob@company.com") is False
    assert blocklist.is_contact_blocked(mid, "slack", "U_CARL") is True
    assert blocklist.is_contact_blocked(mid, "slack", "U_ALICE") is False


def test_blocklist_channel_matching(client):
    mid = client.manager_id
    blocklist.add_blocked_channel(mid, "Random", "slack", "C_RANDOM*")
    assert blocklist.is_channel_blocked(mid, "slack", "C_RANDOM_MEMES") is True
    assert blocklist.is_channel_blocked(mid, "slack", "C_ENG") is False
    assert blocklist.is_channel_blocked(mid, "outlook", "C_RANDOM_MEMES") is False  # source-scoped


def test_blocklist_contact_update_and_delete(client):
    mid = client.manager_id
    entry = blocklist.add_blocked_contact(mid, "Temp", email_pattern="x@y.com")
    updated = blocklist.update_blocked_contact(mid, entry["id"], label="Renamed", slack_pattern="U_X")
    assert updated["label"] == "Renamed" and updated["slack_pattern"] == "U_X"
    assert blocklist.delete_blocked_contact(mid, entry["id"]) is True
    assert blocklist.delete_blocked_contact(mid, entry["id"]) is False
    assert blocklist.load_blocklist(mid)["contacts"] == []


# --- classify_message (the ingest-selection filter) ------------------------

def test_classify_blocked_contact(client):
    mid = client.manager_id
    blocklist.add_blocked_contact(mid, "Vendor", email_pattern="*@vendor.com")
    assert blocklist.classify_message(mid, _msg(sender_raw_id="promo@vendor.com")) == "blocked"
    # receiver side matches too (mail YOU sent to a blocked party)
    assert blocklist.classify_message(mid, _msg(receiver_raw_id="promo@vendor.com")) == "blocked"


def test_classify_blocked_channel(client):
    mid = client.manager_id
    blocklist.add_blocked_channel(mid, "Memes", "slack", "C_MEMES")
    m = _msg(source="slack", sender_raw_id="U_A", receiver_raw_id="C_MEMES", channel_raw_id="C_MEMES")
    assert blocklist.classify_message(mid, m) == "blocked"


def test_classify_noise_rules(client):
    mid = client.manager_id
    assert blocklist.classify_message(mid, _msg(sender_raw_id="noreply@service.com")) == "noise"
    assert blocklist.classify_message(mid, _msg(sender_raw_id="do-not-reply@corp.com")) == "noise"
    assert blocklist.classify_message(mid, _msg(subject="Accepted: Design review")) == "noise"
    assert blocklist.classify_message(mid, _msg(raw_metadata=json.dumps({"internetMessageHeaders": [{"name": "List-Unsubscribe", "value": "<mailto:x>"}]}))) == "noise"


def test_classify_clean_message_passes(client):
    assert blocklist.classify_message(client.manager_id, _msg()) is None


# --- /api/blocklist management API -----------------------------------------

def test_blocklist_api_crud(client):
    r = client.post("/api/blocklist/contacts", json={"label": "Vendor", "email_pattern": "*@vendor.com"})
    assert r.status_code == 201
    contact_id = r.json()["id"]

    r2 = client.post("/api/blocklist/channels", json={"label": "Memes", "source": "slack", "pattern": "C_MEMES"})
    assert r2.status_code == 201

    data = client.get("/api/blocklist").json()
    assert len(data["contacts"]) == 1 and len(data["channels"]) == 1

    r3 = client.put(f"/api/blocklist/contacts/{contact_id}", json={"label": "Vendor Inc"})
    assert r3.json()["label"] == "Vendor Inc"

    assert client.delete(f"/api/blocklist/contacts/{contact_id}").status_code == 204
    assert client.delete(f"/api/blocklist/contacts/{contact_id}").status_code == 404
    assert client.post("/api/blocklist/contacts", json={"label": "no patterns"}).status_code == 400
    assert client.post("/api/blocklist/channels", json={"label": "x", "source": "teams", "pattern": "y"}).status_code == 400


# --- manual MoM endpoint ----------------------------------------------------

def test_manual_message_stored(client, db_session):
    r = client.post("/api/messages/manual", json={"content": "MoM: decided to ship Friday", "subject": "Sprint review MoM"})
    assert r.status_code == 201
    body = r.json()
    assert body["source"] == "manual"
    assert body["platform_msg_id"].startswith("manual_")

    row = db_session.get(UnifiedMessage, body["id"])
    assert row is not None
    assert row.source == "manual"
    assert row.is_processed is False
    assert row.content == "MoM: decided to ship Friday"
    assert row.subject == "Sprint review MoM"


def test_manual_message_empty_content_rejected(client):
    assert client.post("/api/messages/manual", json={"content": "   "}).status_code == 400


def test_manual_message_isolated_per_user(client, db_session):
    client.post("/api/messages/manual", json={"content": "my private MoM"})

    other = TestClient(app)
    r = other.post("/api/auth/dev-login", json={"email": f"other-{uuid.uuid4().hex}@test.local", "name": "Other"})
    assert r.status_code == 200
    try:
        from app.tenancy.db import get_manager_session

        odb = get_manager_session(r.json()["id"])
        try:
            assert odb.query(UnifiedMessage).count() == 0
        finally:
            odb.close()
    finally:
        import shutil
        from app.tenancy.paths import manager_dir
        shutil.rmtree(manager_dir(r.json()["id"]), ignore_errors=True)
