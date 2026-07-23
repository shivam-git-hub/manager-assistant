import json
import logging
from dataclasses import dataclass
from typing import Callable, Any, Dict, List
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

@dataclass
class ToolEntry:
    name: str
    schema: Dict[str, Any]
    handler: Callable[..., Any]

class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, ToolEntry] = {}

    def register(self, name: str, schema: Dict[str, Any], handler: Callable[..., Any]) -> None:
        """
        Registers a tool with its name, schema, and handler function.
        """
        self._tools[name] = ToolEntry(name=name, schema=schema, handler=handler)

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """
        Returns OpenAI-compatible tool/function declarations for all registered tools.
        """
        return [tool.schema for tool in self._tools.values()]

    def execute(
        self,
        name: str,
        args: Dict[str, Any],
        db: Session,
        manager_id: str = None,
        run_context: Dict[str, Any] = None,
    ) -> str:
        """
        Executes a registered tool handler with the parsed arguments, DB session,
        the owning manager_id, and a per-run scratch dict (todo list state etc.).
        Gracefully handles exceptions and returns "ERROR: <msg>".
        Caps the output string at 8000 characters and appends "... [truncated]" if exceeded.
        """
        if name not in self._tools:
            return f"ERROR: Tool '{name}' is not registered."

        tool = self._tools[name]
        try:
            # We call the handler with db session and unpack args
            # Using keyword arguments so that python maps it properly
            result = tool.handler(db=db, manager_id=manager_id, run_context=run_context, **args)
            
            # Convert result to string
            if isinstance(result, (dict, list)):
                result_str = json.dumps(result, default=str)
            else:
                result_str = str(result)
                
        except Exception as e:
            logger.error(f"Error executing tool '{name}' with args {args}: {e}", exc_info=True)
            result_str = f"ERROR: {str(e)}"

        # Clamp the result at 8000 chars
        if len(result_str) > 8000:
            result_str = result_str[:8000] + "... [truncated]"

        return result_str

# Global registry instance
registry = ToolRegistry()
