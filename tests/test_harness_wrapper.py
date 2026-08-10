"""app.agent.harness.run_agent as a thin wrapper over app.agent.runner.run_spec
(step 33). tests/test_agent.py, tests/test_agent_tools.py, and
tests/test_agent_heartbeat_job.py cover the "external behaviour unchanged"
contract end-to-end; this file covers the one behaviour that couldn't be
exercised there: an LLM-call exception must still propagate out of
run_agent (never get swallowed into a graceful apology reply), since
run_spec itself now catches that exception internally."""
import pytest

from app.agent.gemini_client import GeminiClient
from app.agent.harness import run_agent


def test_llm_error_propagates_out_of_run_agent(db_session, client):
    def boom(url, payload):
        raise RuntimeError("upstream exploded")

    fake_client = GeminiClient(api_key="fake", transport=boom)

    with pytest.raises(RuntimeError, match="upstream exploded"):
        run_agent(db_session, client.manager_id, "hi", [], client=fake_client)
