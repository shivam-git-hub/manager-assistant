# Feature 02: Unified Connectors and Message Ingestion

This specification defines how the Manager Assistant Agent (Harry) retrieves and normalizes messages and emails from Slack, Outlook, and the Direct Chat Dashboard, and stores them in a unified SQLite table.

---

## 1. Goal

Create a reliable, decoupled ingestion system that:
1.  Receives messages from Slack (via Webhooks), Outlook (via Polling/Mock Ingestion), and the Dashboard (via API).
2.  Normalizes them into a single `unified_messages` table in SQLite.
3.  Prevents duplicate ingestion (idempotency).
4.  Operates as a lightweight Proof of Concept (PoC) with minimal dependencies.

---

## 2. SQLite Database Schema (`unified_messages`)

All incoming data sources will write to a single `unified_messages` table. This table acts as our unified raw message queue/feed.

```sql
CREATE TABLE unified_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform_msg_id TEXT UNIQUE NOT NULL,      -- Unique ID from source (e.g., Slack TS, Outlook MessageID, UUID)
    source TEXT NOT NULL,                     -- 'slack', 'outlook', or 'dashboard'
    sender_raw_id TEXT NOT NULL,              -- Raw sender identifier (e.g., Slack UserID, email address)
    sender_mapped_name TEXT,                  -- Resolved name (e.g., 'Shivam') from team_members, or NULL
    channel_raw_id TEXT NOT NULL,             -- Channel/Conversation container (e.g., Slack channel, Email thread hash)
    thread_id TEXT,                           -- Sub-thread ID (e.g., Slack thread_ts, Email message-id)
    subject TEXT,                             -- Title/Subject (used for emails, dashboard thread titles)
    content TEXT NOT NULL,                    -- Cleaned plain-text / Markdown message content
    timestamp DATETIME NOT NULL,              -- Event timestamp converted to IST (Asia/Kolkata)
    is_processed BOOLEAN DEFAULT 0,           -- Flag: 1 if processed by Agent loop, 0 if pending
    processed_at DATETIME,                    -- Timestamp when agent processed this message
    raw_metadata TEXT                         -- Complete raw JSON payload stored as a string (for debugging/fallback)
);
```

---

## 3. Data Ingestion Architecture

### A. Slack Connector (Push via FastAPI Webhook)
*   **Endpoint:** `POST /api/integrations/slack/webhook`
*   **Behavior:**
    1.  Handles Slack Event API handshake (if `type` is `url_verification`, return the challenge token).
    2.  Ingests message events (e.g., `event.type == "message"` and `event.subtype is None`).
    3.  Extracts key fields:
        *   `platform_msg_id`: `event.client_msg_id` or `event.ts`
        *   `sender_raw_id`: `event.user`
        *   `channel_raw_id`: `event.channel`
        *   `thread_id`: `event.thread_ts` (if present)
        *   `content`: `event.text`
        *   `timestamp`: Convert unix epoch `event.ts` to IST datetime.
    4.  Saves the raw webhook JSON into `raw_metadata`.
    5.  Performs an `INSERT OR IGNORE` into `unified_messages` to handle duplicate webhook deliveries gracefully.
    6.  Returns `200 OK` immediately so Slack doesn't retry.

### B. Outlook Connector (Decoupled Polling & Mock Ingestion)
To keep the PoC simple and easily runnable without complex enterprise Azure AD configurations, we will support **two modes**:
1.  **Mock File Mode (Default for Local PoC):**
    *   Reads a file at `data/mock_outlook.json` or accepts `POST /api/integrations/outlook/mock-ingest` with mock email payloads.
    *   Simulates the arrival of status report emails, Minutes of Meeting (MoM) documents, or direct manager instructions.
2.  **Standard Polling Mode (Optional):**
    *   An async background worker in FastAPI that runs every `X` minutes to check a configured email inbox using simple Python `imaplib` (IMAP) or a simplified Microsoft Graph endpoint.
    *   Maintains a watermark table `outlook_watermark` storing the last processed email's `receivedDateTime`.
*   **HTML Strip Utility:** Since Outlook emails are rich-text, we will include a simple utility to strip HTML tags and convert them to clean, human-readable plain text or markdown before saving to the database.

### C. Direct Chat Dashboard Connector (FastAPI Endpoint)
*   **Endpoint:** `POST /api/integrations/dashboard/message`
*   **Payload:**
    ```json
    {
      "user_name": "Shivam",
      "message": "What is the current status of the database schema?"
    }
    ```
*   **Behavior:**
    1.  Generates a random UUID for `platform_msg_id`.
    2.  Inserts directly into `unified_messages` with:
        *   `source`: `"dashboard"`
        *   `sender_raw_id`: `"dashboard_shivam"`
        *   `sender_mapped_name`: `"Shivam"`
        *   `content`: message text
        *   `timestamp`: current time in IST
        *   `is_processed`: `0`

---

## 4. Ingestion & User Mapping Flow

To populate `sender_mapped_name` automatically at ingestion time, our database database session will perform a quick lookup against a `team_members` table:

```
                  Unified Ingestion Request
                             │
                             ▼
               Identify raw sender ID (e.g., U054ABC)
                             │
                             ▼
           Lookup in team_members where slack_handle
             or outlook_email matches raw sender ID
                             │
            ┌────────────────┴────────────────┐
            ▼ (Found)                         ▼ (Not Found)
     Map team_member.name              Set sender_mapped_name
   to sender_mapped_name                      to NULL
            │                                 │
            └────────────────┬────────────────┘
                             ▼
                 Write to unified_messages
```

---

## 5. Verification & Testing Strategy

To ensure high-quality, working connectors, we will write tests inside `tests/test_connectors.py`:
1.  **Test Slack Ingestion:** Mock an incoming Slack webhook payload, send it to our FastAPI `/api/integrations/slack/webhook` router, and assert that it adds exactly one row to `unified_messages` with correct values and IST timezone.
2.  **Test Outlook Parsing:** Mock a raw Outlook HTML email, run our plain-text extractor, and verify that HTML tables/lists are cleanly formatted as plain markdown.
3.  **Test Deduplication:** Send the same message payload twice to verify that the second call is ignored (idempotency test).
4.  **Test User Resolution:** Pre-populate `team_members` with a user (e.g. name="Shivam", slack_handle="U1234"), trigger a webhook for sender "U1234", and verify that `sender_mapped_name` is successfully resolved to "Shivam".
