"""Real Outlook connector: Microsoft Graph API. Unlike Slack, Graph has no
simple arbitrary-webhook push -- real arrival is via polling (fetch_since),
using either app-only (client-credentials) or delegated (auth-code-redirect,
per-manager cached token) auth, chosen by OUTLOOK_AUTH_MODE. See OUTLOOK.md
for setup.

Delegated login itself (the "Sign in with Microsoft" redirect flow) lives
in app/controlplane/outlook_auth.py, not here -- it doubles as manager
login + Outlook-connect, so it belongs with the rest of auth, not the
connector. This module only knows how to *use* an already-connected
manager's token, not how to establish one (device-code auth and its
/device-login endpoints were removed in step 13, replaced by that flow).

send() is a plain method, not an HTTP endpoint -- callers are app.outbound
(quiet-hours gated) and, later, agent tools.

/mock-ingest is kept (still receives a Graph-message-shaped payload
directly) for tests and manual injection -- it routes through the same
shared ingest() as real polling does, so both paths exercise identical
normalization/gating logic. /poll is a manual trigger for real polling;
the actual scheduled polling job lives in app/projectkb/jobs/outlook_poll.py.

Real-network calls (token acquisition, fetch_since, sendMail) are skipped
under pytest or when not configured -- see is_configured() and the
"pytest" in sys.modules checks below, same convention as slack.py.
"""
import json
import logging
import os
import re
import sys
import uuid
from datetime import datetime, timedelta
from html.parser import HTMLParser
from typing import Optional, List, Dict, Any

import httpx
from fastapi import APIRouter, Depends, status, Response
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel

from app.database import UnifiedMessage, TeamMember
from app.tenancy.db import get_manager_db
from app.controlplane.models import Manager
from app.controlplane.auth import get_current_manager
from app.config import IST
from app import timeservice
from app.integrations.base import ChannelConnector, NormalizedMessage, SendResult, ingest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/integrations/outlook", tags=["Outlook Integration"])

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def _resolve_active_manager_id() -> Optional[str]:
    """Single-tenant convenience: use whichever manager most recently
    connected Outlook. Real manager_id-scoped connector plumbing (each
    caller passing its own manager_id instead of the connector guessing)
    lands in step 15 alongside the rest of the per-manager data split."""
    from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, OutlookInstallation

    db = ControlPlaneSessionLocal()
    try:
        row = db.query(OutlookInstallation).order_by(OutlookInstallation.updated_at.desc()).first()
        return row.manager_id if row else None
    finally:
        db.close()


def load_token_cache_for_manager(manager_id: str):
    """DB-backed replacement for the old single-file
    data/outlook_token_cache.json -- one cache per manager, keyed by
    OutlookInstallation.manager_id."""
    import msal
    from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, OutlookInstallation

    cache = msal.SerializableTokenCache()
    db = ControlPlaneSessionLocal()
    try:
        row = db.get(OutlookInstallation, manager_id)
        if row and row.token_cache_json:
            cache.deserialize(row.token_cache_json)
    finally:
        db.close()
    return cache


def save_token_cache_for_manager(manager_id: str, cache) -> None:
    if not cache.has_state_changed:
        return
    from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, OutlookInstallation

    db = ControlPlaneSessionLocal()
    try:
        row = db.get(OutlookInstallation, manager_id)
        if row:
            row.token_cache_json = cache.serialize()
            db.commit()
            logger.debug(f"[outlook] token cache updated for manager={manager_id}")
    finally:
        db.close()


# --- HTML cleaning (unchanged from before) ----------------------------------

