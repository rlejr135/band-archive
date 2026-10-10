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
- Connection strings and credentials are environment variables only. Inject
  them into the child process through the approved secret manager or secure
  launcher; do not type, paste, assign, or export a secret in a shell. “Env
  only” does not mean manually assigning a value to a PowerShell variable.

## Dry-run evidence

Use a new, empty report filename for each invocation. The tool retains JSON
itself as UTF-8 and refuses to overwrite evidence.

```powershell
cd backend
# Start this command through the approved secret-manager secure launcher.
# It injects REHEARSAL_TARGET_URL only into this child process; the value is
# never typed into the shell, a command line, or a history file.
<approved-secure-launcher> -- python tools/legacy_rehearsal/rehearsal.py `
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
target. A final import also requires a new `--report-output` path: the CLI
reserves and atomically publishes retained evidence before the target
transaction commits. The retained report records only that the versioned
attestation matched; it does not retain the attestation contents. Use this
CLI as the only operator-facing final-import entrypoint; the Python
`run_rehearsal` function is an internal/test seam and its injected report
publisher is not an operator workflow.

### Claimed staging refresh exception

The empty-target CLI must not be pointed at the already-claimed staging legacy
band. When a separately approved latest-Fly-to-claimed-staging refresh is
needed, use only the PostgreSQL-only contract in
[`backend/tools/README-preserve-identity-refresh.md`](../../backend/tools/README-preserve-identity-refresh.md).
It requires a fresh source freeze and a reviewed v11/v12 provenance-pinned
baseline sidecar, preserves all account/claim/audit identity rows, and has no
R2 mutation capability.

#### Verified preparation checkpoint — 2026-10-11 KST (not GO or approval)

The following local preparation checks are complete; they do not authorize a
remote migration or cutover:

- The official Supabase Dashboard production CA bundle (`prod-ca-2021.crt`)
  was fingerprint-pinned. A `verify-full` connection passed the required
  hostname and TLS checks.
- A public-only v12 backup was restored into a newly owned local PostgreSQL
  17.11 instance. The source inventory matched the pre- and post-backup
  inventories, and the backup dump SHA-256 was independently verified. The
  restore matched across all 28 tables for row counts, catalog, foreign keys,
  sequences, and ownership.
- A local v13 migration simulation preserved account, claim, audit, and content
  data. The v12 baseline had RLS disabled; v13 enabled non-forced RLS on all 28
  tables. The v13 migration reduced anonymous/authenticated table and sequence
  grants and default ACL grants to zero. Owner CRUD privilege checks covered
  four privileges across all 28 tables; an actual single CRUD probe was rolled
  back and verified. The owned local cluster is stopped.
- Focused helper tests (13), read-only capture adapter tests (12), and direct
  CLI checks (11) passed.
- The latest preliminary Fly snapshot and R2 inventory comparison observed 143
  objects. All 142 required references were present, with no missing required
  objects, size mismatches, foreign-key or object-manifest defects; 55 retired
  references were absent. One unreferenced object remains present and retained;
  it has no deletion approval. This preliminary snapshot is not frozen, and
  the old staging content has not been refreshed from it.

Remote Supabase remains at v12. No remote DDL, Fly or Cloudflare deployment,
Git push, R2 mutation, or writer freeze occurred. The local operator helper
and its ignored evidence are private preparation artifacts, not a portable
public workflow; follow the existing refresh contract linked above rather
than inventing commands or transferring private artifacts into public
documentation. Backup Auth/Storage material is excluded from this checkpoint.
Do not publish operator-private evidence or include account emails, URLs,
credentials, bucket names, object paths, or signed URLs in reports.

Next gates remain separate: explicit approval for remote v13 after a verified
backup; then, before any identity refresh, a final writer freeze and fresh
snapshot, independently approved account/bucket pins, review of the retained
orphan, and the protected baseline profile. Cutover deployment requires its
own further approval. This checkpoint is not a GO decision.

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
