import os
import time
import json
import logging
import uuid
from typing import Optional, List, Dict, Any, Callable
import httpx

from app.config import GEMINI_API_KEY, SMART_MODEL, FLASH_MODEL

logger = logging.getLogger(__name__)

def sanitize_gemini_schema(schema: Any) -> Any:
    """
    Recursively strips $schema, additionalProperties, and title from a JSON Schema
    since Gemini's function call validator rejects them.
    """
    if isinstance(schema, list):
        return [sanitize_gemini_schema(item) for item in schema]
    elif isinstance(schema, dict):
        keys_to_strip = {"$schema", "additionalProperties", "title"}
        return {
            k: sanitize_gemini_schema(v)
            for k, v in schema.items()
            if k not in keys_to_strip
        }
    return schema

def json_parse_if_string(val: Any) -> Any:
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return {"output": val}
    return val

def messages_to_gemini_contents(messages: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Translates OpenAI-shaped messages array to Gemini's contents + systemInstruction structure.
    """
    contents = []
    system_parts = []
    
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content") or ""
        
        if role == "system":
            system_parts.append({"text": content})
            continue
            
        # Gemini expects "user" or "model"
        gemini_role = "user" if role in ("user", "tool") else "model"
        
        parts = []
        if "tool_calls" in msg and msg["tool_calls"]:
            for tc in msg["tool_calls"]:
                parts.append({
                    "functionCall": {
                        "name": tc["function"]["name"],
                        "args": json_parse_if_string(tc["function"]["arguments"])
                    }
                })
        elif role == "tool":
            parts.append({
                "functionResponse": {
                    "name": msg.get("name"),
                    "response": json_parse_if_string(content)
                }
            })
        else:
            if content:
                parts.append({"text": content})
                
        if parts:
            contents.append({
                "role": gemini_role,
                "parts": parts
            })
            
    system_instruction = {"parts": system_parts} if system_parts else None
    return contents, system_instruction

def map_tools_to_gemini(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Maps OpenAI tools format to Gemini functionDeclarations.
    """
    function_declarations = []
    for tool in tools:
        if tool.get("type") == "function":
            fn = tool.get("function", {})
        else:
            fn = tool
            
        decl = {
            "name": fn.get("name"),
            "description": fn.get("description", ""),
        }
        if "parameters" in fn:
            decl["parameters"] = sanitize_gemini_schema(fn["parameters"])
        function_declarations.append(decl)
        
    return [{"functionDeclarations": function_declarations}] if function_declarations else []


class GeminiClient:
    def __init__(self, api_key: Optional[str] = None, transport: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None):
        self._api_key = api_key
        # Default transport hits real API via httpx
        self._transport = transport or self._default_transport

    def _default_transport(self, url: str, json_payload: Dict[str, Any]) -> Dict[str, Any]:
        # Synchronous POST request
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(url, json=json_payload)
            resp.raise_for_status()
            return resp.json()

    def chat(self, model: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]] | None = None,
             temperature: float = 0.3, max_output_tokens: int = 65535,
             json_mode: bool = False) -> Dict[str, Any]:
        
        # Enforce key resolution at call-time
        api_key = self._api_key or GEMINI_API_KEY
        if not api_key:
            raise ValueError("GEMINI_API_KEY must be configured to use the Gemini Client")
            
        contents, system_instruction = messages_to_gemini_contents(messages)
        
        # Configuration setup
        config = {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens
        }
        if json_mode:
            config["responseMimeType"] = "application/json"
            
        payload = {
            "contents": contents,
            "generationConfig": config
        }
        if system_instruction:
            payload["systemInstruction"] = system_instruction
            
        if tools:
            payload["tools"] = map_tools_to_gemini(tools)
            
        # Format the URL (stripping possible 'models/' prefix from input name for stability)
        clean_model = model.replace("models/", "")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"
        
        # Implement Retries with exponential backoff on 429/5xx errors
        attempts = 3
        last_error = None
        resp_json = None
        
        for i in range(attempts):
            try:
                resp_json = self._transport(url, payload)
                break
            except Exception as e:
                last_error = e
                # Exclude httpx exceptions/HTTP errors from breaking right away if they look like rate limits/server issues
                status_code = None
                if hasattr(e, "response") and getattr(e, "response") is not None:
                    status_code = getattr(e, "response").status_code
                
                # If we've exhausted attempts, or the error is not a 429/5xx, raise
                if i == attempts - 1 or (status_code is not None and status_code not in (429, 500, 502, 503, 504)):
                    raise e
                    
                backoff = 2 ** (i + 1)  # 2s, 4s, 8s
                logger.warning(f"Gemini API request failed ({e}). Retrying in {backoff}s...")
                time.sleep(backoff)
        
        if resp_json is None:
            if last_error:
                raise last_error
            raise ValueError("No response received from Gemini API transport")
                
        # Parse return payload
        candidates = resp_json.get("candidates", [])
        if not candidates:
            # Check if blocked by safety or empty response
            prompt_feedback = resp_json.get("promptFeedback", {})
            if prompt_feedback:
                raise ValueError(f"Gemini prompt blocked/rejected: {prompt_feedback}")
            return {
                "content": None,
                "tool_calls": [],
                "finish_reason": "OTHER",
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            }
            
        candidate = candidates[0]
        content_obj = candidate.get("content", {})
        parts = content_obj.get("parts", [])
        
        text_content = None
        tool_calls = []
        
        for part in parts:
            if "text" in part:
                text_content = part["text"] if text_content is None else text_content + part["text"]
            elif "functionCall" in part:
                fc = part["functionCall"]
                tool_calls.append({
                    "id": f"call_{uuid.uuid4().hex[:12]}",
                    "name": fc["name"],
                    "arguments": fc.get("args") or {}
                })
                
        # Standardize finish reason
        finish_reason = candidate.get("finishReason", "STOP")
        
        # Usage metrics
        usage_meta = resp_json.get("usageMetadata", {})
        usage = {
            "prompt_tokens": usage_meta.get("promptTokenCount", 0),
            "completion_tokens": usage_meta.get("candidatesTokenCount", 0),
            "total_tokens": usage_meta.get("totalTokenCount", 0)
        }
        
        return {
            "content": text_content,
            "tool_calls": tool_calls,
            "finish_reason": finish_reason,
            "usage": usage
        }


# Cache client instance
_client_singleton: Optional[GeminiClient] = None

def get_client() -> GeminiClient:
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = GeminiClient()
    return _client_singleton
