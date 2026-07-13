# Feature 10: Agent Harness, Tools Registry, and Dashboard Chat Persistence

This specification defines the custom agentic harness ("Harry"), a programmatic tool-calling loop modeled after Hermes, and the direct dashboard chat capability. It outlines the custom execution loop, deterministic tool integrations, budget guards, system prompt layers, and persistence layer.

---

## 1. Motivation

To act as an autonomous assistant, Harry must support free-form conversational interaction with managers on the dashboard while maintaining perfect factuality and capabilities to act. We avoid high-overhead frameworks like LangChain or LangGraph and implement a clean, lightweight, thread-safe, and deterministic agent loop from scratch.

This loop:
1.  **Observes and reasons** step-by-step using a high-fidelity system prompt combined with conversational history.
2.  **Calls tools** deterministically to query or update the project knowledge base (SQLite), send outbound communications via Slack/Outlook, or manage follow-ups.
3.  **Adheres to an iteration budget** to prevent infinite loops, rate limit exhaustions, or costly runaway LLM execution.
4.  **Enforces temporal safety** by relying strictly on the backend simulated clock.

---

## 2. Database Schema Changes

We register a new table to store chat history and tool trace logs for the direct dashboard chat.

### `chat_messages` Table
*   `id`: Integer, Primary Key, autoincrement.
*   `role`: String(20) (allowed: `"user"`, `"assistant"`).
*   `content`: Text (the message body sent by the user or responded by Harry).
*   `tool_trace`: Text, Optional (JSON serialized string recording tool calls made during the turn, e.g., `[{"name": "kb_search", "args": {...}, "result_preview": "..."}]`).
*   `created_at`: DateTime (timezone-naive representing IST, defaulting to `timeservice.now_ist()`).

---

## 3. Subsystem Components

```
┌────────────────────────────────────────────────────────┐
│                   POST /api/chat                       │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│             Harness (app/agent/harness.py)             │
│   (Compiles 3-Tier Prompt, Orchestrates Tool Loop)    │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼ (Loops via IterationBudget)
┌────────────────────────────────────────────────────────┐
│            Registry (app/agent/registry.py)            │
│       (Dispatches actions to tools safely)             │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│             Tools (app/agent/tools.py)                 │
│  (kb_search, get_entity, list_projects, send_slack...) │
└────────────────────────────────────────────────────────┘
```

### A. Thread-Safe Budget Guard (`app/agent/budget.py`)
Enforces a hard limit on the number of sequential LLM turns/tool executions within a single user message invocation.
*   **Default Budget**: 20 turns.
*   **Properties**:
    *   `consume()`: Decrements the budget. Raises an exception or returns false if exhausted.
    *   `refund()`: Restores a budget count (optional).
    *   `remaining()`: Returns current balance.

### B. Tool Registry (`app/agent/registry.py`)
Provides a declarative interface for defining, registering, and executing agent tools.
*   **Schema Schema**: OpenAI/Gemini function calling compatible format:
    ```json
    {
      "name": "tool_name",
      "description": "Prose explaining when/how to use this tool.",
      "parameters": {
        "type": "object",
        "properties": { ... },
        "required": [ ... ]
      }
    }
    ```
*   **Error Catching**: Handler exceptions must be caught and returned as string results (e.g. `"ERROR: <error details>"`), allowing the agent loop to observe the failure and correct itself instead of crashing the FastAPI worker.
*   **Context Passing**: Dispatches the active `Session` database handler safely to each tool execution.
*   **Output Clamping**: Limits execution returns to `8000` characters to protect LLM context length, truncating safely with a `"... [truncated]"` suffix.

