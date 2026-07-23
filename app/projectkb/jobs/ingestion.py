"""Ingest job (spec/architecture_v2_kb.md §4.2, prompts/step_22_ingest_job.md):
blocklist/noise selection -> per-thread batching -> flash-model claim
extraction. Runs every INGESTION_INTERVAL_MINUTES per manager (see
app/projectkb/scheduler.py); this module owns one manager's tick.

Two distinct guards, don't confuse them:
- classify_message() (deterministic, no LLM) decides whether a message is
  even a candidate for claim extraction.
- INGESTION_BATCH_SIZE caps how many *candidate* messages get sent to the
  LLM in one run -- overflow simply waits for next tick, unprocessed.

Idempotency: a batch's source messages flip is_processed=True in the SAME
commit as their resulting Claim/ClaimSource rows. A retry can never
re-select an already-committed batch. content_hash is a secondary belt-
and-suspenders guard (checked before insert) since it carries no unique
DB constraint of its own.
"""
import hashlib
import logging
import uuid
from collections import OrderedDict
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import timeservice
from app.config import FLASH_MODEL, INGESTION_BATCH_SIZE
from app.database import Claim, ClaimSource, UnifiedMessage
from app.agent.gemini_client import GeminiClient, get_client
from app.projectkb.blocklist import classify_message
from app.projectkb.llm_json import parse_json_list_field

logger = logging.getLogger(__name__)


def _mark_skipped(msg: UnifiedMessage, reason: str) -> None:
    msg.is_processed = True
    msg.skip_reason = reason
    msg.processed_at = timeservice.now_ist()


def _batch_by_thread(messages: List[UnifiedMessage]) -> "OrderedDict[str, List[UnifiedMessage]]":
    """Groups survivors by thread_id, preserving first-seen order for
    deterministic test output. A message with no thread_id is its own
    single-message batch, keyed by its own id (never merged with other
    threadless messages -- they may be unrelated)."""
    batches: "OrderedDict[str, List[UnifiedMessage]]" = OrderedDict()
    for msg in messages:
        key = msg.thread_id or f"__solo_{msg.id}"
        batches.setdefault(key, []).append(msg)
    return batches


def _content_hash(text: str, message_ids: List[int]) -> str:
    payload = text.strip() + "|" + ",".join(str(i) for i in sorted(message_ids))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _msg_context(msg: UnifiedMessage) -> str:
    return (
        f"[message_id={msg.id}] from={msg.sender_raw_id} to={msg.receiver_raw_id or 'N/A'} "
        f"source={msg.source} timestamp={msg.timestamp.isoformat()} "
        f"subject={msg.subject or 'None'}\n{msg.content}"
    )


_SYSTEM_INSTRUCTION = (
    "You are a de-noising extraction system for a work knowledge base. Given "
    "a batch of messages that belong to the same conversation thread, extract "
    "short, atomic, verifiable claims -- factual statements, commitments, "
    "blockers, requests, decisions. Ignore pleasantries and irrelevant chatter.\n\n"
    "Crucial: Always treat requests, suggestions, or invitations to schedule, "
    "hold, or connect over meetings, syncs, calls, or chats (e.g., 'lets connect over a call', "
    "'can we sync up at 2pm') as actionable claims (requests or commitments) and extract them. "
    "They are high work-relevance items for our agent-inferred meeting scheduler.\n\n"
    "Also ignore, and extract nothing from, automated/system-generated content: "
    "account security alerts, sign-up/welcome/confirmation emails, marketing or "
    "promotional copy, free-credit or trial offers, and other notifications a "
    "human didn't personally write to this recipient. These sometimes reach you "
    "even after the deterministic sender filter (a service email from an "
    "unusual address, e.g. account-security-noreply@... or azure-noreply@...) -- "
    "judge by the CONTENT (is this a templated system notice, not a person "
    "reporting real work status?), not just the sender address. The one "
    "exception: if such a message reports something a manager would genuinely "
    "need to act on (e.g. a real security breach affecting the team, not a "
    "routine 'new sign-in verified' notice), extract that as a claim.\n\n"
    'Return strict JSON: {"claims": [{"text": "...", "message_ids": [..]}]}. '
    "Each claim's message_ids must be a non-empty subset of the message_id "
    "values shown in the batch, citing exactly which message(s) support it. "
    'If nothing in the batch has business/work relevance, return {"claims": []}.'
)


