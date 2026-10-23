# Featured media production rollout record

Date: 2026-10-24

## Change and validation

- Code commit: `7ea6c6a19f2b9936c585b71487c94a204ca93825`.
- Full backend suite: 1168 passed in 1031.64 seconds.
- Frontend suite: 387 passed in 142.87 seconds.
- Featured-media operator focused suite: 7 passed in 13.19 seconds.

## Backup, rehearsal, and production apply

- Read-only rehearsal verified the backup and local restore, revision 23→24 upgrade, preservation of rows/security/R2 keys, and clean exit of the owned local PostgreSQL process.
- Production apply verified: `committed=true`, `fresh_readonly_postcheck=true`, Alembic revision `20261024_24`, and 38 tables.
- The only intended application-schema change was the approved partial unique index. Apart from the Alembic ledger transition, no application rows, Auth data, R2 objects/keys, or ACLs changed.
- An initial read-only rehearsal stopped at the public object inventory shape/completeness check before any production write. The inventory comparison was corrected to match the pinned provider shape while retaining exact manifest and preservation checks, then the read-only rehearsal was rerun and verified as noted above.

## Deployment status

- Fly.io deployment: running; final deployment verification is pending.
- Frontend push: not run.

This record will be appended with deployment results after they are reported. Until then, it does not attest to a completed backend deployment or frontend push.

## Backend deployment follow-up

- Fly.io backend deployment completed. Machine `080e444f9643d8` started in `NRT` on `shared-cpu-2x` with 2048 MB, using image `registry.fly.io/band-archive:deployment-01M5NDDE2G3PS8XR0163BVPHBS`.
- Deployment smoke check: `/members` returned HTTP 200 with `Access-Control-Allow-Origin` matching the Pages origin. The new `GET /api/v2/bands/1/featured-media?limit=1&offset=0` endpoint returned HTTP 200.
- Startup review covered the latest 16 log lines: 5 startup markers and 0 errors.
- No additional custom Fly checks were run; verification was limited to deployment smoke checks and the HTTP checks above.
- Frontend push remains pending.

This follow-up supersedes the earlier “Fly.io deployment: running” status. The backend deployment is complete; the frontend push is still not complete.

## Frontend publication and final checks

- Normal push to `origin/master` completed at commit `3ad2f1f9183c4c73094d4847e519e6e807785083`.
- Cloudflare Pages is serving [https://band-archive.pages.dev](https://band-archive.pages.dev); the HTML and live JavaScript asset `/assets/index-WspcnyLK.js` were verified.
- All checked live bundle markers were present: `GenericTitleBandArchive`, `representative-media`, `/portal`, `/guide`, `/community/`, and API host `https://band-archive.fly.dev`.
- Validation counts remain: frontend 387 passed, backend 1168 passed, featured-media operator 7 passed.
- Local mocked desktop and mobile UI states were verified. These were not authenticated production writes: representative-item selection, replay upload, and R2 operations were not exercised in the production smoke checks.
- User secrets were preserved.
- Non-blocking P2 follow-ups remain: the existing private-admin/non-member direct-management-route constraint, and the migration-linked rehearsal restriction. These were recorded as limitations, not bypassed.

This final follow-up supersedes the earlier “Frontend push: not run/pending” status. Production representative-media writes, replay uploads, and R2 operations remain untested.
