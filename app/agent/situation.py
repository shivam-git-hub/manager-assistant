from datetime import datetime, timedelta
from sqlalchemy import select, and_, or_
from sqlalchemy.orm import Session

from app import timeservice
from app.database import TeamMember, Project, Task, UnifiedMessage
from app.kb.models import Entity, AttributedClaim, Conflict
from app.followups import Followup
from app.agent.notes import recent_notes

def build_situation(db: Session) -> dict:
    now = timeservice.now_ist()
    today = now.date()

    # Pre-fetch helper maps to avoid N+1 queries
    entities = {e.id: e for e in db.scalars(select(Entity)).all()}
    projects = {p.id: p for p in db.scalars(select(Project)).all()}
    members = {m.id: m for m in db.scalars(select(TeamMember)).all()}

    # 1. Unmet commitments / blockers
    unmet_claims = []
    # Fetch active commitments/blockers
    stmt = select(AttributedClaim).where(
        and_(
            AttributedClaim.active == True,
            AttributedClaim.superseded_by == None,
            AttributedClaim.kind.in_(["commitment", "blocker"])
        )
    )
    active_claims = db.scalars(stmt).all()
    for claim in active_claims:
        if now - claim.claimed_at > timedelta(hours=24):
            # Check for any inbound message from holder since claimed_at
            msg_stmt = select(UnifiedMessage).where(
                and_(
                    UnifiedMessage.sender_raw_id == claim.holder,
                    UnifiedMessage.direction == "inbound",
                    UnifiedMessage.timestamp > claim.claimed_at
                )
            )
            has_new_msg = db.scalars(msg_stmt).first() is not None
            if not has_new_msg:
                ent = entities.get(claim.entity_id)
                ent_slug = ent.slug if ent else "unknown"
                unmet_claims.append({
                    "id": claim.id,
                    "holder": claim.holder,
                    "holder_name": members[claim.holder].name if claim.holder in members else claim.holder,
                    "claim": claim.claim,
                    "kind": claim.kind,
                    "claimed_at": claim.claimed_at,
                    "entity_slug": ent_slug
                })

    # 2. Conflicts needing a nudge
    unnudged_conflicts = []
    conflicts_stmt = select(Conflict).where(Conflict.status == "open")
    open_conflicts = db.scalars(conflicts_stmt).all()
    for conflict in open_conflicts:
        claim_a = db.get(AttributedClaim, conflict.claim_a_id)
        claim_b = db.get(AttributedClaim, conflict.claim_b_id)
        ent = entities.get(conflict.entity_id)
        if claim_a and claim_b and ent:
            holders = [claim_a.holder, claim_b.holder]
            # Check if there is an open followup for this entity referencing either holder
            f_stmt = select(Followup).where(
                and_(
                    Followup.status == "open",
                    Followup.entity_slug == ent.slug,
                    Followup.target_member_id.in_(holders)
                )
            )
            followup_exists = db.scalars(f_stmt).first() is not None
            if not followup_exists:
                unnudged_conflicts.append({
                    "id": conflict.id,
                    "description": conflict.description,
                    "entity_slug": ent.slug,
                    "claim_a_holder": claim_a.holder,
                    "claim_b_holder": claim_b.holder,
                    "claim_a_holder_name": members[claim_a.holder].name if claim_a.holder in members else claim_a.holder,
                    "claim_b_holder_name": members[claim_b.holder].name if claim_b.holder in members else claim_b.holder,
                    "severity": conflict.severity
                })

    # 3. Degraded projects
    degraded_projects = []
    for p in projects.values():
        if p.health in ["yellow", "red"]:
            degraded_projects.append({
                "id": p.id,
                "name": p.name,
                "health": p.health,
                "reasons": p.health_reasons or "No reason specified"
            })

    # 4. Overdue / blocked tasks
    unresolved_tasks = []
    tasks_stmt = select(Task).where(
        and_(
            Task.status != "completed",
            or_(
                Task.status == "blocked",
                and_(Task.due_date != None, Task.due_date < today)
            )
        )
    )
    overdue_blocked_tasks = db.scalars(tasks_stmt).all()
    for t in overdue_blocked_tasks:
        proj = projects.get(t.project_id)
        proj_name = proj.name if proj else "Unknown Project"
        assignee_name = members[t.assignee_id].name if t.assignee_id in members else (t.assignee_id or "Unassigned")
        unresolved_tasks.append({
            "id": t.id,
            "title": t.title,
            "status": t.status,
            "blockage_reason": t.blockage_reason,
            "due_date": t.due_date,
            "project_name": proj_name,
            "assignee_id": t.assignee_id,
            "assignee_name": assignee_name
        })

    # 5. Gone-quiet owners
    quiet_owners = []
    for m in members.values():
        if m.id == "U_HARRY":
            continue
        # Check if they have at least one active commitment
        cmt_stmt = select(AttributedClaim).where(
            and_(
                AttributedClaim.active == True,
                AttributedClaim.superseded_by == None,
                AttributedClaim.kind == "commitment",
                AttributedClaim.holder == m.id
            )
        )
        has_commitment = db.scalars(cmt_stmt).first() is not None
        if has_commitment:
            # Check last inbound message
            msg_stmt = select(UnifiedMessage.timestamp).where(
                and_(
                    UnifiedMessage.sender_raw_id == m.id,
                    UnifiedMessage.direction == "inbound"
                )
            ).order_by(UnifiedMessage.timestamp.desc())
            last_msg_ts = db.scalars(msg_stmt).first()
            if not last_msg_ts or now - last_msg_ts > timedelta(days=2):
                quiet_owners.append({
                    "id": m.id,
                    "name": m.name,
                    "last_msg_at": last_msg_ts
                })

    # 6. Already-open follow-ups (for de-duplication)
    open_followups = []
    f_stmt = select(Followup).where(Followup.status == "open")
    active_followups = db.scalars(f_stmt).all()
    for f in active_followups:
        open_followups.append({
            "id": f.id,
            "target_member_id": f.target_member_id,
            "target_name": members[f.target_member_id].name if f.target_member_id in members else f.target_member_id,
            "question": f.question,
            "entity_slug": f.entity_slug,
            "task_id": f.task_id,
            "ping_count": f.ping_count,
            "last_ping_at": f.last_ping_at
        })

    # 7. Recent memory notes
    notes = recent_notes(db, limit=30)
    recent_notes_list = []
    for n in notes:
        recent_notes_list.append({
            "id": n.id,
            "created_at": n.created_at,
            "kind": n.kind,
            "subject_ref": n.subject_ref,
            "content": n.content
        })

    # Calculate has_signals
    has_signals = (
        len(unmet_claims) > 0 or
        len(unnudged_conflicts) > 0 or
        len(degraded_projects) > 0 or
        len(unresolved_tasks) > 0 or
        len(quiet_owners) > 0
    )

    # Compile Situation Digest string
    digest_lines = []
    digest_lines.append(f"Simulated Now: {now.strftime('%Y-%m-%d %H:%M:%S')} IST")
    digest_lines.append("")

    if unmet_claims:
        digest_lines.append("### UNMET COMMITMENTS / BLOCKERS:")
        for c in unmet_claims:
            digest_lines.append(f"- {c['holder_name']} ({c['holder']}) has a {c['kind']} on {c['entity_slug']} since {c['claimed_at'].strftime('%Y-%m-%d %H:%M')}: \"{c['claim']}\" (no messages sent since then)")
        digest_lines.append("")

    if unnudged_conflicts:
        digest_lines.append("### OPEN CONFLICTS NEEDING NUDGES:")
        for c in unnudged_conflicts:
            digest_lines.append(f"- Conflict #{c['id']} ({c['severity']} severity) on {c['entity_slug']}: {c['description']} (Between {c['claim_a_holder_name']} and {c['claim_b_holder_name']})")
        digest_lines.append("")

    if degraded_projects:
        digest_lines.append("### DEGRADED PROJECTS:")
        for p in degraded_projects:
            digest_lines.append(f"- Project '{p['name']}' is '{p['health']}'. Reason: {p['reasons']}")
        digest_lines.append("")

    if unresolved_tasks:
        digest_lines.append("### OVERDUE OR BLOCKED TASKS:")
        for t in unresolved_tasks:
            due_str = t['due_date'].strftime('%Y-%m-%d') if t['due_date'] else 'No due date'
            reason_str = f" (Blocked reason: {t['blockage_reason']})" if t['status'] == "blocked" else ""
            digest_lines.append(f"- Task #{t['id']} '{t['title']}' in {t['project_name']} assigned to {t['assignee_name']} is {t['status'].upper()}{reason_str}. Due: {due_str}")
        digest_lines.append("")

    if quiet_owners:
        digest_lines.append("### SILENT TEAM MEMBERS (WITH COMMITMENTS):")
        for q in quiet_owners:
            last_str = q['last_msg_at'].strftime('%Y-%m-%d %H:%M') if q['last_msg_at'] else 'Never sent message'
            digest_lines.append(f"- {q['name']} ({q['id']}) has open commitments but was last active at: {last_str}")
        digest_lines.append("")

    if open_followups:
        digest_lines.append("### CURRENT OPEN FOLLOW-UPS (DO NOT DUPLICATE):")
        for f in open_followups:
            ping_str = f" ({f['ping_count']} pings sent)" if f['ping_count'] > 0 else " (Not pinged yet)"
            digest_lines.append(f"- Follow-up #{f['id']} to {f['target_name']} ({f['target_member_id']}) on {f['entity_slug'] or 'global'}: \"{f['question']}\"{ping_str}")
        digest_lines.append("")

    if recent_notes_list:
        digest_lines.append("### RECENT MEMORY NOTES:")
        for n in recent_notes_list[:10]: # Render top 10 compactly in digest
            ref_str = f" [{n['subject_ref']}]" if n['subject_ref'] else ""
            digest_lines.append(f"- {n['created_at'].strftime('%Y-%m-%d %H:%M')} [{n['kind']}] {ref_str}: {n['content']}")
        digest_lines.append("")

    if not has_signals:
        digest_lines.append("No actionable signals found.")

    digest_str = "\n".join(digest_lines)

    return {
        "has_signals": has_signals,
        "digest": digest_str,
        "unmet_claims": unmet_claims,
        "unnudged_conflicts": unnudged_conflicts,
        "degraded_projects": degraded_projects,
        "unresolved_tasks": unresolved_tasks,
        "quiet_owners": quiet_owners,
        "open_followups": open_followups,
        "recent_notes": recent_notes_list
    }
