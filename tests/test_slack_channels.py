"""Slack conversation handling: conversation_type on NormalizedMessage,
the pure conversations.history -> event-dict transform (now thread_ts-
preserving), the conversations.list object -> channel_type mapping, and
store-everything ingest behavior. Blocklist/noise selection
itself is covered in tests/test_poll_completion.py."""
from app.integrations.slack import SlackConnector
from app.integrations.base import ingest


# --- _history_message_to_event (pure, no live API calls) -----------------

def test_history_to_event_dm():
    msg = {"type": "message", "user": "U_ALICE", "text": "hi", "ts": "111.000000"}
    event = SlackConnector._history_message_to_event(msg, "D_CHANNEL", "im")
    assert event == {
        "type": "message", "user": "U_ALICE", "channel": "D_CHANNEL", "channel_type": "im",
        "text": "hi", "ts": "111.000000", "client_msg_id": None,
    }


def test_history_to_event_public_channel():
    msg = {"type": "message", "user": "U_ALICE", "text": "standup notes", "ts": "222.000000", "client_msg_id": "abc"}
    event = SlackConnector._history_message_to_event(msg, "C_ENG", "channel")
    assert event["channel_type"] == "channel"
    assert event["client_msg_id"] == "abc"


def test_history_to_event_group_and_mpim():
    msg = {"type": "message", "user": "U_ALICE", "text": "hey team", "ts": "333.000000"}
    assert SlackConnector._history_message_to_event(msg, "G_TEAM", "group")["channel_type"] == "group"
    assert SlackConnector._history_message_to_event(msg, "M_TEAM", "mpim")["channel_type"] == "mpim"


def test_history_to_event_preserves_thread_ts():
    """Threaded replies carry the parent's ts -- it must survive
    the reshaping so normalize() can fold it into thread_key."""
    msg = {"type": "message", "user": "U_ALICE", "text": "reply", "ts": "445.000000", "thread_ts": "444.000000"}
    event = SlackConnector._history_message_to_event(msg, "C_ENG", "channel")
    assert event["thread_ts"] == "444.000000"

    top_level = {"type": "message", "user": "U_ALICE", "text": "root", "ts": "446.000000"}
    assert "thread_ts" not in SlackConnector._history_message_to_event(top_level, "C_ENG", "channel")


def test_history_to_event_skips_subtype_messages():
    """channel_join / message edits etc. carry a subtype -- not a real
    authored message, must not be forwarded into normalize()/ingest()."""
    msg = {"type": "message", "subtype": "channel_join", "user": "U_ALICE", "text": "joined", "ts": "444.000000"}
    assert SlackConnector._history_message_to_event(msg, "C_ENG", "channel") is None


def test_history_to_event_skips_non_message_type():
    msg = {"type": "channel_topic", "user": "U_ALICE", "ts": "555.000000"}
    assert SlackConnector._history_message_to_event(msg, "C_ENG", "channel") is None


def test_history_to_event_skips_missing_user():
    msg = {"type": "message", "text": "no user field", "ts": "666.000000"}
    assert SlackConnector._history_message_to_event(msg, "C_ENG", "channel") is None


# --- conversations.list object -> channel_type -------------------------

def test_conversation_object_type_mapping():
    assert SlackConnector._conversation_object_type({"is_im": True}) == "im"
    assert SlackConnector._conversation_object_type({"is_mpim": True}) == "mpim"
    assert SlackConnector._conversation_object_type({"is_group": True}) == "group"
    assert SlackConnector._conversation_object_type({"is_channel": True, "is_private": True}) == "group"
    assert SlackConnector._conversation_object_type({"is_channel": True, "is_private": False}) == "channel"


# --- normalize() conversation_type mapping --------------------------------

def test_normalize_dm_sets_conversation_type_dm(client, db_session):
    """DM counterpart resolution's fast path now reads the manager's own
    Employee.slack_id directly, needing manager_id threaded into normalize()."""
    from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, get_employee_by_manager_id

    cp_db = ControlPlaneSessionLocal()
    get_employee_by_manager_id(cp_db, client.manager_id).slack_id = "U_MANAGER"
    cp_db.commit()
    cp_db.close()

    connector = SlackConnector()
    event = {"type": "message", "user": "U_ALICE", "channel": "D_1", "channel_type": "im", "text": "hi", "ts": "1.0"}
    normalized = connector.normalize(db_session, event, client.manager_id)
    assert normalized is not None
    assert normalized.conversation_type == "dm"
    assert normalized.receiver_id == "U_MANAGER"


def test_normalize_channel_sets_conversation_type_channel(client, db_session):
    from app.database import TeamMember

    db_session.add(TeamMember(id="U_ALICE", name="Alice", role="Developer", slack_handle="U_ALICE"))
    db_session.commit()

    connector = SlackConnector()
    event = {"type": "message", "user": "U_ALICE", "channel": "C_ENG", "channel_type": "channel", "text": "standup", "ts": "2.0"}
    normalized = connector.normalize(db_session, event)
    assert normalized is not None
    assert normalized.conversation_type == "channel"
    assert normalized.receiver_id == "C_ENG"  # no single counterpart -- channel id stands in


