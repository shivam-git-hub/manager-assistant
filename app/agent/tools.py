import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import TeamMember, Meeting, ActionItem
from app.followups import get_dm_channel_id
from app.outbound import send_or_hold
from app.agent.registry import registry

# -----------------------------------------------------------------------------
# Tool Handlers
# -----------------------------------------------------------------------------

def send_slack_dm_handler(db: Session, member_id: str, text: str) -> Dict[str, Any]:
    member = db.get(TeamMember, member_id)
    if not member:
        return {"error": f"Team member with ID '{member_id}' not found."}

    dm_channel = get_dm_channel_id("U_HARRY", member_id)
    res = send_or_hold("slack", {"channel": dm_channel, "text": text}, db)
    return {
        "success": True,
        "status": res["status"],
        "channel": dm_channel,
        "release_at": res.get("release_at"),
        "message_id": res.get("message_id")
    }


def send_email_handler(db: Session, member_id: str, subject: str, body: str) -> Dict[str, Any]:
    member = db.get(TeamMember, member_id)
    if not member:
        return {"error": f"Team member with ID '{member_id}' not found."}

    if not member.outlook_email:
        return {"error": f"Team member '{member_id}' has no registered outlook_email."}

    payload = {
        "message": {
            "subject": subject,
            "body": {
                "content": body,
                "contentType": "HTML"
            },
            "toRecipients": [
                {
                    "emailAddress": {
                        "address": member.outlook_email
                    }
                }
            ]
        }
    }
    res = send_or_hold("outlook", payload, db)
    return {
        "success": True,
        "status": res["status"],
        "recipient": member.outlook_email,
        "release_at": res.get("release_at"),
        "message_id": res.get("message_id")
    }


# -----------------------------------------------------------------------------
# Tool Schemas (OpenAI Functional Calling / Gemini Declarations compatible)
# -----------------------------------------------------------------------------

SEND_SLACK_DM_SCHEMA = {
    "name": "send_slack_dm",
    "description": (
        "Sends a direct message (DM) to a team member on Slack. "
        "This routes through our outbound queue which automatically enforces working hours. "
        "If called outside working hours or on weekends, the message will be held and queued "
        "for scheduled release next business morning at 09:00 IST. "
        "Always inform the manager if the message was sent immediately or queued."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "member_id": {
                "type": "string",
                "description": "The team member ID to send the message to (e.g., 'U_BOB')."
            },
            "text": {
                "type": "string",
                "description": "The message body text."
            }
        },
        "required": ["member_id", "text"]
    }
}

SEND_EMAIL_SCHEMA = {
    "name": "send_email",
    "description": (
        "Sends an email to a team member's registered Outlook address. "
        "Like Slack, it uses the outbound working-hours gate to hold/queue messages sent "
        "during quiet hours."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "member_id": {
                "type": "string",
                "description": "The team member ID to email (e.g., 'U_BOB')."
            },
            "subject": {
                "type": "string",
                "description": "Email subject line."
            },
            "body": {
                "type": "string",
                "description": "Email body content."
            }
        },
        "required": ["member_id", "subject", "body"]
    }
}

# -----------------------------------------------------------------------------
# Meetings Handlers & Schemas
# -----------------------------------------------------------------------------

