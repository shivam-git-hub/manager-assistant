# Step 35 — KB probe tools + agentic heartbeat (Phases C & D)

Replaces the heartbeat job's two blind LLM calls with tool-using agents that
**probe the KB before writing to it**. Read `CLAUDE.md` first, then
`app/agent/runner.py`, `app/agent/kb_context.py`, `app/agent/registry.py`,
`app/agent/tools.py`, `app/projectkb/jobs/heartbeat.py` (the file you are
replacing), and `app/projectkb/occurrence.py`.

**Testing policy: implement the logic only. Do NOT write tests, do NOT run
pytest.** Tests land in one consolidated pass at the end (step 39). Verify only
with `.venv/bin/python3 -c "import app.main"`.

---

## Why

Today heartbeat sends *pending claims + a fixed 10-event tail + memory.md* in one
shot and takes whatever events come back. It cannot ask "is there already an open
blocker for this?", "what's this task's current status?", "what did we decide last
week?". So a claim like *"still waiting on the Kafka creds"* becomes a brand-new
blocker every single tick instead of being recognised as the same one. The agent
must be able to look things up. That is the entire point of this step — **recall
quality first, cost second**.

---

## Part 1 — `app/agent/kb_tools.py` (read-only probes)

Register on the shared `registry` (import side-effect pattern, same as
`tools.py:603`). Every handler takes `db, manager_id, run_context, **args`.
Give each a `max_result_chars` appropriate to its shape (default 4000; use 6000
for `get_event`/`get_project_state`). Rely on step 33's
`result_compaction.compact_tool_result` — never hand-roll truncation.

| Tool | Args | Returns |
|---|---|---|
| `search_events` | `project_id?`, `type?`, `severity_min?`, `since_days?` (default 14), `query?` (substring over title/body), `limit?` (default 20, hard max 50) | id, type, severity, title, body(≤300 chars), project_ids, occurred_at, created_at, ui_state |
| `get_event` | `event_id` | the full event **plus the text of every cited claim** — this is the "why did we conclude that" probe |
| `search_claims` | `query?`, `since_days?` (default 7), `processed?`, `limit?` (default 20, max 50) | id, text, thread_key, created_at |
| `get_thread` | `thread_key`, `limit?` (default 20) | the raw `UnifiedMessage` rows behind a thread: sender, timestamp, content(≤400 chars) |
| `get_project_state` | `project_id` | name, kind, member count, open/blocked/overdue task counts, latest `HealthLog` (score + reason), open `Conflict` count, `summary.md` tail (≤800 chars) |
| `list_projects` | — | id, name, kind, your role — for every **visible** project (owned ∪ member) |

Ordering on anything time-based uses `func.coalesce(Event.occurred_at,
Event.created_at)`, per step 34. All of these are **read-only** — none may write.

Reuse, don't reimplement: `context.visible_projects_for_manager`,
`context.team_roster_for_manager`, `app/projects/db.get_project_session`,
`app/projects/paths.summary_md_path`.

---

## Part 2 — Heartbeat write tools (in `app/agent/kb_tools.py` or a sibling)

**`emit_events(events: [...])`** — one batch call, the agent's only way to create
events. Each item: `type`, `severity`, `title`, `body`, `project_ids[]`,
`claim_ids[]`. The handler carries **all** the deterministic validation currently
in `heartbeat.py:373-424` — none of it may be lost:

- `type` must be in `VALID_EVENT_TYPES` (the existing 7) else the item is skipped
- empty `title` → skip
- `severity` → `int()` guarded, clamped to 0..3, then floored to ≥1 for
  `FLOOR_SEVERITY_TYPES` (`blocker`/`clarification`/`conflict`)
- `project_ids` filtered to the manager's known project ids (invented ids dropped)
- `claim_ids` filtered to the ids actually offered in this run
- `general` is **derived** (`general = not project_ids`), never model-supplied
- `occurred_at` via `occurrence.compute_occurred_at(db, claim_ids)`
- **In the same commit as the inserted events, mark every cited claim
  `processed = True`.** This is the cross-tick loop guard: a consumed claim can
  never come back, so a run that dies halfway cannot re-emit what it already
  wrote. Do not let the job do this separately.

Returns a per-item accept/skip report with reasons, so the agent can correct
itself rather than silently losing an event.