### C. Tools Definitions (`app/agent/tools.py`)
Ten specific high-fidelity, prose-documented tools matching the todo_tool house style:
1.  `kb_search(q)`: Searches entity names, timeline summaries, and claim text for keyword matches.
2.  `get_entity(slug)`: Returns detailed payload for an Entity (compiled truth, timeline logs, active claims, conflicts).
3.  `list_projects()`: Lists projects with statuses, health score, and reasons.
4.  `list_tasks(project_id, assignee_id, status)`: Returns tasks filtered by inputs.
5.  `update_task(task_id, status, assignee_id, due_date)`: Modifies a task. Harry must only invoke this when the manager explicitly instructs him to do so.
6.  `get_conflicts(status)`: Returns unresolved/resolved conflicts.
7.  `send_slack_dm(member_id, text)`: Routes via outbound queue respects quiet hours; returns queued status & release time.
8.  `send_email(member_id, subject, body)`: Emails a team member, respecting quiet hours; routes via outbound queue.
9.  `create_followup(member_id, question, due_at, entity_slug)`: Explicitly schedules a new developer checkpoint.
10. `get_current_time()`: Returns current simulated datetime in IST.

### D. System Prompts (`app/agent/prompts.py`)
Built dynamically on each execution turn using three distinct layers:
*   **Stable Tier**: Harry's persona (helpful, blunt, concise, relies strictly on evidence, cites `[T#]` markers, asks before performing major updates like updating tasks, surfaces contradictions instead of resolving them). Provides usage instructions for the toolset.
*   **Context Tier**: Loaded from active DB tables (Roster of team members with IDs, email, Slack handles, and standard roles).
*   **Volatile Tier**: Active simulated datetime, active conflict counts, and degraded projects with details.

### E. Agent Harness Loop (`app/agent/harness.py`)
Executes the converse-and-execute loop:
1.  Receives user message and loads conversational history.
2.  Initializes `IterationBudget`.
3.  Compiles the Three-Tier system prompt.
4.  Loops:
    *   Invokes `client.chat(model=SMART_MODEL, messages=..., tools=...)`.
    *   If response requests tool/function calls:
        *   Appends the assistant's request verbatim to the message history: `{"role": "assistant", "content": ..., "tool_calls": [...]}`. This is a crucial requirement for Gemini compatibility (turns must be strictly symmetric).
        *   For each requested tool call:
            *   Executes the handler.
            *   Appends the result verbatim: `{"role": "tool", "tool_call_id": id, "name": name, "content": result}`.
        *   Decrements the budget. If exhausted, breaks loop and returns an "exhausted" warning message.
    *   If response has no tool calls:
        *   Breaks loop and returns the final assistant reply text.

---

## 4. REST API Routes

We register these new endpoints inside the backend:

### 1. `POST /api/chat`
Submit a message to Harry.
*   **Request Body**:
    ```json
    {
      "message": "What is the status of project Phoenix?"
    }
    ```
*   **Processing**:
    *   Loads the last 20 messages from `chat_messages` as history context.
    *   Runs `run_agent()`.
    *   Saves the user message and Harry's reply (with the serialized `tool_trace`) to the database.
*   **Response Body**:
    ```json
    {
      "reply": "Project Phoenix is in a Yellow state. [T5] Alice is working on the database migrations.",
      "tool_trace": [
        {
          "name": "kb_search",
          "args": {"q": "phoenix"},
          "result_preview": "..."
        }
      ],
      "created_at": "2026-07-12T14:30:00"
    }
    ```

### 2. `GET /api/chat/history?limit=50`
Retrieve chronological chat log.

### 3. `DELETE /api/chat/history`
Purges chat history for demo resets.

---

## 5. Test Plan (`tests/test_agent.py`)

1.  **Registry Operations**:
    *   Validate handler dispatch, error suppression (exception returned as `"ERROR: <details>"`), and 8000 character output truncation.
2.  **Budget Limits**:
    *   Set budget to 2, mock client to always return tool calls, and assert the loop terminates gracefully returning best-effort response.
3.  **End-to-End Converse Harness**:
    *   Supply mock responses (`tool_calls` then regular reply) and verify messages are correctly appended and final outcome cites timeline entries.
4.  **Symmetrical Conversational Context**:
    *   Ensure Gemini-compatible function call-then-response turn ordering is strictly adhered to in message payloads sent back to the LLM.
5.  **Simulated Time Integration**:
    *   Ensure current simulated IST time is dynamically populated in the Volatile prompt tier.
6.  **Outbound Constraints**:
    *   Ensure `send_slack_dm` executed via tools honors the quiet-hours queue end-to-end.
7.  **History Recalls**:
    *   Ensure consecutive `POST /api/chat` payloads correctly load previous exchanges in the context window.
