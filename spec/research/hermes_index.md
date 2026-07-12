# Hermes Agent Research Index

Explored 2026-07-12 from https://github.com/NousResearch/hermes-agent (MIT,
Python 3.11, ~6.3K files). Purpose: identify the harness skeleton and liftable
code for Harry's from-scratch agent (plan Step 6). Re-clone for deep dives.

## What it is / scale check

Nous Research's production personal agent: multi-platform gateway (Telegram/
Discord/Slack/WhatsApp/CLI/TUI), any-model support, skills with a
self-improvement loop, cron automations, subagent delegation, six terminal
backends. `agent/` alone is ~80K lines — 95% of it solves problems we don't
have (credential pools, context compression, streaming TUIs, MoA, transports,
platform gateways). We lift the ~5% skeleton: tool registry, tool loop,
iteration budget, prompt tiers, and the Gemini adapter.

## The liftable gold: native Gemini client

`agent/gemini_native_adapter.py` (~1000 lines, **httpx only, no SDK**) +
`agent/gemini_schema.py` (99 lines). An OpenAI-shaped facade over Gemini's
native `models/{model}:generateContent` REST API:

- `build_gemini_request()` converts OpenAI-style `messages[]`/`tools[]` →
  Gemini `contents[]`/`functionDeclarations`; `translate_gemini_response()`
  converts back (incl. tool-call ids, finish reasons).
- `sanitize_gemini_schema()` — Gemini's `Schema` accepts only a subset of
  JSON Schema; this strips unsupported keys (`$schema`,
  `additionalProperties`, …) recursively (allowed-keys set at top of file).
- Quirk handled: Gemini applies a LOW internal default when `max_tokens`
  omitted → always send `maxOutputTokens` (they use 65535).
- Why native REST not the OpenAI-compat endpoint: auth churn, tool-call
  replay quirks, thought-signature requirements — native is canonical.
- Also has SSE streaming translation and `probe_gemini_tier()` (free-tier
  quota detection). We can vendor a trimmed copy (drop streaming, multimodal,
  credential-pool hooks) for `app/agent/gemini_client.py`.

## Tool system (`tools/registry.py`, 801 lines → copy the pattern, not the file)

- Each tool module self-registers at import:
  `registry.register(name=, toolset=, schema=, handler=, check_fn=, emoji=)`.
- `schema` is OpenAI-style: `{"name", "description", "parameters": {JSON
  Schema object}}`. Descriptions are instructional prose — see
  `tools/todo_tool.py` `TODO_SCHEMA` for the house style (usage rules, param
  semantics, invariants like "only ONE item in_progress" written INTO the
  description).
- `handler(args: dict, **kw)` — kw carries runtime context (e.g. store).
- `ToolEntry` fields: name, toolset, schema, handler, check_fn, requires_env,
  is_async, emoji, max_result_size_chars, dynamic_schema_overrides (zero-arg
  callable merged into schema at definitions-time for runtime-dependent text).
- Toolsets group tools for enable/disable; `check_fn` availability probes are
  TTL-cached (30s) with a flake-suppression grace window.
- `discover_builtin_tools()` AST-scans tool modules for top-level
  `registry.register(` before importing — cute, we don't need it (explicit
  import list suffices for ~10 tools).

## The loop (`agent/conversation_loop.py` 5355 lines → reimplement in ~150)

Essence of `run_conversation()`:
1. Prologue (`build_turn_context`): sanitize input, restore-or-build system
   prompt, load history, persist user message.
2. Loop: call LLM with messages+tool defs → if response has tool_calls:
   execute them (`tool_executor.py` has sequential + concurrent paths;
   concurrent uses a thread pool, results appended in original call order),
   append `{"role":"tool", "tool_call_id", "content"}` results, continue;
   else → final response.
3. Bounded by `IterationBudget` — `agent/iteration_budget.py` is a 62-line
   thread-safe consume/refund counter (parent default 90, subagent 50).
   **Copy verbatim.**
4. Epilogue: persist history, title generation, memory review.
Everything else in the file (failover, compression retries, codex modes,
entitlement guidance) — skip.

## System prompt (`agent/system_prompt.py`)

Built ONCE per session and cached — only context compression rebuilds it —
to keep the provider prefix-cache warm. Three tiers joined with `\n\n`:
- **stable**: identity (SOUL.md → our Harry persona file), tool guidance,
  skills, environment/platform hints.
- **context**: caller-supplied system message + discovered context files.
- **volatile**: memory snapshot, user profile, timestamp/session/model line.
→ Harry: same three-tier split; sim-time line goes in volatile; cache-key
insight matters if we use Gemini context caching later (probably not v1).

## Cron (`cron/`)

`CronScheduler` ABC + `InProcessCronScheduler` + persisted job specs
(`jobs.py`); jobs are natural-language prompts delivered to a platform;
`lifecycle_guard` blocks jobs from running gateway-lifecycle commands.
Superseded for us by the sim-time virtual scheduler (plan Step 5), but
`jobs.py`'s job-spec shape (schedule, prompt, delivery target, enabled) is a
good reference for our `scheduled_jobs` table.

## Timezone (`hermes_time.py`)

Single `now()` helper; IANA tz resolved once from env → config.yaml → server
local, cached with explicit `reset_cache()`. Mirrors our timeservice
philosophy (single clock authority); ours additionally simulates.

## Other patterns worth remembering (not v1)

- `tools/todo_tool.py` — session task list as a tool; merge=true/false
  replace-vs-update semantics.
- `agent/error_classifier.py` — classify provider errors into retry/fail/
  reauth buckets before deciding loop behavior.
- `agent/trajectory.py` + `trajectory_compressor.py` — full turn persistence
  for replay/training.
- `tools/tool_output_limits.py` / `max_result_size_chars` — hard caps on tool
  result size injected into context (we should cap KB search results too).
- Subagent delegation (`tools/delegate_tool.py`) with per-subagent budgets.

## What we deliberately DON'T take

Gateway/platforms (our simulator plays that role), TUI/desktop/web UIs,
skills hub + self-improvement loop, MCP client/server, computer use, voice/
media, credential pools/secret sources, MoA, context compression, terminal
backends, plugins system, i18n, Honcho memory.

## Harness blueprint for Harry (Step 6 sketch)

```
app/agent/
├── gemini_client.py   # vendored trim of gemini_native_adapter + gemini_schema
├── registry.py        # ~60-line ToolEntry dict {name → schema, handler}
├── budget.py          # IterationBudget verbatim
├── harness.py         # run_conversation(): prologue → tool loop → epilogue
├── prompts.py         # three tiers; stable=Harry persona+tool guidance,
│                      # volatile=sim time (timeservice) + manager context
└── tools/             # kb_search, get_project, list_tasks, update_task,
                       # get_claims, send_slack, send_email, schedule_followup,
                       # escalate_to_manager — OpenAI-style schemas,
                       # instructional descriptions per todo_tool house style
```
