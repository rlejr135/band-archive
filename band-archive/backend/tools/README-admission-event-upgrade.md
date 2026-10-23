# Admission/event revision 23 operator tool

`admission_event_upgrade.py` is an operator gate for database revision
`20261021_22` → `20261023_23`. It does not load dotenv files. The connection
password is obtained only from the protected process environment variable
`REHEARSAL_TARGET_URL`; it is never accepted on the command line or copied to
reports.

An invocation without `--rehearse` or `--apply` prints a ready/no-action status
and does not inspect credentials or create files. The rehearsal captures one
repeatable-read snapshot of the public schema, makes and locally restores a
custom PostgreSQL 17 dump, compares all 38 public tables, old-column row
digests, catalog state, ownership, ACL/RLS/role state and the R2 key manifest,
then runs revision 23 only on the owned restore. The local restore reproduces
and checks public ACL/RLS/default privileges, schema ACL and relevant role
memberships; it deliberately does not copy remote role LOGIN, SUPERUSER or
BYPASSRLS attributes to localhost. The full remote role and membership
fingerprint is compared exactly before and after a production apply. The tool
stops the owned server and records private evidence under `backend/tools/.state`.

Review the tool, migration and source commit before running rehearsal. Supply
their reviewed pins explicitly; the helper pin is the SHA-256 of this file and
the migration pin is the SHA-256 of the revision 23 migration. Run from the
backend directory with the protected connection carrier already provided by
the operator environment:

```powershell
$helper = (Get-FileHash .\tools\admission_event_upgrade.py -Algorithm SHA256).Hash.ToLowerInvariant()
$migration = (Get-FileHash .\migrations\versions\20261023_23_admission_positions_rehearsal_type.py -Algorithm SHA256).Hash.ToLowerInvariant()
& .\.venv-test\Scripts\python.exe .\tools\admission_event_upgrade.py `
  --rehearse --approved-helper-sha256 $helper --source-commit <reviewed-source-commit> `
  --migration-sha256 $migration
```

The tool is intentionally a two-stage workflow. A production apply requires
the root operator's separate review, a fresh verified rehearsal, and explicit
SHA-256 pins for the report, manifest, dump, helper, migration and source
commit, plus the exact `ROOT_REVIEWED_ADMISSION_EVENT_V22_V23_APPLY` gate token.
Never copy credentials into command arguments. Apply opens a bounded
transaction, holds a write barrier across all public tables, confirms the
source still matches the backup, applies only the three reviewed columns and
one check constraint, and performs a fresh read-only postcheck. If commit
acknowledgement is lost, the report records the commit state as unknown for
operator reconciliation; it does not retry or roll back automatically.
The post-commit read-only check can also fail after application writes resume;
in that case the report retains `committed: true` and `status: failed`. The
in-transaction preservation proof is the migration check; reconcile the
post-commit observation before any new operator action.

This tool does not modify application data, managed Auth, R2 objects, mail,
secrets, global PostgreSQL configuration, or infrastructure. Do not use it to
perform another revision or to bypass a failed gate. Keep evidence private; it
contains database inventory and row digests even though reports omit row data
and credentials.
