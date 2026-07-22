"""Step 22 (prompts/step_22_ingest_job.md): blocklist/noise selection ->
per-thread batching -> flash-model claim extraction."""
import json

from app import timeservice
from app.agent.gemini_client import GeminiClient
from app.config import INGESTION_BATCH_SIZE
from app.database import Claim, ClaimSource, UnifiedMessage
from app.projectkb import blocklist
from app.projectkb.jobs import ingestion


class FakeTransport:
    def __init__(self, response_dicts):
        self.response_dicts = response_dicts
        self.calls = []
        self.call_count = 0

    def __call__(self, url, json_payload):
        self.calls.append((url, json_payload))
        res = self.response_dicts[self.call_count]
        self.call_count += 1
        if isinstance(res, Exception):
            raise res
        return res


def _gemini_response(claims_obj):
    return {
        "candidates": [
            {
                "content": {"parts": [{"text": json.dumps(claims_obj)}]},
                "finishReason": "STOP",
            }
        ]
    }


def _add_message(db_session, **kw):
    defaults = dict(
        platform_msg_id=f"m_{kw.get('_seq', id(kw))}",
        source="outlook",
        sender_raw_id="bob@company.com",
        receiver_raw_id="me@company.com",
        channel_raw_id="me@company.com",
        subject="regular subject",
        content="Bob: I'll finish the migration by Friday.",
        timestamp=timeservice.now_ist(),
        is_processed=False,
        raw_metadata="{}",
    )
    defaults.pop("_seq", None)
    kw.pop("_seq", None)
    defaults.update(kw)
    msg = UnifiedMessage(**defaults)
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)
    return msg


def test_01_zero_pending_short_circuits_without_llm_call(client, db_session):
    transport = FakeTransport([])
    fake_client = GeminiClient(api_key="fake-key", transport=transport)

    stats = ingestion.run(db_session, client.manager_id, client=fake_client)

    assert stats == {"processed": 0, "claims_created": 0, "skipped": 0}
    assert transport.call_count == 0


def test_02_blocked_and_noise_messages_skip_llm(client, db_session):
    blocklist.add_blocked_contact(client.manager_id, label="spammy", email_pattern="spam@*")
    blocked_msg = _add_message(db_session, sender_raw_id="spam@evil.com")
    noise_msg = _add_message(db_session, sender_raw_id="no-reply@service.com", platform_msg_id="m_noise")

    transport = FakeTransport([])
    fake_client = GeminiClient(api_key="fake-key", transport=transport)

    stats = ingestion.run(db_session, client.manager_id, client=fake_client)

    assert stats["skipped"] == 2
    assert stats["claims_created"] == 0
    assert transport.call_count == 0

    db_session.refresh(blocked_msg)
    db_session.refresh(noise_msg)
    assert blocked_msg.is_processed is True
    assert blocked_msg.skip_reason == "blocked"
    assert noise_msg.is_processed is True
    assert noise_msg.skip_reason == "noise"


def test_03_survivors_batch_by_thread_and_produce_claims(client, db_session):
    m1 = _add_message(db_session, platform_msg_id="m_t1a", thread_id="thread-1", content="root msg")
    m2 = _add_message(db_session, platform_msg_id="m_t1b", thread_id="thread-1", content="reply msg")
    m3 = _add_message(db_session, platform_msg_id="m_t2a", thread_id="thread-2", content="unrelated")

    responses = [
        _gemini_response({"claims": [{"text": "Bob will finish the migration by Friday", "message_ids": [m1.id, m2.id]}]}),
        _gemini_response({"claims": [{"text": "Unrelated topic noted", "message_ids": [m3.id]}]}),
    ]
    transport = FakeTransport(responses)
    fake_client = GeminiClient(api_key="fake-key", transport=transport)

    stats = ingestion.run(db_session, client.manager_id, client=fake_client)

    assert transport.call_count == 2
    assert stats["claims_created"] == 2
    assert stats["processed"] == 3

    claims = db_session.query(Claim).all()
    assert len(claims) == 2
    thread1_claim = next(c for c in claims if c.text.startswith("Bob will finish"))
    sources = db_session.query(ClaimSource).filter_by(claim_id=thread1_claim.id).all()
    assert {s.message_id for s in sources} == {m1.id, m2.id}
    assert thread1_claim.thread_key == "thread-1"

    for m in (m1, m2, m3):
        db_session.refresh(m)
        assert m.is_processed is True


