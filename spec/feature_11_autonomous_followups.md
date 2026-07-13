# Feature 11: Autonomous Follow-ups - Harry's Two-Tier Heartbeat

## 1. Overview
Currently, `Followup` rows inside the system are created manually, either via the `POST /api/followups` endpoint or the conversational agent's `create_followup` tool. However, Harry lacks the ability to autonomously initiate follow-ups or chase down missed commitments, pending conflict nudges, or project health degradations without human prompting.

This specification introduces a robust, efficient **Two-Tier Autonomous Heartbeat**:
1. **Tier 1 — Triage (Flash Tier):** A lightweight, deterministic query gathers all actionable signals (blocked/overdue tasks, unmet commitments, open conflicts, and gone-quiet team members) and packages them alongside Harry's recent memory notes into a compact situation report. This report is checked by a cheap `FLASH_MODEL` to answer one binary question: *Does anything need autonomous action right now?*
2. **Tier 2 — Action (Smart Tier):** If and only if Tier 1 signals `action_needed: True`, Harry triggers his full-fidelity `run_agent` loop (using the `SMART_MODEL`) with a specific heartbeat directive to take precise actions (create follow-ups, ping conflicts, or escalate) and records a persistent memory note of his intervention.

This architecture ensures high frequency without massive token overhead, enforces quiet hours, prevents duplicate pings, and operates fully within the simulated time-travel model.

---

## 2. Database Schema

### A. Agent Notes Table (`agent_notes`)
A lightweight, persistent memory store for Harry to track recent actions, avoid duplicate operations, and maintain conversational continuity.
```python
class AgentNote(Base):
    __tablename__ = "agent_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: timeservice.now_ist()
    )
    kind: Mapped[str] = mapped_column(String(50))  # e.g., "followup_decision", "observation", "escalation"
    subject_ref: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )  # e.g., "person:U_BOB", "conflict:3", "project:phoenix", "followup:5"
    content: Mapped[str] = mapped_column(Text)
```

---

## 3. Core Behaviors

### Subproblem 1 — Situation Report Collector (`app/agent/situation.py`)
A pure Python deterministic utility `build_situation(db) -> dict` aggregates all active environmental signals that could justify an autonomous action. It applies the following collection logic:

1. **Unmet Commitments / Blockers:**
   * Query `AttributedClaim` entries with `kind` as `"commitment"` or `"blocker"` that are currently active (`superseded_by_id IS NULL`).
   * Filter for those where the `holder_id` has not sent any new inbound message since the claim was extracted (`claimed_at`).
   * Filter where `simulated_now - claimed_at > 24 hours` (default threshold).
2. **Conflicts Needing a Nudge:**
   * Query open `Conflict` rows (`status="open"`).
   * Filter out conflicts where an active open `Followup` already references either conflicting claim's holders for the same project/entity.
3. **Degraded Projects:**
   * Query all projects.
   * Highlight projects with health rating of `"yellow"` or `"red"` along with their stated reasons.
4. **Overdue / Blocked Tasks:**
   * Query `Task` entries where `status="blocked"` OR (`status != "completed"` and `due_date < simulated_today`).
5. **Gone-Quiet Owners:**
   * Identify team members with one or more open commitments who have not sent any inbound channel communications in over 48 simulated hours.
6. **Already-Open Follow-ups (For De-duplication):**
   * List all currently open `Followup` items (including target assignee, question, associated entity, and current ping count) so Tier 2 action loops can accurately de-duplicate.
7. **Recent Memory Notes:**
   * Fetch the last 30 entries from `agent_notes` to inject a rolling temporal memory.

**Return Structure:**
```python
{
    "has_signals": bool,  # True if items 1-5 yield any actionable signals
    "digest": str,         # Compact, markdown-formatted situation text for the LLM
    "open_followups": list,
    "recent_notes": list
}
```

---

### Subproblem 2 — Tier 1 Triage Loop (`app/agent/heartbeat.py`)
Deterministic short-circuiting:
1. Call `build_situation(db)`. If `has_signals` is `False`, skip the LLM call entirely and immediately return:
   ```json
   {"action_needed": false, "reason": "No active signals warranting follow-up."}
   ```
