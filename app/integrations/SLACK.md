# Connecting Slack

Slack is two completely independent concerns in Pulse.ai. Keeping them
separate (redesigned 2026-07-23, after step 17's original agent-pool
design conflated them) is the point of this document -- read both
sections even if you only care about one.

1. **Reading** -- tracking a manager's own messages into the knowledge
   base. One single shared Slack app ("the reader"), a user-token OAuth
   grant per manager, nothing to do with bots.
2. **Agents** -- a pool of separately-registered, admin-installed Slack
   apps ("bots") that managers can claim. Claiming is pure database
   bookkeeping; installing a bot into the workspace is a one-time admin
   task done entirely on Slack's own site, no OAuth code in this app is
   involved.

A manager's Slack messages get tracked whether or not they've ever
claimed an agent, and a claimed agent works whether or not its manager
has connected reading. Neither gates the other.

## Part 1: Reading (the "reader" app)

### 1. Create the app (one-time)

1. Go to https://api.slack.com/apps -> "Create New App" -> "From scratch".
2. Name it (e.g. "Pulse Reader"). This ONE app is shared by every manager
   -- each of them authorizes it for their own identity, they don't each
   create an app.
3. Under **OAuth & Permissions**, add a Redirect URL:
   ```
   http://localhost:3003/auth/slack/callback
   ```
4. Under **OAuth & Permissions -> User Token Scopes** (not Bot Token
   Scopes -- this app never installs a bot presence), add:
   - `im:history` -- a manager's own DMs
   - `im:read` -- DM channel metadata
   - `mpim:history` -- group DMs
   - `groups:history` -- private channels the manager is in
   - `channels:history` -- public channels the manager is in
5. Under **Basic Information -> App Credentials**, copy the **Client ID**
   and **Client Secret**.

### 2. Environment variables

```
SLACK_READER_CLIENT_ID=...
SLACK_READER_CLIENT_SECRET=...
SLACK_REDIRECT_URI=http://localhost:3003/auth/slack/callback
```

### 3. Connecting

A logged-in manager visits `GET /auth/slack/install` (the Connectors
page's Slack "Grant" button) -- no agent claim required. They land on
Slack's consent screen, approve, and land back on
`/connectors?connected=slack&workspace=...`. This writes a
`SlackReaderInstallation(manager_id, team_id, user_token)` row in the
control-plane DB. `app/projectkb/jobs/slack_poll.py` polls with that
token on the usual job cadence -- see app/controlplane/slack_auth.py.

`POST /auth/slack/disconnect` revokes the user token and forgets it.
This has no effect on any agent the manager has claimed.

## Part 2: Agents (the bot pool)

### 1. Create each pool app (one-time per agent, by the admin)

1. https://api.slack.com/apps -> "Create New App" -> "From scratch", named
   distinctly (e.g. "Atlas", "Nova") -- recipients see whose bot they're
   talking to, so each pool slot is its own real Slack app identity.
2. Under **OAuth & Permissions -> Bot Token Scopes**, add:
   - `im:history` -- read DMs sent to the bot
   - `im:read` -- DM channel metadata
   - `chat:write` -- send messages org-wide
3. Under **Event Subscriptions**, turn this on, set the Request URL to
   `https://<host>/api/integrations/slack/webhook` (Slack's
   `url_verification` challenge is handled automatically), and subscribe
   to the bot event `message.im`.
4. Under **Basic Information -> App Credentials**, copy the **App ID**,
   **Client ID**, **Client Secret**, and **Signing Secret**.

### 2. Install each app to the workspace (one-time, admin, no OAuth code)

Still on that app's **OAuth & Permissions** page, click **Install to
Workspace** and approve. Slack then displays the **Bot User OAuth
Token** (`xoxb-...`) directly on that page -- copy it. This is the whole
installation step; nothing in this codebase performs an OAuth exchange
for agents.

### 3. Seed the pool

Fill in `agents_pool.json` (gitignored -- see `agents_pool.json.example`)
with each agent's `id`/`name`/`slack_app_id`/`slack_client_id`/
`slack_client_secret`/`slack_signing_secret`, plus `bot_token`/`team_id`
for any agent you've already installed (step 2) -- an entry without them
seeds a claimable-but-not-yet-installed agent. Then:

```
.venv/bin/python3 -m scripts.seed_agents
```

Re-run any time you add agents or finish installing one (it upserts by
`id`, safe to re-run).

### 4. Claiming

A manager sees unclaimed agents (`GET /api/agents/available`, no code
needed) and claims one with the admin-issued access code
(`AGENT_POOL_ACCESS_CODE` env var, `POST /api/agents/claim`) -- see
`app/controlplane/agents.py`. This only writes `Agent.manager_id`; it
never talks to Slack. If the claimed agent isn't installed yet (step 2
still pending), the claim still succeeds, it just doesn't do anything
useful until the admin finishes installing it.

### 5. The manager's own team_members row

For the manager-only filtering used elsewhere in the KB to work, there
must be a `TeamMember` row whose `role` contains "manager" and whose
`slack_handle` matches the manager's real Slack user ID -- this is set
automatically the first time they connect reading (Part 1), since
`authed_user.id` from that OAuth flow IS their Slack user ID
(`_sync_manager_slack_handle` in `app/controlplane/slack_auth.py`).

### 6. Chatting with a claimed bot

A claimed agent's webhook is already live (routed by `api_app_id` ->
`Agent.slack_app_id`, `app/integrations/slack.py`'s `/webhook`) -- DMing
the bot ingests the message into that manager's KB today. A live
reply -- the bot actually chatting back -- is a separate, not-yet-built
piece (a "personal agent" chat endpoint triggered off this webhook).
Unclaimed agents' webhooks are inert: `resolve_agent_by_app_id` finds no
`manager_id` to route to, so incoming messages are ignored.
