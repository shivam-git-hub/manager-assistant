from datetime import datetime
from typing import Optional
from sqlalchemy import Integer, String, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column, Session
from app.database import Base
from app import timeservice

class AgentNote(Base):
    __tablename__ = "agent_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: timeservice.now_ist()
    )
    kind: Mapped[str] = mapped_column(String(50))  # followup_decision, observation, escalation, etc.
    subject_ref: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )  # e.g. "person:U_BOB", "conflict:3", "project:phoenix", "followup:5"
    content: Mapped[str] = mapped_column(Text)

def record_note(db: Session, kind: str, content: str, subject_ref: Optional[str] = None) -> AgentNote:
    note = AgentNote(
        created_at=timeservice.now_ist(),
        kind=kind,
        content=content,
        subject_ref=subject_ref
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return note

def recent_notes(db: Session, limit: int = 30) -> list[AgentNote]:
    return db.query(AgentNote).order_by(AgentNote.id.desc()).limit(limit).all()
