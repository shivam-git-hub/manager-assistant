import os
import json
import logging
from pathlib import Path
import pytz
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

load_dotenv(BASE_DIR / ".env")

# Timezone
IST = pytz.timezone("Asia/Kolkata")

# Server Config
HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "3003"))

# Outlook OAuth (auth-code-redirect flow, see app/controlplane/outlook_auth.py).
# Must exactly match the redirect URI registered on the Azure app's "Web"
# platform -- see app/integrations/OUTLOOK.md.
MS_GRAPH_REDIRECT_URI = os.getenv("MS_GRAPH_REDIRECT_URI", f"http://localhost:{PORT}/auth/outlook/callback")

# Slack OAuth install flow (see app/controlplane/slack_auth.py). Must exactly
# match a Redirect URL registered under the Slack app's OAuth & Permissions
# page -- see app/integrations/SLACK.md.
SLACK_REDIRECT_URI = os.getenv("SLACK_REDIRECT_URI", f"http://localhost:{PORT}/auth/slack/callback")

# Agent pool (step 17 piece 2a, see prompts/step_17_agent_pool.md) -- single
# shared admin-issued code gating who can claim a pre-created pool bot.
AGENT_POOL_ACCESS_CODE = os.getenv("AGENT_POOL_ACCESS_CODE")

# Slack "reader" app (redesigned 2026-07-23, see Agent/SlackReaderInstallation
# docstrings in app/controlplane/models.py) -- ONE Slack app, shared by every
# manager, requesting only a user-token grant to track that manager's own
# messages. Deliberately separate from the Agent pool's per-agent Slack app
# credentials (see app/controlplane/slack_auth.py, app/integrations/SLACK.md).
SLACK_READER_CLIENT_ID = os.getenv("SLACK_READER_CLIENT_ID")
SLACK_READER_CLIENT_SECRET = os.getenv("SLACK_READER_CLIENT_SECRET")

# The Pulse.ai React frontend (frontend/, Vite dev server) -- where post-OAuth
# redirects land the browser. Not the same origin as this backend in dev
# (5173 vs 3003), so this can't be a relative path.
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")

# LLM Config Resolution: Env -> config.json -> Defaults
CONFIG_JSON_PATH = BASE_DIR / "config.json"

smart_model_val = "gemini-2.5-pro"
flash_model_val = "gemini-2.5-flash"
gemini_api_key_val = None

if CONFIG_JSON_PATH.exists():
    try:
        with open(CONFIG_JSON_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            if isinstance(cfg, dict):
                if "smart_model" in cfg:
                    smart_model_val = cfg["smart_model"]
                if "flash_model" in cfg:
                    flash_model_val = cfg["flash_model"]
                # Accept both snake_case and UPPER keys for the API key.
                if cfg.get("gemini_api_key") or cfg.get("GEMINI_API_KEY"):
                    gemini_api_key_val = cfg.get("gemini_api_key") or cfg.get("GEMINI_API_KEY")
            else:
                logger.warning("config.json is not a valid JSON object")
    except Exception as e:
        logger.warning(f"Failed to load or parse config.json: {e}")

SMART_MODEL = os.getenv("SMART_MODEL", smart_model_val)
FLASH_MODEL = os.getenv("FLASH_MODEL", flash_model_val)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", gemini_api_key_val)

# Outbound Quiet Hours config (IST)
WORK_HOURS_START = 9
WORK_HOURS_END = 19

# projectkb job cadence defaults -- job_schedule.json (per-deployment
# override) wins over these when present; see app/projectkb/job_schedule.py.
PROJECTKB_POLL_SECONDS = int(os.getenv("PROJECTKB_POLL_SECONDS", "60"))
INGESTION_INTERVAL_MINUTES = int(os.getenv("INGESTION_INTERVAL_MINUTES", "15"))
INGESTION_MESSAGE_THRESHOLD = int(os.getenv("INGESTION_MESSAGE_THRESHOLD", "10"))
# Max messages the ingest job (step 22) will send to the LLM in a single
# run -- distinct from INGESTION_MESSAGE_THRESHOLD above (which the
# scheduler doesn't currently read at all; this one gates work done PER
# run, not whether a run happens). Overflow waits for the next tick.
INGESTION_BATCH_SIZE = int(os.getenv("INGESTION_BATCH_SIZE", "30"))
HEARTBEAT_INTERVAL_MINUTES = int(os.getenv("HEARTBEAT_INTERVAL_MINUTES", "60"))
# Max unprocessed claims the heartbeat job (step 23) will judge into events
# in a single run -- overflow waits for next tick, same shape as
# INGESTION_BATCH_SIZE above.
HEARTBEAT_CLAIM_BATCH_SIZE = int(os.getenv("HEARTBEAT_CLAIM_BATCH_SIZE", "40"))
DREAM_INTERVAL_MINUTES = int(os.getenv("DREAM_INTERVAL_MINUTES", "1440"))
# Max un-dreamed Event rows the dream job (step 25) will synthesize in a
# single run -- overflow waits for next tick, same shape as the other
# jobs' batch caps.
DREAM_EVENT_BATCH_SIZE = int(os.getenv("DREAM_EVENT_BATCH_SIZE", "200"))
LINT_INTERVAL_MINUTES = int(os.getenv("LINT_INTERVAL_MINUTES", "10080"))
OUTLOOK_POLL_INTERVAL_MINUTES = int(os.getenv("OUTLOOK_POLL_INTERVAL_MINUTES", "5"))
SLACK_POLL_INTERVAL_MINUTES = int(os.getenv("SLACK_POLL_INTERVAL_MINUTES", "5"))

# Personal agent (step 28 -- prompts/step_28_personal_agent.md). Frequent
# enough to catch the 2h pre-meeting-brief window without drifting; most
# ticks have zero candidates (see app/agent/select.py) so the cost of a
# short interval is just a DB scan, not an LLM call.
AGENT_HEARTBEAT_INTERVAL_MINUTES = int(os.getenv("AGENT_HEARTBEAT_INTERVAL_MINUTES", "30"))
# How far ahead a scheduled meeting must be to become a pre-meeting-brief
# candidate.
PRE_MEETING_BRIEF_WINDOW_HOURS = int(os.getenv("PRE_MEETING_BRIEF_WINDOW_HOURS", "2"))
# How long a conflict must sit contacted-but-unresolved before the agent
# escalates it to the manager instead of waiting on the two parties.
CONFLICT_ESCALATE_AFTER_HOURS = int(os.getenv("CONFLICT_ESCALATE_AFTER_HOURS", "24"))
# How far back the agent heartbeat looks at raw Claim text to spot
# unscheduled meeting mentions ("let's connect at 5pm") worth turning into
# a tracked Meeting row.
AGENT_MEETING_SCAN_LOOKBACK_HOURS = int(os.getenv("AGENT_MEETING_SCAN_LOOKBACK_HOURS", "48"))
AGENT_MEETING_SCAN_MAX_CLAIMS = int(os.getenv("AGENT_MEETING_SCAN_MAX_CLAIMS", "20"))
