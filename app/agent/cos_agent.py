import json
import uuid
import logging
import os
import re
import httpx
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import select, delete

from app import timeservice, config
from app.database import ChatMessage, Workflow, CronJob, FollowupAgent, AgentActionLog
from app.controlplane.models import (
    SessionLocal as ControlPlaneSessionLocal,
    get_employee_by_manager_id,
    get_employee_by_slack_id,
    Project as RegistryProject,
    get_member_list
)
from app.tenancy.db import get_manager_session
from app.agent.runner import AgentSpec, run_spec
from app.agent.gemini_client import get_client, GeminiClient
from app.config import SMART_MODEL, FLASH_MODEL
from app.agent.registry import registry
from app.agent.kb_tools import (
    search_events_handler, get_event_handler, search_claims_handler,
    get_thread_handler, get_project_state_handler, list_projects_handler
)

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Cron & Time parsing utility
# -----------------------------------------------------------------------------

def calculate_next_run(schedule: str, from_time: datetime) -> datetime:
    """
    Parses intervals like "30m", "2h", or crontab-like "0 9 * * *"
    and returns the next run time from from_time (IST).
    """
    schedule = schedule.strip().lower()
    
    # 1. Check for standard crontab like "0 9 * * *" or "30 8 * * *"
    cron_match = re.match(r"^(\d+)\s+(\d+)\s+\*\s+\*\s+\*$", schedule)
    if cron_match:
        minute = int(cron_match.group(1))
        hour = int(cron_match.group(2))
        next_run = from_time.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_run <= from_time:
            next_run += timedelta(days=1)
        return next_run
        
    # 2. Check for minutes interval (e.g. "30m", "every 30m", "30 minutes")
    min_match = re.search(r"(\d+)\s*(m|min|minute)", schedule)
    if min_match:
        mins = int(min_match.group(1))
        return from_time + timedelta(minutes=mins)
        
    # 3. Check for hours interval (e.g. "2h", "every 2h", "2 hours")
    hour_match = re.search(r"(\d+)\s*(h|hour)", schedule)
    if hour_match:
        hours = int(hour_match.group(1))
        return from_time + timedelta(hours=hours)
        
    # Default fallback: 1 hour later
    return from_time + timedelta(hours=1)


# -----------------------------------------------------------------------------
# Compaction Strategy for Chat History
# -----------------------------------------------------------------------------

def get_chat_summary_path(manager_id: str) -> str:
    from app.tenancy.paths import manager_dir
    return os.path.join(manager_dir(manager_id), "chat_summary.json")


def get_compacted_history(db: Session, manager_id: str) -> List[Dict[str, Any]]:
    """
    Retrieves the chat history for the manager.
    If total ChatMessage rows > 20:
      - keeps the last 5 messages intact
      - merges older messages into a running summary via FLASH_MODEL
      - writes summary to managers/<manager_id>/chat_summary.json
      - deletes the older messages from ChatMessage table
    """
    # Load all messages chronologically
    stmt = select(ChatMessage).order_by(ChatMessage.id.asc())
    messages = list(db.scalars(stmt).all())
    
    if len(messages) > 20:
        # Keep last 5, compact the rest
        to_keep = messages[-5:]
        to_compact = messages[:-5]
        
        # Load any existing summary
        summary_path = get_chat_summary_path(manager_id)
        existing_summary = ""
        if os.path.exists(summary_path):
            try:
                with open(summary_path, "r", encoding="utf-8") as f:
                    existing_summary = json.load(f).get("summary", "")
            except Exception:
                pass
                
        # Format text to compact
        new_text_lines = []
        for m in to_compact:
            new_text_lines.append(f"{m.role.upper()}: {m.content}")
        new_text = "\n".join(new_text_lines)
        
        # Build prompt for LLM
        prompt = (
            "You are compacting a chat conversation history between a Manager and their Chief of Staff (COS) Agent.\n"
            f"Existing Summary so far:\n{existing_summary or '(None)'}\n\n"
            f"New messages to merge into summary:\n{new_text}\n\n"
            "Produce an updated, concise, yet highly informative summary containing key facts, user preferences, "
            "ongoing tasks, preparation schedules, and important followups. Be direct and avoid conversational fluff."
        )
        
        client = get_client()
        try:
            resp = client.chat(
                model=FLASH_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                json_mode=False
            )
            new_summary = resp.get("content") or existing_summary
            
            # Save new summary
            os.makedirs(os.path.dirname(summary_path), exist_ok=True)
            with open(summary_path, "w", encoding="utf-8") as f:
                json.dump({"summary": new_summary}, f)
                
            # Delete compacted messages
            keep_ids = {m.id for m in to_keep}
            for m in to_compact:
                if m.id not in keep_ids:
                    db.delete(m)
            db.commit()
            
            # Use kept messages
            messages = to_keep
        except Exception as e:
            logger.error(f"Failed to compact chat history for manager={manager_id}: {e}", exc_info=True)
            
    # Re-assemble history
    formatted = []
    # 1. Prepend summary if exists
    summary_path = get_chat_summary_path(manager_id)
    if os.path.exists(summary_path):
        try:
            with open(summary_path, "r", encoding="utf-8") as f:
                summary = json.load(f).get("summary", "")
                if summary:
                    formatted.append({
                        "role": "system",
                        "content": f"[Conversation Summary so far: {summary}]"
                    })
        except Exception:
            pass
            
    # 2. Append uncompacted messages
    for m in messages:
        formatted.append({
            "role": m.role,
            "content": m.content
        })
        
    return formatted


