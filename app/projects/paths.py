"""Per-project home directory -- global, NOT nested under any one manager
(spec/architecture_v2_kb.md §3.1: teammates' dashboards read shared project
data, so it can't live inside a private managers/<id>/ directory). Mirrors
app/tenancy/paths.py's per-manager pattern one level over: projects/<id>/ is
a sibling of managers/ at the repo root.

Deliberately a fresh package, not a rework of app.projectkb.paths -- that
module is an orphaned v1 sketch (per-manager-nested projects/<slug>/, no
global registry) kept around only as reading material and deleted in a
later step; see spec/architecture_v2_kb.md Appendix A and
prompts/step_18_registry_and_scaffold.md.

Not env-redirected for tests, same as MANAGERS_DIR: tests write real
projects/<uuid>/ dirs (uuid ids -> no collisions across test runs) and
rmtree them in teardown -- see tests/test_projects_registry.py.
"""
from pathlib import Path
from typing import Optional

from app.config import BASE_DIR

PROJECTS_DIR = BASE_DIR / "projects"


def project_dir(project_id: str) -> Path:
    return PROJECTS_DIR / project_id


def vault_dir(project_id: str) -> Path:
    return project_dir(project_id) / "vault"


def project_db_path(project_id: str) -> Path:
    return project_dir(project_id) / "db.sqlite"


def project_md_path(project_id: str) -> Path:
    return project_dir(project_id) / "project.md"


def summary_md_path(project_id: str) -> Path:
    return project_dir(project_id) / "summary.md"


def events_md_path(project_id: str) -> Path:
    return project_dir(project_id) / "events.md"


def notes_md_path(project_id: str) -> Path:
    return project_dir(project_id) / "notes.md"


def _project_md_template(name: str, description: Optional[str]) -> str:
    """project.md is manager-owned (written via the UI, not by any job) --
    title = name, then the free-text description, then the three empty
    sections the manager fills in over time."""
    desc_block = f"{description}\n\n" if description else ""
    return f"# {name}\n\n{desc_block}## Overview\n\n## Milestones\n\n## KPIs\n"


def _summary_md_template() -> str:
    return "# Summary\n\n_No synthesis yet — the dream job writes this._\n"


def _events_md_template() -> str:
    return "# Events\n\n_The dream job appends timestamped key events here._\n"


def _notes_md_template() -> str:
    return "# Notes\n\n_Manager and agent keep special instructions and references here._\n"


def ensure_project_scaffold(project_id: str, name: str, description: Optional[str] = None) -> Path:
    """Create this project's on-disk layout (idempotent -- never overwrites
    an md file that already exists) and initialize its db.sqlite. Called at
    project-creation time (app.api.projects_registry.create_project)."""
    pdir = project_dir(project_id)
    pdir.mkdir(parents=True, exist_ok=True)
    vault_dir(project_id).mkdir(exist_ok=True)

    if not project_md_path(project_id).exists():
        project_md_path(project_id).write_text(_project_md_template(name, description), encoding="utf-8")
    if not summary_md_path(project_id).exists():
        summary_md_path(project_id).write_text(_summary_md_template(), encoding="utf-8")
    if not events_md_path(project_id).exists():
        events_md_path(project_id).write_text(_events_md_template(), encoding="utf-8")
    if not notes_md_path(project_id).exists():
        notes_md_path(project_id).write_text(_notes_md_template(), encoding="utf-8")

    from app.projects.db import init_project_db

    init_project_db(project_id)

    return pdir
