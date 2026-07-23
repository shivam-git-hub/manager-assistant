import os
import json
import time
import tempfile
import threading
from datetime import datetime, timedelta
import pytz
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from typing import Optional
import logging
logger = logging.getLogger(__name__)

on_time_change = []

def fire_time_change():
    for cb in on_time_change:
        try:
            cb()
        except Exception as e:
            logger.exception(f"Error in on_time_change callback: {e}")


from app.config import IST, DATA_DIR

# Thread-safety lock
_lock = threading.Lock()

# Cached in-memory values to reduce disk I/O, but we must load/save
_anchor_sim_time = None  # datetime naive IST
_anchor_real_time = None  # float UTC epoch

def _get_sim_clock_path() -> str:
    """Dynamically fetch SIM_CLOCK_PATH to support test environment overrides."""
    default_path = str(DATA_DIR / "sim_clock.json")
    return os.getenv("SIM_CLOCK_PATH", default_path)

def _reset_state_for_tests():
    """Helper to reset in-memory cache for test isolation."""
    global _anchor_sim_time, _anchor_real_time
    with _lock:
        _anchor_sim_time = None
        _anchor_real_time = None

def _load_clock():
    global _anchor_sim_time, _anchor_real_time
    
    # If already loaded, return cached
    if _anchor_sim_time is not None and _anchor_real_time is not None:
        return _anchor_sim_time, _anchor_real_time
        
    path = _get_sim_clock_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                sim_str = data["anchor_sim_time"]
                real_epoch = float(data["anchor_real_time"])
                _anchor_sim_time = datetime.fromisoformat(sim_str)
                _anchor_real_time = real_epoch
                return _anchor_sim_time, _anchor_real_time
        except Exception:
            # If reading fails or is malformed, fall back to initialization
            pass
            
    # Initialize state anchored to the current real IST time
    real_now_epoch = time.time()
    real_now_ist = datetime.fromtimestamp(real_now_epoch, tz=pytz.UTC).astimezone(IST).replace(tzinfo=None)
    
    _anchor_sim_time = real_now_ist
    _anchor_real_time = real_now_epoch
    
    _save_clock_unlocked(_anchor_sim_time, _anchor_real_time)
    return _anchor_sim_time, _anchor_real_time

