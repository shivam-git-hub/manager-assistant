"""Process-wide token bucket for outbound Gemini calls. Every KB agent
(chat, and eventually the heartbeat/dream/lint rewrites) shares one
GeminiClient call path (gemini_client.py::GeminiClient.chat), so a single
process-wide limiter here is enough to keep a scheduler pass that fans out
over many managers from bursting past Gemini's own RPM ceiling -- no
per-caller bookkeeping needed.

Uses time.monotonic(), never app.timeservice.now_ist(): now_ist() is
wall-clock IST for DOMAIN stamping (event/claim timestamps) and tests
monkeypatch it freely to pin simulated moments in time. A rate limiter
measures real elapsed wall-clock time between calls -- if it read
now_ist(), a test that freezes now_ist() at a fixed instant would make the
bucket see zero elapsed time forever and either never admit a call or never
refill. This is a deliberate, narrow exception to CLAUDE.md's "always via
now_ist()" rule, not a violation of it (that rule is about naive-IST domain
timestamps, not internal rate-limiting arithmetic).
"""
import threading
import time
from collections import deque
from typing import Optional


class RateLimiter:
    """Sliding-window token bucket: at most `max_calls_per_minute` calls may
    be admitted in any trailing 60-second window. `max_calls_per_minute <= 0`
    makes every acquire() an immediate no-op True -- the disabled path tests
    rely on so the suite never sleeps."""

    def __init__(self, max_calls_per_minute: int):
        self._max_calls_per_minute = max_calls_per_minute
        self._lock = threading.Lock()
        self._call_times: deque[float] = deque()

    def acquire(self, timeout: Optional[float] = None) -> bool:
        """Blocks (polling) until a slot frees, or `timeout` seconds have
        elapsed, whichever comes first. Returns True once admitted, False if
        the timeout elapsed without a free slot. `timeout=None` blocks
        indefinitely."""
        if self._max_calls_per_minute <= 0:
            return True

        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                now = time.monotonic()
                while self._call_times and now - self._call_times[0] >= 60.0:
                    self._call_times.popleft()
                if len(self._call_times) < self._max_calls_per_minute:
                    self._call_times.append(now)
                    return True
                wait_for = 60.0 - (now - self._call_times[0])

            if deadline is not None and time.monotonic() + wait_for >= deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                time.sleep(min(remaining, wait_for))
                if time.monotonic() >= deadline:
                    return False
                continue

            time.sleep(min(wait_for, 1.0))


# Module singleton -- constructed lazily (not at import time) so
# LLM_MAX_CALLS_PER_MINUTE is read fresh from app.config, which itself
# resolves env at import time; tests that set the env var before importing
# app.config (see tests/conftest.py) see the value they set, never a value
# frozen by import order.
_limiter_singleton: Optional[RateLimiter] = None
_singleton_lock = threading.Lock()


def get_rate_limiter() -> RateLimiter:
    global _limiter_singleton
    if _limiter_singleton is None:
        with _singleton_lock:
            if _limiter_singleton is None:
                from app.config import LLM_MAX_CALLS_PER_MINUTE

                _limiter_singleton = RateLimiter(LLM_MAX_CALLS_PER_MINUTE)
    return _limiter_singleton
