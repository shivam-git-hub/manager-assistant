import pytest
from datetime import datetime, date
from sqlalchemy.orm import Session

from app.database import Project, Meeting, TeamMember
from app.kb.models import Conflict, Entity, AttributedClaim
from app.outbound import OutboundQueue
from app.brief import Brief
from app.seed_demo import check_demo_readiness

def test_checklist_evaluator_empty_db(db_session: Session):
    """
    Checklist should output all False values on a completely blank database.
    """
    result = check_demo_readiness(db_session)
    assert result["open_conflict_exists"] is False
    assert result["phoenix_degraded"] is False
    assert result["held_pings_exist"] is False
    assert result["briefs_exist"] is False
    assert result["next_meeting_scheduled"] is False

def test_checklist_evaluator_populated_db(db_session: Session):
    """
    Checklist should correctly flag True values when the specific demo states exist.
    """
    # 1. Seed Project Phoenix with degraded (Yellow) state
    phoenix = Project(id=1, name="Phoenix Project", status="active", health="yellow")
    db_session.add(phoenix)

    # 2. Seed a Meeting scheduled in the future
    meeting = Meeting(
        id=1,
        title="Phoenix Go/No-Go Align",
        starts_at=datetime(2226, 7, 17, 11, 0, 0),
        status="scheduled",
        attendees="[]"
    )
    db_session.add(meeting)

    # 3. Seed an open claims conflict
    conflict = Conflict(
        id=1,
        entity_id=1,
        claim_a_id=1,
        claim_b_id=2,
        severity="high",
        description="Bob claims sent but Alice says didn't get it",
        status="open",
        detected_at=datetime(2026, 7, 12, 10, 0, 0)
    )
    db_session.add(conflict)

    # 4. Seed a held outbound queue item
    held_alert = OutboundQueue(
        id=1,
        channel_type="slack",
        payload="{}",
        status="held",
        scheduled_release_at=datetime(2026, 7, 16, 9, 0, 0)
    )
    db_session.add(held_alert)

    # 5. Seed a morning brief
    brief = Brief(
        brief_date="2026-07-16",
        content="Testing"
    )
    db_session.add(brief)
    
    db_session.commit()

    # Run check
    result = check_demo_readiness(db_session)
    assert result["open_conflict_exists"] is True
    assert result["phoenix_degraded"] is True
    assert result["held_pings_exist"] is True
    assert result["briefs_exist"] is True
    assert result["next_meeting_scheduled"] is True
