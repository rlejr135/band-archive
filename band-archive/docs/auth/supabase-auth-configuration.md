# Supabase Auth and production cutover readiness

Supabase Auth is the identity provider for browser and native clients. Flask
remains the only application-domain API; clients must not query domain tables
through the Supabase Data API. This document records configuration and release
gates; it is not authorization for a production change.

## Current state — 2026-10-11 KST

- The selected path is to promote the existing `anything-staging` Supabase
  project for production use. A separate Supabase project is not a requirement
  of this contract. Confirm and replace environment-specific URLs, keys,
  issuer/JWKS settings, Auth URL configuration and SMTP settings as part of the
  separately approved promotion.
- Supabase has remote schema revision `20261010_13` (Data API security
  hardening). This does **not** mean the application database has been cut over:
  the production Fly service still uses SQLite and its current v48 release does
  not expose `/api/v2/me` (404). The current Pages origin is permitted by the
  existing backend CORS policy, but that does not prove v2 route readiness.
- The checked-in `frontend/.env.production` names the API and map variables but
  not the Supabase browser variables below. Cloudflare Pages build settings may
  supply them, but their current external values/settings have not been
  verified; confirm them before a production build.
- The latest compatible backend release and PostgreSQL cutover must precede a
  frontend release that exposes authenticated band/v2 screens. Keep
  `SUPABASE_AUTH_ENABLED=false` until the database and backend release are ready.

## Required configuration

Cloudflare Pages injects Vite variables at build time. Production must have:

- `VITE_API_URL` pointing at the approved production Flask API.
- `VITE_NAVER_MAP_CLIENT_ID` for the map integration.
- `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY`. The latter carries the
  Supabase public anon/publishable key; it is public client configuration, not
  a service-role credential. Set production and preview values deliberately;
  never put a `service_role` key in a frontend build.

The Fly backend auth contract requires `SUPABASE_URL`,
`SUPABASE_PROJECT_REF`, `SUPABASE_JWT_ISSUER`,
`SUPABASE_JWT_AUDIENCE=authenticated`, `SUPABASE_JWKS_URL`, its configured
cache/timeout/leeway values, `SUPABASE_PUBLISHABLE_KEY`, and
`BOOTSTRAP_PLATFORM_ADMIN_EMAIL` for the one-time initial platform admin.
These belong in Fly secrets or the approved secret store, not in the frontend
bundle or repository. Do not use a Supabase `service_role` key for this
contract.

When Flask moves from SQLite to Supabase PostgreSQL, it also needs a
`DATABASE_URL` secret containing the approved database connection, including
its password. The password is not stored in source, a checked-in env file, or
as an unrelated standalone application setting; putting it in the protected
connection secret is necessary for PostgreSQL access. This is distinct from
not storing a database password in a browser or frontend bundle. SMTP app
passwords belong only in the chosen mail provider's protected SMTP settings;
they do not belong in Cloudflare or the frontend.

Use the approved database hostname with `sslmode=verify-full` and the
independently pinned CA certificate. `sslmode=require` alone is insufficient
because it does not verify the server hostname. Keep the connection secret and
CA material out of logs and public evidence; follow the PostgreSQL cutover
runbook for the approved operator procedure.

## Supabase Auth URLs and signup protection

Set the Supabase Auth Site URL to the production Pages origin
(`https://band-archive.pages.dev`). The frontend sends confirmation links to
`<current-origin>/verify-email` and password-reset links to
`<current-origin>/reset-password`. Allow those exact production callbacks in
Supabase Auth URL Configuration. Add a preview/staging callback only when that
origin is intentionally being tested; do not use a broad redirect wildcard.

The current signup form does not render a Turnstile challenge or send a
challenge token. Enabling Supabase CAPTCHA/Turnstile enforcement now will make
signup fail. Before publicly promoting signup, explicitly choose either to
implement and test the challenge flow or to authorize a time-limited beta
exception with an accepted abuse-control plan. Configuration alone is not
enough, and this choice remains open.

Native clients persist Supabase session material through the platform secure
storage plugin. Their email callback is not proven by the web configuration:
the frontend derives callback URLs from the running origin, and native
deep-link / universal-link completion requires separate platform URL setup and
device testing. Treat native auth as a separate release gate; a successful
Cloudflare web flow does not verify native callbacks.

## Safe release order

1. Confirm the intended promotion of `anything-staging`, then configure its
   production Auth Site URL, exact redirect allowlist, email confirmation and
   approved mail settings. Keep any new production values in the relevant
   protected environment.
2. Complete the separately authorized database cutover from Fly SQLite to
   PostgreSQL, with a verified backup and the approved `verify-full`/pinned-CA
   connection. Revision `20261010_13` is already applied; it is not an app-data
   import or Fly cutover.
3. Release the latest compatible backend only after its PostgreSQL target and
   required auth contract are ready. Set `SUPABASE_AUTH_ENABLED=true` only
   after the expected Alembic head/tables and configuration are verified.
4. Before publishing frontend v2 screens, smoke-test `/api/v2/me`, public and
   authenticated band APIs, signup/confirmation, password reset, legacy
   compatibility, and Pages-origin CORS against that backend. The current v48
   `/api/v2/me` 404 is a known pre-release state, not a passing smoke test.
5. Build the frontend with the approved Cloudflare Production variables and
   verify the deployed asset, API target and CORS only after separate
   authorization to publish. Keep native builds/releases independent.

Do not run `flask db upgrade` or use normal application startup as a migration
tool. Formal schema changes must follow the approved migration/cutover
procedure and its backup, preflight, and review gates. Turnstile enforcement,
native auth callbacks, identity refresh, and application cutover each retain
their own readiness/approval gates.
