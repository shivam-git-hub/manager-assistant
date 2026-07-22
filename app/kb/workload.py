import json
import logging
import re
from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import select, and_, func, or_
from pydantic import BaseModel

from app.database import Leave, ReassignmentSuggestion, Digest, Task, TeamMember, Project
from app.tenancy.db import get_manager_db
from app.kb.models import get_or_create_entity, slugify, TimelineEntry
from app.agent.gemini_client import get_client
from app.config import FLASH_MODEL
from app.followups import get_dm_channel_id
from app.outbound import send_or_hold
from app import timeservice

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workload", tags=["Workload"])

# -----------------------------------------------------------------------------
# Pydantic Models
# -----------------------------------------------------------------------------

class LeaveCreate(BaseModel):
    member_id: str
    starts_on: date
    ends_on: date
    reason: Optional[str] = None

class LeaveResponse(BaseModel):
    id: int
    member_id: str
    starts_on: date
    ends_on: date
    reason: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

class ReassignmentSuggestionResponse(BaseModel):
    id: int
    leave_id: int
    task_id: int
    from_member_id: str
    to_member_id: str
    rationale: str
    status: str
    created_at: datetime
    decided_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class DigestResponse(BaseModel):
    id: int
    week_start: date
    content: Any  # JSON-parsed dict/list
    created_at: datetime

    class Config:
        from_attributes = True

# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------

@router.post("/leaves", response_model=LeaveResponse, status_code=status.HTTP_201_CREATED)
def create_leave(payload: LeaveCreate, db: Session = Depends(get_manager_db)):
    # Validate member
    member = db.scalars(select(TeamMember).where(TeamMember.id == payload.member_id)).first()
    if not member:
        raise HTTPException(status_code=404, detail=f"Team member '{payload.member_id}' not found")

    if payload.starts_on > payload.ends_on:
        raise HTTPException(status_code=422, detail="starts_on must be before or equal to ends_on")

    new_leave = Leave(
        member_id=payload.member_id,
        starts_on=payload.starts_on,
        ends_on=payload.ends_on,
        reason=payload.reason,
        created_at=timeservice.now_ist()
    )
    db.add(new_leave)
    db.commit()
    db.refresh(new_leave)

    # Generate Reassignment Suggestions deterministically in code
    tasks = db.scalars(
        select(Task).where(
            and_(
                Task.assignee_id == payload.member_id,
                Task.status != "completed"
            )
        )
    ).all()

    suggested_count = 0
    for task in tasks:
        # suggestions only for tasks due inside the window or overdue
        if task.due_date and task.due_date > payload.ends_on:
            continue

        target_date = task.due_date if (task.due_date and task.due_date >= payload.starts_on) else payload.starts_on

        # Find eligible other candidates (exclude Harry and person on leave)
        candidates = db.scalars(
            select(TeamMember).where(
                and_(
                    TeamMember.id != "U_HARRY",
                    TeamMember.id != payload.member_id
                )
            )
        ).all()

        eligible_candidates = []
        for c in candidates:
            # check if candidate is on leave on target_date
            active_leave = db.scalars(
                select(Leave).where(
                    and_(
                        Leave.member_id == c.id,
                        Leave.starts_on <= target_date,
                        Leave.ends_on >= target_date
                    )
                )
            ).first()
            if not active_leave:
                eligible_candidates.append(c)

        if not eligible_candidates:
            continue

        # Choose the least-loaded member
        candidate_loads = []
        for c in eligible_candidates:
            load_count = db.scalar(
                select(func.count(Task.id)).where(
                    and_(
                        Task.assignee_id == c.id,
                        Task.status != "completed"
                    )
                )
            ) or 0
            candidate_loads.append((load_count, c.name, c.id, c))

        # Tie-breaker: load_count, then name, then id
        candidate_loads.sort(key=lambda x: (x[0], x[1], x[2]))
        best_candidate = candidate_loads[0][3]

        # Call FLASH_MODEL for rationale
        rationale = None
        client = get_client()
        try:
            prompt_msgs = [
                {
                    "role": "system",
                    "content": "You are a concise project manager assistant. Draft a single sentence rationale (max 15 words) why this developer is chosen to take over a task during a leave based on having the lightest queue. Output ONLY the rationale."
                },
                {
                    "role": "user",
                    "content": f"Task: '{task.title}' assigned to {member.name}.\nSuggested backup: {best_candidate.name} (lightest load among eligible developers)."
                }
            ]
            resp = client.chat(model=FLASH_MODEL, messages=prompt_msgs, temperature=0.1)
            if resp.get("content"):
                rationale = resp["content"].strip().strip('"').strip("'")
        except Exception as e:
            logger.warning(f"Failed to generate LLM rationale, falling back. Error: {e}")

        if not rationale:
            rationale = f"Assigned to {best_candidate.name} as they currently have the lightest task queue."

        # Create suggestion
        suggestion = ReassignmentSuggestion(
            leave_id=new_leave.id,
            task_id=task.id,
            from_member_id=payload.member_id,
            to_member_id=best_candidate.id,
            rationale=rationale,
            status="suggested",
            created_at=timeservice.now_ist()
        )
        db.add(suggestion)
        suggested_count += 1

    db.commit()

    # Harry DMs the manager if suggestions are created
    if suggested_count > 0:
        manager = db.scalars(
            select(TeamMember).where(TeamMember.role.ilike("%manager%"))
        ).first()
        if manager:
            starts_str = payload.starts_on.strftime("%a")
            ends_str = payload.ends_on.strftime("%a")
            date_range = f"{starts_str}–{ends_str}" if payload.starts_on != payload.ends_on else starts_str
            alert_text = f"{member.name} is out {date_range}; {suggested_count} tasks land in that window — I've drafted reassignments on the workload page."
            manager_dm = get_dm_channel_id("U_HARRY", manager.id)
            send_or_hold("slack", {"channel": manager_dm, "text": alert_text}, db)

    return new_leave

