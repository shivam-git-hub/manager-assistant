"""Slack "Connect" for MESSAGE TRACKING -- a user-token-only OAuth flow,
deliberately independent of the Agent pool (see the Agent/
SlackReaderInstallation docstrings in app/controlplane/models.py for the
2026-07-23 redesign this replaces).

Mirrors app/controlplane/outlook_auth.py's shape closely: ONE globally
configured Slack app (SLACK_READER_CLIENT_ID/SECRET, app/config.py), state
signed with a single secret (not a per-agent one -- there is no agent
involved here at all), requires the manager to already be logged in (via
Outlook) since this is CONNECT, not login. Requests `user_scope` only, no
bot `scope` -- this app never becomes a bot presence in the workspace, it
only ever acts as the installing manager's own Slack identity, polled by
app/projectkb/jobs/slack_poll.py exactly like the old Agent-based reader
did.

Sending/being-messaged-as-a-bot is a completely separate concern, still
handled by the Agent pool (app/controlplane/agents.py for claiming,
app/integrations/slack.py for the bot-token webhook/send) -- each pool
agent's own Slack app is installed to the workspace directly by the admin
(scripts/seed_agents.py), not through any OAuth code in this file.
"""
import base64
import hashlib
import hmac
import logging
import secrets

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from app.config import SLACK_REDIRECT_URI, FRONTEND_URL, SLACK_READER_CLIENT_ID, SLACK_READER_CLIENT_SECRET
from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Manager, SlackReaderInstallation
from app.controlplane.auth import get_current_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/slack", tags=["Slack Auth"])

# User-token scope only -- the manager's own Slack identity, polled for DM/
# channel/group history (see app/integrations/slack.py fetch_since). No bot
# `scope` is requested at all; this app never gets a bot presence.
#
# Bug found live 2026-07-23 (a real DM never showed up in slack_poll's
# results, "fetched: 0" for everything): fetch_since's conversations.list
# call asks for types=im,mpim,private_channel,public_channel, and Slack
# requires the matching `:read` scope for EVERY type included in that
# call, not just `:history` for reading messages once you have the
# channel id -- `mpim:read`/`groups:read`/`channels:read` were missing
# (only `im:read` was ever granted), so conversations.list failed
# outright with `missing_scope` and fetch_since returned [] before it
# ever got to fetching a single message. Any manager who connected before
# this fix needs to disconnect + reconnect (Slack requires re-consent for
# a scope change, an already-issued token can't silently gain scopes).
USER_SCOPES = "im:history,im:read,mpim:history,mpim:read,groups:history,groups:read,channels:history,channels:read"
SLACK_OAUTH_AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
SLACK_OAUTH_ACCESS_URL = "https://slack.com/api/oauth.v2.access"


def _state_secret() -> str:
    return SLACK_READER_CLIENT_SECRET or "dev-insecure-state-secret"


