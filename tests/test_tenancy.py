import uuid

from sqlalchemy import text

from app.database import TeamMember
from app.tenancy.paths import ensure_manager_scaffold, manager_dir
from app.tenancy.db import get_manager_engine, get_manager_session, init_manager_db


def test_manager_scaffolds_are_fully_independent(clean_controlplane_db):
    m1 = f"test-{uuid.uuid4().hex}"
    m2 = f"test-{uuid.uuid4().hex}"

    try:
        ensure_manager_scaffold(m1)
        ensure_manager_scaffold(m2)

        db1 = get_manager_session(m1)
        db2 = get_manager_session(m2)
        try:
            # Harry is seeded independently in both.
            assert db1.query(TeamMember).filter(TeamMember.id == "U_HARRY").first() is not None
            assert db2.query(TeamMember).filter(TeamMember.id == "U_HARRY").first() is not None

            db1.add(TeamMember(id="U_ONLY_M1", name="Only In M1", role="Developer"))
            db1.commit()

            assert db1.query(TeamMember).filter(TeamMember.id == "U_ONLY_M1").first() is not None
            assert db2.query(TeamMember).filter(TeamMember.id == "U_ONLY_M1").first() is None
        finally:
            db1.close()
            db2.close()

        assert manager_dir(m1) != manager_dir(m2)
        assert manager_dir(m1).exists()
        assert manager_dir(m2).exists()
    finally:
        import shutil
        shutil.rmtree(manager_dir(m1), ignore_errors=True)
        shutil.rmtree(manager_dir(m2), ignore_errors=True)


def test_events_occurred_at_migration_adds_column_to_existing_db(clean_controlplane_db):
    """A manager db.sqlite created before Event.occurred_at existed (a
    bare `events` table with no occurred_at column) gets the column added
    on the next init_manager_db sweep -- app.main's lifespan re-runs this
    for every provisioned manager at boot, so this must be idempotent and
    non-destructive, not just work on a brand-new db."""
    m1 = f"test-{uuid.uuid4().hex}"
    try:
        engine = get_manager_engine(m1)
        with engine.connect() as conn:
            conn.execute(text(
                "CREATE TABLE events ("
                "id VARCHAR(36) PRIMARY KEY, type VARCHAR(20), severity INTEGER, "
                "title VARCHAR(255), body TEXT, project_ids TEXT, task_ids TEXT, "
                "claim_ids TEXT, general BOOLEAN, dreamed BOOLEAN, "
                "ui_state VARCHAR(10), created_at DATETIME"
                ")"
            ))
            conn.commit()

        db = get_manager_session(m1)
        try:
            before = [row[1] for row in db.execute(text("PRAGMA table_info(events)")).fetchall()]
            assert "occurred_at" not in before
        finally:
            db.close()

        init_manager_db(m1)

        db = get_manager_session(m1)
        try:
            after = [row[1] for row in db.execute(text("PRAGMA table_info(events)")).fetchall()]
            assert "occurred_at" in after
        finally:
            db.close()

        # Idempotent: running it again must not raise (ALTER TABLE ADD
        # COLUMN on an already-migrated db would error without the guard).
        init_manager_db(m1)
    finally:
        import shutil
        shutil.rmtree(manager_dir(m1), ignore_errors=True)


def test_ensure_manager_scaffold_is_idempotent(clean_controlplane_db):
    m1 = f"test-{uuid.uuid4().hex}"
    try:
        ensure_manager_scaffold(m1)
        ensure_manager_scaffold(m1)  # should not raise or duplicate Harry

        db = get_manager_session(m1)
        try:
            harrys = db.query(TeamMember).filter(TeamMember.id == "U_HARRY").all()
            assert len(harrys) == 1
        finally:
            db.close()
    finally:
        import shutil
        shutil.rmtree(manager_dir(m1), ignore_errors=True)
