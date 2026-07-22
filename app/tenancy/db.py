"""Per-manager engine/session plumbing -- directly mirrors
app/projectkb/models.py's _engine_for/get_project_engine/get_project_session
pattern one level up. Replaces the old single global engine/SessionLocal/
get_db that used to live in app.database (retired in step 15 -- see
app/database.py, which now only holds Base + the table classes; those stay
engine-agnostic, only *which engine* a session is bound to changes here).

NOT a shared engine with a manager_id filter column on every table -- each
manager's db.sqlite is a fully separate SQLite file, so isolation is
structural rather than query-discipline-dependent.
"""
import logging
from functools import lru_cache

from fastapi import Depends
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session as DBSession

from app.tenancy.paths import manager_db_path
from app.controlplane.models import Manager, SessionLocal as ControlPlaneSessionLocal
from app.controlplane.auth import get_current_manager

logger = logging.getLogger(__name__)


@lru_cache(maxsize=64)
def _engine_for(db_path_str: str):
    """One SQLAlchemy engine per manager db.sqlite path, cached so repeated
    calls for the same manager reuse the same connection pool."""
    return create_engine(f"sqlite:///{db_path_str}", connect_args={"check_same_thread": False})


def get_manager_engine(manager_id: str):
    path = manager_db_path(manager_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    return _engine_for(str(path))


def get_manager_session(manager_id: str) -> DBSession:
    """Open a new Session bound to this manager's own database. Caller is
    responsible for closing it (mirrors the old app.database.get_db)."""
    engine = get_manager_engine(manager_id)
    SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return SessionFactory()


def init_manager_db(manager_id: str) -> None:
    """Schema create + column migrations + Harry seed + entity backfill +
    default job seed, scoped to one manager's db.sqlite. Mirrors the old
    global app.database.init_db(), just parameterized -- runs at
    provisioning time (see app.tenancy.paths.ensure_manager_scaffold), not
    at app boot. App boot instead sweeps the migration checks below across
    every already-provisioned manager -- see app.main's lifespan."""
    from app.database import Base, TeamMember, Project
    from app.outbound import OutboundQueue  # noqa: F401 -- registers with Base metadata
    from app.kb import models as kb_models  # noqa: F401
    from app.scheduler import ScheduledJob, seed_default_jobs  # noqa: F401
    from app.followups import Followup  # noqa: F401
    from app.brief import Brief  # noqa: F401
    from app.agent.notes import AgentNote  # noqa: F401

    engine = get_manager_engine(manager_id)
    Base.metadata.create_all(bind=engine)

    db = get_manager_session(manager_id)
    try:
        result = db.execute(text("PRAGMA table_info(unified_messages)")).fetchall()
        columns = [row[1] for row in result]
        if "direction" not in columns:
            db.execute(text("ALTER TABLE unified_messages ADD COLUMN direction VARCHAR(10) DEFAULT 'inbound'"))
            db.commit()
        if "receiver_raw_id" not in columns:
            db.execute(text("ALTER TABLE unified_messages ADD COLUMN receiver_raw_id VARCHAR(100)"))
            db.commit()
        if "receiver_mapped_name" not in columns:
            db.execute(text("ALTER TABLE unified_messages ADD COLUMN receiver_mapped_name VARCHAR(100)"))
            db.commit()
        if "created_at" not in columns:
            db.execute(text("ALTER TABLE unified_messages ADD COLUMN created_at DATETIME"))
            db.commit()

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
                timezone="Asia/Kolkata",
            )
            db.add(harry)
            db.commit()

        projects = db.query(Project).all()
        for p in projects:
            from app.kb.models import get_or_create_entity, slugify
            get_or_create_entity(db, slug=f"project:{slugify(p.name)}", type="project", name=p.name, ref_id=str(p.id))

        members = db.query(TeamMember).all()
        for m in members:
            from app.kb.models import get_or_create_entity
            get_or_create_entity(db, slug=f"person:{m.id}", type="person", name=m.name, ref_id=m.id)

        seed_default_jobs(db)
        logger.debug(f"[tenancy] initialized db.sqlite for manager={manager_id}")
    finally:
        db.close()


def get_manager_db(manager: Manager = Depends(get_current_manager)):
    """FastAPI dependency: resolves the logged-in manager from the session
    cookie, opens a session bound to THEIR db.sqlite. Replaces
    app.database.get_db for every cookie-authenticated route."""
    session = get_manager_session(manager.id)
    try:
        yield session
    finally:
        session.close()


def list_provisioned_manager_ids() -> list:
    """Every manager who has ever logged in -- used by background jobs
    (app/projectkb/scheduler.py) that must iterate each manager's own DB now
    that there's no single shared one to run against."""
    db = ControlPlaneSessionLocal()
    try:
        return [m.id for m in db.query(Manager).all()]
    finally:
        db.close()
