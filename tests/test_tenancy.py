import uuid

from app.database import TeamMember
from app.tenancy.paths import ensure_manager_scaffold, manager_dir
from app.tenancy.db import get_manager_session


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