# -----------------------------------------------------------------------------
# System Prompt Context Compiler for Chief of Staff (COS) Agent
# -----------------------------------------------------------------------------

def compile_cos_system_prompt(db: Session, manager_id: str) -> str:
    """
    Compiles the dynamic, highly contextual system prompt for the Chief of Staff.
    Includes user profile, persistent memory, project list, workflows, and followup statuses.
    """
    from app.tenancy.paths import manager_dir, manager_memory_md_path
    
    # 1. Load user profile (user.md)
    user_md_path = os.path.join(manager_dir(manager_id), "user.md")
    user_profile = "(No user profile information configured. Please write user.md if user preferences are set.)"
    if os.path.exists(user_md_path):
        try:
            with open(user_md_path, "r", encoding="utf-8") as f:
                user_profile = f.read().strip() or user_profile
        except Exception:
            pass
            
    # 2. Load persistent memory (memory.md)
    memory_md_path = manager_memory_md_path(manager_id)
    persistent_memory = "(Empty persistent memory.)"
    if os.path.exists(memory_md_path):
        try:
            with open(memory_md_path, "r", encoding="utf-8") as f:
                persistent_memory = f.read().strip() or persistent_memory
        except Exception:
            pass
            
    # 3. Fetch active projects from control plane
    projects_info = []
    cp_db = ControlPlaneSessionLocal()
    try:
        owned = cp_db.query(RegistryProject).filter(RegistryProject.manager_user_id == manager_id).all()
        owned_ids = {p.id for p in owned}
        all_team_projects = cp_db.query(RegistryProject).filter(RegistryProject.kind == "team").all()
        member_projects = [
            p for p in all_team_projects
            if p.id not in owned_ids and any(m["employee_id"] == manager_id for m in get_member_list(p))
        ]
        all_projects = owned + member_projects
        for p in all_projects:
            members = [m["employee_id"] for m in get_member_list(p)]
            projects_info.append(
                f"- Project ID: {p.id}\n"
                f"  Name: {p.name}\n"
                f"  Description: {p.description or 'No description'}\n"
                f"  Role: {'Owner/Manager' if p.manager_user_id == manager_id else 'Member'}\n"
                f"  Members: {', '.join(members)}"
            )
    finally:
        cp_db.close()
        
    projects_context = "\n".join(projects_info) if projects_info else "No active projects listed in registry."
    
    # 4. Fetch active workflows
    workflows = db.scalars(select(Workflow)).all()
    workflows_info = []
    for wf in workflows:
        workflows_info.append(
            f"- [{wf.status.upper()}] Workflow #{wf.id}: {wf.name} ({wf.task_type})\n"
            f"  Cron: {wf.cron_expression}\n"
            f"  Prompt/Instructions: {wf.description}"
        )
    workflows_context = "\n".join(workflows_info) if workflows_info else "No workflows registered."
    
    # 5. Fetch active cron jobs
    crons = db.scalars(select(CronJob).where(CronJob.status == "pending")).all()
    crons_info = []
    for c in crons:
        crons_info.append(
            f"- Cron #{c.id}: Task={c.task_type}, Schedule={c.schedule}, Next Run={c.next_run_at}"
        )
    crons_context = "\n".join(crons_info) if crons_info else "No pending crons scheduled."
    
    # 6. Fetch active followup chat agents
    followups = db.scalars(select(FollowupAgent)).all()
    followups_info = []
    for f in followups:
        followups_info.append(
            f"- Followup #{f.id} to Employee {f.recipient_employee_id} [{f.status.upper()}]\n"
            f"  Scope Projects: {f.scoped_project_ids}\n"
            f"  Instructions: {f.instructions}\n"
            f"  Latest Report/Context: {f.cos_context or 'None yet'}"
        )
    followups_context = "\n".join(followups_info) if followups_info else "No followup chat agents active."

    system_prompt = (
        "You are the Chief of Staff (COS) Agent for the Manager. You are their trusted, highly professional personal assistant.\n"
        "Your goal is to organize their knowledge base, answer queries, manage crons/workflows, and coordinate followup tasks.\n\n"
        "=================== MANAGER PROFILE (user.md) ===================\n"
        f"{user_profile}\n\n"
        "=================== PERSISTENT LONG-TERM MEMORY (memory.md) ===================\n"
        f"{persistent_memory}\n\n"
        "=================== ACTIVE PROJECTS CONTEXT ===================\n"
        f"{projects_context}\n\n"
        "=================== ACTIVE SYSTEM WORKFLOWS ===================\n"
        f"{workflows_context}\n\n"
        "=================== SCHEDULED CRON JOBS ===================\n"
        f"{crons_context}\n\n"
        "=================== DELEGATED FOLLOWUP CHAT AGENTS ===================\n"
        f"{followups_context}\n\n"
        "=================== CHIEF OF STAFF COMMAND DIRECTIVES ===================\n"
        "1. You only talk directly to the Manager. You NEVER directly contact anyone else in the organization yourself.\n"
        "2. If you need to follow up with other team members or request updates, you MUST use the `spawn_followup_chat_agent` tool to delegate that task to a lightweight, dedicated, asynchronous Chat Agent.\n"
        "3. You are a conversational agent. Provide direct answers to the user's queries. You don't need a send tool to talk back; simply output your thoughts and text response, and it will be delivered directly to the active Slack DM or Portal Chat window.\n"
        "4. You have READ-ONLY access to the Knowledge Base. You cannot write directly to it. If you need to make updates, trigger the Knowledge Synthesis Agent using the `trigger_knowledge_synthesis_agent` tool.\n"
        "5. Keep `user.md` and `memory.md` current using the memory tools when the user specifies a preference, habit, schedule, or correction. Maintain this long-term memory proactively."
    )
    return system_prompt


