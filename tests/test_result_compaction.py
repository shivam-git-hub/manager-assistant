"""app.agent.result_compaction.compact_tool_result -- replaces the old
blind str[:8000]+"...[truncated]" clamp. Two invariants must hold for every
shape thrown at it: the return is always valid JSON, and always
<= max_chars. Property-style: assert both invariants across many shapes
and many max_chars values, not just one golden case each."""
import json

import pytest

from app.agent.result_compaction import compact_tool_result


def _assert_invariants(result, max_chars):
    encoded = compact_tool_result(result, max_chars)
    assert isinstance(encoded, str)
    assert len(encoded) <= max_chars, f"{len(encoded)} > {max_chars} for {result!r} @ {max_chars}"
    json.loads(encoded)  # must not raise
    return encoded


# -----------------------------------------------------------------------------
# Fits as-is -> untouched, no markers
# -----------------------------------------------------------------------------


def test_small_dict_fits_untouched():
    result = {"success": True, "task_id": "abc123"}
    encoded = _assert_invariants(result, 4000)
    assert json.loads(encoded) == result


def test_small_list_fits_untouched():
    result = [{"id": "1"}, {"id": "2"}]
    encoded = _assert_invariants(result, 4000)
    assert json.loads(encoded) == result


def test_small_scalar_fits_untouched():
    encoded = _assert_invariants("all good", 4000)
    assert json.loads(encoded) == "all good"


# -----------------------------------------------------------------------------
# List: drop from the end, envelope with shown/total/hint
# -----------------------------------------------------------------------------


def test_oversized_list_drops_from_end_and_reports_counts():
    items = [{"id": str(i), "title": "x" * 50} for i in range(500)]
    encoded = _assert_invariants(items, 2000)
    parsed = json.loads(encoded)
    assert "items" in parsed and "_truncated" in parsed
    trunc = parsed["_truncated"]
    assert trunc["total"] == 500
    assert trunc["shown"] == len(parsed["items"])
    assert trunc["shown"] < trunc["total"]
    assert "hint" in trunc
    # Dropped from the END: surviving items are a prefix of the original list.
    assert [i["id"] for i in parsed["items"]] == [str(i) for i in range(trunc["shown"])]


# -----------------------------------------------------------------------------
# Dict: shrink largest oversized string field(s), keep every key
# -----------------------------------------------------------------------------


def test_oversized_dict_shrinks_largest_string_keeps_all_keys():
    result = {"id": "t1", "title": "short", "description": "y" * 5000, "status": "todo"}
    encoded = _assert_invariants(result, 1000)
    parsed = json.loads(encoded)
    assert set(parsed.keys()) == set(result.keys())
    assert parsed["id"] == "t1"
    assert parsed["status"] == "todo"
    assert len(parsed["description"]) < len(result["description"])
    assert "truncated" in parsed["description"]


# -----------------------------------------------------------------------------
# Anything else -> {"_raw": ..., "_truncated": true}
# -----------------------------------------------------------------------------


def test_oversized_scalar_wrapped_as_raw():
    encoded = _assert_invariants("Z" * 9000, 500)
    parsed = json.loads(encoded)
    assert parsed["_truncated"] is True
    assert "_raw" in parsed


def test_oversized_custom_object_wrapped_as_raw():
    # A type json.dumps can't natively handle -- default=str must kick in
    # without ever raising, and (since its str() is large) the oversized
    # _raw-wrapping path must engage.
    class Weird:
        def __str__(self):
            return "weird-value-" * 200

    encoded = _assert_invariants(Weird(), 200)
    parsed = json.loads(encoded)
    assert parsed["_truncated"] is True
    assert "_raw" in parsed


def test_small_custom_object_fits_untouched():
    # A tiny non-serializable object that fits comfortably -- must NOT be
    # wrapped in the oversized envelope (only the general JSON-string
    # encoding via default=str applies).
    class Small:
        def __str__(self):
            return "tiny"

    encoded = _assert_invariants(Small(), 200)
    assert json.loads(encoded) == "tiny"


# -----------------------------------------------------------------------------
# Pathologically small budgets -- even the minimal envelope overflows
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("max_chars", [1, 5, 10, 20])
def test_pathologically_small_budget_still_holds_invariants(max_chars):
    _assert_invariants([{"id": str(i)} for i in range(50)], max_chars)
    _assert_invariants({"description": "x" * 5000}, max_chars)
    _assert_invariants("y" * 5000, max_chars)


# -----------------------------------------------------------------------------
# Property sweep across shapes and budgets
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("max_chars", [50, 200, 1000, 4000])
@pytest.mark.parametrize(
    "result",
    [
        [],
        [{"id": i, "title": "x" * 30} for i in range(200)],
        {"a": "b" * 8000, "c": "d" * 8000, "e": "small"},
        {"nested": {"deep": [1, 2, 3]}},
        None,
        12345,
        True,
        "plain string" * 500,
    ],
)
def test_invariants_hold_across_shapes_and_budgets(result, max_chars):
    _assert_invariants(result, max_chars)
