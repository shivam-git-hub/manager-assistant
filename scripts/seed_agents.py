"""Admin, one-time (re-runnable): loads agents_pool.json (gitignored -- see
agents_pool.json.example) and upserts each entry into the control-plane
Agent table (step 17 piece 2a). Each entry corresponds to a Slack app the
admin pre-created manually at api.slack.com/apps (subproblem 0 --
out-of-band, this script does not create Slack apps, only rows).

Usage: .venv/bin/python3 -m scripts.seed_agents [path/to/agents_pool.json]
"""
import json
import sys
from pathlib import Path

from app.config import BASE_DIR
from app.controlplane.models import SessionLocal, Agent, init_controlplane_db


def seed(path: Path) -> None:
    init_controlplane_db()

    with open(path, "r", encoding="utf-8") as f:
        entries = json.load(f)

    db = SessionLocal()
    try:
        for entry in entries:
            agent_id = entry["id"]
            agent = db.get(Agent, agent_id)
            if agent is None:
                agent = Agent(id=agent_id)
                db.add(agent)
            agent.name = entry["name"]
            agent.slack_app_id = entry["slack_app_id"]
            agent.slack_client_id = entry["slack_client_id"]
            agent.slack_client_secret = entry["slack_client_secret"]
            agent.slack_signing_secret = entry["slack_signing_secret"]
            print(f"seeded agent: {agent_id} ({entry['name']})")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "agents_pool.json"
    seed(BASE_DIR / arg if not Path(arg).is_absolute() else Path(arg))
