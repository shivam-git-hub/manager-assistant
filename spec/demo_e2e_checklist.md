# Demo End-to-End Presentation Checklist & Manual Verification Playbook

This is the beat-by-beat checklist to confirm that every flow of the **Harry presentation script** works flawlessly. Execute this checklist sequentially against a freshly seeded instance of the project.

---

## Preparation Beat — Environment Seeding
*   **Action**: Execute the seed command inside the repository root:
    ```bash
    python3 -m app.seed_demo
    ```
*   **Expectation**: Output displays the printed ASCII/Text table with all 5 items marked **✓ (True)**.
*   **Verification**:
    | Item | Expected Observation | Pass / Fail |
    | :--- | :--- | :--- |
    | `open_conflict_exists` | Must be **✓ (True)** | [ ] Pass / [ ] Fail |
    | `phoenix_degraded` | Must be **✓ (Yellow/Red)** | [ ] Pass / [ ] Fail |
    | `held_pings_exist` | Must be **✓ (True)** | [ ] Pass / [ ] Fail |
    | `briefs_exist` | Must be **✓ (True)** | [ ] Pass / [ ] Fail |
    | `next_meeting_scheduled` | Must be **✓ (True)** | [ ] Pass / [ ] Fail |

---

## Beat 1 — "This is my team, seen through Harry"
*   **Setup**: Open simulator (`/`) on the left, dashboard (`/dashboard`, landing on `#/portfolio`) on the right.
*   **Action**: Look at the active portfolio on the dashboard.
*   **Verification**:
    | Element to Check | Expected Observation | Pass / Fail |
    | :--- | :--- | :--- |
    | **Atlas Project** | Status should be **Green** (Healthy) with several active and completed tasks. | [ ] Pass / [ ] Fail |
    | **Phoenix Project** | Status should be **Yellow** (Degraded) due to overdue deliverables and active conflicts. | [ ] Pass / [ ] Fail |
    | **Simulated Clock** | Topbar displays: **Thu 16 Jul 2026, 10:00 IST** (Calculated server-side). | [ ] Pass / [ ] Fail |

---

## Beat 2 — A Signal Arrives (Carol's Fix Deployed)
*   **Action 1**: Switch LEFT window (simulator) to **Carol Iyer (Frontend Developer)**.
*   **Action 2**: Select Slack Channel **#phoenix** on the left.
*   **Action 3**: Paste and send message:
    ```text
    Staging checkout flow is green again, retry fix deployed.
    ```
*   **Action 4**: Trigger the dream cycle on the simulator top bar or run background cron:
    ```bash
    curl -X POST http://localhost:3003/api/scheduler/tick
    ```
*   **Verification**:
    | Element to Check | Expected Observation | Pass / Fail |
    | :--- | :--- | :--- |
    | **Message Post** | Message lands in #phoenix with sender Carol Iyer. | [ ] Pass / [ ] Fail |
    | **Extraction Trace** | Background logs indicate claim extracted with U_CAROL as holder. | [ ] Pass / [ ] Fail |

---

## Beat 3 — The Trust Moment: Compiled Truth & Citations
*   **Action 1**: Open **Phoenix Project page** on the right (`#/project/project:phoenix`).
*   **Action 2**: Look at the updated **Compiled Truth** block.
*   **Action 3**: Hover your cursor over the newest timeline reference chip, e.g. `[T#]`.
*   **Verification**:
    | Element to Check | Expected Observation | Pass / Fail |
    | :--- | :--- | :--- |
    | **Compiled Text** | Harry's summary mentions that Carol Iyer's checkout fixes are now deployed on staging. | [ ] Pass / [ ] Fail |
    | **Citation Chip Hover** | Popup instantly displays: **Carol Iyer (Slack, #phoenix)**, exact timestamp, and raw message text. | [ ] Pass / [ ] Fail |

---

## Beat 4 — The Deadlock Discovery
*   **Action 1**: On the same Phoenix details page, look at the **Conflicts / Deadlocks** table.
*   **Action 2**: Click or hover over the claims inside the conflict row.
*   **Verification**:
    | Element to Check | Expected Observation | Pass / Fail |
    | :--- | :--- | :--- |
    | **Deadlock Surfaced** | Row clearly contrasts: Bob's Slack assertion (*"Sent document on Monday"*) and Alice's Outlook assertion (*"Never received schema document"*). | [ ] Pass / [ ] Fail |
    | **Overdue deliverable** | Task *"Review schema migration doc"* is shown in red under Alice's queue, marked **Blocked** or **Overdue**. | [ ] Pass / [ ] Fail |

---

## Beat 5 — Time Travel (+3 Days)
*   **Action 1**: On the simulator topbar, click the **+3 Days** time advancement action.
*   **Action 2**: Look at the simulator DMs feed and refresh the dashboard.
*   **Verification**:
    | Element to Check | Expected Observation | Pass / Fail |
    | :--- | :--- | :--- |
    | **Harry Slack DMs** | Harry has re-pinged Bob twice, then sent an escalation DM to your inbox (Shivam) informing you Bob went quiet. | [ ] Pass / [ ] Fail |
    | **Portfolio Page** | Phoenix has now degraded to **Red** (Critical). | [ ] Pass / [ ] Fail |
    | **Health Reasons** | Reasons detail: *"overdue schema review (now overdue by 4 days), unresolved high conflict, and no activity for 3 days."* | [ ] Pass / [ ] Fail |

---

## Beat 6 — Quiet Hours Release & Morning Briefs
*   **Action 1**: In the simulator outbound queue logs, find the night bug alert from Carol at 23:05.
*   **Action 2**: Advance Simulated time to **09:00 AM next morning**.
*   **Action 3**: Open the **Briefings View** (`#/briefs`) on the right dashboard.
*   **Verification**:
    | Element to Check | Expected Observation | Pass / Fail |
    | :--- | :--- | :--- |
    | **Safari bug released** | Carol's Safari alert is released from `"held"` and dispatches to Slack. | [ ] Pass / [ ] Fail |
    | **Morning Briefing** | Displays today's briefing detailing critical items, overdue deliverables, and today's schedule in three concise blocks. | [ ] Pass / [ ] Fail |

---

## Beat 7 — Ask Harry Anything
*   **Action 1**: Open the global chat dock on the bottom right of the screen.
*   **Action 2**: Ask Harry: `"What is blocking Phoenix and what should I do today?"`
*   **Action 3**: Ask Harry: `"Prep me for the go/no-go meeting tomorrow."`
*   **Verification**:
    | Element to Check | Expected Observation | Pass / Fail |
    | :--- | :--- | :--- |
    | **Response Factuality** | Harry responds using compiled truths, citing timeline points and current conflicts. | [ ] Pass / [ ] Fail |
    | **Pre-Meeting Prep** | Harry pulls attendees, active deliverables, and notes a list of outstanding design questions to bring up. | [ ] Pass / [ ] Fail |
    | **Tool Accordion Trace** | Collapsible trace logs show Harry queried `kb_search` or `get_entity` behind the scenes. | [ ] Pass / [ ] Fail |