def _sign_state(manager_id: str) -> str:
    nonce = secrets.token_urlsafe(12)
    payload = f"{manager_id}:{nonce}"
    sig = hmac.new(_state_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return base64.urlsafe_b64encode(f"{payload}:{sig}".encode()).decode()


def _verify_state(state: str) -> str | None:
    try:
        decoded = base64.urlsafe_b64decode(state.encode()).decode()
        manager_id, nonce, sig = decoded.split(":", 2)
    except Exception:
        return None
    expected = hmac.new(_state_secret().encode(), f"{manager_id}:{nonce}".encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(expected, sig):
        return None
    return manager_id


def _require_configured():
    if not (SLACK_READER_CLIENT_ID and SLACK_READER_CLIENT_SECRET):
        raise HTTPException(500, "SLACK_READER_CLIENT_ID/SLACK_READER_CLIENT_SECRET must be set -- see SLACK.md")


@router.get("/install")
def slack_install(manager: Manager = Depends(get_current_manager)):
    _require_configured()
    state = _sign_state(manager.id)
    authorize_url = (
        f"{SLACK_OAUTH_AUTHORIZE_URL}?client_id={SLACK_READER_CLIENT_ID}"
        f"&user_scope={USER_SCOPES}"
        f"&redirect_uri={SLACK_REDIRECT_URI}&state={state}"
    )
    logger.debug(f"[slack-auth] redirecting manager={manager.id} to Slack authorize endpoint (reader)")
    return RedirectResponse(authorize_url)


@router.get("/callback")
def slack_callback(code: str = None, state: str = None, error: str = None):
    if error:
        raise HTTPException(400, f"Slack connect failed: {error}")
    if not code or not state:
        raise HTTPException(400, "Missing code/state")

    manager_id = _verify_state(state)
    if not manager_id:
        raise HTTPException(400, "Invalid or tampered OAuth state")

    resp = httpx.post(
        SLACK_OAUTH_ACCESS_URL,
        data={
            "client_id": SLACK_READER_CLIENT_ID,
            "client_secret": SLACK_READER_CLIENT_SECRET,
            "code": code,
            "redirect_uri": SLACK_REDIRECT_URI,
        },
        timeout=15,
    )
    data = resp.json()
    if not data.get("ok"):
        logger.error(f"[slack-auth] oauth.v2.access failed: {data.get('error')}")
        raise HTTPException(400, f"Slack OAuth exchange failed: {data.get('error')}")

    team = data.get("team", {})
    team_id = team.get("id")
    team_name = team.get("name")

    authed_user = data.get("authed_user", {})
    user_token = authed_user.get("access_token")
    user_id = authed_user.get("id")
    if not user_token or not user_id:
        raise HTTPException(400, "Slack OAuth response missing authed_user user-token grant")

    db = ControlPlaneSessionLocal()
    try:
        installation = db.get(SlackReaderInstallation, manager_id)
        if installation:
            installation.team_id = team_id
            installation.team_name = team_name
            installation.user_token = user_token
            installation.user_id = user_id
        else:
            installation = SlackReaderInstallation(
                manager_id=manager_id, team_id=team_id, team_name=team_name,
                user_token=user_token, user_id=user_id,
            )
            db.add(installation)
        db.commit()
        logger.info(f"[slack-auth] manager={manager_id} connected Slack reading for workspace {team_id} ({team_name})")
    finally:
        db.close()

    _sync_manager_slack_handle(manager_id, user_id)

    return RedirectResponse(url=f"{FRONTEND_URL}/connectors?connected=slack&workspace={team_name or team_id}")


def _sync_manager_slack_handle(manager_id: str, slack_user_id: str) -> None:
    """authed_user.id IS the manager's own Slack user id -- without writing
    it onto their TeamMember row, DM-participant resolution
    (SlackConnector._resolve_dm_other_participant) has no way to recognize
    the manager in polled events, and reading silently returns nothing.

    Bug found live 2026-07-23: this used to only UPDATE an existing
    role="manager" TeamMember row, but nothing in the v2 provisioning flow
    (app.tenancy.db.init_manager_db) ever creates one -- only "Harry" gets
    auto-seeded there. So for every manager who connects Slack reading
    without a pre-existing TeamMember(role contains "manager") row (i.e.
    everyone, in practice), this silently no-op'd: get_manager() kept
    returning None, _resolve_dm_other_participant's fast path never
    fired, and every real DM's normalize() returned None
    ("ignored_not_a_message") -- slack_poll reported fetched>0/stored=0
    with no error anywhere. Now creates the row if missing. Deliberately
    NOT moved into init_manager_db's provisioning-time seed (like "Harry"
    is) -- that runs at every dev-login too, including in the test suite's
    `client` fixture, where many tests manually seed their own manager
    TeamMember row and expect to be the only role="manager" row
    get_manager()'s `.first()` can find; scoping this to the real OAuth
    callback path instead leaves every test that doesn't exercise this
    exact route (i.e. that never calls this function) untouched."""
    from app.tenancy.db import get_manager_session
    from app.integrations.base import get_manager

    db = get_manager_session(manager_id)
    try:
        member = get_manager(db)
        if member is None:
            from app.database import TeamMember

            cdb = ControlPlaneSessionLocal()
            try:
                manager = cdb.get(Manager, manager_id)
            finally:
                cdb.close()
            member = TeamMember(
                id=manager_id,
                name=manager.name if manager else manager_id,
                role="manager",
                slack_handle=slack_user_id,
                outlook_email=manager.email if manager else None,
                timezone="Asia/Kolkata",
            )
            db.add(member)
            db.commit()
            logger.info(f"[slack-auth] manager={manager_id} created TeamMember row, slack_handle={slack_user_id}")
        elif member.slack_handle != slack_user_id:
            member.slack_handle = slack_user_id
            db.commit()
            logger.info(f"[slack-auth] manager={manager_id} TeamMember.slack_handle set to {slack_user_id}")
    finally:
        db.close()


@router.post("/disconnect")
def slack_disconnect(manager: Manager = Depends(get_current_manager)):
    """Revokes the user token via Slack's auth.revoke (best-effort) and
    forgets our copy. Has nothing to do with any claimed Agent -- that
    bot's own install is admin-managed and unaffected by a manager turning
    off their own message tracking."""
    db = ControlPlaneSessionLocal()
    try:
        installation = db.get(SlackReaderInstallation, manager.id)
        if installation is None:
            return {"status": "ok"}

        try:
            httpx.post(
                "https://slack.com/api/auth.revoke",
                headers={"Authorization": f"Bearer {installation.user_token}"},
                timeout=10,
            )
        except Exception:
            logger.exception(f"[slack-auth] auth.revoke failed for manager={manager.id}, disconnecting anyway")

        db.delete(installation)
        db.commit()
        logger.info(f"[slack-auth] manager={manager.id} disconnected Slack reading")
    finally:
        db.close()
    return {"status": "ok"}
