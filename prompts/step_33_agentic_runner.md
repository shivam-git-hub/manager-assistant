# Step 33 — Agentic runner foundation (Phase A)

Foundation for replacing the blind single-LLM-call KB jobs (heartbeat/dream/lint)
with tool-using agents that probe the KB before writing to it. **This step builds
only the runner + context + safety rails. No job is rewritten here.**

Read `CLAUDE.md` first. TDD is non-negotiable: write the test, watch it fail, then
implement. `.venv/bin/python3 -m pytest tests/` must be **0 failures** when you
finish — all 242 existing tests must still pass untouched.

---

## Existing code you MUST read before starting

- `app/agent/harness.py` — the current loop (137 lines). You are generalizing this.
- `app/agent/budget.py`, `app/agent/registry.py`, `app/agent/prompts.py`,
  `app/agent/context.py`, `app/agent/gemini_client.py`, `app/agent/tools.py`
- `tests/test_agent.py` — the `GeminiClient(transport=...)` injection pattern and
  the `_client_singleton` swap. Your new tests follow it.
- `tests/test_heartbeat_user.py` lines 30-90 — the `FakeTransport` / `_gemini_response`
  idiom every job test uses.

---

## 1. `app/agent/rate_limit.py` (new)

Process-wide token bucket. There is **no** rate limiter in this codebase today.

```python
class RateLimiter:
    def __init__(self, max_calls_per_minute: int): ...
    def acquire(self, timeout: float | None = None) -> bool:
        """Blocks until a slot frees. Returns False if `timeout` elapsed first."""
```

- `threading.Lock` + a deque of call timestamps, using `time.monotonic()` — **not**
  `timeservice.now_ist()` (that's wall-clock IST for domain stamping; a rate limiter
  measures elapsed real time and must not be affected by test monkeypatching of
  `now_ist`). This is an explicit, commented exception to the CLAUDE.md time rule.
- Module singleton `get_rate_limiter()` reading `LLM_MAX_CALLS_PER_MINUTE`.
- Wire into `GeminiClient.chat` immediately before the HTTP attempt loop.
- Must be a **no-op when the limit is 0** so tests never sleep. Set
  `LLM_MAX_CALLS_PER_MINUTE=0` in `tests/conftest.py`'s env block at the top of
  the file (alongside `CONTROLPLANE_DB_FILENAME`), since `app.config` reads env at
  import time.

Also in `gemini_client.py`: honor a `Retry-After` response header when present,
add ±25% jitter to the backoff, and fix the misleading `# 2s, 4s, 8s` comment
(only 2s and 4s ever fire — the third attempt re-raises before sleeping).

---

## 2. `app/agent/kb_context.py` (new) — context engineering

**This is the most important part of the step. The requirement is: all the
information the agent needs and ONLY that, so it doesn't waste tool calls, while
never bloating the prompt.**

