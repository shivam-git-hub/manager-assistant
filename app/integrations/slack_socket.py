"""Slack Socket Mode ingress -- the company-laptop alternative to the
Events API webhook (`POST /api/integrations/slack/webhook` in
app.integrations.slack). On a corporate network with no reachable public
webhook URL, the bot instead opens an outbound websocket to Slack
(`apps.connections.open`) and receives events over that connection.
Everything AFTER "an event arrived" is identical to the webhook path --
this module's only job is to get the event dict from the socket to
`app.integrations.slack.handle_agent_event()`, the same routing function
the webhook route calls (manager DM -> Chief of Staff chat loop, known
teammate DM -> followup reply, else -> normal ingestion).

One SocketModeHandler per claimed Agent that has both a bot_token and an
app-level token (`xapp-...`, Socket Mode's `connections:write` grant --
stored on `Agent.slack_app_token`, a DIFFERENT credential from bot_token,
see that column's docstring in app.controlplane.models). For the common
single-agent demo case where an admin hasn't done the manual DB insert for
`slack_app_token` yet, this falls back to the `SLACK_APP_TOKEN` env var
paired with whichever agent's `bot_token` matches `SLACK_BOT_TOKEN` (or, if
neither env var resolves to a specific claimed agent, the most-recently-
claimed one) -- see `_resolve_app_token`.

Not covered by pytest: this talks to a real websocket. Guarded so import
and startup are both safe with slack_bolt absent or no agent configured --
`start_all()` logs clearly why it did nothing rather than staying silent,
since "why isn't Slack working" with no error anywhere was an explicit
complaint this module exists to fix.
"""
import logging
import os
import threading
from typing import Dict, List, Optional

from app.config import AEXP_PROXY_URL

logger = logging.getLogger(__name__)

# One SocketModeHandler (and its owning thread) per claimed agent id,
# so start_all()/stop_all() can be called idempotently (e.g. across a
# hot-reload) without leaking duplicate connections.
_handlers: Dict[str, object] = {}
_threads: Dict[str, threading.Thread] = {}


def _slack_bolt_available() -> bool:
    try:
        import slack_bolt  # noqa: F401
        import slack_sdk  # noqa: F401
        return True
    except ImportError:
        return False


def _resolve_app_token(agent) -> Optional[str]:
    """Agent.slack_app_token is the correct place for this credential
    (per-agent, matches bot_token/slack_signing_secret's own pattern), but
    for a demo/single-agent deployment where nobody has done that manual DB
    insert yet, fall back to the SLACK_APP_TOKEN env var -- but only for
    the agent whose bot_token actually matches SLACK_BOT_TOKEN (or, if that
    env var isn't set either, whichever single claimed agent this is, since
    there's nothing to disambiguate against). This avoids one env var
    silently getting applied to the wrong bot identity in a multi-agent
    deployment."""
    if agent.slack_app_token:
        return agent.slack_app_token
    env_app_token = os.getenv("SLACK_APP_TOKEN")
    env_bot_token = os.getenv("SLACK_BOT_TOKEN")
    if not env_app_token:
        return None
    if env_bot_token and env_bot_token != agent.bot_token:
        return None
    return env_app_token


