"""Shared parse-the-model's-structured-JSON helpers for the projectkb jobs
(ingest, heartbeat, ...) -- each sends one structured-output LLM call and
expects back a JSON object, tolerant of malformed/off-shape responses
since a bad response must never crash a scheduler tick."""
import json
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _extract_json_object_substring(text: str) -> str:
    """Best-effort recovery for a gateway model that doesn't honor
    json_mode strictly (seen on Llama-behind-a-gateway backends, see
    app.agent.safechain_client): strips a markdown code fence if present,
    then slices from the first '{' to the LAST '}' in the text so a stray
    leading/trailing sentence ("Sure, here is the JSON: {...} Let me know if
    you need anything else!") doesn't fail json.loads outright. Not a
    parser -- the caller still validates the result is a dict."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t.lstrip("`")
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
        t = t.strip()
    start = t.find("{")
    end = t.rfind("}")
    if start != -1 and end != -1 and end > start:
        return t[start:end + 1]
    return t


def parse_json_object(res: Dict[str, Any], log_prefix: str) -> Dict:
    content_str = res.get("content") or "{}"
    try:
        parsed = json.loads(content_str)
    except json.JSONDecodeError:
        # Strict parse failed -- try the tolerant fallback before giving up.
        # A bad ingestion/heartbeat response must never crash the tick, but
        # silently returning {} on every response this backend sends
        # produces a permanently-empty KB with no visible cause, which is
        # worse than the extra recovery attempt costs.
        recovered = _extract_json_object_substring(content_str)
        try:
            parsed = json.loads(recovered)
            logger.info(f"{log_prefix} recovered JSON after stripping fence/prose wrapper "
                        f"(raw response did not parse as strict JSON)")
        except json.JSONDecodeError:
            logger.warning(f"{log_prefix} failed to parse LLM response as JSON: {content_str!r}")
            return {}
    if not isinstance(parsed, dict):
        logger.warning(f"{log_prefix} LLM response was valid JSON but not an object: {content_str!r}")
        return {}
    return parsed


def parse_json_list_field(res: Dict[str, Any], field: str, log_prefix: str) -> List[Dict]:
    items = parse_json_object(res, log_prefix).get(field, [])
    return items if isinstance(items, list) else []
