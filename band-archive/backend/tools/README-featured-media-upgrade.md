# Featured media revision 23 → 24 operator

`featured_media_upgrade.py` makes a public-schema backup from one repeatable-
read snapshot, restores it into an owned local PostgreSQL 17 instance, and
rehearses only revision `20261023_23` → `20261024_24`. The reviewed database
change is one partial unique index on `media(band_id, song_id)` for rows where
`is_featured IS TRUE`. The rehearsal compares table and row inventory, every
source column digest, ownership, catalog, ACL/RLS/default grants, the full
remote role and membership graph, and the R2 key manifest. It also verifies
that only the exact unique index was added.

The operator loads the pinned revision 22 → 23 operator as an isolated module
instance. It checks that source file's SHA-256 and its revision 23 migration
SHA-256; it does not change the older operator's pins or globals. The target
connection password is read only from the protected `REHEARSAL_TARGET_URL`
process environment carrier. The URL and password are never accepted as
arguments or written to evidence. The default invocation is inert.

From `E:\Anything\band-archive\backend`, after source and migration review,
calculate the helper and migration pins:

```powershell
$helper = (Get-FileHash .\tools\featured_media_upgrade.py -Algorithm SHA256).Hash.ToLowerInvariant()
$migration = (Get-FileHash .\migrations\versions\20261024_24_featured_media_per_song.py -Algorithm SHA256).Hash.ToLowerInvariant()
& .\.venv-test\Scripts\python.exe .\tools\featured_media_upgrade.py `
  --rehearse --approved-helper-sha256 $helper --source-commit <reviewed-source-commit> `
  --migration-sha256 $migration
```

Rehearsal writes private evidence beneath `backend/tools/.state/featured-media23-24-*`.
Keep that directory private. It includes the public database dump, row digests,
security inventory, and R2 key manifest. The restore replays a missing public
schema ACL only when the source/local difference is exactly the captured
`PUBLIC USAGE` grant from `pg_database_owner`; any other ACL difference stops.
Local role replay projects only the required schema security, while remote
role attributes and the complete remote role graph are compared without
copying provider LOGIN/SUPERUSER/BYPASSRLS settings into localhost.

Production apply is a separately reviewed operation. It requires the exact
`ROOT_REVIEWED_FEATURED_MEDIA_V23_V24_APPLY` gate, a rehearsal no older than
24 hours, and SHA-256 pins for its report, manifest, dump, helper, migration,
and source commit. Review the evidence and code first. Then, with the same
protected connection carrier available to the operator process, run:

```powershell
$evidence = 'E:\Anything\band-archive\backend\tools\.state\featured-media23-24-<run-id>'
$report = (Get-FileHash (Join-Path $evidence 'report.json') -Algorithm SHA256).Hash.ToLowerInvariant()
$manifest = (Get-FileHash (Join-Path $evidence 'manifest.json') -Algorithm SHA256).Hash.ToLowerInvariant()
$dump = (Get-FileHash (Join-Path $evidence 'production-public-v23.dump') -Algorithm SHA256).Hash.ToLowerInvariant()
& .\.venv-test\Scripts\python.exe .\tools\featured_media_upgrade.py `
  --apply --approved-helper-sha256 $helper --source-commit <reviewed-source-commit> `
  --migration-sha256 $migration --operator-go ROOT_REVIEWED_FEATURED_MEDIA_V23_V24_APPLY `
  --evidence-directory $evidence --report-sha256 $report --manifest-sha256 $manifest --dump-sha256 $dump
```

Apply rechecks the entire source state under a bounded transaction and table
write barrier, runs the migration, verifies all preserved state, commits once,
then performs a fresh read-only postcheck. It never retries or rolls back
automatically. A lost commit acknowledgement is recorded as
`committed: unknown`; reconcile before taking another action.

The operator does not change application rows, managed Auth, R2 objects,
secrets, mail, or infrastructure. Do not bypass a failed gate or reuse this
operator for another revision.
