import json
import logging
import re
from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import select, and_, func

from app.database import Meeting, ActionItem, Task, Project, TeamMember, UnifiedMessage
from app.tenancy.db import get_manager_db
from app.kb.models import Entity, TimelineEntry, AttributedClaim, get_or_create_entity, slugify
from app.kb.schemas import UnifiedMessageResponse
from app.agent.gemini_client import get_client, GeminiClient
from app.agent.registry import registry
from app.config import SMART_MODEL, FLASH_MODEL
from app.followups import get_dm_channel_id
from app.outbound import send_or_hold, is_quiet_hours, next_work_morning
from app import timeservice
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/meetings", tags=["Meetings"])

# -----------------------------------------------------------------------------
# Pydantic Schemas
# -----------------------------------------------------------------------------

class MeetingCreate(BaseModel):
    title: str
    starts_at: datetime
    ends_at: Optional[datetime] = None
    attendees: List[str]
    project_id: Optional[int] = None

class MeetingResponse(BaseModel):
    id: int
    title: str
    starts_at: datetime
    ends_at: Optional[datetime] = None
    attendees: List[str]
    project_id: Optional[int] = None
    mom_raw: Optional[str] = None
    mom_message_id: Optional[int] = None
    status: str
    created_at: datetime

    class Config:
        from_attributes = True

class ActionItemResponse(BaseModel):
    id: int
    meeting_id: int
    task_id: Optional[int] = None
    description: str
    owner_member_id: str
    due_date: Optional[date] = None
    created_at: datetime

    class Config:
        from_attributes = True

class MeetingDetailPageResponse(BaseModel):
    meeting: MeetingResponse
    action_items: List[ActionItemResponse]
    entity_slug: str

class MeetingUpdate(BaseModel):
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    status: Optional[str] = None

class MomRequest(BaseModel):
    text: str

# -----------------------------------------------------------------------------
# REST Endpoints
# -----------------------------------------------------------------------------

@router.post("", response_model=MeetingResponse, status_code=status.HTTP_201_CREATED)
def create_meeting(payload: MeetingCreate, db: Session = Depends(get_manager_db)):
    """
    Creates a scheduled meeting, registers its Entity, and schedules its pre-meeting brief.
    """
    # 1. Create meeting in DB
    meeting = Meeting(
        title=payload.title,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        attendees=json.dumps(payload.attendees),
        project_id=payload.project_id,
        status="scheduled"
    )
    db.add(meeting)
    db.commit()
    db.refresh(meeting)

    meeting_id = meeting.id

    # 2. Register Entity (meeting:<id>)
    get_or_create_entity(
        db,
        slug=f"meeting:{meeting_id}",
        type="meeting",
        name=payload.title,
        ref_id=str(meeting_id)
    )

    # 3. Schedule Pre-Meeting Brief Job
    # Triggers at starts_at - 30 minutes
    from app.scheduler import ScheduledJob
    brief_time = payload.starts_at - timedelta(minutes=30)
    now = timeservice.now_ist()
    
    if brief_time > now:
        job = ScheduledJob(
            job_type=f"pre_meeting_brief:{meeting_id}",
            payload=json.dumps({"meeting_id": meeting_id}),
            next_due_at=brief_time,
            enabled=True,
            catchup_policy="once"
        )
        db.add(job)
        db.commit()

    # Parse attendees list back for response
    return MeetingResponse(
        id=meeting.id,
        title=meeting.title,
        starts_at=meeting.starts_at,
        ends_at=meeting.ends_at,
        attendees=payload.attendees,
        project_id=meeting.project_id,
        mom_raw=meeting.mom_raw,
        mom_message_id=meeting.mom_message_id,
        status=meeting.status,
        created_at=meeting.created_at
    )


