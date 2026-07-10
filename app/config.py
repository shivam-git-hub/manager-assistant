import os
from pathlib import Path
import pytz

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DATABASE_URL = f"sqlite:///{DATA_DIR}/db.sqlite"

# Timezone
IST = pytz.timezone("Asia/Kolkata")

# Server Config
HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "8001"))
