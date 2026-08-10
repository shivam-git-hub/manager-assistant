# Pulse.ai — Manager Assistant ("Harry")

FastAPI + SQLite backend with a React manager-facing frontend. Harry connects
to Slack + Outlook, maintains a knowledge pipeline (`message → claim → event →
notification`), and acts autonomously (follow-ups, deadlock detection, status
inference, manager updates).

See `CLAUDE.md` for the architecture reference.

---

## Run

```bash
# Backend (port 3003, or the PORT env var)
.venv/bin/python3 -m app.main

# Frontend dev server (port 5173, proxies /api and /auth to :3003)
cd frontend && npm run dev
```

Sign in at `/auth/outlook/login`, or use dev-login (`POST /api/auth/dev-login`,
gated by `DEV_AUTH_ENABLED`). Connect Slack/Outlook from the Connectors page.

## Test

```bash
.venv/bin/python3 -m pytest tests/
```

## Codebase structure

```
manager-assistant/
├── app/                        # FastAPI source code
│   ├── config.py               # Env-backed settings: ports, paths, model tiers, job intervals
│   ├── database.py             # Per-manager db.sqlite table definitions
│   ├── timeservice.py          # now_ist() -- the only sanctioned clock read
│   ├── main.py                 # FastAPI init, lifespan schema sweeps, static mounting
│   ├── controlplane/           # Global DB: identity, sessions, agent pool,
│   │                           #   employee directory, projects registry, OAuth flows
│   ├── tenancy/                # Per-manager directory + engine/session provisioning
│   ├── projects/               # Per-project directory, db.sqlite, and tables
│   ├── integrations/           # ChannelConnector base + Slack/Outlook connectors
│   ├── projectkb/              # Background jobs + scheduler + blocklist
│   │   └── jobs/               # ingestion, heartbeat, dream, lint, polls, agent_heartbeat
│   ├── agent/                  # Personal agent: harness, tools, context, prompts
│   ├── api/                    # Manager-scoped REST routers
│   └── static/                 # No-build pages: login, connect, debug
├── frontend/                   # React + Vite manager-facing product UI
├── tests/                      # pytest suite
├── scripts/                    # Admin seed scripts (agents, employees, demo data)
└── requirements.txt            # Core python requirements
```

## Setup docs

- `app/integrations/OUTLOOK.md` — Azure app registration and Graph auth modes
- `app/integrations/SLACK.md` — the reader app and the agent bot pool
