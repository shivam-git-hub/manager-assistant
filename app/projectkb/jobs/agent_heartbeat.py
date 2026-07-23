"""Personal agent heartbeat (step 28 -- prompts/step_28_personal_agent.md
§6A). A 5th tick, not a re-detection pass: ingestion/heartbeat/dream have
already turned raw messages into claims/events/task-transitions/synthesis.
This job only ACTS on what they already produced -- deterministic candidate
selection in code (app.agent.select), one LLM tool-calling run to draft
text and take the actions, idempotency enforced by AgentActionLog (written
by the tool handlers themselves, not left to the model to remember).

Same run(db, manager_id, client=None) signature convention as the other
jobs (ingestion.py/heartbeat.py/dream.py), registered in
app.projectkb.scheduler's _JOBS dict under JobName.AGENT_HEARTBEAT.
"""
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.agent.gemini_client import GeminiClient
from app.agent.harness import run_agent
from app.agent.select import build_candidates

logger = logging.getLogger(__name__)


def run(db: Session, manager_id: str, client: Optional[GeminiClient] = None) -> dict:
    candidates = build_candidates(db, manager_id)
    if not candidates:
        return {"acted": False, "reason": "no candidates this tick"}

    directive = (
        f"HEARTBEAT TICK: you have {len(candidates)} candidate item(s) to work through -- see the "
        f"CANDIDATE ITEMS section of your context and the instructions that follow it."
    )

    try:
        result = run_agent(db, manager_id, directive, history=[], client=client, candidates=candidates)
    except Exception:
        logger.exception(f"[agent_heartbeat] run failed for manager={manager_id}")
        return {"acted": False, "reason": "run_agent raised", "candidate_count": len(candidates)}

    return {
        "acted": True,
        "candidate_count": len(candidates),
        "reply": result.get("reply", ""),
        "tool_trace": result.get("tool_trace", []),
    }
