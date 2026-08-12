"""Real Slack connector: Events API webhook (push, already the real payload
shape) + real Web API calls for sending and DM-participant resolution.

Bot identity: a pool of
distinctly-named Slack apps ("agents"), each pre-registered AND
pre-installed to the workspace by the admin out of band (a manual DB
insert into the `agents` table -- NOT any OAuth code in this codebase), stored in the
control-plane DB as an Agent row (see app/controlplane/models.py).
Claiming one (app/controlplane/agents.py) is pure DB bookkeeping -- it
never talks to Slack. Webhook routing keys on the payload's `api_app_id`
(-> Agent.slack_app_id), not `team_id`, since multiple agents (multiple
managers' bots) can share a workspace.

Reading a manager's own messages is a SEPARATE concern, unrelated to the
Agent pool -- one single global reader Slack app, a user-token-only OAuth
grant per manager (stored on the Employee row, granted via
app/controlplane/slack_auth.py), polled by app/projectkb/jobs/slack_poll.py
via fetch_since() below. See SLACK.md for setup.

send() is a plain method, not an HTTP endpoint -- callers are app.outbound
(quiet-hours gated) and, later, agent tools. Only /webhook is exposed, since
that's the one thing that genuinely has to be an HTTP endpoint (Slack calls
it).

Real-network calls (chat.postMessage, conversations.members, signature
verification) are skipped under pytest or when not configured, falling back
to the same DB-only behavior the old mock had -- see is_configured() and the
"pytest" in sys.modules checks below. This keeps the test suite offline
while still doing real work in a live deployment.
"""
import hashlib
import hmac
import json
import logging
import sys
import time
import uuid
from datetime import datetime
from typing import Optional, Dict, Any

import httpx
import pytz
from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database import UnifiedMessage
from app.config import IST, AEXP_PROXY_URL, SLACK_INSECURE_SSL
from app import timeservice
from app.integrations.base import ChannelConnector, NormalizedMessage, SendResult, ingest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/integrations/slack", tags=["Slack Integration"])

SLACK_API_BASE = "https://slack.com/api"


