# Task: Agent Harness (Hermes-style tool loop) + Tools + Dashboard Chat

## Context

Repo: `manager-assistant`. Server: `.venv/bin/python3 -m app.main`. Tests:
`.venv/bin/python3 -m pytest tests/`.

First read: `CLAUDE.md`, `spec/research/hermes_index.md` — THE blueprint for
this step (tool system, loop essence, IterationBudget, three-tier system
prompt, harness sketch at the bottom). Then `app/agent/gemini_client.py`,
`app/kb/api.py` + `app/kb/models.py`, `app/outbound.py`, `app/followups.py`,
`app/timeservice.py`.

**This prompt was written ahead of time. If any detail contradicts the
current code, THE CODE WINS — adapt.**

**Why this feature:** Harry becomes conversational and agentic. The manager
asks anything in the dashboard chat dock; Harry answers with citations by
using tools over the KB, and can ACT (send follow-ups, ping people, escalate)
— all through the same tool layer the scheduler already respects.

**Project rules:** NO agent frameworks — hand-written loop, Hermes-inspired
(copying their code/snippets is fine and encouraged where the index says so).
TDD; sim time only; tests never hit network; every outbound tool goes through
`send_or_hold`; keep `CLAUDE.md` untouched.

## Subproblem 0 — Spec file

`spec/feature_10_agent.md`: module layout, tool list with schemas, prompt
tiers, loop pseudocode, chat API, test plan.

## Subproblem 1 — Registry + budget (`app/agent/registry.py`, `app/agent/budget.py`)

- `budget.py`: copy Hermes' `IterationBudget` VERBATIM (thread-safe
  consume/refund counter, ~62 lines — fetch from
  https://github.com/NousResearch/hermes-agent `agent/iteration_budget.py`;
  if unreachable, reimplement to the same interface). Default budget 20 for
  Harry (we're not a coding agent; 20 tool rounds is plenty).
- `registry.py` (~60 lines): `ToolEntry` dataclass (name, schema, handler) +
  `register(name, schema, handler)` + `get_tool_definitions() -> list[dict]`
  (OpenAI-style) + `execute(name, args: dict, *, db) -> str` (dispatch;
  catch handler exceptions → return `"ERROR: {msg}"` as the tool result —
  the loop must survive bad tool calls). Cap every tool result at 8000 chars
  (truncate with a `"... [truncated]"` suffix).

## Subproblem 2 — Tools (`app/agent/tools.py`, one module is fine)

Schemas in the todo_tool house style: instructional prose descriptions that
teach USAGE, not just what it is. All read tools reuse Step 3's query logic
(import the functions / query the models — do NOT shell out to HTTP).

1. `kb_search(q)` — grouped entity/claim/timeline hits with slugs. Description
   tells Harry to search BEFORE answering anything factual.
2. `get_entity(slug)` — the full page payload (truth + timeline + active
   claims + open conflicts). Description: this is the primary read; timeline
   ids map to `[T#]` citations.
3. `list_projects()` — id, name, status, health, health_reasons.
4. `list_tasks(project_id?, assignee_id?, status?)`.
5. `update_task(task_id, status?, assignee_id?, due_date?)` — description
   warns: only when the manager explicitly asks.
6. `get_conflicts(status?)`.
7. `send_slack_dm(member_id, text)` — via `send_or_hold`; returns
   sent-or-held + release time so Harry can tell the manager it's queued.
8. `send_email(member_id, subject, body)` — via `send_or_hold` (Graph-shaped
   payload built in code from the member's outlook_email).
9. `create_followup(member_id, question, due_at?, entity_slug?)`.
10. `get_current_time()` — sim now, IST (Harry must never guess the time).

## Subproblem 3 — Prompts (`app/agent/prompts.py`) + harness (`app/agent/harness.py`)

- Three tiers joined `\n\n` (Hermes pattern):
  **stable** — Harry's persona: manager-assistant for {manager name}; blunt,
  concise, cites `[T#]` markers when stating facts from the KB; never invents;
  asks before irreversible actions; never resolves conflicts himself, only
  surfaces them. Plus tool-usage guidance.
  **context** — team roster (id/name/role) + project list, assembled from DB.
  **volatile** — `Current time: {sim now} IST` + open-conflict count + any
  degraded projects. (Sim time MUST be here — the demo asks Harry the time
  after a jump.)
- `harness.py` — `run_agent(db, user_message, history: list[dict], client=None)
  -> dict` (~150 lines, the Hermes loop essence):
  1. Build system prompt (tiers) + history + user message.
  2. Loop while budget: `client.chat(SMART_MODEL, messages, tools=defs)` →
     if `tool_calls`: execute each in order via registry (append
     `{"role":"tool", "tool_call_id", "content"}` results), continue; else →
     final text.
  3. Budget exhausted → return best-effort text ("I ran out of steps…").
  4. Return `{"reply": str, "tool_trace": [{"name", "args", "result_preview"}]}`.

## Subproblem 4 — Chat persistence + API + docks

- `chat_messages` table: id, role ("user"|"assistant"), content (Text),
  tool_trace (Text JSON, nullable), created_at (sim IST).
- `POST /api/chat` `{message}` → loads last 20 chat_messages as history, runs
  the harness, persists both sides, returns
  `{"reply", "tool_trace", "created_at"}`. `GET /api/chat/history?limit=50`.
  `DELETE /api/chat/history` (demo reset convenience).
- Simulator's existing dashboard/chat pane (`app/static/index.html`): wire it
  to POST `/api/chat` and render the reply (plain text ok; show a small
  "used N tools" line from the trace). The POLISHED chat dock comes with the
  dashboard in Step 7 — keep this minimal.

## Subproblem 5 — Tests (`tests/test_agent.py`, write FIRST, LLM faked)

Fake client returns scripted sequences (first call → tool_calls, second →
final text).

1. Registry: register/execute; handler exception → "ERROR:" result, no raise;
   oversized result truncated.
2. Budget: fake that ALWAYS returns tool_calls → loop stops at budget,
   returns the exhaustion reply.
3. Harness happy path: script [call kb_search → final answer citing result]
   → reply correct, trace has 1 tool, both messages persisted with sim
   timestamps.
4. Tool execution order preserved for multi-tool responses; tool results
   arrive as role:"tool" with matching ids (inspect messages sent to fake on
   call 2).
5. Volatile tier contains current sim time: set clock to a known value,
   inspect the system prompt the fake receives.
6. `send_slack_dm` tool at 23:00 sim → result says held with release time;
   outbound_queue row exists (gate respected end-to-end).
7. `POST /api/chat` twice → second call's history includes the first
   exchange (inspect fake's received messages).
8. Full suite green; guard clean.

## Definition of done

- [ ] Spec written; suite green; guard clean
- [ ] Manual check with real key: ask via `POST /api/chat` — "what's the
      latest on phoenix?" (expect cited synthesis using get_entity),
      "ask Bob to confirm he re-sent the schema doc" (expect create_followup
      or send_slack_dm; verify in simulator), "what time is it?" after a
      clock jump (expect the sim time). Paste transcripts in your summary.
- [ ] Commit with a clear message
