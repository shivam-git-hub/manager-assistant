"""app.projectkb.occurrence.compute_occurred_at -- derives an Event's
occurred_at (when the underlying messages actually happened) from its cited
claim_ids, never LLM-supplied. Shared by the current heartbeat job and the
future agentic one (step 35)."""
import uuid

from app import timeservice
from app.database import Claim, ClaimSource, UnifiedMessage
from app.projectkb.occurrence import compute_occurred_at


def _add_message(db, **kw):
    defaults = dict(
        platform_msg_id=f"m_{uuid.uuid4().hex}",
        source="outlook",
        sender_raw_id="bob@company.com",
        channel_raw_id="me@company.com",
        content="some message content",
        timestamp=timeservice.now_ist(),
    )
    defaults.update(kw)
    msg = UnifiedMessage(**defaults)
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def _add_claim(db, **kw):
    defaults = dict(
        id=uuid.uuid4().hex,
        text="some claim text",
        content_hash=uuid.uuid4().hex,
        processed=False,
    )
    defaults.update(kw)
    claim = Claim(**defaults)
    db.add(claim)
    db.commit()
    db.refresh(claim)
    return claim


def _link(db, claim_id, message_id):
    db.add(ClaimSource(claim_id=claim_id, message_id=message_id))
    db.commit()


def test_empty_claim_ids_returns_none(db_session):
    assert compute_occurred_at(db_session, []) is None


def test_claim_with_no_resolvable_sources_returns_none(db_session):
    claim = _add_claim(db_session)
    # No ClaimSource row links this claim to any message.
    assert compute_occurred_at(db_session, [claim.id]) is None


def test_unknown_claim_id_returns_none(db_session):
    assert compute_occurred_at(db_session, ["does-not-exist"]) is None


def test_single_claim_picks_its_source_message_timestamp(db_session):
    earlier = timeservice.now_ist().replace(microsecond=0)
    msg = _add_message(db_session, timestamp=earlier)
    claim = _add_claim(db_session)
    _link(db_session, claim.id, msg.id)

    result = compute_occurred_at(db_session, [claim.id])

    assert result == earlier


def test_multiple_claims_pick_the_max_source_timestamp(db_session):
    from datetime import timedelta

    now = timeservice.now_ist().replace(microsecond=0)
    older_msg = _add_message(db_session, timestamp=now - timedelta(hours=2))
    newer_msg = _add_message(db_session, timestamp=now)

    claim_a = _add_claim(db_session)
    claim_b = _add_claim(db_session)
    _link(db_session, claim_a.id, older_msg.id)
    _link(db_session, claim_b.id, newer_msg.id)

    result = compute_occurred_at(db_session, [claim_a.id, claim_b.id])

    assert result == now


def test_one_claim_with_multiple_sources_picks_max(db_session):
    from datetime import timedelta

    now = timeservice.now_ist().replace(microsecond=0)
    msg_a = _add_message(db_session, timestamp=now - timedelta(hours=1))
    msg_b = _add_message(db_session, timestamp=now)

    claim = _add_claim(db_session)
    _link(db_session, claim.id, msg_a.id)
    _link(db_session, claim.id, msg_b.id)

    result = compute_occurred_at(db_session, [claim.id])

    assert result == now