@router.get("", response_model=List[MeetingResponse])
def get_meetings(
    from_date: Optional[str] = Query(None, alias="from", description="Start date YYYY-MM-DD"),
    to_date: Optional[str] = Query(None, alias="to", description="End date YYYY-MM-DD"),
    db: Session = Depends(get_manager_db)
):
    """
    Retrieves all scheduled/completed meetings filtered by an optional date range.
    """
    stmt = select(Meeting)
    
    if from_date:
        try:
            from_dt = datetime.strptime(from_date, "%Y-%m-%d")
            stmt = stmt.where(Meeting.starts_at >= from_dt)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid from_date format. Must be YYYY-MM-DD.")
            
    if to_date:
        try:
            to_dt = datetime.strptime(to_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
            stmt = stmt.where(Meeting.starts_at <= to_dt)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid to_date format. Must be YYYY-MM-DD.")
            
    meetings = db.scalars(stmt.order_by(Meeting.starts_at.asc())).all()
    
    resp = []
    for m in meetings:
        try:
            atts = json.loads(m.attendees)
        except Exception:
            atts = []
        resp.append(MeetingResponse(
            id=m.id,
            title=m.title,
            starts_at=m.starts_at,
            ends_at=m.ends_at,
            attendees=atts,
            project_id=m.project_id,
            mom_raw=m.mom_raw,
            mom_message_id=m.mom_message_id,
            status=m.status,
            created_at=m.created_at
        ))
        
    return resp


@router.get("/{id}", response_model=MeetingDetailPageResponse)
def get_meeting_detail(id: int, db: Session = Depends(get_manager_db)):
    """
    Fetches a specific meeting, its actions list, and entity slug.
    """
    meeting = db.get(Meeting, id)
    if not meeting:
        raise HTTPException(status_code=404, detail=f"Meeting with ID {id} not found")
        
    try:
        atts = json.loads(meeting.attendees)
    except Exception:
        atts = []
        
    meeting_resp = MeetingResponse(
        id=meeting.id,
        title=meeting.title,
        starts_at=meeting.starts_at,
        ends_at=meeting.ends_at,
        attendees=atts,
        project_id=meeting.project_id,
        mom_raw=meeting.mom_raw,
        mom_message_id=meeting.mom_message_id,
        status=meeting.status,
        created_at=meeting.created_at
    )
    
    items = db.scalars(
        select(ActionItem)
        .where(ActionItem.meeting_id == id)
        .order_by(ActionItem.id.asc())
    ).all()
    
    action_items_resp = [
        ActionItemResponse(
            id=item.id,
            meeting_id=item.meeting_id,
            task_id=item.task_id,
            description=item.description,
            owner_member_id=item.owner_member_id,
            due_date=item.due_date,
            created_at=item.created_at
        )
        for item in items
    ]
    
    return MeetingDetailPageResponse(
        meeting=meeting_resp,
        action_items=action_items_resp,
        entity_slug=f"meeting:{id}"
    )


@router.patch("/{id}", response_model=MeetingResponse)
def update_meeting(id: int, payload: MeetingUpdate, db: Session = Depends(get_manager_db)):
    """
    Modifies scheduled dates, times, or statuses, adapting the pre-meeting brief.
    """
    meeting = db.get(Meeting, id)
    if not meeting:
        raise HTTPException(status_code=404, detail=f"Meeting with ID {id} not found")
        
    if payload.status is not None:
        meeting.status = payload.status
    if payload.ends_at is not None:
        meeting.ends_at = payload.ends_at
        
    if payload.starts_at is not None:
        meeting.starts_at = payload.starts_at
        
        # Reschedule pre-meeting brief job
        from app.scheduler import ScheduledJob
        job = db.scalars(
            select(ScheduledJob)
            .where(ScheduledJob.job_type == f"pre_meeting_brief:{id}")
        ).first()
        
        new_due = payload.starts_at - timedelta(minutes=30)
        if job:
            if new_due > timeservice.now_ist():
                job.next_due_at = new_due
                job.enabled = True
            else:
                job.enabled = False
                
    db.commit()
    db.refresh(meeting)
    
    try:
        atts = json.loads(meeting.attendees)
    except Exception:
        atts = []
        
    return MeetingResponse(
        id=meeting.id,
        title=meeting.title,
        starts_at=meeting.starts_at,
        ends_at=meeting.ends_at,
        attendees=atts,
        project_id=meeting.project_id,
        mom_raw=meeting.mom_raw,
        mom_message_id=meeting.mom_message_id,
        status=meeting.status,
        created_at=meeting.created_at
    )


# -----------------------------------------------------------------------------
# Ingestion & Claim Propagation Pipeline
# -----------------------------------------------------------------------------

@router.post("/{id}/mom", response_model=MeetingDetailPageResponse)
def ingest_mom(id: int, payload: MomRequest, db: Session = Depends(get_manager_db)):
    """
    POSTs minutes of meeting text, runs structured LLM extraction, seeds action items
    as real tasks, DMs owners, and propagates decisions into relevant project timelines & claims.
    """
    meeting = db.get(Meeting, id)
    if not meeting:
        raise HTTPException(status_code=404, detail=f"Meeting with ID {id} not found")
        
    # 1. Update Meeting RAW
    meeting.mom_raw = payload.text
    
    # 2. Resolve Manager identity for Raw Ingestion Message
    manager = db.scalars(
        select(TeamMember).where(TeamMember.role.ilike("%manager%"))
    ).first()
    manager_id = manager.id if manager else "Shivam"
    manager_name = manager.name if manager else "Shivam"
    
    # 3. Insert mock inbound UnifiedMessage to establish evidence citation trail
    msg_id = f"mom_msg_{meeting.id}_{uuid_str()[:8]}"
    dm_channel = get_dm_channel_id("U_HARRY", manager_id)
    
    unified_msg = UnifiedMessage(
        platform_msg_id=msg_id,
        source="manual",
        direction="inbound",
        sender_raw_id=manager_id,
        sender_mapped_name=manager_name,
        channel_raw_id=dm_channel,
        content=payload.text,
        timestamp=timeservice.now_ist()
    )
    db.add(unified_msg)
    db.commit()
    db.refresh(unified_msg)
    
    meeting.mom_message_id = unified_msg.id
    
    # 4. FLASH_MODEL Ingest & Extraction
    client = get_client()

    # Deterministic context: give the model the REAL team roster and project
    # slugs so it maps names -> IDs instead of guessing (e.g. "Alice" vs
    # "U_ALICE"). Without this the owner/slug resolution below silently drops
    # every action item.
    roster_members = db.scalars(select(TeamMember)).all()
    roster_str = "\n".join(
        f"- {m.id} | {m.name} | {m.role}" for m in roster_members
    ) or "None"
    project_entities = db.scalars(
        select(Entity).where(Entity.type == "project")
    ).all()
    projects_str = "\n".join(
        f"- {e.slug} | {e.name}" for e in project_entities
    ) or "None"

    prompt = f"""You are analyzing the Minutes of Meeting (MoM) for the meeting '{meeting.title}'.
Extract structured information in strict JSON format.

MoM Text:
\"\"\"
{payload.text}
\"\"\"

### TEAM ROSTER (owner_member_id MUST be one of these exact IDs):
{roster_str}

### ACTIVE PROJECTS (project_slug MUST be one of these exact slugs, or null):
{projects_str}

Rules:
- owner_member_id MUST be copied exactly from the TEAM ROSTER ID column. Match
  people by name; never invent an ID. If no roster member matches, skip the item.
- project_slug / project_slugs MUST come from the ACTIVE PROJECTS list, or null.

Output in JSON matching the exact schema:
{{
  "summary": "one sentence meeting summary",
  "decisions": [
    "each decision or approval made"
  ],
  "action_items": [
    {{
      "description": "action item task details",
      "owner_member_id": "exact ID from the roster",
      "due_date": "YYYY-MM-DD or null",
      "project_slug": "exact slug from the projects list or null"
    }}
  ],
  "project_slugs": [
    "exact slug from the projects list of any projects affected"
  ]
}}
"""
    try:
        # Extract via JSON schema structure
        llm_response = client.chat(
            model=FLASH_MODEL,
            messages=[{"role": "user", "content": prompt}],
            json_mode=True
        )
        extracted = json.loads(llm_response["content"])
    except Exception as e:
        logger.exception("Error calling LLM during MoM ingestion, falling back gracefully.")
        extracted = {"summary": "MoM Ingest completed.", "decisions": [], "action_items": [], "project_slugs": []}

    # 5. Extraction validations & mappings
    # Map valid project slugs
    valid_project_slugs = Set()
    for slug in extracted.get("project_slugs", []):
        ent = db.scalars(select(Entity).where(Entity.slug == slug)).first()
        if ent:
            valid_project_slugs.add(slug)
            
    # Save Action Items & real Tasks
    for item in extracted.get("action_items", []):
        owner_id = item.get("owner_member_id")
        desc = item.get("description")

        # Verify real assignee. Prefer exact ID, but fall back to name /
        # slack_handle so a stray "Alice" still resolves to "U_ALICE".
        assignee = db.get(TeamMember, owner_id) if owner_id else None
        if not assignee and owner_id:
            assignee = db.scalars(
                select(TeamMember).where(
                    (func.lower(TeamMember.name) == owner_id.lower()) |
                    (TeamMember.slack_handle == owner_id)
                )
            ).first()
        if not assignee or not desc:
            continue
        owner_id = assignee.id
            
        # Parse due date
        due_val = None
        due_str = item.get("due_date")
        if due_str:
            try:
                due_val = datetime.strptime(due_str, "%Y-%m-%d").date()
            except ValueError:
                pass
                
        # Resolve Project ID (checking item project slug or meeting project id)
        item_slug = item.get("project_slug")
        project_id = None
        
        if item_slug:
            ent = db.scalars(select(Entity).where(Entity.slug == item_slug)).first()
            if ent and ent.type == "project" and ent.ref_id:
                project_id = int(ent.ref_id)
                valid_project_slugs.add(item_slug)
                
        if not project_id:
            project_id = meeting.project_id
            
        # We need a project_id to create a Task
        if not project_id:
            continue
            
        # Create Task
        task = Task(
            project_id=project_id,
            title=desc,
            assignee_id=owner_id,
            status="pending",
            due_date=due_val
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        
        # Save Action Item
        action_item = ActionItem(
            meeting_id=meeting.id,
            task_id=task.id,
            description=desc,
            owner_member_id=owner_id,
            due_date=due_val
        )
        db.add(action_item)
        db.commit()
        
        # Harry Slack DM to Owner
        due_suffix = f" (due {due_val})" if due_val else ""
        dm_text = f"From meeting '{meeting.title}': {desc}{due_suffix}"
        member_dm = get_dm_channel_id("U_HARRY", owner_id)
        send_or_hold("slack", {"channel": member_dm, "text": dm_text}, db)

    # 6. Propagation on Project KBs
    decisions = extracted.get("decisions", [])
    
    # Timeline entry on meeting entity
    meet_entity = db.scalars(select(Entity).where(Entity.slug == f"meeting:{meeting.id}")).first()
    if meet_entity:
        meet_entity.compiled_truth = extracted.get("summary", "MoM parsed.")
        meet_entity.truth_updated_at = timeservice.now_ist()
        
        # Add summary node
        t_entry = TimelineEntry(
            entity_id=meet_entity.id,
            happened_at=timeservice.now_ist(),
            summary=f"Minutes of Meeting uploaded",
            detail=extracted.get("summary", "Decisions and actions successfully propagated."),
            source_message_id=unified_msg.id
        )
        db.add(t_entry)
        
    for p_slug in valid_project_slugs:
        p_ent = db.scalars(select(Entity).where(Entity.slug == p_slug)).first()
        if not p_ent:
            continue
            
        p_ent.truth_updated_at = None # Mark dirty for dream cycle re-synthesis
        
        # Propagate timelines and claims per decision
        for dec in decisions:
            # Timeline entry
            t_entry = TimelineEntry(
                entity_id=p_ent.id,
                happened_at=timeservice.now_ist(),
                summary=f"Decided in meeting '{meeting.title}'",
                detail=dec,
                source_message_id=unified_msg.id
            )
            db.add(t_entry)
            db.commit()
            db.refresh(t_entry)
            
            # Attributed claim
            claim = AttributedClaim(
                entity_id=p_ent.id,
                claim=f"{manager_name} decided: {dec}",
                kind="fact",
                holder=manager_id,
                weight=1.0,
                source_message_id=unified_msg.id,
                claimed_at=timeservice.now_ist(),
                active=True
            )
            db.add(claim)
            
    # 7. Complete meeting
    meeting.status = "completed"
    db.commit()
    
    return get_meeting_detail(id, db)


# -----------------------------------------------------------------------------
# Pre-Meeting Briefing Scheduler Handler
# -----------------------------------------------------------------------------

def pre_meeting_brief_handler(db: Session, job_type: str) -> None:
    """
    Scheduler cron execution handler for 'pre_meeting_brief:<id>'.
    Compiles attendee task balances, open followups, and project status
    and DMs Shivam with a beautifully compiled prep summary.
    """
    meeting_id_str = job_type.split(":")[1] if ":" in job_type else None
    if not meeting_id_str:
        logger.warning(f"Invalid pre_meeting_brief job_type format: {job_type}")
        return
        
    try:
        meeting_id = int(meeting_id_str)
    except ValueError:
        logger.warning(f"Could not parse meeting_id from {job_type}")
        return
        
    meeting = db.get(Meeting, meeting_id)
    if not meeting:
        logger.warning(f"Meeting {meeting_id} not found in DB.")
        return
        
    logger.info(f"Generating Pre-Meeting Briefing for meeting: {meeting.title}")
    
    # 1. Assemble context metrics
    try:
        attendees_list = json.loads(meeting.attendees)
    except Exception:
        attendees_list = []
        
    # Project status context
    project_context = "No linked project."
    project_slug = None
    if meeting.project_id:
        p = db.get(Project, meeting.project_id)
        if p:
            project_slug = f"project:{slugify(p.name)}"
            p_ent = db.scalars(select(Entity).where(Entity.slug == project_slug)).first()
            p_truth = p_ent.compiled_truth if p_ent else "No compiled truths."
            project_context = f"Project: {p.name} (Health: {p.health.upper()}).\nTruth: {p_truth}"

    # Attendees statistics
    attendee_lines = []
    for m_id in attendees_list:
        member = db.get(TeamMember, m_id)
        if not member:
            continue
            
        # Overdue / open tasks
        tasks = db.scalars(
            select(Task)
            .where((Task.assignee_id == m_id) & (Task.status.in_(["in_progress", "pending", "blocked"])))
        ).all()
        
        task_summaries = [f"- {t.title} ({t.status})" for t in tasks]
        task_block = "\n".join(task_summaries) if task_summaries else "No active tasks."
        
        # Followups pending
        from app.followups import Followup
        fups = db.scalars(
            select(Followup)
            .where((Followup.target_member_id == m_id) & (Followup.status == "open"))
        ).all()
        
        fup_summaries = [f"- Question: {f.question}" for f in fups]
        fup_block = "\n".join(fup_summaries) if fup_summaries else "No pending follow-ups."
        
        attendee_lines.append(f"""Attendee: {member.name} (ID: {m_id}, Role: {member.role})
Active Tasks:
{task_block}
Pending Follow-ups:
{fup_block}""")

    attendees_context = "\n\n".join(attendee_lines) if attendee_lines else "No attendee stats compiled."
    
    # 2. Call SMART_MODEL for beautiful prose briefing
    client = get_client()
    
    prompt = f"""You are preparing a Pre-Meeting Briefing for Shivam (General Manager) before the meeting '{meeting.title}'.
This meeting starts at {meeting.starts_at.strftime('%Y-%m-%d %H:%M')} IST.

Context:
=== LINKED PROJECT STATUS ===
{project_context}

=== ATTENDEES DOSSIER ===
{attendees_context}

Write a brief, punchy, and professional executive brief (bold and bullets only) that highlights what Shivam should watch out for:
1. Are there any blocked or overdue tasks belonging to the attendees?
2. Are there any outstanding developer followups or pings that remain unanswered?
3. What is the current health of the linked project?
"""
    
    fallback_brief = f"""**Pre-Meeting Brief: {meeting.title}**

*   **Project context**: {project_context}
*   **Attendees compiled**: {len(attendees_list)} participant(s). See workload dashboard for live details.
"""
    
    try:
        res = client.chat(
            model=SMART_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )
        brief_text = res["content"] or fallback_brief
    except Exception as e:
        logger.exception("Pre-meeting brief LLM synthesis failed. Falling back.")
        brief_text = fallback_brief

    # 3. Save as timeline entry on the meeting entity page
    meet_ent = db.scalars(select(Entity).where(Entity.slug == f"meeting:{meeting.id}")).first()
    if meet_ent:
        t_entry = TimelineEntry(
            entity_id=meet_ent.id,
            happened_at=timeservice.now_ist(),
            summary="Pre-meeting briefing compiled",
            detail=brief_text
        )
        db.add(t_entry)
        db.commit()
        
    # 4. DM the Manager via send_or_hold
    manager = db.scalars(select(TeamMember).where(TeamMember.role.ilike("%manager%"))).first()
    if manager:
        manager_dm = get_dm_channel_id("U_HARRY", manager.id)
        send_or_hold("slack", {"channel": manager_dm, "text": brief_text}, db)


# -----------------------------------------------------------------------------
# HELPER UTILITIES
# -----------------------------------------------------------------------------

def uuid_str() -> str:
    import uuid
    return str(uuid.uuid4())

def Set():
    return set()
