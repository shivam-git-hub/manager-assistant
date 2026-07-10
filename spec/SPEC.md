# SPEC.md - High-Level Architecture Specification

This is the central specification document for the **Manager Assistant Agent**. It outlines the high-level architecture, module design, and structural conventions of the project.

---

## 1. System Architecture

The project consists of a lightweight FastAPI backend containing three primary layers:

```
┌────────────────────────────────────────────────────────┐
│               Direct Chat Dashboard (UI)               │
└───────────────────────────┬────────────────────────────┘
                            │ (REST / WebSockets)
                            ▼
┌────────────────────────────────────────────────────────┐
│                   FastAPI Core Service                 │
│                                                        │
│  ┌──────────────────────┐    ┌──────────────────────┐  │
│  │   Slack Integration  │    │  Outlook Integration │  │
│  └──────────────────────┘    └──────────────────────┘  │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│               Custom Agent Harness (Harry)             │
│   (State Loop, Tool Executor, LLM Prompting, Parser)   │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│              Knowledge Base Layer (gbrain)             │
│      (Structured SQLite Storage + Metadata Search)     │
└────────────────────────────────────────────────────────┘
```

### A. FastAPI Core Service
*   Acts as the central web server.
*   Exposes endpoints for the direct chat dashboard, Slack Webhooks, and Outlook Graph API integration.
*   Manages async background tasks (e.g., polling for status updates, checking emails).

### B. Custom Agent Harness (Harry)
*   An LLM orchestration loop written from scratch.
*   Uses a systematic system prompt, short-term session memory, and deterministic tool execution.
*   Parses LLM outputs (e.g., custom tool calls or text messages) and executes the actions safely.

### C. Knowledge Base Layer (gbrain-inspired)
*   Stores project states, task lists, user mappings, team rosters, and historical updates.
*   Backbone database will be lightweight (SQLite) combined with simple metadata matching or vector/text search when required.
*   Keeps a highly organized representation of what team member is working on what project, current progress, blockages, and next steps.

---

## 2. Directory Structure

This represents the planned layout of the project:

```
manager-assistant/
├── spec/                       # Specification files
│   ├── SPEC.md                 # High-level architecture (this file)
│   ├── notes_and_instructions.md # Core constraints, rules, and workspace logs
│   └── [feature_files].md      # Individual specs for each feature
├── app/                        # FastAPI application source code
│   ├── main.py                 # FastAPI application entry point
│   ├── config.py               # Global settings (ports, API keys, timezone)
│   ├── database.py             # SQLite/SQLAlchemy schema and initialization
│   ├── agent/                  # Custom agent harness
│   │   ├── __init__.py
│   │   ├── harness.py          # Custom agent main loop
│   │   ├── prompt.py           # Core agent system prompt ("Harry")
│   │   └── tools.py            # Agent tools (e.g., search KB, update task, send slack)
│   ├── kb/                     # Knowledge Base layer (gbrain-inspired)
│   │   ├── __init__.py
│   │   ├── manager.py          # Reading and writing project statuses
│   │   └── schemas.py          # Data structures for tasks, updates, and MoM
│   ├── integrations/           # Third-party integrations
│   │   ├── __init__.py
│   │   ├── slack.py            # Slack API client & webhook handler
│   │   └── outlook.py          # Outlook REST API/Graph client
│   └── dashboard/              # Dashboard frontend SPA (HTML/CSS/JS)
├── tests/                      # Testing suite (pytest)
│   ├── conftest.py
│   ├── test_agent.py
│   ├── test_kb.py
│   └── test_integrations.py
├── requirements.txt            # Project Python dependencies
├── NOTES.md                    # Symlink or short helper file to spec/notes
└── README.md                   # Project overview and run instructions
```

---

## 3. Initial Features Roadmap

We will draft specific `.md` specification files inside `spec/` for each feature as we tackle them. The two proposed starting features are:

1.  **Feature 01: High-Level Project Status Tracking (KB Storage)**
    *   *File:* `spec/feature_01_project_kb.md`
    *   *Objective:* Design how project states, metadata, and task lists are modeled in SQLite, and how the custom agent updates or queries them from chat.
2.  **Feature 02: Slack Active Polling (Reminders & Status Gathering)**
    *   *File:* `spec/feature_02_slack_polling.md`
    *   *Objective:* Design the background scheduler that detects overdue updates, commands the agent to ping relevant team members on Slack, parses their replies, and updates the KB.

---

## 4. Coding & Quality Guidelines

*   **Zero Framework Overhead:** Implement basic LLM APIs directly via the `google-genai` or `openai` SDK.
*   **Strong Typing:** Use Pydantic models for request/response bodies, database schemas, and tool specifications.
*   **Timezone Enforcement:** Explicitly use `timezone('Asia/Kolkata')` for any date-time parsing, storing, or scheduling.
