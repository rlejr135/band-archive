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

## Local staging rehearsal

`backend/.env.staging-auth.local` is an ignored, non-secret staging contract.
It deliberately keeps `SUPABASE_AUTH_ENABLED=false`. It is not loaded by
`app.py`'s default `load_dotenv()` call, so from the repository root use the
dotenv runner explicitly whenever using it:

```powershell
& backend\.venv-test\Scripts\python.exe -m dotenv -f backend\.env.staging-auth.local run -- `
  backend\.venv-test\Scripts\python.exe -c "import sys; sys.path.insert(0, 'backend'); from config import DevelopmentConfig, validate_supabase_auth_config; c={k:getattr(DevelopmentConfig,k) for k in dir(DevelopmentConfig) if k.isupper()}; assert not c['SUPABASE_AUTH_ENABLED']; c['SUPABASE_AUTH_ENABLED']=True; validate_supabase_auth_config(c); print('staging auth contract validated; auth remains disabled')"
```

The validation command checks the full configuration that would be required
when auth is enabled, but does not create an app, contact Supabase, or change
the disabled setting. The backend must be launched with the same explicit
dotenv wrapper only *after* the database is at the checked-in Alembic head:

```powershell
Push-Location backend
& .\.venv-test\Scripts\python.exe -m dotenv -f .env.staging-auth.local run -- `
  .\.venv-test\Scripts\python.exe app.py
Pop-Location
```

Keep the database password in the process environment (for example, an
ephemeral operator variable), never in this file. Use the current pooler host,
username, and `sslmode=require`; do not reuse a stale host or user embedded in
an older connection URL. Before changing remote schema, take a restorable
backup, run the read-only snapshot/preflight check, then run the isolated
runner with the process `DATABASE_URL`:

```powershell
Push-Location backend
& .\.venv-test\Scripts\python.exe migration_runner.py upgrade
Pop-Location
```

That command performs the migration preflight before any DDL. Do not use
`flask db upgrade`, do not start the normal application as a migration tool,
and do not set `SUPABASE_AUTH_ENABLED=true` until the command completes and
the recorded revision equals the checked-in head. Turnstile is not enabled in
this rollout: there is currently no client-side challenge/token submission
coverage, so enabling it would make signup fail.
