# Task: Knowledge-Base Schema (Entities, Timeline, Claims, Conflicts) + Query APIs

## Context

Repo: `manager-assistant` — FastAPI + SQLAlchemy 2.0 + SQLite, Vue 3 CDN
simulator. Start server: `.venv/bin/python3 -m app.main` (port 3003).
Tests: `.venv/bin/python3 -m pytest tests/`.

First read: `CLAUDE.md`, `spec/research/gbrain_index.md` (the design this KB
is adapted from — especially "Compiled truth + timeline", "Attributed
claims", and the schema reference at the bottom), `app/database.py`,
`app/kb/schemas.py`, `app/integrations/unified.py` (existing project/task
CRUD), `app/integrations/team.py`, `tests/conftest.py`.

**Why this feature:** This is Harry's brain — the storage layer everything
else writes into and reads from. Per entity (project / person / meeting) we
keep: a **compiled truth** (a rewritten synthesis paragraph, written by the
LLM in Step 4 — this step only stores it), an **append-only timeline** of
cited evidence (each entry traces to a `unified_messages` row), **attributed
claims** ("who claims what, with what confidence, superseded by what"), and
**conflicts** (pairs of contradicting claims — surfaced, NEVER auto-resolved).

**THIS STEP HAS ZERO LLM CODE.** Tables, deterministic helpers, and query
APIs only. Step 4 adds the extraction/synthesis that populates them.

**Project rules:**
- TDD: failing tests first, then implement, then run the FULL suite.
- All datetimes naive IST via `app.timeservice` — NEVER `datetime.now()` /
  `time.time()` outside `app/timeservice.py` (a guard test enforces this).
- No new pip dependencies. No embeddings — search is SQL `LIKE`, that's fine.
- Keep `CLAUDE.md` untouched (the reviewer updates it).

## Subproblem 0 — Spec file

Write `spec/feature_06_kb.md`: the four tables with column meanings, the
citation-marker convention (below), API list with example request/response
JSON, and the test plan. Same style as feature_05.

## Subproblem 1 — Models (`app/kb/models.py`, new file)

SQLAlchemy models importing `Base` from `app.database`. New tables only, so
`create_all` suffices — just make sure `init_db()` imports this module before
`create_all` (same trick as `OutboundQueue`).

```python
class Entity(Base):
    __tablename__ = "entities"
    id: int PK autoincrement
    slug: str UNIQUE            # "project:phoenix", "person:U_ALICE", "meeting:42"
    type: str                   # "project" | "person" | "meeting"
    name: str                   # display name
    ref_id: Optional[str]       # loose pointer to projects.id / team_members.id
    compiled_truth: Optional[Text]   # LLM-written synthesis (Step 4); nullable
    truth_updated_at: Optional[DateTime]
    created_at / updated_at: sim IST defaults (copy the lambda pattern from Project)

class TimelineEntry(Base):
    __tablename__ = "timeline_entries"
    id: int PK autoincrement
    entity_id: FK entities.id
    happened_at: DateTime        # sim IST — when the event happened (usually the message timestamp)
    summary: str (String(500))   # one-line, deterministic or LLM-written later
    detail: Optional[Text]
    source_message_id: Optional[FK unified_messages.id]  # the citation target
    created_at: sim IST default
    # APPEND-ONLY: no update/delete API for this table, ever.

class AttributedClaim(Base):
    __tablename__ = "attributed_claims"
    id: int PK autoincrement
    entity_id: FK entities.id
    claim: Text                  # "Bob sent the schema doc to Alice on Jul 10"
    kind: str                    # "fact" | "status" | "commitment" | "blocker" | "opinion"
    holder: str                  # team_members.id of who asserts it (e.g. "U_BOB")
    weight: float                # 0.0-1.0 confidence; 1.0 = holder stated it directly
    source_message_id: Optional[FK unified_messages.id]
    claimed_at: DateTime         # sim IST — when the claim was made
    superseded_by: Optional[FK attributed_claims.id]  # chain to the newer claim
    active: bool default True    # False once superseded
    created_at: sim IST default

class Conflict(Base):
    __tablename__ = "conflicts"
    id: int PK autoincrement
    entity_id: FK entities.id
    claim_a_id: FK attributed_claims.id
    claim_b_id: FK attributed_claims.id
    severity: str                # "low" | "medium" | "high"
    description: Text            # why these two contradict
    status: str default "open"   # "open" | "resolved" | "dismissed"
    detected_at: DateTime        # sim IST
    resolved_at: Optional[DateTime]
    resolution_note: Optional[Text]
```

**Citation convention (document in the spec, no code needed yet):** compiled
truth text will contain inline markers like `[T17]` where 17 is a
`timeline_entries.id`; the dashboard (Step 7) resolves marker → timeline
entry → `source_message_id` → original message on hover. This step just
guarantees the id chain exists.

## Subproblem 2 — Deterministic entity collectors

gbrain's rule: entity pages are created by CODE, not by the LLM.

- Helper `get_or_create_entity(db, slug, type, name, ref_id=None) -> Entity`
  in `app/kb/models.py` (or a small `app/kb/service.py` — your call).
- Wire it in: creating a Project via `POST /api/projects` also creates entity
  `slug=f"project:{slugify(name)}"` (lowercase, spaces→hyphens — write a tiny
  `slugify`, no dependency). Creating a TeamMember via `POST /api/team` also
  creates `slug=f"person:{id}"`. Both idempotent via get_or_create.
- `init_db()` backfills: for every existing project and team member, ensure
  the entity page exists (covers rows created before this step; also gives
  Harry a person page — harmless).

## Subproblem 3 — Query APIs (`app/kb/api.py`, new router, prefix `/api/kb`)

Pydantic schemas go in `app/kb/schemas.py` (extend the existing file).
Register the router in `app/main.py`.

1. `GET /api/kb/entities?type=project` — list (id, slug, type, name,
   truth_updated_at; NOT the full truth text — keep the list light).
2. `POST /api/kb/entities` — `{slug, type, name, ref_id?}`; 409 on duplicate
   slug; validate type against the three allowed values (422).
3. `GET /api/kb/entities/{slug}` — the full "page" payload the dashboard
   will render:
   ```json
   { "entity": {...}, "compiled_truth": "...", "truth_updated_at": "...",
     "timeline": [ ...newest first, each with source_message_id... ],
     "claims": [ ...active only... ],
     "conflicts": [ ...open only... ] }
   ```
   404 if slug unknown.
4. `POST /api/kb/entities/{slug}/timeline` —
   `{happened_at?, summary, detail?, source_message_id?}`; `happened_at`
   defaults to sim now; if `source_message_id` given and doesn't exist → 404.
   No PUT/DELETE routes for timeline (append-only).
5. `GET /api/kb/entities/{slug}/timeline?limit=50` — newest first.
6. `POST /api/kb/entities/{slug}/claims` —
   `{claim, kind, holder, weight, source_message_id?, claimed_at?}`;
   validate kind ∈ the five values and 0.0 ≤ weight ≤ 1.0 (422 otherwise);
   `claimed_at` defaults to sim now.
7. `GET /api/kb/entities/{slug}/claims?active=true&holder=U_BOB` — filters
   optional; default returns ALL (active and superseded).
8. `POST /api/kb/claims/{id}/supersede` — body = same shape as claim create
   (entity inherited from the old claim). Creates the new claim, sets old
   `superseded_by=new.id` and `active=False`, returns both. 409 if the old
   claim is already superseded.
9. `POST /api/kb/conflicts` — `{entity_slug, claim_a_id, claim_b_id,
   severity, description}`; both claims must exist and belong to that entity
   (422 otherwise); `detected_at` = sim now. (Step 4's contradiction probe
   and the demo seed will call this.)
10. `GET /api/kb/conflicts?status=open&entity=project:phoenix` — filters
    optional.
11. `PATCH /api/kb/conflicts/{id}` — `{status: "resolved"|"dismissed",
    resolution_note?}` → sets `resolved_at` = sim now. This is the ONLY way
    a conflict closes — Harry never auto-resolves (project rule).
12. `GET /api/kb/search?q=schema` — case-insensitive `LIKE` across
    entity names, compiled truths, claim texts, and timeline summaries.
    Response grouped: `{"entities": [...], "claims": [...], "timeline": [...]}`,
    each hit carrying its entity slug. Cap each group at 20.

## Subproblem 4 — Tests (write FIRST), `tests/test_kb.py`

Reuse the `db_session`/`client` fixtures and the tmp `SIM_CLOCK_PATH`
autouse fixture pattern from `tests/test_outbound.py`.

1. Entity create → 200; duplicate slug → 409; bad type → 422.
2. `POST /api/projects` auto-creates `project:<slug>` entity;
   `POST /api/team` auto-creates `person:<id>` entity (idempotent on repeat).
3. Timeline append with explicit `happened_at` + one defaulting to sim now
   (set sim time first, assert stamped value); newest-first ordering;
   bad `source_message_id` → 404; no DELETE/PUT route exists (405/404).
4. Claim create with weight 1.2 → 422; kind "vibe" → 422; happy path stores
   defaults (active=True, claimed_at = sim now).
5. Supersede: create claim A, supersede with B → A inactive +
   `superseded_by == B.id`, B active; superseding A again → 409;
   `?active=true` returns only B; no filter returns both.
6. Conflict create with claims from two different entities → 422; happy path
   → open conflict listed in `GET /api/kb/conflicts?status=open`.
7. Conflict PATCH resolve → status, `resolved_at` (sim time), note set;
   entity page payload no longer includes it (open only).
8. Entity page payload (`GET /api/kb/entities/{slug}`) contains truth field,
   timeline newest-first, only active claims, only open conflicts.
9. Search: seed an entity + claim + timeline entry all mentioning "phoenix";
   `?q=phoenix` returns hits in all three groups; `?q=zzz` returns empties.
10. Full existing suite still green; wall-clock guard still clean.

## Definition of done

- [ ] `spec/feature_06_kb.md` written
- [ ] Full test suite green; `grep -rn "datetime.now(\|time.time()" app/ | grep -v timeservice` → nothing
- [ ] Manual check: start server, create a project via the existing API,
      `GET /api/kb/entities` shows `project:...`; append a timeline entry
      citing a real message id; create two claims + a conflict; fetch the
      entity page and see all of it in one payload
- [ ] Commit with a clear message
