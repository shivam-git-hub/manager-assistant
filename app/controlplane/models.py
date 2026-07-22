"""The control-plane DB: the one genuinely global piece of state in a
multi-manager deployment. Everything else (team members, unified messages,
projects/tasks, projectkb's per-project files) becomes per-manager in
step 15 -- this file only holds identity, sessions, and per-manager
connection credentials (which Slack workspace / Outlook mailbox belongs to
which manager), stored in data/controlplane.sqlite, separate from the
(soon to be per-manager) data/db.sqlite.

AuthSession is deliberately not named "Session" -- half the codebase does
`from sqlalchemy.orm import Session` for type hints, and shadowing that
name here would be a standing footgun.
"""
import os
from datetime import datetime
from typing import Optional
from sqlalchemy import create_engine, String, DateTime, ForeignKey, Text, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.config import DATA_DIR
from app import timeservice

# Env-overridable so tests can point this at an isolated file instead of the
# real dev/prod one -- tests/conftest.py sets CONTROLPLANE_DB_PATH before
# importing app.main. Without this, the test suite's per-test wipe-and-
# recreate cycle (needed so managers/sessions don't leak between tests) was
# nuking real signed-in sessions and OAuth installations on every test run.
CONTROLPLANE_DB_PATH = DATA_DIR / os.getenv("CONTROLPLANE_DB_FILENAME", "controlplane.sqlite")

engine = create_engine(f"sqlite:///{CONTROLPLANE_DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class ControlPlaneBase(DeclarativeBase):
    pass


class Manager(ControlPlaneBase):
    __tablename__ = "managers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid4 hex
    email: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AuthSession(ControlPlaneBase):
    __tablename__ = "auth_sessions"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    manager_id: Mapped[str] = mapped_column(String(36), ForeignKey("managers.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class OutlookInstallation(ControlPlaneBase):
    """One manager's connected Outlook mailbox. token_cache_json is a
    serialized MSAL SerializableTokenCache blob (replaces the old
    single-file data/outlook_token_cache.json from the device-code era)."""

    __tablename__ = "outlook_installations"

    manager_id: Mapped[str] = mapped_column(String(36), ForeignKey("managers.id"), primary_key=True)
    mailbox_email: Mapped[str] = mapped_column(String(255))
    token_cache_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Comma-separated Graph scopes actually granted so far (e.g. "Mail.Read,
    # User.Read", later "Mail.Read,User.Read,Mail.Send" once the user opts
    # into send access via the incremental-consent /auth/outlook/enable-send
    # flow). Drives the "Enable send" button's state in the manage UI.
    granted_scopes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class Agent(ControlPlaneBase):
    """Pool slot (step 17 piece 2a -- prompts/step_17_agent_pool.md). Each
    row is a distinctly-named, separately-registered Slack app, pre-created
    out of band by the admin (subproblem 0) and seeded via
    scripts/seed_agents.py -- NOT created dynamically by this code.
    `manager_id` unique+nullable enforces the 1:1 (one bot per manager, one
    bot per manager) at the schema level: null while unclaimed, set once a
    manager redeems the access code and claims this slot.

    Piece 2b (done): `/auth/slack/install`, the webhook (routed by
    `slack_app_id` == the payload's `api_app_id`), `send()`, `/connections`,
    disconnect, and `slack_poll` all read from this table now -- the old
    team_id-keyed `SlackInstallation` table is retired (no live data ever
    existed to migrate; `SLACK_CLIENT_ID` was empty until this pool model)."""

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)  # slug, e.g. "atlas"
    name: Mapped[str] = mapped_column(String(100))  # display name for the picker UI
    slack_app_id: Mapped[str] = mapped_column(String(50), unique=True)  # Slack's "App ID" -- webhook routing key in piece 2b
    slack_client_id: Mapped[str] = mapped_column(String(100))
    slack_client_secret: Mapped[str] = mapped_column(Text)
    slack_signing_secret: Mapped[str] = mapped_column(Text)
    manager_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("managers.id"), unique=True, nullable=True)
    team_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    bot_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # User-token grant (piece 1, relocated here in piece 2b) -- the
    # manager's own Slack identity, requested via user_scope alongside the
    # bot scope at install time. Used for polling the manager's own DMs
    # (app/integrations/slack.py fetch_since); distinct from bot_token,
    # which can only see conversations the bot itself is a member of.
    user_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    installed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Employee(ControlPlaneBase):
    """Org directory (step 18 -- prompts/step_18_registry_and_scaffold.md,
    spec/architecture_v2_kb.md §3). Seeded by scripts/seed_employees.py from
    a gitignored employees.json (admin process now; Graph directory sync
    later). This is the membership-picker source for the projects registry
    below -- distinct from Manager (identity/auth), though in practice every
    Manager is also an employee in real org terms; the two tables are not
    kept in sync automatically in v2.

    created_at uses the sim-time service, not datetime.now(), per CLAUDE.md's
    "wall-clock reads forbidden in new code" rule -- a stricter bar than the
    other tables in this file (Manager/AuthSession/OutlookInstallation/Agent
    predate that rule and are left alone)."""

    __tablename__ = "employees"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid4 hex
    email: Mapped[str] = mapped_column(String(255), unique=True)  # always lowercase
    slack_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    name: Mapped[str] = mapped_column(String(100))
    role: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    skills: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON-serialized list
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())


class Project(ControlPlaneBase):
    """Global projects registry (spec §3.1 -- "why projects are global"):
    teammates' dashboards read shared project data, so it can't live inside
    one manager's private managers/<id>/ directory. `app/database.py` also
    has an older, unrelated per-manager Project class (v1 philosophy, one
    row per manager db.sqlite) -- left untouched this step; any module that
    needs both imports this one aliased, e.g.
    `from app.controlplane.models import Project as RegistryProject`."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid4 hex
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String(20))  # "team" | "personal"
    manager_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("managers.id"))
    supervisors: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON list of employee ids
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())


class ProjectMember(ControlPlaneBase):
    """One row per (project, employee) membership. Unique on the pair so
    re-adding an existing member is a no-op at the schema level, same
    reasoning as Agent.manager_id's unique constraint elsewhere in this
    file."""

    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "employee_id", name="uq_project_member"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid4 hex
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"))
    employee_id: Mapped[str] = mapped_column(String(36), ForeignKey("employees.id"))
    role: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)


def init_controlplane_db() -> None:
    """Schema create + idempotent ALTER-TABLE migration checks -- same
    PRAGMA-based pattern as app.tenancy.db.init_manager_db, needed here too
    now that this DB has evolved past its step-12 shape (e.g. the
    granted_scopes/team_name columns added for the optional-send-permission
    feature)."""
    ControlPlaneBase.metadata.create_all(bind=engine)

    with engine.begin() as conn:
        outlook_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(outlook_installations)")).fetchall()]
        if "granted_scopes" not in outlook_cols:
            conn.execute(text("ALTER TABLE outlook_installations ADD COLUMN granted_scopes TEXT"))

        agent_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(agents)")).fetchall()]
        if "user_token" not in agent_cols:
            conn.execute(text("ALTER TABLE agents ADD COLUMN user_token TEXT"))
        if "user_id" not in agent_cols:
            conn.execute(text("ALTER TABLE agents ADD COLUMN user_id VARCHAR(50)"))


def get_controlplane_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def list_provisioned_managers(controlplane_db) -> list:
    """Every manager who has ever logged in (a row created at first login) --
    used by the background scheduler (app/projectkb/scheduler.py) to fan out
    the fixed job list across every manager's own db.sqlite each tick."""
    return controlplane_db.query(Manager).all()
