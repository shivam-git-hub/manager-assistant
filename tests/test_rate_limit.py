"""app.agent.rate_limit.RateLimiter -- process-wide token bucket for
outbound Gemini calls. The 0-disabled path is what the rest of the suite
relies on (LLM_MAX_CALLS_PER_MINUTE=0 in tests/conftest.py) so no test ever
sleeps; this file pins that contract plus the active-limiting path using
timeout=0 (never a real sleep)."""
from app.agent.rate_limit import RateLimiter, get_rate_limiter


def test_disabled_limiter_never_blocks():
    limiter = RateLimiter(max_calls_per_minute=0)
    for _ in range(50):
        assert limiter.acquire(timeout=0) is True


def test_limiter_admits_up_to_cap_then_rejects_with_zero_timeout():
    limiter = RateLimiter(max_calls_per_minute=3)
    assert limiter.acquire(timeout=0) is True
    assert limiter.acquire(timeout=0) is True
    assert limiter.acquire(timeout=0) is True
    # 4th call within the same 60s window with no time to wait -> rejected.
    assert limiter.acquire(timeout=0) is False


def test_negative_max_calls_also_disables():
    limiter = RateLimiter(max_calls_per_minute=-1)
    assert limiter.acquire(timeout=0) is True


def test_get_rate_limiter_singleton_reads_test_env_as_disabled():
    # tests/conftest.py sets LLM_MAX_CALLS_PER_MINUTE=0 before app.config is
    # first imported by the suite -- the module singleton must reflect that,
    # not some hardcoded default, or every test using the shared client
    # would risk sleeping.
    limiter = get_rate_limiter()
    for _ in range(50):
        assert limiter.acquire(timeout=0) is True
