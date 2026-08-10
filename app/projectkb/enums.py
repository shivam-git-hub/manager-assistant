from enum import Enum


class JobName(str, Enum):
    """The fixed projectkb background jobs. Used as the key in
    job_schedule.json so job identifiers live in one place instead of being
    repeated as string literals."""

    INGESTION = "ingestion"
    HEARTBEAT = "heartbeat"
    DREAM = "dream"
    LINT = "lint"
    OUTLOOK_POLL = "outlook_poll"  # arrival cadence for Outlook (poll, not push) -- distinct from the extraction cadence above
    SLACK_POLL = "slack_poll"  # user-token DM/channel polling -- distinct from the bot-token webhook
    AGENT_HEARTBEAT = "agent_heartbeat"  # acts on events/tasks/meetings already produced by heartbeat/dream