# -----------------------------------------------------------------------------
# Web Search Tool using Brave Search
# -----------------------------------------------------------------------------

def web_search_handler(db: Session, manager_id: str, run_context: Dict[str, Any], query: str) -> Dict[str, Any]:
    api_key = os.getenv("BRAVE_SEARCH_API_KEY")
    if not api_key:
        return {"error": "Brave Search Web Tool is disabled. BRAVE_SEARCH_API_KEY environment variable is not configured."}
        
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {
        "X-Subscription-Token": api_key,
        "Accept": "application/json"
    }
    params = {
        "q": query,
        "count": 5
    }
    try:
        resp = httpx.get(url, headers=headers, params=params, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        results = []
        for r in data.get("web", {}).get("results", []):
            results.append({
                "title": r.get("title"),
                "description": r.get("description"),
                "url": r.get("url")
            })
        return {"query": query, "results": results}
    except Exception as e:
        return {"error": f"Brave Search Web Tool call failed: {str(e)}"}


# Register Brave Search tool in shared registry
registry.register(
    name="web_search",
    schema={
        "name": "web_search",
        "description": "Searches the web for real-time information or lookups using the Brave Search engine (if enabled).",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query to look up on the web."}
            },
            "required": ["query"]
        }
    },
    handler=web_search_handler
)


# -----------------------------------------------------------------------------
# Memory Management Tools
# -----------------------------------------------------------------------------

def read_memory_handler(db: Session, manager_id: str, run_context: Dict[str, Any]) -> Dict[str, Any]:
    from app.tenancy.paths import manager_dir, manager_memory_md_path
    user_md_path = os.path.join(manager_dir(manager_id), "user.md")
    memory_md_path = manager_memory_md_path(manager_id)
    
    user_profile = ""
    if os.path.exists(user_md_path):
        try:
            with open(user_md_path, "r", encoding="utf-8") as f:
                user_profile = f.read()
        except Exception:
            pass
            
    persistent_memory = ""
    if os.path.exists(memory_md_path):
        try:
            with open(memory_md_path, "r", encoding="utf-8") as f:
                persistent_memory = f.read()
        except Exception:
            pass
            
    return {
        "user_profile_user_md": user_profile,
        "persistent_memory_memory_md": persistent_memory
    }


def update_memory_handler(
    db: Session,
    manager_id: str,
    run_context: Dict[str, Any],
    user_profile: Optional[str] = None,
    persistent_memory: Optional[str] = None
) -> Dict[str, Any]:
    from app.tenancy.paths import manager_dir, manager_memory_md_path
    user_md_path = os.path.join(manager_dir(manager_id), "user.md")
    memory_md_path = manager_memory_md_path(manager_id)
    
    updates = {}
    if user_profile is not None:
        try:
            os.makedirs(os.path.dirname(user_md_path), exist_ok=True)
            with open(user_md_path, "w", encoding="utf-8") as f:
                f.write(user_profile.strip())
            updates["user_profile"] = "updated successfully"
        except Exception as e:
            updates["user_profile"] = f"failed to update: {e}"
            
    if persistent_memory is not None:
        try:
            os.makedirs(os.path.dirname(memory_md_path), exist_ok=True)
            with open(memory_md_path, "w", encoding="utf-8") as f:
                f.write(persistent_memory.strip())
            updates["persistent_memory"] = "updated successfully"
        except Exception as e:
            updates["persistent_memory"] = f"failed to update: {e}"
            
    return {"status": "completed", "updates": updates}


registry.register(
    name="read_memory",
    schema={
        "name": "read_memory",
        "description": "Reads the manager's durable profile (user.md) and long-run persistent memory (memory.md) files.",
        "parameters": {"type": "object", "properties": {}}
    },
    handler=read_memory_handler
)

