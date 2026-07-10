# Feature 01: Project Status Tracking (Knowledge Base)

This specification defines how our manager assistant agent (Harry) models, stores, and queries the status of projects, tasks, and team members in its Knowledge Base (KB).

---

## 1. Objectives

*   Model project statuses, task lists, and team roles in a structured SQLite database.
*   Allow the custom agent (Harry) to read and write status updates directly via standard database queries wrapped as Python tools.
*   Support tracking of blockages, next steps, and update history.

---

## 2. Database Schema (gbrain-inspired)

We will use a lightweight SQLite database managed via SQLAlchemy. The initial schema consists of four tables:

### A. `projects` Table
Tracks high-level projects being managed.
*   `id`: INTEGER (Primary Key)
*   `name`: VARCHAR(100) (Unique, e.g., "Time Tracker Integration")
*   `description`: TEXT
*   `manager_id`: VARCHAR(100) (Slack/Outlook ID of the manager)
*   `status`: VARCHAR(20) (e.g., "active", "completed", "on_hold")
*   `created_at`: DATETIME (IST)
*   `updated_at`: DATETIME (IST)

### B. `team_members` Table
Tracks team members, their roles, and integration handles.
*   `id`: VARCHAR(100) (Primary Key, e.g., Slack User ID or email)
*   `name`: VARCHAR(100) (Display name, e.g., "Shivam")
*   `role`: VARCHAR(100) (e.g., "Developer", "Designer", "Product Manager")
*   `slack_handle`: VARCHAR(100)
*   `outlook_email`: VARCHAR(150)
*   `timezone`: VARCHAR(50) (Default "Asia/Kolkata")

### C. `tasks` Table
Tracks specific deliverables or action items within a project.
*   `id`: INTEGER (Primary Key)
*   `project_id`: INTEGER (Foreign Key to `projects.id`)
*   `title`: VARCHAR(255)
*   `description`: TEXT
*   `assignee_id`: VARCHAR(100) (Foreign Key to `team_members.id`)
*   `status`: VARCHAR(20) (e.g., "pending", "in_progress", "completed", "blocked")
*   `blockage_reason`: TEXT (NULL if not blocked)
*   `due_date`: DATE (IST)
*   `completed_at`: DATETIME (IST)

### D. `status_updates` Table
A ledger of historical status reports and pings (the KB "brain" feed).
*   `id`: INTEGER (Primary Key)
*   `source`: VARCHAR(50) (e.g., "slack_ping", "outlook_email", "dashboard_chat", "mom_upload")
*   `project_id`: INTEGER (Foreign Key to `projects.id`, optional)
*   `task_id`: INTEGER (Foreign Key to `tasks.id`, optional)
*   `reporter_id`: VARCHAR(100) (Foreign Key to `team_members.id`)
*   `raw_content`: TEXT (The actual message fed into the KB)
*   `summary`: TEXT (The agent-extracted structured update)
*   `created_at`: DATETIME (IST)

---

## 3. Agent KB Interface (Tools)

To enable the custom agent to interact with this KB, we will expose the following Python functions as tools within the agent's harness:

1.  `kb_get_project_status(project_id_or_name: str) -> dict`
    *   Retrieves project details, active tasks, team assignments, and latest updates.
2.  `kb_update_task_status(task_id: int, status: str, blockage_reason: str = None) -> dict`
    *   Updates a task's status and logs the change.
3.  `kb_add_status_update(source: str, reporter_id: str, raw_content: str, project_id: int = None, task_id: int = None) -> dict`
    *   Appends a raw text update to the ledger and updates associated task statuses.

---

## 4. Verification & Testing Plan

*   **Unit Tests (`tests/test_kb.py`):**
    *   Test database creation and migration using SQLite in-memory databases.
    *   Test standard CRUD operations for projects, team members, and tasks.
    *   Test cascading updates (e.g., when a task is updated, check if the status update ledger is populated correctly).
*   **Integration Tests:**
    *   Verify that timezones are correctly written and returned as IST.
