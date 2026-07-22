"""Agent pool claim flow (step 17 piece 2a -- prompts/step_17_agent_pool.md).

Wires up the pool's identity/isolation machinery: redeem the shared access
code, pick an unclaimed bot, claim it. The install cutover itself
(agent-aware /auth/slack/install, webhook routed by api_app_id, send()
reading from Agent) is in app/controlplane/slack_auth.py and
app/integrations/slack.py (piece 2b).
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.config import AGENT_POOL_ACCESS_CODE
from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Manager, Agent
from app.controlplane.auth import get_current_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["Agent Pool"])


class RedeemRequest(BaseModel):
    code: str


class ClaimRequest(BaseModel):
    agent_id: str


@router.post("/redeem")
def redeem_code(payload: RedeemRequest, manager: Manager = Depends(get_current_manager)):
    if not AGENT_POOL_ACCESS_CODE:
        raise HTTPException(500, "AGENT_POOL_ACCESS_CODE is not configured")
    if payload.code != AGENT_POOL_ACCESS_CODE:
        raise HTTPException(403, "Invalid access code")

    db = ControlPlaneSessionLocal()
    try:
        unassigned = db.query(Agent).filter(Agent.manager_id.is_(None)).all()
        return {"agents": [{"id": a.id, "name": a.name} for a in unassigned]}
    finally:
        db.close()


@router.post("/claim")
def claim_agent(payload: ClaimRequest, manager: Manager = Depends(get_current_manager)):
    db = ControlPlaneSessionLocal()
    try:
        existing = db.get(Agent, payload.agent_id)
        if existing is None:
            raise HTTPException(404, "Unknown agent")

        if existing.manager_id == manager.id:
            # Idempotent re-claim -- a manager re-picking their own already-
            # claimed agent (e.g. retried request) just succeeds, no-op.
            agent = existing
        else:
            now = datetime.now()
            try:
                result = db.execute(
                    update(Agent)
                    .where(Agent.id == payload.agent_id, Agent.manager_id.is_(None))
                    .values(manager_id=manager.id, claimed_at=now)
                )
                db.commit()
            except IntegrityError:
                # manager_id is unique -- this manager already owns a
                # different agent; the schema is the actual race-safe gate.
                db.rollback()
                raise HTTPException(409, "You already have an agent assigned")

            if result.rowcount != 1:
                # Someone else claimed this slot between the read above and
                # the guarded UPDATE -- the WHERE clause, not the read, is
                # what actually loses the race.
                raise HTTPException(409, "Agent already claimed")

            agent = db.get(Agent, payload.agent_id)
    finally:
        db.close()

    _write_assignment_mirror(manager.id, agent)
    logger.info(f"[agents] manager={manager.id} claimed agent={agent.id}")
    return {"agent_id": agent.id, "agent_name": agent.name}


def _write_assignment_mirror(manager_id: str, agent: Agent) -> None:
    """Per the explicit isolation requirement: the assignment must be
    visible from the manager's own db.sqlite too, not just the control-plane
    Agent row -- see app.database.AgentAssignment."""
    from app.tenancy.db import get_manager_session
    from app.database import AgentAssignment

    mdb = get_manager_session(manager_id)
    try:
        mirror = mdb.get(AgentAssignment, agent.id)
        if mirror is None:
            mdb.add(AgentAssignment(agent_id=agent.id, agent_name=agent.name, team_id=agent.team_id))
        else:
            mirror.agent_name = agent.name
            mirror.team_id = agent.team_id
        mdb.commit()
    finally:
        mdb.close()
