"""Tracked-channel list (step 17 piece 3 -- prompts/step_17_agent_pool.md):
the channel/group analogue of app/projectkb/tracked.py's per-contact list.

A DM has exactly one counterpart, so is_tracked(manager_id, source, address)
gates on that single address. A channel or group has N participants -- there
is no single "the other party" to check -- so ingest() gates channel/group
messages on the CHANNEL's own id/name instead, via is_channel_tracked()
here, deliberately a separate list rather than implicitly overlapping with
tracked-contacts (a channel message from an untracked channel is ignored
even if one of its participants happens to be an individually-tracked
contact -- see ingest()'s conversation_type branch in app/integrations/base.py).

Storage: managers/<manager_id>/tracked_channels.json, same flat-list-of-
patterns shape as tracked_contacts.json, one pattern field per source
(fnmatch, so exact IDs and globs share one code path):

    [
      {"label": "#eng-standup", "slack_pattern": "C0123456"},
      {"label": "All project channels", "slack_pattern": "C_PROJ_*"}
    ]

No UI/endpoint yet -- same out-of-scope-for-this-pass note as tracked.py.
"""
import json
import fnmatch
import uuid
from typing import List, Dict, Optional

SOURCE_PATTERN_FIELD = {
    "slack": "slack_pattern",
}


def _load_raw(manager_id: str) -> List[Dict]:
    from app.tenancy.paths import manager_tracked_channels_path

    path = manager_tracked_channels_path(manager_id)
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def load_tracked_channels(manager_id: str) -> List[Dict]:
    return _load_raw(manager_id)


def save_tracked_channels(manager_id: str, entries: List[Dict]) -> None:
    from app.tenancy.paths import manager_dir, manager_tracked_channels_path

    manager_dir(manager_id).mkdir(parents=True, exist_ok=True)
    with open(manager_tracked_channels_path(manager_id), "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


def add_tracked_channel(manager_id: str, label: str, slack_pattern: str) -> Dict:
    entries = load_tracked_channels(manager_id)
    for e in entries:
        if e.get("slack_pattern") == slack_pattern:
            return e
    entry = {"id": uuid.uuid4().hex, "label": label, "slack_pattern": slack_pattern}
    entries.append(entry)
    save_tracked_channels(manager_id, entries)
    return entry


def delete_tracked_channel(manager_id: str, channel_id: str) -> bool:
    entries = load_tracked_channels(manager_id)
    remaining = [e for e in entries if e.get("id") != channel_id]
    if len(remaining) == len(entries):
        return False
    save_tracked_channels(manager_id, remaining)
    return True


def is_channel_tracked(manager_id: str, source: str, channel_address: str) -> bool:
    """Deterministic match, same convention as tracked.is_tracked."""
    if not channel_address:
        return False
    field = SOURCE_PATTERN_FIELD.get(source)
    if not field:
        return False
    address_lower = channel_address.lower()
    for entry in load_tracked_channels(manager_id):
        pattern = entry.get(field)
        if not pattern:
            continue
        if fnmatch.fnmatch(address_lower, pattern.lower()):
            return True
    return False
