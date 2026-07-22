from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.database import TeamMember
from app.tenancy.db import get_manager_db
from app.kb.schemas import TeamMemberCreate, TeamMemberResponse
from typing import List

router = APIRouter(prefix="/api/team", tags=["Team"])

@router.get("", response_model=List[TeamMemberResponse])
def get_team_members(db: Session = Depends(get_manager_db)):
    result = db.scalars(select(TeamMember)).all()
    return result

@router.post("", response_model=TeamMemberResponse, status_code=status.HTTP_201_CREATED)
def create_or_update_team_member(member: TeamMemberCreate, db: Session = Depends(get_manager_db)):
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
        
        # Ensure KB entity exists/updates
        from app.kb.models import get_or_create_entity
        get_or_create_entity(db, slug=f"person:{existing.id}", type="person", name=existing.name, ref_id=existing.id)
        
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
    
    # Auto-create KB entity
    from app.kb.models import get_or_create_entity
    get_or_create_entity(db, slug=f"person:{new_member.id}", type="person", name=new_member.name, ref_id=new_member.id)
    
    return new_member

@router.delete("/{member_id}", status_code=status.HTTP_200_OK)
def delete_team_member(member_id: str, db: Session = Depends(get_manager_db)):
    member = db.get(TeamMember, member_id)
    if not member:
        raise HTTPException(status_code=404, detail="Team member not found")
    db.delete(member)
    db.commit()
    return {"status": "ok", "detail": f"Team member {member_id} deleted successfully"}