**`record_conflict(project_id, claim_a_id, claim_b_id, severity)`** — inserts a
row into the project db's `conflicts` table. **Nothing in this codebase currently
writes that table** (it is read by the health rubric and the project-detail API
and is permanently empty) — this is the fix. Both claim ids must resolve to real
`Claim` rows or the call is rejected, mirroring fan-out's existing "cite a real
event id" discipline. Never auto-resolve: `status="open"` always.

**Project-phase tools:** `apply_task_transitions(project_id, transitions[])`
(each must cite a real `event_id` from this run — keep that guardrail),
`draft_tasks(project_id, tasks[])` (always forced `status="pending_approval"`,
`created_by="agent"`), `link_request_to_task(event_id, project_id, task_id)`
(accumulates onto `Event.task_ids`, never overwrites).

Archive writes stay **deterministic and non-LLM**: the job itself writes one
`ArchiveEntry` per event exactly as `heartbeat.py:275-284` does today.

---

## Part 3 — The agentic heartbeat job

Rewrite `app/projectkb/jobs/heartbeat.py`, keeping
`run(db, manager_id, client=None) -> dict`.

**Phase 1 — events (one agent run per manager):**
1. Select unprocessed claims oldest-first, slice to `HEARTBEAT_CLAIM_BATCH_SIZE`.
   **Empty → return immediately, zero LLM calls** (preserve today's short-circuit).
2. Build context with `build_kb_context(db, manager_id, include_conventions=True)`.
3. Seed message = the numbered pending claims with their ids, plus a short
   directive: probe before judging, then call `emit_events` once.
4. `AgentSpec(name="kb_heartbeat", model=SMART_MODEL, tool_names=<read probes> +
   ("emit_events", "record_conflict"), max_llm_calls=KB_HEARTBEAT_MAX_LLM_CALLS,
   max_tool_calls=..., deadline_seconds=KB_AGENT_DEADLINE_SECONDS)`.
   The instructions must state: severity doctrine, the conflict-vs-blocker
   discrimination (lift the existing wording from `heartbeat.py:99-108` — it was
   tuned), and **"check for an existing open event before creating a
   near-duplicate; prefer probing over guessing."**
5. **Claim disposal after the run** — this is the anti-loop rule, get it exactly
   right:
   - `stop_reason == "final"` → mark every remaining uncited batch claim processed.
     The agent finished and chose not to use them; they are not eternal.
   - `stop_reason in ("budget","deadline","tool_cap")` → leave uncited claims
     unprocessed; cited ones are already consumed by the tool. Next tick retries.
   - `stop_reason in ("error","empty_response")` → leave everything uncited
     unprocessed.
   - **Starvation guard:** add `Claim.heartbeat_attempts` (Integer, default 0, with
     a PRAGMA migration in `init_manager_db`). Increment it for every claim in the
     batch on every run. Any claim reaching 3 attempts without being cited is
     force-marked `processed=True` and logged. Without this, a claim the model
     always ignores is re-sent every hour forever.

**Phase 2 — project fan-out (one agent run per affected owned project):**
Group this run's non-general events by project via the existing
`project_scope.manager_owned_projects_with_events`. For each, one agent run with
the project tools + read probes, `max_llm_calls=KB_HEARTBEAT_PROJECT_MAX_LLM_CALLS`.
Per-project exceptions are caught and logged and must not abort the other
projects or the whole job (preserve `heartbeat.py:331-335`'s isolation).

**Return dict** — keep today's keys (`claims_consumed`, `events_created`, plus the
fan-out stats) and add `llm_calls`, `tokens_in`, `tokens_out`, `stop_reason` so the
debug page can show real cost per run.

---

## Definition of done

- `.venv/bin/python3 -c "import app.main"` succeeds. That is your only check.
- No deterministic guardrail from the old `heartbeat.py` is lost — the validation
  moved into tool handlers, it did not disappear.
- No LLM call is made when there are no pending claims.
- Old `_call_llm` / `_build_user_prompt` / `_SYSTEM_INSTRUCTION` /
  `_FANOUT_SYSTEM_INSTRUCTION` machinery is deleted, not left dead alongside the
  new path.
- Docstrings explain *why*. No narrative "this used to be X" comments.
