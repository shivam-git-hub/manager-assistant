from datetime import datetime
import pytz
from fastapi import APIRouter

from app.config import IST


def now_ist() -> datetime:
    """The one call site for wall-clock reads. Returns the current naive
    IST datetime -- real time, always. THE replacement for
    datetime.now(IST).replace(tzinfo=None)/datetime.utcnow()/time.time()
    everywhere else in the app (see test_07_wall_clock_guard)."""
    return datetime.now(pytz.UTC).astimezone(IST).replace(tzinfo=None)


def now_epoch() -> float:
    """now_ist() as a Unix epoch float."""
    return IST.localize(now_ist()).timestamp()


def now_utc_iso() -> str:
    """now_ist() as a UTC ISO 8601 string with a 'Z' suffix -- the shape MS
    Graph's receivedDateTime uses."""
    return IST.localize(now_ist()).astimezone(pytz.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


router = APIRouter(prefix="/api/time", tags=["Time"])


@router.get("", response_model=dict)
def get_time():
    """Current real IST wall-clock time -- read-only, informational (used
    by the debug page). There is no simulated/settable clock in this
    codebase; tests control time via the set_sim_time fixture instead,
    which monkeypatches this module's now_ist directly."""
    now = now_ist()
    return {
        "sim_time_ist": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "epoch": now_epoch(),
        "utc_iso": now_utc_iso(),
    }
