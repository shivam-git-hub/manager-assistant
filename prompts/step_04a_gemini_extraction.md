# Task: Gemini Client + Flash Claim Extraction (per-message KB ingestion)

## Context

Repo: `manager-assistant` — FastAPI + SQLAlchemy 2.0 + SQLite. Server:
`.venv/bin/python3 -m app.main` (port 3003). Tests:
`.venv/bin/python3 -m pytest tests/`.

First read: `CLAUDE.md`, `spec/research/hermes_index.md` (section "The
liftable gold: native Gemini client"), `spec/research/gbrain_index.md`
(attributed claims), `app/config.py` (SMART_MODEL/FLASH_MODEL/
GEMINI_API_KEY), `app/kb/models.py`, `app/kb/api.py`, `app/database.py`
(UnifiedMessage: `is_processed`, `processed_at`).

**This prompt was written ahead of time. If any detail here contradicts the
current code (Step 3 may have evolved during review), THE CODE WINS — adapt.**

**Why this feature:** the cheap real-time half of the dream cycle. Every
inbound message gets claims extracted by the flash model and a timeline entry
appended, so the KB stays current without expensive synthesis on every
message (that's Step 4b).

**Project rules:**
- TDD first. All datetimes via `app.timeservice` (guard test enforces).
- No new pip dependencies — `httpx` is already installed (used by the test
  client); use it for the Gemini REST calls. NO google SDK, NO LangChain.
- **Tests must NEVER hit the network.** The client must be mockable (see
  Subproblem 1's injection design); every test fakes the LLM.
- Keep `CLAUDE.md` untouched (the reviewer updates it).

## Subproblem 0 — Spec file

`spec/feature_07_extraction.md`: client interface, extraction prompt design,
JSON output contract, processing endpoint, test plan.

## Subproblem 1 — Gemini client (`app/agent/gemini_client.py`, new pkg `app/agent/`)

A compact (~200-line) native-REST client, modeled on Hermes'
`gemini_native_adapter.py` (see the index; reimplementing from the notes is
fine — vendoring and trimming the original is also fine if easier):

- `POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}`
- OpenAI-shaped interface so the Step 6 harness can use it unchanged:
  ```python
  class GeminiClient:
      def __init__(self, api_key=None, transport=None): ...  # transport: injectable callable for tests
      def chat(self, model: str, messages: list[dict], tools: list[dict] | None = None,
               temperature: float = 0.3, max_output_tokens: int = 65535,
               json_mode: bool = False) -> dict:
          """returns {"content": str|None, "tool_calls": [{"id","name","arguments":dict}], "finish_reason": str, "usage": {...}}"""
  ```
- Convert `messages` (`role` user/assistant/system/tool) → Gemini `contents[]`
  + `systemInstruction`; `tools` (OpenAI `{name,description,parameters}`) →
  `functionDeclarations` with `sanitize_gemini_schema()` (strip `$schema`,
  `additionalProperties`, `title`, etc. recursively — allowed-keys set, per
  hermes_index). Translate the response back (functionCall parts → tool_calls).
- **Always send `maxOutputTokens`** (Gemini applies a low default otherwise —
  known quirk). `json_mode=True` sets `responseMimeType: "application/json"`.
- Retries: on 429/5xx, up to 3 attempts with 2s/4s/8s sleeps — BUT wall-clock
  `time.sleep` is fine here (it's real network backoff, not domain time);
  put the guard-test exemption ONLY if the guard flags `time.sleep` (it
  greps `time.time()`/`datetime.now()`, so plain `time.sleep` should pass).
- **Injection for tests**: `transport` is a callable
  `(url, json_payload) -> dict` defaulting to an httpx-based one. Module
  provides `get_client()` returning a cached singleton; tests monkeypatch
  `get_client` or pass a fake transport. Raise a clear error at call time
  (not import time) if `GEMINI_API_KEY` is unset.

## Subproblem 2 — Flash claim extraction (`app/kb/extraction.py`)

`extract_from_message(db, message: UnifiedMessage, client=None) -> dict`:

1. Skip (mark processed, return empty) if: sender is Harry
   (direction=="outbound"), or content is empty/trivial (<10 chars).
2. Build the flash prompt with deterministic context assembled in CODE:
   - the message (sender name, channel, timestamp, subject, content),
   - the candidate entity list: all `entities` rows (slug + name + type),
   - the team roster (id + name + role).
3. Ask FLASH_MODEL (json_mode=True) for STRICT JSON:
   ```json
   { "claims": [ { "entity_slug": "project:phoenix", "claim": "...",
                   "kind": "fact|status|commitment|blocker|opinion",
                   "holder": "U_BOB", "weight": 0.9 } ],
     "timeline_summary": "Bob says he sent the schema doc to Alice" }
   ```
   Prompt rules to encode: claims must be verifiable single statements;
   holder = the SENDER unless the message quotes someone else; weight 1.0
   only for first-person direct statements; entity_slug MUST come from the
   candidate list (or `null` if nothing matches); if the message has no
   project/work relevance, return `{"claims": [], "timeline_summary": null}`.
4. Validate in code (never trust the LLM): parse JSON (one repair retry on
   parse failure), drop claims with unknown entity_slug/kind, clamp weight
   to [0,1], drop unknown holder ids (fall back to sender).
5. Persist: each valid claim → `attributed_claims` row
   (`source_message_id=message.id`, `claimed_at=message.timestamp`); if
   `timeline_summary` present and ≥1 claim survived → ONE timeline entry per
   involved entity (`happened_at=message.timestamp`,
   `source_message_id=message.id`).
6. Always finish with `message.is_processed=True`,
   `processed_at=timeservice.now_ist()` — even for skips/LLM failure (log
   and move on; a poison message must not wedge the queue).

## Subproblem 3 — Processing endpoint

`POST /api/kb/process?limit=20` (in `app/kb/api.py`): fetch unprocessed
inbound messages oldest-first, run extraction on each, return
`{"processed": n, "claims_created": n, "timeline_entries_created": n,
"skipped": n}`. This is the hook Step 5's scheduler will call.

## Subproblem 4 — Tests (`tests/test_extraction.py`, write FIRST, all LLM-faked)

Build a `FakeGeminiClient` (records prompts, returns canned responses) in the
test file or a shared helper.

1. Client unit: messages→contents conversion, system message handling,
   schema sanitization strips `additionalProperties`, `maxOutputTokens`
   always present in payload (inspect via fake transport), tool_calls
   translated back.
2. Client retry: fake transport raises a 429-ish error twice then succeeds →
   result returned, 3 calls made.
3. Extraction happy path: seed entity + members + one inbound message; fake
   returns 2 valid claims + summary → claim rows with correct
   source_message_id/claimed_at/holder, timeline entry appended, message
   marked processed.
4. Validation: fake returns claim with `entity_slug:"project:nope"`, kind
   `"vibe"`, weight `3.0`, unknown holder → invalid parts dropped/clamped,
   nothing crashes.
5. Malformed JSON from fake (twice) → no claims, message STILL marked
   processed.
6. Harry's own outbound message → skipped, marked processed, LLM never called.
7. `POST /api/kb/process` end-to-end with fake client (monkeypatch
   `get_client`): 3 unprocessed messages → correct counts, none left
   unprocessed.
8. Full existing suite green; wall-clock guard clean.

## Definition of done

- [ ] Spec written; full suite green; guard clean
- [ ] Manual check WITH real key (GEMINI_API_KEY in config.json): start
      server, POST a realistic Slack webhook message ("Shipped the payment
      retry fix, phoenix backend is unblocked"), run `POST /api/kb/process`,
      then `GET /api/kb/entities/project:phoenix` shows the claim + timeline
      entry. Paste the output in your summary.
- [ ] Commit with a clear message
