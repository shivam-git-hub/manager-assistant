"""Outlook "Sign in with Microsoft" -- an auth-code-redirect flow.

Identity and mailbox-read grant are separate: login (`LOGIN_SCOPES`, just
User.Read) only establishes who this manager is -- it does NOT grant Pulse
mailbox read access. Connecting the mailbox is its own explicit action from
the Connectors page (`/auth/outlook/connect-mail`, `MAIL_SCOPES`), matching
how Slack works. Mail.Send is a further, separate incremental-consent step
on top of that.

Confidential client throughout (MS_GRAPH_CLIENT_SECRET required) -- see
OUTLOOK.md for the Azure app registration this needs (a Web platform +
redirect URI + client secret).

state is HMAC-signed (not a server-side table) and carries a `purpose`
("login" | "connect_mail" | "enable_send") plus, for the latter two, the
manager_id -- the callback branches on purpose. Signed rather than stored in
a cookie so the callback doesn't depend on cookies surviving the redirect
round trip -- the same technique the Slack state uses.
"""
import base64
import hashlib
import hmac
import logging
import secrets
from typing import Optional

import httpx
import msal
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from app.config import MS_GRAPH_REDIRECT_URI, FRONTEND_URL
from app.controlplane.models import (
    SessionLocal as ControlPlaneSessionLocal,
    Employee, get_employee_by_email, get_employee_by_manager_id,
)
from app.controlplane.auth import create_session, get_current_employee, SESSION_COOKIE_NAME, SESSION_TTL_DAYS
from app.integrations.outlook import connector as outlook_connector, load_token_cache_for_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/outlook", tags=["Outlook Auth"])

# offline_access (what actually gets a refresh token) is a reserved scope --
# MSAL requests it automatically and raises if it's passed explicitly.
LOGIN_SCOPES = ["User.Read"]  # identity only -- no mailbox access implied
MAIL_SCOPES = ["Mail.Read"]  # the Connectors-page "connect mailbox" grant
SEND_SCOPE = ["Mail.Send"]
GRAPH_ME_URL = "https://graph.microsoft.com/v1.0/me"


def _authority() -> str:
    return f"https://login.microsoftonline.com/{outlook_connector.tenant_id}"


def _state_secret() -> str:
    return outlook_connector.client_secret or "dev-insecure-state-secret"


