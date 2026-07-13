import json
import logging
from datetime import datetime, date, timedelta
from typing import Optional, List
from fastapi import APIRouter, Depends, Query, status, HTTPException
from sqlalchemy.orm import Session, Mapped, mapped_column
from sqlalchemy import select, and_, or_, Integer, String, Text, DateTime

from app.database import Base, get_db, TeamMember, Project, Task
from app.kb.models import Entity, Conflict
from app.followups import Followup, get_dm_channel_id
from app.outbound import release_queued_messages_sync, send_or_hold
from app import timeservice
from app.agent.gemini_client import get_client, GeminiClient
from app.config import SMART_MODEL

logger = logging.getLogger(__name__)

class Brief(Base):
    __tablename__ = "briefs"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    brief_date: Mapped[str] = mapped_column(String(10), unique=True)  # "YYYY-MM-DD"
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())


def run_morning_brief(db: Session, virtual_time: Optional[datetime] = None) -> Optional[Brief]:
    """
    Assembles project metrics, releases overnight pings, calls the LLM to write a concise morning brief,
    saves it to the DB, and pings the manager.
    """
    if virtual_time is None:
        virtual_time = timeservice.now_ist()
        
    brief_date_str = virtual_time.strftime("%Y-%m-%d")
    
    # 1. Skip if brief already exists for this date (idempotent catchup)
    existing = db.scalars(select(Brief).where(Brief.brief_date == brief_date_str)).first()
    if existing:
        return existing
        
    # 2. Release queued messages overnight
    release_stats = release_queued_messages_sync(db)
    released_count = release_stats.get("released", 0)
    
    # 3. Assemble Metrics
    # Open conflicts
    conflicts = list(db.scalars(
        select(Conflict).where(Conflict.status == "open")
    ).all())
    conflicts_text = ""
    for idx, c in enumerate(conflicts):
        entity = db.get(Entity, c.entity_id)
        entity_name = entity.name if entity else f"ID {c.entity_id}"
        conflicts_text += f"- [{c.severity.upper()} Conflict on {entity_name}]: {c.description}\n"
        
    # Projects Health (red -> yellow -> green)
    projects = list(db.scalars(select(Project).where(Project.status == "active")).all())
    health_weights = {"red": 1, "yellow": 2, "green": 3}
    projects.sort(key=lambda p: health_weights.get(p.health or "green", 3))
    
    projects_text = ""
    for p in projects:
        reasons_list = []
        if p.health_reasons:
            try:
                reasons_list = json.loads(p.health_reasons)
            except Exception:
                reasons_list = [p.health_reasons]
        reasons_str = "; ".join(reasons_list)
        projects_text += f"- Project: {p.name} | Health: {p.health.upper()} | Reasons: {reasons_str}\n"
        
    # Follow-ups awaiting answers
    followups = list(db.scalars(
        select(Followup).where(Followup.status.in_(["open", "escalated"]))
    ).all())
    followups_text = ""
    for f in followups:
        member = db.get(TeamMember, f.target_member_id)
        name = member.name if member else f.target_member_id
        followups_text += f"- Awaiting reply from {name} (asked {f.ping_count} times) about: '{f.question}' (Status: {f.status.upper()})\n"
        
    # Tasks due today or overdue
    today_date = virtual_time.date()
    tasks = list(db.scalars(
        select(Task).where(
            (Task.status != "completed") & (Task.due_date != None)
        )
    ).all())
    
    overdue_count = 0
    due_today_count = 0
    tasks_text = ""
    for t in tasks:
        assignee_name = "Unassigned"
        if t.assignee_id:
            m = db.get(TeamMember, t.assignee_id)
            if m:
                assignee_name = m.name
                
        if t.due_date:
            if t.due_date < today_date:
                overdue_count += 1
                tasks_text += f"- [OVERDUE]: '{t.title}' assigned to {assignee_name} (Due: {t.due_date.isoformat()})\n"
            elif t.due_date == today_date:
                due_today_count += 1
                tasks_text += f"- [DUE TODAY]: '{t.title}' assigned to {assignee_name}\n"
            
    # Compile raw deterministic fallback text
    fallback_text = (
        f"Morning Brief for {brief_date_str}\n"
        f"---------------------------\n"
        f"Activity Summary:\n"
        f"- {released_count} outbound message(s) released overnight.\n\n"
        f"Project Portfolios & Health:\n"
        f"{projects_text or '- All projects are healthy.'}\n\n"
        f"Overdue and Due Tasks:\n"
        f"- Overdue tasks: {overdue_count}\n"
        f"- Tasks due today: {due_today_count}\n"
        f"{tasks_text or '- No upcoming tasks due.'}\n\n"
        f"Pending Team Follow-ups:\n"
        f"{followups_text or '- No active follow-ups.'}\n\n"
        f"Open Conflicts & Blockers:\n"
        f"{conflicts_text or '- No active conflicts.'}\n"
    )
    
    # 4. Synthesis via SMART_MODEL
    system_instruction = (
        "You are an elite, highly concise executive assistant. "
        "Your task is to write a beautifully polished, clean morning briefing for a busy engineering manager. "
        "Summarize the provided metrics and facts into 2-3 short sections with clean bullet points. "
        "Keep the language factual, present-tense, and professional. "
        "Do NOT mention any meta details, introducing sentences, or placeholders. "
        "Do NOT invent any facts. Keep all numbers, names, and details accurate."
    )
    
    prompt = (
        f"### Morning Facts for Date: {brief_date_str}\n"
        f"### Current Simulated IST Time: {virtual_time.isoformat()} IST\n\n"
        f"### Facts Raw Context:\n{fallback_text}\n\n"
        "Generate the polished executive briefing note."
    )
    
    brief_content = None
    try:
        client = get_client()
        res = client.chat(model=SMART_MODEL, messages=[
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt}
        ], temperature=0.3)
        brief_content = res.get("content")
    except Exception as e:
        logger.error(f"Error during LLM brief synthesis: {e}")
        
    # 5. Graceful Fallback if LLM fails
    if not brief_content:
        brief_content = fallback_text
        
    # 6. Save Brief
    new_brief = Brief(
        brief_date=brief_date_str,
        content=brief_content.strip(),
        created_at=timeservice.now_ist()
    )
    db.add(new_brief)
    db.commit()
    db.refresh(new_brief)
    
    # 7. Deliver 2-line teaser to Manager
    manager = db.scalars(
        select(TeamMember).where(TeamMember.role.ilike("%manager%"))
    ).first()
    if manager:
        manager_dm = get_dm_channel_id("U_HARRY", manager.id)
        teaser_text = (
            f"Good morning, {manager.name}! Your executive morning brief for *{brief_date_str}* is ready on the dashboard.\n"
            f"Overdue tasks: *{overdue_count}*, due today: *{due_today_count}*. Released overnight pings: *{released_count}*."
        )
        send_or_hold("slack", {"channel": manager_dm, "text": teaser_text}, db)
        
    return new_brief


# Pydantic Schemas
from pydantic import BaseModel, ConfigDict

class BriefResponse(BaseModel):
    id: int
    brief_date: str
    content: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# FastAPI Router
router = APIRouter(prefix="/api/briefs", tags=["Morning Brief Service"])

@router.get("", response_model=List[BriefResponse])
def get_briefs(limit: int = Query(7), db: Session = Depends(get_db)):
    stmt = select(Brief).order_by(Brief.brief_date.desc()).limit(limit)
    return list(db.scalars(stmt).all())

@router.get("/{date}", response_model=BriefResponse)
def get_brief_by_date(date: str, db: Session = Depends(get_db)):
    brief = db.scalars(select(Brief).where(Brief.brief_date == date)).first()
    if not brief:
        raise HTTPException(status_code=404, detail=f"No brief found for date {date}")
    return brief
