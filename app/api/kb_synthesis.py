"""POST /api/kb/ask -- the manual, ad-hoc counterpart to the scheduled KB
agents (heartbeat/dream/lint). Same auth + session pattern as
app.agent.api's /api/chat and /api/heartbeat/run: get_current_employee
resolves the logged-in manager from the session cookie, get_manager_db
opens a session on their own db.sqlite.
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agent.synthesis import run_synthesis
from app.controlplane.auth import get_current_employee
from app.controlplane.models import Employee
from app.tenancy.db import get_manager_db

router = APIRouter(prefix="/api/kb", tags=["Knowledge Synthesis"])


class KBAskRequest(BaseModel):
    question: str
    # Writes default ON -- see app.agent.synthesis's module docstring.
    # Pass false for a provably read-only run.
    allow_writes: bool = True


class KBAskResponse(BaseModel):
    # Optional: AgentRunResult.reply is None on a non-"final" stop_reason
    # (budget/deadline/tool_cap/empty_response/error) -- a run that didn't
    # end in a normal model-produced answer still returns 200 with the
    # trace/usage fields populated and answer=None, not a 500.
    answer: Optional[str] = None
    tool_trace: List[Dict[str, Any]]
    llm_calls: int
    tokens_in: int
    tokens_out: int
    stop_reason: str


@router.post("/ask", response_model=KBAskResponse)
def ask_kb(
    payload: KBAskRequest,
    db: Session = Depends(get_manager_db),
    manager: Employee = Depends(get_current_employee),
):
    """Runs the Knowledge Synthesis agent (app.agent.synthesis.run_synthesis)
    against the logged-in manager's own KB. Read-only by default;
    allow_writes=True additionally grants the KB write tools -- never
    send_message, see app.agent.synthesis's module docstring."""
    if not payload.question.strip():
        raise HTTPException(status_code=400, detail="question cannot be empty")

    result = run_synthesis(db, manager.id, payload.question, allow_writes=payload.allow_writes)

    return KBAskResponse(
        answer=result.reply,
        tool_trace=result.tool_trace,
        llm_calls=result.llm_calls,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        stop_reason=result.stop_reason,
    )
