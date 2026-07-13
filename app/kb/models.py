from datetime import datetime
from typing import Optional
from sqlalchemy import ForeignKey, String, Text, Boolean, DateTime, Integer, Float
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
from app import timeservice

class Entity(Base):
    __tablename__ = "entities"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(150), unique=True, index=True)
    type: Mapped[str] = mapped_column(String(20))  # "project" | "person" | "meeting"
    name: Mapped[str] = mapped_column(String(255))
    ref_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    compiled_truth: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    truth_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist(), onupdate=lambda: timeservice.now_ist())

class TimelineEntry(Base):
    __tablename__ = "timeline_entries"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_id: Mapped[int] = mapped_column(Integer, ForeignKey("entities.id"))
    happened_at: Mapped[datetime] = mapped_column(DateTime)
    summary: Mapped[str] = mapped_column(String(500))
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_message_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("unified_messages.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())

class AttributedClaim(Base):
    __tablename__ = "attributed_claims"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_id: Mapped[int] = mapped_column(Integer, ForeignKey("entities.id"))
    claim: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(20))  # "fact" | "status" | "commitment" | "blocker" | "opinion"
    holder: Mapped[str] = mapped_column(String(100))  # team_members.id of who asserts it (e.g. "U_BOB")
    weight: Mapped[float] = mapped_column(Float)  # 0.0 - 1.0 confidence
    source_message_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("unified_messages.id"), nullable=True)
    claimed_at: Mapped[datetime] = mapped_column(DateTime)
    superseded_by: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("attributed_claims.id"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())

class Conflict(Base):
    __tablename__ = "conflicts"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_id: Mapped[int] = mapped_column(Integer, ForeignKey("entities.id"))
    claim_a_id: Mapped[int] = mapped_column(Integer, ForeignKey("attributed_claims.id"))
    claim_b_id: Mapped[int] = mapped_column(Integer, ForeignKey("attributed_claims.id"))
    severity: Mapped[str] = mapped_column(String(10))  # "low" | "medium" | "high"
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), default="open")  # "open" | "resolved" | "dismissed"
    detected_at: Mapped[datetime] = mapped_column(DateTime)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    resolution_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


import re
from sqlalchemy import select
from sqlalchemy.orm import Session

def slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r'[^a-z0-9\s-]', '', s)
    s = re.sub(r'[\s-]+', '-', s)
    return s

def get_or_create_entity(db: Session, slug: str, type: str, name: str, ref_id: Optional[str] = None) -> Entity:
    stmt = select(Entity).where(Entity.slug == slug)
    entity = db.scalars(stmt).first()
    if not entity:
        entity = Entity(
            slug=slug,
            type=type,
            name=name,
            ref_id=ref_id
        )
        db.add(entity)
        db.commit()
        db.refresh(entity)
    elif entity.name != name or (ref_id is not None and entity.ref_id != ref_id):
        # Callers pass the current display name (e.g. team-member rename) —
        # keep the entity page in sync instead of freezing the first value.
        entity.name = name
        if ref_id is not None:
            entity.ref_id = ref_id
        db.commit()
        db.refresh(entity)
    return entity
