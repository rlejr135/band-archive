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
