# Step 38 — Consolidated test pass for the agentic KB refactor

Steps 33–37 were implemented **without tests** on purpose. This step writes them,
in one pass, covering the **main paths only** — not exhaustive coverage.

---

## Starting state

Run `.venv/bin/python3 -m pytest tests/ -q` first to see the damage.

**Three failures are KNOWN and OUT OF SCOPE — do not touch them:**
`tests/test_kb_pipeline_judge.py::test_01_classification`, `::test_02_ingestion_claims`,
`::test_03_heartbeat_events`. They are live-Gemini accuracy tests from a separate
ingestion-tuning workstream.

**These test files WILL be broken and must be rewritten**, because the code they
test was replaced by agentic versions:
- `tests/test_heartbeat_user.py` — tested the old single-JSON-call heartbeat
- `tests/test_heartbeat_project_fanout.py` — tested the old fan-out call
- `tests/test_dream_job.py` — tested the old dream calls; also asserts `dump.md`,
  which no longer exists (`manager_dump_md_path` was deleted)

Phase A already has tests (`test_runner.py`, `test_kb_context.py`,
`test_result_compaction.py`, `test_rate_limit.py`, `test_registry_allowlist.py`,
`test_harness_wrapper.py`, `test_gemini_schema_nested_title.py`,
`test_gemini_client_retry.py`, `test_occurrence.py`). Keep them; fix them only if
they genuinely broke.

---

## The one thing you must get right: faking a tool-calling agent

Existing job tests script exact JSON via `FakeTransport` list order. Agentic jobs
produce a **multi-turn tool-calling** sequence instead, so you need a helper
alongside the existing `_gemini_response`:

```python
def _gemini_tool_call_response(name, args):
    return {"candidates": [{"content": {"parts": [
        {"functionCall": {"name": name, "args": args}}
    ]}, "finishReason": "STOP"}]}
```

A typical run is scripted as: `FakeTransport([_gemini_tool_call_response("emit_events",
{...}), _gemini_response_text("done")])` — one turn that calls the write tool, then
a turn with plain text that ends the loop. Put the shared helpers in
`tests/conftest.py` rather than copy-pasting `FakeTransport` into every file again.

---

## What to cover (main paths only)

**Heartbeat (`tests/test_heartbeat_user.py`, `tests/test_heartbeat_project_fanout.py`):**
- no pending claims → returns immediately, `transport.call_count == 0`
- a scripted `emit_events` call creates the Event rows with correct type/severity/
  `general`, and marks the cited claims `processed=True`
- the severity floor still applies to `blocker`/`clarification`/`conflict`
- an invented `project_id` is dropped; an invented `claim_id` is dropped
- `occurred_at` is populated from the source message timestamp
- **claim disposal:** `stop_reason=="final"` marks uncited claims processed;
  a budget/deadline stop leaves them unprocessed
- **starvation guard:** a claim reaching 3 attempts is force-processed
- project fan-out: a scripted `apply_task_transitions` applies, and one citing a
  `done` task is rejected
- `ArchiveEntry` rows are written deterministically (no extra LLM call for them)

**Dream (`tests/test_dream_job.py`):**
- no un-dreamed events → zero LLM calls
- scripted `write_memory` writes `memory.md`; a blank content is rejected and the
  file is NOT blanked
- `set_health_adjustment` clamps to ±1 and forces 0 when `reason` is blank; the
  `HealthLog` row's `base_score` comes from the deterministic rubric
- events are marked `dreamed=True` even when a project run fails, but NOT when the
  user synthesis fails
- **no test may reference `dump.md` or `manager_dump_md_path`**

**Lint (`tests/test_lint_job.py`, new):**
- a clean tree → `issues_found == 0` and **zero LLM calls** (assert with a
  transport that raises on any call)
- an orphaned `ClaimSource` produces exactly one `AgentNote(kind="lint_finding")`

**Synthesis (`tests/test_synthesis.py`, new):**
- `send_message` is absent from the tool allowlist with `allow_writes` both False
  and True
- `POST /api/kb/ask` returns 400 on an empty question and 200 with a scripted
  answer otherwise

**Scheduler (extend `tests/test_projectkb_scheduler.py`):**
- an unregistered job, and a job whose `interval_minutes` is missing/0, both get
  the 60-minute fallback rather than being always-due
- a corrupt `job_state.json` does not make every job due at once
- a held job lock makes the scheduler skip that job this tick

---

## Rules

- Follow the existing test idioms: the `client` / `db_session` / `manager_employee_id`
  / `cleanup_projects` fixtures in `conftest.py`, `GeminiClient(api_key="fake-key",
  transport=FakeTransport([...]))` injection, `monkeypatch.setattr(timeservice,
  "now_ist", lambda: <fixed dt>)` for time.
- Assert LLM call counts with `transport.call_count` — that idiom is established
  and is how "sufficient but only necessary budget" gets verified.
- Do NOT weaken a test to make it pass. If the implementation is wrong, fix the
  implementation and say so in your report.
- Final state must be: **0 failures except the 3 known live-LLM ones.**
