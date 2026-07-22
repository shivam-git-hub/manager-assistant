"""Tracked-contact list: gates which counterpart addresses/handles the
ingestion job is allowed to process messages for, on top of the existing
"only messages to/from the manager" restriction.

Storage: one JSON file per manager (managers/<manager_id>/tracked_contacts.json,
step 15), a flat list of entries. Each entry represents one real person or a
whole domain/team, and can carry a pattern per source since Slack IDs and
email addresses are different namespaces for the same person:

    [
      {"label": "Alice", "email_pattern": "alice@company.com", "slack_pattern": "U_ALICE"},
      {"label": "Vendor team", "email_pattern": "*@vendor.com", "slack_pattern": null}
    ]

Patterns are matched with fnmatch, which handles both "exact match" (a
pattern with no wildcard characters matches only itself) and glob
expressions ("*@vendor.com") through the same code path -- no separate
exact/glob "type" field needed.

How entries get added: there's no UI/endpoint for this yet (out of scope
for this pass) -- for now a human edits data/tracked_contacts.json directly,
or calls add_tracked_contact() from a script/shell. A management endpoint
can wrap these same functions later without changing the storage format.

How it's used at ingestion (once wired into app/integrations/slack.py and
app/integrations/outlook.py): for a message that's already been confirmed
to be to/from the manager, the OTHER party's address is checked with
is_tracked(source, address) before the message is queued for extraction.
Slack messages check the Slack user_id against slack_pattern; Outlook
messages check the email address against email_pattern. A message whose
counterpart isn't tracked is left alone entirely -- not ingested, not
extracted, not stored anywhere in the projectkb pipeline.
"""
import json
import fnmatch
import uuid
from typing import List, Dict, Optional

SOURCE_PATTERN_FIELD = {
    "slack": "slack_pattern",
    "outlook": "email_pattern",
}


def _load_raw(manager_id: str) -> List[Dict]:
    from app.tenancy.paths import manager_tracked_contacts_path

    path = manager_tracked_contacts_path(manager_id)
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def load_tracked_contacts(manager_id: str) -> List[Dict]:
    return _load_raw(manager_id)


def save_tracked_contacts(manager_id: str, entries: List[Dict]) -> None:
    from app.tenancy.paths import manager_dir, manager_tracked_contacts_path

    manager_dir(manager_id).mkdir(parents=True, exist_ok=True)
    with open(manager_tracked_contacts_path(manager_id), "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


def add_tracked_contact(
    manager_id: str,
    label: str,
    email_pattern: Optional[str] = None,
    slack_pattern: Optional[str] = None,
) -> Dict:
    """Add (or return the existing match, no duplicate, if an identical
    entry already exists) one tracked contact/team. Either pattern may be
    omitted if this contact only uses one channel. Returns the entry
    (including its id)."""
    entries = load_tracked_contacts(manager_id)
    for e in entries:
        if e.get("email_pattern") == email_pattern and e.get("slack_pattern") == slack_pattern:
            return e
    entry = {
        "id": uuid.uuid4().hex,
        "label": label,
        "email_pattern": email_pattern,
        "slack_pattern": slack_pattern,
    }
    entries.append(entry)
    save_tracked_contacts(manager_id, entries)
    return entry


def get_tracked_contact(manager_id: str, contact_id: str) -> Optional[Dict]:
    for e in load_tracked_contacts(manager_id):
        if e.get("id") == contact_id:
            return e
    return None


def update_tracked_contact(
    manager_id: str,
    contact_id: str,
    label: Optional[str] = None,
    email_pattern: Optional[str] = ...,
    slack_pattern: Optional[str] = ...,
) -> Optional[Dict]:
    """Partial update -- omit a field (leave it at the `...` sentinel) to
    keep its current value; pass None explicitly to clear it."""
    entries = load_tracked_contacts(manager_id)
    for e in entries:
        if e.get("id") == contact_id:
            if label is not None:
                e["label"] = label
            if email_pattern is not ...:
                e["email_pattern"] = email_pattern
            if slack_pattern is not ...:
                e["slack_pattern"] = slack_pattern
            save_tracked_contacts(manager_id, entries)
            return e
    return None


def delete_tracked_contact(manager_id: str, contact_id: str) -> bool:
    entries = load_tracked_contacts(manager_id)
    remaining = [e for e in entries if e.get("id") != contact_id]
    if len(remaining) == len(entries):
        return False
    save_tracked_contacts(manager_id, remaining)
    return True


def is_tracked(manager_id: str, source: str, address: str) -> bool:
    """Deterministic match -- no LLM involved. `source` is "slack" or
    "outlook"; `address` is the Slack user_id or the email address of the
    OTHER party in a manager conversation. Matching is case-insensitive."""
    if not address:
        return False
    field = SOURCE_PATTERN_FIELD.get(source)
    if not field:
        return False
    address_lower = address.lower()
    for entry in load_tracked_contacts(manager_id):
        pattern = entry.get(field)
        if not pattern:
            continue
        if fnmatch.fnmatch(address_lower, pattern.lower()):
            return True
    return False
