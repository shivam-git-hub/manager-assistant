import json
from datetime import datetime
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select, delete
from pydantic import BaseModel

from app.database import get_db, ChatMessage
from app.agent.harness import run_agent

router = APIRouter(prefix="/api/chat", tags=["Agent Chat"])

# -----------------------------------------------------------------------------
# Request & Response Pydantic Models
# -----------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str

class ChatMessageResponse(BaseModel):
    id: int
    role: str
    content: str
    tool_trace: Optional[List[Dict[str, Any]]] = None
    created_at: datetime

    class Config:
        from_attributes = True

class ChatResponse(BaseModel):
    reply: str
    tool_trace: List[Dict[str, Any]]
    created_at: datetime

# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------

@router.post("", response_model=ChatResponse)
def post_chat_message(payload: ChatRequest, db: Session = Depends(get_db)):
    """
    Submits a message to Harry. Loads the last 20 chat messages as conversation history,
    runs the agentic harness loop, persists both user and assistant turns, and returns the response.
    """
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    # 1. Load history (last 20 messages)
    stmt = (
        select(ChatMessage)
        .order_by(ChatMessage.id.desc())
        .limit(20)
    )
    history_reversed = db.scalars(stmt).all()
    # Reverse to restore chronological order
    history_list = list(reversed(history_reversed))
    
    # Format history for harness
    formatted_history = []
    for m in history_list:
        formatted_history.append({
            "role": m.role,
            "content": m.content
        })

    # 2. Run Agent Harness
    result = run_agent(db, payload.message, formatted_history)

    # 3. Persist User Message
    user_msg = ChatMessage(
        role="user",
        content=payload.message
    )
    db.add(user_msg)
    
    # 4. Persist Assistant Reply
    assistant_msg = ChatMessage(
        role="assistant",
        content=result["reply"],
        tool_trace=json.dumps(result["tool_trace"]) if result.get("tool_trace") else None
    )
    db.add(assistant_msg)
    
    db.commit()
    db.refresh(assistant_msg)

    return {
        "reply": assistant_msg.content,
        "tool_trace": result["tool_trace"],
        "created_at": assistant_msg.created_at
    }


@router.get("/history", response_model=List[ChatMessageResponse])
def get_chat_history(limit: int = Query(50), db: Session = Depends(get_db)):
    """
    Retrieves the chronological history of direct chat messages up to limit.
    """
    stmt = (
        select(ChatMessage)
        .order_by(ChatMessage.id.desc())
        .limit(limit)
    )
    history_reversed = db.scalars(stmt).all()
    history = list(reversed(history_reversed))
    
    resp = []
    for m in history:
        trace = None
        if m.tool_trace:
            try:
                trace = json.loads(m.tool_trace)
            except Exception:
                trace = []
                
        resp.append(ChatMessageResponse(
            id=m.id,
            role=m.role,
            content=m.content,
            tool_trace=trace,
            created_at=m.created_at
        ))
        
    return resp


@router.delete("/history")
def clear_chat_history(db: Session = Depends(get_db)):
    """
    Deletes all records from the direct chat history table (useful for demo resets).
    """
    db.execute(delete(ChatMessage))
    db.commit()
    return {"success": True, "message": "Chat history cleared successfully."}
