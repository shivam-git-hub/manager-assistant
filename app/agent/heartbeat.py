import json
import logging
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session

from app import timeservice
from app.config import FLASH_MODEL, SMART_MODEL
from app.agent.gemini_client import get_client, GeminiClient
from app.agent.situation import build_situation
from app.agent.harness import run_agent

logger = logging.getLogger(__name__)

def triage(db: Session, client: Optional[GeminiClient] = None) -> dict:
    sit = build_situation(db)
    if not sit["has_signals"]:
        return {
            "action_needed": False,
            "reason": "No active signals warranting follow-up.",
            "focus": []
        }
    
    # We have signals! Invoke FLASH_MODEL
    client = client or get_client()
    
    prompt = (
        "You are Harry's triage layer. Analyze the following situation report of a software development workspace "
        "and determine if autonomous action is needed right now.\n\n"
        "Your task is to identify if there are unmet commitments, overdue/blocked tasks, quiet team members, "
        "or open conflicts that are NOT already addressed by an open follow-up or a recent memory note.\n\n"
        "Say NO (action_needed = false) if everything actionable is already being chased (e.g., active open follow-up or recent notes show recent followups/escalations).\n\n"
        "Return a JSON object matching this exact structure:\n"
        "{\n"
        "  \"action_needed\": true/false,\n"
        "  \"reason\": \"Explain briefly why action is or is not needed\",\n"
        "  \"focus\": [\"person:U_BOB\", \"conflict:3\"]\n"
        "}\n\n"
        f"SITUATION REPORT:\n{sit['digest']}"
    )
    
    messages = [
        {"role": "user", "content": prompt}
    ]
    
    try:
        response = client.chat(
            model=FLASH_MODEL,
            messages=messages,
            json_mode=True,
            temperature=0.0
        )
        content_str = response.get("content", "")
        parsed = json.loads(content_str)
        
        # Validate keys and defaults
        action_needed = bool(parsed.get("action_needed", False))
        reason = parsed.get("reason", "No reason provided by model.")
        focus = parsed.get("focus", [])
        if not isinstance(focus, list):
            focus = []
            
        return {
            "action_needed": action_needed,
            "reason": reason,
            "focus": focus
        }
    except Exception as e:
        logger.error(f"Failed to triage situation: {e}", exc_info=True)
        return {
            "action_needed": False,
            "reason": f"Failed to triage situation: {e}",
            "focus": []
        }

def run_heartbeat(db: Session, client: Optional[GeminiClient] = None) -> dict:
    t = triage(db, client)
    if not t["action_needed"]:
        return {
            "triaged": True,
            "acted": False,
            "reason": t["reason"]
        }
    
    # Tier 2 - Action needed! Build directive prompt and execute run_agent
    sit = build_situation(db)
    
    directive = (
        f"HEARTBEAT: Your triage flagged action may be needed.\n"
        f"Reason: {t['reason']}\n"
        f"Focus items: {t['focus']}\n\n"
        f"Here is the current situation report:\n{sit['digest']}\n\n"
        f"Your goal is to address the flagged issues. Take action:\n"
        f"- Create follow-ups for anyone who owes an update or is silent on an open commitment.\n"
        f"- Ping both holders of any open conflict that has no follow-up yet (create a follow-up for EACH holder about the conflict so both are notified).\n"
        f"- Escalate to the manager if required by standard escalation thresholds.\n\n"
        f"DO NOT duplicate any follow-up that is already open.\n"
        f"Once done, you MUST call record_note summarizing your interventions. Include any subject references (e.g. 'person:U_BOB' or 'conflict:3') in the call."
    )
    
    res = run_agent(db, directive, history=[], client=client)
    
    return {
        "triaged": True,
        "acted": True,
        "reply": res.get("reply", ""),
        "tool_trace": res.get("tool_trace", [])
    }
