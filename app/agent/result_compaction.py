"""Structure-aware tool-result compaction, replacing the old blind
str[:8000] + "... [truncated]" clamp in app.agent.registry.ToolRegistry.execute.
A blind slice on a JSON-serialized dict/list very often lands mid-token,
handing the model invalid JSON it then has to guess at (or, worse, silently
mis-parses). Every shape below instead drops/marks whole structural units so
the result is always valid JSON, and always <= max_chars -- both properties
are load-bearing for the model's function-response turn (see
gemini_client.messages_to_gemini_contents, which parses tool content as
JSON before wrapping it as a Gemini functionResponse struct).
"""
import json
from typing import Any


def _dumps(value: Any) -> str:
    return json.dumps(value, default=str)


def _fallback_raw(result: Any, max_chars: int) -> str:
    """Last-resort shape: anything that isn't a list/dict, or a list/dict
    where even the minimal envelope would overflow max_chars. Always fits:
    the raw slice is shrunk (re-encoding each time) until the whole wrapper
    is within budget."""
    raw = result if isinstance(result, str) else _dumps(result)
    sliced = raw[: max(max_chars, 0)]
    out = _dumps({"_raw": sliced, "_truncated": True})
    while len(out) > max_chars and sliced:
        sliced = sliced[: len(sliced) // 2] if len(sliced) > 1 else ""
        out = _dumps({"_raw": sliced, "_truncated": True})
    if len(out) > max_chars:
        # max_chars is smaller than `{"_raw":"","_truncated":true}` itself --
        # there's no valid-JSON rendering of the wrapper that fits. Fall
        # back to the smallest valid JSON document that DOES fit: "{}" (2
        # chars) normally, a single digit for the pathological max_chars==1
        # case. max_chars<=0 has no valid non-empty JSON document at all --
        # returns "" as a documented, unavoidable exception to the
        # otherwise-universal "always valid JSON" guarantee.
        if max_chars <= 0:
            out = ""
        elif max_chars == 1:
            out = "0"
        else:
            out = "{}"
    return out


def _compact_list(items: list, max_chars: int) -> str:
    shown = list(items)
    while shown:
        encoded = _dumps({
            "items": shown,
            "_truncated": {
                "shown": len(shown),
                "total": len(items),
                "hint": "narrow your filters or use a more specific tool",
            },
        })
        if len(encoded) <= max_chars:
            return encoded
        shown.pop()
    # Even a single item plus the truncation envelope doesn't fit.
    return _fallback_raw(items, max_chars)


def _compact_dict(result: dict, max_chars: int) -> str:
    """Truncates oversized string-valued fields, largest first, keeping
    every key present. Implemented as a binary search over a single
    uniform per-field cap (only fields longer than the cap get cut), not
    repeated halve-and-remeasure of one field at a time: halving an
    already-marked value's *total* length (content + the fixed-size
    "... [truncated]" marker) can converge to a fixed point around ~2x the
    marker's own length and loop forever, since the marker gets re-appended
    to a content slice of roughly the same size every iteration. A
    monotonic search on the cap alone has no such fixed point and is
    bounded by log2(longest field)."""
    marker = "… [truncated]"
    string_keys = [k for k, v in result.items() if isinstance(v, str)]
    if not string_keys:
        # Oversized for some other reason (e.g. a huge nested list/dict
        # value) -- nothing string-shaped to shrink.
        return _fallback_raw(result, max_chars)

    def build(cap: int) -> dict:
        working = dict(result)
        for k in string_keys:
            v = working[k]
            if len(v) > cap:
                working[k] = v[:cap] + marker
        return working

    lo, hi = 0, max(len(result[k]) for k in string_keys)
    best_encoded = None
    while lo <= hi:
        mid = (lo + hi) // 2
        encoded = _dumps(build(mid))
        if len(encoded) <= max_chars:
            best_encoded = encoded
            lo = mid + 1
        else:
            hi = mid - 1

    if best_encoded is not None:
        return best_encoded
    return _fallback_raw(result, max_chars)


def compact_tool_result(result: Any, max_chars: int) -> str:
    """Serializes `result` (already a Python value -- dict/list/str/etc,
    never pre-JSON-encoded) to a JSON string, compacting it to fit
    `max_chars` only if the plain encoding overflows. Guarantees: the
    return value is always valid JSON (json.loads never raises on it) and
    always `len(return) <= max_chars`.

    - Fits as-is -> returned verbatim (json.dumps), no markers added.
    - list, oversized -> drop items from the END until it fits; the
      envelope carries shown/total/hint so the model knows to narrow its
      query rather than silently treating a partial list as the whole
      answer.
    - dict, oversized -> shrink its largest string field(s) first, keeping
      every key present.
    - anything else, oversized -> wrapped as
      {"_raw": <sliced>, "_truncated": true}.
    """
    full = _dumps(result)
    if len(full) <= max_chars:
        return full
    if isinstance(result, list):
        return _compact_list(result, max_chars)
    if isinstance(result, dict):
        return _compact_dict(result, max_chars)
    return _fallback_raw(result, max_chars)
