# Task: Project Tracker Dashboard — Shell, Design System, Portfolio View

## Context

Repo: `manager-assistant`. Server: `.venv/bin/python3 -m app.main` (port
3003). Tests: `.venv/bin/python3 -m pytest tests/`.

First read: `CLAUDE.md`, `app/static/index.html` (the simulator — you are NOT
touching it, but copy its Vue-3-CDN loading pattern), `app/kb/api.py`
(everything you render comes from these endpoints), `app/main.py` (static
mounting), `app/timeservice.py` endpoints.

**This prompt was written ahead of time. If any detail contradicts the
current code, THE CODE WINS — adapt.**

**Why this feature:** the manager-facing product surface. The simulator is
the "world"; this dashboard is what Shivam actually demos. 7a builds the
shell + design system + portfolio view; 7b adds the deep views (project
detail with hover citations, conflicts, workload, briefs, chat dock).

**Project rules:** Vue 3 via CDN, NO build tooling, NO npm. Code split
across plain files — `app/static/dashboard/` with `index.html`, `css/*.css`,
`js/*.js` (components as plain objects with template strings, loaded via
ordinary `<script>` tags in dependency order). Keep `CLAUDE.md` untouched.
Backend changes in this step are minimal (one route + maybe one aggregate
endpoint) — TDD those; the frontend is checked manually.

## Design system — MUST NOT look AI-generated

This is an explicit product requirement. Hand-build a small design system in
`css/theme.css` + `css/components.css` and use it everywhere:

- **Banned**: purple/indigo gradients, glassmorphism, emoji-as-icons in
  headings, uniform `border-radius: 12px` cards with drop shadows on
  everything, Inter-on-white with blue-600 accents, centered hero layouts.
- Instead: a warm paper background (e.g. `#faf8f4`), near-black ink text,
  ONE muted accent (deep green or rust — pick one), a serif display font for
  headings (Georgia stack is fine — no webfont downloads), system sans for
  body, hairline borders (`1px solid #e4ded2`) instead of shadows, tight
  spacing scale (4/8/12/20/32), small-caps section labels.
- Health colors: green/yellow/red as small solid dots + text label, not
  giant colored cards.
- Density over whitespace: this is a working tool for a manager, closer to
  Linear/a broadsheet than a marketing page.

## Subproblem 0 — Spec file

`spec/feature_11_dashboard.md`: route, file layout, view list (7a + 7b
scope split), design tokens, API dependencies per view, test plan (backend
bits only).

## Subproblem 1 — Route + shell

- `GET /dashboard` serves `app/static/dashboard/index.html` (same pattern as
  the simulator route; static assets under `/static/dashboard/...`).
- Shell layout: left sidebar nav (Portfolio, Projects, Conflicts, Workload,
  Briefs — 7b views can be stubs that say "coming in 7b"), top bar with:
  product name ("Harry — Project Tracker"), current sim time (poll
  `GET /api/time` every 5s, display "Mon 13 Jul, 09:42 IST"), open-conflict
  count badge (poll `GET /api/kb/conflicts?status=open`).
- The sim time in the top bar is READ-ONLY here (the simulator owns the
  clock controls). It must visibly update after a time jump — the demo
  switches between the two tabs constantly.
- Simple hash router in `js/router.js` (30 lines, no dependency):
  `#/portfolio`, `#/project/<slug>`, `#/conflicts`, `#/workload`, `#/briefs`.

## Subproblem 2 — Portfolio view (`js/views/portfolio.js`)

- One row per project (from `GET /api/projects` joined client-side with
  `GET /api/kb/entities?type=project`): name, health dot + label (falls back
  to "green" if the column doesn't exist yet — Step 5 adds it), health
  reasons (small text), open-conflict count for that entity, task counts by
  status (`GET /api/tasks?project_id=`), last-activity time (newest timeline
  entry `happened_at`), truncated compiled-truth first sentence.
- Sort: red first, then yellow, then green; secondary by last activity.
- Row click → `#/project/<slug>` (stub page in 7a: show slug + "detail view
  lands in 7b").
- If an aggregate in one round-trip is cleaner, add
  `GET /api/dashboard/portfolio` (backend, TDD'd) that returns the joined
  rows — RECOMMENDED over N+1 fetches.

## Subproblem 3 — Tests (`tests/test_dashboard.py`, backend only)

1. `GET /dashboard` → 200 HTML; static css/js files served.
2. If you added `GET /api/dashboard/portfolio`: seed 2 projects with
   entities, tasks, a conflict → payload has per-project health, conflict
   count, task counts, last_activity; sorted red-first.
3. Full suite green; wall-clock guard clean.

## Definition of done

- [ ] Spec written; suite green; guard clean
- [ ] Manual check: open `/dashboard` next to the simulator; create a project
      + send a few webhook messages; portfolio row appears with counts; jump
      the sim clock in the simulator → dashboard top-bar time follows within
      5s. Screenshot-level polish: would a designer believe a human made
      this? Fix until yes.
- [ ] Commit with a clear message
