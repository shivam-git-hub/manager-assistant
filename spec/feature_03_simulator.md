# Feature 03: Interactive Integration Simulator

This specification defines the design and behavior of the **Simulator Service**, a lightweight web interface that allows us to mock multiple users (employees) sending messages via simulated Slack and Outlook clients.

---

## 1. Objectives

*   Provide an easy-to-use, zero-setup testing interface for demonstrating the PoC.
*   Allow the creation of multiple employee profiles directly in the database.
*   Enable logging in as different employees to simulate real communication channels.
*   Translate UI inputs into exact payload formats that mock real Slack Webhooks and Outlook API responses.

---

## 2. Architecture & UI Design

The Simulator is a lightweight Single Page Application (SPA) served directly by FastAPI (e.g., mounting a static folder `/simulator` or returning HTML files).

### High-Fidelity UI Styling (Slack & Outlook Realism)
Since this PoC will be presented to **senior management**, the user interface must be highly polished, instantly recognizable, and visually impressive while remaining simple under the hood. We will combine our clean, peaceful pastel aesthetic with the authentic layouts of the actual platforms:

1.  **Slack View Mimicry:**
    *   **Sidebar:** Classic dark aubergine/purple-gray (`#3F0E40` or a softer pastel variant like `#4A154B`) containing channels, direct messages, and workspace branding.
    *   **Chat Workspace:** Crisp white/gray background with message headers showing user avatars (simple pastel colored initials), timestamps, and realistic slack-style message groupings.
2.  **Outlook View Mimicry:**
    *   **Classic Three-Pane Layout:**
        *   *Left Pane:* Navigation folder list (Inbox, Sent Items, Drafts) in soft gray.
        *   *Middle Pane:* Live email thread card feed (showing sender, subject preview, and timestamp).
        *   *Right Pane:* Fully-featured reading & compose window showing clean headers, formatted email bodies, and high-fidelity "Compose Email" controls.
3.  **Global Presentation Wrapper:**
    *   A clean top navigation bar allowing seamless switching between the **Admin Desk**, **Slack Client**, and **Outlook Inbox**.
    *   A persistent banner displaying: **"Impersonation Context: Acting as [User Name] ([User Role])"** with a quick switch button.
    *   **Colors & Theme:** A hybrid of authentic UI geometry with a fresh, soft, peaceful pastel color palette (rounded borders, generous padding, gentle shadow depths) that looks modern, clean, and professional.

---

## 3. Simulator Views and User Flow

```
┌────────────────────────────────────────────────────────┐
│                    SIMULATOR PORTAL                    │
│                                                        │
│  ┌──────────────────┐  ┌────────────────────────────┐  │
│  │   ADMIN VIEW     │  │        LOGIN PAGE          │  │
│  │  (Add/Edit Team) │  │ (Select profile to assume) │  │
│  └────────┬─────────┘  └─────────────┬──────────────┘  │
│           │                          │                 │
│           ▼                          ▼                 │
│  ┌──────────────────────────────────────────────────┐  │
│  │                  EMPLOYEE HUB                    │  │
│  │                                                  │  │
│  │  ┌──────────────────────┐┌─────────────────────┐ │  │
│  │  │   SLACK SIMULATOR    ││  OUTLOOK SIMULATOR  │ │  │
│  │  │  (Mock Channels/Chat) ││  (Compose/Send MoM)│ │  │
│  │  └──────────────────────┘└─────────────────────┘ │  │
│  └──────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────┘
```

### A. Admin View (Team Management)
Provides a form to add employees directly into the SQLite `team_members` table.
*   **Fields:**
    *   **Full Name:** e.g., `Alice Johnson`
    *   **Role:** e.g., `Backend Engineer`
    *   **Slack ID:** e.g., `U054ALICE`
    *   **Outlook Email:** e.g., `alice@company.com`
*   **Behavior:** Submitting the form performs a `POST /api/team` request, immediately adding the user to the database and making them available in the Login dropdown list.

### B. Login View (Profile Selection)
Allows the tester to choose who they want to act as.
*   **Controls:** A simple drop-down menu containing all team members from the SQLite database.
*   **Action:** Clicking "Log In" saves the selected user's profile metadata (Name, Slack ID, Email) into the browser's `localStorage` or session cookie. The dashboard changes to show: **"Acting as: Alice Johnson (Backend Engineer)"**.

### C. Slack Simulator View
A messaging panel resembling Slack.
*   **Sidebar:** Channel list (e.g., `#general`, `#project-updates`, `#alerts`).
*   **Chat Window:** Shows historical messages sent in that channel.
*   **Input Box:** A text area where the active user can type a message.
*   **Submit Behavior:** Clicking "Send Message" triggers a `POST /api/integrations/slack/webhook` payload:
    ```json
    {
      "type": "event_callback",
      "api_app_id": "A054APP",
      "event": {
        "type": "message",
        "client_msg_id": "sim-slack-msg-uuid",
        "user": "ACTIVE_USER_SLACK_ID",
        "channel": "ACTIVE_CHANNEL_ID",
        "text": "Message typed by user",
        "ts": "1789025345.000000"
      }
    }
    ```

### D. Outlook Simulator View
A composition form resembling an email client.
*   **Form Fields:**
    *   **From:** (Locked to the active logged-in user's email)
    *   **To:** `harry.assistant@company.com` (Our agent email box)
    *   **Subject:** text input (e.g., "MoM: Database Migration Alignment")
    *   **Body:** large text area (simulates status report or meeting minutes)
*   **Submit Behavior:** Clicking "Send Email" triggers a `POST /api/integrations/outlook/mock-ingest` payload:
    ```json
    {
      "id": "sim-mail-uuid",
      "sender": {
        "emailAddress": {
          "address": "ACTIVE_USER_EMAIL"
        }
      },
      "subject": "Subject typed by user",
      "body": {
        "contentType": "html",
        "content": "<div>Body typed by user</div>"
      },
      "receivedDateTime": "2026-05-29T10:00:00Z"
    }
    ```

---

## 4. Backend Implementation Plan (FastAPI)

To support this front-end simulator, we will implement the following endpoints in our FastAPI app:

1.  `GET /api/team` - Returns list of all team members.
2.  `POST /api/team` - Adds a team member.
3.  `POST /api/integrations/slack/webhook` - Standard connector webhook (shared by mock & real Slack).
4.  `POST /api/integrations/outlook/mock-ingest` - Simulates receiving an email (only active in development/PoC mode).

---

## 5. Verification & Testing Strategy

To ensure the simulator correctly bridges to our unified message ingestion logic:
1.  **Unit Tests (`tests/test_simulator.py`):**
    *   Verify that submitting team members in Admin View successfully populates SQLite `team_members` table.
    *   Verify that selecting a user, typing a Slack message, and hitting the endpoint puts a correctly resolved `UnifiedMessage` row in SQLite with `sender_mapped_name` equal to that user's name.
    *   Verify that submitting an Outlook email parses and strips any HTML and logs the unified email properly.
