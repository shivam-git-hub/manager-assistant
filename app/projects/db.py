"""Per-project engine/session plumbing -- mirrors app/tenancy/db.py's
engine-cache pattern one level over: instead of one db.sqlite per manager,
each row in the global registry (app.controlplane.models.Project) gets its
own db.sqlite under projects/<project_id>/ (see app.projects.paths),
sibling to managers/ at the repo root rather than nested inside one
manager's private directory -- projects are shared by every member's
dashboard (spec/architecture_v2_kb.md §3.1).
"""
import logging
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session as DBSession

from app.projects.paths import project_db_path

logger = logging.getLogger(__name__)


@lru_cache(maxsize=128)
def _engine_for(db_path_str: str):
    """One SQLAlchemy engine per project db.sqlite path, cached so repeated
    calls for the same project reuse the same connection pool."""
    return create_engine(f"sqlite:///{db_path_str}", connect_args={"check_same_thread": False})


def get_project_engine(project_id: str):
    path = project_db_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    return _engine_for(str(path))


def get_project_session(project_id: str) -> DBSession:
    """Open a new Session bound to this project's own database. Caller is
    responsible for closing it (mirrors app.tenancy.db.get_manager_session)."""
    engine = get_project_engine(project_id)
    SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return SessionFactory()


def init_project_db(project_id: str) -> None:
    """Schema create for this project's db.sqlite -- tasks/archive/
    conflicts/suggestions/concerns/health_log. Called by
    app.projects.paths.ensure_project_scaffold(), idempotent."""
    from app.projects.models import ProjectBase

    engine = get_project_engine(project_id)
    ProjectBase.metadata.create_all(bind=engine)
    logger.debug(f"[projects] initialized db.sqlite for project={project_id}")
