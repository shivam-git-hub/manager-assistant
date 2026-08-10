"""The control-plane DB: the one genuinely global piece of state in a
multi-manager deployment. Everything else (team members, unified messages,
projects/tasks, projectkb's per-project files) is per-manager or
per-project -- this file only holds identity, sessions, the org directory,
the projects registry, and per-manager connector credentials, stored in
data/controlplane.sqlite.

AuthSession is deliberately not named "Session" -- half the codebase does
`from sqlalchemy.orm import Session` for type hints, and shadowing that
name here would be a standing footgun.
"""
import json
import os
from datetime import datetime
from typing import Optional
from sqlalchemy import create_engine, String, DateTime, ForeignKey, Text, text
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


class AuthSession(ControlPlaneBase):
    """Step 30: manager_id stores an Employee.id -- there is no separate
    Manager identity table anymore, Employee IS the auth identity."""

    __tablename__ = "auth_sessions"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    manager_id: Mapped[str] = mapped_column(String(36), ForeignKey("employees.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class Agent(ControlPlaneBase):
    """Pool slot. Each row is a distinctly-named, separately-registered
    Slack app, pre-created AND pre-installed into the workspace out of band
    by the admin and inserted manually into this table -- NOT created or
    installed dynamically by this code. `manager_id` unique+nullable
    enforces the 1:1 (one bot per manager) at the schema level: null while
    unclaimed, set once a manager redeems the access code and claims this
    slot -- claiming is pure bookkeeping (see app/controlplane/agents.py),
    it never talks to Slack.

    Bot identity and message-reading are deliberately independent concerns:
    a manager can connect Slack for reading without claiming a bot, and vice
    versa. `bot_token`/`team_id`/`installed_at` are populated ONLY by the
    admin, out of band, via a manual row insert (the admin installs
    each pool app to the workspace themselves via that Slack app's own
    "OAuth & Permissions -> Install to Workspace" page, which hands them the
    bot token directly -- no OAuth code in this codebase is involved).
    Reading a manager's own messages lives on their Employee row instead --
    see `Employee` below and app/controlplane/slack_auth.py.

    `user_token`/`user_id` are vestigial: no code path writes them anymore
    (left in place because SQLite ALTER-DROP is not worth it here)."""

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)  # slug, e.g. "atlas"
    name: Mapped[str] = mapped_column(String(100))  # display name for the picker UI
    slack_app_id: Mapped[str] = mapped_column(String(50), unique=True)  # Slack's "App ID" -- webhook routing key
    slack_client_id: Mapped[str] = mapped_column(String(100))
    slack_client_secret: Mapped[str] = mapped_column(Text)
    slack_signing_secret: Mapped[str] = mapped_column(Text)
    manager_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("employees.id"), unique=True, nullable=True)
    team_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    bot_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Vestigial -- see docstring above. No longer written.
    user_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    installed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Employee(ControlPlaneBase):
    """Org directory AND the single identity/credential table -- Employee.id
    IS the auth identity (step 30 removed the separate Manager table; every
    session, project-ownership, and agent-claim FK that used to point at
    managers.id now points at employees.id). Seeded by the admin manually
    inserting rows into this table (Graph directory sync later) -- an Employee row can exist
    with no login/connector activity at all (a teammate who's never logged
    in). `is_manager` is set True at login and never auto-unset.

    Connector credentials live here (a single source of truth rather than
    separate per-connector credential tables) and are populated ONLY by the
    Connectors-page flows (app/controlplane/outlook_auth.py's connect-mail
    route, app/controlplane/slack_auth.py's /install), never by login
    itself: "sign in with Microsoft" and "grant Pulse mailbox read access"
    are separate OAuth round-trips.

    created_at goes through app.timeservice, not datetime.now(), per
    CLAUDE.md's "wall-clock reads forbidden" rule -- a stricter bar than the
    other tables in this file, which are left alone."""

    __tablename__ = "employees"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid4 hex
    email: Mapped[str] = mapped_column(String(255), unique=True)  # always lowercase
    slack_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    name: Mapped[str] = mapped_column(String(100))
    role: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    skills: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON-serialized list
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())

    # -- manager flag -----------------------------------------------------
    is_manager: Mapped[bool] = mapped_column(default=False)

    # -- Outlook credentials ---------------------------------------------
    outlook_mailbox_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Serialized MSAL SerializableTokenCache blob.
    outlook_token_cache_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Comma-separated Graph scopes actually granted so far (e.g. "Mail.Read",
    # later "Mail.Read,Mail.Send" once the user opts into send access via
    # the incremental-consent /auth/outlook/enable-send flow).
    outlook_granted_scopes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # -- Slack reader credentials -----------------------------------------
    # slack_id above IS the reader's own Slack user id once connected --
    # no separate column needed for that.
    slack_team_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    slack_team_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    slack_user_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # -- Poll job audit (step 32 global scheduler) -------------------------
    # Stamped by app/projectkb/jobs/outlook_poll.py / slack_poll.py at the
    # end of a run that completed without raising -- NOT bumped when the
    # connector isn't configured for this employee (nothing ran). A stale
    # timestamp relative to the job's own interval_minutes (job_schedule.py)
    # is the audit signal that polling is failing for this employee.
    outlook_poll_last_success_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    slack_poll_last_success_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


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
    manager_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("employees.id"))
    supervisors: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON list of employee ids
    # JSON list of {"employee_id": ..., "role": ...|None} -- was a separate
    # ProjectMember table; collapsed to a column because every read is
    # "give me this project's members," never "which projects is employee X
    # on" at any scale that would need an indexed join (step 31).
    member_employee_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())


