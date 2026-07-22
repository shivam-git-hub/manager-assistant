"""Outlook "Sign in with Microsoft" -- the auth-code-redirect flow that
replaces the old device-code login (app/integrations/outlook.py used to
have /device-login/start + /device-login/status; removed in step 13).

Does double duty: identity (who is this manager, via Graph /me) AND grant
(Mail.Read/User.Read access, via the same consent screen), per the earlier
decision to fold login and Outlook-connect together for this provider
specifically (Slack, in step 14, keeps them separate since a Slack install
doesn't establish identity the way an Outlook sign-in does).

Send access (Mail.Send) is a SEPARATE, optional, later grant via
incremental consent -- some users aren't comfortable letting an assistant
send mail on their behalf even if they're fine with it reading mail. Login
only ever requests BASE_SCOPES; /auth/outlook/enable-send is a second,
smaller consent screen requesting the fuller scope set. Microsoft Graph
only prompts for what hasn't already been granted, so this is genuinely a
"one more click" flow for the user, not a second full re-auth.

Confidential client throughout (MS_GRAPH_CLIENT_SECRET required) -- see
OUTLOOK.md for the Azure app registration changes this needs (a Web
platform + redirect URI + client secret) beyond the old device-code setup.

state is HMAC-signed (not a server-side table) and carries a `purpose`
("login" or "enable_send") plus, for enable_send, the manager_id -- the
callback branches on purpose to decide whether it's establishing a new
identity/session or just adding a scope to an existing one. Signed rather
than a cookie (step 13's original approach) so the callback doesn't depend
on cookies surviving the redirect round trip -- same technique step 14's
Slack state already uses.
"""
import base64
import hashlib
import hmac
import logging
import secrets
import uuid
from typing import Optional

import httpx
import msal
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from app.config import MS_GRAPH_REDIRECT_URI, FRONTEND_URL
from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Manager, OutlookInstallation
from app.controlplane.auth import create_session, get_current_manager, SESSION_COOKIE_NAME, SESSION_TTL_DAYS
from app.integrations.outlook import connector as outlook_connector, load_token_cache_for_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/outlook", tags=["Outlook Auth"])

# offline_access (what actually gets a refresh token) is a reserved scope --
# MSAL requests it automatically and raises if it's passed explicitly.
BASE_SCOPES = ["Mail.Read", "User.Read"]
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
    # the callback can persist it to OutlookInstallation afterward.
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
    _require_configured()
    state = _sign_state("login")
    authorize_url = _confidential_app().get_authorization_request_url(
        BASE_SCOPES, state=state, redirect_uri=MS_GRAPH_REDIRECT_URI
    )
    logger.debug("[outlook-auth] redirecting to Microsoft authorize endpoint (login)")
    return RedirectResponse(authorize_url)


@router.get("/enable-send")
def outlook_enable_send(manager: Manager = Depends(get_current_manager)):
    """Second, optional consent screen requesting Mail.Send on top of
    whatever the manager already granted at login. Requires an existing
    OutlookInstallation -- you can't enable send before you've connected
    Outlook at all."""
    _require_configured()

    db = ControlPlaneSessionLocal()
    try:
        installation = db.get(OutlookInstallation, manager.id)
    finally:
        db.close()
    if installation is None:
        raise HTTPException(400, "Connect Outlook (sign in) before enabling send permissions")

    state = _sign_state("enable_send", manager.id)
    # prompt="consent" forces Microsoft to SHOW the permission screen
    # ("send mail as you") even when the consent technically already exists
    # from an earlier grant -- without it, a user who ever consented before
    # sees only a sign-in flash and reasonably doubts anything happened.
    authorize_url = _confidential_app().get_authorization_request_url(
        BASE_SCOPES + SEND_SCOPE, state=state, redirect_uri=MS_GRAPH_REDIRECT_URI, prompt="consent"
    )
    logger.debug(f"[outlook-auth] redirecting manager={manager.id} to Microsoft authorize endpoint (enable_send)")
    return RedirectResponse(authorize_url)


