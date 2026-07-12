# Feature 04: Backend Simulated-Time Service + LLM Model Config

This specification defines how the Manager Assistant Agent (Harry) implements a single, unified backend authority for simulated time (to support time-travel, virtual crons, and follow-up timing) and handles configuration of LLM models.

---

## 1. Motivation

A robust project management assistant requires a reliable mechanism for scheduling follow-ups, triggering alerts, and modeling project status histories. Today, our simulator clock runs entirely in the frontend. If we want Harry to check for task updates that are "overdue by 3 days" or send a reminder according to a virtual schedule, the backend must be the single source of truth for "now". 

By shifting the authority of "now" to the backend with an anchored simulated clock, we:
*   Make all background tasks and database timestamps respect the same simulated timeline.
*   Prevent clock drift and mismatches between the simulator frontend and backend.
*   Allow the simulation of time-travel (e.g., advancing the clock by days/weeks) to test agent logic end-to-end.

---

## 2. Anchored Simulated Clock Model

The clock operates on an "anchored" principle where we do not pause or manually tick the time in a loop. Instead, time continues to flow naturally at a 1:1 rate, but it is offset from real-world time by an anchored shift.

We store two values in `data/sim_clock.json`:
1.  `anchor_sim_time`: A naive IST datetime representing the virtual simulated starting point.
2.  `anchor_real_time`: A float UTC epoch representing the exact real-world instant the simulated time was set.

To compute the current simulated time at any instant:
$$\text{current\_sim\_time} = \text{anchor\_sim\_time} + (\text{real\_now\_epoch} - \text{anchor\_real\_time})$$

### Core Advantages:
*   Time flows naturally on the backend without active polling or background tick loops.
*   "Advancing" or "setting" the clock simply shifts the anchor values.
*   The clock is highly accurate and doesn't drift.

---

## 3. JSON State File Format

The state is persisted to a local JSON file (`data/sim_clock.json` by default, or overridden via `SIM_CLOCK_PATH`). This ensures clock state survives database resets, server restarts, and simulator reloads.

```json
{
  "anchor_sim_time": "2026-07-12T12:00:00",
  "anchor_real_time": 1783857600.0
}
```

*   **Atomic Writes**: To prevent state corruption during rapid concurrent access, updates are written atomically: write to a temporary file in the same directory, then rename/replace using `os.replace`.
*   **Initialization**: If the JSON file is missing, it will initialize anchored to the current real IST time.
*   **Thread Safety**: A `threading.Lock` guards all read-modify-write operations on the JSON file.

---

## 4. API Endpoints

We expose several endpoints to allow the simulator frontend (or automated testing tools) to inspect and control the clock:

### A. `GET /api/time`
Returns the current simulation state.
*   **Response Payload**:
    ```json
    {
      "sim_time_ist": "2026-07-12T12:05:00",
      "epoch": 1783857900.0,
      "utc_iso": "2026-07-12T06:35:00Z",
      "anchor_sim_time": "2026-07-12T12:00:00",
      "anchor_real_time": 1783857600.0
    }
    ```

### B. `POST /api/time/set`
Manually sets the simulated time to a specific naive IST datetime.
*   **Request Body**:
    ```json
    {
      "datetime": "2026-07-15T10:30:00"
    }
    ```
*   **Response**: New `GET /api/time` payload.

### C. `POST /api/time/advance`
Moves the simulated clock forward by a specified delta.
*   **Request Body**:
    ```json
    {
      "days": 1,
      "hours": 2,
      "minutes": 30
    }
    ```
*   **Validation**: Any subset of fields may be provided, but the total duration must be positive and non-zero. Failing to meet this returns a `422 Unprocessable Entity` status.
*   **Response**: New `GET /api/time` payload.

### D. `POST /api/time/reset`
Resets the simulated clock to align exactly with real-world time.
*   **Response**: New `GET /api/time` payload.

---

## 5. Rewiring of Wall-Clock Reads

To guarantee consistency, we replace all raw `datetime.now()` or `datetime.now(IST)` calls with our central `timeservice` calls.

1.  **Database Column Defaults**:
    *   In `app/database.py`, the `created_at` and `updated_at` properties of the `Project` table will use `default=lambda: timeservice.now_ist()` and `onupdate=lambda: timeservice.now_ist()`.
2.  **Dashboard/Direct Chat Ingestion**:
    *   In `app/integrations/unified.py`, incoming message timestamps are resolved using `timeservice.now_ist()`.
    *   Task completion datetime (`completed_at`) is set to `timeservice.now_ist()`.
3.  **Slack Ingestion Webhook**:
    *   In `app/integrations/slack.py`, falls back to `timeservice.now_epoch()` when processing missing message timestamps.
4.  **Outlook Ingestion**:
    *   In `app/integrations/outlook.py`, falls back to `timeservice.now_ist()` on invalid or unparseable `receivedDateTime`.

---

## 6. LLM Model Configuration

As we prepare to integrate the core LLM execution loop, we will establish model settings in `app/config.py` following this resolution order:
1.  Environment variables.
2.  An optional `config.json` file at the repository root.
3.  Sensible hardcoded defaults.

### Model Parameters:
*   `SMART_MODEL`: Default `"gemini-2.5-pro"` (for high-level reasoning and planning).
*   `FLASH_MODEL`: Default `"gemini-2.5-flash"` (for low-latency operations).
*   `GEMINI_API_KEY`: API key retrieved solely from environment.

A `config.json.example` will be provided, and `config.json` is added to `.gitignore`.

---

## 7. Test Plan

We implement a dedicated suite at `tests/test_timeservice.py` to verify correctness:

1.  **Initialization**: Verify that starting the service without a state file creates one anchored to actual real IST time.
2.  **Setting Time**: Set the time, confirm `now_ist()` matches, and assert it continues ticking forward naturally.
3.  **Clock Advancement**: Set, advance by days/hours, and verify accurate datetime calculations.
4.  **ISO / Epoch Agreement**: Ensure localized Unix epochs and UTC ISO strings correctly align with naive IST (IST = UTC+5:30).
5.  **API client integration**: Test `GET /api/time`, `POST /api/time/set`, `POST /api/time/advance`, and `POST /api/time/reset`. Test validation on `advance` to reject zero or negative offsets.
6.  **Ingestion validation**: Assert that new UnifiedMessages ingested via Slack and direct chat respect the mock time when a message timestamp is omitted.
7.  **No Wall-Clock Leaks**: Build a scanner test that checks `app/**/*.py` to guarantee no raw `datetime.now(` or `time.time()` exists outside of `app/timeservice.py`.
