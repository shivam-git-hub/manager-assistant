# Connecting Slack

Harry observes the **manager's own Slack DMs** (not a general-purpose bot in
every channel). As of step 14, connecting Slack is a **multi-tenant OAuth
install flow**: each manager installs the same Slack app into their own
workspace via `GET /auth/slack/install`, and the resulting per-workspace
bot token is stored in the control-plane DB, not a single shared `.env`
value. This is "connect," not "login" -- the manager must already be signed
in (via Outlook, step 13) before installing Slack.

## 1. Create the Slack app (one-time, by whoever runs this deployment)

1. Go to https://api.slack.com/apps -> "Create New App" -> "From scratch".
2. Name it (e.g. "Harry") -- this app definition is shared across all
   managers; each of them installs *this same app* into their own
   workspace, they don't each create their own Slack app.

## 2. Bot token scopes

Under **OAuth & Permissions**, add these Bot Token Scopes:

- `im:history` -- read DM messages
- `im:read` -- see DM channel metadata
- `chat:write` -- send messages (Harry's own outbound pings)
- `users:read` -- resolve user IDs to names (optional, we do this via our
  own `team_members` table instead, but it helps debugging)

## 3. OAuth redirect URL (multi-tenant install)

Still under **OAuth & Permissions**, add a Redirect URL:

```
http://localhost:3003/auth/slack/callback
```

(add your real deployed URL too, once there is one). This is what lets
`GET /auth/slack/install` send a manager to Slack's consent screen and get
routed back to `/auth/slack/callback` afterward, instead of a one-time
manual "Install to Workspace" click.

Under **Basic Information -> App Credentials**, copy the **Client ID** and
**Client Secret** -- these are `SLACK_CLIENT_ID` / `SLACK_CLIENT_SECRET`,
needed to complete each manager's install handshake.

## 4. Making the manager's DMs visible to the bot

Slack bots only see conversations they're a member of. There is no
API-level "observe another user's inbox" for a plain bot token. The
practical options, in order of how much setup they need:

- **Simplest, manual per-conversation**: the manager invites the Harry bot
  into each relevant DM as needed. Doesn't scale to "every DM automatically."
- **Recommended for real use**: request `im:history`/`im:read`/`chat:write`
  under **User Token Scopes** too (not just Bot Token Scopes) so the
  installing manager can grant a *user* token instead. The resulting user
  token acts as the manager's own account, so `im:history` genuinely
  returns their DMs. Our connector code doesn't care which kind of token a
  `SlackInstallation.bot_token` holds, only that it's a valid Bearer token
  for the Slack Web API -- if you go this route, `oauth.v2.access`'s
  response carries it under `authed_user.access_token` instead of the
  top-level `access_token` (the install callback currently reads the
  top-level bot token; switching to a user token needs that one-line change
  in `app/controlplane/slack_auth.py`).
- **Enterprise Grid**: the Discovery API can observe org-wide conversations,
  but that's out of scope here (heavy, admin-only).

## 5. Event subscriptions (webhook)

Under **Event Subscriptions**, turn this on and set the Request URL to:

```
https://<your-deployed-host>/api/integrations/slack/webhook
```

Slack will send a `url_verification` challenge first -- our webhook handles
that automatically. Subscribe to the bot event `message.im` (DM messages).
This is one shared endpoint for every connected workspace -- incoming
events carry `team_id` at the top level, which the webhook handler uses to
look up which manager's `SlackInstallation` the event belongs to.

## 6. Signing secret

Under **Basic Information -> App Credentials**, copy the **Signing
Secret** -- this is `SLACK_SIGNING_SECRET`. It's a single app-level value
(not per-installation) used to verify that incoming webhook requests
genuinely came from Slack (HMAC-SHA256 over the request body), regardless
of which workspace they came from.

## 7. Environment variables

Add to `.env`:

```
SLACK_CLIENT_ID=...
SLACK_CLIENT_SECRET=...
SLACK_SIGNING_SECRET=...
SLACK_REDIRECT_URI=http://localhost:3003/auth/slack/callback
```

Without `SLACK_SIGNING_SECRET` and at least one connected workspace, the
connector runs in "unconfigured" mode: webhook signature checks are skipped
(with a warning) and outbound sends are recorded in the database but not
actually delivered.

## 8. Connecting a workspace

With the app running and a manager already signed in (see OUTLOOK.md),
visit `GET /auth/slack/install` in a browser (or click "Connect Slack" on
`/connect.html`). You're redirected to Slack, pick the workspace, approve
the scopes, and land back on `/connect.html?connected=slack&workspace=...`.
This creates (or updates) a `SlackInstallation(team_id, manager_id,
bot_token)` row in the control-plane DB (`data/controlplane.sqlite`).

## 9. The manager's own team_members row

For the manager-only filtering to work, there must be a `TeamMember` row
whose `role` contains "manager" (case-insensitive) and whose `slack_handle`
matches their real Slack user ID. Create/update via `POST /api/team`.

## 10. Tracked contacts

Only DMs whose *other* participant is on the tracked-contacts list get
stored. Add people via `POST /api/projectkb/tracked-contacts` with a
`slack_pattern` (exact Slack user ID, or a glob if you have a convention
for matching multiple IDs -- rare for Slack since IDs aren't structured
like emails).
