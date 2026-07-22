# Step 20 — Poll Completion: track-everything + blocklist, threads, channels, manual MoMs

Authority: `spec/architecture_v2_kb.md` §4.1–§4.2 + §3.2. Implementation
order item 3 (plus the message-schema/blocklist remainder of item 2).
Implemented directly by Claude (workflow of 2026-07-22).

## 1. Philosophy inversion (spec §4.2)

v1 stored a message only if its counterpart was on an allowlist
(`tracked.py`) / the channel on `tracked_channels.py`. v2 stores
EVERYTHING the pollers fetch (the user's own mailbox/DMs/channels belong
to them by construction) and filters at ingest-job selection time instead:

- `app/integrations/base.py ingest()`: drop the manager-involvement and
  tracked-list gates entirely → normalize → dedup → store. Statuses reduce
  to `ok | ignored_duplicate | ignored_not_a_message`. Write
  `thread_id` from the new `NormalizedMessage.thread_key`. Replace the
  `datetime.now()` wall-clock read with `timeservice.now_ist()` while
  touching the insert.
- DELETE `app/projectkb/tracked.py` + `tracked_channels.py` and the
  `/api/projectkb/tracked-contacts` router; replace with
  `app/projectkb/blocklist.py` + `/api/blocklist` (below).

## 2. Blocklist + noise filter (`app/projectkb/blocklist.py`)

Per-manager `managers/<id>/blocklist.json`:
`{"contacts": [{id,label,email_pattern,slack_pattern}], "channels":
[{id,label,source,pattern}]}` — same fnmatch matching as the old
allowlist. API (`app/projectkb/api.py`, cookie-auth): `GET /api/blocklist`,
`POST/PUT/DELETE /api/blocklist/contacts[/{id}]`,
`POST/DELETE /api/blocklist/channels[/{id}]`.

`classify_message(manager_id, msg: UnifiedMessage) -> "blocked" | "noise" | None`
— THE selection filter the future ingest job (step 4) calls before any
LLM sees a message; skipped rows get `is_processed=True` +
`skip_reason` there. Rules (deterministic, no LLM):
- blocked: sender OR receiver matches a blocked contact (source-matched
  pattern field), or the channel matches a blocked channel.
- noise: no-reply-style sender local parts (`noreply|no-reply|
  donotreply|notifications|mailer-daemon|postmaster`), calendar response
  subjects (`Accepted:|Declined:|Tentative:`), `List-Unsubscribe` in
  raw_metadata (bulk mail).

## 3. Schema

- `NormalizedMessage.thread_key: Optional[str]` — Outlook:
  `conversationId`; Slack: `"<channel>:<thread_ts or ts>"`
  (`_history_message_to_event` must pass `thread_ts` through).
- `UnifiedMessage.skip_reason` (nullable) + ALTER-TABLE check in
  `init_manager_db`.

## 4. Slack channels/groups polling

`fetch_since`: `conversations.list types="im,mpim,private_channel,
public_channel"`; per-conversation `channel_type` derived from the
conversation object flags (`is_im→im, is_mpim→mpim, is_group or
is_private→group, else channel`) so history rows flow through the same
normalize() map.

## 5. Manual MoM endpoint (spec §3.2)

`POST /api/messages/manual {content, subject?}` (app/api/home.py) →
`unified_messages` row: `source="manual"`, `platform_msg_id=manual_<uuid>`,
sender = the logged-in user (email/name), `channel_raw_id="manual"`,
sim-time timestamps, `is_processed=False`. MoMs/pasted notes then flow
through the exact same claim→event pipeline — no side channel.

## 6. Tests

New `tests/test_poll_completion.py`: thread_key for both sources; history
shaping passes thread_ts; blocklist module CRUD + fnmatch; classify
(blocked contact / blocked channel / no-reply / calendar stub /
List-Unsubscribe / clean→None); manual MoM endpoint + per-user isolation;
untracked-counterpart DM now STORED (inversion proof).
Updated: `test_connectors.py` (gate assertions inverted),
`test_slack_channels.py` (tracked_channels → channel blocklist),
`test_outbound.py` (drop tracked-contacts setup).