registry.register(
    name="update_memory",
    schema={
        "name": "update_memory",
        "description": "Updates or overrides the manager's durable profile (user.md) and long-run persistent memory (memory.md) files.",
        "parameters": {
            "type": "object",
            "properties": {
                "user_profile": {"type": "string", "description": "Full new content for user.md (user profile, preferences, UPSC prep details). Pass only if updating."},
                "persistent_memory": {"type": "string", "description": "Full new content for memory.md (long-term historical memory). Pass only if updating."}
            }
        }
    },
    handler=update_memory_handler
)


# -----------------------------------------------------------------------------
# Trigger Knowledge Synthesis Agent Tool
# -----------------------------------------------------------------------------

def trigger_knowledge_synthesis_agent_handler(
    db: Session,
    manager_id: str,
    run_context: Dict[str, Any],
    query: str,
    allow_writes: bool = True
) -> Dict[str, Any]:
    """
    Invokes the active synthesis agent to perform structural KB checks or updates.
    """
    from app.agent.synthesis import run_synthesis
    try:
        reply = run_synthesis(db, manager_id, query, allow_writes=allow_writes)
        return {"query": query, "allow_writes": allow_writes, "agent_reply": reply}
    except Exception as e:
        return {"error": f"Failed to execute Knowledge Synthesis Agent: {e}"}


registry.register(
    name="trigger_knowledge_synthesis_agent",
    schema={
        "name": "trigger_knowledge_synthesis_agent",
        "description": "Invokes the background Knowledge Synthesis Agent to query or make structural write-updates to the database/KB (such as updating task transitions, writing event notifications, record conflicts, drafting tasks etc).",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The natural language instruction/update to feed to the Knowledge Synthesis Agent (e.g. 'Move task X to blocked due to Y' or 'Find conflicts between claims A and B')."},
                "allow_writes": {"type": "boolean", "description": "Whether the KB synthesis agent is permitted to write or make edits. Defaults to True."}
            },
            "required": ["query"]
        }
    },
    handler=trigger_knowledge_synthesis_agent_handler
)


# -----------------------------------------------------------------------------
# Cron Scheduling & Workflow Tools
# -----------------------------------------------------------------------------

def schedule_one_time_reminder_handler(
    db: Session,
    manager_id: str,
    run_context: Dict[str, Any],
    prompt: str,
    run_at_iso: str
) -> Dict[str, Any]:
    try:
        next_run = datetime.fromisoformat(run_at_iso.replace("Z", "+00:00"))
    except ValueError:
        # Fallback parser if plain string
        try:
            next_run = datetime.strptime(run_at_iso, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return {"error": f"Could not parse run_at_iso '{run_at_iso}'. Must be ISO-8601 or YYYY-MM-DD HH:MM:SS."}
            
    cron = CronJob(
        workflow_id=None,
        task_type="reminder",
        prompt=prompt,
        schedule=run_at_iso,
        is_recurring=False,
        next_run_at=next_run,
        status="pending"
    )
    db.add(cron)
    db.commit()
    return {"status": "scheduled", "cron_job_id": cron.id, "next_run_at": str(cron.next_run_at)}


def create_workflow_handler(
    db: Session,
    manager_id: str,
    run_context: Dict[str, Any],
    name: str,
    cron_expression: str,
    prompt: str,
    description: Optional[str] = None,
    task_type: str = "custom"
) -> Dict[str, Any]:
    # 1. Create Workflow row
    wf = Workflow(
        name=name,
        description=prompt if not description else f"{description}\nTrigger Prompt: {prompt}",
        task_type=task_type,
        cron_expression=cron_expression,
        config=json.dumps({"prompt": prompt}),
        status="active"
    )
    db.add(wf)
    db.commit()
    db.refresh(wf)
    
    # 2. Schedule first CronJob
    next_run = calculate_next_run(cron_expression, timeservice.now_ist())
    cron = CronJob(
        workflow_id=wf.id,
        task_type=task_type,
        prompt=prompt,
        schedule=cron_expression,
        is_recurring=True,
        next_run_at=next_run,
        status="pending"
    )
    db.add(cron)
    db.commit()
    
    return {
        "status": "workflow_created",
        "workflow_id": wf.id,
        "cron_job_id": cron.id,
        "next_run_at": str(cron.next_run_at)
    }


def list_cron_jobs_handler(db: Session, manager_id: str, run_context: Dict[str, Any]) -> List[Dict[str, Any]]:
    crons = db.scalars(select(CronJob).order_by(CronJob.next_run_at.asc())).all()
    results = []
    for c in crons:
        results.append({
            "id": c.id,
            "workflow_id": c.workflow_id,
            "task_type": c.task_type,
            "prompt": c.prompt,
            "schedule": c.schedule,
            "is_recurring": c.is_recurring,
            "next_run_at": str(c.next_run_at),
            "status": c.status
        })
    return results


def delete_cron_job_handler(db: Session, manager_id: str, run_context: Dict[str, Any], cron_job_id: int) -> Dict[str, Any]:
    cron = db.get(CronJob, cron_job_id)
    if not cron:
        return {"error": f"Cron job with ID {cron_job_id} not found."}
    db.delete(cron)
    db.commit()
    return {"status": "deleted", "cron_job_id": cron_job_id}


registry.register(
    name="schedule_one_time_reminder",
    schema={
        "name": "schedule_one_time_reminder",
        "description": "Schedules a one-time reminder or task prompt to be run at a specific future ISO-8601 datetime.",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "The reminder text or task trigger (e.g. 'I have a meeting at 5pm today')."},
                "run_at_iso": {"type": "string", "description": "The exact target ISO-8601 timestamp (e.g. '2026-08-10T17:00:00')."}
            },
            "required": ["prompt", "run_at_iso"]
        }
    },
    handler=schedule_one_time_reminder_handler
)

