# PostgreSQL cutover: stage 1 performance diagnosis

This is a diagnosis, not a deployed performance change. No heavy production
endpoint was called for these measurements. Existing production inventory is
13 songs, 83 media and 11 rehearsals; the local synthetic fixture also contains
42 song/rehearsal associations, connected into one graph. Synthetic content is
not a copy of production content.

## Confirmed local bottlenecks

`routes/songs.py::get_songs` calls `legacy_scope.validate_legacy_graphs`.
The validator starts a new visited set for each song and repeatedly traverses
the same connected graph. Relationship loading adds further SQL.

| Local SQLite request | SELECTs | Graph-validation SELECTs | Other SELECTs | Local elapsed |
| --- | ---: | ---: | ---: | ---: |
| Current v1 `/songs` | 7,865 | 7,748 | 117 | 2.719277 s |
| Unshipped single-invocation shared-set proposal | 598 | 596 | 2 | 0.208026 s |
| Current v2 public song list | 5 | — | 5 | 0.016059 s |

The candidate was monkeypatched only inside a test. Its JSON response matched
the baseline exactly. A cross-band child inserted after an earlier successful
request was still rejected with 404 before any URL signing. No validation
cache may cross a validation call, request or write boundary. The proposed
backend optimization remains **unshipped**; stage 2 instead stops the frontend
from eagerly loading the legacy graph and uses the v2 routes.

The frontend static review found that anonymous `/` mounts the legacy app and
eagerly requests `/songs`, dashboard statistics, featured content and calendar
data: four legacy reads. The Docker command does not set Gunicorn workers or
worker class, so its default single synchronous worker makes a slow request
block other requests handled by that worker. No production queueing or worker
timing was measured here.

For standard Supabase tokens without a signed email-confirmation claim,
`auth_service._email_is_confirmed` fetches the trusted `/auth/v1/user` on each
authenticated request. Mocked `/me`, `/bands` and workspace-by-slug requests
made one confirmation call each (three total), and issued 3, 7 and 7 SELECTs
respectively. Stable user synchronization committed once per request, but
issued no UPDATE in this fixture. Mocked network time is not real Auth latency.
Do not add an authentication cache that weakens verification or revocation.

PostgreSQL network round-trip latency, connection establishment/pooling and
production endpoint times were not profiled. Thousands of sequential queries
amplify any nonzero database round-trip cost, but these local timings must not
be represented as measured production latency. Worker, database or Auth
changes need a separate reviewed design and explicit deployment authorization.

## One-time legacy ownership acquisition retirement

The already-claimed owner, membership, imported content, claim-token history,
audit records and migrations remain intact. Retire only these four web entry
points (return 404):

- POST `/api/v2/platform/bands/<id>/legacy-ownership-claim`
- GET `/api/v2/platform/legacy-ownership-claims/eligible-bands`
- POST `/api/v2/platform/bands/<id>/legacy-ownership-claim/invalidate`
- POST `/api/v2/bands/<id>/legacy-ownership-claim/redeem`

Historical service helpers and their lifecycle/race tests remain available to
offline tests, not to web routes. Invite/join/role-management routes are not
changed. This retirement is reviewed separately from this diagnosis.

## Reproduce

Use the project test Python with a new owned TEMP/basetemp and no inherited
production PG URL/options. Run:

```text
.venv-test/Scripts/python.exe -m pytest -q -s tests/test_performance_diagnosis.py --basetemp <new-owned-path>/pytest
```

All object storage and Auth responses are mocked; three tests pass. Optional
`PERF_DIAGNOSTIC_OUTPUT` writes aggregate query evidence exclusively to a new
private output file. The captured query and Auth evidence hashes are
`ac68740594dd5cd52f71b9ac7e2e0f042026b829f944c9a7bde38f6b269c33c9`
and `539fd31ac9bc28f38d06f83f1e06c4b3726cf9a723b9e403825953eeb85894f2`.
