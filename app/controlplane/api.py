import logging
import os
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from app.controlplane.models import (
    get_controlplane_db, Employee, get_employee_by_email, get_employee_by_manager_id,
)
from app.controlplane.auth import (
    create_session,
    delete_session,
    get_current_employee,
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


def _manager_dict(manager: Employee) -> dict:
    return {"id": manager.id, "email": manager.email, "name": manager.name}


@router.post("/dev-login")
def dev_login(payload: DevLoginPayload, response: Response, db: DBSession = Depends(get_controlplane_db)):
    """Testing/bootstrap only -- real login is Outlook OAuth.
    Do not treat this as a production auth path. Gated by DEV_AUTH_ENABLED
    (defaults on) so it can be disabled without deleting the code."""
    if not _dev_auth_enabled():
        raise HTTPException(status_code=404, detail="Not found")

    # Real logins (Outlook) reject an email not already in the Employee
    # directory -- see outlook_auth.py. dev-login stays permissive
    # (testing/bootstrap only, see docstring above) and upserts a row for
    # its throwaway test emails. Step 30: Employee IS the auth identity, no
    # separate Manager row to find-or-create.
    employee = get_employee_by_email(db, payload.email)
    is_new = employee is None
    if is_new:
        employee = Employee(id=uuid.uuid4().hex, email=payload.email.lower(), name=payload.name)
        db.add(employee)
        db.commit()
        db.refresh(employee)

    first_time_manager = not employee.is_manager
    if first_time_manager:
        employee.is_manager = True
        db.commit()
        from app.tenancy.paths import ensure_manager_scaffold
        ensure_manager_scaffold(employee.id)

    token = create_session(db, employee.id)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        max_age=SESSION_TTL_DAYS * 86400,
    )
    return _manager_dict(employee)


@router.get("/me")
def me(manager: Employee = Depends(get_current_employee)):
    return _manager_dict(manager)


@router.get("/connections")
def connections(manager: Employee = Depends(get_current_employee), db: DBSession = Depends(get_controlplane_db)):
    """Everything the manage UI needs to render Outlook/Slack connection
    status -- connected or not, which mailbox/workspace, and (Outlook only)
    whether send access has been granted yet. Slack here is ONLY the
    message-tracking grant -- deliberately unrelated to whether this
    manager has claimed an Agent; see GET /api/agents/mine for that.
    Credentials live on the Employee row."""
    employee = get_employee_by_manager_id(db, manager.id)

    outlook = None
    if employee and employee.outlook_mailbox_email:
        granted = (employee.outlook_granted_scopes or "").split(",")
        outlook = {
            "connected": True,
            "mailbox_email": employee.outlook_mailbox_email,
            "send_enabled": "Mail.Send" in granted,
        }

    slack = None
    if employee and employee.slack_user_token:
        slack = {
            "connected": True,
            "team_id": employee.slack_team_id,
            "team_name": employee.slack_team_name,
        }

    return {
        "outlook": outlook or {"connected": False},
        "slack": slack or {"connected": False},
    }


@router.post("/logout")
def logout(request: Request, response: Response, db: DBSession = Depends(get_controlplane_db)):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        delete_session(db, token)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"status": "ok"}