registry.register(
    name="create_workflow",
    schema={
        "name": "create_workflow",
        "description": "Registers a new automated/recurring workflow for the manager, such as project followups, morning briefs, or study reminders.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The name of the workflow (e.g. 'Morning brief trigger')."},
                "cron_expression": {"type": "string", "description": "Cron interval or expression (e.g. '0 9 * * *' for 9am daily, or 'every 2h', or '30m')."},
                "prompt": {"type": "string", "description": "The prompt instruction that triggers when scheduled (e.g. 'Compile active milestones and deliver morning report over Slack')."},
                "description": {"type": "string", "description": "Optional workflow description detail."},
                "task_type": {"type": "string", "enum": ["followup", "morning_brief", "custom"], "description": "The task category."}
            },
            "required": ["name", "cron_expression", "prompt"]
        }
    },
    handler=create_workflow_handler
)

registry.register(
    name="list_cron_jobs",
    schema={
        "name": "list_cron_jobs",
        "description": "Lists all scheduled cron runs and active time reminders for the manager.",
        "parameters": {"type": "object", "properties": {}}
    },
    handler=list_cron_jobs_handler
)

registry.register(
    name="delete_cron_job",
    schema={
        "name": "delete_cron_job",
        "description": "Deletes or cancels a scheduled cron job using its integer ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "cron_job_id": {"type": "integer", "description": "The unique ID of the cron job to delete."}
            },
            "required": ["cron_job_id"]
        }
    },
    handler=delete_cron_job_handler
)


# -----------------------------------------------------------------------------
# Followup & Multi-Agent Spawning Tools
# -----------------------------------------------------------------------------

