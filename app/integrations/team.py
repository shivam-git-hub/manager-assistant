from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.database import get_db, TeamMember
from app.kb.schemas import TeamMemberCreate, TeamMemberResponse
from typing import List

router = APIRouter(prefix="/api/team", tags=["Team"])

@router.get("", response_model=List[TeamMemberResponse])
def get_team_members(db: Session = Depends(get_db)):
    result = db.scalars(select(TeamMember)).all()
    return result

@router.post("", response_model=TeamMemberResponse, status_code=status.HTTP_201_CREATED)
def create_or_update_team_member(member: TeamMemberCreate, db: Session = Depends(get_db)):
    # Check if team member already exists
    existing = db.get(TeamMember, member.id)
    if existing:
        # Update existing
        existing.name = member.name
        existing.role = member.role
        existing.slack_handle = member.slack_handle
        existing.outlook_email = member.outlook_email
        existing.timezone = member.timezone
        db.commit()
        db.refresh(existing)
        return existing
    
    # Create new
    new_member = TeamMember(
        id=member.id,
        name=member.name,
        role=member.role,
        slack_handle=member.slack_handle,
        outlook_email=member.outlook_email,
        timezone=member.timezone
    )
    db.add(new_member)
    db.commit()
    db.refresh(new_member)
    return new_member