def test_normalize_mpim_maps_to_group(client, db_session):
    from app.database import TeamMember

    db_session.add(TeamMember(id="U_ALICE", name="Alice", role="Developer", slack_handle="U_ALICE"))
    db_session.commit()

    connector = SlackConnector()
    event = {"type": "message", "user": "U_ALICE", "channel": "M_1", "channel_type": "mpim", "text": "hey", "ts": "3.0"}
    normalized = connector.normalize(db_session, event)
    assert normalized.conversation_type == "group"


def test_normalize_unrecognized_channel_type_returns_none(client, db_session):
    connector = SlackConnector()
    event = {"type": "message", "user": "U_ALICE", "channel": "X_1", "channel_type": "weird_future_type", "text": "?", "ts": "4.0"}
    assert connector.normalize(db_session, event) is None


# --- channel/group session-window thread grouping --------------------------

def test_channel_top_level_messages_within_gap_share_thread_key(client, db_session):
    """Two top-level (no thread_ts) channel messages close in time land in
    the SAME thread_key -- a live back-and-forth should batch into one LLM
    call, not one per message."""
    connector = SlackConnector()
    first = {"type": "message", "client_msg_id": "s1", "user": "U_ALICE", "channel": "C_ENG", "channel_type": "channel", "text": "starting the migration now", "ts": "1000.0"}
    second = {"type": "message", "client_msg_id": "s2", "user": "U_ALICE", "channel": "C_ENG", "channel_type": "channel", "text": "done, all green", "ts": "1300.0"}  # 5 min later

    m1, _ = ingest(connector, first, db_session, client.manager_id)
    m2, _ = ingest(connector, second, db_session, client.manager_id)
    assert m1.thread_id == m2.thread_id


def test_channel_top_level_messages_after_gap_start_new_thread(client, db_session):
    """A top-level channel message arriving after a real gap (unrelated
    topic, most likely) starts a fresh thread_key instead of chaining onto
    whatever the channel's last message happened to be about."""
    from app.config import SLACK_CHANNEL_SESSION_GAP_MINUTES

    connector = SlackConnector()
    gap_seconds = SLACK_CHANNEL_SESSION_GAP_MINUTES * 60 + 60
    first = {"type": "message", "client_msg_id": "g1", "user": "U_ALICE", "channel": "C_ENG", "channel_type": "channel", "text": "morning standup notes", "ts": "2000.0"}
    later = {"type": "message", "client_msg_id": "g2", "user": "U_ALICE", "channel": "C_ENG", "channel_type": "channel", "text": "unrelated afternoon question", "ts": str(2000.0 + gap_seconds)}

    m1, _ = ingest(connector, first, db_session, client.manager_id)
    m2, _ = ingest(connector, later, db_session, client.manager_id)
    assert m1.thread_id != m2.thread_id


# --- ingest(): store-everything -------------------------------------------

def test_ingest_channel_message_stored_without_any_list(client, db_session):
    """No allowlist -- every channel message is stored."""
    from app.database import TeamMember

    db_session.add_all([
        TeamMember(id="U_MANAGER", name="Shivam", role="Manager", slack_handle="U_MANAGER"),
        TeamMember(id="U_ALICE", name="Alice", role="Developer", slack_handle="U_ALICE"),
    ])
    db_session.commit()

    connector = SlackConnector()
    event = {"type": "message", "client_msg_id": "c1", "user": "U_ALICE", "channel": "C_ENG", "channel_type": "channel", "text": "standup", "ts": "10.0"}
    msg, status = ingest(connector, event, db_session, client.manager_id)
    assert status == "ok"
    assert msg.content == "standup"
    assert msg.thread_id == "C_ENG:10.0"


def test_ingest_channel_message_between_non_managers_stored(client, db_session):
    from app.database import TeamMember

    db_session.add_all([
        TeamMember(id="U_MANAGER", name="Shivam", role="Manager", slack_handle="U_MANAGER"),
        TeamMember(id="U_BOB", name="Bob", role="Developer", slack_handle="U_BOB"),
    ])
    db_session.commit()

    connector = SlackConnector()
    event = {"type": "message", "client_msg_id": "c3", "user": "U_BOB", "channel": "C_ENG", "channel_type": "channel", "text": "no manager here", "ts": "12.0"}
    _, status = ingest(connector, event, db_session, client.manager_id)
    assert status == "ok"


def test_ingest_threaded_reply_shares_thread_key_with_parent(client, db_session):
    from app.database import TeamMember

    db_session.add_all([
        TeamMember(id="U_MANAGER", name="Shivam", role="Manager", slack_handle="U_MANAGER"),
        TeamMember(id="U_ALICE", name="Alice", role="Developer", slack_handle="U_ALICE"),
    ])
    db_session.commit()

    connector = SlackConnector()
    parent = {"type": "message", "client_msg_id": "p1", "user": "U_ALICE", "channel": "C_ENG", "channel_type": "channel", "text": "root", "ts": "20.0"}
    reply = {"type": "message", "client_msg_id": "r1", "user": "U_ALICE", "channel": "C_ENG", "channel_type": "channel", "text": "reply", "ts": "21.0", "thread_ts": "20.0"}
    p_msg, _ = ingest(connector, parent, db_session, client.manager_id)
    r_msg, _ = ingest(connector, reply, db_session, client.manager_id)
    assert p_msg.thread_id == r_msg.thread_id == "C_ENG:20.0"
