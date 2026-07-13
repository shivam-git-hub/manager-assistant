import os
import json
import logging
from pathlib import Path
import pytz

logger = logging.getLogger(__name__)

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DATABASE_URL = f"sqlite:///{DATA_DIR}/db.sqlite"

# Timezone
IST = pytz.timezone("Asia/Kolkata")

# Server Config
HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "3003"))

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
