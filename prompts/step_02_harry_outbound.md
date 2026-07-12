# Task: Harry's Identity + Outbound Send Path + Quiet-Hours Queue

## Context

Repo: `manager-assistant` — FastAPI + SQLAlchemy 2.0 + SQLite, Vue 3 CDN
simulator at `app/static/index.html`. Start server: `.venv/bin/python3 -m app.main`
(port 3003). Tests: `.venv/bin/python3 -m pytest tests/`.

First read: `CLAUDE.md`, `spec/notes_and_instructions.md`,
`spec/feature_04_sim_time.md` (the sim clock you must use for ALL timestamps),
`app/timeservice.py`, `app/database.py`, `app/config.py`,
`app/integrations/{slack,outlook,unified}.py`, and in `app/static/index.html`:
the impersonation switcher (~line 110), Slack message rendering, Outlook list
rendering, and `populateSamples` (~line 1090).

**Why this feature:** Harry (the AI assistant, built in later steps) must be
able to SEND Slack messages and emails, not just receive them. This step
builds the transport: send endpoints whose request/response shapes mirror the
real Slack Web API `chat.postMessage` and Microsoft Graph `sendMail`, so
Harry's tool layer works unchanged against real services later. It also adds
the quiet-hours outbound queue (Harry must never ping people at 3 AM — held
pings release at the next working morning) and renders Harry's messages in
the simulator, completing the two-way sandbox.

**Project rules:**
- TDD: failing tests first, then implement, then run the FULL suite.
- All datetimes naive IST via `app.timeservice` — NEVER `datetime.now()` /
  `time.time()` outside `app/timeservice.py` (a guard test enforces this).
- No new pip dependencies.
- Payload realism: request/response shapes must mirror the real APIs. No
  processing logic in the simulator — it only sends/renders.
- Keep `CLAUDE.md`'s step list untouched (the reviewer updates it).

## Subproblem 0 — Spec file

Write `spec/feature_05_outbound.md`: motivation, Harry's identity, the two
send endpoints with example request/response JSON (copied shapes from real
Slack/Graph docs), the `direction` column, the quiet-hours rules, the
`outbound_queue` table, and the test plan. Same style as feature_04.

## Subproblem 1 — Harry's identity (backend-seeded)

Team members are currently seeded by the FRONTEND (`populateSamples` in
index.html) — Harry must instead be guaranteed by the BACKEND so he exists
even on a fresh DB with no UI visit. In `init_db()` (app/database.py), after
`create_all`, insert-if-missing:

```
id="U_HARRY", name="Harry", role="AI Assistant",
slack_handle="U_HARRY", outlook_email="harry.assistant@company.com"
```

Also: the simulator's "Reset DB" flow keeps team members (verify), and the
impersonation dropdown must EXCLUDE Harry (filter `id !== 'U_HARRY'` where
the switcher options are built) — humans impersonate humans; Harry's messages
come only through the outbound API.

## Subproblem 2 — `direction` column on unified_messages

Add `direction: Mapped[str]` (String(10), default `"inbound"`) to
`UnifiedMessage`. SQLite + `create_all` will NOT add columns to an existing
table: add a tiny migration helper in `init_db()` that checks
`PRAGMA table_info(unified_messages)` and runs
`ALTER TABLE unified_messages ADD COLUMN direction VARCHAR(10) DEFAULT 'inbound'`
if missing. Include `direction` in the message response schema and in
`GET /api/messages` output.

## Subproblem 3 — Slack send endpoint (mirrors chat.postMessage)

`POST /api/integrations/slack/send` in `app/integrations/slack.py`.

