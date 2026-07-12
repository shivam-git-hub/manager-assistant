# Feature 05: Harry's Identity, Outbound Send Path, and Quiet-Hours Queue

This specification defines how the Manager Assistant Agent (Harry) establishes its system identity, provides outbound communication transport endpoints mirroring real Slack and MS Graph APIs, implements a quiet-hours outbound queue to prevent late-night pings, and renders outbound messages in the simulator.

---

## 1. Motivation

To act as a project manager assistant, Harry must be able to autonomously *send* communications (such as Slack DMs/channel messages and Outlook emails), rather than just ingest inbound messages. 

To ensure the system remains modular and ready for real-world production deployment, we require that:
*   **API Realism**: The request and response payloads of our outbound sandboxes exactly match official Slack and Microsoft Graph Web APIs. This guarantees Harry's future tool implementations work seamlessly whether pointed at our local mock servers or the real cloud APIs.
*   **Respectful Communication (Quiet Hours)**: Harry shouldn't message developers outside working hours (e.g., at 3 AM). A centralized outbound queue must hold non-urgent outbound pings until the next working morning.
*   **Full Two-Way Sandbox**: Outbound messages sent by Harry must render correctly in the simulator UI with a designated visual identity (e.g., "APP" badge).

---

## 2. Harry's Identity

Harry is seeded automatically inside the backend's user/roster store during database initialization (`init_db()`), guaranteeing his existence before any frontend action occurs.

### Profile Properties:
*   `id`: `"U_HARRY"`
*   `name`: `"Harry"`
*   `role`: `"AI Assistant"`
*   `slack_handle`: `"U_HARRY"`
*   `outlook_email`: `"harry.assistant@company.com"`

### Frontend Switcher Safeguard:
Humans impersonate humans. The frontend's impersonation dropdown switcher must exclude Harry (`id !== 'U_HARRY'`). Harry only speaks programmatically via the backend APIs.

---

## 3. Database Schema Changes

### A. `unified_messages` Table Extension
To distinguish incoming messages from Harry's outgoing pings, we add a `direction` column:
*   `direction`: `Mapped[str]`, SQL type `String(10)`, defaults to `"inbound"`.
*   Allowed values: `"inbound"`, `"outbound"`.
*   A lightweight migration check is executed during `init_db()` to dynamically run `ALTER TABLE unified_messages ADD COLUMN direction VARCHAR(10) DEFAULT 'inbound'` if it is missing (to prevent SQLite schema mismatch errors).

### B. `outbound_queue` Table
A database table to store messages held due to quiet hours:
*   `id`: Integer, Primary Key, autoincrement.
*   `channel_type`: String(10) (`"slack"` or `"outlook"`).
*   `payload`: Text (serialized JSON representing the exact request body of the send endpoint).
*   `status`: String(10) (`"held"`, `"sent"`, or `"cancelled"`; default is `"held"`).
*   `created_at`: DateTime (naive sim IST).
*   `scheduled_release_at`: DateTime (naive sim IST).
*   `sent_at`: DateTime (naive sim IST, nullable).
*   `result_message_id`: String(100) (nullable, mapped to the `platform_msg_id` generated upon successful dispatch).

---

## 4. Slack Outbound Send Path

### Endpoint: `POST /api/integrations/slack/send`
Sends a message to Slack as Harry. This endpoint is a raw transport and does **not** gate on quiet hours.

### Request Body (Mirrors official Slack `chat.postMessage` core schema):
```json
{
  "channel": "C_GENERAL",
  "text": "Hi Alice, do you have any update on the database schema spec?"
}
```
*`channel` can be a channel ID (`C_*`) or a direct message channel (`DM_<USER_A>_<USER_B>`).*

### Behavior:
1.  Verify `channel` and `text` are provided.
2.  If invalid or empty, return HTTP 200 with an in-band error (matching real Slack behavior):
    ```json
    {
      "ok": false,
      "error": "invalid_arguments"
    }
    ```
3.  Store a new `UnifiedMessage`:
    *   `source`: `"slack"`
    *   `direction`: `"outbound"`
    *   `sender`: `"U_HARRY"`
    *   `sender_mapped_name`: `"Harry"`
    *   `channel`: The payload's channel
    *   `content`: The message text
    *   `timestamp`: `timeservice.now_ist()`
    *   `platform_msg_id`: `"slack_out_<epoch>"` (e.g., using `timeservice.now_epoch()`). If a collision occurs on the unique ID, append a UUID4-based string suffix.

### Response Body:
```json
{
  "ok": true,
  "channel": "C_GENERAL",
  "ts": "1783857600.000000",
  "message": {
    "user": "U_HARRY",
    "type": "message",
    "text": "Hi Alice, do you have any update on the database schema spec?",
    "ts": "1783857600.000000"
  }
}
```

---

## 5. Outlook Outbound Send Path

### Endpoint: `POST /api/integrations/outlook/send`
Sends an email as Harry. This endpoint is a raw transport and does **not** gate on quiet hours.