def _save_clock_unlocked(sim_dt: datetime, real_epoch: float):
    global _anchor_sim_time, _anchor_real_time
    _anchor_sim_time = sim_dt
    _anchor_real_time = real_epoch
    
    data = {
        "anchor_sim_time": sim_dt.isoformat(),
        "anchor_real_time": real_epoch
    }
    
    path = _get_sim_clock_path()
    parent_dir = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent_dir, exist_ok=True)
    
    # Write atomically
    fd, temp_path = tempfile.mkstemp(dir=parent_dir, prefix="sim_clock_tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(temp_path, path)
    except Exception as e:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise e

def now_ist() -> datetime:
    """
    Returns the current naive IST datetime -- real wall-clock time, not simulated.
    THE replacement for datetime.now(IST).replace(tzinfo=None)

    2026-07-23: switched from the sim-clock anchor to real time per Shivam's
    request (product is past the demo-storyline phase). The anchor file /
    _load_clock / set_time / advance / reset_to_real and the /api/time
    set|advance|reset endpoints are left in place (the simulator UI's clock
    widget still calls them) but are now inert for anything that matters --
    nothing reads the anchor to compute "now" anymore.
    """
    real_now_epoch = time.time()
    return datetime.fromtimestamp(real_now_epoch, tz=pytz.UTC).astimezone(IST).replace(tzinfo=None)

def now_epoch() -> float:
    """
    Returns the current simulated time as a Unix epoch float.
    Localizes naive IST simulated time to the real UTC timezone to compute epoch.
    """
    sim_now = now_ist()
    # Localize naive IST to active timezone
    localized = IST.localize(sim_now)
    # Convert to UTC and get timestamp
    return localized.timestamp()

def now_utc_iso() -> str:
    """
    Returns the current simulated time formatted as a UTC ISO 8601 string (with 'Z' suffix).
    Suitable for MS Graph receivedDateTime.
    """
    sim_now = now_ist()
    localized = IST.localize(sim_now)
    utc_dt = localized.astimezone(pytz.UTC)
    # Format with 'Z' suffix
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

def set_time(dt: datetime) -> None:
    """
    Sets the simulated clock to a specific naive IST datetime.
    Re-anchors: anchor_sim = dt, anchor_real = real-world time.time().
    """
    with _lock:
        real_epoch = time.time()
        _save_clock_unlocked(dt, real_epoch)

def advance(seconds: int) -> None:
    """
    Moves the simulated clock anchor sim time forward by a given number of seconds.
    Keeps the clock flowing naturally.
    """
    with _lock:
        sim_anchor, real_anchor = _load_clock()
        new_sim_anchor = sim_anchor + timedelta(seconds=seconds)
        _save_clock_unlocked(new_sim_anchor, real_anchor)

def reset_to_real() -> None:
    """
    Resets the simulated clock to align exactly with real-world time.
    """
    with _lock:
        real_epoch = time.time()
        real_now_ist = datetime.fromtimestamp(real_epoch, tz=pytz.UTC).astimezone(IST).replace(tzinfo=None)
        _save_clock_unlocked(real_now_ist, real_epoch)

def get_state() -> dict:
    """
    Returns simulated time properties and anchor states.
    """
    # Grab now values safely without double locking
    sim_now = now_ist()
    localized = IST.localize(sim_now)
    utc_dt = localized.astimezone(pytz.UTC)
    epoch_val = utc_dt.timestamp()
    utc_iso_val = utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    
    with _lock:
        sim_anchor, real_anchor = _load_clock()
        return {
            "sim_time_ist": sim_now.strftime("%Y-%m-%dT%H:%M:%S"),
            "epoch": epoch_val,
            "utc_iso": utc_iso_val,
            "anchor_sim_time": sim_anchor.strftime("%Y-%m-%dT%H:%M:%S"),
            "anchor_real_time": real_anchor
        }

# ────────────────────────────────────────────────────────
# FASTAPI ROUTER AND PYDANTIC SCHEMAS
# ────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/time", tags=["Simulated Time Service"])

class TimeSetRequest(BaseModel):
    datetime: str = Field(description="Naive IST datetime ISO string (YYYY-MM-DDTHH:MM:SS)")

class TimeAdvanceRequest(BaseModel):
    days: int = 0
    hours: int = 0
    minutes: int = 0

@router.get("", response_model=dict)
def get_time():
    """Returns the current simulated clock state."""
    return get_state()

@router.post("/set", response_model=dict)
def api_set_time(payload: TimeSetRequest):
    """Manually sets the simulated time to a specific datetime."""
    try:
        dt = datetime.fromisoformat(payload.datetime)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid datetime format. Expected YYYY-MM-DDTHH:MM:SS"
        )
    if dt.tzinfo is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Datetime must be naive IST (no timezone offset). Expected YYYY-MM-DDTHH:MM:SS"
        )
    set_time(dt)
    fire_time_change()
    return get_state()

@router.post("/advance", response_model=dict)
def api_advance_time(payload: TimeAdvanceRequest):
    """Advances the simulated clock by a specified duration."""
    total_seconds = (
        payload.days * 86400 +
        payload.hours * 3600 +
        payload.minutes * 60
    )
    if total_seconds <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Advance duration must be positive and greater than zero."
        )
    advance(total_seconds)
    fire_time_change()
    return get_state()

@router.post("/reset", response_model=dict)
def api_reset_time():
    """Resets the simulated clock to match the current real time."""
    reset_to_real()
    fire_time_change()
    return get_state()