def list_meetings_handler(
    db: Session,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None
) -> List[Dict[str, Any]]:
    stmt = select(Meeting)
    if from_date:
        try:
            from_dt = datetime.strptime(from_date, "%Y-%m-%d")
            stmt = stmt.where(Meeting.starts_at >= from_dt)
        except ValueError:
            return [{"error": "Invalid from_date format. Must be YYYY-MM-DD"}]
    if to_date:
        try:
            to_dt = datetime.strptime(to_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
            stmt = stmt.where(Meeting.starts_at <= to_dt)
        except ValueError:
            return [{"error": "Invalid to_date format. Must be YYYY-MM-DD"}]

    meetings = db.scalars(stmt.order_by(Meeting.starts_at.asc())).all()
    resp = []
    for m in meetings:
        try:
            atts = json.loads(m.attendees)
        except Exception:
            atts = []
        resp.append({
            "id": m.id,
            "title": m.title,
            "starts_at": m.starts_at.strftime("%Y-%m-%d %H:%M:%S"),
            "ends_at": m.ends_at.strftime("%Y-%m-%d %H:%M:%S") if m.ends_at else None,
            "attendees": atts,
            "project_id": m.project_id,
            "status": m.status
        })
    return resp

def get_meeting_handler(db: Session, meeting_id: int) -> Dict[str, Any]:
    meeting = db.get(Meeting, meeting_id)
    if not meeting:
        return {"error": f"Meeting {meeting_id} not found."}

    try:
        atts = json.loads(meeting.attendees)
    except Exception:
        atts = []

    items = db.scalars(select(ActionItem).where(ActionItem.meeting_id == meeting_id)).all()
    action_items = [
        {
            "id": item.id,
            "description": item.description,
            "owner_member_id": item.owner_member_id,
            "due_date": item.due_date.strftime("%Y-%m-%d") if item.due_date else None
        }
        for item in items
    ]

    return {
        "id": meeting.id,
        "title": meeting.title,
        "starts_at": meeting.starts_at.strftime("%Y-%m-%d %H:%M:%S"),
        "ends_at": meeting.ends_at.strftime("%Y-%m-%d %H:%M:%S") if meeting.ends_at else None,
        "attendees": atts,
        "project_id": meeting.project_id,
        "status": meeting.status,
        "mom_raw": meeting.mom_raw,
        "action_items": action_items
    }

def create_meeting_handler(
    db: Session,
    title: str,
    starts_at: str,
    attendees: List[str],
    project_id: Optional[int] = None
) -> Dict[str, Any]:
    try:
        parsed_starts = datetime.strptime(starts_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            parsed_starts = datetime.strptime(starts_at, "%Y-%m-%d")
        except ValueError:
            return {"error": "Invalid starts_at format. Must be YYYY-MM-DD HH:MM:SS"}

    from app.kb.meetings import create_meeting as create_meeting_internal, MeetingCreate
    payload = MeetingCreate(
        title=title,
        starts_at=parsed_starts,
        attendees=attendees,
        project_id=project_id
    )
    res = create_meeting_internal(payload, db)
    return {
        "success": True,
        "message": f"Meeting '{title}' created successfully with ID {res.id}.",
        "meeting": {
            "id": res.id,
            "title": res.title,
            "starts_at": res.starts_at.strftime("%Y-%m-%d %H:%M:%S"),
            "attendees": res.attendees
        }
    }


LIST_MEETINGS_SCHEMA = {
    "name": "list_meetings",
    "description": "Lists calendar entries for the range of dates. Helpful to retrieve what meetings are scheduled or completed.",
    "parameters": {
        "type": "object",
        "properties": {
            "from_date": {
                "type": "string",
                "description": "Optional start date in YYYY-MM-DD format."
            },
            "to_date": {
                "type": "string",
                "description": "Optional end date in YYYY-MM-DD format."
            }
        }
    }
}

GET_MEETING_SCHEMA = {
    "name": "get_meeting",
    "description": "Fetches a specific meeting with its attendees, raw minutes, and extracted action items list by ID.",
    "parameters": {
        "type": "object",
        "properties": {
            "meeting_id": {
                "type": "integer",
                "description": "The integer ID of the meeting to fetch."
            }
        },
        "required": ["meeting_id"]
    }
}

CREATE_MEETING_SCHEMA = {
    "name": "create_meeting",
    "description": "Schedules a new meeting in the calendar, registers its meeting Entity, and schedules its pre-meeting brief. Harry or the manager can call this tool from chat.",
    "parameters": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "The title or subject of the meeting."
            },
            "starts_at": {
                "type": "string",
                "description": "The starting date-time in 'YYYY-MM-DD HH:MM:SS' format."
            },
            "attendees": {
                "type": "array",
                "items": { "type": "string" },
                "description": "A list of participating team member IDs, e.g., ['U_ALICE', 'U_BOB']."
            },
            "project_id": {
                "type": "integer",
                "description": "Optional associated project integer ID."
            }
        },
        "required": ["title", "starts_at", "attendees"]
    }
}

