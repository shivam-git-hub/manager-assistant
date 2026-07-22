# Task: Slack Workspace Connect — Multi-Tenant OAuth + team_id Webhook Routing

## Context

Repo: `manager-assistant`. Depends on step 12 (auth/sessions) and requires
the manager to already be logged in (via step 13's Outlook flow) before
connecting Slack — Slack is "connect," not "login," per the earlier
decision to keep those separate.

First read: `app/integrations/slack.py` (current single-workspace,
`.env`-token setup), `app/integrations/SLACK.md`, `app/controlplane/models.py`
(from step 12).

**Why webhook stays, not polling:** confirmed with the user — for
multi-tenant Slack, polling would mean enumerating and re-polling every
connected manager's DM channels on a schedule, which hits rate limits fast
and has no clean "list all my DMs since X" primitive. One shared webhook
endpoint, routed by the `team_id` every Slack event payload already
carries, scales to N managers without any of that — keep it.

## Subproblem 1 — Slack app becomes a "distributed" OAuth app, not a single install

Today's setup (`SLACK.md`) is "install to my one workspace, copy the bot
token into `.env`." For multi-tenant, the Slack app needs its own OAuth
install flow so *each* manager can install it into *their* workspace:
- Under **OAuth & Permissions**, add Redirect URL:
  `http://localhost:3003/auth/slack/callback`.
- `SLACK_SIGNING_SECRET` stays a single `.env` value (it's the *app's*
  secret, used to verify every incoming webhook regardless of which
  workspace installed it — not per-installation).
- `SLACK_CLIENT_ID` / `SLACK_CLIENT_SECRET` (new `.env` vars) — the app's
  OAuth credentials, needed to complete each manager's install handshake.
  `SLACK_BOT_TOKEN` as a single global `.env` var goes away — replaced by
  per-installation tokens in the DB (Subproblem 3).

## Subproblem 2 — Install redirect endpoints

`GET /auth/slack/install` (requires `get_current_manager` — must be logged
in first):
- Redirect to `https://slack.com/oauth/v2/authorize` with `client_id`,
  `scope=im:history,im:read,chat:write`, `redirect_uri`, and `state`
  (encode the current manager's id, signed, so the callback knows who's
  installing — same CSRF-protection pattern as step 13's `state`).

`GET /auth/slack/callback?code=...&state=...`:
- Verify `state`, extract `manager_id`.
- Exchange `code` via `POST https://slack.com/api/oauth.v2.access` with
  `client_id`/`client_secret`/`code`/`redirect_uri`.
- Response includes `team.id` and `access_token` (the bot token) — upsert
  `SlackInstallation(team_id, manager_id, bot_token)` (from step 12's schema).
- Redirect to the connect confirmation page (same one step 13 uses).

## Subproblem 3 — Webhook routing by team_id

Slack's Events API payload includes `team_id` at the top level (sibling of
`event`, not inside it — check the real payload shape, current code only
reads `payload.get("event")`). Update `slack_webhook()`:
- Extract `team_id` from the payload.
- Look up `SlackInstallation` by `team_id` → get `manager_id` and that
  installation's `bot_token`.
- If no installation found for this `team_id` → log a warning, return
  `{"status": "ignored", "detail": "unknown workspace"}` (shouldn't happen
  in practice — Slack only sends events for workspaces that installed the
  app — but don't crash if it does).
- `SlackConnector` needs to become manager-aware for real API calls
  (`chat.postMessage`, `conversations.members`) — use the resolved
  installation's `bot_token` instead of the single `.env` `SLACK_BOT_TOKEN`.
  Signature verification still uses the single app-level `SLACK_SIGNING_SECRET`
  (unchanged).

Note: full per-manager *data* routing (which manager's `UnifiedMessage`
table this lands in) is step 15 — this step's job is establishing "which
manager does this webhook belong to," not yet rewiring where the row gets
stored. It's fine if, until step 15 lands, the resolved `manager_id` is
just threaded through and logged/available but rows still land in the
single shared DB.

## Subproblem 4 — Minimal connect UI

Same placeholder page as step 13: "Connect Slack" → `GET /auth/slack/install`.
After redirect back, show "Connected workspace {team.name}."

## Test plan

- `GET /auth/slack/install` without a session → 401 (must be logged in first).
- `GET /auth/slack/install` with a session → 302 to the right authorize URL,
  `state` encodes the manager's id.
- `GET /auth/slack/callback` with mismatched/tampered `state` → 400, no
  installation row created.
- Webhook with a `team_id` matching a seeded `SlackInstallation` → routes
  correctly (mock `_api_call` where needed, same convention as existing
  Slack tests — no live network in tests).
- Webhook with an unrecognized `team_id` → `{"status": "ignored", ...}`,
  no crash.