class SlackConnector(ChannelConnector):
    source = "slack"

    def resolve_agent_by_app_id(self, api_app_id: str):
        """Webhook routing: which manager (via which agent) does this
        api_app_id belong to. Payloads always carry api_app_id at the top
        level regardless of which pool app sent them -- see the webhook
        handler below."""
        from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Agent

        db = ControlPlaneSessionLocal()
        try:
            return db.query(Agent).filter(Agent.slack_app_id == api_app_id).first()
        finally:
            db.close()

    def resolve_reader_by_manager(self, manager_id: str):
        """This manager's own Slack reading grant, if any -- NOT the Agent
        pool; a manager can read their own messages whether or not they've
        ever claimed a bot). Returns the Employee row itself (or None) --
        callers read `.slack_user_token`/`.slack_team_id`/`.slack_team_name`
        directly. See app/controlplane/models.py's Employee docstring."""
        from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, get_employee_by_manager_id

        db = ControlPlaneSessionLocal()
        try:
            return get_employee_by_manager_id(db, manager_id)
        finally:
            db.close()

    def _resolve_active_agent(self, manager_id: Optional[str] = None):
        """Which installed Agent's bot token to send as. Prefers the agent
        actually CLAIMED by `manager_id` when given; falls back to
        whichever agent was most recently installed (the old single-tenant
        behavior) when no manager_id is available -- send()/is_configured()
        still don't carry one everywhere (see app.outbound), same
        concession OutlookConnector's _resolve_active_manager_id already
        makes.

        Manager scoping matters here: without it this picks whichever pool
        bot was installed most recently, so an UNCLAIMED bot could win over
        the agent the calling manager actually owns, silently sending as
        the wrong bot identity (and, if that bot lacks the im:write scope
        applied, surfacing as a `missing_scope` error that looked
        unrelated). Callers that DO have a manager_id (open_dm, so far)
        must pass it."""
        from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Agent

        db = ControlPlaneSessionLocal()
        try:
            if manager_id:
                claimed = db.query(Agent).filter(
                    Agent.manager_id == manager_id, Agent.bot_token.isnot(None)
                ).first()
                if claimed:
                    return claimed
            return db.query(Agent).filter(Agent.bot_token.isnot(None)).order_by(Agent.installed_at.desc()).first()
        finally:
            db.close()

    @property
    def bot_token(self) -> Optional[str]:
        agent = self._resolve_active_agent()
        return agent.bot_token if agent else None

    def is_configured(self) -> bool:
        return bool(self._resolve_active_agent())

    def _skip_live_calls(self) -> bool:
        return "pytest" in sys.modules or not self.is_configured()

    def _api_call(self, method: str, params: Optional[dict] = None, json_body: Optional[dict] = None, token: Optional[str] = None) -> dict:
        used_token = token or self.bot_token
        headers = {"Authorization": f"Bearer {used_token}"}
        # Corp-network egress proxy (Amex laptop) -- see app.config's
        # AEXP_PROXY_URL/SLACK_INSECURE_SSL docstrings. Both are no-ops
        # (None / False) off that network.
        proxy = AEXP_PROXY_URL
        verify = not SLACK_INSECURE_SSL
        logger.debug(f"[slack] API call: {method} params={params} json={json_body}")
        if json_body is not None:
            resp = httpx.post(f"{SLACK_API_BASE}/{method}", headers=headers, json=json_body, proxy=proxy, verify=verify, timeout=10)
        else:
            resp = httpx.get(f"{SLACK_API_BASE}/{method}", headers=headers, params=params, proxy=proxy, verify=verify, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        # Slack's API returns HTTP 200 even on failure (`ok: false` +
        # an `error` code) -- raise_for_status() above never catches an
        # auth/scope problem, which is exactly what a silent "401
        # unauthorized" symptom turns out to be: the request succeeded at
        # the HTTP layer, Slack rejected it at the application layer. Log
        # it loudly (not DEBUG) so it's visible without needing to enable
        # debug logging, and call out the token's last few chars so it's
        # possible to tell WHICH agent/reader token is bad without ever
        # logging the full secret.
        if not data.get("ok"):
            token_hint = f"...{used_token[-6:]}" if used_token else "(none)"
            logger.error(
                f"[slack] API call FAILED: {method} error={data.get('error')!r} "
                f"needed={data.get('needed')} provided={data.get('provided')} token={token_hint}"
            )
        else:
            logger.debug(f"[slack] API response: {method} ok=True")
        return data

    def open_dm(self, slack_user_id: str, manager_id: Optional[str] = None) -> Optional[str]:
        """Resolves a Slack user id to a DM channel id via conversations.open
        (idempotent on Slack's side -- reopening an existing DM just returns
        its channel id). Used by the personal agent's send_message tool
        to message a teammate/manager by their Employee.slack_id
        rather than a pre-known channel id. `manager_id`, when given,
        resolves the calling manager's own claimed bot rather than
        whichever pool bot happened to be installed most recently -- see
        _resolve_active_agent's docstring for the bug this fixes."""
        if not slack_user_id:
            return None
        agent = self._resolve_active_agent(manager_id)
        token = agent.bot_token if agent else None
        if "pytest" in sys.modules or not token:
            logger.debug(f"[slack] open_dm: skipping live call for user={slack_user_id} (no bot token)")
            return f"DM_{slack_user_id}"
        try:
            resp = self._api_call("conversations.open", json_body={"users": slack_user_id}, token=token)
            if not resp.get("ok"):
                logger.error(f"[slack] conversations.open failed: {resp.get('error')}")
                return None
            return resp.get("channel", {}).get("id")
        except Exception:
            logger.exception(f"[slack] open_dm failed for user={slack_user_id}")
            return None

    def verify_signature(self, body: bytes, timestamp: str, signature: str, signing_secret: str) -> bool:
        """Slack's HMAC-SHA256 webhook signature scheme, checked against
        THIS agent's own signing_secret (not a single global one -- each
        pool app has its own). Skipped (returns True) under pytest -- see
        module docstring."""
        if "pytest" in sys.modules:
            return True
        if not timestamp or not signature:
            logger.warning("[slack] webhook request missing signature headers")
            return False
        try:
            if abs(time.time() - float(timestamp)) > 60 * 5:
                logger.warning("[slack] webhook request timestamp outside replay window")
                return False
        except ValueError:
            return False
        basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
        computed = "v0=" + hmac.new(signing_secret.encode(), basestring.encode(), hashlib.sha256).hexdigest()
        valid = hmac.compare_digest(computed, signature)
        if not valid:
            logger.warning("[slack] webhook signature mismatch")
        return valid

    def _resolve_member(self, cdb, slack_id: str):
        """Resolves a Slack user id straight against the control-plane
        Employee directory -- connectors don't need the per-manager
        TeamMember roster at all (that table is now purely a manually
        maintained team-view, synced from Employee on project-member-add,
        see app.agent.tools/app.api.projects_registry). `cdb` is a
        control-plane session opened once by the caller (normalize()) and
        reused across every resolution call in that pass, rather than each
        call opening its own."""
        from app.controlplane.models import get_employee_by_slack_id

        return get_employee_by_slack_id(cdb, slack_id)

    def _resolve_dm_other_participant(self, db: Session, cdb, channel_id: str, known_id: str, manager_id: Optional[str] = None) -> Optional[str]:
        """Given one known participant of a DM channel, find the other one.

        normalize() doesn't decide manager-involvement (ingest() does that
        generically) -- but it CAN cheaply short-circuit the common case
        without an API call: if the manager is configured and isn't the
        sender, then in a DM ("im") channel the only other possible
        participant is the manager (our token/bot only sees conversations
        it has access to). A real conversations.members call is only
        needed for the less common case of resolving who a
        manager-initiated DM was with.

        The manager-outbound case (known_id IS the manager's own slack_id --
        they DMed a teammate) must NOT fall through to the live-call branch
        below using self.bot_token (a pool agent's bot token): the bot isn't
        a member of a DM between two humans, so conversations.members comes
        back empty/errored and every message a manager sends to someone else
        silently vanished (normalize() -> None -> ingest()
        "ignored_not_a_message", no error surfaced anywhere). Fixed by
        resolving this manager's own reader user_token (the same token
        fetch_since already polls with -- it's a member of the manager's own
        DMs by construction) and using THAT for the live call instead of the
        bot token.

        manager_id is resolved against the control-plane Employee row
        directly (Employee.slack_id is always kept current -- written at
        Slack-connect time, app.controlplane.slack_auth) rather than the old
        TeamMember role="manager" lookup."""
        from app.controlplane.models import get_employee_by_manager_id

        manager_employee = get_employee_by_manager_id(cdb, manager_id) if manager_id else None

        if manager_employee and manager_employee.slack_id and known_id != manager_employee.slack_id:
            return manager_employee.slack_id

        token = None
        if manager_id:
            reader = self.resolve_reader_by_manager(manager_id)
            token = reader.slack_user_token if reader else None

        if "pytest" in sys.modules or not token:
            logger.debug(f"[slack] skipping live conversations.members lookup for {channel_id} (no reader token)")
            return None
        try:
            resp = self._api_call("conversations.members", {"channel": channel_id}, token=token)
            members = resp.get("members", [])
            others = [m for m in members if m != known_id]
            return others[0] if others else None
        except Exception:
            logger.exception(f"[slack] failed to resolve DM members for channel {channel_id}")
            return None

    def _resolve_channel_session_thread_key(self, db: Session, channel_id: str, timestamp_ist: datetime, ts_val: str) -> str:
        """A channel/group top-level message (no thread_ts -- Slack gives no
        conversation signal for these at all) is bucketed into a rolling
        "session": if the channel's most recently STORED message (any
        status, any conversation_type variant of it -- just "the last thing
        that happened in this channel") landed within
        SLACK_CHANNEL_SESSION_GAP_MINUTES, this message joins that same
        thread_key (a live back-and-forth); otherwise it starts a fresh
        session rooted at its own ts (an unrelated topic, more likely
        after a real gap). A real Slack reply-in-thread (thread_ts present)
        never goes through this path -- it always groups under its parent
        via thread_ts regardless of elapsed time, see normalize()."""
        from app.config import SLACK_CHANNEL_SESSION_GAP_MINUTES
        from datetime import timedelta

        last = (
            db.query(UnifiedMessage)
            .filter(UnifiedMessage.source == "slack", UnifiedMessage.channel_raw_id == channel_id)
            .order_by(UnifiedMessage.timestamp.desc())
            .first()
        )
        if last is not None and last.thread_id and (timestamp_ist - last.timestamp) <= timedelta(minutes=SLACK_CHANNEL_SESSION_GAP_MINUTES):
            return last.thread_id
        return f"{channel_id}:{ts_val}"

    # Slack's channel_type -> our conversation_type. "im"
    # is a 1:1 DM (one counterpart, gated on tracked-contacts); everything
    # else is N-participant and gated on tracked-channels instead -- see
    # ingest()'s conversation_type branch in app/integrations/base.py.
    _CHANNEL_TYPE_MAP = {"im": "dm", "channel": "channel", "group": "group", "mpim": "group"}

    def normalize(self, db: Session, raw: Dict[str, Any], manager_id: Optional[str] = None) -> Optional[NormalizedMessage]:
        """`raw` is a Slack Events API `event` object (or the equivalent
        shape built from conversations.history -- see
        _history_message_to_event). normalize() otherwise doesn't know who
        the manager is, it just describes sender/receiver objectively;
        ingest() decides manager-involvement and tracked-list membership
        generically. `manager_id` is threaded through only for the DM
        counterpart resolution's live-API fallback (see
        _resolve_dm_other_participant) -- it needs to know whose reader
        token to use."""
        if raw.get("type") != "message" or "user" not in raw or raw.get("subtype") == "bot_message":
            return None
        user_id = raw["user"]
        if user_id == "USLACKBOT":
            return None

        conversation_type = self._CHANNEL_TYPE_MAP.get(raw.get("channel_type"))
        if conversation_type is None:
            logger.debug(f"[slack] normalize: unrecognized channel_type={raw.get('channel_type')}, skipping")
            return None

        channel_id = raw.get("channel", "unknown_channel")

        # One control-plane session for every Employee lookup this call
        # needs (sender, receiver, DM-counterpart manager check) -- avoids
        # opening a separate session per lookup.
        from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal

        cdb = ControlPlaneSessionLocal()
        try:
            if conversation_type == "dm":
                receiver_id = self._resolve_dm_other_participant(db, cdb, channel_id, user_id, manager_id)
                if receiver_id is None:
                    logger.debug(f"[slack] normalize: could not resolve DM counterpart for channel {channel_id}")
                    return None
                receiver_member = self._resolve_member(cdb, receiver_id)
                receiver_name = receiver_member.name if receiver_member else None
            else:
                # Channel/group: no single "the receiver" -- ingest() gates
                # these on the channel's own tracked-list membership, not a
                # counterpart identity, so there's nothing to resolve here.
                receiver_id = channel_id
                receiver_name = None

            sender_member = self._resolve_member(cdb, user_id)
        finally:
            cdb.close()

        ts_val = raw.get("ts")
        if not ts_val:
            msg_id = raw.get("client_msg_id") or f"slack_missing_ts_{uuid.uuid4()}"
        else:
            msg_id = raw.get("client_msg_id") or f"slack_{ts_val}"

        ts_float = float(raw.get("ts", timeservice.now_epoch()))
        timestamp_utc = datetime.fromtimestamp(ts_float, tz=pytz.UTC)
        timestamp_ist = timestamp_utc.astimezone(IST).replace(tzinfo=None)

        if conversation_type == "dm":
            # A DM channel IS one conversation -- group every message in it
            # under one thread_key regardless of Slack's own thread_ts
            # (which is only set for explicit "reply in thread" messages;
            # a normal flat back-and-forth never sets it, so keying on
            # thread_ts/ts here would have split a single DM into one
            # separate one-message "thread" per message, one LLM call each).
            thread_key = channel_id
        elif not ts_val:
            thread_key = None
        elif raw.get("thread_ts"):
            # Explicit "reply in thread" -- always groups under the parent,
            # regardless of elapsed time.
            thread_key = f"{channel_id}:{raw['thread_ts']}"
        else:
            # Top-level channel/group message: Slack gives no conversation
            # signal at all for these, so bucket by time-proximity to the
            # channel's own last message instead (see
            # _resolve_channel_session_thread_key) -- related messages land
            # in the same LLM call, an unrelated one after a real gap starts
            # a fresh thread.
            thread_key = self._resolve_channel_session_thread_key(db, channel_id, timestamp_ist, ts_val)

        return NormalizedMessage(
            platform_msg_id=msg_id,
            sender_id=user_id,
            sender_name=sender_member.name if sender_member else None,
            receiver_id=receiver_id,
            receiver_name=receiver_name,
            channel=channel_id,
            subject=None,
            content=raw.get("text", ""),
            timestamp=timestamp_ist,
            raw_metadata=json.dumps(raw),
            conversation_type=conversation_type,
            thread_key=thread_key,
        )

    @staticmethod
    def _history_message_to_event(msg: Dict[str, Any], channel_id: str, channel_type: str) -> Optional[Dict[str, Any]]:
        """Pure transform: one conversations.history result -> the same
        Events-API `message` event shape the webhook delivers, so both
        paths go through the identical normalize()/ingest() pipeline. Kept
        as a standalone, synchronously-testable function (no API calls, no
        DB) since this is the most bug-prone part of polling -- the API
        orchestration around it (fetch_since) is live-only and can't be
        exercised under pytest, but this shaping logic can and must be."""
        if msg.get("type") != "message" or "user" not in msg or msg.get("subtype"):
            return None
        event = {
            "type": "message",
            "user": msg["user"],
            "channel": channel_id,
            "channel_type": channel_type,
            "text": msg.get("text", ""),
            "ts": msg.get("ts"),
            "client_msg_id": msg.get("client_msg_id"),
        }
        # Threaded replies carry the parent's ts -- normalize() folds it
        # into thread_key, so it must survive this reshaping.
        if msg.get("thread_ts"):
            event["thread_ts"] = msg["thread_ts"]
        return event

    def fetch_since(self, since: datetime, manager_id: str) -> list:
        """User-token polling for a manager's own DMs --
        the read-path analogue of OutlookConnector.fetch_since. Unlike the
        bot-token webhook, conversations.history with oldest=<ts> is a
        genuine per-user "everything since X" primitive, so this is safe to
        poll per manager without a shared-bot rate-limit problem. Returns
        raw dicts already shaped like Events API `message` events so they
        can go straight through the same normalize()/ingest() as webhook
        traffic -- see app/projectkb/jobs/slack_poll.py."""
        if "pytest" in sys.modules:
            return []

        reader = self.resolve_reader_by_manager(manager_id)
        if reader is None or not reader.slack_user_token:
            return []

        since_ts = f"{since.replace(tzinfo=pytz.UTC).timestamp():.6f}" if since.tzinfo is None else f"{since.timestamp():.6f}"

        raw_messages = []
        try:
            # DMs AND every group/channel the user is in (track
            # everything; the blocklist decides what not to
            # PROCESS, not what to fetch). Requires the broader user scopes
            # (groups:history, channels:history, mpim:history) on the
            # reader app (app/controlplane/slack_auth.py's USER_SCOPES) --
            # conversations the token can't read just error per-channel and
            # are skipped, so partial grants degrade gracefully.
            convos = self._api_call(
                "conversations.list",
                {"types": "im,mpim,private_channel,public_channel", "limit": 200},
                token=reader.slack_user_token,
            )
            if not convos.get("ok"):
                logger.warning(f"[slack] fetch_since: conversations.list failed for manager={manager_id}: {convos.get('error')}")
                return []
            for channel in convos.get("channels", []):
                channel_id = channel.get("id")
                channel_type = self._conversation_object_type(channel)
                # Public channels the user merely CAN see (not a member of)
                # would fail history anyway; skip explicit non-membership.
                if channel_type == "channel" and channel.get("is_member") is False:
                    continue
                try:
                    history = self._api_call(
                        "conversations.history",
                        {"channel": channel_id, "oldest": since_ts, "limit": 200},
                        token=reader.slack_user_token,
                    )
                except Exception:
                    logger.exception(f"[slack] fetch_since: conversations.history failed for channel={channel_id}")
                    continue
                if not history.get("ok"):
                    logger.warning(f"[slack] fetch_since: conversations.history error for channel={channel_id}: {history.get('error')}")
                    continue
                for msg in history.get("messages", []):
                    event = self._history_message_to_event(msg, channel_id, channel_type)
                    if event is not None:
                        raw_messages.append(event)
        except Exception:
            logger.exception(f"[slack] fetch_since: polling failed for manager={manager_id}")

        return raw_messages

    @staticmethod
    def _conversation_object_type(channel: dict) -> str:
        """conversations.list object flags -> the Events-API channel_type
        string normalize()'s _CHANNEL_TYPE_MAP expects."""
        if channel.get("is_im"):
            return "im"
        if channel.get("is_mpim"):
            return "mpim"
        if channel.get("is_group") or channel.get("is_private"):
            return "group"
        return "channel"

    def send(self, db: Session, to: str, content: str, subject: Optional[str] = None) -> SendResult:
        if not to or not content:
            return SendResult(ok=False, error="invalid_arguments")

        ts_epoch = timeservice.now_epoch()
        platform_msg_id = f"slack_out_{ts_epoch}"
        existing = db.scalars(select(UnifiedMessage).where(UnifiedMessage.platform_msg_id == platform_msg_id)).first()
        if existing:
            platform_msg_id = f"slack_out_{ts_epoch}_{uuid.uuid4().hex[:8]}"

        new_msg = UnifiedMessage(
            platform_msg_id=platform_msg_id,
            source="slack",
            direction="outbound",
            sender_raw_id="U_HARRY",
            sender_mapped_name="Harry",
            receiver_raw_id=to,
            channel_raw_id=to,
            subject=None,
            content=content,
            timestamp=timeservice.now_ist(),
            created_at=timeservice.now_ist(),
            is_processed=False,
            raw_metadata=json.dumps({"channel": to, "text": content}),
        )
        db.add(new_msg)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            platform_msg_id = f"slack_out_{ts_epoch}_{uuid.uuid4().hex[:8]}"
            new_msg.platform_msg_id = platform_msg_id
            db.add(new_msg)
            db.commit()

        if self._skip_live_calls():
            logger.debug(f"[slack] send: skipping live call (configured={self.is_configured()}), recorded {platform_msg_id}")
            return SendResult(ok=True, platform_msg_id=platform_msg_id)

        try:
            resp = self._api_call("chat.postMessage", json_body={"channel": to, "text": content})
            ok = bool(resp.get("ok"))
            if ok:
                # Explicit, INFO-level confirmation that the bot actually
                # delivered a message -- requested specifically so a
                # followup/report send to a teammate who isn't otherwise
                # connected to this deployment is verifiable in the logs
                # ("message sent to Slack via bot", not just "we tried").
                logger.info(f"[slack] send: delivered to {to} (platform_msg_id={platform_msg_id})")
            else:
                logger.error(f"[slack] send: chat.postMessage FAILED to {to}: {resp.get('error')}")
            return SendResult(ok=ok, platform_msg_id=platform_msg_id, error=resp.get("error"))
        except Exception as e:
            logger.exception(f"[slack] send: chat.postMessage raised for {to}")
            return SendResult(ok=False, platform_msg_id=platform_msg_id, error=str(e))


connector = SlackConnector()


@router.post("/webhook")
async def slack_webhook(request: Request):
    """Slack calls this with no session cookie -- unlike every other
    manager-scoped route, the manager here is resolved from the payload's
    api_app_id (via Agent.slack_app_id -- multiple agents/managers can now
    share a team_id, so api_app_id, not team_id, is the routing key), not
    Depends(get_current_manager)/get_manager_db. api_app_id has to be read
    from the body BEFORE signature verification (verification needs to know
    WHICH agent's signing_secret to check against) -- safe as long as
    nothing stateful happens before that check, which is the case here."""
    body_bytes = await request.body()
    body_str = body_bytes.decode("utf-8")

    if not body_str:
        return {"status": "error", "message": "empty body"}

    try:
        payload = json.loads(body_str)
    except json.JSONDecodeError:
        return {"status": "error", "message": "invalid json"}

    if payload.get("type") == "url_verification":
        return PlainTextResponse(payload.get("challenge", ""))

    api_app_id = payload.get("api_app_id")
    if not api_app_id:
        logger.warning("[slack] webhook: payload has no api_app_id, cannot route to an agent, ignoring")
        return {"status": "ignored", "detail": "missing api_app_id"}

    agent = connector.resolve_agent_by_app_id(api_app_id)
    if agent is None:
        logger.warning(f"[slack] webhook: no Agent found for api_app_id={api_app_id}, ignoring")
        return {"status": "ignored", "detail": "unknown agent"}

    if not connector.verify_signature(
        body_bytes,
        request.headers.get("X-Slack-Request-Timestamp", ""),
        request.headers.get("X-Slack-Signature", ""),
        agent.slack_signing_secret,
    ):
        return {"status": "error", "message": "invalid signature"}

    if agent.manager_id is None:
        # Unclaimed pool bot -- inert by design (see agents_pool.json's
        # docstring / SLACK.md part 2): a message to it does nothing,
        # rather than crashing on manager_dir(None) further down.
        logger.debug(f"[slack] webhook: agent={agent.id} is unclaimed, ignoring")
        return {"status": "ignored", "detail": "unclaimed agent"}

    logger.debug(f"[slack] webhook: api_app_id={api_app_id} routed to agent={agent.id} manager={agent.manager_id}")

    event = payload.get("event")
    if not event:
        return {"status": "ok", "detail": "no event in payload"}

    return handle_agent_event(agent, event)


def handle_agent_event(agent, event: Dict[str, Any]) -> Dict[str, Any]:
    """Shared routing for one Slack event already resolved to a claimed
    Agent -- the same logic regardless of whether the event arrived via the
    Events API webhook above or the Socket Mode ingress
    (app.integrations.slack_socket, the company-laptop path where no public
    webhook URL is reachable). Three cases: a DM from the manager runs the
    Chief of Staff chat loop; a DM from a known team member is routed as a
    reply to their active FollowupAgent; anything else goes through the
    normal ingestion pipeline (normalize -> dedup -> store)."""
    from app.tenancy.db import get_manager_session
    from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, get_employee_by_manager_id, get_employee_by_slack_id

    db = get_manager_session(agent.manager_id)
    try:
        # Determine if this is a 1:1 direct message to the agent from a human
        is_im_message = (
            event.get("type") == "message"
            and event.get("channel_type") == "im"
            and not event.get("bot_id")
            and not event.get("subtype")
        )

        if is_im_message and "pytest" not in sys.modules:
            # Resolve manager and sender Slack IDs to route correctly
            cp_db = ControlPlaneSessionLocal()
            try:
                manager_emp = get_employee_by_manager_id(cp_db, agent.manager_id)
                manager_slack_id = manager_emp.slack_id if manager_emp else None

                sender_slack_id = event.get("user")

                if manager_slack_id and sender_slack_id == manager_slack_id:
                    # Case A: Manager DMed the COS Agent. Run COS Agent chat loop.
                    logger.info("[slack] DM from %s: manager, routing to Chief of Staff chat loop", sender_slack_id)
                    from app.agent.cos_agent import run_cos_agent
                    run_cos_agent(db, agent.manager_id, event.get("text", ""), channel="slack")
                    return {"status": "ok", "detail": "manager_cos_chat_handled"}
                else:
                    # Case B: Other team member DMed our bot. This is a reply to an active FollowupAgent.
                    sender_emp = get_employee_by_slack_id(cp_db, sender_slack_id)
                    if sender_emp:
                        logger.info(
                            "[slack] DM received from team member %s (slack_id=%s) -- routing as followup reply",
                            sender_emp.name, sender_slack_id,
                        )
                        from app.agent.cos_agent import handle_team_member_reply
                        handle_team_member_reply(db, agent.manager_id, sender_emp.id, event.get("text", ""), source_channel="slack")
                        return {"status": "ok", "detail": "team_member_followup_handled"}
                    else:
                        logger.warning(f"[slack] Received DM from unrecognized Slack ID {sender_slack_id}, ignoring.")
                        return {"status": "ignored", "detail": "unrecognized_sender"}
            finally:
                cp_db.close()

        # Case C: Not a direct message to our bot. Run normal ingestion flow.
        msg, ingest_status = ingest(connector, event, db, agent.manager_id)
        message_id = msg.platform_msg_id if ingest_status == "ok" else None
        sender_mapped = msg.sender_mapped_name if ingest_status == "ok" else None
    finally:
        db.close()

    if ingest_status == "ok":
        logger.info(f"[slack] DM/message polled and stored: message_id={message_id} sender={sender_mapped}")
    else:
        logger.debug(f"[slack] webhook ingest result: {ingest_status}")

    if ingest_status == "ignored_duplicate":
        return {"status": "ignored", "detail": "duplicate message"}
    if ingest_status != "ok":
        return {"status": "ignored", "detail": ingest_status}

    return {
        "status": "ok",
        "message_id": message_id,
        "sender_mapped": sender_mapped,
    }
