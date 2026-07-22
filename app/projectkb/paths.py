from pathlib import Path

from app.projectkb.enums import TimelineSection


def _timeline_template() -> str:
    sections = "\n\n".join(f"## {s.value}\n" for s in TimelineSection)
    return f"# Timeline\n\n{sections}"


def slugify(name: str) -> str:
    return "-".join(name.strip().lower().split())


def project_dir(manager_id: str, project_name: str) -> Path:
    from app.tenancy.paths import manager_projects_dir

    return manager_projects_dir(manager_id) / slugify(project_name)


def files_dir(manager_id: str, project_name: str) -> Path:
    return project_dir(manager_id, project_name) / "files"


def project_md_path(manager_id: str, project_name: str) -> Path:
    return project_dir(manager_id, project_name) / "project.md"


def timeline_md_path(manager_id: str, project_name: str) -> Path:
    return project_dir(manager_id, project_name) / "timeline.md"


def notes_md_path(manager_id: str, project_name: str) -> Path:
    return project_dir(manager_id, project_name) / "notes.md"


def index_md_path(manager_id: str, project_name: str) -> Path:
    return project_dir(manager_id, project_name) / "index.md"


def project_db_path(manager_id: str, project_name: str) -> Path:
    return project_dir(manager_id, project_name) / "project.db"


def ensure_project_scaffold(manager_id: str, project_name: str) -> Path:
    """Create the on-disk layout for a project if it doesn't already exist.

    Idempotent: never overwrites files that already exist.
    """
    pdir = project_dir(manager_id, project_name)
    pdir.mkdir(parents=True, exist_ok=True)
    files_dir(manager_id, project_name).mkdir(exist_ok=True)

    if not project_md_path(manager_id, project_name).exists():
        project_md_path(manager_id, project_name).write_text(
            f"# {project_name}\n\n## Description\n\n## Milestones\n\n## Status\n",
            encoding="utf-8",
        )
    if not timeline_md_path(manager_id, project_name).exists():
        timeline_md_path(manager_id, project_name).write_text(_timeline_template(), encoding="utf-8")
    if not notes_md_path(manager_id, project_name).exists():
        notes_md_path(manager_id, project_name).write_text("", encoding="utf-8")
    if not index_md_path(manager_id, project_name).exists():
        index_md_path(manager_id, project_name).write_text("", encoding="utf-8")

    # project.db tables are created lazily by models.init_project_db()
    from app.projectkb.models import init_project_db

    init_project_db(manager_id, project_name)

    return pdir
