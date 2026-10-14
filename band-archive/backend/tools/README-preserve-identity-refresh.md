# Claimed staging legacy-archive refresh

`legacy_rehearsal/preserve_identity_refresh.py` is the separately approved,
PostgreSQL-only path for refreshing the old Fly SQLite archive into the
already-claimed `deutteun-legacy` band in anything-staging. It does not weaken
`legacy_rehearsal/rehearsal.py`: that historical command remains an
empty-target, one-shot import and must reject this target.

The refresh tool performs no startup action, DDL, Supabase Auth action, or R2
operation. It has no R2 copy/delete/re-encode/upload capability; old keys are
preserved as database/object-manifest evidence only.

## Required proof

Before even a dry run, stop both Fly source and staging writers; make a fresh
point-in-time SQLite backup (never a live WAL main file), retain it, and create
the standard `phase9-write-freeze-v1` attestation bound to its exact file
fingerprint.

Create a reviewed local baseline sidecar from the saved v11 restore/source
proof and v12 post-claim proof. This tool cannot create a sidecar or bless a
current target as its own baseline. Its exact JSON contract is:

```json
{
  "version": "phase9-preserve-identity-refresh-v2",
  "target_writes_frozen": true,
  "provenance": {
    "baseline_proof": { "file": "offline-v11-baseline.json", "sha256": "<sha256>" },
    "post_claim": { "file": "postclaim-proof.json", "sha256": "<sha256>" }
  },
  "protected": { "table_digests": { "<every protected table>": { "count": 0, "sha256": "<digest>" } } },
  "legacy_domain": { "table_digests": { "<every legacy-domain table>": { "count": 0, "sha256": "<digest>" } } }
}
```

The sidecar pins full typed-row digests for all protected and legacy-domain
tables. Both referenced files must sit below the sidecar directory; the tool
hashes and parses them. `build_refresh_baseline_proof.py` derives the first
map from an expected-SHA-pinned v11 custom dump restored and migrated only in
a private local PostgreSQL instance, cross-linked to verified v11 restore and
v12 apply reports. `build_refresh_profile.py` accepts separately approved
expected SHA-256 values for that proof and a successful fresh read-only
post-claim proof; it requires the post-claim legacy domain map to equal the
independently derived baseline exactly. A self-generated current-target
baseline is not valid evidence. The current retained failed post-claim reports
must be replaced by a successful fresh proof before this command can run.
The target must have exactly one active verified platform-admin, one matching
bootstrap row, one claimed `deutteun-legacy` band, one active owner membership,
one redeemed initial-owner claim, and exactly three existing audit events.
Any new user, other band, membership, claim, audit event, pending request or
invite, direct upload, suggestion vote, multipart row, or domain-content
change stops the tool.

The target must already be migrated to the current Alembic head `20261014_14`. That
one-way PostgreSQL migration disables browser Data API access to the reviewed
application tables; it is not a production cutover authorization. It refuses
to begin if the migration owner has unsafe global default ACLs for `PUBLIC`,
`anon`, or `authenticated`. Remediating such global defaults is a separate,
reviewed database-security operation; do not alter them merely to make this
refresh command proceed.

The v2 read-only object proof is a separately reviewed JSON document. It must
contain the exact frozen-source fingerprint and a complete list/head inventory:
`version: "phase9-r2-readonly-manifest-v2"`, a `capture` with method
`operator-readonly-list-head`, capture ID, complete flag, count, canonical
inventory SHA-256, account SHA-256, and bucket SHA-256, plus unique
`{key, size}` entries. The complete proof bytes, and separately the expected
account and bucket SHA-256 values, are supplied independently to the CLI; the
canonical inventory SHA-256 is recomputed from the proof entries. For every
inventory object classified by reconciliation as orphaned or
retired-but-still-present, a second, SHA-pinned approval document must provide
exactly one source/account/bucket/inventory/capture-bound,
`operator-reviewed`, `retain_no_mutation` disposition. It can only retain;
it cannot approve object mutation.

These pins prove that an operator reviewed the supplied local bytes; they do
not authenticate a remote R2 response. The repository provides three separate
operator helpers, but none authorizes remote access, transport, a writer
freeze, or a refresh by itself:

1. `capture_sqlite_snapshot.py` runs only on the approved source machine. It
   opens the named `/data` SQLite source with `mode=ro`, creates a SQLite
   `backup()` in an owned `/tmp` workspace, integrity-checks that backup, and
   writes a private capture manifest. Its status is always
   `captured_not_frozen`; it does not copy a raw WAL main file and cannot issue
   `phase9-write-freeze-v1` authority.
2. `capture_readonly_r2_inventory.py --plan` makes no request and reports the
   fixed request ceiling. The capture mode uses only existing Fly `S3_*`
   environment credentials, derives and hashes the canonical HTTPS R2 account
   from the endpoint, lists the exact configured bucket through every cursor,
   heads every key, and requires two full passes to agree on key, size, ETag,
   and the full-precision listed LastModified. LIST and HEAD timestamps must
   agree at UTC-second precision because HEAD's HTTP date can omit fractions;
   the listed fraction remains in the artifact so changes between passes fail
   closed. It fails closed on pagination ambiguity, mutation, or the
   default 1,000-object ceiling rather than retaining a partial inventory. Its
   plan reports a conservative two-pass request maximum (including short
   truncated pages) before it constructs a provider client.
   Its private raw artifact contains keys and metadata; terminal output carries
   only the digest, actual count, and request totals. It has no put, copy,
   delete, upload, multipart, or re-encode operation.
