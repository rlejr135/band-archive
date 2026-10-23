# Featured media release safety review — 2026-10-24

Scope: static review of revision 23 → 24, the new pinned operator and its tests,
historical metadata boundaries, and the new representative-media API paths.
No production connection, private state/evidence read, test execution, source
change, or deployment was performed by this reviewer. The only review write
is this report. The applicable root AGENTS.md and Supabase skill were read.

## Confirmed blocker: inventory comparison rejects the intended transition

At review time, `backend/tools/featured_media_upgrade.py::without_index`
normalizes only `alembic_versions` and the named index in `catalog.indexes`,
then `compare_preserved` compares the entire normalized inventory. The
inventory contract supplied by root includes the ledger table row digest,
top-level index count, and an object hash derived from FK/index/trigger
definitions. Those fields necessarily change when the ledger advances and
the index is added. A valid rehearsal/apply therefore fails its preservation
gate and rolls back. Priority: P1; production apply is not approved while
this remains unresolved.

The fix must retain exact comparison of all non-ledger rows, existing owners,
security, keys, and catalog definitions. Permit only one ledger transition
and the exact new index on `public.media`; assert the index-count delta rather
than masking all derived fields without replacement. A regression fixture
must include the real ledger/count/hash fields. The implementation agent has
been notified; fix and validation are pending as of this report's initial write.

## Nonblocking limitation: linked-rehearsal restriction preflight

Revision 24's PostgreSQL and SQLite preflight checks include direct resource
restrictions and linked-rehearsal deletion tasks, but omit an active content
restriction inherited through `rehearsal_songs` when the media's direct
rehearsal differs or is null. Such an existing featured row can pass migration.
Priority: P2 consistency follow-up, not a deployment privacy blocker: the
migration changes no flags or application rows, and runtime restriction
predicates exclude these items from new listing, management candidates,
selection, and URL signing. Do not describe the migration preflight as
covering every inherited restriction.

## Other reviewed boundaries

- Owner writes also allow a current DB-backed active, email-verified platform
  administrator without band membership. The actor and active owner
  membership are re-read with locks; no email or editable metadata shortcut
  was found in these paths.
- Management candidate/action responses return bounded metadata, not playback
  URLs or raw storage identifiers. Ordinary reads retain tenant, visibility,
  inherited restriction, pending deletion, and signed-URL TTL checks.
- The migration adds one partial unique index and advances the ledger through
  the operator. No application-row, R2-object, ACL, role, or infrastructure
  mutation is requested by the new operator.
- Revision-24 PostgreSQL migration/startup coverage is in
  `test_admission_event_migration.py::_exercise(postgres=True)`; it checks the
  explicit upgrade, preserved rows/security, schema, and read-only startup.
  Root reported its focused checks passed. This reviewer did not rerun them.
- Exact index verification should reject unexpected INCLUDE columns, null
  semantics, access method/opclasses, or deferred/non-live state as well as
  wrong keys, predicate, table, uniqueness, readiness, and validity. This was
  sent to the operator implementation agent for review.

## Release decision

No production apply GO yet. Resolve the P1 operator comparison, validate the
realistic inventory/index tests, and obtain a verified backup/restore rehearsal
using the final reviewed source pins. Private helper internals and production
evidence were outside this review; root must verify those before separately
authorizing apply. The linked-rehearsal preflight limitation alone does not
block deployment.

## Addendum — 2026-10-24 static rereview after operator correction

This addendum supersedes the initial report's pending-source-fix status;
the initial report is preserved above.

The implementation agent has corrected the P1 source defect.
`compare_inventories` checks the one-row revision transition, exact index-count
delta, and preserved inventory. `public_object_manifest` and
`verify_object_transition` compare exact FK/index/trigger definitions and
remove only the reviewed index on `media`. Source capture, local restore,
precommit, and fresh postcommit call sites carry the captured manifest. The
realistic regression fixture now includes ledger digests, object hashes, and
object counts. Static rereview found no remaining P1 source defect.

The updated exact index verifier checks public/media association, two key and
total attributes, uniqueness, validity, readiness, immediate uniqueness,
default null semantics, btree/default opclasses/ordering options, exact
predicate, and canonical full index definition. The reviewed DDL is adequately
constrained by these checks.

Static review permits root to proceed with local targeted checks and, after
they pass, the read-only backup/local restore rehearsal. Production apply is
still conditional on validation of the final code, final reviewed source pins,
and a fresh verified rehearsal. This reviewer did not execute tests or inspect
private evidence. The linked-rehearsal preflight limitation remains a
documented nonblocking P2 follow-up.

## Addendum — 2026-10-24 correction after actual provider rehearsal

The first revised operator failed a real read-only rehearsal with
`public object inventory summary is incomplete`. Root reported remote writes
were false and the owned local server stopped. The preceding static review
relied on a supplied structural description from a different tracked helper;
that description did not match the pinned provider actually used. Its
inventory-shape validation was therefore insufficient. This addendum corrects
the earlier status without changing the preceding report or addendum.

With the expanded code-read authorization, this reviewer directly inspected
the pinned helper chain and the source of
`backup_staging_v12_security13_rehearsal.py::public_inventory` and
`stable_hash`. No dump, manifest, production evidence, credentials, or secrets
were read. The actual inventory has exactly `tables`, `catalog`,
`catalog_sha256`, `owners`, and `alembic_versions`. Its catalog has `columns`,
`constraints`, `functions`, `indexes`, `sequence_dependencies`, `sequences`,
and `triggers`. Index entries contain table, name, definition, validity, and
readiness. The hash uses ASCII-escaped, sorted, compact JSON.

Independent static rereview of the final correction passes. It verifies
the original catalog checksum before normalization, requires the actual
inventory/catalog shape, removes only `(media, uq_media_featured_band_song)`,
and recomputes the normalized checksum with the provider's exact canonical
JSON algorithm. All remaining catalog entries, non-ledger rows, and owners
remain equal. The ledger remains exactly one OLD-to-NEW row transition and
the catalog index list increases by exactly one. Separate exact new-index
definition and FK/index/trigger transition checks remain in the call path.

The new local PostgreSQL integration regression invokes the actual pinned
inventory provider in an isolated imported module, overriding only its test
table allowlist. Root reported all seven final operator tests passed in
session 96621 (13.19 seconds). This reviewer did not execute tests.

No additional blocking source defect was found. The final corrected source
may proceed to a new read-only backup/local restore rehearsal with final
reviewed pins. Production apply remains conditional on that fresh verified
rehearsal. The nonblocking P2 limitation above is unchanged. This review report
is frozen after this append; root owns subsequent commit and rehearsal work.
