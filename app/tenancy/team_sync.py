"""Keeps a manager's per-manager TeamMember roster (app.database.TeamMember
-- a manually maintained team-view table, backing GET/POST /api/team) in
sync with the control-plane Employee directory whenever someone is added as
a project member. Connectors/ingestion no longer read TeamMember at all
(sender/receiver/manager resolution matches Employee directly -- see
app.integrations.slack/outlook) -- this is purely so the team view reflects
who's actually on the manager's projects, without a separate manual step."""
from typing import Optional

from app.database import TeamMember
from app.tenancy.db import get_manager_session


def sync_team_member_from_employee(manager_id: str, employee, role: Optional[str] = None) -> None:
    """Upserts one TeamMember row in `manager_id`'s own db.sqlite from a
    real Employee row (control-plane) -- keyed by employee.id rather than
    the historical "Slack UserID or Email" convention, since nothing
    resolves messages against TeamMember.id anymore."""
    db = get_manager_session(manager_id)
    try:
        member = db.get(TeamMember, employee.id)
        if member is None:
            db.add(TeamMember(
                id=employee.id,
                name=employee.name,
                role=role or employee.role or "Member",
                slack_handle=employee.slack_id,
                outlook_email=employee.email,
            ))
        else:
            member.name = employee.name
            if role:
                member.role = role
            member.slack_handle = employee.slack_id
            member.outlook_email = employee.email
        db.commit()
    finally:
        db.close()