def spawn_followup_chat_agent_handler(
    db: Session,
    manager_id: str,
    run_context: Dict[str, Any],
    recipient_employee_id: str,
    scoped_project_ids: List[str],
    instructions: str
) -> Dict[str, Any]:
    # 1. Fetch recipient and manager details from Control Plane
    cp_db = ControlPlaneSessionLocal()
    try:
        recipient = get_employee_by_manager_id(cp_db, recipient_employee_id)
        manager = get_employee_by_manager_id(cp_db, manager_id)
        if not recipient:
            return {"error": f"Recipient Employee ID '{recipient_employee_id}' not found."}
        recipient_name = recipient.name if recipient else "team member"
        manager_name = manager.name if manager else "the manager"
        recipient_slack_id = recipient.slack_id if recipient else None
        recipient_email = recipient.email if recipient else None
    finally:
        cp_db.close()
        
    # 2. Check if there is already an active followup with this employee
    existing = db.scalars(
        select(FollowupAgent).where(FollowupAgent.recipient_employee_id == recipient_employee_id, FollowupAgent.status == "active")
    ).first()
    if existing:
        return {"status": "already_active", "followup_id": existing.id, "detail": "There is already an active followup running for this person."}
        
    # 3. Create FollowupAgent row
    agent = FollowupAgent(
        recipient_employee_id=recipient_employee_id,
        scoped_project_ids=json.dumps(scoped_project_ids),
        instructions=instructions,
        status="active",
        chat_history="[]"
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    
    # 4. Draft initial greeting over LLM (lightweight chatbot)
    draft_prompt = (
        f"You are writing a polite, highly concise initial followup message from {manager_name} to {recipient_name}.\n"
        f"Followup Objective:\n{instructions}\n\n"
        "Produce only the direct greeting and opening query message. Do not add any tags, system tokens, or explanations. Keep it under 2 sentences."
    )
    client = get_client()
    try:
        resp = client.chat(model=FLASH_MODEL, messages=[{"role": "user", "content": draft_prompt}], temperature=0.3)
        greeting = resp.get("content") or f"Hi {recipient_name}, I am reaching out to follow up on your tasks. Could you give me an update?"
        
        # 5. Send message via Slack or Email (using existing connector fallback)
        from app.integrations.slack import connector as slack_connector
        sent_ok = False
        channel_used = "slack"
        if recipient_slack_id:
            try:
                slack_connector.send(db, recipient_slack_id, greeting)
                sent_ok = True
            except Exception as e:
                logger.warning(f"Slack delivery failed for followup #{agent.id}: {e}")
                
        if not sent_ok and recipient_email:
            # Fallback to email
            from app.integrations.outlook import connector as outlook_connector
            try:
                outlook_connector.send(db, recipient_email, greeting, subject="Task Followup Update Request")
                channel_used = "email"
                sent_ok = True
            except Exception as e:
                logger.warning(f"Outlook delivery failed for followup #{agent.id}: {e}")
                
        if sent_ok:
            # Add to history
            history = [{"role": "assistant", "content": greeting, "timestamp": timeservice.now_utc_iso()}]
            agent.chat_history = json.dumps(history)
            agent.last_message_sent_at = timeservice.now_ist()
            db.commit()
            return {"status": "spawned_successfully", "followup_id": agent.id, "channel": channel_used, "initial_message": greeting}
        else:
            agent.status = "failed"
            db.commit()
            return {"error": "Failed to deliver initial message via Slack or Email."}
    except Exception as e:
        logger.error(f"Failed to spawn followup chat agent: {e}", exc_info=True)
        return {"error": f"Failed to spawn followup: {e}"}


def list_active_followups_handler(db: Session, manager_id: str, run_context: Dict[str, Any]) -> List[Dict[str, Any]]:
    agents = db.scalars(select(FollowupAgent).order_by(FollowupAgent.updated_at.desc())).all()
    results = []
    cp_db = ControlPlaneSessionLocal()
    try:
        for a in agents:
            emp = get_employee_by_manager_id(cp_db, a.recipient_employee_id)
            name = emp.name if emp else "Unknown Employee"
            results.append({
                "id": a.id,
                "recipient_employee_id": a.recipient_employee_id,
                "recipient_name": name,
                "scoped_projects": json.loads(a.scoped_project_ids),
                "instructions": a.instructions,
                "status": a.status,
                "last_message_sent_at": str(a.last_message_sent_at) if a.last_message_sent_at else None,
                "last_message_received_at": str(a.last_message_received_at) if a.last_message_received_at else None,
                "report_summary": a.cos_context
            })
    finally:
        cp_db.close()
    return results


registry.register(
    name="spawn_followup_chat_agent",
    schema={
        "name": "spawn_followup_chat_agent",
        "description": "Delegates a project followup to an asynchronous Chat Agent. The Chat Agent will automatically message the team member over Slack/Email and converse to collect updates.",
        "parameters": {
            "type": "object",
            "properties": {
                "recipient_employee_id": {"type": "string", "description": "The Employee ID of the person we are following up with (e.g., 'employee_bob_iyer')."},
                "scoped_project_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of Registry Project IDs that this chat agent has read-only access to (to answer team member questions)."
                },
                "instructions": {"type": "string", "description": "The specific query/objective of the followup (e.g. 'Ask Bob when the frontend layout will be completed')."}
            },
            "required": ["recipient_employee_id", "scoped_project_ids", "instructions"]
        }
    },
    handler=spawn_followup_chat_agent_handler
)

registry.register(
    name="list_active_followups",
    schema={
        "name": "list_active_followups",
        "description": "Lists all delegated warmup/followup chat agents, showing who they are contacting, instructions, and their statuses/reports.",
        "parameters": {"type": "object", "properties": {}}
    },
    handler=list_active_followups_handler
)


# -----------------------------------------------------------------------------
# Dynamic Registry Mapping for read-only KB probes
# -----------------------------------------------------------------------------

registry.register(
    name="search_events",
    schema={
        "name": "search_events",
        "description": "Searches for events matching keywords or project filter.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "project_id": {"type": "string"}
            }
        }
    },
    handler=search_events_handler
)

registry.register(
    name="get_event",
    schema={
        "name": "get_event",
        "description": "Fetches a single event with detailed citations.",
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string"}
            },
            "required": ["event_id"]
        }
    },
    handler=get_event_handler
)

registry.register(
    name="search_claims",
    schema={
        "name": "search_claims",
        "description": "Searches for raw parsed claims inside the KB.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"}
            }
        }
    },
    handler=search_claims_handler
)

registry.register(
    name="get_thread",
    schema={
        "name": "get_thread",
        "description": "Fetches raw conversation history of a specific message thread.",
        "parameters": {
            "type": "object",
            "properties": {
                "thread_key": {"type": "string"}
            },
            "required": ["thread_key"]
        }
    },
    handler=get_thread_handler
)

registry.register(
    name="get_project_state",
    schema={
        "name": "get_project_state",
        "description": "Reads full task backlog and health summaries of a specific project.",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"}
            },
            "required": ["project_id"]
        }
    },
    handler=get_project_state_handler
)

registry.register(
    name="list_projects",
    schema={
        "name": "list_projects",
        "description": "Lists all registered projects in the control-plane.",
        "parameters": {"type": "object", "properties": {}}
    },
    handler=list_projects_handler
)


# -----------------------------------------------------------------------------
# CORE Chief of Staff Agent Execution Function
# -----------------------------------------------------------------------------

