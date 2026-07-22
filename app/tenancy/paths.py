"""Per-manager home directory -- the top-level unit of data isolation.
Mirrors app/projectkb/paths.py's per-project pattern one level up: each
manager gets managers/<manager_id>/ containing their own db.sqlite,
projects/<slug>/ (formerly top-level projects/<slug>/), job_state.json, and
tracked_contacts.json (formerly under data/). No manager_id column on any
table anywhere -- isolation is structural (separate files, separate SQLite
engines), not query-discipline-dependent. See prompts/step_15_per_manager_datadb.md.
"""
from pathlib import Path

from app.config import BASE_DIR

MANAGERS_DIR = BASE_DIR / "managers"


def manager_dir(manager_id: str) -> Path:
    return MANAGERS_DIR / manager_id


def manager_db_path(manager_id: str) -> Path:
    return manager_dir(manager_id) / "db.sqlite"


def manager_projects_dir(manager_id: str) -> Path:
    return manager_dir(manager_id) / "projects"


def manager_job_state_path(manager_id: str) -> Path:
    return manager_dir(manager_id) / "job_state.json"


def manager_blocklist_path(manager_id: str) -> Path:
    """Step 20: the track-everything inversion -- blocklist.json replaces
    the old tracked_contacts.json/tracked_channels.json allowlists."""
    return manager_dir(manager_id) / "blocklist.json"


def manager_memory_md_path(manager_id: str) -> Path:
    """Step 25 (dream job) owns writing this -- durable per-user facts,
    Hermes-style long-term memory (spec §3). Step 23 (heartbeat) only
    reads it as optional context and must not error before it exists."""
    return manager_dir(manager_id) / "memory.md"


def manager_events_md_path(manager_id: str) -> Path:
    """Step 25: timestamped log of the user's own key events, append-only."""
    return manager_dir(manager_id) / "events.md"


def manager_dump_md_path(manager_id: str) -> Path:
    """Step 25: literal reference dump (contacts, feature descriptions,
    etc.) for future RAG use, append-only."""
    return manager_dir(manager_id) / "dump.md"


def ensure_manager_scaffold(manager_id: str) -> Path:
    """Create this manager's home directory and initialize db.sqlite
    (idempotent). Called at provisioning time -- dev-login (step 12) or
    Outlook sign-in (step 13) -- not at app boot, unlike the old global
    app.database.init_db()."""
    mdir = manager_dir(manager_id)
    mdir.mkdir(parents=True, exist_ok=True)
    manager_projects_dir(manager_id).mkdir(exist_ok=True)

    from app.tenancy.db import init_manager_db

    init_manager_db(manager_id)

    return mdir
