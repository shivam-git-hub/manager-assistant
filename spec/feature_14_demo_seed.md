# Feature 14: Demo Seed Scenario + End-to-End Pass

## 1. Overview
To ensure a high-fidelity, polished, and believable presentation for senior management, a deterministic database-seeding utility `python -m app.seed_demo` is created. This utility takes a clean instance of the platform and populates it with weeks of plausible corporate communications, meetings, timeline evidence, and unresolved disputes (claims deadlocks) by sequentially advancing the simulated IST clock and running standard ingestion pipelines.

---

## 2. Seed Scenario Cast & Projects

### A. Roster of Team Members
1. **Shivam** (`U_SHIVAM`): General Manager (the user / manager). Role containing `"Manager"`.
2. **Alice Sharma** (`U_ALICE`): Backend Lead.
3. **Bob Verma** (`U_BOB`): Product / Integrations Engineer.
4. **Carol Iyer** (`U_CAROL`): Frontend Engineer.

### B. Portfolio of Projects
1. **Phoenix Project** (`project_id: 1`):
   * *Subject*: Payment gateway revamp and migrations.
   * *Health*: Yellow on startup (due to an unresolved high-severity conflict and an overdue schema review deliverable). Degrades to Red after advancing simulated time +3 days.
2. **Atlas Project** (`project_id: 2`):
   * *Subject*: Internal analytics and reports.
   * *Health*: Healthy (Green) with complete tasks and ongoing normal updates.

---

## 3. Scenario Timeline & Ingestion Milestones

The scenario runs over a 14-day history, starting at **Thursday, 2026-07-02 09:00 IST** and landing at **Thursday, 2026-07-16 10:00 IST**. To generate valid citation chains, messages are processed through real ingestion paths (`/api/slack/webhook` or `/api/outlook/webhook` or `/api/unified/ingest`), and standard dream-cycle synthesis is triggered at periodic checkpoints.

### Timeline Sequence

#### Epoch Day 0: Thursday, 2026-07-02
*   **10:00 IST**: Sim clock anchored here.
*   **Communications**:
    *   Slack #phoenix: Carol asks for project spec. Alice replies linking the payments design doc.
    *   Slack #atlas: Bob starts the analytics project structure.
*   **Dream Cycle**: run synthesis on Phoenix and Atlas.

#### Epoch Day 5: Thursday, 2026-07-07
*   **09:30 IST**: Advance clock.
*   **Meetings & Minutes**:
    *   Past meeting "Phoenix Kickoff & Design Review" (held Day 5 10:00 - 11:00).
    *   MoM processed via the propagation pipeline:
        *   Carol is assigned task: `"Draft checkout UI mocks"` (due Day 8).
        *   Bob is assigned task: `"Define API schema contracts"` (due Day 10).
        *   Alice is assigned task: `"Review schema migration doc"` (due Day 14).
*   **Dream Cycle**: Run synthesis and claims extraction.

#### Epoch Day 8: Sunday, 2026-07-10 (Staging Bug Block)
*   **11:00 IST**: Advance clock.
*   **Communications**:
    *   Slack #phoenix: Carol reports: `"Staging payment checkout is throwing 500 errors on load. I am blocked on Carol's checkout mocks."`
    *   Slack #phoenix: Alice: `"Looking into it, looks like database migration issue."`
*   **Tasks**: Task `"Draft checkout UI mocks"` marked in-progress.
*   **Dream Cycle**: Run synthesis (Phoenix degrades to yellow temporarily due to block).

#### Epoch Day 10: Tuesday, 2026-07-12 (Bug Resolved)
*   **10:00 IST**: Advance clock.
*   **Communications**:
    *   Slack #phoenix: Alice: `"Fixed database staging migration, checkout mocks can be tested now."`
    *   Slack #phoenix: Carol: `"Tested! Checkout flow looks perfect on staging."`
*   **Tasks**: Task `"Draft checkout UI mocks"` marked completed.
*   **Dream Cycle**: Run synthesis (Phoenix recovers to green).

#### Epoch Day 14: Thursday, 2026-07-14 (The Deadlock Spark)
*   **15:00 IST**: Advance clock.
*   **The Deadlock Messages**:
    *   **Slack #phoenix (Bob, 15:02 IST)**: `"Sent the schema doc to Alice on Monday, waiting on her review. We are on track."`
    *   **Outlook Email (Alice to Shivam, next day 09:15 IST)**: `"I still haven't received any schema doc from Bob — Phoenix migration is blocked on it."`
*   **Tasks**: Alice's deliverable `"Review schema migration doc"` (due today) sits pending/blocked.
*   **Dream Cycle**: Run synthesis. Since Bob claims he "sent the schema doc" and Alice claims "she never received the schema doc", a high-severity claims conflict is generated organically. Phoenix project degrades to **Yellow** due to the overdue block and unresolved conflict.

#### Epoch Day 15: Friday, 2026-07-15 (The 11 PM Outbound Block)
*   **23:05 IST**: Advance clock.
*   **Communications**:
    *   Slack #phoenix: Carol reports: `"Seeing checkout checkout integration bug on safari browser."`
    *   Harry wants to DM/alert the manager, but the clock sits at **23:05 IST (Quiet Hours)**.
    *   Downstream outbounds are correctly queued as `"held"` in `outbound_queue` scheduled for release on Friday morning.

#### Epoch Day 16: Thursday, 2026-07-16 (Demo Kickoff State)
*   **10:00 IST**: Advance clock.
*   **Meetings**: "Phoenix Go/No-Go Alignment" scheduled for tomorrow Friday, 2026-07-17 11:00 IST (validates pre-meeting briefing trigger).
*   **Dream Cycle**: Final run.

---

## 4. REST Seed Endpoint (`POST /api/demo/seed`)
A new endpoint is added to trigger this complete setup programmatically.
*   **Payload**: `{ "confirm": true }`
*   **Logic**:
    1. Truncates all tables: `leaves`, `reassignment_suggestions`, `digests`, `meetings`, `action_items`, `tasks`, `projects`, `unified_messages`, `chat_messages`, `attributed_claims`, `timeline_entries`, `conflicts`, `briefs`, `followups`, `outbound_queue`, `scheduled_jobs`.
    2. Resets sim clock anchor.
    3. Executes the chronological seed sequence using real mock-message channels.
    4. Prints and returns a checklist object verifying exact demo-ready parameters.

---

## 5. Verification Test & Checklist
*   `tests/test_seed_checklist.py`: Tests the integrity of the checklist parser and reset scripts against mock data, ensuring no wall-clock leaks or schema violations occur.
*   `spec/demo_e2e_checklist.md`: Manual playbook to confirm that the live presentation steps match the seeded scenario perfectly.