class HTMLToMarkdown(HTMLParser):
    def __init__(self):
        super().__init__()
        self.reset()
        self.fed = []
        self.skip_tags = {"style", "script", "head"}
        self.active_skips = set()

    def handle_starttag(self, tag, attrs):
        lower_tag = tag.lower()
        if lower_tag in self.skip_tags:
            self.active_skips.add(lower_tag)
            return

        if self.active_skips:
            return

        if tag in ["p", "div", "tr"]:
            self.fed.append("\n")
        elif tag == "br":
            self.fed.append("\n")
        elif tag in ["h1", "h2", "h3", "h4"]:
            self.fed.append(f"\n\n{'#' * int(tag[1])} ")
        elif tag == "li":
            self.fed.append("\n* ")
        elif tag == "td":
            self.fed.append(" | ")

    def handle_endtag(self, tag):
        lower_tag = tag.lower()
        if lower_tag in self.skip_tags:
            self.active_skips.discard(lower_tag)
            return

        if self.active_skips:
            return

        if tag in ["p", "div", "tr"]:
            self.fed.append("\n")
        elif tag in ["h1", "h2", "h3", "h4"]:
            self.fed.append("\n")

    def handle_data(self, data):
        if self.active_skips:
            return
        self.fed.append(data)

    def get_text(self) -> str:
        raw_text = "".join(self.fed)
        raw_text = re.sub(r'[ \t]+', ' ', raw_text)
        raw_text = re.sub(r'\n{3,}', '\n\n', raw_text)
        return raw_text.strip()


def clean_html(html_content: str) -> str:
    if not html_content:
        return ""
    if "<" not in html_content or ">" not in html_content:
        return html_content
    parser = HTMLToMarkdown()
    parser.feed(html_content)
    return parser.get_text()


# --- Graph API auth ----------------------------------------------------------

