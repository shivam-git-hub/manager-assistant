from datetime import datetime
from sqlalchemy import select, and_, func
from sqlalchemy.orm import Session

from app.database import TeamMember, Project
from app.kb.models import Conflict
from app import timeservice

STABLE_PROMPT = """You are Harry, a professional, highly efficient AI Project Management Assistant built specifically to assist the manager, Shivam. 

Your core personality traits:
- Helpful, blunt, structured, and extremely concise.
- Relies STRICTLY on evidence. Never invent any factual details or timeline events.
- Cites source evidence: Whenever you state a fact or progress status retrieved from the knowledge base, you MUST append an inline bracketed citation referencing the matching timeline ID, e.g., '[T12]' if the timeline entry has ID 12.
- Respects limits: Always ask the manager for confirmation before performing irreversible modifications (such as using update_task to reassign or complete tasks), unless they have already explicitly instructed you to do so.
- Conflict awareness: Never try to resolve claim conflicts or contradictions yourself. If a conflict is discovered, surface it clearly to Shivam so he can make an executive decision.

Tool Usage Rules:
- You have access to tools to read/write from the database and queue outbound Slack/email messages.
- Always perform a 'kb_search' or read specific entities using 'get_entity' before answering any factual status questions.
- Enforce the working-hours gate: Understand that sending messages or emails outside working hours (09:00 - 19:00 IST) or on weekends will place them in a held state.
"""

def get_context_prompt(db: Session) -> str:
    """
    Compiles the Team Roster and Project metadata directly from the DB.
    """
    members = db.scalars(select(TeamMember).order_by(TeamMember.id.asc())).all()
    projects = db.scalars(select(Project).order_by(Project.id.asc())).all()
    
    roster_lines = []
    for m in members:
        slack_part = f"slack: {m.slack_handle}" if m.slack_handle else "no slack"
        email_part = f"email: {m.outlook_email}" if m.outlook_email else "no email"
        roster_lines.append(f"- ID: {m.id} | Name: {m.name} | Role: {m.role} ({slack_part}, {email_part})")
        
    project_lines = []
    for p in projects:
        project_lines.append(f"- ID: {p.id} | Name: {p.name} | Health: {p.health} | Status: {p.status}")
        
    roster_str = "\n".join(roster_lines) if roster_lines else "No team members found."
    project_str = "\n".join(project_lines) if project_lines else "No projects found."
    
    return f"""### ACTIVE TEAM ROSTER:
{roster_str}

### ACTIVE PROJECTS:
{project_str}"""

def get_volatile_prompt(db: Session) -> str:
    """
    Assembles volatile session-specific lines: current sim time, open conflict counts, degraded projects.
    """
    now_str = timeservice.now_ist().strftime("%Y-%m-%d %H:%M:%S")
    
    # Count open conflicts
    open_conflicts_count = db.scalar(
        select(func.count(Conflict.id)).where(Conflict.status == "open")
    ) or 0
    
    # Fetch degraded projects (yellow or red health)
    degraded_projects = db.scalars(
        select(Project).where(Project.health.in_(["yellow", "red"]))
    ).all()
    
    degraded_lines = []
    for p in degraded_projects:
        degraded_lines.append(f"- Project: {p.name} (Health: {p.health.upper()}). Reasons: {p.health_reasons}")
        
    degraded_str = "\n".join(degraded_lines) if degraded_lines else "No degraded projects."
    
    return f"""### SYSTEM METRICS (VOLATILE):
- Current Simulated Time: {now_str} IST
- Open Claims Conflicts: {open_conflicts_count}
- Degraded Projects:
{degraded_str}"""

def compile_system_prompt(db: Session) -> str:
    """
    Assembles stable, context, and volatile tiers into a unified system prompt.
    """
    stable = STABLE_PROMPT
    context = get_context_prompt(db)
    volatile = get_volatile_prompt(db)
    
    return f"{stable}\n\n{context}\n\n{volatile}"
