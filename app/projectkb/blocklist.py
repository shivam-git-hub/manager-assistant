"""Blocklist + deterministic noise filter (step 20 --
prompts/step_20_poll_completion.md, spec/architecture_v2_kb.md §4.2).

Philosophy inversion from v1: the pollers store EVERYTHING (the user's own
mailbox/DMs/channels belong to them by construction); the user lists what
NOT to process. classify_message() is the selection filter the ingest job
(spec §4.2, step 4) runs before any LLM sees a message -- skipped rows get
`is_processed=True` + `skip_reason` there, so editing the blocklist never
loses history and every skip is auditable.

Storage: one blocklist.json per manager (managers/<id>/blocklist.json):

    {
      "contacts": [{"id", "label", "email_pattern", "slack_pattern"}],
      "channels": [{"id", "label", "source", "pattern"}]
    }

Patterns are fnmatch (exact strings match themselves; globs like
"*@vendor.com" work through the same code path), case-insensitive --
identical matching semantics to the old allowlist so nothing subtle
changes in how patterns behave, only in what a match MEANS.
"""
import json
import fnmatch
import uuid
from typing import Dict, List, Optional

SOURCE_PATTERN_FIELD = {
    "slack": "slack_pattern",
    "outlook": "email_pattern",
}

# Deterministic noise rules (spec §4.2 layer 2) -- built-in, not
# user-managed. All lowercase; matched against the sender's local part.
NOISE_SENDER_LOCAL_PARTS = (
    "noreply",
    "no-reply",
    "no_reply",
    "donotreply",
    "do-not-reply",
    "notifications",
    "notification",
    "mailer-daemon",
    "postmaster",
)
# Calendar accept/decline stubs (Outlook prefixes these).
NOISE_SUBJECT_PREFIXES = ("accepted:", "declined:", "tentative:")
# Bulk-mail marker; Graph exposes raw headers inside the stored payload.
NOISE_METADATA_MARKERS = ("list-unsubscribe",)


def _empty() -> Dict:
    return {"contacts": [], "channels": []}


def load_blocklist(manager_id: str) -> Dict:
    from app.tenancy.paths import manager_blocklist_path

    path = manager_blocklist_path(manager_id)
    if not path.exists():
        return _empty()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    return {
        "contacts": data.get("contacts") or [],
        "channels": data.get("channels") or [],
    }


def save_blocklist(manager_id: str, data: Dict) -> None:
    from app.tenancy.paths import manager_dir, manager_blocklist_path

    manager_dir(manager_id).mkdir(parents=True, exist_ok=True)
    with open(manager_blocklist_path(manager_id), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ── Contacts ─────────────────────────────────────────────

def add_blocked_contact(
    manager_id: str,
    label: str,
    email_pattern: Optional[str] = None,
    slack_pattern: Optional[str] = None,
) -> Dict:
    data = load_blocklist(manager_id)
    for e in data["contacts"]:
        if e.get("email_pattern") == email_pattern and e.get("slack_pattern") == slack_pattern:
            return e
    entry = {
        "id": uuid.uuid4().hex,
        "label": label,
        "email_pattern": email_pattern,
        "slack_pattern": slack_pattern,
    }
    data["contacts"].append(entry)
    save_blocklist(manager_id, data)
    return entry


def update_blocked_contact(
    manager_id: str,
    contact_id: str,
    label: Optional[str] = None,
    email_pattern: Optional[str] = ...,
    slack_pattern: Optional[str] = ...,
) -> Optional[Dict]:
    """Partial update -- omit a field (`...` sentinel) to keep it; pass
    None explicitly to clear it."""
    data = load_blocklist(manager_id)
    for e in data["contacts"]:
        if e.get("id") == contact_id:
            if label is not None:
                e["label"] = label
            if email_pattern is not ...:
                e["email_pattern"] = email_pattern
            if slack_pattern is not ...:
                e["slack_pattern"] = slack_pattern
            save_blocklist(manager_id, data)
            return e
    return None


def delete_blocked_contact(manager_id: str, contact_id: str) -> bool:
    data = load_blocklist(manager_id)
    remaining = [e for e in data["contacts"] if e.get("id") != contact_id]
    if len(remaining) == len(data["contacts"]):
        return False
    data["contacts"] = remaining
    save_blocklist(manager_id, data)
    return True


# ── Channels ─────────────────────────────────────────────

def add_blocked_channel(manager_id: str, label: str, source: str, pattern: str) -> Dict:
    data = load_blocklist(manager_id)
    for e in data["channels"]:
        if e.get("source") == source and e.get("pattern") == pattern:
            return e
    entry = {"id": uuid.uuid4().hex, "label": label, "source": source, "pattern": pattern}
    data["channels"].append(entry)
    save_blocklist(manager_id, data)
    return entry


def delete_blocked_channel(manager_id: str, channel_id: str) -> bool:
    data = load_blocklist(manager_id)
    remaining = [e for e in data["channels"] if e.get("id") != channel_id]
    if len(remaining) == len(data["channels"]):
        return False
    data["channels"] = remaining
    save_blocklist(manager_id, data)
    return True


# ── Matching ─────────────────────────────────────────────

def is_contact_blocked(manager_id: str, source: str, address: str) -> bool:
    if not address:
        return False
    field = SOURCE_PATTERN_FIELD.get(source)
    if not field:
        return False
    address_lower = address.lower()
    for entry in load_blocklist(manager_id)["contacts"]:
        pattern = entry.get(field)
        if pattern and fnmatch.fnmatch(address_lower, pattern.lower()):
            return True
    return False


def is_channel_blocked(manager_id: str, source: str, channel: str) -> bool:
    if not channel:
        return False
    channel_lower = channel.lower()
    for entry in load_blocklist(manager_id)["channels"]:
        if entry.get("source") != source:
            continue
        pattern = entry.get("pattern")
        if pattern and fnmatch.fnmatch(channel_lower, pattern.lower()):
            return True
    return False


# ── The ingest-selection filter ──────────────────────────

def classify_message(manager_id: str, msg) -> Optional[str]:
    """Returns "blocked" | "noise" | None for a UnifiedMessage row. Run by
    the ingest job on unprocessed rows BEFORE any LLM call; a non-None
    result means: mark is_processed=True with this skip_reason, never scan
    again. Deterministic only -- no LLM here (that's the flash model's
    separate de-noise role, spec §4.2 layer 3)."""
    # Layer 1: user blocklist.
    if is_contact_blocked(manager_id, msg.source, msg.sender_raw_id or ""):
        return "blocked"
    if is_contact_blocked(manager_id, msg.source, msg.receiver_raw_id or ""):
        return "blocked"
    if is_channel_blocked(manager_id, msg.source, msg.channel_raw_id or ""):
        return "blocked"

    # Layer 2: built-in noise rules.
    sender = (msg.sender_raw_id or "").lower()
    local_part = sender.split("@", 1)[0]
    # Substring, not just prefix: real automated senders routinely put the
    # marker mid-local-part (account-security-noreply@..., azure-noreply@...,
    # no-reply-<random-token>@slack.com) -- a startswith-only check let
    # those straight through to the LLM in live testing (2026-07-23).
    if any(p in local_part for p in NOISE_SENDER_LOCAL_PARTS):
        return "noise"
    subject = (msg.subject or "").lower()
    if any(subject.startswith(p) for p in NOISE_SUBJECT_PREFIXES):
        return "noise"
    metadata = (msg.raw_metadata or "").lower()
    if any(m in metadata for m in NOISE_METADATA_MARKERS):
        return "noise"

    return None