# -----------------------------------------------------------------------------
# Hermes Imported Tools (Handlers)
# -----------------------------------------------------------------------------

def skill_manage_handler(
    db: Session,
    action: str,
    name: str,
    category: Optional[str] = None,
    content: Optional[str] = None,
    new_string: Optional[str] = None,
    old_string: Optional[str] = None,
    replace_all: Optional[bool] = False,
    file_path: Optional[str] = None,
    file_content: Optional[str] = None,
    absorbed_into: Optional[str] = None
) -> Dict[str, Any]:
    """Mock/placeholder for Hermes skill_manage tool."""
    return {
        "success": True,
        "message": f"skill_manage action '{action}' on skill '{name}' processed (mocked).",
        "action": action,
        "name": name,
        "category": category,
        "file_path": file_path
    }


def skills_list_handler(
    db: Session,
    category: Optional[str] = None
) -> Dict[str, Any]:
    """Mock/placeholder for Hermes skills_list tool."""
    return {
        "success": True,
        "message": "skills_list retrieved (mocked).",
        "category": category,
        "skills": []
    }


def skill_view_handler(
    db: Session,
    name: str,
    file_path: Optional[str] = None
) -> Dict[str, Any]:
    """Mock/placeholder for Hermes skill_view tool."""
    return {
        "success": True,
        "message": f"skill_view for '{name}' processed (mocked).",
        "name": name,
        "file_path": file_path,
        "content": "Mock skill content."
    }


def memory_handler(
    db: Session,
    action: str,
    target: str,
    content: Optional[str] = None,
    old_text: Optional[str] = None
) -> Dict[str, Any]:
    """Mock/placeholder for Hermes memory tool."""
    return {
        "success": True,
        "message": f"memory action '{action}' on target '{target}' processed (mocked).",
        "action": action,
        "target": target
    }


def todo_handler(
    db: Session,
    todos: Optional[List[Dict[str, Any]]] = None,
    merge: Optional[bool] = False
) -> Dict[str, Any]:
    """Mock/placeholder for Hermes todo tool."""
    return {
        "success": True,
        "message": "todo list updated/retrieved (mocked).",
        "todos": todos or [],
        "merge": merge
    }


def session_search_handler(
    db: Session,
    query: Optional[str] = None,
    session_id: Optional[str] = None,
    around_message_id: Optional[int] = None,
    window: Optional[int] = 5,
    sort: Optional[str] = None,
    role_filter: Optional[str] = None,
    limit: Optional[int] = 3
) -> Dict[str, Any]:
    """Mock/placeholder for Hermes session_search tool."""
    return {
        "success": True,
        "message": "session_search executed (mocked).",
        "query": query,
        "session_id": session_id,
        "results": []
    }


def read_file_handler(
    db: Session,
    path: str,
    offset: Optional[int] = 1,
    limit: Optional[int] = 500
) -> Dict[str, Any]:
    """Mock/placeholder for Hermes read_file tool."""
    return {
        "success": True,
        "message": f"read_file for '{path}' processed (mocked).",
        "path": path,
        "offset": offset,
        "limit": limit,
        "content": "Mock file content."
    }


def write_file_handler(
    db: Session,
    path: str,
    content: str,
    cross_profile: Optional[bool] = False
) -> Dict[str, Any]:
    """Mock/placeholder for Hermes write_file tool."""
    return {
        "success": True,
        "message": f"write_file for '{path}' processed (mocked).",
        "path": path,
        "cross_profile": cross_profile
    }


def patch_handler(
    db: Session,
    mode: str,
    path: Optional[str] = None,
    old_string: Optional[str] = None,
    new_string: Optional[str] = None,
    patch: Optional[str] = None,
    replace_all: Optional[bool] = False,
    cross_profile: Optional[bool] = False
) -> Dict[str, Any]:
    """Mock/placeholder for Hermes patch tool."""
    return {
        "success": True,
        "message": f"patch mode '{mode}' processed (mocked).",
        "mode": mode,
        "path": path
    }


