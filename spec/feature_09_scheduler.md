# Feature 09: Virtual Scheduler, Follow-up Engine, Project Health, and Morning Brief

This specification defines the backend-driven virtual scheduler and follow-up engine that drive automated actions, project health scoring, and summarized briefs in synchronization with the backend simulated-time clock.

---

## 1. Motivation

To act as an autonomous agent, Harry must do more than respond to incoming HTTP webhooks; he must proactively execute background tasks (like checking for task follow-ups, evaluating project health, releasing delayed outbound messages, and composing daily reports). 

To support seamless timeline manipulation and testing:
1.  **Virtual Chronology**: Background jobs must execute against *simulated time*, not wall-clock time. Jumping simulated time forward by 3 days must trigger all jobs that came due in that virtual gap, in sequential order, with realistic catch-up behavior.
2.  **Autonomous Interactions**: Follow-ups must automate developers' pings, checking if they have replied, and escalating to managers when they go silent.
3.  **Visual Portfolios**: Project health must degrade dynamically according to factual project blockers and overdue task parameters, providing immediate visual feedback to senior managers.

---

## 2. Database Schema Changes

We register three new tables to handle scheduler, follow-up, and brief logs.

### A. `scheduled_jobs` Table
Stores virtual recurring and one-shot cron jobs.
*   `id`: Integer, Primary Key, autoincrement.
*   `job_type`: String(100), unique (for built-in seeds) or searchable.
*   `payload`: Text, Optional (JSON configuration parameters).
*   `next_due_at`: DateTime (sim IST when the job is next scheduled to run).
*   `interval_seconds`: Integer, Optional (frequency interval, `null` indicates one-shot execution).
*   `enabled`: Boolean, default `True`.
*   `last_run_at`: DateTime, Optional (sim IST).
*   `catchup_policy`: String(20), default `"once"` (allowed: `"once"` or `"every"`).
*   `created_at`: DateTime (sim IST default).

