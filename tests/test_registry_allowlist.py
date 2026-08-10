"""ToolRegistry allowlist filtering (get_tool_definitions(names=...)) and
execute()'s allowed_names rejection (step 33) -- the mechanism
app.agent.runner.AgentSpec.tool_names is built on. allowed_names=None stays
unrestricted so every pre-existing direct-execute call site (the chat
harness, tests/test_agent_tools.py) is unaffected."""
import json

from app.agent.registry import ToolRegistry


def _make_registry():
    reg = ToolRegistry()

    def add_handler(db, manager_id, run_context, x, y):
        return {"sum": x + y}

    def sub_handler(db, manager_id, run_context, x, y):
        return {"diff": x - y}

    reg.register("add", {"name": "add"}, add_handler)
    reg.register("sub", {"name": "sub"}, sub_handler)
    return reg


def test_get_tool_definitions_unfiltered_returns_all():
    reg = _make_registry()
    defs = reg.get_tool_definitions()
    assert {d["name"] for d in defs} == {"add", "sub"}


def test_get_tool_definitions_filtered_by_names():
    reg = _make_registry()
    defs = reg.get_tool_definitions(names=("add",))
    assert [d["name"] for d in defs] == ["add"]


def test_get_tool_definitions_filtered_ignores_unknown_names_silently():
    # get_tool_definitions is a display/allowlist filter, not the
    # loud-failure point -- run_spec is responsible for raising on an
    # unknown tool_names entry at spec-construction/run time (see
    # test_runner.py::test_unknown_tool_name_in_spec_raises).
    reg = _make_registry()
    defs = reg.get_tool_definitions(names=("add", "nonexistent"))
    assert [d["name"] for d in defs] == ["add"]


def test_execute_unrestricted_when_allowed_names_none(db_session, client):
    reg = _make_registry()
    res = reg.execute("sub", {"x": 5, "y": 2}, db_session, manager_id=client.manager_id)
    assert json.loads(res) == {"diff": 3}


def test_execute_allows_call_within_allowlist(db_session, client):
    reg = _make_registry()
    res = reg.execute("add", {"x": 1, "y": 2}, db_session, manager_id=client.manager_id, allowed_names=("add", "sub"))
    assert json.loads(res) == {"sum": 3}


def test_execute_rejects_call_outside_allowlist_with_correctable_message(db_session, client):
    reg = _make_registry()
    res = reg.execute("sub", {"x": 5, "y": 2}, db_session, manager_id=client.manager_id, allowed_names=("add",))
    assert res.startswith("ERROR:")
    assert "sub" in res
    assert "not in this agent's allowed tool list" in res
