# Phase 9 PostgreSQL cutover preflight

This runbook prepares a future SQLite-to-PostgreSQL cutover. It is not an
approval to deploy, change Fly configuration, connect to production, or write,
copy, or delete R2 objects. Perform every live-service command under a
separately approved change window.

## Non-negotiable boundary

- Stop and verify every SQLite writer before creating the source backup. The
  input must be a point-in-time SQLite backup/snapshot, never a live database
  file with an active `-wal` sidecar.
- Preserve the original snapshot in operator-controlled storage. Record its
  SHA-256, byte size, and mtime; make two dry-run reports from that same file.
- The target must be the explicitly approved, disposable PostgreSQL target,
  already migrated to the checked-in Alembic head. The rehearsal rejects a
  schema/version mismatch and any user rows.
- Object checks are comparison-only. A permitted adapter can list/head R2;
  it must not expose put, copy, or delete. No R2 mutation is part of this
  cutover.
- Connection strings and credentials are environment variables only. Do not
  place them in commands, reports, tickets, source files, or shell history.

## Dry-run evidence

Use a new, empty report filename for each invocation. The tool retains JSON
itself as UTF-8 and refuses to overwrite evidence.

```powershell
cd backend
$env:REHEARSAL_TARGET_URL = '<approved disposable target connection string>'
python tools/legacy_rehearsal/rehearsal.py `
  --source-sqlite E:\operator\snapshots\band_archive.sqlite `
  --expected-target-host '<approved-host>' `
  --expected-target-database '<approved-database>' `
  --expected-target-schema public `
  --dry-run `
  --report-output E:\operator\reports\phase9-dry-run-1.json
```

Run it again with a second report filename. The source fingerprint and report
content checks must agree. Review the report's table/primary-key and full-row
digests, FK-orphan list, cached vote-score mismatches, comment roots/parents,
tenant visibility counts, multipart quarantine, and object manifest defects.
Compare the manifest with a read-only R2 listing/head result; required keys and
known sizes must match, retired keys must have their expected lifecycle, and
any unexpected key needs an explicit disposition.

## Final-import gate (future, separately approved)

Immediately after the write freeze and snapshot, create a local attestation
whose values exactly equal the source fingerprint. It contains no URL,
credential, bucket, object key, or free-form note.

```json
{
  "version": "phase9-write-freeze-v1",
  "writes_frozen": true,
  "snapshot": {
    "sha256": "<64 lowercase hex characters>",
    "size": 123,
    "mtime_ns": 123
  }
}
```

Pass that path through `--write-freeze-attestation` on the final (non-dry)
rehearsal import. The tool refuses an import with no attestation, an altered
snapshot, a stale fingerprint, unknown JSON fields, or a non-empty/non-head
target. The retained report records only that the versioned attestation
matched; it does not retain the attestation contents.

After import, reconcile database rows, FKs, score aggregates, comment trees,
and the R2 manifest before deploying the PostgreSQL backend. Keep SQLite and
its Fly volume snapshot as recovery material.

## Rollback boundary

Before any new writes are enabled, a failed cutover may restore the previously
tested SQLite/Fly release using the preserved snapshot and the approved
operator procedure. After PostgreSQL writes have been enabled, do **not** run
an automatic or bidirectional rollback: it can lose or duplicate new writes.
Stop writes, preserve evidence, diagnose, and require an explicit recovery
decision instead.
