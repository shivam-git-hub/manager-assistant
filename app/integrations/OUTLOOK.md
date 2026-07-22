# Connecting Outlook

Harry observes the **manager's own mailbox** via Microsoft Graph. Unlike
Slack, Graph has no simple "POST to my server whenever mail arrives" --
real arrival is via **polling** (`OutlookConnector.fetch_since`), not a
webhook. Two auth modes are supported; pick based on what access you have.

## Which mode to use

| | App-only (client-credentials) | Delegated (sign-in redirect) |
|---|---|---|
| Needs | Azure tenant admin consent | The manager's own login, via browser, once |
| Ongoing complexity | None -- re-request a token each time, no refresh handling | A cached token per manager (DB-backed), refreshed silently |
| Set `OUTLOOK_AUTH_MODE=` | `app` | `delegated` |

If you have (or can get) tenant admin rights, **app-only is simpler and
more robust** for a background service -- use it. If you only have a
regular user account, use delegated.

## 1. Register an Azure AD app (both modes)

1. https://portal.azure.com -> Azure Active Directory -> App registrations
   -> New registration.
2. Note the **Application (client) ID** (`MS_GRAPH_CLIENT_ID`) and
   **Directory (tenant) ID** (`MS_GRAPH_TENANT_ID`).

## 2a. App-only mode setup

1. Under **Certificates & secrets**, create a new client secret. This is
   `MS_GRAPH_CLIENT_SECRET`.
2. Under **API permissions**, add **Application permissions** (not
   delegated): `Mail.Read`, `Mail.Send`.
3. Click **Grant admin consent** -- this requires a tenant admin.
4. Set in `.env`:
   ```
   OUTLOOK_AUTH_MODE=app
   MS_GRAPH_CLIENT_ID=...
   MS_GRAPH_CLIENT_SECRET=...
   MS_GRAPH_TENANT_ID=...
   OUTLOOK_MAILBOX=manager@yourcompany.com
   ```
   `OUTLOOK_MAILBOX` is whichever mailbox Harry reads/sends from -- normally
   the manager's own address.

## 2b. Delegated mode setup

Delegated mode uses the standard "Sign in with Microsoft" browser redirect
flow -- the same consent screen doubles as manager login (identity) and
Outlook-connect (Mail.Read/Mail.Send grant). This is a confidential client,
same as app-only mode, just with a different consent flow.

1. Under **Authentication**, add a platform -> **Web** -> redirect URI
   `http://localhost:3003/auth/outlook/callback` (add your real deployed
   URL too, once there is one).
2. Under **Certificates & secrets**, create a client secret --
   `MS_GRAPH_CLIENT_SECRET`.
3. Under **API permissions**, add **Delegated permissions**: `Mail.Read`,
   `Mail.Send`, `offline_access`, `User.Read`. Admin consent isn't required
   for these if your tenant allows user consent (default for most tenants).
4. For real multi-manager use (not just testing with your own account),
   the app registration's "Supported account types" needs to allow other
   orgs/personal accounts. Not required to test with your own account first.
5. Set in `.env`:
   ```
   OUTLOOK_AUTH_MODE=delegated
   MS_GRAPH_CLIENT_ID=...
   MS_GRAPH_CLIENT_SECRET=...
   MS_GRAPH_TENANT_ID=...
   MS_GRAPH_REDIRECT_URI=http://localhost:3003/auth/outlook/callback
   ```
6. **Sign in**: with the app running, visit `GET /auth/outlook/login` in a
   browser (or click "Sign in with Outlook" on `/connect.html`). You're
   redirected to Microsoft, sign in and consent, and land back on
   `/connect.html?connected=outlook` with a session cookie set.

   This creates (or finds) a `Manager` row keyed by the signed-in email,
   and stores the token cache on that manager's `OutlookInstallation` row
   in the control-plane DB (`data/controlplane.sqlite`) -- no more
   `data/outlook_token_cache.json` file. The connector silently reacquires
   access tokens from that cache afterward; repeat this step only if the
   refresh token is revoked or the row is deleted.

## 3. Real arrival is polling, not a webhook

`OutlookConnector.fetch_since(since)` calls Graph's
`GET /users/{mailbox}/messages` (app mode) or `GET /me/messages` (delegated
mode) filtered by `receivedDateTime`. There's a manual trigger at
`POST /api/integrations/outlook/poll` for testing; wiring this into a
recurring schedule is a separate decision (see `app/projectkb/job_schedule.py`
notes -- polling cadence is a different concern from claim-extraction
cadence).

`/api/integrations/outlook/mock-ingest` still exists, accepting a
Graph-message-shaped payload directly -- useful for tests/manual injection,
and goes through the exact same normalization/gating as a real poll would.

## 4. The manager's own team_members row

Same as Slack: a `TeamMember` row with `role` containing "manager" and
`outlook_email` matching the real mailbox address, via `POST /api/team`.

## 5. Tracked contacts

Only emails whose other party (sender, if addressed to the manager; or
recipient, if sent by the manager) is on the tracked-contacts list get
stored. Add via `POST /api/projectkb/tracked-contacts` with an
`email_pattern` (exact address or a glob like `*@vendor.com`).
