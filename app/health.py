import json
import logging
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import select, and_

from app.database import Project, Task, TeamMember
from app.kb.models import Entity, Conflict, TimelineEntry
from app.followups import Followup, get_dm_channel_id
from app import timeservice
from app.kb.models import slugify
from app.outbound import send_or_hold

logger = logging.getLogger(__name__)

def evaluate_project_health(db: Session, project: Project) -> tuple[str, list[str]]:
    """
    Evaluates project health based on tasks, conflicts, timelines, and follow-ups.
    Returns (health_grade, reasons_list).
    """
    now = timeservice.now_ist()
    today = now.date()
    
    score = 0
    reasons = []
    
    # 1. Overdue tasks (+2 each)
    overdue_stmt = select(Task).where(
        (Task.project_id == project.id) &
        (Task.status != "completed") &
        (Task.due_date != None) &
        (Task.due_date < today)
    )
    overdue_tasks = list(db.scalars(overdue_stmt).all())
    if overdue_tasks:
        count = len(overdue_tasks)
        score += 2 * count
        reasons.append(f"{count} task(s) overdue")
        
    # 2. Blocked tasks (+2 each)
    blocked_stmt = select(Task).where(
        (Task.project_id == project.id) & (Task.status == "blocked")
    )
    blocked_tasks = list(db.scalars(blocked_stmt).all())
    if blocked_tasks:
        count = len(blocked_tasks)
        score += 2 * count
        reasons.append(f"{count} task(s) blocked")
        
    # Find project entity
    proj_slug = f"project:{slugify(project.name)}"
    entity = db.scalars(select(Entity).where(Entity.slug == proj_slug)).first()
    
    if entity:
        # 3. Open conflicts on project entity (+3 each)
        conflict_stmt = select(Conflict).where(
            (Conflict.entity_id == entity.id) & (Conflict.status == "open")
        )
        open_conflicts = list(db.scalars(conflict_stmt).all())
        if open_conflicts:
            count = len(open_conflicts)
            score += 3 * count
            reasons.append(f"{count} open conflict(s)")
            
        # 4. No inbound activity on project entity for >3 sim days (+2)
        newest_timeline = db.scalars(
            select(TimelineEntry)
            .where(TimelineEntry.entity_id == entity.id)
            .order_by(TimelineEntry.happened_at.desc())
        ).first()
        
        if not newest_timeline or (now - newest_timeline.happened_at) > timedelta(days=3):
            score += 2
            reasons.append("No inbound activity on the project for >3 days")
            
    # 5. Escalated followups touching project (+2 each)
    task_ids = db.scalars(select(Task.id).where(Task.project_id == project.id)).all()
    if task_ids:
        escalated_stmt = select(Followup).where(
            (Followup.task_id.in_(task_ids)) & (Followup.status == "escalated")
        )
        escalated_followups = list(db.scalars(escalated_stmt).all())
        if escalated_followups:
            count = len(escalated_followups)
            score += 2 * count
            reasons.append(f"{count} escalated follow-up(s) awaiting answers")
            
    # Resolve grade
    if score <= 2:
        grade = "green"
    elif score <= 5:
        grade = "yellow"
    else:
        grade = "red"
        
    if not reasons:
        reasons.append("Project is running smoothly")
        
    return grade, reasons


def run_health_eval(db: Session) -> dict:
    """
    Runs project health evaluation for all active projects, triggering notifications on degrade.
    """
    now = timeservice.now_ist()
    projects = list(db.scalars(select(Project).where(Project.status == "active")).all())
    
    # Find manager
    manager = db.scalars(
        select(TeamMember).where(TeamMember.role.ilike("%manager%"))
    ).first()
    
    evaluated = 0
    degraded = 0
    
    # Helper to check if new grade is a degradation
    # green (1) -> yellow (2) -> red (3)
    grade_weight = {"green": 1, "yellow": 2, "red": 3}
    
    for p in projects:
        old_health = p.health or "green"
        new_health, reasons = evaluate_project_health(db, p)
        
        # Save updates
        p.health = new_health
        p.health_reasons = json.dumps(reasons)
        p.health_updated_at = now
        evaluated += 1
        
        # Check degradation
        if grade_weight[new_health] > grade_weight[old_health]:
            degraded += 1
            if manager:
                manager_dm = get_dm_channel_id("U_HARRY", manager.id)
                reasons_bulleted = "\n".join([f"- {r}" for r in reasons])
                notification_text = (
                    f"Project *{p.name}* health has degraded from *{old_health.upper()}* to *{new_health.upper()}* due to:\n"
                    f"{reasons_bulleted}"
                )
                send_or_hold("slack", {"channel": manager_dm, "text": notification_text}, db)
                
    db.commit()
    return {
        "projects_evaluated": evaluated,
        "degraded": degraded
    }
