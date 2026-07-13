# Feature 07: Gemini REST Client and Flash Claim Extraction

This specification defines the lightweight, dependency-free native Gemini REST client and the real-time Flash model claim extraction system that processes inbound communication records into the structured KB.

---

## 1. Motivation

Harry needs a robust, fast, and light LLM execution bridge that doesn't introduce large third-party framework overheads like LangChain or LangGraph. By building a compact native REST-based Gemini client directly over `httpx`, we:
*   Ensure full control over request-response structures.
*   Remain highly testable by injecting mock network transport layers instead of hitting the real API during tests.
*   Enforce a deterministic real-time processing path (the first half of gbrain's "Dream Cycle") that extracts verifiable claims and appends timeline entries on a per-message basis.

---

## 2. Native Gemini REST Client

The client is implemented at `app/agent/gemini_client.py`.

### A. Interface
```python
class GeminiClient:
    def __init__(self, api_key: Optional[str] = None, transport: Optional[Callable] = None):
        ...
    def chat(self, model: str, messages: list[dict], tools: list[dict] | None = None,
             temperature: float = 0.3, max_output_tokens: int = 65535,
             json_mode: bool = False) -> dict:
        ...
```
*Returns*:
```json
{
  "content": "...",
  "tool_calls": [
    {
      "id": "call_abc123",
      "name": "tool_name",
      "arguments": {}
    }
  ],
  "finish_reason": "STOP",
  "usage": {
    "prompt_tokens": 120,
    "completion_tokens": 45,
    "total_tokens": 165
  }
}
```

### B. Mappings
*   **System instructions**: Extracted from messages with `role: "system"` and mapped to `systemInstruction` in Gemini's API config.
*   **User/Assistant contents**: Mapped to standard `contents[]` array structure.
*   **Tool schemas**: Cleaned recursively via `sanitize_gemini_schema()` to strip metadata keys (like `$schema`, `additionalProperties`, `title`) that Gemini's function call validator rejects.
*   **JSON Mode**: Setting `json_mode=True` configures `responseMimeType: "application/json"`.

---

## 3. Flash Claim Ingestion Engine

Module: `app/kb/extraction.py`

### A. API Contract
`extract_from_message(db: Session, message: UnifiedMessage, client: Optional[GeminiClient] = None) -> dict`

### B. Ingest Prompt Shape
```json
{
  "claims": [
    {
      "entity_slug": "project:phoenix",
      "claim": "Alice has finished the backend SQLite migrations",
      "kind": "fact",
      "holder": "U_ALICE",
      "weight": 1.0
    }
  ],
  "timeline_summary": "Alice finished writing standard SQLAlchemy migrations"
}
```

### C. Validation & Safety Guards
1.  **Skip Trivial Messages**: Skip and mark `is_processed=True` on Harry's own outbound messages or text under 10 characters.
2.  **Entity Integrity**: Drop claims referencing non-existent entity slugs.
3.  **Categories Check**: Validate `kind` matches one of the five DB-allowed values.
4.  **Range Check**: Clamp weight directly within `[0.0, 1.0]`.
5.  **Poison-Message Isolation**: If JSON format parse fails, swallow the error, mark `is_processed=True`, and log the trace to prevent blocking downstream queue execution.

---

## 4. Inbound Processing Endpoint

### Endpoint: `POST /api/kb/process?limit=20`
Triggers real-time flash ingestion over unprocessed unified messages, oldest first.

### Response Payload:
```json
{
  "processed": 5,
  "claims_created": 3,
  "timeline_entries_created": 2,
  "skipped": 2
}
```

---

## 5. Test Plan

We implement `tests/test_extraction.py` to validate:
1.  API payload conversions of `GeminiClient` (roles mapped, instructions set, tool sanitizations).
2.  Mock network backoffs and retries.
3.  Successful ingestion and persistence of extracted claims and timeline rows.
4.  Validation and truncation of invalid kinds, weights, and holder profiles.
5.  Seamless error handling for malformed JSON returns.
6.  Auto-marking and skipping logic on outbound pings.
