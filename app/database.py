"""Table definitions shared by every manager's db.sqlite. There is no
global engine/SessionLocal here -- each manager gets their own SQLite file
(app/tenancy/db.py's get_manager_engine/get_manager_session), and these are
plain SQLAlchemy declarative models, engine-agnostic until a session binds
to one. See app.tenancy.db.init_manager_db for schema creation + migrations
and app.tenancy.db.get_manager_db for the FastAPI dependency that replaces the
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
    # Why the ingest job skipped this row without an
    # LLM call -- "blocked" (user blocklist; the sole skip reason for now,
    # see app.projectkb.blocklist.classify_message). NULL for rows that
    # were (or will be) genuinely processed. Auditable, never re-scanned.
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
    # Set the moment the agent sends a pre-meeting
    # brief, so a later heartbeat tick inside the same 2h window doesn't
    # re-send it. NULL = not yet briefed.
    brief_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

class Todo(Base):
    """User-maintained TODO list (home dashboard panel).
    No LLM anywhere in this table's lifecycle -- the user is the only
    writer, via app/api/home.py."""
    __tablename__ = "todos"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid4 hex
    text: Mapped[str] = mapped_column(Text)
    due: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="open")  # open | done
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist(), onupdate=lambda: timeservice.now_ist())


class Claim(Base):
    """Short structured statement extracted from message(s) by the ingest
    job. content_hash guards retry dedup (transactional idempotency)."""
    __tablename__ = "claims"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid4 hex -- THE claim_id
    text: Mapped[str] = mapped_column(Text)
    thread_key: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    processed: Mapped[bool] = mapped_column(Boolean, default=False)  # consumed by heartbeat yet?
    # Starvation guard for the agentic heartbeat (step 35): incremented every
    # time this claim lands in a batch, whether or not the agent cites it.
    # Reaching 3 without ever being cited force-marks it processed so a claim
    # the model keeps ignoring doesn't get re-sent to the LLM forever -- see
    # app.projectkb.jobs.heartbeat.run.
    heartbeat_attempts: Mapped[int] = mapped_column(Integer, default=0)
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
    ui_state: Mapped[str] = mapped_column(String(10), default="shown")  # shown | dismissed | promoted | approved | rejected (approved/rejected: request events only)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())
    # When the underlying messages actually happened (max timestamp across
    # the cited claims' source messages -- app.projectkb.occurrence), NOT
    # when this row was inserted (that's created_at, unchanged). Computed in
    # code, never LLM-supplied. NULL for events created before this column
    # existed, or whose claims have no resolvable source -- every ordering/
    # filtering read site must COALESCE(occurred_at, created_at) rather than
    # reading created_at alone, or old and new events interleave wrongly.
    occurred_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class AgentAssignment(Base):
    """Mirror of the control-plane Agent claim, written
    into the manager's OWN db.sqlite so the assignment is visible from both
    sides -- any code already holding a manager-scoped session can look up
    "which bot is mine" without a control-plane round trip. At most one row
    per manager db (a manager holds exactly one bot)."""
    __tablename__ = "agent_assignment"

    agent_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    agent_name: Mapped[str] = mapped_column(String(100))
    team_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())


class AgentActionLog(Base):
    """Idempotency ledger for the personal agent's autonomous heartbeat
    heartbeat. The deterministic
    candidate selector (app/agent/select.py) checks this before proposing
    an action; the tool handlers that actually take an action
    (app/agent/tools.py) write the matching row afterwards -- never left to
    the model to remember, same "code enforces the invariant" pattern as
    the severity floors in the heartbeat/dream jobs.

    ref_key convention: "meeting:<id>" (pre_meeting_brief, once per
    meeting); "task:<project_id>:<task_id>:<YYYY-MM-DD>" (followup, at most
    once/day per task); "event:<event_id>" (conflict_contact /
    conflict_escalate, once each per conflict event)."""

    __tablename__ = "agent_action_log"
    __table_args__ = (UniqueConstraint("action_type", "ref_key", name="uq_agent_action_log"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid4 hex
    action_type: Mapped[str] = mapped_column(String(30))  # pre_meeting_brief | followup | conflict_contact | conflict_escalate
    ref_key: Mapped[str] = mapped_column(String(255))
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())


class Workflow(Base):
    __tablename__ = "workflows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    task_type: Mapped[str] = mapped_column(String(50))  # followup | morning_brief | custom
    cron_expression: Mapped[str] = mapped_column(String(50))  # e.g. "0 9 * * *" or "30m"
    config: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON-serialized params
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | paused
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist(), onupdate=lambda: timeservice.now_ist())


class CronJob(Base):
    __tablename__ = "cron_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("workflows.id"), nullable=True)
    task_type: Mapped[str] = mapped_column(String(50))  # followup | morning_brief | reminder | custom
    prompt: Mapped[str] = mapped_column(Text)
    schedule: Mapped[str] = mapped_column(String(100))  # e.g., "0 9 * * *" or timestamp ISO
    is_recurring: Mapped[bool] = mapped_column(Boolean, default=False)
    next_run_at: Mapped[datetime] = mapped_column(DateTime)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | running | completed | failed | paused
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())


class FollowupAgent(Base):
    __tablename__ = "followup_agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recipient_employee_id: Mapped[str] = mapped_column(String(36))  # Who we are contacting
    scoped_project_ids: Mapped[str] = mapped_column(Text)  # JSON-serialized list of projects they can access
    instructions: Mapped[str] = mapped_column(Text)  # Instructions from the COS agent
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | completed | reported | no_response
    last_message_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_message_received_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    chat_history: Mapped[str] = mapped_column(Text, default="[]")  # JSON-serialized chat history with the recipient
    cos_context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Context/conversation with COS
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist(), onupdate=lambda: timeservice.now_ist())


