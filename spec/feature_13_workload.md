# Feature 13: Workload - Leave Marking + Reassignment Suggestions + Training/Newsletter Suggestions

## 1. Overview
Harry acts as an active assistant identifying people-centric problems. When team members are on leave, Harry suggests optimal reassignment plans for pending deliverables within the leave window. Additionally, Harry gathers signal metrics (blockage reasons, overdue task patterns, claims contradiction keywords) to generate automated weekly training and newsletter recommendations personalized for the team or individual engineers.

---

## 2. Database Schema

### A. Leaves Table (`leaves`)
Tracks scheduled or active time-off periods for team members.
```python
class Leave(Base):
    __tablename__ = "leaves"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    member_id: Mapped[str] = mapped_column(String(100), ForeignKey("team_members.id"))
    starts_on: Mapped[date] = mapped_column(Date) # IST sim date
    ends_on: Mapped[date] = mapped_column(Date) # IST sim date
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())
```

### B. Reassignment Suggestions Table (`reassignment_suggestions`)
Holds proposed reassignments drafted by Harry for manager review. No change is automatically applied without explicit approval.
```python
class ReassignmentSuggestion(Base):
    __tablename__ = "reassignment_suggestions"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    leave_id: Mapped[int] = mapped_column(Integer, ForeignKey("leaves.id"))
    task_id: Mapped[int] = mapped_column(Integer, ForeignKey("tasks.id"))
    from_member_id: Mapped[str] = mapped_column(String(100), ForeignKey("team_members.id"))
    to_member_id: Mapped[str] = mapped_column(String(100), ForeignKey("team_members.id"))
    rationale: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="suggested") # "suggested" | "approved" | "rejected"
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
```

### C. Digests Table (`digests`)
Stores compiled weekly training recommendations to avoid duplicate processing in the same week interval.
```python
class Digest(Base):
    __tablename__ = "digests"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    week_start: Mapped[date] = mapped_column(Date, unique=True) # Week start date (Monday), unique
    content: Mapped[str] = mapped_column(Text) # JSON string representation
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())
```

---

## 3. Core Behaviors

### Subproblem 1 — Leaves & Reassignment Pipeline

#### A. Creating a Leave & Automatic Generation of Suggestions
1. **Endpoint**: `POST /api/leaves`
   * Parameters: `member_id`, `starts_on` (Date), `ends_on` (Date), `reason` (Optional).
2. **Deterministic Suggestion Solver**:
   * Identifies all tasks assigned to `member_id` where:
     * `status` is NOT `"completed"`.
     * `due_date` lands in the interval `[starts_on, ends_on]` OR is already overdue (`due_date < starts_on`).
   * For each identified task, determine the target candidate to take over.
     * Candidate must NOT be the person on leave.
     * Candidate must NOT be Harry (`U_HARRY`).
     * Candidate must NOT be anyone else who is on leave during the *overlap* of the task's due date (or if overdue, overlaps the starts_on).
   * From the eligible candidate roster, choose the **least-loaded member**:
     * Calculate active load count (`status != 'completed'`) for each candidate as of the simulated now.
     * Pick the candidate with the absolute fewest total active tasks.
     * Tie-breaker: Alphabetical sorting of member names (deterministic).
   * **Rationale Extraction**:
     * Submit signals to `FLASH_MODEL` to output a neat 1-sentence rationale (e.g. *"Bob is currently free and recently contributed to this repository"*).
     * If the LLM call fails, falls back gracefully to a deterministic fallback string: `"Assigned to [Target] as they currently have the lightest task queue."`
3. **Manager Alert**:
   * Harry formats an outbound notification via `send_or_hold` (Slack DM to the manager):
     * *"Alice is out Mon–Wed; 3 tasks land in that window — I've drafted reassignments on the workload page."*

#### B. Approval Flow
1. **Approval Endpoint**: `POST /api/reassignments/{id}/approve`
   * Modifies suggestion `status` to `"approved"` and `decided_at = timeservice.now_ist()`.
   * Reassigns `task.assignee_id = suggestion.to_member_id` inside the database.
   * Compiles timeline entry on the project Entity: `"Task '{task.title}' reassigned {from_member_id} -> {to_member_id} during leave"`.
   * Dispatches symmetrical outbound notification DMs to both old and new assignee via `send_or_hold`:
     * To Old Assignee: *"Hi [Alice], while you are away, '[Task]' has been reassigned to [Bob] to keep progress on track."*
     * To New Assignee: *"Hi [Bob], '[Task]' has been reassigned to you from [Alice] during their leave window."*
   * Re-approvals of already decided suggestions return `409 Conflict`.
2. **Rejection Endpoint**: `POST /api/reassignments/{id}/reject`
   * Sets status to `"rejected"`, does not mutate task fields.

#### C. Active Leaves Query
* `GET /api/leaves?active=true`: Filters leaves active on simulated today (`starts_on <= today_date <= ends_on`).

---

## 4. Subproblem 2 — Weekly Training & Newsletter Suggestions

### A. Scheduler Job: `weekly_digest`
* Runs at `09:30` on simulated Mondays.
* Check-up mechanism prevents running twice in the same calendar week starting Monday.

### B. Input Signal Extraction
Gathers core signals directly from SQL schemas:
1. Blocked task descriptions and their `blockage_reason` strings.
2. Overdue task tallies per assignee.
3. Common keywords inside unresolved Claims contradiction descriptions.

### C. LLM Processing Tier
* Feeds gathered signals to `FLASH_MODEL` with strict schemas.
* Outputs 2-4 items, validated to ensure that `audience` is either `"team"` or maps to a valid member ID in the roster.
* Halls of hallucinated or unrecognized IDs are dropped cleanly.
* Teaser is sent to the manager via `send_or_hold`: *"Hi Shivam, I have compiled this week's technical digests and trainings tailored to our team's current blockers."*

---

## 5. Agent Tools
The agent harness is extended with the following conversational actions:
* `mark_leave(member_id: str, starts_on: str, ends_on: str, reason: Optional[str] = None)`: Sets leaves and initiates proposals.
* `list_reassignment_suggestions(status: Optional[str] = None)`: Inspects proposals.
* `approve_reassignment(suggestion_id: int)`: Direct conversational approvals.

---

## 6. Verification and Test Plan
1. `tests/test_workload.py` will validate suggestion generation, load-balancing heuristics, and transaction gates.
2. Verify visual rendering inside the dashboard views.