COS_TOOL_NAMES = (
    "search_events",
    "get_event",
    "search_claims",
    "get_thread",
    "get_project_state",
    "list_projects",
    "trigger_knowledge_synthesis_agent",
    "web_search",
    "read_memory",
    "update_memory",
    "schedule_one_time_reminder",
    "create_workflow",
    "list_cron_jobs",
    "delete_cron_job",
    "spawn_followup_chat_agent",
    "list_active_followups"
)

def run_cos_agent(db: Session, manager_id: str, user_message: str, channel: str = "portal") -> Dict[str, Any]:
    """
    User-facing COS Agent loop. Encapsulates profile context compiling,
    compaction trigger, history compilation, tool running, and response delivery.
    """
    client = get_client()
    
    # 1. Append new user message to chat history DB (first, so it lands in context)
    user_msg = ChatMessage(role="user", content=user_message)
    db.add(user_msg)
    db.commit()
    
    # 2. Get compacted history list (includes summary prepending)
    history = get_compacted_history(db, manager_id)
    
    # 3. Compile dynamic context system instructions
    context_text = compile_cos_system_prompt(db, manager_id)
    
    # 4. Define agent specification
    spec = AgentSpec(
        name="chief_of_staff",
        model=SMART_MODEL,
        instructions="",  # context_text alone is the entire prompt
        tool_names=COS_TOOL_NAMES,
        max_llm_calls=25,
        max_tool_calls=150,
        deadline_seconds=300.0,
        temperature=0.2
    )
    
    # 5. Run Spec using core Agentic runner
    result = run_spec(
        spec,
        db,
        manager_id,
        user_message,
        client=client,
        history=history,
        run_context={},
        context_text=context_text,
        tool_registry=registry
    )
    
    reply_text = result.reply or "I apologize, but I encountered an error formulating my reply."
    
    # 6. Persist assistant reply
    assistant_msg = ChatMessage(
        role="assistant",
        content=reply_text,
        tool_trace=json.dumps(result.tool_trace) if result.tool_trace else None
    )
    db.add(assistant_msg)
    db.commit()
    db.refresh(assistant_msg)
    
    # 7. Deliver directly over Slack if the origin is Slack
    if channel == "slack":
        # Lookup manager's slack ID to find their channel ID (IM)
        from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal
        cp_db = ControlPlaneSessionLocal()
        try:
            m = get_employee_by_manager_id(cp_db, manager_id)
            if m and m.slack_id:
                from app.integrations.slack import connector as slack_connector
                slack_connector.send(db, m.slack_id, reply_text)
        finally:
            cp_db.close()
            
    return {
        "reply": reply_text,
        "tool_trace": result.tool_trace,
        "created_at": assistant_msg.created_at
    }


# -----------------------------------------------------------------------------
# RECIPIENT CHAT AGENT REPLY LOOP
# -----------------------------------------------------------------------------

