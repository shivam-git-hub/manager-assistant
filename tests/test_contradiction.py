import json
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import timeservice
from app.kb.models import Entity, AttributedClaim, Conflict
from app.kb.synthesis import (
    _is_assertive_claim,
    run_global_contradiction_probe,
)
from app.agent.gemini_client import GeminiClient


class FakeTransport:
    """Returns queued Gemini-shaped responses; records calls."""
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, url, payload):
        self.calls.append((url, payload))
        return self.responses.pop(0) if self.responses else {
            "candidates": [{"content": {"parts": [{"text": "{}"}]}, "finishReason": "STOP"}],
            "usageMetadata": {},
        }


def _judge_response(results):
    return {
        "candidates": [{
            "content": {"parts": [{"text": json.dumps({"results": results})}]},
            "finishReason": "STOP",
        }],
        "usageMetadata": {},
    }


def test_is_assertive_claim_filters_questions_and_requests():
    assert _is_assertive_claim("Bob sent the schema document to Alice.") is True
    assert _is_assertive_claim("Alice has not received the schema document.") is True
    # Non-assertive: questions / inquiries / requests must be excluded
    assert _is_assertive_claim("The finalization status of the spec is being inquired about.") is False
    assert _is_assertive_claim("Is the design spec finalized?") is False
    assert _is_assertive_claim("Bob requested Carol to provide feedback on the charts.") is False
    assert _is_assertive_claim("") is False


def _seed_deadlock(db: Session):
    """Bob's 'sent' claim and Alice's 'not received' claim on DIFFERENT person pages."""
    now = timeservice.now_ist()
    ph = Entity(slug="project:phoenix", type="project", name="Phoenix", ref_id="1")
    ea = Entity(slug="person:u_alice", type="person", name="Alice", ref_id="U_ALICE")
    eb = Entity(slug="person:u_bob", type="person", name="Bob", ref_id="U_BOB")
    db.add_all([ph, ea, eb])
    db.commit()
    bob = AttributedClaim(entity_id=ea.id, claim="Bob sent the schema document to Alice.",
                          kind="fact", holder="U_BOB", weight=1.0, claimed_at=now, active=True)
    alice = AttributedClaim(entity_id=eb.id, claim="Alice has not received the schema document from Bob.",
                            kind="status", holder="U_ALICE", weight=1.0, claimed_at=now, active=True)
    db.add_all([bob, alice])
    db.commit()
    return ph, bob, alice


def test_global_probe_catches_cross_entity_deadlock(db_session: Session):
    ph, bob, alice = _seed_deadlock(db_session)

    # Flash judges the single cross-entity, keyword-overlapping pair as a contradiction.
    transport = FakeTransport([_judge_response([
        {"pair_index": 0, "contradicts": True, "severity": "high",
         "description": "Bob says sent; Alice says not received."}
    ])])
    client = GeminiClient(api_key="fake-key", transport=transport)

    created = run_global_contradiction_probe(db_session, client)
    assert len(created) == 1
    assert len(transport.calls) == 1  # exactly one flash judge call

    conf = db_session.scalars(select(Conflict)).first()
    assert conf is not None
    assert {conf.claim_a_id, conf.claim_b_id} == {bob.id, alice.id}
    assert conf.severity == "high"


def test_global_probe_dedups_existing_conflict(db_session: Session):
    ph, bob, alice = _seed_deadlock(db_session)
    db_session.add(Conflict(entity_id=ph.id, claim_a_id=bob.id, claim_b_id=alice.id,
                            severity="high", description="already logged", status="open",
                            detected_at=timeservice.now_ist()))
    db_session.commit()

    # No candidate pairs remain -> the LLM is never called, nothing new created.
    transport = FakeTransport([])
    client = GeminiClient(api_key="fake-key", transport=transport)
    created = run_global_contradiction_probe(db_session, client)
    assert created == []
    assert len(transport.calls) == 0
    assert db_session.scalar(select(Conflict).where(Conflict.description == "already logged")) is not None
    assert len(db_session.scalars(select(Conflict)).all()) == 1


def test_global_probe_ignores_non_overlapping_claims(db_session: Session):
    """Cross-entity claims with no shared keywords are not even sent to the judge."""
    now = timeservice.now_ist()
    ea = Entity(slug="person:u_alice", type="person", name="Alice", ref_id="U_ALICE")
    eb = Entity(slug="person:u_bob", type="person", name="Bob", ref_id="U_BOB")
    db_session.add_all([ea, eb])
    db_session.commit()
    db_session.add_all([
        AttributedClaim(entity_id=ea.id, claim="Deployment pipeline finished successfully.",
                        kind="status", holder="U_BOB", weight=1.0, claimed_at=now, active=True),
        AttributedClaim(entity_id=eb.id, claim="The marketing newsletter draft looks great.",
                        kind="fact", holder="U_ALICE", weight=1.0, claimed_at=now, active=True),
    ])
    db_session.commit()

    transport = FakeTransport([])
    client = GeminiClient(api_key="fake-key", transport=transport)
    created = run_global_contradiction_probe(db_session, client)
    assert created == []
    assert len(transport.calls) == 0  # no keyword overlap -> no LLM call
