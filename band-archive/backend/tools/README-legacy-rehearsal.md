# Legacy SQLite → PostgreSQL rehearsal (Phase 5)

`legacy_rehearsal/rehearsal.py` is an **offline, one-shot rehearsal tool**. It
does not run at Flask/Fly startup, does not infer a database URL, and has no
R2 write operation. It copies the supplied SQLite file into a temporary
directory, upgrades and tenant-backfills that copy, then imports rows with
their original primary keys into an already-empty target at Alembic head.

Use an isolated PostgreSQL staging database; never point it at production.
The invocation must provide the exact expected host, database/project
reference, and connected schema—free-form labels do not authorize a target.
The tool refuses any nonempty target rather than attempting an idempotent
merge. SQLite targets require `--allow-sqlite-target` plus the exact resolved
test-file path and are only a local compatibility test path.

```powershell
cd backend
python tools/legacy_rehearsal/rehearsal.py `
  --source-sqlite E:\operator\snapshot\band_archive.sqlite `
  --target-url $env:REHEARSAL_TARGET_URL `
  --expected-target-host db.staging.example.test `
  --expected-target-database rehearsal `
  --expected-target-schema public `
  --report-output E:\operator\reports\phase5-dry-run.json `
  --dry-run
```

The target URL can be supplied through `REHEARSAL_TARGET_URL`, but reports
contain only a dialect and a one-way host/database fingerprint. The parsed URL
host/database and the connected PostgreSQL schema must match the three explicit
expected values; a descriptive label cannot authorize a target. Capability
tokens, upload ids, passwords, URLs, and credentials are redacted. A real
import is the same command without `--dry-run`; it is one target transaction,
resets PostgreSQL sequences only after rows are inserted, verifies full-row
content digests (not just counts/PKs), and rolls back on any mismatch.

Always retain operator evidence with `--report-output`. The tool writes the
JSON bytes directly as UTF-8, publishes the file atomically, and refuses to
overwrite an existing report. It probes a same-directory durable hard-link
publication before starting and publishes the final report before the import
transaction commits, so a write, sync, or destination race rolls the import
back. Do not capture the JSON through PowerShell or
another shell's stdout redirection: a console encoding conversion can replace
valid non-ASCII object-key characters even though the database import itself
is exact. When `--report-output` is used, stdout contains only the SHA-256 of
the report bytes.

## Required operator gates

1. Stop application writers and take a point-in-time SQLite snapshot. Do not
   use a live `-wal` file as an input. Retain the original, checksum it, and
   pass the copy by an explicit path.
2. Run the dry-run twice and retain the equal JSON checksums. Record the source
   SHA-256/size/mtime before and after; the temporary immutable snapshot SHA is
   also included. Review null
   tenant scope/visibility, FK orphans, cached vote-score mismatches, comment
   roots, session/part relationships, and the expected object manifest.
3. Create a **new empty** Supabase staging PostgreSQL database/namespace and
   use the normal isolated migration runner to move it to Alembic head. This
   rehearsal tool intentionally will not upgrade it for you.
4. Run one import. Run reconciliation again against source-copy and target,
   compare all counts/PK checksums and relationship checksums, and verify
   PostgreSQL sequences by inserting only in the disposable environment.
5. Legacy multipart sessions and their parts are **quarantined by omission**:
   no live R2 upload id, capability hash, ETag, or checksum reaches the target.
   The report retains only count/PK-hash quarantine evidence. The application
   therefore cannot resume, complete, or abort an imported provider session.
6. Only then, with separate approval, use a read-only object listing/head
   adapter to compare expected keys and known sizes. The adapter exposes list
   and head data only; it has no put/copy/delete method. No object migration is
   part of this phase.

## Fly snapshot procedure (documented only; do not execute from this tool)

Use Fly's approved operator access to stop or quiesce writers, create a
consistent SQLite backup from the mounted volume (SQLite `.backup`/snapshot,
not a raw main-db copy while WAL is active), and transfer that backup by the
approved read-only channel. Record only local file checksums in the rehearsal
record. Do **not** run `migration_runner.py`, Alembic, or this tool against a
mounted production volume. Do **not** put production URLs, bucket names,
credentials, or signed URLs in tickets or JSON reports.

The tool supports the checked-in pre-Alembic baseline, an Alembic-known schema,
and one explicitly named Fly production variant:
`fly_production_legacy_20260919`. Its deterministic schema contract includes
every table/column position, declared type, nullability, default, index,
foreign key, CHECK clause, and trigger. Any other structural difference is
rejected before stamping or importing.

For this Fly variant, the tool additionally requires the audited, schema-only
SQLite signature captured from the approved read-only snapshot procedure. It
uses a fail-closed token scanner over every `sqlite_schema` CREATE TABLE,
CREATE INDEX, and CREATE TRIGGER statement, then incorporates `table_xinfo`,
foreign-key facts, and `index_xinfo`. This catches generated/hidden columns,
AUTOINCREMENT, collations, deferrability, conflict behavior, STRICT/WITHOUT
ROWID table options, expression/partial indexes, and trigger changes that a
small set of PRAGMAs can miss. The signature and DDL are internal verification
material and are never emitted in reports or operator errors.

That named variant has only the historically observed differences: an optional
`song.lyrics` field and the six reviewed media/personal-log default/nullability
differences. The tool never alters or drops anything on the supplied source or
its immutable copied snapshot. Instead it creates a fresh exact revision-01
SQLite work database through the checked-in migration, copies only revision-01
columns in a foreign-key checked transaction, verifies source-to-work row
counts, primary-key hashes, full mapped-column typed digests, integrity, and
the exact normalized schema, then follows the normal stamp/upgrade/backfill
path.

`song.lyrics` is intentionally not mapped: every value must be exactly `NULL`
or the empty string. Any non-empty value, including whitespace, stops the
rehearsal; the operator-retained immutable source snapshot remains the recovery
source. The four
fields that become revision-01 `NOT NULL` (`media.processing_attempts`,
`media.transcoding_status`, `personal_log.processing_attempts`, and
`personal_log.transcoding_status`) must already contain no NULLs. Normalization
creates a private mode-0600, digest-only quarantine sidecar in the temporary
operator workspace; the JSON report carries only its variant, source snapshot
hash, counts, and typed hashes—never lyric values, primary keys, or local paths.

Phase 5 is evidence gathering. It does not authorize the later `NOT NULL`,
per-band uniqueness, Fly cutover, Supabase production, or R2 object contract.
