"""Per-project db.sqlite tables (spec/architecture_v2_kb.md §3, step 18 --
prompts/step_18_registry_and_scaffold.md). Own DeclarativeBase, separate
from app.database.Base (per-manager) and app.controlplane.models
.ControlPlaneBase (global registry) -- one project = one sqlite file under
projects/<project_id>/db.sqlite (see app.projects.paths), engine-agnostic
here same as the other model modules.

Fresh package, not a rework of the orphaned app.projectkb.models sketch --
see that module's docstring-equivalent note in app.projects.paths.

All timestamp defaults read the sim-time service, never the wall clock --
app/projects/ is not exempt from tests/test_timeservice.py's
test_07_wall_clock_guard (only app/projectkb, app/controlplane,
app/integrations, and app/api are)."""
from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, String, Text, Integer, DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app import timeservice


class ProjectBase(DeclarativeBase):
    pass


class Task(ProjectBase):
    """id is a uuid hex (not autoincrement) because parent_task_id is a
    self-referencing FK for subtasks -- explicit in the step prompt. The
    other five tables below use plain autoincrement ints, matching the
    convention used everywhere else for per-store append/log tables
    (app.database, the orphaned app.projectkb.models) -- nothing outside
    this db.sqlite ever cites a task/archive/conflict/suggestion/concern/
    health_log row by id except events.task_ids (task ids only, per
    spec §2), so there is no cross-db uuid requirement for the rest."""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid4 hex
    parent_task_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("tasks.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Cross-db reference (controlplane.employees) -- no FK constraint, same
    # reasoning as the orphaned app.projectkb.models.Claim.source_message_id.
    assignee_employee_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="todo")  # todo|in_progress|blocked|done|pending_approval
    priority: Mapped[str] = mapped_column(String(10), default="medium")  # low|medium|high (wireframe 6, step 21)
    due: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str] = mapped_column(String(20))  # "manager" | "agent"
    approved_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist(), onupdate=lambda: timeservice.now_ist())


class ArchiveEntry(ProjectBase):
    """Append-only by convention (never updated/deleted in application
    code) -- full project lifecycle audit trail, spec §3."""

    __tablename__ = "archive"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())
    kind: Mapped[str] = mapped_column(String(50))  # free text: event/mom/action/...
    content: Mapped[str] = mapped_column(Text)
    source_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class Conflict(ProjectBase):
    """Claim-pair refs -- never auto-resolved (CLAUDE.md). status flips to
    resolved_by_human only via explicit manager action."""

    __tablename__ = "conflicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    claim_a_ref: Mapped[str] = mapped_column(String(255))
    claim_b_ref: Mapped[str] = mapped_column(String(255))
    severity: Mapped[str] = mapped_column(String(10), default="medium")  # low|medium|high
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|resolved_by_human
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())


class Suggestion(ProjectBase):
    """Dream-generated, shown on the project page."""

    __tablename__ = "suggestions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|dismissed


class Concern(ProjectBase):
    """Dream-generated, shown on the project page."""

    __tablename__ = "concerns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|dismissed


class HealthLog(ProjectBase):
    """Auditable health history (spec §4.4): a deterministic rubric produces
    base_score, the LLM may adjust ±1 band with a written reason -- both land
    here so "why is this project red" always has a citable answer."""

    __tablename__ = "health_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())
    rubric_inputs: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON text
    base_score: Mapped[int] = mapped_column(Integer)
    llm_adjustment: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    final_score: Mapped[int] = mapped_column(Integer)
