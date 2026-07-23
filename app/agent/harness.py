import logging
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session

from app.agent.budget import IterationBudget
from app.agent.registry import registry
from app.agent.prompts import compile_system_prompt
from app.agent.gemini_client import get_client, GeminiClient
from app.config import SMART_MODEL
# Importing tools registers all 10 handlers on the shared registry as an import
# side-effect. Without this, the registry is empty at runtime and Harry answers
# with no tools (hallucinating). Tests happened to pass because test_agent.py
# imports this module directly; the running app never did.
import app.agent.tools  # noqa: F401

logger = logging.getLogger(__name__)

def run_agent(
    db: Session,
    manager_id: str,
    user_message: str,
    history: List[Dict[str, Any]],
    client: Optional[GeminiClient] = None,
    candidates: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """
    Core Hermes-style converse-and-execute loop for Harry.
    Compiles the dynamic system prompt (owner/project/team context, plus a
    candidate worklist when invoked from the agent heartbeat job), routes
    history, calls tool handlers via ToolRegistry, and preserves turn
    symmetry. `run_context` is fresh per call -- see app/agent/tools.py's
    `todo` handler for why (scratch worklist state, never persisted).
    """
    client = client or get_client()
    budget = IterationBudget(limit=20)
    tool_trace = []
    run_context: Dict[str, Any] = {
        "candidates": {(c.kind, c.ref_key): c for c in candidates} if candidates else {}
    }

    # 1. Compile System Prompt
    system_prompt = compile_system_prompt(db, manager_id, candidates=candidates)

    # 2. Build Message List
    messages = [{"role": "system", "content": system_prompt}]
    for msg in history:
        messages.append({
            "role": msg["role"],
            "content": msg["content"],
            **({"tool_calls": msg["tool_calls"]} if "tool_calls" in msg else {})
        })
    messages.append({"role": "user", "content": user_message})
    
    # Get registered OpenAI-compatible tool definitions
    tools = registry.get_tool_definitions()
    openai_tools = [{"type": "function", "function": t} for t in tools] if tools else None
    
    final_reply = None
    
    # 3. Main Tool-Calling Loop
    while budget.remaining > 0:
        try:
            budget.consume(1)
        except ValueError:
            break
            
        logger.info(f"Harry running step (Remaining budget: {budget.remaining})")
        
        # Invoke LLM
        response = client.chat(
            model=SMART_MODEL,
            messages=messages,
            tools=openai_tools,
            temperature=0.2
        )
        
        content = response.get("content")
        tool_calls = response.get("tool_calls", [])
        
        if tool_calls:
            # Echo assistant tool_calls turn (Gemini turn symmetry mandate)
            echoed_assistant_msg = {
                "role": "assistant",
                "content": content or "",
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": tc["arguments"]
                        }
                    }
                    for tc in tool_calls
                ]
            }
            messages.append(echoed_assistant_msg)
            
            # Execute each tool call sequentially
            for tc in tool_calls:
                tc_name = tc["name"]
                tc_args = tc["arguments"]
                
                logger.info(f"Harry calling tool '{tc_name}' with args {tc_args}")
                result_str = registry.execute(tc_name, tc_args, db, manager_id=manager_id, run_context=run_context)
                
                # Append tool result turn
                tool_result_msg = {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "name": tc_name,
                    "content": result_str
                }
                messages.append(tool_result_msg)
                
                # Save trace for debugging / manager review
                tool_trace.append({
                    "name": tc_name,
                    "args": tc_args,
                    "result_preview": result_str[:200] + ("..." if len(result_str) > 200 else "")
                })
                
            # If budget ran out during execution, stop
            if budget.remaining <= 0:
                break
        else:
            # Final text response found!
            final_reply = content
            break
            
    if final_reply is None:
        final_reply = "I ran out of steps (iteration budget exhausted) before I could complete your request."
        
    return {
        "reply": final_reply,
        "tool_trace": tool_trace
    }