def send_message_handler(
    db: Session,
    action: Optional[str] = "send",
    message: Optional[str] = None,
    target: Optional[str] = None
) -> Dict[str, Any]:
    """Mock/placeholder for Hermes send_message tool."""
    return {
        "success": True,
        "message": f"send_message action '{action}' processed (mocked).",
        "action": action,
        "target": target
    }


def terminal_handler(
    db: Session,
    command: str,
    background: Optional[bool] = False,
    notify_on_complete: Optional[bool] = False,
    pty: Optional[bool] = False,
    timeout: Optional[int] = None,
    watch_patterns: Optional[List[str]] = None,
    workdir: Optional[str] = None
) -> Dict[str, Any]:
    """Mock/placeholder for Hermes terminal tool."""
    return {
        "success": True,
        "message": f"terminal command '{command}' executed (mocked).",
        "command": command,
        "background": background
    }


def cronjob_handler(
    db: Session,
    action: str,
    job_id: Optional[str] = None,
    schedule: Optional[str] = None,
    prompt: Optional[str] = None,
    skills: Optional[List[str]] = None,
    script: Optional[str] = None,
    no_agent: Optional[bool] = False,
    deliver: Optional[str] = None,
    context_from: Optional[List[str]] = None,
    enabled_toolsets: Optional[List[str]] = None,
    workdir: Optional[str] = None,
    profile: Optional[str] = None
) -> Dict[str, Any]:
    """Mock/placeholder for Hermes cronjob tool."""
    return {
        "success": True,
        "message": f"cronjob action '{action}' processed (mocked).",
        "action": action,
        "job_id": job_id
    }


def delegate_task_handler(
    db: Session,
    goal: Optional[str] = None,
    context: Optional[str] = None,
    role: Optional[str] = None,
    toolsets: Optional[List[str]] = None,
    tasks: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """Mock/placeholder for Hermes delegate_task tool."""
    return {
        "success": True,
        "message": "delegate_task processed (mocked).",
        "goal": goal,
        "tasks_count": len(tasks) if tasks else 0
    }

# -----------------------------------------------------------------------------
# Hermes Imported Tools (Schemas)
# -----------------------------------------------------------------------------

SKILL_MANAGE_SCHEMA = {
    "name": "skill_manage",
    "description": "Manage skills (create, update, delete). Skills are your procedural memory — reusable approaches for recurring task types. New skills go to ~/.hermes/skills/; existing skills can be modified wherever they live.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "patch", "edit", "delete", "write_file", "remove_file"],
                "description": "The action to perform."
            },
            "name": {
                "type": "string",
                "description": "Skill name (lowercase, hyphens/underscores, max 64 chars). Must match an existing skill for patch/edit/delete/write_file/remove_file."
            },
            "category": {
                "type": "string",
                "description": "Optional category/domain for organizing the skill (e.g., 'devops', 'data-science', 'mlops'). Creates a subdirectory grouping. Only used with 'create'."
            },
            "content": {
                "type": "string",
                "description": "Full SKILL.md content (YAML frontmatter + markdown body). Required for 'create' and 'edit'. For 'edit', read the skill first with skill_view() and provide the complete updated text."
            },
            "new_string": {
                "type": "string",
                "description": "Replacement text (required for 'patch'). Can be empty string to delete the matched text."
            },
            "old_string": {
                "type": "string",
                "description": "Text to find in the file (required for 'patch'). Must be unique unless replace_all=true. Include enough surrounding context to ensure uniqueness."
            },
            "replace_all": {
                "type": "boolean",
                "description": "For 'patch': replace all occurrences instead of requiring a unique match (default: false)."
            },
            "file_path": {
                "type": "string",
                "description": "Path to a supporting file within the skill directory. For 'write_file'/'remove_file': required, must be under references/, templates/, scripts/, or assets/. For 'patch': optional, defaults to SKILL.md if omitted."
            },
            "file_content": {
                "type": "string",
                "description": "Content for the file. Required for 'write_file'."
            },
            "absorbed_into": {
                "type": "string",
                "description": "For 'delete' only — declares intent so the curator can tell consolidation from pruning without guessing."
            }
        },
        "required": ["action", "name"]
    }
}

