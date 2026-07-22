"""Slack workspace "Connect" -- agent-pool-aware OAuth install flow (step 17
piece 2b -- prompts/step_17_agent_pool.md). Unlike Outlook (step 13), this
is CONNECT only, not login: the manager must already be signed in (via
Outlook) AND already have claimed a pool agent (POST /api/agents/claim,
see app/controlplane/agents.py) before installing Slack.

Supersedes the step-14 single-shared-app design: install now uses the
manager's OWN claimed Agent's client_id/client_secret/signing_secret, not
one global app-level SLACK_CLIENT_ID -- each manager's bot is a distinctly
named, separately registered Slack app, so recipients can tell whose bot
they're talking to.

state carries the installing manager's id, HMAC-signed with THAT manager's
claimed agent's client_secret (not a single global secret, since there is
no longer one) so the callback -- which Slack calls directly, no session
cookie guaranteed to still be the same browser context -- knows who to
attribute the resulting installation to, without needing server-side state
storage. The callback decodes manager_id from the (as yet unverified) state
first, looks up which agent that manager claimed, and verifies against
THAT agent's secret.

Reading the manager's own DMs is a separate concern: a *user*-token grant
(user_scope below), polled via app/projectkb/jobs/slack_poll.py rather than
pushed over the webhook -- unlike the bot-token case, conversations.history
with oldest=<ts> is a clean per-user "everything since X" primitive, so
polling doesn't have the fan-out rate-limit problem a shared bot would; see
prompts/step_17_agent_pool.md.
"""
import base64
import hashlib
import hmac
import logging
import secrets
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from app.config import SLACK_REDIRECT_URI, FRONTEND_URL
from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Manager, Agent
from app.controlplane.auth import get_current_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/slack", tags=["Slack Auth"])

BOT_SCOPES = "im:history,im:read,chat:write"
# User-token scope -- the manager's own Slack identity, polled for DM
# history (see app/integrations/slack.py fetch_since). Starts DM-only;
# channels/groups are a later piece (tracked_channels redesign).
USER_SCOPES = "im:history,im:read"
SLACK_OAUTH_AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
SLACK_OAUTH_ACCESS_URL = "https://slack.com/api/oauth.v2.access"


def _get_claimed_agent(db, manager_id: str) -> Agent | None:
    return db.query(Agent).filter(Agent.manager_id == manager_id).first()


