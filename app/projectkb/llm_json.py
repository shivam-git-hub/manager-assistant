"""Shared parse-the-model's-structured-JSON helpers for the projectkb jobs
(ingest, heartbeat, ...) -- each sends one structured-output LLM call and
expects back a JSON object, tolerant of malformed/off-shape responses
since a bad response must never crash a scheduler tick."""
import json
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def parse_json_object(res: Dict[str, Any], log_prefix: str) -> Dict:
    content_str = res.get("content") or "{}"
    try:
        parsed = json.loads(content_str)
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