3. After an approved manual transfer of the private source backup, source
   manifest, and raw R2 artifact, run `build_readonly_object_proof.py` locally.
   It requires independently supplied SHA-256 pins for both transferred
   manifests and produces the strict v2 proof plus a provenance sidecar. That
   sidecar deliberately says `local_file_identity_only` and
   `writer_freeze_attestation: false`: it binds exact local files, not a claim
   that the R2 observation and SQLite backup were simultaneous.

The raw capture, local binding sidecar, and a real reviewed retain-only
disposition approval are therefore still required before any refresh is
eligible to run. Do not invent a proof from this guide, from a database
manifest, or from a test fixture; until those artifacts and separate writer
freeze approval exist this is a no-go gate. Capture consistency is also not a
schema/model or cross-source verdict: preserve the existing rehearsal and
refresh reconciliation gates separately.

## Invocation

Use an approved secure launcher to inject `REHEARSAL_TARGET_URL` only into the
child process. Do not put a URL, password, token, object key, or profile
contents on a command line, ticket, or chat transcript.

```powershell
Push-Location backend
<approved-secure-launcher> -- .\.venv-test\Scripts\python.exe `
  tools\legacy_rehearsal\preserve_identity_refresh.py `
  --source-sqlite E:\operator\snapshots\fly-latest.sqlite `
  --expected-target-host '<approved staging pooler host>' `
  --expected-target-database '<approved staging database>' `
  --expected-target-schema public `
  --approved-tls-ca-sha256 '<reviewed CA bundle SHA-256>' `
  --write-freeze-attestation E:\operator\attestations\fly-latest-freeze.json `
  --reviewed-baseline-profile E:\operator\evidence\v11-v12-reviewed-profile.json `
  --expected-reviewed-baseline-profile-sha256 '<reviewed profile SHA-256>' `
  --readonly-object-manifest-proof E:\operator\evidence\fly-latest-r2-list-head.json `
  --expected-readonly-object-manifest-proof-sha256 '<reviewed proof SHA-256>' `
  --expected-object-account-sha256 '<approved R2 account SHA-256>' `
  --expected-object-bucket-sha256 '<approved R2 bucket SHA-256>' `
  --approved-object-disposition-manifest E:\operator\evidence\fly-latest-r2-dispositions.json `
  --expected-approved-object-disposition-manifest-sha256 '<reviewed disposition SHA-256>' `
  --report-output E:\operator\reports\refresh-dry-run-1.json `
  --dry-run
Pop-Location
```

Run and compare two dry runs from the same frozen SQLite file. The final run
uses a fresh report filename and removes `--dry-run`. It takes a PostgreSQL
advisory lock and fail-fast table locks, re-proves the sidecar under lock,
deletes only the proven legacy-domain graph child-to-parent, inserts frozen
source rows parent-to-child while remapping only the legacy `band_id`, resets
sequences, and proves protected identity is unchanged. Source rows requiring
membership/account mapping are refused. A separate snapshot-bound read-only
R2 list/head proof and a complete retain-only disposition approval are required
and compared with the refreshed manifest; the tool cannot capture, list, or
mutate R2 itself. The immutable main report is published before commit with
`status: "prepared"`; only after PostgreSQL commits and the tool reconnects to
verify the database does it atomically publish the adjacent `.committed`
marker. A prepared report is never success evidence. Any pre-commit profile,
source, FK, object-manifest, disposition, or report failure rolls back the
transaction; a post-commit marker failure leaves no completed marker and the
prepared report remains retained evidence.

Every operator-facing PostgreSQL URL must include `sslmode=verify-full` and an
`sslrootcert` file. The tool hashes that exact CA bundle and refuses to connect
unless it equals `--approved-tls-ca-sha256`; libpq host, database, service, and
options overrides are also refused. The private localhost PostgreSQL instance
used by the offline baseline builder is a separate internal fixture and never
accepts an operator target URL.

For the Fly production app, `fly.toml` places the tracked public Supabase root
CA at `/app/certs/supabase-prod-ca-2021.crt` on every deploy. The production
`DATABASE_URL` must retain `sslmode=verify-full` and set `sslrootcert` to that
exact path. Keep the focused packaging test in `tests/test_fly_ca_packaging.py`
passing when updating either the CA or its Fly file mapping; do not replace
hostname verification with `sslmode=require`.

No Fly-volume action, deployment, Supabase DDL, R2 action, or git push is part
of this command. A new source snapshot is required immediately before every
actual mutation.

## PostgreSQL application startup after cutover

Application startup never creates, stamps, migrates, or repairs PostgreSQL
tables. Before storage initialization, upload-session recovery, or an audio
worker, it verifies a bounded read-only snapshot of the exact Alembic head,
full schema contract, uniquely validated tenant marker, owner CRUD access,
and Data API RLS/ACL isolation. A failure prevents startup; use the separately
reviewed operator path rather than weakening the gate. SQLite retains its
existing compatibility startup behavior.

Keep `PG_AUDIO_WORKER_ENABLED=false` and
`PG_MULTIPART_RECOVERY_ENABLED=false` during cutover. These are independent,
PostgreSQL-only explicit opt-ins. `start_worker=False` always suppresses the
factory worker; `start_worker=True` cannot bypass the PostgreSQL audio flag,
even in tests. Direct worker startup also requires the successful internal
validation marker and the audio flag. New video records may remain `queued`
while audio processing is disabled; normal wake/enqueue behavior is unchanged.

Enabling the audio flag recovers stale processing rows and processes the
existing media/personal-log video backlog, including FFmpeg and R2 writes.
It therefore needs separate CPU/media-processing approval. Enabling multipart
startup recovery can update session rows and abort expired R2 uploads and
requires separate provider-mutation approval. Neither flag is a writer-freeze
attestation or a dynamic stop mechanism for an already running process.
API writers, owner clients, existing presigned URLs, and in-flight uploads
still require the separately reviewed freeze/drain procedure before refresh.
