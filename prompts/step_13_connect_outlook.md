# Task: Outlook Sign-In — Authorization-Code Redirect Flow (replaces device-code)

## Context

Repo: `manager-assistant`. Depends on step 12 (control-plane DB, sessions,
`get_current_manager`) being done first.

First read: `app/integrations/outlook.py` (current device-code auth —
being replaced), `app/integrations/OUTLOOK.md`, `app/controlplane/auth.py`
(from step 12), `scripts/outlook_device_login.py` (being deleted).

**Why this step:** device-code auth was the right call for a single
developer testing headlessly with no frontend. Now that there's a real
login page, the standard "Sign in with Microsoft" redirect flow is both
more correct (proper OAuth for a web app) and simpler to test (the user's
own browser navigates between localhost and login.microsoftonline.com and
back — no ngrok/tunnel needed, unlike Slack's webhook in step 14).

**This flow does double duty**: it's both login (identity: who is this
manager) and connect (grant: Mail.Read/Mail.Send access) in one consent
screen, per the earlier decision to fold them together for Outlook.

## Subproblem 0 — Azure config change (tell the user, don't skip silently)

The existing app registration (client_id `a8d987d1-...`, tenant
`e4a13321-...`, already in `.env`) was configured for device-code (a
"Mobile and desktop applications" platform, no secret). Auth-code-redirect
with a confidential client (recommended for a real backend — the secret
authenticates the token exchange) needs:
- Add a **Web** platform under Authentication, with redirect URI
  `http://localhost:3003/auth/outlook/callback` (add the real deployed URL
  later too).
- A **client secret** (Certificates & secrets) — new: `MS_GRAPH_CLIENT_SECRET`
  in `.env`. Reuse the existing `client_id`/`tenant_id`.
- For real multi-manager (not just the developer's own account), the app
  registration's "Supported account types" needs to allow other
  orgs/personal accounts ("Accounts in any organizational directory"). Not
  required to test with your own account first — flag it, don't block on it.

## Subproblem 1 — Remove device-code auth

Delete: `/api/integrations/outlook/device-login/start`,
`/device-login/status` (and their helpers) from `app/integrations/outlook.py`,
and `scripts/outlook_device_login.py`. Update `OUTLOOK.md`'s delegated-mode
section to describe the redirect flow instead (see Subproblem 3). Don't
leave both paths coexisting.

## Subproblem 2 — Auth-code redirect endpoints (`app/controlplane/outlook_auth.py` or similar)

`GET /auth/outlook/login`:
- Build Microsoft's authorize URL (`https://login.microsoftonline.com/{tenant}
  /oauth2/v2.0/authorize`) with `client_id`, `redirect_uri`,
  `response_type=code`, `scope=Mail.Read Mail.Send offline_access User.Read`
  (`offline_access` is what gets you a refresh token; `User.Read` for the
  `/me` profile call below), and a random `state` (store it server-side —
  e.g. a short-lived signed cookie — to check on callback, CSRF protection).
- Redirect the browser there.

`GET /auth/outlook/callback?code=...&state=...`:
- Verify `state` matches.
- Exchange `code` for tokens via
  `msal.ConfidentialClientApplication(client_id, authority, client_credential
  =client_secret).acquire_token_by_authorization_code(code, scopes=[...],
  redirect_uri=...)`.
- Call Graph `GET /me` with the new access token to get `id`, `mail` (or
  `userPrincipalName`), `displayName`.
- Find-or-create a `Manager` by email (reuse `app.controlplane.models.Manager`
  from step 12).
- Serialize the MSAL token cache, upsert `OutlookInstallation(manager_id,
  mailbox_email, token_cache_json)`.
- `create_session(db, manager.id)`, set the cookie (reuse step 12's
  `create_session`), redirect to a simple `/connected` confirmation page
  (or return JSON if no frontend page exists yet — fine for now).

## Subproblem 3 — Reading the token for a given manager

Replace `OutlookConnector`'s single-file token cache
(`data/outlook_token_cache.json`) with per-manager: given a `manager_id`,
load `OutlookInstallation.token_cache_json`, deserialize into an MSAL
`SerializableTokenCache`, build a `ConfidentialClientApplication` (now that
we're on confidential-client throughout — app-only mode already used one;
delegated mode switches to one too), `acquire_token_silent`. On refresh,
re-serialize and write back to the `OutlookInstallation` row (mirrors the
old `_save_token_cache`, just DB-backed instead of file-backed now).

Note: full "which manager's connector instance" plumbing (methods taking a
`manager_id` param instead of reading global `.env`/file state) properly
lands in step 15 alongside the rest of the data-DB split — for this step,
it's fine if `OutlookConnector` only supports one active manager at a time
via the control-plane lookup (single-tenant testing), as long as the
storage format (DB-backed token cache keyed by manager_id) is already
correct so step 15 doesn't have to re-migrate it again.

## Subproblem 4 — Minimal connect UI (browser-testable before the real frontend)

A tiny static page (`app/static/connect.html` or similar, or just plain
links) with:
- "Sign in with Outlook" → `GET /auth/outlook/login`
- After redirect back, show "Connected as {mailbox_email}" (from
  `/api/auth/me` + a lookup of the manager's `OutlookInstallation`).

Doesn't need real styling — this is a placeholder so you (and the user) can
click through the flow in a browser before Vue/whatever the real dashboard
uses exists.

## Test plan

Live Graph calls are impossible to unit test without real credentials
(same convention as the rest of `app/integrations/` — gate on `is_configured()`
/ `"pytest" in sys.modules`). What IS testable without live network:
- `GET /auth/outlook/login` redirects (302) to the right authorize URL with
  correct query params.
- `GET /auth/outlook/callback` with a mismatched `state` → 400, no session
  created.
- Manager find-or-create logic: given a fake Graph `/me` response (mock the
  HTTP call), verify the right `Manager`/`OutlookInstallation` rows land,
  idempotently on a second "login" with the same email.