def handle_team_member_reply(db: Session, manager_id: str, recipient_employee_id: str, message_text: str, source_channel: str = "slack") -> None:
    """
    Called when a message from a non-manager team member is received by the bot.
    Finds the active FollowupAgent, compiles its lightweight scoped prompt,
    calculates response, parses [REPORT_COS: ...], and sends back friendly text.
    """
    # 1. Fetch active followups
    agent = db.scalars(
        select(FollowupAgent).where(FollowupAgent.recipient_employee_id == recipient_employee_id, FollowupAgent.status == "active")
    ).first()
    if not agent:
        logger.warning(f"Received reply from team member={recipient_employee_id} but no active followup agent was found.")
        return
        
    # 2. Append incoming message to followup history
    history = json.loads(agent.chat_history)
    history.append({
        "role": "user",
        "content": message_text,
        "timestamp": timeservice.now_utc_iso()
    })
    agent.last_message_received_at = timeservice.now_ist()
    agent.chat_history = json.dumps(history)
    db.commit()
    
    # 3. Build lightweight scoped project context
    scoped_projects = json.loads(agent.scoped_project_ids)
    projects_backlog_data = []
    for pid in scoped_projects:
        proj_context = get_project_state_handler(db, manager_id="", run_context={}, project_id=pid)
        projects_backlog_data.append(f"Project #{pid} Context:\n{json.dumps(proj_context)}")
    scoped_data_text = "\n\n".join(projects_backlog_data)
    
    # 4. Fetch manager details
    cp_db = ControlPlaneSessionLocal()
    try:
        manager = get_employee_by_manager_id(cp_db, manager_id)
        recipient = get_employee_by_manager_id(cp_db, recipient_employee_id)
        manager_name = manager.name if manager else "the manager"
        recipient_name = recipient.name if recipient else "team member"
        manager_slack_id = manager.slack_id if manager else None
    finally:
        cp_db.close()
        
    # 5. Formulate lightweight Chat Agent system instructions
    system_prompt = (
        f"You are an automated, friendly project assistant bot representing {manager_name}.\n"
        f"Your sole objective is to follow up with {recipient_name} regarding:\n{agent.instructions}\n\n"
        "Read-only Scoped Projects Context (to answer questions, if needed):\n"
        f"{scoped_data_text}\n\n"
        "=================== COMMAND DIRECTIVES ===================\n"
        "1. Converse politely, briefly, and professionally to gather the requested update.\n"
        "2. If they provide a solid, substantial update or answer your questions, or if they state they are blocked, "
        "you MUST report this back to the Chief of Staff (COS) Agent by appending `[REPORT_COS: <your concise structured report details>]` to your message.\n"
        "3. Ensure the [REPORT_COS: ...] tag is at the very end of your response text. Output only friendly conversational text + the tag if reporting."
    )
    
    # Translate history list to Gemini/OpenAI spec
    formatted_history = []
    for h in history:
        formatted_history.append({"role": h["role"], "content": h["content"]})
        
    messages_payload = [{"role": "system", "content": system_prompt}] + formatted_history
    
    # 6. Execute model call (lighweight Flash)
    client = get_client()
    try:
        resp = client.chat(model=FLASH_MODEL, messages=messages_payload, temperature=0.3)
        reply = resp.get("content") or "Thank you for the update."
        
        # 7. Check for [REPORT_COS] tag
        report_match = re.search(r"\[REPORT_COS:\s*(.*?)\]", reply, re.IGNORECASE | re.DOTALL)
        if report_match:
            report_text = report_match.group(1).strip()
            # Mark reported
            agent.cos_context = report_text
            agent.status = "reported"
            # Strip tag from employee-facing reply
            clean_reply = re.sub(r"\[REPORT_COS:\s*.*?\]", "", reply, flags=re.IGNORECASE | re.DOTALL).strip()
        else:
            clean_reply = reply
            
        # 8. Append to history and update sent timestamps
        history.append({
            "role": "assistant",
            "content": clean_reply,
            "timestamp": timeservice.now_utc_iso()
        })
        agent.chat_history = json.dumps(history)
        agent.last_message_sent_at = timeservice.now_ist()
        db.commit()
        
        # 9. Deliver reply to the team member
        delivered = False
        if source_channel == "slack" and recipient and recipient.slack_id:
            try:
                from app.integrations.slack import connector as slack_connector
                slack_connector.send(db, recipient.slack_id, clean_reply)
                delivered = True
            except Exception:
                pass
                
        if not delivered and recipient and recipient.email:
            # Email fallback
            from app.integrations.outlook import connector as outlook_connector
            try:
                outlook_connector.send(db, recipient.email, clean_reply, subject="Task Followup Update")
            except Exception:
                pass
                
        # 10. Proactively notify manager if reported
        if agent.status == "reported" and agent.cos_context:
            notification = (
                f"📢 *Delegated Chat Agent Followup Report*:\n"
                f"Recipient: *{recipient_name}*\n"
                f"Instructions: {agent.instructions}\n"
                f"Update Reported: {agent.cos_context}"
            )
            # Log action
            db.add(AgentActionLog(
                id=uuid.uuid4().hex,
                action_type="followup_reported",
                ref_key=f"followup:{agent.id}",
                detail=notification[:500]
            ))
            db.commit()
            
            # Send live FYI to manager's Slack IM
            if manager_slack_id:
                try:
                    from app.integrations.slack import connector as slack_connector
                    slack_connector.send(db, manager_slack_id, notification)
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"Failed to handle team member reply for followup #{agent.id}: {e}", exc_info=True)


# -----------------------------------------------------------------------------
# Background Scheduler Poller for Cron Jobs
# -----------------------------------------------------------------------------

def check_and_run_manager_crons() -> None:
    """
    Sweeps over every provisioned manager DB, finds due pending CronJobs,
    sets status to 'running', triggers the run_cos_agent with the cron prompt as a message,
    and updates the cron job status (scheduling the next run if recurring).
    """
    from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, list_provisioned_managers
    from app.tenancy.db import get_manager_session
    
    cp_db = ControlPlaneSessionLocal()
    try:
        managers = list_provisioned_managers(cp_db)
    finally:
        cp_db.close()
        
    now = timeservice.now_ist()
    for manager in managers:
        db = get_manager_session(manager.id)
        try:
            due_crons = db.scalars(
                select(CronJob).where(CronJob.status == "pending", CronJob.next_run_at <= now)
            ).all()
            for cron in due_crons:
                cron.status = "running"
                db.commit()
                
                try:
                    logger.info(f"[cron] Executing Cron #{cron.id} for manager={manager.id} (prompt: '{cron.prompt}')")
                    # Trigger the COS agent
                    run_cos_agent(db, manager.id, user_message=cron.prompt, channel="cron")
                    
                    # Update status
                    if cron.is_recurring:
                        cron.last_run_at = timeservice.now_ist()
                        cron.next_run_at = calculate_next_run(cron.schedule, timeservice.now_ist())
                        cron.status = "pending"
                    else:
                        cron.status = "completed"
                    db.commit()
                except Exception as e:
                    logger.error(f"[cron] Failed to execute Cron #{cron.id} for manager={manager.id}: {e}", exc_info=True)
                    cron.status = "failed"
                    db.commit()
        except Exception:
            logger.exception(f"[cron] Failed to check crons for manager={manager.id}")
        finally:
            db.close()

