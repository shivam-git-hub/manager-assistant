"""Session mechanism: a plain signed-nothing opaque token in an HttpOnly
cookie, looked up against the AuthSession table. No JWT -- one DB lookup
per request is fine at this scale, and it means logout/expiry are simple
row operations instead of needing a revocation list.

Real wall-clock time throughout -- auth sessions expire in real time
(app/controlplane/ is exempted from the wall-clock guard, same reasoning
as app/integrations/ and app/projectkb/)."""
import logging
import secrets
from datetime import datetime, timedelta
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session as DBSession

from app.controlplane.models import get_controlplane_db, Employee, AuthSession

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "harry_session"
SESSION_TTL_DAYS = 30


def create_session(db: DBSession, manager_id: str) -> str:
    """manager_id is an Employee.id (step 30: Employee IS the auth identity,
    no separate Manager table)."""
    token = secrets.token_urlsafe(32)
    now = datetime.now()
    session = AuthSession(
        token=token,
        manager_id=manager_id,
        created_at=now,
        expires_at=now + timedelta(days=SESSION_TTL_DAYS),
    )
    db.add(session)
    db.commit()
    logger.debug(f"[auth] created session for manager={manager_id}")
    return token


def delete_session(db: DBSession, token: str) -> None:
    session = db.get(AuthSession, token)
    if session:
        db.delete(session)
        db.commit()
        logger.debug(f"[auth] deleted session for manager={session.manager_id}")


def get_current_employee(request: Request, db: DBSession = Depends(get_controlplane_db)) -> Employee:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    session = db.get(AuthSession, token)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")
    if session.expires_at < datetime.now():
        logger.debug(f"[auth] session expired for manager={session.manager_id}")
        raise HTTPException(status_code=401, detail="Session expired")

    employee = db.get(Employee, session.manager_id)
    if not employee:
        raise HTTPException(status_code=401, detail="Employee not found")
    return employee
