# Task: Workload — Leave Marking + Reassignment Suggestions + Training/Newsletter Digest

## Context

Repo: `manager-assistant`. Server: `.venv/bin/python3 -m app.main`. Tests:
`.venv/bin/python3 -m pytest tests/`.

First read: `CLAUDE.md`, `app/database.py` (Task), `app/scheduler.py`,
`app/agent/tools.py`, `app/outbound.py`, the 7b workload view
(`app/static/dashboard/js/views/workload.js` or equivalent).

**This prompt was written ahead of time. If any detail contradicts the
current code, THE CODE WINS — adapt.**

**Why this feature:** Harry notices people-problems, not just task-problems.
Someone goes on leave → Harry suggests who picks up what (the MANAGER
approves — Harry never reassigns on his own); weekly, Harry suggests
trainings/reading tailored to what the team is actually struggling with.

**Project rules:** TDD; sim time only; LLM faked in tests; suggestions are
SUGGESTIONS — every mutation requires explicit manager approval; outbound
via `send_or_hold`; no new deps; `CLAUDE.md` untouched.

## Subproblem 0 — Spec file

`spec/feature_13_workload.md`: leave model, suggestion algorithm, approval
flow, digest job, test plan.

## Subproblem 1 — Leaves + reassignment suggestions

```
leaves: id, member_id, starts_on (date, sim), ends_on (date), reason
  (nullable), created_at
reassignment_suggestions: id, leave_id FK, task_id FK, from_member_id,
  to_member_id, rationale (Text), status ("suggested"|"approved"|"rejected"),
  created_at, decided_at (nullable)
```

- `POST /api/leaves`, `GET /api/leaves?active=true` (active = covers sim
  today), `DELETE /api/leaves/{id}`.
- On leave creation, generate suggestions DETERMINISTICALLY in code: for
  each of the member's open/in_progress tasks due within the leave window
  (or overdue), pick the least-loaded other member (fewest open tasks;
  exclude Harry, exclude anyone also on leave). Rationale via FLASH_MODEL
  one-liner ("Bob has the lightest queue and touched this project last
  week") with a deterministic fallback string.
- Approval: `POST /api/reassignments/{id}/approve` → task.assignee updated,
  Harry DMs old + new assignee via `send_or_hold`, timeline entry on the
  project entity ("Task X reassigned Alice→Bob during leave", cite none).
  `POST /api/reassignments/{id}/reject` → status only. NO auto-apply, ever.
- Harry DMs the manager when suggestions are created: "Alice is out
  Mon–Wed; 3 tasks land in that window — I've drafted reassignments on the
  workload page."
- Agent tools: `mark_leave(member_id, starts_on, ends_on, reason?)`,
  `list_reassignment_suggestions(status?)`, `approve_reassignment(id)` (its
  description warns: only on explicit manager instruction).

## Subproblem 2 — Weekly training/newsletter digest

- Scheduler job `weekly_digest` (weekly, Mon 09:30, catchup "once").
- Assemble signals in CODE: blocked tasks + blockage_reason strings,
  overdue clusters per member, high-frequency claim keywords, recent
  conflict descriptions.
- FLASH_MODEL: 2-4 suggestions, each `{"audience": member_id|"team",
  "suggestion": "...", "reason": "..."}` — grounded ONLY in the provided
  signals (validate audience ids in code; drop hallucinated ones).
- Store in a `digests` table (id, week_start UNIQUE, content JSON,
  created_at); DM manager a teaser via `send_or_hold`;
  `GET /api/digests?limit=4`.

## Subproblem 3 — Dashboard wiring

Workload view upgrades: leave badges on members ("out till Wed"), a "mark
leave" mini-form, the suggestions list with Approve/Reject buttons (confirm
dialog states the DM side-effects), latest digest rendered in a side card.

## Subproblem 4 — Tests (`tests/test_workload.py`, write FIRST, LLM faked)

1. Leave creation → suggestions only for tasks due inside the window or
   overdue; least-loaded member chosen; members on leave never suggested as
   targets; Harry's manager DM queued/sent.
2. Approve → task reassigned + both DMs + timeline entry; reject → nothing
   mutates; approving twice → 409.
3. Flash rationale failure → deterministic fallback rationale, flow intact.
4. Digest job (fake flash): row created with validated audiences,
   hallucinated member id dropped, manager teaser sent; UNIQUE week_start —
   re-run same week doesn't duplicate.
5. Full suite green; guard clean.

## Definition of done

- [ ] Spec written; suite green; guard clean
- [ ] Manual check: mark Alice on leave with 2 due tasks → suggestions on
      the workload page with sane rationales; approve one → simulator shows
      both DMs; trigger the digest job → readable suggestions. Paste the
      digest in your summary.
- [ ] Commit with a clear message
