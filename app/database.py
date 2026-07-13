from datetime import datetime, date
from typing import Optional
from sqlalchemy import create_engine, ForeignKey, String, Text, Boolean, DateTime, Date, Integer, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from app.config import DATABASE_URL, IST
from app import timeservice

# Create engine and session
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Base(DeclarativeBase):
    pass

class TeamMember(Base):
    __tablename__ = "team_members"
    
    id: Mapped[str] = mapped_column(String(100), primary_key=True)  # Slack UserID or Email
    name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(100))
    slack_handle: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    outlook_email: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    timezone: Mapped[str] = mapped_column(String(50), default="Asia/Kolkata")

class Project(Base):
    __tablename__ = "projects"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    manager_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active, completed, on_hold
    health: Mapped[str] = mapped_column(String(10), default="green")
    health_reasons: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    health_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist(), onupdate=lambda: timeservice.now_ist())

class Task(Base):
    __tablename__ = "tasks"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"))
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    assignee_id: Mapped[Optional[str]] = mapped_column(String(100), ForeignKey("team_members.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending, in_progress, completed, blocked
    blockage_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

class UnifiedMessage(Base):
    __tablename__ = "unified_messages"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform_msg_id: Mapped[str] = mapped_column(String(100), unique=True)
    source: Mapped[str] = mapped_column(String(20))  # slack, outlook, dashboard
    direction: Mapped[str] = mapped_column(String(10), default="inbound")
    sender_raw_id: Mapped[str] = mapped_column(String(100))
    sender_mapped_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    channel_raw_id: Mapped[str] = mapped_column(String(100))
    thread_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    subject: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    is_processed: Mapped[bool] = mapped_column(Boolean, default=False)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    raw_metadata: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

class ChatMessage(Base):
    __tablename__ = "chat_messages"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role: Mapped[str] = mapped_column(String(20))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    tool_trace: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON-serialized trace list
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())

def init_db():
    from app.outbound import OutboundQueue  # Register with Base metadata
    from app.kb import models as kb_models # Register with Base metadata
    from app.scheduler import ScheduledJob
    from app.followups import Followup
    from app.brief import Brief
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        # Check if 'direction' column exists in unified_messages
        result = db.execute(text("PRAGMA table_info(unified_messages)")).fetchall()
        columns = [row[1] for row in result]
        if "direction" not in columns:
            db.execute(text("ALTER TABLE unified_messages ADD COLUMN direction VARCHAR(10) DEFAULT 'inbound'"))
            db.commit()
            
        # Check if 'health' column exists in projects
        res_proj = db.execute(text("PRAGMA table_info(projects)")).fetchall()
        proj_cols = [row[1] for row in res_proj]
        if "health" not in proj_cols:
            db.execute(text("ALTER TABLE projects ADD COLUMN health VARCHAR(10) DEFAULT 'green'"))
        if "health_reasons" not in proj_cols:
            db.execute(text("ALTER TABLE projects ADD COLUMN health_reasons TEXT"))
        if "health_updated_at" not in proj_cols:
            db.execute(text("ALTER TABLE projects ADD COLUMN health_updated_at DATETIME"))
        db.commit()
            
        harry = db.query(TeamMember).filter(TeamMember.id == "U_HARRY").first()
        if not harry:
            harry = TeamMember(
                id="U_HARRY",
                name="Harry",
                role="AI Assistant",
                slack_handle="U_HARRY",
                outlook_email="harry.assistant@company.com",
                timezone="Asia/Kolkata"
            )
            db.add(harry)
            db.commit()
            
        # Backfill entities for existing projects
        projects = db.query(Project).all()
        for p in projects:
            from app.kb.models import get_or_create_entity, slugify
            get_or_create_entity(db, slug=f"project:{slugify(p.name)}", type="project", name=p.name, ref_id=str(p.id))
            
        # Backfill entities for existing team members
        members = db.query(TeamMember).all()
        for m in members:
            from app.kb.models import get_or_create_entity
            get_or_create_entity(db, slug=f"person:{m.id}", type="person", name=m.name, ref_id=m.id)
            
        # Seed default background jobs
        from app.scheduler import seed_default_jobs
        seed_default_jobs(db)
    finally:
        db.close()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