`app/agent/context.py::build_agent_context` is the chat agent's context and stays
as-is (it's a different surface). This is a new, bounded, job-agent context.

```python
@dataclass(frozen=True)
class ContextBudget:
    max_total_chars: int = 6000
    max_projects: int = 15
    max_roster: int = 40
    max_memory_chars: int = 1500

def build_kb_context(db, manager_id, *, budget=ContextBudget(),
                     include_memory=True, include_conventions=True) -> str
```

Sections, in this exact order:

1. `## OWNER` — one line: name, `employee_id`, role.
2. `## PROJECTS` — one compact line per project, **not** prose summaries:
   `- id=<id> | <name> | <kind> | you are <manager|member> | open_tasks=<n> | blockers=<n> | health=<score or "n/a">`
   Reuse `context.visible_projects_for_manager(manager_id)`. Open-task and blocker
   counts come from the project db / `Event` rows. Cap at `max_projects`; if more,
   append `- … <n> more projects, use list_projects to see them`.
   **Rationale to put in the docstring:** full `summary.md` text for every project
   is what bloats the prompt; the agent pulls depth on demand via
   `get_project_state` / `get_project_doc`. Ids + names + a health signal is what it
   needs to *decide where to look*.
3. `## TEAM` — `- <employee_id> | <name> | <role> | slack:<yes|no>` per person, from
   `context.team_roster_for_manager(manager_id)`. Cap at `max_roster`, same
   `… n more` overflow marker. The agent needs ids to tag assignees/attendees, so
   this must be complete-or-explicitly-truncated, never silently short.
4. `## YOUR DURABLE MEMORY` — `memory.md`, **tail-truncated** to
   `max_memory_chars` (the tail is the freshest synthesis). Only when
   `include_memory`. Omit the whole section if the file is missing/empty.
5. `## KB CONVENTIONS` — only when `include_conventions`. The 7 event types with
   one-line definitions, the 0–3 severity doctrine, and the rule that `general` is
   derived from `project_ids` (never model-supplied). Keep this under 900 chars.
6. `## NOW` — **must be the LAST section.** Emitted from
   `app.timeservice.now_ist()`:
   `Current date and time: <YYYY-MM-DD HH:MM:SS> IST (<Weekday>)`.
   Recency at the end of the prompt is deliberate; do not move it.

Enforcement: after assembly, if the string still exceeds
`budget.max_total_chars`, drop sections by priority (memory → conventions →
projects overflow) and append an explicit
`[context truncated to fit budget]` marker. **Never** blind-slice the whole
string — a half-cut section is worse than a dropped one.

Tests: assert `## NOW` is last; assert the timestamp matches a monkeypatched
`now_ist`; assert a 200-project manager produces a context under
`max_total_chars` with the overflow marker present; assert team ids appear in
full when under the cap.

---

## 3. Tool-result size limits + compaction (`app/agent/registry.py`)

Today `ToolRegistry.execute` blind-slices at 8000 chars and appends
`"... [truncated]"` — which produces **invalid JSON** the model then has to guess at.
Replace with structure-aware compaction.

- `register(name, schema, handler, max_result_chars: int = 4000)` — store it on
  `ToolEntry`. Existing `register_tools()` calls keep working via the default.
- New `app/agent/result_compaction.py::compact_tool_result(result, max_chars) -> str`:
  - **list of items** → drop items from the END until it fits, return
    `{"items": [...], "_truncated": {"shown": n, "total": m, "hint": "narrow your filters or use a more specific tool"}}`
  - **dict with oversized string values** → truncate the largest string fields
    first, marking each with `… [truncated]`, keeping all keys present.
  - **anything else** → `json.dumps`, hard-slice, wrap as
    `{"_raw": "<sliced>", "_truncated": true}`.
  - Guarantee: the returned string is **always valid JSON** and **always**
    `<= max_chars`. Test both properties with a property-style test over several
    shapes.
- `execute()` uses it. The 8000-char global clamp is replaced by the per-tool cap.

---

## 4. `app/agent/runner.py` (new) — the generic loop

```python
@dataclass(frozen=True)
class AgentSpec:
    name: str
    model: str
    instructions: str          # the agent's own playbook, prepended to context
    tool_names: tuple[str, ...]  # explicit allowlist
    max_llm_calls: int
    max_tool_calls: int
    deadline_seconds: float
    temperature: float = 0.2

@dataclass
class AgentRunResult:
    reply: str | None
    tool_trace: list[dict]
    llm_calls: int
    tool_calls: int
    tokens_in: int
    tokens_out: int
    stop_reason: str   # final | budget | deadline | tool_cap | empty_response | error
    error: str | None = None

def run_spec(spec, db, manager_id, seed_message, *, client=None,
             history=None, run_context=None, context_text=None) -> AgentRunResult
```

System prompt = `spec.instructions` + `"\n\n"` + (`context_text` or
`build_kb_context(...)`). Because `## NOW` is the last context section, the
timestamp lands at the very end of the system prompt. Keep the existing
assistant-turn echo for Gemini turn symmetry (`harness.py:81-97`) — it is load-bearing.

**Five independent stop conditions. Each needs its own test asserting both
`stop_reason` and `transport.call_count`:**

1. `max_llm_calls` → `stop_reason="budget"`
2. `deadline_seconds` → checked **before** each LLM call, using `time.monotonic()`.
   `stop_reason="deadline"`
3. `max_tool_calls` cumulative → `stop_reason="tool_cap"`
4. **Repeat-call guard** — key on `sha256(name + json.dumps(args, sort_keys=True))`.
   On the 3rd identical call, do **not** re-execute: return the cached prior result
   with a prefix `"[repeat] You already called this with identical arguments. Do
   not call it again — use the result below and move on."` Returning an `ERROR:`
   string here makes models thrash; returning the real data does not.
5. Dead turn (no content **and** no tool calls) → `stop_reason="empty_response"`.
   Today this silently yields the "ran out of steps" text.

Also: accumulate `usage.promptTokenCount`/`candidatesTokenCount` from every
`chat()` response into `tokens_in`/`tokens_out`. `gemini_client.chat` already
returns `usage`; the current harness discards it.

`json_mode` must **never** be set when `tools` is passed — Gemini rejects
`responseMimeType: application/json` alongside function declarations. Add a test
asserting the outgoing payload never contains both.

Tool allowlist: add `ToolRegistry.get_tool_definitions(names: tuple | None = None)`
filtering by name, and have `execute()` reject a call to a tool outside the
allowlist with a correctable message (not a crash). Unknown names in
`spec.tool_names` should raise at spec-construction/run time — a typo must fail
loud, not silently hand the agent fewer tools.

---

## 5. Rewire `harness.run_agent` as a wrapper

`run_agent(db, manager_id, user_message, history, client=None, candidates=None)`
keeps its exact signature and return shape `{"reply", "tool_trace"}`. Internally it
builds a chat `AgentSpec` — `max_llm_calls=20` (today's `IterationBudget(limit=20)`),
all 9 tool names, `SMART_MODEL`, `temperature=0.2`, a generous deadline — and uses
`compile_system_prompt(db, manager_id, candidates)` as `context_text` so the chat
agent's prompt is **byte-identical to today**. Preserve the exhaustion fallback
string at `harness.py:132` verbatim.

`tests/test_agent.py`, `tests/test_agent_tools.py` and
`tests/test_agent_heartbeat_job.py` must pass **unmodified**. If one fails, the
wrapper is wrong — fix the wrapper, not the test.

---

## 6. Config (`app/config.py`)

Add, each `os.getenv`-overridable with a comment explaining the number:
`LLM_MAX_CALLS_PER_MINUTE=15`, `AGENT_DEFAULT_TOOL_RESULT_CHARS=4000`,
`KB_HEARTBEAT_MAX_LLM_CALLS=6`, `KB_HEARTBEAT_PROJECT_MAX_LLM_CALLS=5`,
`KB_DREAM_MAX_LLM_CALLS=6`, `KB_DREAM_PROJECT_MAX_LLM_CALLS=6`,
`KB_LINT_MAX_LLM_CALLS=4`, `KB_SYNTHESIS_MAX_LLM_CALLS=12`,
`KB_AGENT_DEADLINE_SECONDS=180`.

---

## 7. Regression test: the Gemini schema bug (CLAUDE.md critical bug #1)

The next steps introduce batch write tools whose schema is an **array of objects
with a property literally named `title`** — e.g.
`emit_events(events=[{type, severity, title, body, project_ids[], claim_ids[]}])`.

`sanitize_gemini_schema` (gemini_client.py:13) special-cases `properties` so
property *names* survive. I verified by reading that it recurses correctly through
`items` → `properties`, so nested `items.properties.title` should survive. **Pin it
with a test anyway**, since a regression here 400s the entire tools payload:

Build that exact nested schema, push it through
`sanitize_gemini_schema` → `map_tools_to_gemini`, and assert `title` is still
present inside the nested item schema **and** still listed in the item's
`required`.

---

## Definition of done

- `.venv/bin/python3 -m pytest tests/` — 0 failures.
- New tests cover: all 5 stop conditions, token accounting, tool allowlist
  filtering + rejection, rate limiter (including the 0 = disabled path),
  compaction (valid JSON + size guarantee across shapes), context section order /
  `## NOW` last / overflow markers, the nested-schema regression, and the
  no-`json_mode`-with-tools invariant.
- No behaviour change to `/api/chat`, the Slack DM path, or `agent_heartbeat`.
- Every new module has a docstring explaining *why*, matching the density of the
  surrounding code. Do not add narrative comments about what changed.