SKILLS_LIST_SCHEMA = {
    "name": "skills_list",
    "description": "List available skills (name + description). Use skill_view(name) to load full content.",
    "parameters": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "description": "Optional category filter to narrow results"
            }
        }
    }
}

SKILL_VIEW_SCHEMA = {
    "name": "skill_view",
    "description": "Skills allow for loading information about specific tasks and workflows, as well as scripts and templates. Load a skill's full content or access its linked files (references, templates, scripts). First call returns SKILL.md content plus a 'linked_files' dict showing available references/templates/scripts. To access those, call again with file_path parameter.",
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The skill name (use skills_list to see available skills). For plugin-provided skills, use the qualified form 'plugin:skill' (e.g. 'superpowers:writing-plans')."
            },
            "file_path": {
                "type": "string",
                "description": "OPTIONAL: Path to a linked file within the skill (e.g., 'references/api.md', 'templates/config.yaml', 'scripts/validate.py'). Omit to get the main SKILL.md content."
            }
        },
        "required": ["name"]
    }
}

MEMORY_SCHEMA = {
    "name": "memory",
    "description": "Save durable information to persistent memory that survives across sessions. Memory is injected into future turns, so keep it compact and focused on facts that will still matter later.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "replace", "remove"],
                "description": "The action to perform."
            },
            "target": {
                "type": "string",
                "enum": ["memory", "user"],
                "description": "Which memory store: 'memory' for personal notes, 'user' for user profile."
            },
            "content": {
                "type": "string",
                "description": "The entry content. Required for 'add' and 'replace'."
            },
            "old_text": {
                "type": "string",
                "description": "Short unique substring identifying the entry to replace or remove."
            }
        },
        "required": ["action", "target"]
    }
}

TODO_SCHEMA = {
    "name": "todo",
    "description": "Manage your task list for the current session. Use for complex tasks with 3+ steps or when the user provides multiple tasks. Call with no parameters to read the current list.",
    "parameters": {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": { "type": "string", "description": "Unique item identifier" },
                        "content": { "type": "string", "description": "Task description" },
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed", "cancelled"],
                            "description": "Current status"
                        }
                    },
                    "required": ["id", "content", "status"]
                },
                "description": "Task items to write. Omit to read current list."
            },
            "merge": {
                "type": "boolean",
                "default": False,
                "description": "true: update existing items by id, add new ones. false (default): replace the entire list."
            }
        }
    }
}

SESSION_SEARCH_SCHEMA = {
    "name": "session_search",
    "description": "Search past sessions stored in the local session DB, or scroll inside one. FTS5-backed retrieval over the SQLite message store. No LLM calls — every shape returns actual messages from the DB.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query (discovery shape). Keywords, phrases, or boolean expressions to find in past sessions. Omit to browse recent sessions."
            },
            "session_id": {
                "type": "string",
                "description": "Scroll shape. Session to read inside. Use the session_id returned from a prior discovery call. Must be paired with around_message_id."
            },
            "around_message_id": {
                "type": "integer",
                "description": "Scroll shape. Message id to center the window on."
            },
            "window": {
                "type": "integer",
                "default": 5,
                "description": "Scroll shape only. Messages to return on each side of the anchor."
            },
            "sort": {
                "type": "string",
                "enum": ["newest", "oldest"],
                "description": "Discovery shape only. Temporal bias on top of FTS5 ranking."
            },
            "role_filter": {
                "type": "string",
                "description": "Optional. Comma-separated roles to include. Discovery defaults to 'user,assistant'."
            },
            "limit": {
                "type": "integer",
                "default": 3,
                "description": "Discovery shape only. Max sessions to return (default 3, max 10)."
            }
        }
    }
}

