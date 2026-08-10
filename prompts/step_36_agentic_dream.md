# Step 36 — Agentic dream job (Phase E)

Replaces `app/projectkb/jobs/dream.py`'s two blind LLM calls with tool-using
agents, and removes `dump.md`.

**Testing policy: implement the logic only. Do NOT write tests, do NOT run
pytest.** Only verification: `.venv/bin/python3 -c "import app.main"`.
**Do NOT edit `CLAUDE.md`** — docs are handled separately.

---

## Why

Dream writes the durable narrative layer: `memory.md` (who the user is, what they
committed to, what recurs) and per-project `summary.md` (how the project got
here). Today it does that from *only* the un-dreamed event titles plus the current
file — it cannot look up what an event actually meant, read the task board, or
check whether a concern it's about to raise is already open. That produces thin,
repetitive synthesis. Recall quality is the priority here.

---

## Part 1 — Dream write tools

Add to `app/agent/kb_tools.py` (follow its existing conventions exactly — handler
signature `db, manager_id, run_context, **args`, schema constant, registration in
`register_tools()`):

- `write_memory(content)` — full overwrite of `manager_memory_md_path`. **Reject a
  blank/whitespace-only `content`** (today `dream.py:106-108` guards this; the
  memory file must never be blanked by a bad generation).
- `append_manager_events(lines[])` — append to `manager_events_md_path`, one `- `
  prefixed line each, using the existing `_append_lines` behaviour.
- `write_project_summary(project_id, content)` — full overwrite of
  `summary_md_path`; same non-blank guard. **Owned projects only** (reuse
  `_require_owned_project`).
- `append_project_events(project_id, lines[])` — append to the project's
  `events_md_path`. Owned only.
- `add_suggestions(project_id, texts[])` / `add_concerns(project_id, texts[])` —
  insert `Suggestion` / `Concern` rows (`status="open"`, `ts=now_ist()`), skipping
  blanks. Owned only.
- `set_health_adjustment(project_id, adjustment, reason)` — records the LLM nudge.
  Clamp `adjustment` to `[-1, 1]`; **force it to 0 when `reason` is missing or
  blank** (today's rule, `dream.py:264-268`). The handler computes the base score
  itself via the existing deterministic rubric and writes the `HealthLog` row —
  the agent supplies only the nudge and the reason, never the final score.

**The deterministic health rubric (`_compute_base_health_score`, `dream.py:196-210`)
must be preserved exactly** — 100 − 15×blockers − 10×overdue − 20×conflicts −
days_since_progress, all clamped. Move it somewhere both the job and the tool can
use (e.g. keep it in `dream.py` and import, or move to a small shared module).
Do not let the LLM compute health.

---

## Part 2 — The agentic dream job

Rewrite `run(db, manager_id, client=None) -> dict` in `dream.py`.

1. Select `Event.dreamed == False`, oldest first, slice to `DREAM_EVENT_BATCH_SIZE`.
   **Empty → return immediately, zero LLM calls.**
2. **User synthesis:** one agent run. Tools = the read probes from
   `kb_tools.py` (`search_events`, `get_event`, `search_claims`, `get_thread`,
   `list_projects`, `get_project_state`) + `write_memory` +
   `append_manager_events`. `max_llm_calls=KB_DREAM_MAX_LLM_CALLS`.
   Seed = the batch's events (type/severity/title, and ids so the agent can
   `get_event` for detail). Context via `build_kb_context(...)`.

   Instructions must cover what memory.md is *for*, and this is a deliberate
   widening of today's prompt (`dream.py:55-70`), which only asks for
   "preferences, commitments, recurring patterns":
   > a durable picture of this person's working life — the projects they're
   > involved in and their role in each, key events and decisions, commitments
   > they made or are owed, stated preferences and working style, and things
   > inferable from their conversations that would be costly to forget. Rewrite
   > the whole file each time; merge and dedupe rather than appending; drop
   > nothing that is still true.

   `events.md` stays a short one-line-per-event timestamped log and must NOT
   repeat memory.md's content.

3. **Per-project synthesis:** for each **owned** project with events in this batch
   (`project_scope.manager_owned_projects_with_events`), one agent run with the
   read probes + the project write tools, `max_llm_calls=KB_DREAM_PROJECT_MAX_LLM_CALLS`.
   Per-project exceptions caught and logged; one failure must not abort the rest
   (preserve `dream.py:331-336`).
4. **Mark dreamed:** after the project loop, set `dreamed=True` on the whole batch
   and commit — **including events whose project synthesis failed**. This is
   deliberate and already documented at `dream.py:324-328`; preserve it, or a
   permanently-failing project re-sends its events every 24h forever.
   One exception, also preserved: if the **user synthesis** itself fails, return
   early without marking anything dreamed (`dream.py:318-322`).
5. Return dict keeps `events_dreamed` / `projects_synthesized` and gains
   `llm_calls`, `tokens_in`, `tokens_out`, `stop_reason`.

---

## Part 3 — Remove `dump.md`

Grep-confirmed: `dream.py` is its only writer and **nothing ever reads it**.

- Delete `manager_dump_md_path` from `app/tenancy/paths.py`.
- Remove the `dump_lines` concept from the dream prompt/flow entirely.
- Delete the existing file on disk? **No** — leave any existing `dump.md` files
  alone; just stop writing them. Removing user data is not this step's job.
- `tests/test_dream_job.py` references it (`:20`, `:42`, `:164`) and WILL break.
  That is expected — the consolidated test pass rewrites that file. Do not try to
  fix the test now.

---

## Definition of done

- `.venv/bin/python3 -c "import app.main"` succeeds.
- The deterministic rubric, the non-blank-file guards, the ±1 clamp, the
  reason-required rule and the mark-dreamed-anyway semantics all survive.
- No zero-work LLM calls.
- Old `_USER_SYSTEM_INSTRUCTION` / `_PROJECT_SYSTEM_INSTRUCTION` /
  `_call_user_synthesis` / `_call_project_synthesis` / `_build_project_prompt`
  machinery deleted, not left dead.
