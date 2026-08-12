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

# Agent pool -- single shared admin-issued code gating who can claim a pre-created pool bot.
AGENT_POOL_ACCESS_CODE = os.getenv("AGENT_POOL_ACCESS_CODE")

# Slack "reader" app (see the Agent/Employee docstrings in
# app/controlplane/models.py) -- ONE Slack app, shared by every
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

smart_model_val = "gemini-3.5-flash"
flash_model_val = "gemini-3.5-flash-lite"
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

# Company-laptop environment: "safechain" routes every LLM call through
# app.agent.safechain_client (enterprise LangChain gateway) instead of
# GeminiClient -- see app.agent.gemini_client.get_client(). Read directly
# via os.getenv at the two call sites that need it (gemini_client.py,
# safechain_client.py) rather than as a constant here, so nothing in this
# module needs to know safechain exists; documented here for discoverability.
# LLM_PROVIDER=safechain also requires CONFIG_PATH and DEPLOY_ENV to be set
# (safechain's own ee_config.Config.from_env() reads those, not this app)
# and SAFECHAIN_MODEL_INDEX to select the YAML `models:` catalog entry --
# see .env.example.

# Corporate egress proxy (Amex-laptop specific). Used by the Slack connector
# for every httpx call (chat.postMessage, conversations.*) since slack.com
# isn't reachable directly from inside the corp network. Empty/unset is a
# no-op everywhere it's used (off-network / non-corporate deployments).
AEXP_PROXY_URL = os.getenv("AEXP_PROXY_URL") or None
# The proxy does TLS interception with a self-signed root cert on some
# corporate networks -- set true to skip cert verification on Slack calls
# (dev/demo only; never enable this for a real production deployment).
SLACK_INSECURE_SSL = os.getenv("SLACK_INSECURE_SSL", "false").strip().lower() in ("1", "true", "yes")

# Outbound Quiet Hours config (IST)
WORK_HOURS_START = 9
WORK_HOURS_END = 19

# projectkb job cadence defaults -- job_schedule.json (per-deployment
# override) wins over these when present; see app/projectkb/job_schedule.py.
PROJECTKB_POLL_SECONDS = int(os.getenv("PROJECTKB_POLL_SECONDS", "60"))
# Where app.projectkb.scheduler persists each job's last-run timestamp
# across process restarts. Env-overridable (JOB_STATE_FILENAME, same
# pattern as CONTROLPLANE_DB_FILENAME) so tests don't clobber the real file.
JOB_STATE_PATH = DATA_DIR / os.getenv("JOB_STATE_FILENAME", "job_state.json")
INGESTION_INTERVAL_MINUTES = float(os.getenv("INGESTION_INTERVAL_MINUTES", "15"))
INGESTION_MESSAGE_THRESHOLD = int(os.getenv("INGESTION_MESSAGE_THRESHOLD", "10"))
# Max messages the ingest job will send to the LLM in a SINGLE
# CALL -- a thread larger than this is split into consecutive chunks (never
# reordered, never merged across threads). Distinct from
# INGESTION_MESSAGE_THRESHOLD above (which the scheduler doesn't currently
# read at all).
INGESTION_BATCH_SIZE = int(os.getenv("INGESTION_BATCH_SIZE", "30"))
# Soft char budget per LLM call, applied alongside INGESTION_BATCH_SIZE when
# chunking a thread -- whichever limit is hit first ends the chunk. A single
# message longer than this on its own still gets sent as its own one-message
# chunk (best-effort; not truncated) rather than being dropped or split.
INGESTION_MAX_CHARS_PER_CALL = int(os.getenv("INGESTION_MAX_CHARS_PER_CALL", "20000"))
# Max LLM calls (chunks, across all survivor threads combined) the ingest
# job will make in a single run -- a safety ceiling for a large backlog
# (e.g. first ingestion after connecting a mailbox with years of history).
# Everything within this run's survivor set gets processed across possibly
# many calls; only calls beyond this ceiling wait for the next tick.
INGESTION_MAX_CALLS_PER_RUN = int(os.getenv("INGESTION_MAX_CALLS_PER_RUN", "40"))
HEARTBEAT_INTERVAL_MINUTES = float(os.getenv("HEARTBEAT_INTERVAL_MINUTES", "60"))
# Max unprocessed claims the heartbeat job will judge into events
# in a single run -- overflow waits for next tick, same shape as
# INGESTION_BATCH_SIZE above.
HEARTBEAT_CLAIM_BATCH_SIZE = int(os.getenv("HEARTBEAT_CLAIM_BATCH_SIZE", "40"))
DREAM_INTERVAL_MINUTES = int(os.getenv("DREAM_INTERVAL_MINUTES", "1440"))
# Max un-dreamed Event rows the dream job will synthesize in a
# single run -- overflow waits for next tick, same shape as the other
# jobs' batch caps.
DREAM_EVENT_BATCH_SIZE = int(os.getenv("DREAM_EVENT_BATCH_SIZE", "200"))
LINT_INTERVAL_MINUTES = int(os.getenv("LINT_INTERVAL_MINUTES", "10080"))
# Staleness threshold (Part 1's deterministic check 4): an owned project's
# summary.md/events.md untouched for this many days WHILE new events kept
# arriving for it is a lint finding -- a signal the dream job isn't keeping
# that project's synthesis current, not that the project itself is idle.
LINT_STALE_PROJECT_DAYS = int(os.getenv("LINT_STALE_PROJECT_DAYS", "14"))
OUTLOOK_POLL_INTERVAL_MINUTES = int(os.getenv("OUTLOOK_POLL_INTERVAL_MINUTES", "5"))
SLACK_POLL_INTERVAL_MINUTES = int(os.getenv("SLACK_POLL_INTERVAL_MINUTES", "5"))
# Slack channel/group top-level messages (no thread_ts -- Slack itself
# gives no conversation signal for these) are grouped into the same
# ingestion thread_key as the channel's own most recent message if it's
# within this many minutes; otherwise a new thread session starts. Mirrors
# how a DM is always one thread and a real reply-in-thread always groups --
# see app.integrations.slack.SlackConnector.normalize.
SLACK_CHANNEL_SESSION_GAP_MINUTES = int(os.getenv("SLACK_CHANNEL_SESSION_GAP_MINUTES", "30"))

