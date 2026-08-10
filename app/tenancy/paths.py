"""Per-manager home directory -- the top-level unit of data isolation.
Each manager gets managers/<manager_id>/ containing their own db.sqlite,
blocklist.json, and memory.md/events.md files (written lazily by the
heartbeat/dream jobs). No manager_id column on any table anywhere --
isolation is structural (separate files, separate SQLite engines), not
query-discipline-dependent.

Project storage is a SEPARATE, global concern -- see app/projects/paths.py
(projects/<project_id>/, a sibling of managers/, not nested under any one
manager since teammates who are members, not owners, still need to read
shared project data).
"""
from pathlib import Path

from app.config import BASE_DIR

MANAGERS_DIR = BASE_DIR / "managers"


def manager_dir(manager_id: str) -> Path:
    return MANAGERS_DIR / manager_id


def manager_db_path(manager_id: str) -> Path:
    return manager_dir(manager_id) / "db.sqlite"


def manager_blocklist_path(manager_id: str) -> Path:
    """Track-everything-except patterns: what the ingest job must NOT read."""
    return manager_dir(manager_id) / "blocklist.json"


def manager_memory_md_path(manager_id: str) -> Path:
    """The dream job owns writing this -- durable per-user facts,
    Hermes-style long-term memory. The heartbeat job only reads it as
    optional context and must not error before it exists."""
    return manager_dir(manager_id) / "memory.md"


def manager_events_md_path(manager_id: str) -> Path:
    """Dream-written: timestamped log of the user's own key events, append-only."""
    return manager_dir(manager_id) / "events.md"


def ensure_manager_scaffold(manager_id: str) -> Path:
    """Create this manager's home directory and initialize db.sqlite
    (idempotent). Called at provisioning time -- dev-login or Outlook
    sign-in -- not at app boot."""
    mdir = manager_dir(manager_id)
    mdir.mkdir(parents=True, exist_ok=True)

    from app.tenancy.db import init_manager_db

    init_manager_db(manager_id)

    return mdir