class OutlookConnector(ChannelConnector):
    source = "outlook"

    def __init__(self):
        self._msal_app = None  # app-only mode only: one instance reused across
        # calls so MSAL's own in-memory token cache avoids re-authenticating on
        # every API call. Delegated mode has no equivalent instance cache --
        # its token cache is per-manager and DB-backed (see _get_msal_app).

    @property
    def auth_mode(self) -> str:
        return os.getenv("OUTLOOK_AUTH_MODE", "")  # "app" | "delegated"

    @property
    def client_id(self) -> Optional[str]:
        return os.getenv("MS_GRAPH_CLIENT_ID")

    @property
    def client_secret(self) -> Optional[str]:
        return os.getenv("MS_GRAPH_CLIENT_SECRET")

    @property
    def tenant_id(self) -> Optional[str]:
        return os.getenv("MS_GRAPH_TENANT_ID")

    @property
    def mailbox(self) -> Optional[str]:
        return os.getenv("OUTLOOK_MAILBOX")

    def is_configured(self, manager_id: Optional[str] = None) -> bool:
        if self.auth_mode == "app":
            return bool(self.client_id and self.client_secret and self.tenant_id and self.mailbox)
        if self.auth_mode == "delegated":
            # Delegated mode is now confidential-client throughout (the
            # redirect flow needs the secret to exchange the auth code), and
            # "configured" means the given manager (or, if none given, at
            # least one manager) has actually connected.
            return bool(
                self.client_id and self.client_secret and self.tenant_id
                and (manager_id or _resolve_active_manager_id())
            )
        return False

    def _skip_live_calls(self, manager_id: Optional[str] = None) -> bool:
        return "pytest" in sys.modules or not self.is_configured(manager_id)

    def _get_msal_app(self, manager_id: Optional[str] = None):
        """App-only mode: one instance reused for the connector's lifetime.
        Delegated mode: rebuilt each call from the given manager's (or, if
        none given, whichever manager most recently connected --
        _resolve_active_manager_id, the single-tenant convenience from step
        13, still used by callers that don't yet know a specific manager_id
        like the manual /poll and /mock-ingest routes) DB-persisted cache --
        there's no single "the" cache to hold on an instance since it's
        per-manager, so the instance-reuse optimization that matters for
        app-only mode doesn't apply here."""
        import msal

        authority = f"https://login.microsoftonline.com/{self.tenant_id}"

        if self.auth_mode == "app":
            if self._msal_app is not None:
                return self._msal_app
            self._msal_app = msal.ConfidentialClientApplication(
                self.client_id, authority=authority, client_credential=self.client_secret
            )
            return self._msal_app

        # delegated
        resolved_manager_id = manager_id or _resolve_active_manager_id()
        if not resolved_manager_id:
            return None
        cache = load_token_cache_for_manager(resolved_manager_id)
        app = msal.ConfidentialClientApplication(
            self.client_id, authority=authority, client_credential=self.client_secret, token_cache=cache
        )
        app._harry_manager_id = resolved_manager_id
        app._harry_cache = cache
        return app

    def send_allowed(self, manager_id: Optional[str] = None) -> bool:
        """Is Mail.Send in this manager's granted_scopes? The OUR-SIDE send
        gate (step 20 follow-up): /auth/outlook/revoke-send removes the
        scope from granted_scopes, and this check is what makes that revoke
        real -- Microsoft's consent may still exist, but Pulse won't use it."""
        if self.auth_mode == "app":
            return True  # app-mode permissions are admin-consented app-wide
        resolved = manager_id or _resolve_active_manager_id()
        if not resolved:
            return False
        from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, OutlookInstallation

        db = ControlPlaneSessionLocal()
        try:
            row = db.get(OutlookInstallation, resolved)
            return bool(row and "Mail.Send" in (row.granted_scopes or "").split(","))
        finally:
            db.close()

    def _acquire_token(self, manager_id: Optional[str] = None, scopes: Optional[list] = None) -> Optional[str]:
        """`scopes` defaults to read-only. Callers that need send pass
        ["Mail.Send"] explicitly -- silently requesting Mail.Send for a
        user who never granted it can fail the whole silent acquisition
        (consent_required), which would break READING for read-only users."""
        app = self._get_msal_app(manager_id)
        if app is None:
            logger.error("[outlook] no connected manager found for delegated auth -- visit /auth/outlook/login")
            return None

        if self.auth_mode == "app":
            result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
        else:  # delegated
            accounts = app.get_accounts()
            if not accounts:
                logger.error("[outlook] no cached delegated login found -- visit /auth/outlook/login to connect.")
                return None
            result = app.acquire_token_silent(scopes or ["Mail.Read"], account=accounts[0])
            save_token_cache_for_manager(app._harry_manager_id, app._harry_cache)

        if not result or "access_token" not in result:
            logger.error(f"[outlook] failed to acquire Graph token: {result.get('error_description') if result else 'no result'}")
            return None
        logger.debug("[outlook] acquired Graph access token")
        return result["access_token"]

    def _graph_base_path(self) -> str:
        return f"/users/{self.mailbox}" if self.auth_mode == "app" else "/me"

    def _api_call(
        self, method: str, path: str, params: Optional[dict] = None,
        json_body: Optional[dict] = None, manager_id: Optional[str] = None,
        scopes: Optional[list] = None,
    ) -> httpx.Response:
        token = self._acquire_token(manager_id, scopes=scopes)
        if not token:
            raise RuntimeError("Could not acquire Graph API token")
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{GRAPH_BASE}{path}"
        logger.debug(f"[outlook] API call: {method} {path} params={params}")
        if method == "GET":
            return httpx.get(url, headers=headers, params=params, timeout=15)
        return httpx.post(url, headers=headers, json=json_body, timeout=15)

    # --- Inbound -------------------------------------------------------------

    def _resolve_member(self, db: Session, email: str) -> Optional[TeamMember]:
        return db.scalars(select(TeamMember).where(
            (TeamMember.outlook_email == email) | (TeamMember.id == email)
        )).first()

    def normalize(self, db: Session, raw: Dict[str, Any]) -> Optional[NormalizedMessage]:
        """`raw` is a Graph message resource (from a poll fetch, or the
        /mock-ingest payload shape, which mirrors the same resource).
        Doesn't know who the manager is -- just describes sender/receiver
        objectively; ingest() decides manager-involvement generically."""
        sender_email = raw.get("sender", {}).get("emailAddress", {}).get("address")
        if not sender_email:
            return None

        to_recipients = raw.get("toRecipients") or []
        to_emails = [r.get("emailAddress", {}).get("address") for r in to_recipients if r.get("emailAddress")]
        if not to_emails:
            logger.debug("[outlook] normalize: no toRecipients, skipping")
            return None
        receiver_email = to_emails[0]

        sender_member = self._resolve_member(db, sender_email)
        receiver_member = self._resolve_member(db, receiver_email)

        body = raw.get("body", {})
        content = body.get("content", "")
        if body.get("contentType", "").lower() == "html":
            content = clean_html(content)

        try:
            dt_utc = datetime.fromisoformat(raw["receivedDateTime"].replace("Z", "+00:00"))
            dt_ist = dt_utc.astimezone(IST).replace(tzinfo=None)
        except Exception:
            dt_ist = timeservice.now_ist()

        return NormalizedMessage(
            platform_msg_id=raw["id"],
            sender_id=sender_email,
            sender_name=sender_member.name if sender_member else None,
            receiver_id=receiver_email,
            receiver_name=receiver_member.name if receiver_member else None,
            channel=receiver_email,
            subject=raw.get("subject"),
            content=content,
            timestamp=dt_ist,
            raw_metadata=json.dumps(raw),
            # Graph's conversationId groups a whole mail thread (step 20) --
            # the ingest job batches per thread_key for claim context.
            thread_key=raw.get("conversationId"),
        )

    def fetch_since(self, since: datetime, manager_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Real Graph poll -- returns [] (not an error) when unconfigured or
        under pytest, since polling has no synchronous caller to report to.
        `manager_id`, when given (the scheduled per-manager outlook_poll job
        always gives it -- see app/projectkb/jobs/outlook_poll.py), scopes
        which manager's token this poll authenticates with."""
        if self._skip_live_calls(manager_id):
            logger.debug("[outlook] fetch_since: skipping live poll (not configured or under pytest)")
            return []
        since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            resp = self._api_call(
                "GET",
                f"{self._graph_base_path()}/messages",
                params={
                    "$filter": f"receivedDateTime ge {since_iso}",
                    "$orderby": "receivedDateTime asc",
                    "$top": 50,
                },
                manager_id=manager_id,
            )
            resp.raise_for_status()
            messages = resp.json().get("value", [])
            logger.info(f"[outlook] fetch_since({since_iso}): {len(messages)} message(s)")
            return messages
        except Exception:
            logger.exception("[outlook] fetch_since failed")
            return []

    # --- Outbound --------------------------------------------------------------

    def send(self, db: Session, to: str, content: str, subject: Optional[str] = None) -> SendResult:
        if not to or not content:
            return SendResult(ok=False, error="invalidRequest")

        cleaned_body = clean_html(content) if "<" in content and ">" in content else content
        platform_msg_id = f"mail_out_{uuid.uuid4().hex[:12]}"

        new_msg = UnifiedMessage(
            platform_msg_id=platform_msg_id,
            source="outlook",
            direction="outbound",
            sender_raw_id="harry.assistant@company.com",
            sender_mapped_name="Harry",
            receiver_raw_id=to,
            channel_raw_id=to,
            subject=subject or "",
            content=cleaned_body,
            timestamp=timeservice.now_ist(),
            created_at=datetime.now(),
            is_processed=False,
            raw_metadata=json.dumps({"subject": subject, "body": content, "toRecipients": [to]}),
        )
        db.add(new_msg)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            platform_msg_id = f"mail_out_{uuid.uuid4().hex[:12]}"
            new_msg.platform_msg_id = platform_msg_id
            db.add(new_msg)
            db.commit()

        if self._skip_live_calls():
            logger.debug(f"[outlook] send: skipping live call (configured={self.is_configured()}), recorded {platform_msg_id}")
            return SendResult(ok=True, platform_msg_id=platform_msg_id)

        if not self.send_allowed():
            logger.warning("[outlook] send blocked: Mail.Send not in granted_scopes (grant it from Connectors)")
            return SendResult(ok=False, platform_msg_id=platform_msg_id, error="send_permission_not_granted")

        try:
            body = {
                "message": {
                    "subject": subject or "",
                    "body": {"contentType": "HTML", "content": content},
                    "toRecipients": [{"emailAddress": {"address": to}}],
                },
                "saveToSentItems": True,
            }
            resp = self._api_call("POST", f"{self._graph_base_path()}/sendMail", json_body=body, scopes=["Mail.Send"])
            ok = resp.status_code == 202
            if not ok:
                logger.error(f"[outlook] sendMail failed: {resp.status_code} {resp.text}")
            return SendResult(ok=ok, platform_msg_id=platform_msg_id, error=None if ok else resp.text)
        except Exception as e:
            logger.exception("[outlook] real Graph sendMail call failed")
            return SendResult(ok=False, platform_msg_id=platform_msg_id, error=str(e))


connector = OutlookConnector()


# --- Pydantic schemas mirroring Microsoft Graph API Mail representation ------

class OutlookEmailBody(BaseModel):
    contentType: str
    content: str


class OutlookEmailSenderAddress(BaseModel):
    address: str
    name: Optional[str] = None


class OutlookEmailSender(BaseModel):
    emailAddress: OutlookEmailSenderAddress


class OutlookEmailRecipientAddress(BaseModel):
    address: str
    name: Optional[str] = None


class OutlookEmailRecipient(BaseModel):
    emailAddress: OutlookEmailRecipientAddress


class OutlookEmailPayload(BaseModel):
    id: str
    sender: OutlookEmailSender
    toRecipients: Optional[List[OutlookEmailRecipient]] = None
    subject: str
    body: OutlookEmailBody
    receivedDateTime: str


@router.post("/mock-ingest", status_code=status.HTTP_201_CREATED)
async def outlook_mock_ingest(
    payload: OutlookEmailPayload,
    response: Response,
    db: Session = Depends(get_manager_db),
    manager: Manager = Depends(get_current_manager),
):
    """Dev/manual injection endpoint -- cookie-scoped like any other route,
    so it writes into the logged-in manager's own db.sqlite/tracked-contacts."""
    msg, ingest_status = ingest(connector, payload.model_dump(), db, manager.id)
    logger.debug(f"[outlook] mock-ingest result: {ingest_status}")

    if ingest_status == "ignored_duplicate":
        response.status_code = status.HTTP_200_OK
        return {"status": "ignored", "detail": "duplicate email ID", "message_id": payload.id}
    if ingest_status != "ok":
        response.status_code = status.HTTP_200_OK
        return {"status": "ignored", "detail": ingest_status, "message_id": payload.id}

    return {"status": "ok", "message_id": msg.platform_msg_id, "sender_mapped": msg.sender_mapped_name}


@router.post("/poll")
async def outlook_poll(db: Session = Depends(get_manager_db), manager: Manager = Depends(get_current_manager)):
    """Manual trigger for real polling -- the scheduled version of this
    lives in app/projectkb/jobs/outlook_poll.py (runs per-manager there,
    same as here)."""
    since = timeservice.now_ist() - timedelta(minutes=15)
    raw_messages = connector.fetch_since(since, manager_id=manager.id)
    results = []
    for raw in raw_messages:
        msg, ingest_status = ingest(connector, raw, db, manager.id)
        results.append({"id": raw.get("id"), "status": ingest_status})
    return {"fetched": len(raw_messages), "results": results}

