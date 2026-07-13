# Task: Dashboard Deep Views — Project Detail (hover citations), Conflicts, Workload, Briefs, Chat Dock

## Context

Repo: `manager-assistant`. Server: `.venv/bin/python3 -m app.main`. Tests:
`.venv/bin/python3 -m pytest tests/`.

First read: `CLAUDE.md`, `app/static/dashboard/` (7a shell — reuse its
design system, router, and fetch helpers EXACTLY; do not restyle),
`app/kb/api.py` (entity page payload), `app/main.py` chat endpoints (Step 6),
`GET /api/briefs` (Step 5), `GET /api/messages` (message lookup for
citations).

**This prompt was written ahead of time. If any detail contradicts the
current code, THE CODE WINS — adapt.**

**Why this feature:** the demo money-shots live here — hovering a `[T#]`
marker in the compiled truth and seeing the ORIGINAL message is the moment
the audience trusts Harry. Everything else (conflicts, workload, briefs,
chat) makes Harry feel like a full product.

**Project rules:** same as 7a — CDN Vue, plain JS/CSS files, hand-built
design system, no new deps, backend additions TDD'd, `CLAUDE.md` untouched.

## Subproblem 0 — Spec update

Extend `spec/feature_11_dashboard.md` with the 7b views and any new
endpoints.

## Subproblem 1 — Project detail (`#/project/<slug>`) — THE view

Data: `GET /api/kb/entities/{slug}` in one call.

- **Compiled truth block** at top (serif, comfortable line height). Parse
  `[T\d+]` markers in JS: render each as a small superscript chip. On hover
  (or click on touch), a popover shows the timeline entry summary +
  `happened_at` + the SOURCE MESSAGE: fetch the message by
  `source_message_id` (add `GET /api/messages/{id}` if it doesn't exist —
  TDD it), render sender name, channel, timestamp, full content, and a
  Slack/Outlook badge. This popover must feel instant — prefetch all cited
  messages when the page loads.
- **Timeline** below: newest-first list (time, summary, source badge;
  expandable detail). Append-only — no edit affordances at all.
- **Claims panel**: active claims grouped by holder, weight rendered subtly
  (e.g. "reportedly" style badge under 0.7). A toggle reveals superseded
  claims grayed out with a "superseded →" link that scrolls to the
  replacement.
- **Conflicts strip**: open conflicts on this entity — severity, the two
  claims side by side with holder names, description. Buttons "Resolve" /
  "Dismiss" (with note) → `PATCH /api/kb/conflicts/{id}` — the MANAGER
  resolves, never Harry (surface this in the confirm dialog copy).
- **Tasks + milestones**: tasks for the linked project (via entity
  `ref_id`), grouped by status, overdue ones flagged.

## Subproblem 2 — Conflicts view (`#/conflicts`)

All conflicts across entities (`GET /api/kb/conflicts`), open first,
severity-sorted; each row links to its entity page; resolve/dismiss inline.
Empty state: "No open conflicts — Harry is watching."

## Subproblem 3 — Workload view (`#/workload`)

Per team member (exclude Harry): open/in-progress/blocked/overdue task
counts, escalated followups targeting them, last-heard-from time (newest
inbound message). Simple bar-per-member (CSS widths, no chart lib). This is
read-only until Step 9 adds leave/reassignment.

## Subproblem 4 — Briefs inbox (`#/briefs`)

`GET /api/briefs?limit=7` — newest first; today's brief expanded, prior ones
collapsed. Render the brief content as light markdown (bold + bullets only —
20-line renderer, no library).

## Subproblem 5 — Harry chat dock

- Collapsible dock, bottom-right, on EVERY dashboard view. History from
  `GET /api/chat/history`; send via `POST /api/chat`; "Harry is thinking…"
  while awaiting; render reply with the same mini-markdown + `[T#]` chips
  (hover popovers work here too — reuse the component; resolve entity from
  the chip's timeline entry).
- Tool trace: a discreet "used 3 tools ▸" expander per assistant message
  showing tool names + args (audit affordance, demo talking point).
- Clear-history button (calls the DELETE endpoint) tucked in a menu.

## Subproblem 6 — Tests (backend additions only, `tests/test_dashboard.py`)

1. `GET /api/messages/{id}` (if added): exists → full message; missing → 404.
2. Any other new aggregate endpoints: happy path + empty DB.
3. Full suite green; guard clean.

## Definition of done

- [ ] Spec updated; suite green; guard clean
- [ ] Manual check (real key helps): run the Step 4b deadlock seed →
      dream cycle → open the project page: compiled truth renders with
      chips, hover shows Bob's actual Slack message; conflict strip shows
      the high-severity pair; ask the chat dock "what's blocking phoenix?"
      → cited answer. Screenshots in your summary.
- [ ] Commit with a clear message
