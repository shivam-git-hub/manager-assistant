# Manager Assistant Agent (Harry) - Integration & Simulator Portal

Welcome to the **Manager Assistant Agent (Harry)** proof-of-concept project. This workspace contains a custom FastAPI server, a structured SQLite database, and an interactive **High-Fidelity Integration Simulator** built to mimic Slack and Outlook for presentation to senior management.

---

## 🚀 Quick Start (How to Run)

We use `uv` for lightning-fast Python dependency resolution. The project environment has been pre-configured with a `.venv` folder containing all dependencies.

To start the server:

```bash
# 1. Run the FastAPI development server
.venv/bin/python3 app/main.py
```

The server will initialize an SQLite database at `data/db.sqlite`, pre-populate it with sample employee profiles, and start serving on:
👉 **`http://localhost:3003`** (or configured `PORT` env var)

If you are running this on a remote server behind our Nginx proxy, check the configured subpath gateway routes or simply access it via your allocated public address.

---

## 🛠️ Testing the Suite

To run our automated unit and integration test suite (verifying user resolution mapping, Slack webhooks, Outlook HTML stripping, and duplicate message protection):

```bash
.venv/bin/python3 -m pytest tests/
```

---

## 🎨 Inside the High-Fidelity Simulator

When you open **`http://localhost:3003`** in your browser, you will see a unified portal structured for professional demos:

### 1. Impersonation Context Switcher (Top Right)
Senior management can watch you switch active users dynamically (e.g., from *Shivam (Manager)* to *Alice Developer (Backend Engineer)* to *Bob Product (Designer)*).
*   Switching profiles immediately updates the simulator environment.
*   Sending a message under a profile records that specific employee's raw Slack ID or Outlook email.
*   The backend automatically maps this raw identifier to their database name at ingestion!

### 2. Slack Workspace (Aubergine Sidebar)
An authentic Slack clone featuring:
*   Standard sidebar with `#general`, `#project-updates`, and `#alerts` channels.
*   A reactive chat workspace with user initials avatars, headers, and timestamps.
*   Submitting a message fires a POST to `/api/integrations/slack/webhook` mimicking the actual Slack Event API payloads.

### 3. Outlook Email (Three-Pane Layout)
A clean Outlook clone featuring:
*   Left folder list pane.
*   Middle card feed showing incoming emails.
*   Right-hand Reading Pane to display email headers and bodies.
*   **New Message Composer:** Open the composer to write email updates with custom HTML. The backend uses a built-in standard Python `html.parser` to strip out styling and tables, converting them into clean text/markdown before storing them.
*   Submitting a mail fires a POST to `/api/integrations/outlook/mock-ingest` mimicking a Microsoft Graph API message resource.

### 4. Admin Desk
Directly read and write to SQLite database tables:
*   **Team Member Form:** Add new employees to populate the master roster.
*   **Projects Tracking:** High-level project metadata and statuses.
*   **Tasks Ledger:** Mapped deliverables, assignees, and due dates.

### 5. Direct Chat
A lightweight direct chat interface to message Harry directly (simulating the web dashboard chat integration).

### 6. Reset DB Button (Top Bar)
Wipe all messages, projects, and tasks instantly during a live demo to start a fresh presentation sequence, while keeping your team members list intact.

---

## 📂 Codebase Structure

```
manager-assistant/
├── spec/                       # Architecture and feature specification markdown files
├── app/                        # FastAPI source code
│   ├── config.py               # Timezones (IST), default ports, paths
│   ├── database.py             # SQLAlchemy models (SQLite base)
│   ├── main.py                 # FastAPI initialization and static file mounting
│   ├── static/
│   │   └── index.html          # High-fidelity Vue.js + Tailwind CSS SPA Simulator
│   ├── kb/
│   │   └── schemas.py          # Pydantic schemas for data serialization
│   └── integrations/
│       ├── team.py             # CRUD endpoints for employees
│       ├── slack.py            # Slack webhook receiver & validator
│       ├── outlook.py          # Outlook mock email receiver & HTML-to-markdown engine
│       └── unified.py          # Unified messages log and direct dashboard APIs
├── tests/                      # Testing suite (pytest)
│   ├── conftest.py             # Clean test DB file lifecycle management
│   └── test_connectors.py      # Assertions for webhooks, parsing, & mapping
├── requirements.txt            # Core python requirements
└── NOTES.md                    # Symlink helper to specs
```