def _sign_state(purpose: str, manager_id: str = "") -> str:
    nonce = secrets.token_urlsafe(12)
    payload = f"{purpose}:{manager_id}:{nonce}"
    sig = hmac.new(_state_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return base64.urlsafe_b64encode(f"{payload}:{sig}".encode()).decode()


def _verify_state(state: str):
    """Returns (purpose, manager_id) or (None, None) if invalid/tampered."""
    try:
        decoded = base64.urlsafe_b64decode(state.encode()).decode()
        purpose, manager_id, nonce, sig = decoded.split(":", 3)
    except Exception:
        return None, None
    expected = hmac.new(_state_secret().encode(), f"{purpose}:{manager_id}:{nonce}".encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(expected, sig):
        return None, None
    return purpose, (manager_id or None)


def _confidential_app(preload_cache: Optional[msal.SerializableTokenCache] = None) -> msal.ConfidentialClientApplication:
    # A plain (unserializable) TokenCache is what MSAL defaults to if no
    # token_cache is passed -- pass a SerializableTokenCache explicitly so
    # the callback can persist it onto the Employee row afterward.
    # preload_cache lets enable-send merge the new (broader-scope) token
    # into the manager's EXISTING cache instead of overwriting it with a
    # fresh one that only has the new token.
    return msal.ConfidentialClientApplication(
        outlook_connector.client_id,
        authority=_authority(),
        client_credential=outlook_connector.client_secret,
        token_cache=preload_cache if preload_cache is not None else msal.SerializableTokenCache(),
    )


def _require_configured():
    if not (outlook_connector.client_id and outlook_connector.client_secret and outlook_connector.tenant_id):
        raise HTTPException(500, "MS_GRAPH_CLIENT_ID/MS_GRAPH_CLIENT_SECRET/MS_GRAPH_TENANT_ID must be set -- see OUTLOOK.md")


@router.get("/login")
def outlook_login():
    """Identity only -- User.Read, no mailbox access. See module docstring;
    connecting the mailbox is a separate action (`/connect-mail`)."""
    _require_configured()
    state = _sign_state("login")
    authorize_url = _confidential_app().get_authorization_request_url(
        LOGIN_SCOPES, state=state, redirect_uri=MS_GRAPH_REDIRECT_URI
    )
    logger.debug("[outlook-auth] redirecting to Microsoft authorize endpoint (login)")
    return RedirectResponse(authorize_url)


@router.get("/connect-mail")
def outlook_connect_mail(manager: Employee = Depends(get_current_employee)):
    """The Connectors-page action that actually grants Pulse mailbox read
    access (Mail.Read) -- separate from login. Requires an existing session
    (must already be logged in via /login)."""
    _require_configured()
    state = _sign_state("connect_mail", manager.id)
    authorize_url = _confidential_app().get_authorization_request_url(
        MAIL_SCOPES, state=state, redirect_uri=MS_GRAPH_REDIRECT_URI
    )
    logger.debug(f"[outlook-auth] redirecting manager={manager.id} to Microsoft authorize endpoint (connect_mail)")
    return RedirectResponse(authorize_url)


@router.get("/enable-send")
def outlook_enable_send(manager: Employee = Depends(get_current_employee)):
    """Second, optional consent screen requesting Mail.Send on top of
    Mail.Read. Requires the mailbox to already be connected (`/connect-mail`
    first) -- you can't enable send before you've connected Outlook at all."""
    _require_configured()

    db = ControlPlaneSessionLocal()
    try:
        employee = get_employee_by_manager_id(db, manager.id)
    finally:
        db.close()
    if employee is None or not employee.outlook_mailbox_email:
        raise HTTPException(400, "Connect Outlook mailbox before enabling send permissions")

    state = _sign_state("enable_send", manager.id)
    # prompt="consent" forces Microsoft to SHOW the permission screen
    # ("send mail as you") even when the consent technically already exists
    # from an earlier grant -- without it, a user who ever consented before
    # sees only a sign-in flash and reasonably doubts anything happened.
    authorize_url = _confidential_app().get_authorization_request_url(
        MAIL_SCOPES + SEND_SCOPE, state=state, redirect_uri=MS_GRAPH_REDIRECT_URI, prompt="consent"
    )
    logger.debug(f"[outlook-auth] redirecting manager={manager.id} to Microsoft authorize endpoint (enable_send)")
    return RedirectResponse(authorize_url)


@router.post("/revoke-send")
def outlook_revoke_send(manager: Employee = Depends(get_current_employee)):
    """Our-side send revoke: drop Mail.Send from outlook_granted_scopes so
    Pulse stops requesting/using send tokens (the outbound path gates on
    this -- see OutlookConnector.send). Microsoft keeps the underlying
    consent (there is no app-side API to revoke a delegated grant); the
    user can remove that themselves at account.live.com/consent. Read
    access is untouched."""
    db = ControlPlaneSessionLocal()
    try:
        employee = get_employee_by_manager_id(db, manager.id)
        if employee is None or not employee.outlook_mailbox_email:
            raise HTTPException(400, "Outlook is not connected")
        granted = [s for s in (employee.outlook_granted_scopes or "").split(",") if s and s != "Mail.Send"]
        employee.outlook_granted_scopes = ",".join(granted)
        db.commit()
        logger.info(f"[outlook-auth] send permission revoked (our side) for manager={manager.id}")
    finally:
        db.close()
    return {"status": "ok"}


@router.post("/disconnect")
def outlook_disconnect(manager: Employee = Depends(get_current_employee)):
    """Forgets our stored token cache for this manager. Does NOT revoke the
    grant on Microsoft's side -- Microsoft has no API for an app to revoke
    its own delegated grant; the user can do that themselves at
    account.live.com/consent (linked from the manage UI) if they want the
    consent itself gone, not just our copy of the token."""
    db = ControlPlaneSessionLocal()
    try:
        employee = get_employee_by_manager_id(db, manager.id)
        if employee and employee.outlook_mailbox_email:
            employee.outlook_mailbox_email = None
            employee.outlook_token_cache_json = None
            employee.outlook_granted_scopes = None
            db.commit()
            logger.info(f"[outlook-auth] disconnected Outlook for manager={manager.id}")
    finally:
        db.close()
    return {"status": "ok"}


@router.get("/callback")
def outlook_callback(code: str = None, state: str = None, error: str = None, error_description: str = None):
    if error:
        raise HTTPException(400, f"Outlook login failed: {error_description or error}")
    if not code or not state:
        raise HTTPException(400, "Missing code/state")

    purpose, state_manager_id = _verify_state(state)
    if purpose is None:
        raise HTTPException(400, "Invalid or tampered OAuth state")

    if purpose == "enable_send":
        return _handle_enable_send_callback(code, state_manager_id)
    if purpose == "connect_mail":
        return _handle_connect_mail_callback(code, state_manager_id)
    return _handle_login_callback(code)


def _handle_login_callback(code: str):
    """Identity only: exchanges the code for a User.Read-scoped token just
    long enough to call Graph /me, then discards it -- no cache is
    persisted, no mailbox credential is written. See module docstring."""
    app_msal = _confidential_app()
    result = app_msal.acquire_token_by_authorization_code(code, scopes=LOGIN_SCOPES, redirect_uri=MS_GRAPH_REDIRECT_URI)
    if "access_token" not in result:
        logger.error(f"[outlook-auth] token exchange failed: {result.get('error_description')}")
        raise HTTPException(400, f"Token exchange failed: {result.get('error_description', result.get('error'))}")

    me_resp = httpx.get(GRAPH_ME_URL, headers={"Authorization": f"Bearer {result['access_token']}"}, timeout=15)
    if me_resp.status_code != 200:
        logger.error(f"[outlook-auth] Graph /me failed: {me_resp.status_code} {me_resp.text}")
        raise HTTPException(400, f"Graph /me call failed: {me_resp.text}")
    profile = me_resp.json()
    email = profile.get("mail") or profile.get("userPrincipalName")
    name = profile.get("displayName") or email
    if not email:
        raise HTTPException(400, "Graph /me returned no mail/userPrincipalName")

    db = ControlPlaneSessionLocal()
    try:
        # Reject login outright for anyone not already in the pre-seeded
        # Employee directory (admin manually inserts rows) -- checked before
        # any is_manager flip or scaffold, so a rejected attempt leaves no
        # trace. Real production auth, unlike dev-login (which stays
        # permissive for test/bootstrap convenience -- see api.py).
        employee = get_employee_by_email(db, email)
        if employee is None:
            logger.warning(f"[outlook-auth] login rejected: {email} is not in the Employee directory")
            # Redirect back to the login page with an error query param
            # rather than raising -- this endpoint is hit by the browser
            # following Microsoft's OAuth redirect, not an API caller, so a
            # raw JSON 403 has nowhere to render; the frontend Login page
            # reads ?error= and shows it inline.
            return RedirectResponse(url=f"{FRONTEND_URL}/?error=not_recognized_employee")

        first_time_manager = not employee.is_manager
        if first_time_manager:
            employee.is_manager = True
            db.commit()
            logger.info(f"[outlook-auth] {employee.id} ({email}) signed in as manager for the first time")

            from app.tenancy.paths import ensure_manager_scaffold
            ensure_manager_scaffold(employee.id)
        else:
            logger.info(f"[outlook-auth] existing manager {employee.id} ({email}) signed in")

        token = create_session(db, employee.id)
    finally:
        db.close()

    resp = RedirectResponse(url=f"{FRONTEND_URL}/")
    resp.set_cookie(SESSION_COOKIE_NAME, token, httponly=True, samesite="lax", max_age=SESSION_TTL_DAYS * 86400)
    return resp


def _handle_connect_mail_callback(code: str, manager_id: str):
    """The Connectors-page grant: exchanges the code for a Mail.Read token
    and persists mailbox_email/token_cache/granted_scopes onto this
    manager's Employee row -- the first real Outlook credential write."""
    if not manager_id:
        raise HTTPException(400, "Missing manager in state")

    app_msal = _confidential_app()
    result = app_msal.acquire_token_by_authorization_code(code, scopes=MAIL_SCOPES, redirect_uri=MS_GRAPH_REDIRECT_URI)
    if "access_token" not in result:
        logger.error(f"[outlook-auth] connect_mail token exchange failed: {result.get('error_description')}")
        raise HTTPException(400, f"Token exchange failed: {result.get('error_description', result.get('error'))}")

    me_resp = httpx.get(GRAPH_ME_URL, headers={"Authorization": f"Bearer {result['access_token']}"}, timeout=15)
    if me_resp.status_code != 200:
        logger.error(f"[outlook-auth] Graph /me failed: {me_resp.status_code} {me_resp.text}")
        raise HTTPException(400, f"Graph /me call failed: {me_resp.text}")
    profile = me_resp.json()
    mailbox_email = profile.get("mail") or profile.get("userPrincipalName")

    db = ControlPlaneSessionLocal()
    try:
        employee = get_employee_by_manager_id(db, manager_id)
        if employee is None:
            raise HTTPException(400, "No Employee record found for this manager -- sign in first")
        employee.outlook_mailbox_email = mailbox_email
        employee.outlook_token_cache_json = app_msal.token_cache.serialize()
        employee.outlook_granted_scopes = ",".join(MAIL_SCOPES)
        db.commit()
        logger.info(f"[outlook-auth] connected Outlook mailbox {mailbox_email} for manager={manager_id}")
    finally:
        db.close()

    return RedirectResponse(url=f"{FRONTEND_URL}/connectors?connected=outlook")


def _handle_enable_send_callback(code: str, manager_id: str):
    if not manager_id:
        raise HTTPException(400, "Missing manager in state")

    # Preload the manager's existing cache so the new (broader-scope) token
    # merges into it rather than replacing it with a cache that only has
    # the new token.
    existing_cache = load_token_cache_for_manager(manager_id)
    app_msal = _confidential_app(preload_cache=existing_cache)
    result = app_msal.acquire_token_by_authorization_code(
        code, scopes=MAIL_SCOPES + SEND_SCOPE, redirect_uri=MS_GRAPH_REDIRECT_URI
    )
    if "access_token" not in result:
        logger.error(f"[outlook-auth] enable_send token exchange failed: {result.get('error_description')}")
        raise HTTPException(400, f"Token exchange failed: {result.get('error_description', result.get('error'))}")

    db = ControlPlaneSessionLocal()
    try:
        employee = get_employee_by_manager_id(db, manager_id)
        if employee is None or not employee.outlook_mailbox_email:
            raise HTTPException(400, "No connected Outlook mailbox found for this manager")
        employee.outlook_token_cache_json = app_msal.token_cache.serialize()
        employee.outlook_granted_scopes = ",".join(MAIL_SCOPES + SEND_SCOPE)
        db.commit()
        logger.info(f"[outlook-auth] send permission enabled for manager={manager_id}")
    finally:
        db.close()

    return RedirectResponse(url=f"{FRONTEND_URL}/connectors?connected=outlook&send_enabled=1")
