# Step 37 — Lint job + Knowledge Synthesis agent (Phases F & G)

Two independent pieces. Neither touches `heartbeat.py` or `dream.py`.

**Testing policy: implement the logic only. Do NOT write tests, do NOT run
pytest.** Only verification: `.venv/bin/python3 -c "import app.main"`.
**Do NOT edit `CLAUDE.md`** — docs are handled separately.

---

## Part 1 — The lint job (`app/projectkb/jobs/lint.py`)

Today it is a 22-line no-op stub. Signature is already
`run(db, manager_id, client=None) -> dict`.

**Deterministic checks first — these are the point of the job and run with zero
LLM calls.** The five intended checks are listed in the current stub's docstring:

1. Every `ClaimSource` row resolves to a real `UnifiedMessage`.
2. Every `Event.claim_ids` entry resolves to a real `Claim`.
3. No orphaned refs in any owned project db: `Task.parent_task_id` pointing at a
   missing task, `Conflict.claim_a_ref`/`claim_b_ref` not resolving to real
   `Claim` rows, `Event.task_ids` pointing at tasks that no longer exist.
4. Staleness: owned projects whose `summary.md` / `events.md` haven't been
   touched in N days while events kept arriving (config
   `LINT_STALE_PROJECT_DAYS`, default 14); claims stuck unprocessed past several
   heartbeat cycles; events stuck `dreamed=False` well past a dream cycle.
5. Poll-health: `Employee.outlook_poll_last_success_at` /
   `slack_poll_last_success_at` stale relative to that job's own
   `interval_minutes` — per CLAUDE.md that staleness IS the audit signal that
   polling is silently failing.

Each finding is a dict: `{check, severity: "info"|"warn"|"error", subject_ref,
detail}`. Persist via `app/agent/notes.py::record_note` with
`kind="lint_finding"` — that function exists and currently has **zero callers**,
and `GET /api/agent/notes` already exposes it, so this needs no new table or
endpoint. Set `subject_ref` to the offending row (`event:<id>`, `project:<id>`,
`claim:<id>`, `employee:<id>`).

**Then an optional, capped LLM coherence pass** — additive, never a replacement
for the deterministic checks. Only runs when there are findings **or** the manager
has owned projects; skip entirely (zero LLM calls) when the deterministic pass
found nothing. Use `FLASH_MODEL` and `KB_LINT_MAX_LLM_CALLS`, with the read probes
from `kb_tools.py` plus a `report_finding` write tool (same `AgentNote` shape,
`kind="lint_finding"`). Its job is narrow: spot contradictions between what the md
files claim and what the db rows say. It must not fix anything.

Return `{"issues_found": n, "by_severity": {...}, "llm_calls": n, ...}`.

---

## Part 2 — Knowledge Synthesis agent

A dedicated ad-hoc agent for querying and updating the KB on demand — the
manual counterpart to the three scheduled agents.

**`app/agent/synthesis.py`:**

```python
def run_synthesis(db, manager_id, question, *, allow_writes=False, client=None) -> AgentRunResult
```

- Read-only by default: tool set = all the `kb_tools.py` read probes +
  `get_project_doc` / `list_tasks` / `get_task` / `list_team` from `tools.py`.
- `allow_writes=True` additionally grants the KB write tools (`emit_events`,
  `record_conflict`, the dream write tools, the task tools). It must **never**
  grant `send_message` — this agent reasons about the KB, it does not talk to
  people. That separation is the whole reason it isn't just the chat agent.
- `max_llm_calls=KB_SYNTHESIS_MAX_LLM_CALLS`, deadline `KB_AGENT_DEADLINE_SECONDS`.
- Context via `build_kb_context(db, manager_id)`.
- Instructions: answer from what the tools actually return; probe before
  asserting; cite the event/claim/task ids behind every claim made; say plainly
  when the KB does not contain the answer rather than inferring. When writes are
  enabled, state what was written.

**`app/api/kb_synthesis.py`:** `POST /api/kb/ask`, body
`{question: str, allow_writes: bool = False}`, auth via the existing
`get_current_employee` + `get_manager_db` dependencies (copy the pattern from
`app/agent/api.py`). Returns `{answer, tool_trace, llm_calls, tokens_in,
tokens_out, stop_reason}`. Register the router in `app/main.py` next to the other
routers. Reject an empty question with 400.

---

## Definition of done

- `.venv/bin/python3 -c "import app.main"` succeeds and `POST /api/kb/ask` appears
  in the app's routes.
- The lint job makes **zero** LLM calls when the deterministic pass is clean.
- `record_note` is now actually called.
- The synthesis agent cannot send messages under any flag.
