"""GeminiClient.chat retry/backoff behavior (step 33): Retry-After header
honored when present, +/-25% jitter otherwise, only 2 of 3 attempts ever
sleep, and every chat() call goes through the process-wide rate limiter
first."""
import pytest

from app.agent import gemini_client as gemini_client_module
from app.agent.gemini_client import GeminiClient


class _FakeResponse:
    def __init__(self, status_code, headers=None):
        self.status_code = status_code
        self.headers = headers or {}


class _FakeHTTPError(Exception):
    def __init__(self, status_code, headers=None):
        super().__init__(f"status {status_code}")
        self.response = _FakeResponse(status_code, headers)


def _ok_response():
    return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}


def test_retry_after_header_is_honored(monkeypatch):
    calls = {"n": 0}
    sleeps = []

    def transport(url, payload):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _FakeHTTPError(429, headers={"Retry-After": "7.5"})
        return _ok_response()

    monkeypatch.setattr(gemini_client_module.time, "sleep", lambda s: sleeps.append(s))

    client = GeminiClient(api_key="fake", transport=transport)
    result = client.chat(model="gemini-3.5-flash", messages=[{"role": "user", "content": "hi"}])

    assert result["content"] == "ok"
    assert sleeps == [7.5]


def test_backoff_without_retry_after_has_jitter_and_only_two_sleeps(monkeypatch):
    sleeps = []

    def transport(url, payload):
        raise _FakeHTTPError(500)

    monkeypatch.setattr(gemini_client_module.time, "sleep", lambda s: sleeps.append(s))

    client = GeminiClient(api_key="fake", transport=transport)
    with pytest.raises(_FakeHTTPError):
        client.chat(model="gemini-3.5-flash", messages=[{"role": "user", "content": "hi"}])

    # 3 attempts total, but only the first 2 failures sleep before retrying --
    # the 3rd (final) failure re-raises immediately.
    assert len(sleeps) == 2
    # attempt 0 base is 2s, attempt 1 base is 4s; jitter is +/-25%.
    assert 1.5 <= sleeps[0] <= 2.5
    assert 3.0 <= sleeps[1] <= 5.0


def test_json_mode_with_tools_raises_before_any_request():
    calls = []

    def transport(url, payload):
        calls.append(payload)
        return _ok_response()

    client = GeminiClient(api_key="fake", transport=transport)
    tools = [{"type": "function", "function": {"name": "noop", "description": "", "parameters": {"type": "object", "properties": {}}}}]

    with pytest.raises(ValueError, match="json_mode cannot be combined with tools"):
        client.chat(model="gemini-3.5-flash", messages=[{"role": "user", "content": "hi"}], tools=tools, json_mode=True)

    assert calls == []  # never even reached the transport


def test_chat_acquires_rate_limiter_slot(monkeypatch):
    acquired = []

    class _FakeLimiter:
        def acquire(self, timeout=None):
            acquired.append(timeout)
            return True

    monkeypatch.setattr(gemini_client_module, "get_rate_limiter", lambda: _FakeLimiter())

    client = GeminiClient(api_key="fake", transport=lambda url, payload: _ok_response())
    client.chat(model="gemini-3.5-flash", messages=[{"role": "user", "content": "hi"}])

    assert acquired == [None]
