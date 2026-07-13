# Task: Dream-Cycle Synthesis (compiled truth) + Contradiction Probe

## Context

Repo: `manager-assistant`. Server: `.venv/bin/python3 -m app.main`. Tests:
`.venv/bin/python3 -m pytest tests/`.

First read: `CLAUDE.md`, `spec/research/gbrain_index.md` (sections "Compiled
truth + timeline", "Contradiction probe", "Dream cycle"), `app/kb/models.py`,
`app/kb/extraction.py`, `app/agent/gemini_client.py` (built in Step 4a — use
its interface and its test-faking pattern).

**This prompt was written ahead of time. If any detail contradicts the
current code, THE CODE WINS — adapt.**

**Why this feature:** the expensive batch half of the dream cycle. The smart
model rewrites each entity's compiled truth (a readable synthesis with
inline `[T<id>]` citations), and a probe hunts contradicting claim pairs and
files them as conflicts. This produces the two demo money-shots: hover a
sentence → see the source message; and the Bob-vs-Alice deadlock surfacing
as a conflict Harry escalates.

**Project rules:** TDD; sim time only via `app.timeservice`; no new deps;
tests never hit the network (fake the client); Harry NEVER auto-resolves
conflicts — the probe only CREATES them; keep `CLAUDE.md` untouched.

## Subproblem 0 — Spec file

`spec/feature_08_dream_cycle.md`: synthesis prompt design, citation-marker
validation, supersession pass, probe pair-selection rules, endpoint, test plan.

## Subproblem 1 — Supersession pass (deterministic + flash judge)

Before synthesis, resolve stale claims. In `app/kb/synthesis.py`:

`run_supersession_pass(db, entity, client) -> int`:
- Candidate pairs in CODE: active claims on the same entity with the same
  `holder` and same `kind`, older vs newer (`claimed_at`). Cap 10 pairs/entity.
- Flash judge per batch (one call, json_mode): "for each pair, does the NEWER
  claim update/replace the OLDER one? true only if they're about the same
  thing." → for `true` pairs, wire `older.superseded_by = newer.id`,
  `older.active = False` (reuse the Step 3 logic/helper if one exists).
- Never supersede across holders (Alice's claim can't supersede Bob's —
  that's a potential CONFLICT, not an update).

## Subproblem 2 — Compiled-truth synthesis (smart model)

`synthesize_entity(db, entity, client) -> str | None`:

- Dirty check in CODE: skip unless the entity has timeline entries or claims
  created after `truth_updated_at` (or truth is null).
- Input assembled in code: entity name/type, ACTIVE claims (id, holder name,
  kind, weight, claimed_at, text), timeline entries newest-first capped at 40
  (id, happened_at, summary), current compiled_truth (for continuity), and
  `Current time: {timeservice.now_ist()} IST`.
- SMART_MODEL prompt rules: 1-3 short paragraphs, present tense, plain
  factual prose a busy manager skims; EVERY factual sentence ends with one or
  more `[T<id>]` markers where <id> is a timeline entry id from the input;
  mention unknowns/uncertainty honestly (weights < 0.7 → "reportedly");
  DO NOT invent facts not in the inputs.
- Validation in CODE: regex all `[T\d+]` markers; any id not in this entity's
  timeline → strip that marker; if a sentence loses all markers, keep the
  sentence (it just has no hover). Store to `entity.compiled_truth`,
  `truth_updated_at = timeservice.now_ist()`.

## Subproblem 3 — Contradiction probe (flash judge, never auto-resolves)

`run_contradiction_probe(db, entity, client) -> list[Conflict]`:

- Candidate pairs in CODE: active claims on the same entity from DIFFERENT
  holders, `kind` in {fact, status, commitment, blocker}, no existing
  conflict row for that pair in ANY status (check both orderings), cap 15
  pairs/entity per run.
- Flash judge (json_mode, batched): for each pair →
  `{"contradicts": bool, "severity": "low|medium|high", "description": "..."}`
  Severity rubric from gbrain: low = wording/naming mismatch, medium = stale
  vs fresh value disagreement, high = factual deadlock or identity error
  (e.g. "I sent it" vs "I never received it" = high).
- `contradicts:true` → insert `conflicts` row (`detected_at` = sim now,
  status "open"). NEVER touch the claims themselves, NEVER resolve anything.

## Subproblem 4 — The dream-cycle orchestrator + endpoint

`run_dream_cycle(db, client=None) -> dict` in `app/kb/synthesis.py`:
1. Process unprocessed messages (call Step 4a's batch function, limit 50).
2. For each dirty entity: supersession pass → synthesis → probe.
3. Return stats: `{"messages_processed": n, "entities_synthesized": n,
   "claims_superseded": n, "conflicts_found": n}`.

`POST /api/kb/dream` (in `app/kb/api.py`) → runs it, returns stats. (Step
5's scheduler and the demo both trigger this endpoint.)

## Subproblem 5 — Tests (`tests/test_dream_cycle.py`, write FIRST, LLM faked)

1. Supersession: same holder+kind pair, fake says true → old inactive with
   `superseded_by` set; different holders NEVER sent to the judge.
2. Synthesis dirty-check: entity with no new activity since
   `truth_updated_at` → smart model NOT called.
3. Synthesis happy path: fake returns text citing `[T{real_id}]` and
   `[T99999]` → stored truth keeps the real marker, bogus one stripped,
   `truth_updated_at` = sim now.
4. Probe: two claims, different holders, fake says contradicts/high →
   conflict row open with severity high; running the probe AGAIN creates no
   duplicate; claims untouched (still active, no supersession).
5. Probe pair with an existing RESOLVED conflict → not re-judged.
6. `POST /api/kb/dream` end-to-end with fakes: unprocessed message → claim →
   truth rewritten → stats dict correct.
7. Full suite green; wall-clock guard clean.

## Definition of done

- [ ] Spec written; suite green; guard clean
- [ ] Manual check with real key: seed the deadlock — Bob's Slack message "I
      sent the schema doc to Alice on Monday" and Alice's "I still haven't
      received any schema doc from Bob" (both re project phoenix) → POST
      /api/kb/dream → entity page shows a cited compiled truth AND an open
      high-severity conflict pairing the two claims. Paste the truth text +
      conflict row in your summary.
- [ ] Commit with a clear message
