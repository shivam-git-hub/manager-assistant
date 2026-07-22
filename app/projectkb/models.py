from datetime import datetime
from typing import Optional
from functools import lru_cache

from sqlalchemy import create_engine, ForeignKey, String, Text, Boolean, DateTime, Integer, Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.projectkb.enums import TodoStatus, ConflictSeverity


class ProjectBase(DeclarativeBase):
    pass


class Claim(ProjectBase):
    """Structured extraction output -- one row per atomic assertion pulled
    out of a tracked message by the ingestion job. timeline.md's Current
    State section is a rendered VIEW of the active rows here; it is never
    the other way around (nothing re-parses that markdown)."""

    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content: Mapped[str] = mapped_column(Text)
    holder: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # team_members.id
    # unified_messages.id in the main db -- cross-database (separate sqlite
    # file per project), so a plain int rather than an FK constraint.
    source_message_id: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    # Supersession chain: when a later claim replaces this one, active flips
    # to False and superseded_by points at the replacement. This is what lets
    # "current state" mean "active claims" instead of re-deriving the latest
    # version of each fact from full history on every read.
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    superseded_by: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("claims.id"), nullable=True)


class ProjectPerson(ProjectBase):
    """Project-scoped fields only. Identity (name/email/slack/skills) stays
    in the global team_members table, keyed by member_id -- this table never
    duplicates identity, only per-project state."""

    __tablename__ = "people"

    member_id: Mapped[str] = mapped_column(String(100), primary_key=True)  # global team_members.id
    current_tasks: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class Todo(ProjectBase):
    __tablename__ = "todos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(SAEnum(TodoStatus), default=TodoStatus.OPEN)
    due_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Conflict(ProjectBase):
    __tablename__ = "conflicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Comma-separated claims.id list -- a conflict can arise from more than a
    # single pair (e.g. three people each reporting a different status for
    # the same thing), so this isn't a fixed-arity claim_a/claim_b pair.
    claim_ids: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(SAEnum(ConflictSeverity), default=ConflictSeverity.MEDIUM)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolution: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    def claim_id_list(self) -> list[int]:
        return [int(x) for x in self.claim_ids.split(",") if x.strip()]

    @staticmethod
    def format_claim_ids(ids: list[int]) -> str:
        return ",".join(str(i) for i in ids)


# --- engine/session plumbing -------------------------------------------------
# Each project gets its own project.db file (not a shared engine like the
# main app database), so these helpers exist to look up/cache the right
# engine per project name rather than hardcoding one global engine.

@lru_cache(maxsize=10)
def _engine_for(db_path_str: str):
    """One SQLAlchemy engine per project.db path, cached so repeated calls
    for the same project reuse the same connection pool instead of opening
    a fresh engine every time."""
    return create_engine(f"sqlite:///{db_path_str}", connect_args={"check_same_thread": False})


def get_project_engine(manager_id: str, project_name: str):
    """Resolve (manager_id, project_name) -> its project.db path (via
    paths.py, nested under that manager's own directory as of step 15) and
    return the (cached) engine for it, creating the parent directory if
    needed."""
    from app.projectkb.paths import project_db_path

    path = project_db_path(manager_id, project_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    return _engine_for(str(path))


def get_project_session(manager_id: str, project_name: str):
    """Open a new SQLAlchemy Session bound to this project's own database.
    Caller is responsible for closing it (mirrors app.tenancy.db.get_manager_db)."""
    engine = get_project_engine(manager_id, project_name)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return Session()


def init_project_db(manager_id: str, project_name: str) -> None:
    """Create claims/people/todos/conflicts tables in this project's
    project.db if they don't already exist. Called by
    paths.ensure_project_scaffold(), idempotent."""
    engine = get_project_engine(manager_id, project_name)
    ProjectBase.metadata.create_all(bind=engine)
