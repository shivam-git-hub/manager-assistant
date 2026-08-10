import json
import logging
from dataclasses import dataclass
from typing import Callable, Any, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session

from app.agent.result_compaction import compact_tool_result
from app.config import AGENT_DEFAULT_TOOL_RESULT_CHARS

logger = logging.getLogger(__name__)

@dataclass
class ToolEntry:
    name: str
    schema: Dict[str, Any]
    handler: Callable[..., Any]
    max_result_chars: int = AGENT_DEFAULT_TOOL_RESULT_CHARS

class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, ToolEntry] = {}

    def register(
        self,
        name: str,
        schema: Dict[str, Any],
        handler: Callable[..., Any],
        max_result_chars: int = AGENT_DEFAULT_TOOL_RESULT_CHARS,
    ) -> None:
        """
        Registers a tool with its name, schema, handler function, and a
        per-tool result-size cap (see app.agent.result_compaction). Existing
        callers that don't pass max_result_chars get the shared default --
        this parameter is additive, not a required migration.
        """
        self._tools[name] = ToolEntry(name=name, schema=schema, handler=handler, max_result_chars=max_result_chars)

    def known_names(self) -> set:
        """Every registered tool name -- used by app.agent.runner.run_spec
        to validate an AgentSpec.tool_names allowlist loudly (a typo'd tool
        name must raise, not silently hand the agent fewer tools)."""
        return set(self._tools.keys())

    def get_tool_definitions(self, names: Optional[Tuple[str, ...]] = None) -> List[Dict[str, Any]]:
        """
        Returns OpenAI-compatible tool/function declarations. `names=None`
        (the default) returns every registered tool -- existing callers
        (the chat harness today) are unaffected. Passing a tuple filters to
        just those names, in registry order, for an AgentSpec's explicit
        allowlist (app.agent.runner.AgentSpec.tool_names).
        """
        if names is None:
            return [tool.schema for tool in self._tools.values()]
        return [self._tools[n].schema for n in names if n in self._tools]

    def execute(
        self,
        name: str,
        args: Dict[str, Any],
        db: Session,
        manager_id: str = None,
        run_context: Dict[str, Any] = None,
        allowed_names: Optional[Tuple[str, ...]] = None,
    ) -> str:
        """
        Executes a registered tool handler with the parsed arguments, DB
        session, the owning manager_id, and a per-run scratch dict (todo
        list state etc). Gracefully handles exceptions and returns
        "ERROR: <msg>". `allowed_names=None` (the default) means
        unrestricted -- every existing direct-execute call site (chat
        harness, tests) keeps working unchanged; an AgentSpec-scoped runner
        passes its own tool_names tuple so a call to a tool outside the
        agent's allowlist gets a correctable error message back (not a
        crash, so the model can pick a different tool) instead of silently
        running.

        The result is compacted (app.agent.result_compaction) to the tool's
        own max_result_chars, replacing the old blind global 8000-char
        slice -- guarantees valid JSON and a hard size cap in one pass.
        """
        if allowed_names is not None and name not in allowed_names:
            return f"ERROR: Tool '{name}' is not in this agent's allowed tool list: {list(allowed_names)}."

        if name not in self._tools:
            return f"ERROR: Tool '{name}' is not registered."

        tool = self._tools[name]
        try:
            # We call the handler with db session and unpack args
            # Using keyword arguments so that python maps it properly
            result = tool.handler(db=db, manager_id=manager_id, run_context=run_context, **args)
        except Exception as e:
            logger.error(f"Error executing tool '{name}' with args {args}: {e}", exc_info=True)
            result = f"ERROR: {str(e)}"

        return compact_tool_result(result, tool.max_result_chars)

# Global registry instance
registry = ToolRegistry()