def _sign_state(manager_id: str, agent_secret: str) -> str:
    nonce = secrets.token_urlsafe(12)
    payload = f"{manager_id}:{nonce}"
    sig = hmac.new(agent_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return base64.urlsafe_b64encode(f"{payload}:{sig}".encode()).decode()


def _decode_state_manager_id(state: str) -> str | None:
    """Extracts manager_id WITHOUT verifying the signature -- verification
    needs to know which agent's secret to check against, and that's looked
    up BY manager_id, so this has to run first. Never trust the result of
    this alone; _verify_state below does the actual check."""
    try:
        decoded = base64.urlsafe_b64decode(state.encode()).decode()
        manager_id, _nonce, _sig = decoded.split(":", 2)
        return manager_id
    except Exception:
        return None


def _verify_state(state: str, agent_secret: str) -> str | None:
    try:
        decoded = base64.urlsafe_b64decode(state.encode()).decode()
        manager_id, nonce, sig = decoded.split(":", 2)
    except Exception:
        return None
    expected = hmac.new(agent_secret.encode(), f"{manager_id}:{nonce}".encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(expected, sig):
        return None
    return manager_id


@router.get("/install")
def slack_install(manager: Manager = Depends(get_current_manager)):
    db = ControlPlaneSessionLocal()
    try:
        agent = _get_claimed_agent(db, manager.id)
        if agent is None:
            raise HTTPException(404, "Claim an agent first -- POST /api/agents/claim")

        state = _sign_state(manager.id, agent.slack_client_secret)
        authorize_url = (
            f"{SLACK_OAUTH_AUTHORIZE_URL}?client_id={agent.slack_client_id}"
            f"&scope={BOT_SCOPES}&user_scope={USER_SCOPES}"
            f"&redirect_uri={SLACK_REDIRECT_URI}&state={state}"
        )
        logger.debug(f"[slack-auth] redirecting manager={manager.id} (agent={agent.id}) to Slack authorize endpoint")
        return RedirectResponse(authorize_url)
    finally:
        db.close()


@router.get("/callback")
def slack_callback(code: str = None, state: str = None, error: str = None):
    if error:
        raise HTTPException(400, f"Slack install failed: {error}")
    if not code or not state:
        raise HTTPException(400, "Missing code/state")

    manager_id = _decode_state_manager_id(state)
    if not manager_id:
        raise HTTPException(400, "Invalid or tampered OAuth state")

    db = ControlPlaneSessionLocal()
    try:
        agent = _get_claimed_agent(db, manager_id)
        if agent is None:
            raise HTTPException(400, "No agent claimed for this manager")

        if _verify_state(state, agent.slack_client_secret) != manager_id:
            raise HTTPException(400, "Invalid or tampered OAuth state")

        resp = httpx.post(
            SLACK_OAUTH_ACCESS_URL,
            data={
                "client_id": agent.slack_client_id,
                "client_secret": agent.slack_client_secret,
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
        bot_token = data.get("access_token")
        if not team_id or not bot_token:
            raise HTTPException(400, "Slack OAuth response missing team.id or access_token")

        # authed_user carries the user-token grant (user_scope above) -- the
        # manager's own Slack identity, separate from the bot. May be absent
        # if the manager denied the user-scope consent while still approving
        # the bot scope; reading (slack_poll.py) just no-ops until granted.
        authed_user = data.get("authed_user", {})
        user_token = authed_user.get("access_token")
        user_id = authed_user.get("id")

        agent.team_id = team_id
        agent.bot_token = bot_token
        agent.installed_at = datetime.now()
        if user_token:
            agent.user_token = user_token
            agent.user_id = user_id
        db.commit()
        logger.info(f"[slack-auth] agent={agent.id} installed into workspace {team_id} ({team_name}) for manager={manager_id}, user_grant={'yes' if user_token else 'no'}")
    finally:
        db.close()

    if user_id:
        _sync_manager_slack_handle(manager_id, user_id)

    return RedirectResponse(url=f"{FRONTEND_URL}/connectors?connected=slack&workspace={team_name or team_id}")


def _sync_manager_slack_handle(manager_id: str, slack_user_id: str) -> None:
    """authed_user.id IS the manager's own Slack user id -- without writing
    it onto their TeamMember row, DM-participant resolution
    (SlackConnector._resolve_dm_other_participant) has no way to recognize
    the manager in polled/webhook events, and reading silently returns
    nothing. Upserts the one TeamMember with role~manager in this manager's
    own db.sqlite (see app.integrations.base.get_manager)."""
    from app.tenancy.db import get_manager_session
    from app.integrations.base import get_manager

    db = get_manager_session(manager_id)
    try:
        member = get_manager(db)
        if member is not None and member.slack_handle != slack_user_id:
            member.slack_handle = slack_user_id
            db.commit()
            logger.info(f"[slack-auth] manager={manager_id} TeamMember.slack_handle set to {slack_user_id}")
    finally:
        db.close()


@router.post("/disconnect")
def slack_disconnect(manager: Manager = Depends(get_current_manager)):
    """Revokes the bot token via Slack's auth.revoke (best-effort -- proceeds
    with forgetting our copy even if the revoke call fails/is unconfigured)
    and clears the install-derived fields on the manager's claimed Agent.
    Does NOT release the agent claim itself -- disconnecting Slack doesn't
    give up the manager's bot identity, it just tears down the live
    install; reclaiming/reinstalling reuses the same agent."""
    db = ControlPlaneSessionLocal()
    try:
        agent = _get_claimed_agent(db, manager.id)
        if agent is None or agent.bot_token is None:
            return {"status": "ok"}

        try:
            httpx.post(
                "https://slack.com/api/auth.revoke",
                headers={"Authorization": f"Bearer {agent.bot_token}"},
                timeout=10,
            )
        except Exception:
            logger.exception(f"[slack-auth] auth.revoke failed for agent={agent.id}, disconnecting anyway")

        agent.team_id = None
        agent.bot_token = None
        agent.user_token = None
        agent.user_id = None
        agent.installed_at = None
        db.commit()
        logger.info(f"[slack-auth] disconnected agent={agent.id} for manager={manager.id}")
    finally:
        db.close()
    return {"status": "ok"}
