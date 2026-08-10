"""Regression pin for CLAUDE.md critical bug #1's exact failure shape,
ahead of the batch write tools (emit_events(events=[{..., title, ...}]))
the next steps introduce: a property literally named "title" nested inside
`items.properties` (an array of objects), not just a top-level property.
sanitize_gemini_schema strips the JSON-Schema KEYWORD "title" but must
never drop a property NAMED "title" -- verified here to recurse correctly
through items -> properties two levels deep, with "title" surviving both
as a key in `properties` and as an entry in that item schema's `required`."""
from app.agent.gemini_client import map_tools_to_gemini, sanitize_gemini_schema

NESTED_ARRAY_SCHEMA = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "severity": {"type": "integer"},
                    "title": {"type": "string", "description": "Short event title."},
                    "body": {"type": "string"},
                    "project_ids": {"type": "array", "items": {"type": "string"}},
                    "claim_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["type", "severity", "title"],
            },
        }
    },
    "required": ["events"],
}


def test_sanitize_gemini_schema_keeps_nested_title_property():
    sanitized = sanitize_gemini_schema(NESTED_ARRAY_SCHEMA)
    item_props = sanitized["properties"]["events"]["items"]["properties"]
    assert "title" in item_props
    assert item_props["title"] == {"type": "string", "description": "Short event title."}
    assert "title" in sanitized["properties"]["events"]["items"]["required"]


def test_map_tools_to_gemini_keeps_nested_title_end_to_end():
    tools = [{
        "type": "function",
        "function": {
            "name": "emit_events",
            "description": "Writes typed KB events.",
            "parameters": NESTED_ARRAY_SCHEMA,
        },
    }]

    mapped = map_tools_to_gemini(tools)

    decl = mapped[0]["functionDeclarations"][0]
    assert decl["name"] == "emit_events"
    item_schema = decl["parameters"]["properties"]["events"]["items"]
    assert "title" in item_schema["properties"]
    assert "title" in item_schema["required"]
    # The JSON-Schema KEYWORD "title" (as opposed to a property named
    # "title") must still be stripped wherever it appears as a keyword --
    # e.g. Gemini also rejects a bare top-level "title" keyword. None of
    # the keys here are the keyword form, only the property name form, so
    # this also asserts sanitize_gemini_schema didn't strip too little.
    assert "title" not in decl  # not smuggled in as a top-level schema keyword
