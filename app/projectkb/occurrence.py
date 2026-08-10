"""Derives Event.occurred_at -- when the messages behind an event's cited
claims actually happened, as opposed to Event.created_at (when the
heartbeat job noticed and inserted the row). Computed here, in code, never
LLM-supplied -- see the Event.occurred_at column comment in app/database.py.

Shared by the current heartbeat.py event-creation path and the future
agentic heartbeat (step 35), so this stays a standalone helper rather than
inlined logic in either job.
"""
from datetime import datetime
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import ClaimSource, UnifiedMessage


def compute_occurred_at(db: Session, claim_ids: List[str]) -> Optional[datetime]:
    """max(UnifiedMessage.timestamp) across every message cited by any of
    these claim_ids, via ClaimSource. None when claim_ids is empty or none
    of them resolve to a real source message (unsourced claim, or a claim
    id that doesn't exist) -- callers keep created_at as the sole timestamp
    in that case."""
    if not claim_ids:
        return None
    return db.query(func.max(UnifiedMessage.timestamp)).join(
        ClaimSource, ClaimSource.message_id == UnifiedMessage.id
    ).filter(ClaimSource.claim_id.in_(claim_ids)).scalar()