### B. `followups` Table
Tracks autonomous follow-up questions sent by Harry.
*   `id`: Integer, Primary Key, autoincrement.
*   `entity_slug`: String(150), Optional.
*   `task_id`: Integer, ForeignKey (`tasks.id`), Optional.
*   `target_member_id`: String(100), ForeignKey (`team_members.id`).
*   `question`: Text (the question text sent to the developer).
*   `created_by`: String(100) (e.g., `"harry"` or a manager's `team_members.id`).
*   `due_at`: DateTime (sim IST when the next response or follow-up is expected).
*   `status`: String(20), default `"open"` (allowed: `"open"`, `"answered"`, `"escalated"`, `"cancelled"`).
*   `ping_count`: Integer, default `0`.
*   `last_ping_at`: DateTime, Optional (sim IST).
*   `answer_message_id`: Integer, ForeignKey (`unified_messages.id`), Optional.
*   `created_at`: DateTime (sim IST default).

### C. `briefs` Table
Caches daily synthesized morning briefs compiled for managers.
*   `id`: Integer, Primary Key, autoincrement.
*   `brief_date`: String(10), Unique (formatted `"YYYY-MM-DD"`).
*   `content`: Text (the LLM-synthesized prose summary).
*   `created_at`: DateTime (sim IST default).

### D. Project Model Extensions (`projects` table)
We add columns to the `Project` model to log dynamic health grades:
*   `health`: String(10), default `"green"` (allowed: `"green"`, `"yellow"`, `"red"`).
*   `health_reasons`: Text, Optional (JSON serialized string array of grading notes).
*   `health_updated_at`: DateTime, Optional (sim IST).

*A migration checker in `init_db()` runs `ALTER TABLE projects ADD COLUMN...` to prevent database schema mismatches on older files.*

---

## 3. Built-In Jobs & Catch-Up Policy

Five default cron jobs are seeded inside `init_db()` during startup:

1.  **`dream_cycle`**: Runs every 1 hour (`interval_seconds = 3600`).
    *   *Catchup Policy*: `"once"`. Even on a 3-day jump, we only run the dream cycle once to prevent token waste and redundant synthesis.
2.  **`quiet_release`**: Runs every 15 minutes (`interval_seconds = 900`).
    *   *Catchup Policy*: `"once"`. Automatically flushes and releases held pings.
3.  **`followup_check`**: Runs every 1 hour (`interval_seconds = 3600`).
    *   *Catchup Policy*: `"once"`. Executes automated ping detection, answers checking, and manager escalations.
4.  **`health_eval`**: Runs every 6 hours (`interval_seconds = 21600`).
    *   *Catchup Policy*: `"once"`. Computes project score metrics.
5.  **`morning_brief`**: Runs daily at `09:00` IST (`interval_seconds = 86400`).
    *   *Catchup Policy*: `"every"`. If the clock jumps by 3 days, Harry compiles **three** separate briefs (one for each missed morning) to preserve chronological realism for testing.

---

## 4. Job Orchestration & The Tick Algorithm

The central virtual loop is executed via `tick(db)` in `app/scheduler.py`:

1.  **Execution Loop**:
    *   Fetch all `enabled == True` jobs with `next_due_at <= timeservice.now_ist()`, ordered ascending by `next_due_at`.
    *   For each job, run the matching handler in our `JOB_HANDLERS` dictionary.
    *   Wrap execution inside `try-except` blocks. If any handler fails, log the exception, set `job.last_run_at = timeservice.now_ist()`, and **continue** to the next job to prevent wedging the scheduler.
2.  **Rescheduling**:
    *   If a job is a **one-shot** (i.e., `interval_seconds is None`), set `enabled = False`.
    *   If the job is **recurring**:
        *   If `catchup_policy == "once"`: Move `next_due_at` repeatedly by `interval_seconds` until it sits strictly in the future. Execute the handler exactly **once**.
        *   If `catchup_policy == "every"`: Evaluate how many times the job was missed in the elapsed interval. Execute the handler **each** missed occurrence (with its corresponding simulated virtual time, mock-anchored), advancing `next_due_at` sequentially.

### Real-Time Live Tick Hook:
To ensure live simulated time flows correctly when the browser remains open, we register a background loop in `lifespan()` ticking every 30 real seconds. In addition, when time-travel endpoints are hit (`/api/time/set`, `/api/time/advance`), we trigger `scheduler.tick()` immediately at the end of the transaction to fire due follow-ups instantly!

---

## 5. Follow-Up Engine Logic

The `followup_check` job automates developer checkpoints deterministically:

1.  **Answer Detection**:
    *   For any `"open"` followup with `ping_count >= 1`:
    *   Scan for inbound `UnifiedMessage` records from the `target_member_id` sent to the mutual Slack DM channel (`DM_` containing both IDs) *newer* than the followup's `last_ping_at`.
    *   If found, mark status `"answered"` and store `answer_message_id`.
2.  **Automated Pinging**:
    *   For any `"open"` followup with `due_at <= now_ist()`:
    *   If `ping_count == 0` OR (`ping_count >= 1` and `now - last_ping_at >= 24 hours`):
        *   Dispatch the question as Harry to the target's Slack DM using `send_or_hold("slack", ...)`.
        *   Set `last_ping_at = timeservice.now_ist()` and increment `ping_count` by 1.
3.  **Manager Escalation**:
    *   If `ping_count >= 2` and `now - last_ping_at >= 24 hours` (i.e., 24 hours have elapsed after the second unanswered ping):
        *   Set status `"escalated"`.
        *   Send an escalation alert to the first manager found in the `team_members` database (role containing `"Manager"`):
            > "Harry: I've asked {name} twice about '{question}' with no reply — you may want to step in." (via `send_or_hold`).

---

## 6. Project Health Evaluation

The `health_eval` job computes project health grades using a deterministic score rubric:
*   Overdue open tasks: **+2 points** each.
*   Blocked tasks (`status == "blocked"`): **+2 points** each.
*   Open conflicts associated with the project's entity slug: **+3 points** each.
*   No inbound activity on the project's entity for $> 3$ simulated days: **+2 points**.
*   Escalated follow-ups linked to the project: **+2 points** each.

### Grading Scale:
*   **`green`**: `0 - 2` points.
*   **`yellow`**: `3 - 5` points.
*   **`red`**: $\ge 6$ points.

### Degrade Notifications:
If a project's health degrades (e.g., `green` $\rightarrow$ `yellow` or `yellow` $\rightarrow$ `red`), Harry automatically DMs the manager with the specific bulleted grading reasons. Improvement back to a healthier state is updated silently.

---

## 7. Daily Morning Brief

The `morning_brief` job runs daily at `09:00` simulated IST:

1.  **Held Message Release**: Invokes `/api/outbound/release` to dispatch queued messages.
2.  **Data Assembly**: Gathers overnight metrics:
    *   Outbound messages released overnight.
    *   Open project conflicts.
    *   List of projects sorted by worst health first with reasons.
    *   Pending follow-ups awaiting developer answers.
    *   Tasks due today or overdue.
3.  **Synthesis (SMART_MODEL)**: Instructs the Smart model to rewrite the compiled text into a crisp, bulleted briefing note with zero fluff.
4.  **Graceful Fallback**: If the LLM call fails, falls back instantly to the plain text format so the brief is guaranteed to persist.
5.  **Delivery**: Saves the output as a `Brief` record and Slack-DMs the manager a brief 2-line teaser notification.

---

## 8. API Routes

1.  `POST /api/followups`: Create a new follow-up.
2.  `GET /api/followups?status=open`: Queries open follow-ups.
3.  `PATCH /api/followups/{id}`: Cancel or resolve a follow-up.
4.  `GET /api/briefs?limit=7`: Fetch recent briefings.
5.  `GET /api/briefs/{date}`: Fetch a briefing by ISO date.
6.  `POST /api/scheduler/tick`: Manually trigger a virtual tick check.

---

## 9. Test Plan

We write `tests/test_scheduler.py` to validate:
1.  Virtual scheduling (disabling one-shots, repeating recurrences).
2.  Multi-day clock catchups (once vs daily-brief sequences).
3.  Resiliency when a handler raises an unhandled Exception.
4.  The follow-up lifecycle (auto-pings, DMs generation, answers tracking, and manager escalations).
5.  Factual health evaluations, grade degradation alerts, and silence on repeat grades.
6.  Morning briefs synthesis and fallback rendering.
