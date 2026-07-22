# Task: Agent Pool -- Dedicated Bot Per Manager + User-Token Reading

## Context

Supersedes the step-14 Slack design (single shared "Harry" app, bot-token
only). Real-world usage turned out to be: multiple managers at the SAME
org, in the SAME Slack workspace. A single shared bot identity per
workspace can't be told apart by message recipients ("whose Harry is this
reply for?"), and our schema (`SlackInstallation.team_id` as primary key)
assumed one manager per workspace, which silently breaks the moment a
second colleague connects.

**New model:**
- **Reading** (DMs, group DMs, channels) is a **user-token grant** --
  requested directly against the manager's own Slack identity, no bot
  involved. Already correctly isolated per-person by construction (each
  individual's OAuth grant is their own).
- **Sending/being messaged** goes through a **dedicated bot**, drawn from a
  pre-created pool of distinctly-named Slack apps (e.g. "Atlas", "Nova",
  "Comet"...), assigned 1:1 to a manager. Recipients see a visually
  distinct bot per manager -- no shared-identity ambiguity.
- Assignment is gated by a single shared admin-issued access code.

## Subproblem 0 -- Admin: pre-create the bot pool (manual, one-time, out of band)

For each pool slot: register a new Slack app at api.slack.com/apps (own
name/avatar), add Bot Token Scopes (`im:history`, `im:read`, `chat:write`,
`channels:history`, `groups:history`, `mpim:history` -- see subproblem 5),
Redirect URL `http://localhost:3003/auth/slack/callback` (same URL for
EVERY pool app -- routing to the right one happens via `api_app_id` in the
payload, not a distinct URL per app), copy Client ID/Secret/Signing Secret
+ App ID (Basic Information page). These get inserted into the `agents`
table (subproblem 1) via a small seed script/admin endpoint -- not
something the code needs to create programmatically (explicitly decided
against the App Manifest API dynamic-creation route, for now).

## Subproblem 1 -- `Agent` pool table (control-plane DB)

```python
class Agent(ControlPlaneBase):
    __tablename__ = "agents"
    id: str primary key            # slug, e.g. "atlas"
    name: str                      # display name shown in the picker UI
    slack_app_id: str unique       # Slack's "App ID" (Basic Information) --
                                    # THE key used to route incoming webhook
                                    # events to the right agent, since every
                                    # Events API payload carries api_app_id
                                    # at the top level regardless of which
                                    # of our pool apps sent it.
    slack_client_id: str
    slack_client_secret: str
    slack_signing_secret: str
    manager_id: str | None FK -> managers.id, nullable, unique  # unassigned while null
    team_id: str | None            # set once installed into a workspace
    bot_token: str | None          # set once installed
    claimed_at: datetime | None
    installed_at: datetime | null
```

`manager_id` unique + nullable enforces the 1:1 (one bot serves exactly one
manager; a manager holds exactly one bot) at the schema level, not just in
application code.

## Subproblem 2 -- Per-manager mirror row

Per the explicit requirement: the assignment must be visible from BOTH
sides, not just the control-plane `Agent` row. Add a tiny table to
`app/database.py` (created inside each manager's own db.sqlite via the
existing `init_manager_db` path):

```python
class AgentAssignment(Base):
    __tablename__ = "agent_assignment"
    agent_id: str primary key
    agent_name: str
    team_id: str | None
    assigned_at: datetime
```

Expect at most one row per manager db (a manager has exactly one bot).
Lets any code already holding a manager-scoped session look up "which bot
is mine" without a control-plane round trip.

## Subproblem 3 -- Access code + picker endpoints

- `AGENT_POOL_ACCESS_CODE` env var (single shared code).
- `POST /api/agents/redeem {code}` (requires login) -- validates the code,
  returns the list of unassigned `Agent` rows (id, name only -- never
  secrets) for the picker UI.
- `POST /api/agents/claim {agent_id}` (requires login) -- atomically sets
  `Agent.manager_id` (fails/409s if already claimed -- race between two
  managers picking the same one), writes the `AgentAssignment` mirror row
  into the manager's own db, returns a redirect target for that agent's
  install flow.
- `GET /auth/slack/install` becomes agent-aware: looks up the manager's
  claimed `Agent` (404 if none claimed yet -- claim before install), builds
  the authorize URL using THAT agent's `slack_client_id`, and separately
  requests `user_scope=...` (see subproblem 5) for reading.

## Subproblem 4 -- Webhook routing by `api_app_id`, not a per-agent URL

One shared `/api/integrations/slack/webhook` endpoint still works for
every pool bot (Slack payloads always carry `api_app_id` at the top level):
look up `Agent` by `api_app_id` -> get its `slack_signing_secret` (verify
signature using THIS agent's secret, not a single global one) -> get its
`manager_id` -> route into that manager's db.sqlite, same as the existing
`team_id`-based routing, but keyed one level more specifically (an agent,
not a whole workspace) since multiple agents can share a `team_id` now
(multiple managers' bots, same workspace).

## Subproblem 5 -- User-token reading (DMs + channels + groups) -- POLLING, not webhook

Revised after advisor review: the original draft assumed "Subscribe to
events on behalf of users" would push user-scoped message events over the
same webhook as bot events. That was asserted, not verified, and it's the
one assumption the whole read path depended on -- Slack's Events API is
fundamentally bot-membership-centric, so whether it pushes events for
conversations the bot itself isn't in is genuinely uncertain.

Decision: **skip the uncertainty, use polling instead.** Under a *user*
token (unlike a *bot* token), `conversations.history` with
`oldest=<last-poll-ts>` per conversation IS a clean, well-established
"everything since X" primitive -- the rate-limit objection that ruled out
polling for step 14's bot-token design doesn't apply here, since each
manager's poll is scoped to their own token/conversations, not a shared
bot fanning out across everyone. This exactly mirrors the existing Outlook
poll pattern (`app/projectkb/jobs/outlook_poll.py`) -- same shape, nothing
new to invent, no experimental Slack config required.

OAuth `user_scope` requested alongside the agent's bot scope at install
time: `im:history,im:read,channels:history,groups:history,mpim:history`
(piece 1 -- see Sequencing below -- starts with `im:history,im:read` only,
DMs, no pool). Send-as-user (`chat:write` under `user_scope`) stays a
separate, later, optional incremental-consent step (mirrors the Outlook
enable-send pattern already built) -- default off.

Bot events (webhook, unchanged) stay the delivery path for replies sent
*directly to the dedicated bot* -- that part is proven (the bot is party to
those DMs since it sent the first message). Passive observation of the
manager's own pre-existing conversations goes through user-token polling.
`api_app_id` webhook routing (subproblem 4) is still correct and still
needed for bot-directed events -- it just doesn't carry the observation
stream, contrary to the original draft.

**Tracked-contacts model needs to grow up to handle channels**: today
`is_tracked(manager_id, source, address)` assumes one counterpart per
message. A channel/group has N participants. New concept: a
`tracked_channels` list (channel ID/name patterns), parallel to
tracked-contacts, checked instead of the single-counterpart gate when
`NormalizedMessage` represents a channel/group rather than a DM (needs a
`conversation_type` field on `NormalizedMessage`: "dm" | "group" |
"channel", and `SlackConnector.normalize()` populated accordingly from the
event's `channel_type`).

## Sequencing (this step is built in pieces, not one commit)

1. **DONE.** User-token OAuth grant + polling for DMs only -- extends the
   *existing* single shared Slack app (no pool yet). `user_scope` requested
   at `/auth/slack/install`, `authed_user` stored on `SlackInstallation`
   (`user_token`/`user_id`), `app/projectkb/jobs/slack_poll.py` polls via
   `SlackConnector.fetch_since`. Proves the read path's shape works; the
   live poll itself has not yet run against a real Slack workspace (no pool
   app exists yet -- see piece 2b below), so DM-participant attribution
   under a live user token remains unverified.
2. Bot pool + access-code claim flow -- split further, following the same
   testable-now/live-only seam that made piece 1 clean:
   - **2a -- DONE (additive, offline-testable).** `Agent` control-plane
     table (`app/controlplane/models.py`), `AgentAssignment` per-manager
     mirror (`app/database.py`), `scripts/seed_agents.py` +
     `agents_pool.json.example`, `POST /api/agents/redeem` /
     `POST /api/agents/claim` (`app/controlplane/agents.py`) with an atomic
     guarded `UPDATE ... WHERE manager_id IS NULL` (not read-then-write) for
     the claim race, and idempotent re-claim. Nothing existing changed --
     `/auth/slack/install`, the webhook, and `send()` still run on
     `SlackInstallation` untouched.
   - **2b -- DONE (code + full offline test coverage; live verification still
     pending real pool apps).** Agent-aware `/auth/slack/install` (uses the
     claimed agent's own `slack_client_id`/`user_scope`), state signed with
     that agent's own `slack_client_secret` (not a single global secret --
     the callback decodes `manager_id` from the state first, unverified,
     looks up which agent that manager claimed, then verifies against
     *that* agent's secret). Webhook routed by `api_app_id` ->
     `Agent.slack_app_id` (own `slack_signing_secret` per agent, checked
     against the resolved agent, not a global one). Tokens (`bot_token`,
     `user_token`/`user_id`) now live on `Agent` directly; `SlackInstallation`
     is retired (no live data ever existed -- `SLACK_CLIENT_ID` was empty
     until this pool model, so nothing to migrate). `send()`/`/connections`/
     `disconnect`/`slack_poll` all read from `Agent`. **Also fixed the gap
     flagged during design review**: the callback now upserts
     `TeamMember.slack_handle = authed_user.id` for the manager (see
     `_sync_manager_slack_handle` in `app/controlplane/slack_auth.py`) --
     without this, DM-participant resolution
     (`SlackConnector._resolve_dm_other_participant`) had no way to ever
     recognize the manager, so reading would have landed and silently
     returned nothing. `disconnect` clears install-derived fields
     (team_id/tokens) but keeps the manager's agent *claim* -- reinstalling
     reuses the same bot identity rather than releasing the slot.
3. **DONE (offline-testable half only).** Channels + group DMs:
   `conversation_type` on `NormalizedMessage` (`app/integrations/base.py`),
   `SlackConnector.normalize()` maps Slack's `channel_type` -> `conversation_type`
   (`im`->`dm`, `channel`->`channel`, `group`/`mpim`->`group`), `ingest()`
   branches on it (`dm` keeps the exact existing single-counterpart/
   tracked-contacts gate; anything else is gated on the new
   `app/projectkb/tracked_channels.py` list instead, with NO requirement
   that the manager be sender/receiver -- a channel structurally involves
   everyone in it). The `conversations.history` -> event-dict shaping is
   extracted into a pure, unit-tested static method
   (`SlackConnector._history_message_to_event`). **Deliberately NOT done
   yet** (deferred to 2b, since it's unverifiable without a live pool app):
   broadening `user_scope` to include `channels:history,groups:history,
   mpim:history`, the incremental-consent re-grant, and actually iterating
   channels/groups in `fetch_since` (still DM-only, `types=im`).

**What "go ahead" bought so far, concretely testable today:** the pool's
identity/claim machinery (2a), the channel/group data-model + gating logic
(3, offline half), and the full install/webhook/send/disconnect cutover
(2b) are all real and covered by tests (documented, stable Slack response
shapes -- `api_app_id` at payload top level, `authed_user` object, per-app
`oauth.v2.access` -- not behavioral unknowns, so safe to build against
without live confirmation). What remains genuinely unverified until a real
pool app exists and one live install/poll happens: whether user-token DM
polling actually returns sane data against a real Slack workspace, and
whether webhook delivery + signature verification behaves as documented
end to end. **This is the next concrete milestone**: create one real Slack
app (subproblem 0, just one slot), seed it via `scripts/seed_agents.py`,
set `AGENT_POOL_ACCESS_CODE`, claim + install it, and run the first live
poll of this entire subsystem.

## Test plan

- Two managers claim two different agents -- both installable into the
  SAME `team_id` without collision; each manager's `AgentAssignment` row
  is independent.
- Claiming an already-claimed agent -> 409, no mutation.
- Webhook payload with a given `api_app_id` routes to the correct agent's
  manager_id and verifies against THAT agent's signing secret (a payload
  signed with agent A's secret must fail verification if routed as if it
  were agent B's).
- Tracked-channel gate: a channel message from an untracked channel is
  ignored even if a participant is an individually-tracked contact
  (channels and individual contacts are separate lists, not implicitly
  overlapping).
