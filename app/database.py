"""Table definitions shared by every manager's db.sqlite. As of step 15
there is no single global engine/SessionLocal here anymore -- each manager
gets their own SQLite file (app/tenancy/db.py's get_manager_engine/
get_manager_session), and these are plain SQLAlchemy declarative models,
engine-agnostic until a session binds to one. See
app.tenancy.db.init_manager_db for schema creation + migrations (the old
init_db()'s logic, now parameterized per manager) and
app.tenancy.db.get_manager_db for the FastAPI dependency that replaces the
old get_db.
"""
from datetime import datetime, date
from typing import Optional
from sqlalchemy import ForeignKey, String, Text, Boolean, DateTime, Date, Integer, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from app import timeservice

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
    receiver_raw_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    receiver_mapped_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    channel_raw_id: Mapped[str] = mapped_column(String(100))
    thread_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    subject: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime)  # the message's own claimed event time (source-provided)
    # Real DB-insertion time -- deliberately no Python-side default here so
    # nothing in this file reads the wall clock (see test_07_wall_clock_guard);
    # every insertion site sets it explicitly. Can lag `timestamp` (e.g. an
    # Outlook poll picking up mail that arrived several minutes earlier).
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_processed: Mapped[bool] = mapped_column(Boolean, default=False)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Step 20 (spec §4.2): why the ingest job skipped this row without an
    # LLM call -- "blocked" (user blocklist) or "noise" (built-in filter).
    # NULL for rows that were (or will be) genuinely processed. Auditable,
    # never re-scanned.
    skip_reason: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    raw_metadata: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

class ChatMessage(Base):
    __tablename__ = "chat_messages"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role: Mapped[str] = mapped_column(String(20))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    tool_trace: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON-serialized trace list
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())

class Meeting(Base):
    __tablename__ = "meetings"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255))
    starts_at: Mapped[datetime] = mapped_column(DateTime)
    ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    attendees: Mapped[str] = mapped_column(Text)  # JSON-serialized list of team member IDs
    project_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("projects.id"), nullable=True)
    mom_raw: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    mom_message_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("unified_messages.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="scheduled")  # "scheduled" | "completed" | "cancelled"
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())

class ActionItem(Base):
    __tablename__ = "action_items"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[int] = mapped_column(Integer, ForeignKey("meetings.id"))
    task_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("tasks.id"), nullable=True)
    description: Mapped[str] = mapped_column(Text)
    owner_member_id: Mapped[str] = mapped_column(String(100), ForeignKey("team_members.id"))
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())

class Leave(Base):
    __tablename__ = "leaves"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    member_id: Mapped[str] = mapped_column(String(100), ForeignKey("team_members.id"))
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())

class ReassignmentSuggestion(Base):
    __tablename__ = "reassignment_suggestions"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    leave_id: Mapped[int] = mapped_column(Integer, ForeignKey("leaves.id"))
    task_id: Mapped[int] = mapped_column(Integer, ForeignKey("tasks.id"))
    from_member_id: Mapped[str] = mapped_column(String(100), ForeignKey("team_members.id"))
    to_member_id: Mapped[str] = mapped_column(String(100), ForeignKey("team_members.id"))
    rationale: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="suggested")  # "suggested", "approved", "rejected"
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

class Digest(Base):
    __tablename__ = "digests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    week_start: Mapped[date] = mapped_column(Date, unique=True)
    content: Mapped[str] = mapped_column(Text)  # JSON-serialized string
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())

class Todo(Base):
    """User-maintained TODO list (home dashboard panel, wireframe 2.png).
    No LLM anywhere in this table's lifecycle -- the user is the only
    writer, via app/api/home.py. Step 19."""
    __tablename__ = "todos"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid4 hex
    text: Mapped[str] = mapped_column(Text)
    due: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="open")  # open | done
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist(), onupdate=lambda: timeservice.now_ist())


class Claim(Base):
    """Short structured statement extracted from message(s) by the ingest
    job (spec/architecture_v2_kb.md §2). Schema settled in step 19 so later
    pipeline steps only populate, never re-shape; nothing writes rows until
    the v2 ingest job lands. content_hash guards retry dedup (transactional
    idempotency, spec §4)."""
    __tablename__ = "claims"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid4 hex -- THE claim_id
    text: Mapped[str] = mapped_column(Text)
    thread_key: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    processed: Mapped[bool] = mapped_column(Boolean, default=False)  # consumed by heartbeat yet?
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())


class ClaimSource(Base):
    """Citation join: which unified_messages row(s) a claim came from."""
    __tablename__ = "claim_sources"
    __table_args__ = (UniqueConstraint("claim_id", "message_id", name="uq_claim_source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    claim_id: Mapped[str] = mapped_column(String(36), ForeignKey("claims.id"))
    message_id: Mapped[int] = mapped_column(Integer, ForeignKey("unified_messages.id"))


class Event(Base):
    """Condensed, judged output of the heartbeat agent (spec §2): typed,
    tagged, severity-scored. The dashboard Updates panel is a QUERY over
    this table (severity >= threshold, minus dismissed, plus promoted) --
    notifications are deliberately not their own table. Only jobs create
    rows; app/api/home.py exposes read + dismiss/promote."""
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid4 hex
    type: Mapped[str] = mapped_column(String(20))  # status_update|blocker|clarification|commitment|request|conflict|fyi
    severity: Mapped[int] = mapped_column(Integer, default=0)  # 0-3
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    project_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON list, registry project ids
    task_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON list, per-project task ids
    claim_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON list -> claims.id (citations)
    general: Mapped[bool] = mapped_column(Boolean, default=False)  # not tied to any project/task
    dreamed: Mapped[bool] = mapped_column(Boolean, default=False)  # consumed by dream yet?
    ui_state: Mapped[str] = mapped_column(String(10), default="shown")  # shown | dismissed | promoted | approved | rejected (approved/rejected: request events only, step 24)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())


class AgentAssignment(Base):
    """Mirror of the control-plane Agent claim (step 17 piece 2a), written
    into the manager's OWN db.sqlite so the assignment is visible from both
    sides -- any code already holding a manager-scoped session can look up
    "which bot is mine" without a control-plane round trip. At most one row
    per manager db (a manager holds exactly one bot)."""
    __tablename__ = "agent_assignment"

    agent_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    agent_name: Mapped[str] = mapped_column(String(100))
    team_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())