class Portfolio(ControlPlaneBase):
    """A manager's personal grouping of projects ("My Portfolios").

    Real entity, not a saved filter: it has its own name and is
    independently creatable/deletable. Scoped to the owning manager only (no
    sharing/membership concept, unlike Project)."""

    __tablename__ = "portfolios"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid4 hex
    name: Mapped[str] = mapped_column(String(255))
    manager_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("employees.id"))
    # JSON list of project ids -- was a separate PortfolioProject join
    # table; collapsed to a column for the same reason as
    # Project.member_employee_ids (step 31). Project.delete strips any
    # deleted project's id out of every portfolio's list so this never goes
    # stale.
    project_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: timeservice.now_ist())


def get_member_list(project: "Project") -> list:
    """Parse Project.member_employee_ids -> list of {"employee_id", "role"} dicts."""
    return json.loads(project.member_employee_ids) if project.member_employee_ids else []


def set_member_list(project: "Project", members: list) -> None:
    project.member_employee_ids = json.dumps(members) if members else None


def get_portfolio_project_ids(portfolio: "Portfolio") -> list:
    return json.loads(portfolio.project_ids) if portfolio.project_ids else []


def set_portfolio_project_ids(portfolio: "Portfolio", project_ids: list) -> None:
    portfolio.project_ids = json.dumps(project_ids) if project_ids else None


def init_controlplane_db() -> None:
    """Schema create + idempotent ALTER-TABLE migration checks -- same
    PRAGMA-based pattern as app.tenancy.db.init_manager_db."""
    ControlPlaneBase.metadata.create_all(bind=engine)

    with engine.begin() as conn:
        agent_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(agents)")).fetchall()]
        if "user_token" not in agent_cols:
            conn.execute(text("ALTER TABLE agents ADD COLUMN user_token TEXT"))
        if "user_id" not in agent_cols:
            conn.execute(text("ALTER TABLE agents ADD COLUMN user_id VARCHAR(50)"))

        employee_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(employees)")).fetchall()]
        employee_migrations = {
            "is_manager": "BOOLEAN DEFAULT 0",
            "outlook_mailbox_email": "VARCHAR(255)",
            "outlook_token_cache_json": "TEXT",
            "outlook_granted_scopes": "TEXT",
            "slack_team_id": "VARCHAR(50)",
            "slack_team_name": "VARCHAR(255)",
            "slack_user_token": "TEXT",
            "outlook_poll_last_success_at": "DATETIME",
            "slack_poll_last_success_at": "DATETIME",
        }
        for col_name, col_ddl in employee_migrations.items():
            if col_name not in employee_cols:
                conn.execute(text(f"ALTER TABLE employees ADD COLUMN {col_name} {col_ddl}"))

        project_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(projects)")).fetchall()]
        if "member_employee_ids" not in project_cols:
            conn.execute(text("ALTER TABLE projects ADD COLUMN member_employee_ids TEXT"))

        portfolio_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(portfolios)")).fetchall()]
        if "project_ids" not in portfolio_cols:
            conn.execute(text("ALTER TABLE portfolios ADD COLUMN project_ids TEXT"))


def get_controlplane_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def list_provisioned_managers(controlplane_db) -> list:
    """Every employee who has ever logged in as a manager (is_manager=True)
    -- used by the background scheduler (app/projectkb/scheduler.py) to fan
    out the fixed job list across every manager's own db.sqlite each tick.
    Returns Employee rows; callers use .id as the manager_id/scaffold key."""
    return controlplane_db.query(Employee).filter(Employee.is_manager == True).all()  # noqa: E712


def get_employee_by_email(db, email: str) -> Optional["Employee"]:
    """Case-insensitive lookup against the pre-seeded org directory --
    login gates on this: only someone the admin already manually inserted
    into this table may log in at all. Never creates a row."""
    return db.query(Employee).filter(Employee.email == email.lower()).first()


def get_employee_by_manager_id(db, manager_id: str) -> Optional["Employee"]:
    """Step 30: manager_id IS employees.id now -- this is a plain PK lookup,
    kept as a named helper (rather than inlining db.get(Employee, ...)
    everywhere) since call sites read more clearly as "give me the employee
    behind this manager_id"."""
    return db.get(Employee, manager_id)


def get_employee_by_slack_id(db, slack_id: Optional[str]) -> Optional["Employee"]:
    """Used by SlackConnector to resolve a sender/receiver/manager directly
    against the org directory instead of the per-manager TeamMember roster
    (connectors don't need TeamMember at all -- see its own docstring)."""
    if not slack_id:
        return None
    return db.query(Employee).filter(Employee.slack_id == slack_id).first()
