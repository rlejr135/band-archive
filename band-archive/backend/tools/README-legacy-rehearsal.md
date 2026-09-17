# Legacy SQLite → PostgreSQL rehearsal (Phase 5)

`legacy_rehearsal/rehearsal.py` is an **offline, one-shot rehearsal tool**. It
does not run at Flask/Fly startup, does not infer a database URL, and has no
R2 write operation. It copies the supplied SQLite file into a temporary
directory, upgrades and tenant-backfills that copy, then imports rows with
their original primary keys into an already-empty target at Alembic head.

Use a PostgreSQL staging database whose name/namespace is explicitly labelled
`rehearsal` or `staging`; never point it at production. The tool refuses any
nonempty target rather than attempting an idempotent merge. SQLite targets
require `--allow-sqlite-target` and are only a local compatibility test path.

```powershell
cd backend
python tools/legacy_rehearsal/rehearsal.py `
  --source-sqlite E:\operator\snapshot\band_archive.sqlite `
  --target-url $env:REHEARSAL_TARGET_URL `
  --target-label "supabase staging rehearsal" `
  --dry-run
```

The target URL can be supplied through `REHEARSAL_TARGET_URL`, but reports
contain only a dialect and a one-way host/database fingerprint. Capability
tokens, upload ids, passwords, URLs, and credentials are redacted. A real
import is the same command without `--dry-run`; it is one target transaction,
resets PostgreSQL sequences only after rows are inserted, verifies counts and
primary keys, and rolls the transaction back on any reconciliation failure.

## Required operator gates

1. Stop application writers and take a point-in-time SQLite snapshot. Do not
   use a live `-wal` file as an input. Retain the original, checksum it, and
   pass the copy by an explicit path.
2. Run the dry-run twice and retain the equal JSON checksums. Review null
   tenant scope/visibility, FK orphans, cached vote-score mismatches, comment
   roots, session/part relationships, and the expected object manifest.
3. Create a **new empty** Supabase staging PostgreSQL database/namespace and
   use the normal isolated migration runner to move it to Alembic head. This
   rehearsal tool intentionally will not upgrade it for you.
4. Run one import. Run reconciliation again against source-copy and target,
   compare all counts/PK checksums and relationship checksums, and verify
   PostgreSQL sequences by inserting only in the disposable environment.
5. Only then, with separate approval, use a read-only object listing/head
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

The tool supports only the checked-in pre-Alembic baseline or an Alembic-known
schema. It deliberately refuses older partial variants such as a `practice_log`
table or a `song.lyrics` column before stamping anything. Those need an
explicit, reviewed normalizer that maps every legacy field without guesswork.

Phase 5 is evidence gathering. It does not authorize the later `NOT NULL`,
per-band uniqueness, Fly cutover, Supabase production, or R2 object contract.
