# Step 27 — Remaining frontend gaps: blocklist settings, full create-project form, Portfolios, workload

Authority: `spec/architecture_v2_kb.md` §7 item 9 ("frontend wiring... as
wireframes are worked through") plus the CLAUDE.md "Frontend rebuild"
section's running list of what's built. This step is a grab-bag of
UI surfaces whose BACKEND already exists but has no frontend yet, or
whose wireframe hasn't been tackled. Confirm each backend endpoint still
matches what's described below before building against it (re-read the
actual router file — this prompt may lag real code by the time it's
picked up).

Do these independently and in whatever order the user prioritizes when
this step starts — they don't depend on each other or on steps 22–26.
Standing frontend conventions apply (re-read the CLAUDE.md "Frontend
rebuild" section before starting): `constants.ts` for runtime knobs,
`tailwind.config.js` for structural colors, real assets from `/assets`
only, unbuilt-nav-as-plain-text, honest no-data states, headless-Chrome
screenshot verification (no custom `--user-data-dir`), `npm run build`
clean.

## 1. Blocklist settings page

Backend done (step 20): `GET/POST/PUT/DELETE /api/blocklist[/contacts|
/channels][/{id}]` (`app/projectkb/api.py`). No wireframe exists for
this — use judgment: a simple settings page (new nav destination,
probably under a gear icon or a new Sidebar entry) with two sections
(blocked contacts, blocked channels), each a list + add-row form
(pattern field explained as "supports wildcards, e.g. `*@newsletter.com`").
Add `getBlocklist`/CRUD functions to `frontend/src/lib/api.ts` following
the existing typed-fetch-function convention.

## 2. Full Create Project form (wireframe 5.png)

Currently `CreateProjectModal` is a minimal name/description/kind modal
(built ahead of the full wireframe, noted as a stopgap in the Projects
grid step). Look at wireframe 5.png specifically and build out the real
form: member picker against `GET /api/employees` (already returns
name/email/role/skills — build a searchable multi-select), per-member
role field, supervisors field (maps to `Project.supervisors` in the
registry — confirm the registry API's create payload shape in
`app/api/projects_registry.py` before assuming a field name). Kind stays
locked to whichever rail (Tasks vs Projects) the user clicked "+" from,
same as today.

## 3. Portfolios pages (wireframes 4.png, 8.png)

Currently a disabled tab in `Projects.tsx` ("Portfolios -- coming soon").
No backend concept of a "portfolio" exists yet anywhere in the registry
or spec — this needs a short design check before coding: read wireframes
4/8.png fresh, figure out whether "portfolio" is (a) a saved filter/view
over existing projects (no schema change, purely a frontend grouping) or
(b) a real entity needing its own registry table and CRUD API. Don't
assume — if it's (b), this becomes a mini spec addition (a short section
appended to `architecture_v2_kb.md`, following the project's own
spec-first rule) before writing backend code, not a silent schema
addition.

## 4. Workload view

Spec's product model mentions "a user db... to create user level records
for workload... for each user, the projects they are involved in." No
endpoint exists yet. Backend: an endpoint (likely
`GET /api/employees/{id}/workload` or folded into the existing
`GET /api/employees` response) aggregating, per employee, their
`ProjectMember` rows across all projects + open `Task` counts assigned to
them (cross-project query — iterate `list` of projects the employee is a
member of, open each project db, count). No wireframe currently covers
this in the reviewed set — check with the user whether a dedicated page
is wanted or if this folds into an existing surface (e.g. a hover/expand
on ProjectCard's member avatars) before building UI; the backend
aggregation is safe to build regardless since it's read-only and
low-risk.

## 5. Sidebar stub pages

`Sidebar.tsx`'s Settings/Preferences/"Report Issue" entries currently
render as plain non-clickable text (correct, per convention, until built).
Not in scope to build fully in this step unless the user specifically
asks — flagged here only so a future picker of this prompt knows they
exist and are deliberately unbuilt, not missing by oversight.

## Tests / verification

Each sub-item gets its own backend tests where new endpoints are added
(pytest, following existing per-router test-file conventions) plus the
standard frontend verification bar: `npm run build` clean, headless-
Chrome screenshot against the relevant wireframe, real backend data (no
dummy/mocked data in the shipped UI).

## Done criteria

Whichever sub-items get tackled in a given pass, log them individually in
CLAUDE.md's "Frontend rebuild" → "Built so far" list (follow the existing
one-clause-per-surface style) rather than waiting to batch all five
before updating docs — this step is explicitly a grab-bag meant to be
picked at incrementally.