READ_FILE_SCHEMA = {
    "name": "read_file",
    "description": "Read a text file with line numbers and pagination. Use this instead of cat/head/tail in terminal. Output format: 'LINE_NUM|CONTENT'. Suggests similar filenames if not found. Use offset and limit for large files.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to read (absolute, relative, or ~/path)"
            },
            "offset": {
                "type": "integer",
                "default": 1,
                "description": "Line number to start reading from (1-indexed, default: 1)"
            },
            "limit": {
                "type": "integer",
                "default": 500,
                "description": "Maximum number of lines to read (default: 500, max: 2000)"
            }
        },
        "required": ["path"]
    }
}

WRITE_FILE_SCHEMA = {
    "name": "write_file",
    "description": "Write content to a file, completely replacing existing content. Use this instead of echo/cat heredoc in terminal. Creates parent directories automatically. OVERWRITES the entire file — use 'patch' for targeted edits.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to write"
            },
            "content": {
                "type": "string",
                "description": "Complete content to write to the file"
            },
            "cross_profile": {
                "type": "boolean",
                "default": False,
                "description": "Opt out of the cross-profile soft guard."
            }
        },
        "required": ["path", "content"]
    }
}

PATCH_SCHEMA = {
    "name": "patch",
    "description": "Targeted find-and-replace edits in files. Use this instead of sed/awk in terminal. Uses fuzzy matching (9 strategies) so minor whitespace/indentation differences won't break it. Returns a unified diff.",
    "parameters": {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["replace", "patch"],
                "default": "replace",
                "description": "Edit mode. 'replace' (default): requires path + old_string + new_string. 'patch': requires patch content only."
            },
            "path": {
                "type": "string",
                "description": "REQUIRED when mode='replace'. File path to edit."
            },
            "old_string": {
                "type": "string",
                "description": "REQUIRED when mode='replace'. Exact text to find and replace. Must be unique in the file unless replace_all=true."
            },
            "new_string": {
                "type": "string",
                "description": "REQUIRED when mode='replace'. Replacement text. Pass empty string '' to delete the matched text."
            },
            "patch": {
                "type": "string",
                "description": "REQUIRED when mode='patch'. V4A format patch content."
            },
            "replace_all": {
                "type": "boolean",
                "default": False,
                "description": "Replace all occurrences instead of requiring a unique match (default: false)"
            },
            "cross_profile": {
                "type": "boolean",
                "default": False,
                "description": "Opt out of the cross-profile soft guard."
            }
        },
        "required": ["mode"]
    }
}

SEND_MESSAGE_SCHEMA = {
    "name": "send_message",
    "description": "Send a message to a connected messaging platform, or list available targets.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["send", "list"],
                "default": "send",
                "description": "Action to perform. 'send' (default) sends a message. 'list' returns all available channels/contacts across connected platforms."
            },
            "message": {
                "type": "string",
                "description": "The message text to send."
            },
            "target": {
                "type": "string",
                "description": "Delivery target."
            }
        }
    }
}

TERMINAL_SCHEMA = {
    "name": "terminal",
    "description": "Execute shell commands on a Linux environment. Filesystem usually persists between calls.",
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The command to execute on the VM"
            },
            "background": {
                "type": "boolean",
                "default": False,
                "description": "Run the command in the background."
            },
            "notify_on_complete": {
                "type": "boolean",
                "default": False,
                "description": "When true (and background=true), you'll be automatically notified exactly once when the process finishes."
            },
            "pty": {
                "type": "boolean",
                "default": False,
                "description": "Run in pseudo-terminal (PTY) mode for interactive CLI tools."
            },
            "timeout": {
                "type": "integer",
                "description": "Max seconds to wait."
            },
            "watch_patterns": {
                "type": "array",
                "items": { "type": "string" },
                "description": "Strings to watch for in background process output."
            },
            "workdir": {
                "type": "string",
                "description": "Working directory for this command (absolute path)."
            }
        },
        "required": ["command"]
    }
}

