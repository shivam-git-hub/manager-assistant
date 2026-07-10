from datetime import datetime, date
from typing import Optional
from sqlalchemy import create_engine, ForeignKey, String, Text, Boolean, DateTime, Date, Integer
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from app.config import DATABASE_URL, IST

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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(IST))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(IST), onupdate=lambda: datetime.now(IST))

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

def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