def _start_one(agent, bot_token: str, app_token: str) -> None:
    from slack_bolt import App
    from slack_bolt.adapter.socket_mode import SocketModeHandler
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError

    from app.integrations.slack import handle_agent_event

    # Matches the verified-working reference pattern: WebClient carries the
    # bot token + proxy; App is handed that client directly rather than a
    # bare token= (App would otherwise construct its own WebClient with no
    # proxy, which can't reach slack.com from inside the corp network).
    client = WebClient(token=bot_token, proxy=AEXP_PROXY_URL)
    app = App(client=client, signing_secret=agent.slack_signing_secret)

    @app.event("message")
    def _on_message(body, logger=logger):  # noqa: ANN001 -- slack_bolt injects these by name
        event = body.get("event") or {}
        try:
            result = handle_agent_event(agent, event)
            logger.debug(f"[slack_socket] agent={agent.id} event routed: {result}")
        except Exception:
            logger.exception(f"[slack_socket] agent={agent.id} failed to handle incoming event")

    @app.event("app_mention")
    def _on_mention(body, logger=logger):  # noqa: ANN001
        # Same routing as a DM -- handle_agent_event's is_im_message check
        # will be False for a channel mention, so this just runs the normal
        # ingestion path for it (still useful: an @-mention in a channel is
        # a real message worth storing).
        event = body.get("event") or {}
        try:
            handle_agent_event(agent, event)
        except Exception:
            logger.exception(f"[slack_socket] agent={agent.id} failed to handle app_mention")

    handler = SocketModeHandler(app=app, app_token=app_token, proxy=AEXP_PROXY_URL)

    def _run():
        try:
            logger.info(f"[slack_socket] agent={agent.id} (manager={agent.manager_id}): connecting via Socket Mode...")
            handler.connect()
            logger.info(f"[slack_socket] agent={agent.id}: connected -- listening for Slack events")
        except SlackApiError as e:
            # The single most likely failure on a fresh setup: an invalid/
            # revoked app-level token, or Socket Mode not enabled on this
            # Slack app. Slack reports this as a normal API error from
            # apps.connections.open (invalid_auth / not_allowed_token_type),
            # not an HTTP-layer exception, so it needs its own log line --
            # this IS the "Slack unauthorized" symptom, surfaced with the
            # actual cause instead of nothing.
            logger.error(
                f"[slack_socket] agent={agent.id}: Socket Mode connection FAILED "
                f"(error={e.response.get('error') if e.response else e}) -- check that "
                f"slack_app_token/SLACK_APP_TOKEN is a valid, unrevoked xapp- token and "
                f"that Socket Mode is enabled on this Slack app's settings page."
            )
        except Exception:
            logger.exception(f"[slack_socket] agent={agent.id}: Socket Mode connection failed unexpectedly")

    thread = threading.Thread(target=_run, name=f"slack-socket-{agent.id}", daemon=True)
    _handlers[agent.id] = handler
    _threads[agent.id] = thread
    thread.start()


def start_all() -> None:
    """Starts one Socket Mode connection per claimed agent that has both a
    bot_token and a resolvable app-level token. Called once from
    app.main's lifespan at boot. Logs (loudly, at INFO/WARNING) exactly why
    it started zero connections when that happens, rather than doing
    nothing silently -- that silence was the original complaint."""
    if not _slack_bolt_available():
        logger.warning(
            "[slack_socket] slack_bolt/slack_sdk not installed -- Socket Mode ingress disabled. "
            "Slack messages will only be captured via the manager's own read-token polling "
            "(app.projectkb.jobs.slack_poll), not via the bot. `pip install slack_bolt slack_sdk` to enable it."
        )
        return

    from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Agent

    db = ControlPlaneSessionLocal()
    try:
        claimed_agents: List = db.query(Agent).filter(
            Agent.manager_id.isnot(None), Agent.bot_token.isnot(None)
        ).all()
    finally:
        db.close()

    if not claimed_agents:
        logger.warning(
            "[slack_socket] no claimed Agent with a bot_token found -- Socket Mode ingress disabled "
            "until a bot is claimed (see /agents / the Agents & Cron admin tab)."
        )
        return

    started = 0
    for agent in claimed_agents:
        if agent.id in _threads and _threads[agent.id].is_alive():
            continue  # already running (e.g. re-entrant lifespan on reload)
        app_token = _resolve_app_token(agent)
        if not app_token:
            logger.warning(
                f"[slack_socket] agent={agent.id} (manager={agent.manager_id}) is claimed but has no "
                f"app-level token (Agent.slack_app_token, or a matching SLACK_APP_TOKEN env var) -- "
                f"Socket Mode ingress skipped for this agent. Messages sent TO this bot's DM "
                f"(e.g. teammate followup replies) will not be received until this is set."
            )
            continue
        _start_one(agent, agent.bot_token, app_token)
        started += 1

    logger.info(f"[slack_socket] start_all: {started} Socket Mode connection(s) starting "
                f"out of {len(claimed_agents)} claimed agent(s)")


def stop_all() -> None:
    for agent_id, handler in list(_handlers.items()):
        try:
            handler.close()
        except Exception:
            logger.exception(f"[slack_socket] agent={agent_id}: error closing Socket Mode connection")
    _handlers.clear()
    _threads.clear()