CRONJOB_SCHEMA = {
    "name": "cronjob",
    "description": "Manage scheduled cron jobs with a single compressed tool.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "One of: create, list, update, pause, resume, remove, run."
            },
            "job_id": {
                "type": "string",
                "description": "Required for update/pause/resume/remove/run"
            },
            "schedule": {
                "type": "string",
                "description": "REQUIRED for action=create."
            },
            "prompt": {
                "type": "string",
                "description": "For create: the full self-contained prompt."
            },
            "skills": {
                "type": "array",
                "items": { "type": "string" },
                "description": "Optional ordered list of skill names to load before executing the cron prompt."
            },
            "script": {
                "type": "string",
                "description": "Optional path to a script that runs each tick."
            },
            "no_agent": {
                "type": "boolean",
                "default": False,
                "description": "Default: False. Set True to skip the LLM entirely."
            },
            "deliver": {
                "type": "string",
                "description": "Omit this parameter to auto-deliver back to the current chat."
            },
            "context_from": {
                "type": "array",
                "items": { "type": "string" },
                "description": "Optional job ID or list of job IDs whose most recent completed output is injected into the prompt as context."
            },
            "enabled_toolsets": {
                "type": "array",
                "items": { "type": "string" },
                "description": "Optional list of toolset names to restrict the job's agent to."
            },
            "workdir": {
                "type": "string",
                "description": "Optional absolute path to run the job from."
            },
            "profile": {
                "type": "string",
                "description": "Optional Hermes profile name."
            }
        },
        "required": ["action"]
    }
}

DELEGATE_TASK_SCHEMA = {
    "name": "delegate_task",
    "description": "Spawn one or more subagents to work on tasks in isolated contexts. Each subagent gets its own conversation, terminal session, and toolset. Only the final summary is returned.",
    "parameters": {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": "What the subagent should accomplish."
            },
            "context": {
                "type": "string",
                "description": "Background information the subagent needs."
            },
            "role": {
                "type": "string",
                "enum": ["leaf", "orchestrator"],
                "description": "Role of the child agent."
            },
            "toolsets": {
                "type": "array",
                "items": { "type": "string" },
                "description": "Toolsets to enable for this subagent."
            },
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "goal": { "type": "string", "description": "Task goal" },
                        "context": { "type": "string", "description": "Task-specific context" },
                        "role": { "type": "string", "enum": ["leaf", "orchestrator"] },
                        "toolsets": { "type": "array", "items": { "type": "string" } }
                    },
                    "required": ["goal"]
                },
                "description": "Batch mode: tasks to run in parallel."
            }
        }
    }
}

# -----------------------------------------------------------------------------
# Tool Registration
# -----------------------------------------------------------------------------

def register_tools() -> None:
    registry.register("send_slack_dm", SEND_SLACK_DM_SCHEMA, send_slack_dm_handler)
    registry.register("send_email", SEND_EMAIL_SCHEMA, send_email_handler)

    # Meetings Tools
    registry.register("list_meetings", LIST_MEETINGS_SCHEMA, list_meetings_handler)
    registry.register("get_meeting", GET_MEETING_SCHEMA, get_meeting_handler)
    registry.register("create_meeting", CREATE_MEETING_SCHEMA, create_meeting_handler)

    # Hermes Tools
    registry.register("skill_manage", SKILL_MANAGE_SCHEMA, skill_manage_handler)
    registry.register("skills_list", SKILLS_LIST_SCHEMA, skills_list_handler)
    registry.register("skill_view", SKILL_VIEW_SCHEMA, skill_view_handler)
    registry.register("memory", MEMORY_SCHEMA, memory_handler)
    registry.register("todo", TODO_SCHEMA, todo_handler)
    registry.register("session_search", SESSION_SEARCH_SCHEMA, session_search_handler)
    registry.register("read_file", READ_FILE_SCHEMA, read_file_handler)
    registry.register("write_file", WRITE_FILE_SCHEMA, write_file_handler)
    registry.register("patch", PATCH_SCHEMA, patch_handler)
    registry.register("send_message", SEND_MESSAGE_SCHEMA, send_message_handler)
    registry.register("terminal", TERMINAL_SCHEMA, terminal_handler)
    registry.register("cronjob", CRONJOB_SCHEMA, cronjob_handler)
    registry.register("delegate_task", DELEGATE_TASK_SCHEMA, delegate_task_handler)

# Auto register on import
register_tools()