@router.get("/leaves", response_model=List[LeaveResponse])
def list_leaves(active: Optional[bool] = Query(None), db: Session = Depends(get_manager_db)):
    stmt = select(Leave)
    if active:
        today = timeservice.now_ist().date()
        stmt = stmt.where(and_(Leave.starts_on <= today, Leave.ends_on >= today))
    return list(db.scalars(stmt).all())

@router.delete("/leaves/{id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_leave(id: int, db: Session = Depends(get_manager_db)):
    leave = db.scalars(select(Leave).where(Leave.id == id)).first()
    if not leave:
        raise HTTPException(status_code=404, detail="Leave not found")
    
    # Also delete any suggestions associated
    db.execute(
        ReassignmentSuggestion.__table__.delete().where(ReassignmentSuggestion.leave_id == id)
    )
    db.delete(leave)
    db.commit()

@router.get("/reassignments", response_model=List[ReassignmentSuggestionResponse])
def list_reassignments(status: Optional[str] = Query(None), db: Session = Depends(get_manager_db)):
    stmt = select(ReassignmentSuggestion)
    if status:
        stmt = stmt.where(ReassignmentSuggestion.status == status)
    return list(db.scalars(stmt).all())

@router.post("/reassignments/{id}/approve", response_model=ReassignmentSuggestionResponse)
def approve_reassignment(id: int, db: Session = Depends(get_manager_db)):
    sugg = db.scalars(select(ReassignmentSuggestion).where(ReassignmentSuggestion.id == id)).first()
    if not sugg:
        raise HTTPException(status_code=404, detail="Suggestion not found")

    if sugg.status != "suggested":
        raise HTTPException(status_code=409, detail=f"Suggestion already decided: {sugg.status}")

    # Load task and leave
    task = db.scalars(select(Task).where(Task.id == sugg.task_id)).first()
    leave = db.scalars(select(Leave).where(Leave.id == sugg.leave_id)).first()
    if not task:
        raise HTTPException(status_code=404, detail="Associated task not found")

    # Reassign task
    old_assignee_id = task.assignee_id
    task.assignee_id = sugg.to_member_id
    sugg.status = "approved"
    sugg.decided_at = timeservice.now_ist()

    # Get names
    from_member = db.scalars(select(TeamMember).where(TeamMember.id == sugg.from_member_id)).first()
    to_member = db.scalars(select(TeamMember).where(TeamMember.id == sugg.to_member_id)).first()
    from_name = from_member.name if from_member else sugg.from_member_id
    to_name = to_member.name if to_member else sugg.to_member_id

    # Add project timeline entry
    project = db.scalars(select(Project).where(Project.id == task.project_id)).first()
    if project:
        project_slug = f"project:{slugify(project.name)}"
        entity = get_or_create_entity(db, slug=project_slug, type="project", name=project.name, ref_id=str(project.id))
        timeline = TimelineEntry(
            entity_id=entity.id,
            happened_at=timeservice.now_ist(),
            summary=f"Task '{task.title}' reassigned {from_name}->{to_name} during leave",
            detail=f"Task reassigned from {from_name} to {to_name} due to leave {leave.starts_on if leave else ''} to {leave.ends_on if leave else ''}."
        )
        db.add(timeline)

    # Dispatch outbound DM messages via send_or_hold
    if from_member:
        old_dm = get_dm_channel_id("U_HARRY", from_member.id)
        old_text = f"Hi {from_name}, while you are away, '{task.title}' has been reassigned to {to_name} to keep progress on track."
        send_or_hold("slack", {"channel": old_dm, "text": old_text}, db)

    if to_member:
        new_dm = get_dm_channel_id("U_HARRY", to_member.id)
        new_text = f"Hi {to_name}, '{task.title}' has been reassigned to you from {from_name} during their leave window."
        send_or_hold("slack", {"channel": new_dm, "text": new_text}, db)

    db.commit()
    db.refresh(sugg)
    return sugg

@router.post("/reassignments/{id}/reject", response_model=ReassignmentSuggestionResponse)
def reject_reassignment(id: int, db: Session = Depends(get_manager_db)):
    sugg = db.scalars(select(ReassignmentSuggestion).where(ReassignmentSuggestion.id == id)).first()
    if not sugg:
        raise HTTPException(status_code=404, detail="Suggestion not found")

    if sugg.status != "suggested":
        raise HTTPException(status_code=409, detail=f"Suggestion already decided: {sugg.status}")

    sugg.status = "rejected"
    sugg.decided_at = timeservice.now_ist()
    db.commit()
    db.refresh(sugg)
    return sugg

@router.get("/digests", response_model=List[DigestResponse])
def list_digests(limit: int = Query(4), db: Session = Depends(get_manager_db)):
    stmt = select(Digest).order_by(Digest.week_start.desc()).limit(limit)
    digests = list(db.scalars(stmt).all())
    
    # Parse json content
    response = []
    for d in digests:
        try:
            parsed_content = json.loads(d.content)
        except Exception:
            parsed_content = d.content
        response.append({
            "id": d.id,
            "week_start": d.week_start,
            "content": parsed_content,
            "created_at": d.created_at
        })
    return response

# -----------------------------------------------------------------------------
# Weekly Digest Job Handler
# -----------------------------------------------------------------------------

def run_weekly_digest(db: Session) -> Optional[Digest]:
    now = timeservice.now_ist()
    monday_date = (now - timedelta(days=now.weekday())).date()

    # Check if we already have a digest for this week to prevent duplicates
    existing = db.scalars(select(Digest).where(Digest.week_start == monday_date)).first()
    if existing:
        logger.info(f"Weekly digest already exists for week start {monday_date}. Skipping.")
        return existing

    # Gather Signals
    # 1. Blocked tasks
    blocked_stmt = select(Task).where(Task.status == "blocked")
    blocked_tasks = db.scalars(blocked_stmt).all()
    blocked_signals = []
    for t in blocked_tasks:
        blocked_signals.append({
            "task_id": t.id,
            "title": t.title,
            "blockage_reason": t.blockage_reason or "Unknown blockage reason"
        })

    # 2. Overdue task counts per member
    today_date = now.date()
    overdue_stmt = select(Task.assignee_id, func.count(Task.id)).where(
        and_(
            Task.status != "completed",
            Task.due_date < today_date
        )
    ).group_by(Task.assignee_id)
    overdue_rows = db.execute(overdue_stmt).fetchall()
    overdue_signals = {row[0]: row[1] for row in overdue_rows if row[0]}

    # 3. High-frequency active conflict descriptions
    from app.kb.models import Conflict
    conflicts_stmt = select(Conflict).where(Conflict.status == "open")
    active_conflicts = db.scalars(conflicts_stmt).all()
    conflict_signals = [c.description for c in active_conflicts]

    # Combine Signals
    signals = {
        "blocked_tasks": blocked_signals,
        "overdue_counts_by_member": overdue_signals,
        "active_conflicts": conflict_signals
    }

    # Fetch valid member roster to validate LLM suggestions
    members = db.scalars(select(TeamMember)).all()
    member_ids = {m.id for m in members}

    # Call FLASH_MODEL
    suggestions = []
    client = get_client()
    try:
        system_instruction = (
            "You are a talent development and team coaching expert. Based on the provided project blockers, "
            "overdue task loads, and conflict signals, generate 2 to 4 actionable suggestions for training, "
            "workshops, books, newsletters, or coaching topics.\n\n"
            "Each suggestion must be focused on helping the team resolve their current bottlenecks. "
            "Provide the output strictly as a JSON list matching this structure:\n"
            "[\n"
            "  {\n"
            '    "audience": "team" or a specific member ID (must strictly match one of the IDs provided),\n'
            '    "suggestion": "Detailed suggestion (e.g., recommend a specific reading, tutorial, or course)",\n'
            '    "reason": "Clear explanation of why this is suggested based strictly on the signals provided"\n'
            "  }\n"
            "]\n\n"
            "Ground your suggestions ONLY in the provided signals. Do NOT hallucinate member IDs."
        )
        prompt = f"Team member IDs:\n{list(member_ids)}\n\nSignals:\n{json.dumps(signals)}"
        
        resp = client.chat(
            model=FLASH_MODEL,
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            json_mode=True
        )

        if resp.get("content"):
            parsed = json.loads(resp["content"])
            if isinstance(parsed, list):
                for item in parsed:
                    aud = item.get("audience")
                    sug = item.get("suggestion")
                    rea = item.get("reason")
                    if sug and rea:
                        # Validate audience
                        if aud == "team" or aud in member_ids:
                            suggestions.append({
                                "audience": aud,
                                "suggestion": sug,
                                "reason": rea
                            })
                        else:
                            logger.warning(f"Dropping suggestions for unrecognized audience: '{aud}'")
    except Exception as e:
        logger.error(f"Failed to generate weekly digest via LLM: {e}")

    # Fallback if LLM failed or generated empty list
    if not suggestions:
        suggestions = [
            {
                "audience": "team",
                "suggestion": "Read articles on systematic debugging and technical communication.",
                "reason": "General recommendation to reduce blockages and task queue buildup."
            }
        ]

    # Save to Digests Table
    digest_record = Digest(
        week_start=monday_date,
        content=json.dumps(suggestions),
        created_at=timeservice.now_ist()
    )
    db.add(digest_record)
    db.commit()
    db.refresh(digest_record)

    # Send a Slack DM to the manager
    manager = db.scalars(
        select(TeamMember).where(TeamMember.role.ilike("%manager%"))
    ).first()
    if manager:
        manager_dm = get_dm_channel_id("U_HARRY", manager.id)
        teaser_text = "Hi Shivam, I have compiled this week's technical digests and trainings tailored to our team's current blockers."
        send_or_hold("slack", {"channel": manager_dm, "text": teaser_text}, db)

    return digest_record
