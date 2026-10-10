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
  "version": "phase9-preserve-identity-refresh-v1",
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
  --readonly-object-manifest-proof E:\operator\evidence\fly-latest-r2-list-head.json `
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
R2 list/head proof is required and compared with the refreshed manifest; the
tool cannot list or mutate R2 itself. The immutable main report is published
before commit with `status: "prepared"`; only after PostgreSQL commits and the
tool reconnects to verify the database does it atomically publish the adjacent
`.committed` marker. A prepared report is never success evidence. Any
pre-commit profile, source, FK, object-manifest, or report failure rolls back
the transaction; a post-commit marker failure leaves no completed marker.

Every operator-facing PostgreSQL URL must include `sslmode=verify-full` and an
`sslrootcert` file. The tool hashes that exact CA bundle and refuses to connect
unless it equals `--approved-tls-ca-sha256`; libpq host, database, service, and
options overrides are also refused. The private localhost PostgreSQL instance
used by the offline baseline builder is a separate internal fixture and never
accepts an operator target URL.

No Fly-volume action, deployment, Supabase DDL, R2 action, or git push is part
of this command. A new source snapshot is required immediately before every
actual mutation.
