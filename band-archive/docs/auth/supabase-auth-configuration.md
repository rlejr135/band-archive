# Supabase Auth configuration contract

Phase 2 uses Supabase only as the identity provider.  Browser and native apps
use Supabase Auth for email/password signup, email verification, password reset
and token refresh; they must not query application domain tables through the
Supabase Data API.  All songs, media, bands and memberships continue to go
through Flask.

## Required deployment settings

Set `SUPABASE_AUTH_ENABLED=true` only after all of the following are present
in Fly secrets or the equivalent environment-specific secret store:

- `SUPABASE_URL` and `SUPABASE_PROJECT_REF`
- `SUPABASE_JWT_ISSUER` and `SUPABASE_JWT_AUDIENCE=authenticated`
- `SUPABASE_JWKS_URL`, cache TTL, HTTP timeout and JWT leeway
- `BOOTSTRAP_PLATFORM_ADMIN_EMAIL` for the one-time initial platform admin

No service-role key, SMTP app password, Supabase database password, or client
refresh token belongs in this repository, Cloudflare public variables, or the
Flask configuration.  The backend only fetches public JWKS documents and
verifies bearer tokens locally.

## Environments and rollout gate

Create separate Supabase Tokyo staging and production projects. Enable email
confirmation, set redirect URLs to the matching Cloudflare Pages domain, use
Gmail SMTP only for small beta volume, and configure Turnstile before exposing
signup. Test JWKS rotation, email verification, reset links and CORS against
staging before enabling production auth.

`SUPABASE_AUTH_ENABLED=false` is intentional during compatibility work:
existing public v1 routes continue to boot. Once it is true, startup fails if
the complete non-secret contract is missing. The current SQLite migration path
remains unchanged; use only `python migration_runner.py --database-url ...`
for formal migrations, never `flask db upgrade`.

## Required release order

1. Stop the backend or keep `SUPABASE_AUTH_ENABLED=false`.
2. Back up the database, stamp a verified legacy database if necessary, then
   run `python migration_runner.py --database-url ... upgrade`.
3. Verify the Alembic head and `app_user`/`auth_bootstrap_state` tables on a
   staging copy.
4. Enable `SUPABASE_AUTH_ENABLED=true` and start the backend.

The normal app factory deliberately creates only the historical v1 metadata.
It never creates Alembic-managed auth tables. With auth enabled it checks both
the required tables and Alembic head, then refuses startup until this release
order has been completed.
