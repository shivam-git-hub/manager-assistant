import logging
import os
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from app.controlplane.models import get_controlplane_db, Manager, OutlookInstallation, Agent
from app.controlplane.auth import (
    create_session,
    delete_session,
    get_current_manager,
    SESSION_COOKIE_NAME,
    SESSION_TTL_DAYS,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Auth"])


def _dev_auth_enabled() -> bool:
    return os.getenv("DEV_AUTH_ENABLED", "true").lower() != "false"


class DevLoginPayload(BaseModel):
    email: str
    name: str = "Dev Manager"


def _manager_dict(manager: Manager) -> dict:
    return {"id": manager.id, "email": manager.email, "name": manager.name}


@router.post("/dev-login")
def dev_login(payload: DevLoginPayload, response: Response, db: DBSession = Depends(get_controlplane_db)):
    """Testing/bootstrap only -- real login is Outlook OAuth (step 13).
    Do not treat this as a production auth path. Gated by DEV_AUTH_ENABLED
    (defaults on) so it can be disabled without deleting the code."""
    if not _dev_auth_enabled():
        raise HTTPException(status_code=404, detail="Not found")

    manager = db.scalars(select(Manager).where(Manager.email == payload.email)).first()
    if not manager:
        manager = Manager(id=uuid.uuid4().hex, email=payload.email, name=payload.name)
        db.add(manager)
        db.commit()
        db.refresh(manager)
        logger.info(f"[auth] dev-login created new manager {manager.id} ({manager.email})")

        from app.tenancy.paths import ensure_manager_scaffold
        ensure_manager_scaffold(manager.id)

    token = create_session(db, manager.id)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        max_age=SESSION_TTL_DAYS * 86400,
    )
    return _manager_dict(manager)


@router.get("/me")
def me(manager: Manager = Depends(get_current_manager)):
    return _manager_dict(manager)


@router.get("/connections")
def connections(manager: Manager = Depends(get_current_manager), db: DBSession = Depends(get_controlplane_db)):
    """Everything the manage UI needs to render Outlook/Slack connection
    status -- connected or not, which mailbox/workspace/agent, and (Outlook
    only) whether send access has been granted yet. Slack is agent-pool-
    shaped now (step 17 piece 2b): a manager holds at most one Agent, so
    this is a single object/null, not a list of workspaces."""
    outlook_installation = db.get(OutlookInstallation, manager.id)
    agent = db.query(Agent).filter(Agent.manager_id == manager.id).first()

    outlook = None
    if outlook_installation:
        granted = (outlook_installation.granted_scopes or "").split(",")
        outlook = {
            "connected": True,
            "mailbox_email": outlook_installation.mailbox_email,
            "send_enabled": "Mail.Send" in granted,
        }

    slack = None
    if agent:
        slack = {
            "agent_id": agent.id,
            "agent_name": agent.name,
            "installed": agent.installed_at is not None,
            "team_id": agent.team_id,
            "read_enabled": bool(agent.user_token),
        }

    return {
        "outlook": outlook or {"connected": False},
        "slack": slack,
    }


@router.post("/logout")
def logout(request: Request, response: Response, db: DBSession = Depends(get_controlplane_db)):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        delete_session(db, token)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"status": "ok"}
