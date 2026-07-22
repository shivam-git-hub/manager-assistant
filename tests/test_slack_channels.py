"""Step 17 piece 3: conversation_type on NormalizedMessage, the
tracked-channels gate, and the pure conversations.history -> event-dict
transform. See prompts/step_17_agent_pool.md."""
from app.integrations.slack import SlackConnector
from app.integrations.base import ingest
from app.projectkb.tracked_channels import (
    add_tracked_channel,
    is_channel_tracked,
    delete_tracked_channel,
    load_tracked_channels,
)


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


# --- normalize() conversation_type mapping --------------------------------

def test_normalize_dm_sets_conversation_type_dm(client, db_session):
    from app.database import TeamMember

    db_session.add_all([
        TeamMember(id="U_MANAGER", name="Shivam", role="Manager", slack_handle="U_MANAGER"),
        TeamMember(id="U_ALICE", name="Alice", role="Developer", slack_handle="U_ALICE"),
    ])
    db_session.commit()

    connector = SlackConnector()
    event = {"type": "message", "user": "U_ALICE", "channel": "D_1", "channel_type": "im", "text": "hi", "ts": "1.0"}
    normalized = connector.normalize(db_session, event)
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


# --- tracked_channels module -----------------------------------------------

def test_is_channel_tracked_exact_and_glob(client):
    manager_id = client.manager_id
    add_tracked_channel(manager_id, "Eng standup", "C_ENG_STANDUP")
    add_tracked_channel(manager_id, "All project channels", "C_PROJ_*")

    assert is_channel_tracked(manager_id, "slack", "C_ENG_STANDUP") is True
    assert is_channel_tracked(manager_id, "slack", "C_PROJ_ALPHA") is True
    assert is_channel_tracked(manager_id, "slack", "C_RANDOM") is False


def test_is_channel_tracked_empty_list_is_false(client):
    assert is_channel_tracked(client.manager_id, "slack", "C_ANYTHING") is False


def test_delete_tracked_channel(client):
    manager_id = client.manager_id
    entry = add_tracked_channel(manager_id, "Temp", "C_TEMP")
    assert delete_tracked_channel(manager_id, entry["id"]) is True
    assert load_tracked_channels(manager_id) == []
    assert delete_tracked_channel(manager_id, entry["id"]) is False


# --- ingest() branch: channel/group gated on tracked-channels -------------

def test_ingest_channel_message_untracked_is_ignored(client, db_session):
    from app.database import TeamMember

    db_session.add_all([
        TeamMember(id="U_MANAGER", name="Shivam", role="Manager", slack_handle="U_MANAGER"),
        TeamMember(id="U_ALICE", name="Alice", role="Developer", slack_handle="U_ALICE"),
    ])
    db_session.commit()

    connector = SlackConnector()
    event = {"type": "message", "client_msg_id": "c1", "user": "U_ALICE", "channel": "C_ENG", "channel_type": "channel", "text": "standup", "ts": "10.0"}
    msg, status = ingest(connector, event, db_session, client.manager_id)
    assert status == "ignored_untracked_channel"
    assert msg is None


def test_ingest_channel_message_tracked_is_stored(client, db_session):
    from app.database import TeamMember

    db_session.add_all([
        TeamMember(id="U_MANAGER", name="Shivam", role="Manager", slack_handle="U_MANAGER"),
        TeamMember(id="U_ALICE", name="Alice", role="Developer", slack_handle="U_ALICE"),
    ])
    db_session.commit()
    add_tracked_channel(client.manager_id, "Eng standup", "C_ENG")

    connector = SlackConnector()
    event = {"type": "message", "client_msg_id": "c2", "user": "U_ALICE", "channel": "C_ENG", "channel_type": "channel", "text": "standup notes", "ts": "11.0"}
    msg, status = ingest(connector, event, db_session, client.manager_id)
    assert status == "ok"
    assert msg is not None
    assert msg.content == "standup notes"


def test_ingest_channel_tracked_does_not_require_manager_involvement(client, db_session):
    """A channel message between two non-manager participants still stores
    (gated purely on the channel, not on manager-sender/receiver identity --
    unlike DMs, which require the manager to literally be sender or receiver)."""
    from app.database import TeamMember

    db_session.add_all([
        TeamMember(id="U_MANAGER", name="Shivam", role="Manager", slack_handle="U_MANAGER"),
        TeamMember(id="U_BOB", name="Bob", role="Developer", slack_handle="U_BOB"),
    ])
    db_session.commit()
    add_tracked_channel(client.manager_id, "Eng standup", "C_ENG")

    connector = SlackConnector()
    event = {"type": "message", "client_msg_id": "c3", "user": "U_BOB", "channel": "C_ENG", "channel_type": "channel", "text": "no manager here", "ts": "12.0"}
    msg, status = ingest(connector, event, db_session, client.manager_id)
    assert status == "ok"


def test_ingest_dm_behavior_unchanged_by_channel_gate(client, db_session):
    """Regression guard: the conversation_type branch must not disturb the
    existing DM path (single-counterpart, tracked-contacts gate)."""
    from app.database import TeamMember
    from app.projectkb.tracked import add_tracked_contact

    db_session.add_all([
        TeamMember(id="U_MANAGER", name="Shivam", role="Manager", slack_handle="U_MANAGER"),
        TeamMember(id="U_ALICE", name="Alice", role="Developer", slack_handle="U_ALICE"),
    ])
    db_session.commit()

    connector = SlackConnector()
    event = {"type": "message", "client_msg_id": "d1", "user": "U_ALICE", "channel": "D_1", "channel_type": "im", "text": "untracked dm", "ts": "13.0"}
    _, status = ingest(connector, event, db_session, client.manager_id)
    assert status == "ignored_untracked"

    add_tracked_contact(client.manager_id, "Alice", slack_pattern="U_ALICE")
    event2 = {"type": "message", "client_msg_id": "d2", "user": "U_ALICE", "channel": "D_1", "channel_type": "im", "text": "tracked dm", "ts": "14.0"}
    msg, status2 = ingest(connector, event2, db_session, client.manager_id)
    assert status2 == "ok"
    assert msg.content == "tracked dm"
