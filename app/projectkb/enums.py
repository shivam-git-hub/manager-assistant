from enum import Enum


class JobName(str, Enum):
    """The four fixed projectkb background jobs. Used as the key in
    job_schedule.json and in job_state.json (last-run tracking) so job
    identifiers live in one place instead of being repeated as string
    literals. Not to be confused with the future agent-creatable scheduling
    system (a separate, later design problem)."""

    INGESTION = "ingestion"
    HEARTBEAT = "heartbeat"
    DREAM = "dream"
    LINT = "lint"
    OUTLOOK_POLL = "outlook_poll"  # arrival cadence for Outlook (poll, not push) -- distinct from the extraction cadence above
    SLACK_POLL = "slack_poll"  # user-token DM polling (step 17 piece 1) -- distinct from the bot-token webhook
    AGENT_HEARTBEAT = "agent_heartbeat"  # step 28: acts on events/tasks/meetings already produced by heartbeat/dream


class TodoStatus(str, Enum):
    OPEN = "open"
    DONE = "done"


class ConflictSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TimelineSection(str, Enum):
    """Heading text for timeline.md's three tiers. Both the template writer
    (paths.py) and any future job that parses/rewrites a section (dream.py)
    must use these values rather than hardcoding the heading string."""

    CURRENT_STATE = "Current State"
    KEY_EVENTS_SUMMARY = "Key Events & Summary"
    HISTORY = "History"
