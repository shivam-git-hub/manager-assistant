"""Agent pool claim flow.

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
from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Employee, Agent
from app.controlplane.auth import get_current_employee

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["Agent Pool"])


class ClaimRequest(BaseModel):
    agent_id: str
    code: str


@router.get("/mine")
def my_agent(manager: Employee = Depends(get_current_employee)):
    """The bot this manager has claimed, if any -- deliberately separate
    from GET /api/auth/connections' `slack` key (that's the unrelated
    message-tracking grant). `installed` reflects
    whether the admin has installed this agent's Slack app to the
    workspace yet (a manual row insert/update) -- purely informational, there
    is nothing for the manager to do about it either way."""
    db = ControlPlaneSessionLocal()
    try:
        agent = db.query(Agent).filter(Agent.manager_id == manager.id).first()
        if agent is None:
            return None
        return {
            "agent_id": agent.id,
            "agent_name": agent.name,
            "installed": agent.bot_token is not None,
            "team_id": agent.team_id,
        }
    finally:
        db.close()


@router.get("/available")
def list_available_agents(manager: Employee = Depends(get_current_employee)):
    """Unclaimed pool agents -- visible to any logged-in user WITHOUT the
    access code: users see what's available first, and the admin's code
    gates the CLAIM, not the view."""
    db = ControlPlaneSessionLocal()
    try:
        unassigned = db.query(Agent).filter(Agent.manager_id.is_(None)).all()
        return {"agents": [{"id": a.id, "name": a.name} for a in unassigned]}
    finally:
        db.close()


@router.post("/claim")
def claim_agent(payload: ClaimRequest, manager: Employee = Depends(get_current_employee)):
    if not AGENT_POOL_ACCESS_CODE:
        raise HTTPException(500, "AGENT_POOL_ACCESS_CODE is not configured")
    if payload.code != AGENT_POOL_ACCESS_CODE:
        raise HTTPException(403, "Invalid access code")

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


@router.post("/release")
def release_agent(manager: Employee = Depends(get_current_employee)):
    """Unclaim this manager's agent (Agents tab "Remove").
    Clears manager_id/claimed_at on the Agent row, freeing it back into the
    available pool for anyone to claim -- install-derived fields
    (bot_token/team_id/user_token/user_id) are left as-is, mirroring
    slack_auth.disconnect's reasoning: they belong to the Slack app
    installation, not the claim, so a future claimant of the same bot
    reuses the same identity rather than re-installing."""
    db = ControlPlaneSessionLocal()
    try:
        agent = db.query(Agent).filter(Agent.manager_id == manager.id).first()
        if agent is None:
            raise HTTPException(404, "No agent claimed")
        agent.manager_id = None
        agent.claimed_at = None
        db.commit()
        agent_id = agent.id
    finally:
        db.close()

    from app.tenancy.db import get_manager_session
    from app.database import AgentAssignment

    mdb = get_manager_session(manager.id)
    try:
        mirror = mdb.get(AgentAssignment, agent_id)
        if mirror is not None:
            mdb.delete(mirror)
            mdb.commit()
    finally:
        mdb.close()

    logger.info(f"[agents] manager={manager.id} released agent={agent_id}")
    return {"status": "released"}


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
