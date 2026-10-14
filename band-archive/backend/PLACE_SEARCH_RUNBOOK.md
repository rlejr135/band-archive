# Band-scoped place search operations

`GET /api/v2/bands/{band_id}/search-places?query=...` requires a valid
authenticated user and an active owner, admin, or member membership in that
band. The membership check runs before query validation, cache lookup, quota
checks, or provider access. Pending, rejected, and left memberships do not
qualify; platform-admin status alone does not bypass membership. The response
retains the existing bare list of bounded Naver place objects. The former
public `GET /api/search-places` endpoint is retired and always returns HTTP
410, including when a bearer token is supplied or legacy compatibility writes
are enabled.

The proxy validates queries (100 characters maximum), limits each verified
local user to 10 valid searches per 60 seconds across bands, caps provider
calls at 60 per 60 seconds, keeps up to 512 results for 30 seconds, and allows
at most two simultaneous provider calls. Defaults can be overridden with the
matching `PLACE_SEARCH_*` Flask config values. Provider calls have a 2-second
connect and 3-second read timeout, no retries, redirects disabled, and a 256
KiB response-body limit. The stream reader checks a 5-second elapsed deadline
between received chunks; a currently blocked read can overrun that check by up
to the read timeout, so this is not a strict 5-second wall-clock ceiling.

These controls are process-local: each worker has its own counters/cache, the
effective cap multiplies by worker count, and all state resets at restart.
The personal quota key is the server-authenticated local user ID, not a
caller-supplied header or socket address. Global upstream budget, cache, and
concurrency state remain process-local; multi-worker deployments multiply
those limits and restart resets the state. This is an abuse-reduction boundary,
not a provider-wide quota. Tune limits only within separately reviewed bounds.
Do not raise limits based on observed worker-local counts alone. For durable
cross-worker enforcement, choose a shared store in a separate reviewed change.

Provider credentials continue to come from the existing runtime environment
variables `NAVER_SEARCH_CLIENT_ID` and `NAVER_SEARCH_CLIENT_SECRET`; do not put
them in this document, logs, or error responses. The endpoint does not log
search terms or provider response bodies.