@router.post("/revoke-send")
def outlook_revoke_send(manager: Manager = Depends(get_current_manager)):
    """Our-side send revoke: drop Mail.Send from granted_scopes so Pulse
    stops requesting/using send tokens (the outbound path gates on this --
    see OutlookConnector.send). Microsoft keeps the underlying consent
    (there is no app-side API to revoke a delegated grant); the user can
    remove that themselves at account.live.com/consent. Read access is
    untouched."""
    db = ControlPlaneSessionLocal()
    try:
        installation = db.get(OutlookInstallation, manager.id)
        if installation is None:
            raise HTTPException(400, "Outlook is not connected")
        granted = [s for s in (installation.granted_scopes or "").split(",") if s and s != "Mail.Send"]
        installation.granted_scopes = ",".join(granted)
        db.commit()
        logger.info(f"[outlook-auth] send permission revoked (our side) for manager={manager.id}")
    finally:
        db.close()
    return {"status": "ok"}


@router.post("/disconnect")
def outlook_disconnect(manager: Manager = Depends(get_current_manager)):
    """Forgets our stored token cache for this manager. Does NOT revoke the
    grant on Microsoft's side -- Microsoft has no API for an app to revoke
    its own delegated grant; the user can do that themselves at
    account.live.com/consent (linked from the manage UI) if they want the
    consent itself gone, not just our copy of the token."""
    db = ControlPlaneSessionLocal()
    try:
        installation = db.get(OutlookInstallation, manager.id)
        if installation:
            db.delete(installation)
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
    return _handle_login_callback(code)


def _handle_login_callback(code: str):
    app_msal = _confidential_app()
    result = app_msal.acquire_token_by_authorization_code(code, scopes=BASE_SCOPES, redirect_uri=MS_GRAPH_REDIRECT_URI)
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
        manager = db.query(Manager).filter(Manager.email == email).first()
        if not manager:
            manager = Manager(id=uuid.uuid4().hex, email=email, name=name)
            db.add(manager)
            db.commit()
            db.refresh(manager)
            logger.info(f"[outlook-auth] created new manager {manager.id} ({email})")

            from app.tenancy.paths import ensure_manager_scaffold
            ensure_manager_scaffold(manager.id)
        else:
            logger.info(f"[outlook-auth] existing manager {manager.id} ({email}) signed in")

        installation = db.get(OutlookInstallation, manager.id)
        cache_json = app_msal.token_cache.serialize()
        granted = ",".join(BASE_SCOPES)
        if installation:
            installation.mailbox_email = email
            installation.token_cache_json = cache_json
            installation.granted_scopes = granted
        else:
            installation = OutlookInstallation(
                manager_id=manager.id, mailbox_email=email, token_cache_json=cache_json, granted_scopes=granted
            )
            db.add(installation)
        db.commit()
        logger.info(f"[outlook-auth] connected Outlook mailbox {email} for manager={manager.id}")

        token = create_session(db, manager.id)
    finally:
        db.close()

    resp = RedirectResponse(url=f"{FRONTEND_URL}/")
    resp.set_cookie(SESSION_COOKIE_NAME, token, httponly=True, samesite="lax", max_age=SESSION_TTL_DAYS * 86400)
    return resp


def _handle_enable_send_callback(code: str, manager_id: str):
    if not manager_id:
        raise HTTPException(400, "Missing manager in state")

    # Preload the manager's existing cache so the new (broader-scope) token
    # merges into it rather than replacing it with a cache that only has
    # the new token.
    existing_cache = load_token_cache_for_manager(manager_id)
    app_msal = _confidential_app(preload_cache=existing_cache)
    result = app_msal.acquire_token_by_authorization_code(
        code, scopes=BASE_SCOPES + SEND_SCOPE, redirect_uri=MS_GRAPH_REDIRECT_URI
    )
    if "access_token" not in result:
        logger.error(f"[outlook-auth] enable_send token exchange failed: {result.get('error_description')}")
        raise HTTPException(400, f"Token exchange failed: {result.get('error_description', result.get('error'))}")

    db = ControlPlaneSessionLocal()
    try:
        installation = db.get(OutlookInstallation, manager_id)
        if installation is None:
            raise HTTPException(400, "No OutlookInstallation found for this manager")
        installation.token_cache_json = app_msal.token_cache.serialize()
        installation.granted_scopes = ",".join(BASE_SCOPES + SEND_SCOPE)
        db.commit()
        logger.info(f"[outlook-auth] send permission enabled for manager={manager_id}")
    finally:
        db.close()

    return RedirectResponse(url=f"{FRONTEND_URL}/connectors?connected=outlook&send_enabled=1")