# Personal agent. RESERVED/UNUSED: agent_heartbeat is deliberately NOT
# registered in app.projectkb.scheduler's _JOBS (it sends real Slack DMs,
# so it's manual-trigger-only -- see that job's own docstring), so nothing
# in this codebase currently reads this constant on any automatic cadence.
# Kept for when/if an automatic tick is deliberately added later; the
# "frequent enough for the 2h pre-meeting-brief window" reasoning below
# describes the interval it WOULD run at, not a live cadence today.
AGENT_HEARTBEAT_INTERVAL_MINUTES = float(os.getenv("AGENT_HEARTBEAT_INTERVAL_MINUTES", "30"))
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

# Agentic runner foundation (step 33). Process-wide Gemini call rate cap --
# 15/min is comfortably under typical free/low-tier Gemini RPM limits while
# still letting a heartbeat/dream pass over several managers run without
# stalling. 0 disables the limiter entirely (tests set this so no test ever
# sleeps -- see tests/conftest.py).
LLM_MAX_CALLS_PER_MINUTE = int(os.getenv("LLM_MAX_CALLS_PER_MINUTE", "15"))
# Default per-tool result cap (app.agent.registry.ToolRegistry.register) --
# small enough that a handful of tool calls plus the KB context still fit
# comfortably under a model's context window, large enough that a normal
# list_tasks/list_team result never needs compaction.
AGENT_DEFAULT_TOOL_RESULT_CHARS = int(os.getenv("AGENT_DEFAULT_TOOL_RESULT_CHARS", "4000"))
# Per-agent-spec LLM call ceilings for the future KB agents (heartbeat/dream/
# lint job rewrites, Phase B+) -- defined now so those steps only wire an
# AgentSpec, never invent a new budget knob. User-level and project-fanout
# passes get separate ceilings since project fan-out runs once per
# project-with-events, not once per manager.
KB_HEARTBEAT_MAX_LLM_CALLS = int(os.getenv("KB_HEARTBEAT_MAX_LLM_CALLS", "6"))
KB_HEARTBEAT_PROJECT_MAX_LLM_CALLS = int(os.getenv("KB_HEARTBEAT_PROJECT_MAX_LLM_CALLS", "5"))
KB_DREAM_MAX_LLM_CALLS = int(os.getenv("KB_DREAM_MAX_LLM_CALLS", "6"))
KB_DREAM_PROJECT_MAX_LLM_CALLS = int(os.getenv("KB_DREAM_PROJECT_MAX_LLM_CALLS", "6"))
KB_LINT_MAX_LLM_CALLS = int(os.getenv("KB_LINT_MAX_LLM_CALLS", "4"))
KB_SYNTHESIS_MAX_LLM_CALLS = int(os.getenv("KB_SYNTHESIS_MAX_LLM_CALLS", "12"))
# Wall-clock ceiling for one KB agent run (app.agent.runner.run_spec's
# deadline_seconds) -- generous enough to cover several tool-call round
# trips plus Gemini's own latency without letting one stuck manager stall a
# whole scheduler pass indefinitely.
KB_AGENT_DEADLINE_SECONDS = float(os.getenv("KB_AGENT_DEADLINE_SECONDS", "180"))