def test_04_retry_after_commit_is_idempotent(client, db_session):
    m1 = _add_message(db_session, platform_msg_id="m_retry", content="Alice shipped the fix")
    response = _gemini_response({"claims": [{"text": "Alice shipped the fix", "message_ids": [m1.id]}]})

    fake_client_1 = GeminiClient(api_key="fake-key", transport=FakeTransport([response]))
    stats1 = ingestion.run(db_session, client.manager_id, client=fake_client_1)
    assert stats1["claims_created"] == 1

    # Simulate a retry: nothing left unprocessed, so a second run must not
    # call the LLM again or duplicate the claim.
    fake_client_2 = GeminiClient(api_key="fake-key", transport=FakeTransport([]))
    stats2 = ingestion.run(db_session, client.manager_id, client=fake_client_2)
    assert stats2 == {"processed": 0, "claims_created": 0, "skipped": 0}
    assert db_session.query(Claim).count() == 1


def test_05_claim_with_message_id_outside_batch_is_dropped(client, db_session):
    m1 = _add_message(db_session, platform_msg_id="m_bad_cite")
    bogus_response = _gemini_response({"claims": [{"text": "hallucinated claim", "message_ids": [999999]}]})
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport([bogus_response]))

    stats = ingestion.run(db_session, client.manager_id, client=fake_client)

    assert stats["claims_created"] == 0
    assert db_session.query(Claim).count() == 0
    # The message itself is still marked processed -- the batch succeeded,
    # the LLM just didn't produce a usable claim from it.
    db_session.refresh(m1)
    assert m1.is_processed is True


def test_06_batch_size_cap_leaves_excess_for_next_tick(client, db_session):
    # Each message is its own thread (solo batch), so the cap boundary
    # falls cleanly between whole batches -- exercises the common case.
    messages = [
        _add_message(db_session, platform_msg_id=f"m_cap_{i}", thread_id=f"solo-{i}")
        for i in range(INGESTION_BATCH_SIZE + 5)
    ]
    responses = [_gemini_response({"claims": []}) for _ in range(INGESTION_BATCH_SIZE)]
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport(responses))

    stats = ingestion.run(db_session, client.manager_id, client=fake_client)

    assert stats["processed"] == INGESTION_BATCH_SIZE
    for m in messages:
        db_session.refresh(m)
    processed_count = sum(1 for m in messages if m.is_processed)
    assert processed_count == INGESTION_BATCH_SIZE


def test_06b_batch_size_cap_never_splits_a_thread(client, db_session):
    # A thread larger than the cap goes through whole rather than being
    # sliced mid-conversation (which would let a later tick re-extract
    # from a partial thread and produce divergent claims).
    big_thread = [
        _add_message(db_session, platform_msg_id=f"m_big_{i}", thread_id="big-thread")
        for i in range(INGESTION_BATCH_SIZE + 3)
    ]
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport([_gemini_response({"claims": []})]))

    stats = ingestion.run(db_session, client.manager_id, client=fake_client)

    assert stats["processed"] == len(big_thread)
    for m in big_thread:
        db_session.refresh(m)
        assert m.is_processed is True


def test_08_duplicate_claim_in_one_response_is_deduped_by_content_hash(client, db_session):
    m1 = _add_message(db_session, platform_msg_id="m_dup_1", thread_id="thread-a")

    # The LLM returning the same claim twice for one batch (plausible model
    # behavior) must not create two rows -- content_hash is the guard.
    response = _gemini_response(
        {
            "claims": [
                {"text": "Same claim text", "message_ids": [m1.id]},
                {"text": "Same claim text", "message_ids": [m1.id]},
            ]
        }
    )
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport([response]))

    stats = ingestion.run(db_session, client.manager_id, client=fake_client)

    assert stats["claims_created"] == 1
    assert db_session.query(Claim).count() == 1


def test_09_non_dict_json_response_is_treated_as_no_claims(client, db_session):
    m1 = _add_message(db_session, platform_msg_id="m_arr_resp")
    array_response = _gemini_response(["not", "an", "object"])
    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport([array_response]))

    stats = ingestion.run(db_session, client.manager_id, client=fake_client)

    assert stats["claims_created"] == 0
    db_session.refresh(m1)
    assert m1.is_processed is True


def test_07_llm_failure_leaves_batch_unprocessed(client, db_session):
    m1 = _add_message(db_session, platform_msg_id="m_fail_1", thread_id="thread-fail")
    m2 = _add_message(db_session, platform_msg_id="m_fail_2", thread_id="thread-fail")

    fake_client = GeminiClient(api_key="fake-key", transport=FakeTransport([RuntimeError("boom")]))

    stats = ingestion.run(db_session, client.manager_id, client=fake_client)

    assert stats["claims_created"] == 0
    assert stats["processed"] == 0
    db_session.refresh(m1)
    db_session.refresh(m2)
    assert m1.is_processed is False
    assert m2.is_processed is False