2. Otherwise, invoke the `FLASH_MODEL` (with `json_mode=True`) with a structured system instruction and the situation digest + notes.
   * **Question:** *Does anything need autonomous action right now?* Say NO if everything actionable is already covered by an open follow-up or a recent action note.
   * **Schema Return:**
     ```json
     {
         "action_needed": bool,
         "reason": "Explain briefly why action is or is not needed",
         "focus": ["person:U_BOB", "conflict:3"]  # List of target keys
     }
     ```
   * Parse-Failure Guard: If parsing fails, fall back to `action_needed = False` to prevent spamming.

---

### Subproblem 3 — Tier 2 Action Loop (`app/agent/heartbeat.py`)
1. If Tier 1 triage indicates `action_needed: True`:
   * Prepare a specialized, system-driven `run_agent` session (using the `SMART_MODEL`).
   * Frame the goal around a clear heartbeat directive:
     ```text
     HEARTBEAT: Your triage flagged action may be needed.
     Reason: {reason}
     Focus items: {focus}
     
     Here is the current situation report:
     {digest}

     Your goal is to address the flagged issues. Take action:
     - Create follow-ups for anyone who owes an update or is silent on an open commitment.
     - Ping both holders of any open conflict that has no follow-up yet.
     - Escalate to the manager if required by standard escalation thresholds.
     
     DO NOT duplicate any follow-up that is already open.
     Once done, you MUST call record_note summarizing your interventions.
     ```
   * Execute `run_agent(db, prompt, history=[])`. The agent will invoke its tools and write a note detailing what it accomplished.

---

## 4. De-duplication & Idempotency Rules
To prevent Harry from spamming channels or creating multiple identical follow-ups, we enforce defensive checks at multiple levels:
1. **Digest Context:** Tier 2 has full sight of existing open follow-ups.
2. **Agent Memory:** Harry consults `agent_notes` recording prior actions.
3. **Hard Duplication Guard:** The `create_followup` tool handler is hardened to block creations where there is an existing **active** (open) `Followup` for the same `target_member_id` and same `entity_slug` or closely matching question. If found, the tool returns a message indicating `"already following up on this item"`.

---

## 5. Time-Travel & Virtual Scheduler Integrations
1. **Heartbeat Registration:** Registered with simulated interval `7200` seconds (every 2 simulated hours). Uses `catchup_policy="once"`.
2. **Multi-Day Drift Repair:** Currently, the virtual scheduler evaluates `run_followup_check` based on `timeservice.now_ist()`, and the job uses `catchup_policy="once"`. When jumping time by 3 days, only the final moment gets evaluated—missing the ping-ping-escalation progression.
   * **Fix:** Modify `run_followup_check(db, now=None)` to accept an explicit evaluation timestamp.
   * **Scheduler Registration:** Register with `catchup_policy="every"`, routing the virtual tick time `vt` into `run_followup_check(db, now=vt)`. This ensures that as simulated time leaps, the schedule checks and fires sequential follow-up escalations organically for every hour bypassed.

---

## 6. Endpoints
* `POST /api/heartbeat/run`: Force runs the heartbeat (Triage + Action) and returns execution results (JSON).
* `GET /api/agent/notes?limit=30`: Retrieves a chronological list of agent memory notes.

---

## 7. Verification and Testing Plan
1. **Unit and Integration Tests (`tests/test_heartbeat.py`)**:
   * Mock `FLASH_MODEL` and `SMART_MODEL` returns using a fake client.
   * Test `build_situation` under different conditions (no signals vs. open conflicts vs. overdue commitments).
   * Test Tier 1 short-circuiting when `has_signals` is false.
   * Test Tier 2 agent loop trigger when triage signals `True`.
   * Assert hard duplication prevents twin follow-up creations.
   * Test multi-day time travel, verifying ping -> escalation progresses correctly across sequential catchup ticks.
   * Assert quiet hours are end-to-end respected (messages held in `outbound_queue`).