def _extract_claims_for_batch(client: GeminiClient, messages: List[UnifiedMessage]) -> List[Dict]:
    prompt = "\n\n---\n\n".join(_msg_context(m) for m in messages)
    res = client.chat(
        model=FLASH_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_INSTRUCTION},
            {"role": "user", "content": prompt},
        ],
        json_mode=True,
    )
    return parse_json_list_field(res, "claims", "[projectkb.ingestion]")


def run(db: Session, manager_id: str, client: Optional[GeminiClient] = None) -> Dict:
    """Ingestion job: flash model extracts structured Claim rows (with
    citations) from newly arrived, unblocklisted messages. See
    prompts/step_22_ingest_job.md for the full design; `client` is
    injectable for tests (mirrors app.kb.extraction.extract_from_message's
    convention), defaults to the real Gemini client otherwise."""
    real_client = client if client is not None else get_client()

    pending = list(
        db.scalars(
            select(UnifiedMessage)
            .where(UnifiedMessage.is_processed.is_(False))
            .order_by(UnifiedMessage.timestamp)
        ).all()
    )

    skipped_count = 0
    survivors: List[UnifiedMessage] = []
    for msg in pending:
        classification = classify_message(manager_id, msg)
        if classification is not None:
            _mark_skipped(msg, classification)
            skipped_count += 1
        else:
            survivors.append(msg)
    db.commit()

    if not survivors:
        return {"processed": skipped_count, "claims_created": 0, "skipped": skipped_count}

    # Cap applies to candidate MESSAGES, but only in WHOLE-thread units --
    # slicing mid-thread would let a later tick re-extract from a partial
    # conversation (no shared history with the already-committed half),
    # producing duplicate/divergent claims content_hash can't catch (the
    # message_ids subset differs). A thread larger than the cap on its own
    # still goes through whole, even if it overshoots -- better than
    # fragmenting it.
    all_batches = _batch_by_thread(survivors)
    batches: "OrderedDict[str, List[UnifiedMessage]]" = OrderedDict()
    running_total = 0
    for thread_key, batch_messages in all_batches.items():
        if running_total >= INGESTION_BATCH_SIZE:
            break
        batches[thread_key] = batch_messages
        running_total += len(batch_messages)

    claims_created = 0
    messages_processed = 0
    for batch_messages in batches.values():
        try:
            raw_claims = _extract_claims_for_batch(real_client, batch_messages)
        except Exception:
            logger.exception(
                f"[projectkb.ingestion] extraction failed for a batch of {len(batch_messages)} "
                f"message(s), manager={manager_id}; leaving batch unprocessed for next tick"
            )
            continue

        batch_ids = {m.id for m in batch_messages}
        batch_created = 0
        for raw_claim in raw_claims:
            text = (raw_claim.get("text") or "").strip()
            message_ids = [int(mid) for mid in (raw_claim.get("message_ids") or []) if str(mid).isdigit() and int(mid) in batch_ids]
            if not text or not message_ids:
                continue

            content_hash = _content_hash(text, message_ids)
            existing = db.scalar(select(Claim).where(Claim.content_hash == content_hash))
            if existing is not None:
                continue

            claim = Claim(
                id=uuid.uuid4().hex,
                text=text,
                thread_key=batch_messages[0].thread_id,
                content_hash=content_hash,
            )
            db.add(claim)
            db.flush()
            for mid in message_ids:
                db.add(ClaimSource(claim_id=claim.id, message_id=mid))
            batch_created += 1

        for msg in batch_messages:
            msg.is_processed = True
            msg.processed_at = timeservice.now_ist()

        db.commit()
        claims_created += batch_created
        messages_processed += len(batch_messages)

    return {
        "processed": skipped_count + messages_processed,
        "claims_created": claims_created,
        "skipped": skipped_count,
    }
