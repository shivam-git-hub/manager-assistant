# Notes and Instructions - Project Management Assistant Agent

This file serves as a persistent record of the core rules, architectural constraints, and user preferences for our custom Manager Assistant Agent.

---

## 1. Core Directives

*   **No Heavy Frameworks:** Do NOT use LangChain, LangGraph, or other heavy, opinionated agent frameworks. We are building the agent harness from scratch to keep it lean, simple, and deterministic.
*   **Write Specs First:** Never write any code for a new feature without first drafting, discussing, and finalizing the corresponding specification `.md` file under the `spec/` directory.
*   **No Code Without Confirmation:** Do NOT start writing code for any feature until the user ("Shivam") explicitly confirms and approves the finalized specification.
*   **Iterative TDD Loop:** Once a feature specification is approved, write tests first (or alongside code), implement the feature, verify it works, and only then proceed to the next iteration.
*   **Timezone:** Shivam operates in **IST (Asia/Kolkata, UTC+5:30)**. All schedules, updates, reminders, and cron jobs must use IST. Ensure server UTC times are properly converted.

---

## 2. Key Architectural Decisions

*   **Language & Backend:** Python with FastAPI.
*   **Agent Harness:** Custom harness inspired by Hermes (agent loop, clean system prompt, JSON output or tool-calling, step-by-step reasoning).
*   **Knowledge Base (KB):** Structured memory inspired by `gbrain` (designed to keep track of project statuses, schedules, deliverables, meeting minutes, and interactions).
*   **Integrations:**
    1.  **Slack:** For pinging team members, gathering status updates, reassigning tasks, and sending manager alerts.
    2.  **Outlook:** For tracking email communication, calendar schedules, and processing Minutes of Meetings (MoM).
    3.  **Direct Chat Dashboard:** A clean, lightweight web-based interface.
*   **UI Theme:** Clean, lightweight, peaceful pastel-themed user interface.

---

## 3. General Project Metadata

*   **GitHub Username:** `shivam-git-hub`
*   **Assistant Name:** "Harry" (you are Harry!)
*   **User Name:** "Shivam"

---

## 4. Work Log & Future Notes

*(This section will be updated as the project progresses to track major decisions, notes, and milestones.)*
