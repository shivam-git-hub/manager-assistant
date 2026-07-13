# Task: Meetings — MoM Ingestion, Action-Item Propagation, Calendar, Pre-Meeting Briefs

## Context

Repo: `manager-assistant`. Server: `.venv/bin/python3 -m app.main`. Tests:
`.venv/bin/python3 -m pytest tests/`.

First read: `CLAUDE.md`, `app/kb/models.py` + `app/kb/extraction.py`
(entities of type "meeting" already allowed), `app/kb/synthesis.py`,
`app/scheduler.py` (one-shot jobs — pre-meeting briefs use them),
`app/agent/harness.py` + `app/agent/tools.py`, `app/outbound.py`.

**This prompt was written ahead of time. If any detail contradicts the
current code, THE CODE WINS — adapt.**

**Why this feature:** meetings are where manager reality gets decided. Paste
minutes-of-meeting → Harry turns them into a meeting page, extracts action
items into real tasks, propagates decisions into the affected projects' KBs,
and preps the manager before the next one.

**Project rules:** TDD; sim time only; LLM faked in tests; deterministic
collectors — code creates the meeting entity/rows, LLM only extracts;
outbound via `send_or_hold`; no new deps; `CLAUDE.md` untouched.

## Subproblem 0 — Spec file

`spec/feature_12_meetings.md`: tables, MoM pipeline, propagation rules,
calendar API, pre-meeting-brief job, test plan.

## Subproblem 1 — Tables + calendar API

```
meetings: id, title, starts_at (sim IST), ends_at (nullable), attendees
  (Text JSON list of member ids), project_id (nullable FK), mom_raw (Text,
  nullable — pasted minutes), mom_message_id (nullable FK unified_messages),
  status ("scheduled"|"completed"|"cancelled"), created_at
action_items: id, meeting_id FK, task_id (nullable FK tasks), description,
  owner_member_id, due_date (nullable), created_at
```

- `POST /api/meetings` (create scheduled), `GET /api/meetings?from=&to=`
  (calendar range, sim dates), `GET /api/meetings/{id}` (meeting +
  action items + entity slug), `PATCH /api/meetings/{id}` (status, times).
- Creating a meeting also creates entity `meeting:<id>` (name = title) via
  `get_or_create_entity` and schedules a one-shot `pre_meeting_brief`
  scheduler job at `starts_at - 30 min` (skip if already past).

## Subproblem 2 — MoM ingestion pipeline

`POST /api/meetings/{id}/mom` `{text}`:

1. Store `mom_raw`; ALSO insert a `unified_messages` row (source "manual",
   direction "inbound", sender = manager, content = the MoM) so every
   downstream citation has a real message to point at; set `mom_message_id`.
2. FLASH_MODEL (json_mode) extracts:
   `{"summary": "...", "decisions": ["..."], "action_items": [{"description",
   "owner_member_id", "due_date": "YYYY-MM-DD"|null,
   "project_slug": "project:..."|null}], "project_slugs": ["project:..."]}`
   — candidate rosters/entity lists assembled in code, same validation
   discipline as Step 4a (drop unknown owners/slugs, never trust the LLM).
3. Persist: each action item → `action_items` row AND a real `tasks` row
   (assignee = owner, due_date, title = description, project = the item's
   project or the meeting's); timeline entry on `meeting:<id>` citing
   `mom_message_id`; **propagation** — for each referenced project entity:
   a timeline entry ("Decided in <title>: <decision>", citing the MoM
   message) + a claim per decision (holder = manager, kind "fact",
   weight 1.0).
4. Meeting `status="completed"`; mark the meeting entity dirty so the next
   dream cycle synthesizes its compiled truth.
5. Harry DMs each action-item owner via `send_or_hold`:
   "From <title>: <description> (due <date>)".

## Subproblem 3 — Pre-meeting brief (scheduler job) + agent tools

- `pre_meeting_brief` handler: at T-30min, assemble per attendee-relevant
  content: the linked project's compiled truth, open conflicts, attendees'
  open/overdue tasks, unanswered followups; SMART_MODEL rewrites (same
  fallback-to-deterministic rule as the morning brief); DM the MANAGER via
  `send_or_hold`; store as a timeline entry on the meeting entity.
- New agent tools: `list_meetings(from?, to?)`, `get_meeting(id)`,
  `create_meeting(title, starts_at, attendees, project_id?)` — registered in
  the Step 6 registry so the manager can do all of this from chat.

## Subproblem 4 — Dashboard: calendar + meeting page

- New `#/meetings` view (7a design system): week list grouped by day (sim
  "today" highlighted), each meeting → `#/meeting/<id>`.
- Meeting page: title/time/attendees, MoM paste box (POSTs to the pipeline,
  then re-renders), extracted action items with task links, decisions,
  pre-meeting brief if generated, compiled truth + timeline via the entity
  page component (REUSE the 7b component — citations hover-resolve to the
  MoM message).

## Subproblem 5 — Tests (`tests/test_meetings.py`, write FIRST, LLM faked)

1. Create meeting → meeting entity exists; one-shot brief job scheduled at
   starts_at-30min; meeting in the past → no job.
2. MoM happy path (fake extractor): action_items + tasks created with
   owners/due dates; project timeline entry + claims exist and cite the MoM
   message id; meeting completed; owner DMs queued/sent via send_or_hold.
3. Validation: unknown owner id / unknown project slug dropped; malformed
   JSON → mom_raw stored, nothing else created, no crash.
4. Pre-meeting brief handler (fake smart model): DM to manager exists,
   timeline entry on meeting entity; LLM failure → deterministic fallback
   still delivered.
5. Calendar range query returns only meetings in [from, to].
6. Full suite green; guard clean.

## Definition of done

- [ ] Spec written; suite green; guard clean
- [ ] Manual check with real key: create a meeting for sim-tomorrow, paste a
      realistic 10-line MoM with 2 action items for Alice and Bob touching
      project phoenix → tasks appear, phoenix timeline gains cited entries,
      owners get DMs; advance clock to T-30min → manager receives the brief.
      Paste the brief in your summary.
- [ ] Commit with a clear message