Request body (exactly the real API's core fields — extra fields ignored):
```json
{ "channel": "C_GENERAL", "text": "Hi Alice, any update on the schema?" }
```
`channel` may be a channel id (`C_*`) or DM id (`DM_*` — same convention the
simulator uses, sorted-handles `DM_<A>_<B>`).

Behavior: sender is ALWAYS Harry (like a bot token identity). Create a
`UnifiedMessage`: source="slack", direction="outbound", sender="U_HARRY",
sender_mapped_name="Harry", channel=payload channel, content=text,
timestamp=`timeservice.now_ist()`, platform_msg_id=`"slack_out_{ts_epoch}"`
(use `timeservice.now_epoch()`; on UNIQUE collision, retry with a uuid4
suffix).

Response mirrors the real API:
```json
{ "ok": true, "channel": "C_GENERAL", "ts": "<epoch as string>",
  "message": { "user": "U_HARRY", "type": "message", "text": "...", "ts": "<same>" } }
```
Errors mirror Slack style too: unknown channel type / empty text →
`{ "ok": false, "error": "invalid_arguments" }` with HTTP 200 (that's how
Slack behaves — errors are in-band).

## Subproblem 4 — Outlook send endpoint (mirrors Graph sendMail)

`POST /api/integrations/outlook/send` in `app/integrations/outlook.py`.

Request body (real Graph shape):
```json
{ "message": { "subject": "...", "body": { "contentType": "HTML", "content": "<p>...</p>" },
    "toRecipients": [ { "emailAddress": { "address": "alice@company.com" } } ] },
  "saveToSentItems": true }
```

Behavior: sender is Harry (harry.assistant@company.com). Clean the HTML body
through the existing `HTMLToMarkdown` cleaner (same as ingest — one storage
format everywhere). Store UnifiedMessage: source="outlook",
direction="outbound", sender=harry's email, sender_mapped_name="Harry",
channel=`"email:<first recipient address>"` (matching however ingest stores
the mail channel — check and stay consistent with the existing convention),
content=cleaned body prefixed with `Subject: <subject>\n\n` (again: match the
ingest convention exactly — read outlook.py first),
timestamp=`timeservice.now_ist()`, platform_msg_id=`"mail_out_<uuid4hex12>"`.

Response mirrors Graph: HTTP **202 Accepted with empty body**. Validation
errors → 400 with a Graph-style `{"error": {"code": "...", "message": "..."}}`.

## Subproblem 5 — Quiet hours + outbound queue

New module `app/outbound.py` + config in `app/config.py`:
`WORK_HOURS_START = 9`, `WORK_HOURS_END = 19` (IST hours).

- `is_quiet_hours(dt: datetime) -> bool` — True when outside
  [09:00, 19:00) OR on Saturday/Sunday.
- `next_work_morning(dt: datetime) -> datetime` — next datetime at 09:00 on a
  weekday strictly after quiet time begins (e.g. Fri 22:00 → Mon 09:00;
  Tue 03:00 → Tue 09:00; Tue 20:00 → Wed 09:00).
- New table `outbound_queue`: id (int PK), channel_type ("slack"/"outlook"),
  payload (Text, the JSON body that would have been POSTed to the send
  endpoint), status ("held"/"sent"/"cancelled", default "held"),
  created_at (sim IST), scheduled_release_at (sim IST), sent_at (nullable),
  result_message_id (nullable, the platform_msg_id produced on send).
- `send_or_hold(channel_type, payload, db) -> dict` — the function Harry's
  tools will call in later steps: if `is_quiet_hours(timeservice.now_ist())`,
  insert a held row with `scheduled_release_at=next_work_morning(now)` and
  return `{"status": "held", "release_at": ...}`; otherwise call the same
  internal send logic the endpoints use (refactor endpoints so the core send
  is a plain function both paths share) and return
  `{"status": "sent", "message_id": ...}`.
- `POST /api/outbound/release` — process all held rows with
  `scheduled_release_at <= timeservice.now_ist()`: send each (mark "sent",
  set sent_at + result_message_id). Returns
  `{"released": <n>, "remaining_held": <m>}`. (Step 5's scheduler will call
  this on time-advance; for now it's manual/testable.)
- `GET /api/outbound/queue?status=held` — list queue rows (for dashboard +
  tests).

IMPORTANT: the raw send endpoints (subproblems 3-4) do NOT gate on quiet
hours — they are transports, like the real APIs (real Slack sends at 3 AM if
you call it). The gate lives ONLY in `send_or_hold`.

## Subproblem 6 — Simulator renders Harry's messages

- Slack pane: outbound messages already flow into `/api/messages`; verify
  they render in the right channel/DM. Give Harry a distinct look: name
  "Harry", a robot/sparkle avatar (initials "H" with a distinct accent color
  works), and a small "APP" badge next to the name like real Slack bots have.
- DM sidebar: a DM between Harry and a user (`DM_` id containing `U_HARRY`)
  must appear in the DM list with Harry's name.
- Outlook pane: outbound mails (direction="outbound") appear in the mail
  list with sender "Harry" and render in the reading pane. If the list is
  folder-based, they can appear in the same feed — no folder logic needed.
- Do NOT add any send-as-Harry UI. Harry only speaks via the API.
  (Manual testing: `curl` the send endpoints.)

## Subproblem 7 — README run-command fix

`README.md` documents `.venv/bin/python3 app/main.py`, which fails with
`ModuleNotFoundError: No module named 'app'`. Fix the README to
`.venv/bin/python3 -m app.main` (both occurrences if more than one).

## Subproblem 8 — Tests (write FIRST)

New `tests/test_outbound.py` (reuse the tmp `SIM_CLOCK_PATH` fixture pattern
from `tests/test_timeservice.py`):

1. Harry exists after `init_db()` on a fresh test DB (id U_HARRY).
2. Slack send: valid body → response has `ok: true`, `ts` string; DB row has
   direction="outbound", sender_mapped_name="Harry", correct channel; message
   appears in `GET /api/messages`.
3. Slack send with empty text → `{"ok": false, "error": "invalid_arguments"}`.
4. Outlook send: valid Graph-shaped body → 202 empty body; DB row outbound,
   HTML body cleaned (send `<style>x{color:red}</style><p>Done</p>`, stored
   content must contain "Done" and no CSS), subject stored per convention.
5. `is_quiet_hours`: 08:59 → True, 09:00 → False, 18:59 → False, 19:00 →
   True; any Saturday/Sunday daytime → True.
6. `next_work_morning`: Tue 03:00 → Tue 09:00; Tue 20:00 → Wed 09:00;
   Fri 22:00 → Mon 09:00; Sat 11:00 → Mon 09:00.
7. `send_or_hold` during work hours (set sim time Wed 11:00) → sent, row NOT
   queued, message in DB.
8. `send_or_hold` at Wed 23:00 → held with release Thu 09:00; then set sim
   time Thu 09:05, POST `/api/outbound/release` → released=1, message now in
   DB with direction="outbound", queue row status="sent".
9. `direction` defaults to "inbound" for a normal Slack webhook ingest.
10. Existing full suite still green.

## Definition of done

- [ ] `spec/feature_05_outbound.md` written
- [ ] Full test suite green; wall-clock guard still clean
      (`grep -rn "datetime.now(" app/ | grep -v timeservice` → nothing)
- [ ] Manual check: start server; `curl -X POST .../api/integrations/slack/send`
      with a DM channel to Alice → message visible in simulator Slack DM with
      Harry's APP badge; Graph-shaped sendMail curl → mail visible in Outlook
      pane from Harry; set clock to 23:00, exercise `send_or_hold` via a tiny
      python snippet or test, advance to 09:05, POST release → message appears
- [ ] README run command fixed
- [ ] Commit with a clear message
