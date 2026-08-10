"""Ingest job: blocklist selection -> per-thread chunking -> flash-model claim extraction.
Runs every INGESTION_INTERVAL_MINUTES per manager (see
app/projectkb/scheduler.py); this module owns one manager's tick.

Three distinct guards, don't confuse them:
- classify_message() (deterministic, no LLM) decides whether a message is
  even a candidate for claim extraction.
- INGESTION_BATCH_SIZE / INGESTION_MAX_CHARS_PER_CALL cap how big a single
  LLM call's prompt can get -- a thread bigger than either limit is split
  into multiple consecutive chunks (never reordered, never merged across
  threads), each its own LLM call.
- INGESTION_MAX_CALLS_PER_RUN caps how many LLM calls (chunks, across every
  survivor thread combined) happen in ONE run -- a safety ceiling for a
  large backlog. All work within that ceiling happens THIS run, however
  many calls it takes; only chunks beyond the ceiling wait for next tick.

Idempotency: a chunk's source messages flip is_processed=True in the SAME
commit as their resulting Claim/ClaimSource rows. A retry can never
re-select an already-committed chunk. content_hash is a secondary belt-
and-suspenders guard (checked before insert) since it carries no unique
DB constraint of its own.
"""
import hashlib
import logging
import uuid
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import timeservice
from app.config import FLASH_MODEL, INGESTION_BATCH_SIZE, INGESTION_MAX_CHARS_PER_CALL, INGESTION_MAX_CALLS_PER_RUN
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


def _chunk_thread(
    messages: List[UnifiedMessage], max_count: int, max_chars: int
) -> List[List[UnifiedMessage]]:
    """Splits one thread's messages (in order) into consecutive chunks, each
    bounded by max_count messages AND max_chars of combined content --
    whichever limit is hit first ends the chunk. A single message whose own
    content already exceeds max_chars still becomes its own one-message
    chunk (best-effort, not truncated or dropped) rather than being split
    mid-message or silently discarded."""
    chunks: List[List[UnifiedMessage]] = []
    current: List[UnifiedMessage] = []
    current_chars = 0
    for msg in messages:
        msg_chars = len(_msg_context(msg))
        if current and (len(current) >= max_count or current_chars + msg_chars > max_chars):
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(msg)
        current_chars += msg_chars
    if current:
        chunks.append(current)
    return chunks


def _content_hash(text: str, message_ids: List[int]) -> str:
    payload = text.strip() + "|" + ",".join(str(i) for i in sorted(message_ids))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _format_party(name: Optional[str], raw_id: str) -> str:
    # raw_id (Slack user id / email) is always present; the mapped name
    # (resolved against the Employee directory at ingest time, see
    # app.integrations.slack/outlook._resolve_member) is only missing for
    # an external sender or one not yet seeded into Employee -- send both
    # when we have a name, rather than one or the other.
    return f"{name} ({raw_id})" if name else raw_id


def _msg_context(msg: UnifiedMessage) -> str:
    sender = _format_party(msg.sender_mapped_name, msg.sender_raw_id)
    receiver = _format_party(msg.receiver_mapped_name, msg.receiver_raw_id) if msg.receiver_raw_id else "N/A"
    return (
        f"[message_id={msg.id}] from={sender} to={receiver} "
        f"source={msg.source} timestamp={msg.timestamp.isoformat()} "
        f"subject={msg.subject or 'None'}\n{msg.content}"
    )


_SYSTEM_INSTRUCTION = (
    "You are a de-noising extraction system for a work knowledge base. Given "
    "a batch of messages that belong to the same conversation thread, extract "
    "short, atomic, verifiable claims -- factual statements, commitments, "
    "blockers, requests, decisions. Ignore pleasantries and irrelevant chatter.\n\n"
    "Also ignore, and extract nothing from, automated/system-generated content: "
    "account security alerts, sign-up/welcome/confirmation emails, marketing or "
    "promotional copy, free-credit or trial offers, calendar accept/decline "
    "notifications, and other notifications a human didn't personally write "
    "to this recipient. These sometimes reach you "
    "even after the deterministic sender filter (a service email from an "
    "unusual address, e.g. account-security-noreply@... or azure-noreply@...) -- "
    "judge by the CONTENT (is this a templated system notice, not a person "
    "reporting real work status?), not just the sender address. The one "
    "exception: if such a message reports something a manager would genuinely "
    "need to act on (e.g. a real security breach affecting the team, not a "
    "routine 'new sign-in verified' notice), extract that as a claim.\n\n"
    "Never state a date, figure, name, or other detail that isn't explicitly "
    "present in the message content or precisely derivable from it -- do not "
    "invent or guess data that isn't there.\n\n"
    "When a message uses a relative date/time reference ('yesterday', 'next "
    "Friday', 'in two weeks'), resolve it against that message's own "
    "timestamp and double-check the arithmetic before writing an absolute "
    "date; if you are not fully certain of the resolved date, keep the "
    "original relative phrase in the claim instead of stating a specific "
    "date you are not sure of.\n\n"
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
    citations) from newly arrived, unblocklisted messages. `client` is
    injectable for tests, and defaults to the real Gemini client."""

    real_client = client if client is not None else get_client() ##LLM Client

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

    # Every survivor thread is chunked (never truncated) -- each chunk stays
    # within INGESTION_BATCH_SIZE messages and INGESTION_MAX_CHARS_PER_CALL
    # combined content, becoming its own LLM call. All chunks from this
    # run's survivors get processed, up to INGESTION_MAX_CALLS_PER_RUN LLM
    # calls total; only calls beyond that ceiling wait for next tick.
    all_batches = _batch_by_thread(survivors)
    call_units: List[Tuple[str, List[UnifiedMessage]]] = []
    for thread_key, thread_messages in all_batches.items():
        for chunk in _chunk_thread(thread_messages, INGESTION_BATCH_SIZE, INGESTION_MAX_CHARS_PER_CALL):
            call_units.append((thread_key, chunk))

    claims_created = 0
    messages_processed = 0
    for call_index, (thread_key, batch_messages) in enumerate(call_units):
        if call_index >= INGESTION_MAX_CALLS_PER_RUN:
            logger.info(
                f"[projectkb.ingestion] hit INGESTION_MAX_CALLS_PER_RUN "
                f"({INGESTION_MAX_CALLS_PER_RUN}), manager={manager_id}; "
                f"{len(call_units) - call_index} chunk(s) left for next tick"
            )
            break
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
            message_ids = [mid for mid in (raw_claim.get("message_ids") or []) if mid in batch_ids]
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