### Request Body (Mirrors official Microsoft Graph `sendMail` schema):
```json
{
  "message": {
    "subject": "Status Update Request",
    "body": {
      "contentType": "HTML",
      "content": "<p>Hi Alice, please update the specs.</p>"
    },
    "toRecipients": [
      {
        "emailAddress": {
          "address": "alice@company.com"
        }
      }
    ]
  },
  "saveToSentItems": true
}
```

### Behavior:
1.  Validate structure. If recipients or body content is missing, return HTTP 400 with Microsoft Graph-style JSON error envelope:
    ```json
    {
      "error": {
        "code": "invalidRequest",
        "message": "Missing toRecipients or body content."
      }
    }
    ```
2.  Clean HTML through our custom `HTMLToMarkdown` helper.
3.  Store a new `UnifiedMessage`:
    *   `source`: `"outlook"`
    *   `direction`: `"outbound"`
    *   `sender`: `"harry.assistant@company.com"`
    *   `sender_mapped_name`: `"Harry"`
    *   `channel`: `"<recipient_address>"` (first recipient — plain address, same convention as ingest)
    *   `content`: `"<cleaned_markdown_content>"` (body only; subject lives in the `subject` column, same as ingest)
    *   `timestamp`: `timeservice.now_ist()`
    *   `platform_msg_id`: `"mail_out_<uuid4_hex_12>"`

### Response:
*   HTTP **202 Accepted** with an empty body.

---

## 6. Quiet Hours and Outbound Queue Engine

To ensure developers are not disturbed at odd times, Harry checks working hours before dispatching messages.

### A. Working Hour Configuration (`app/config.py`):
*   `WORK_HOURS_START`: `9` (9:00 AM IST)
*   `WORK_HOURS_END`: `19` (7:00 PM IST)

### B. Quiet Hours Logic (`app/outbound.py`):
1.  `is_quiet_hours(dt: datetime) -> bool`:
    *   Returns `True` if `dt.hour` is outside `[9, 19)` (i.e., before 9:00 AM or on/after 7:00 PM IST).
    *   Returns `True` if `dt.weekday()` is Saturday (`5`) or Sunday (`6`).
    *   Otherwise, returns `False` (working hours).
2.  `next_work_morning(dt: datetime) -> datetime`:
    *   Finds the next weekday at 9:00 AM IST strictly after the quiet period begins.
    *   *Examples*:
        *   Tue 03:00 $\rightarrow$ Tue 09:00 (same day)
        *   Tue 20:00 $\rightarrow$ Wed 09:00 (next day)
        *   Fri 22:00 $\rightarrow$ Mon 09:00 (next week)
        *   Sat 11:00 $\rightarrow$ Mon 09:00 (next week)

### C. Gate Logic (`send_or_hold`):
```python
def send_or_hold(channel_type: str, payload: dict, db: Session) -> dict:
    now = timeservice.now_ist()
    if is_quiet_hours(now):
        # Insert held row
        release_time = next_work_morning(now)
        # Store in outbound_queue
        # Return {"status": "held", "release_at": release_time}
    else:
        # Execute raw dispatch function (shared logic with endpoints)
        # Return {"status": "sent", "message_id": platform_msg_id}
```

### D. Release and Administration Endpoints:
*   `POST /api/outbound/release`:
    *   Finds all queue rows with `status == "held"` and `scheduled_release_at <= timeservice.now_ist()`.
    *   Dispatches each one via the shared internal sender logic.
    *   Updates status to `"sent"`, populates `sent_at` and `result_message_id`.
    *   Returns:
        ```json
        {
          "released": 1,
          "remaining_held": 0
        }
        ```
*   `GET /api/outbound/queue?status=held`:
    *   Lists matching rows from `outbound_queue`.

---

## 7. Simulator UI Updates

*   **Slack Messages**: Render outbound messages safely in their channels/DMs. Outbound messages from `U_HARRY` will carry:
    *   Name: `Harry`
    *   Visual representation: Initials "H" with a distinctive sparkle/accent avatar.
    *   Badge: An "APP" label adjacent to the name to mimic official Slack apps.
*   **Slack DM list**: Include Harry in the sidebar DMs (`DM_<user>_U_HARRY`) if there are messages or active contact entries.
*   **Outlook Mails**: Render outgoing messages with direction `outbound` in the mail folder/threads lists showing Harry as sender.

---

## 8. Test Plan

A test suite `tests/test_outbound.py` will validate:
1.  Harry's backend-seeding during `init_db()`.
2.  Slack outbound sends for validity, format accuracy, DB persistence, and missing argument handlers (HTTP 200 + in-band error).
3.  Outlook outbound sends for Graph-compliant structure, Markdown stripping, and Graph-formatted validation failures.
4.  Correct behavior of `is_quiet_hours()` and `next_work_morning()` boundaries.
5.  Verification of `send_or_hold()` holding or executing based on simulated timestamps.
6.  The manual `/api/outbound/release` invocation process.
7.  Ensuring all previous test cases remain green and no wall-clock leaks are present.
