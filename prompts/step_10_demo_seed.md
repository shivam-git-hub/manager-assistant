# Task: Demo Seed Scenario + End-to-End Pass

## Context

Repo: `manager-assistant`. Server: `.venv/bin/python3 -m app.main`. Tests:
`.venv/bin/python3 -m pytest tests/`.

First read: `CLAUDE.md` (demo storyline section), `spec/demo_script.md`
(the beat-by-beat script this seed must SERVE — every beat needs its data
in place), `app/integrations/slack.py` + `outlook.py` webhook shapes,
`app/kb/extraction.py`, `app/scheduler.py`, `POST /api/messages/reset`.

**This prompt was written ahead of time. If any detail contradicts the
current code, THE CODE WINS — adapt.**

**Why this feature:** one command takes a blank machine to
"demo-ready-at-beat-1". The seed must produce a world that looks LIVED-IN —
weeks of plausible history — so the audience believes the compiled truths.

**Project rules:** the seed goes through REAL ingestion paths (webhooks +
`/api/kb/process` + `/api/kb/dream`) — no direct KB inserts for message-
derived data (the citation chain must be genuine). Needs a real
GEMINI_API_KEY at seed time. Sim time only. `CLAUDE.md` untouched.

## Subproblem 0 — Spec file

`spec/feature_14_demo_seed.md`: the cast, the scenario timeline, the seed
mechanics, the E2E checklist.

## Subproblem 1 — The scenario (`app/seed_demo.py`)

`python -m app.seed_demo` (also `POST /api/demo/seed` guarded by a confirm
flag). Steps:

1. **Reset**: wipe via the reset endpoint logic + clear followups, briefs,
   meetings, leaves, chat, scheduler job state (keep builtin jobs).
2. **Clock**: set sim time to a fixed anchor — **Thursday 2026-07-16
   10:00 IST** (a weekday mid-morning; weekend quiet-hours must not eat the
   demo).
3. **Cast**: Shivam (Manager), Alice Sharma (Backend Lead), Bob Verma
   (Product/Integrations), Carol Iyer (Frontend). Projects: **Phoenix**
   (payments revamp — the star) and **Atlas** (internal analytics — the
   healthy background project).
4. **History** (~35-45 messages over sim T-14d → T-0, stamped by moving the
   sim clock forward between webhook batches — NEVER backdating by editing
   rows): standups, an Outlook thread on Phoenix API contracts, Atlas
   humming along, Carol blocked 2 days then unblocked, a completed design
   review meeting with MoM (via the Step 8 pipeline).
5. **The deadlock**, T-2d: Bob (Slack): "Sent the schema doc to Alice on
   Monday, waiting on her review." Alice (Outlook, next day): "I still
   haven't received any schema doc from Bob — Phoenix migration is blocked
   on it." Plus a blocked task "Review schema migration doc" assigned to
   Alice, due T-1d (overdue → health degradation is real).
6. **The 11 PM message**, T-1d 23:05: Carol reports a staging bug at night
   → whatever Harry wants to send gets HELD (quiet hours beat).
7. **Process**: run extraction + dream cycle at the same sim checkpoints a
   live system would have hit (at minimum after each day's batch) so
   supersession chains and the Bob/Alice conflict form organically.
8. **Tasks**: ~10 across both projects — mix of done/in-progress/blocked/
   overdue so portfolio + workload views look real.
9. **Meeting**: "Phoenix go/no-go" scheduled T+1d 11:00 (pre-meeting brief
   beat).
10. **Print a beat checklist** at the end: expected state (open conflict
    exists, Phoenix yellow/red, held ping in queue, N briefs, next meeting)
    with ✓/✗ per item — the presenter runs this right before going live.

## Subproblem 2 — Seed verification test + E2E checklist

- `tests/test_seed_checklist.py`: UNIT-test the checklist helper functions
  against a hand-built tiny DB (LLM faked) — the full seed itself is NOT run
  in tests (needs a real key).
- Add `spec/demo_e2e_checklist.md`: the manual pass — every beat of
  `spec/demo_script.md` executed once against a fresh seed, with expected
  observations and a pass/fail column to fill in.

## Subproblem 3 — Polish sweep

Run the whole demo once yourself and fix what grates: empty states, loading
flickers, timestamps rendering as raw ISO, chat dock scroll, conflict copy.
List every fix in your summary.

## Definition of done

- [ ] Spec + checklist docs written; suite green; guard clean
- [ ] `python -m app.seed_demo` from a wiped `data/` → checklist all ✓
- [ ] Full manual E2E pass against `spec/demo_script.md` — attach the filled
      checklist and screenshots of: portfolio, Phoenix page with hover
      citation open, conflicts panel, morning brief, chat answer
- [ ] Commit with a clear message
