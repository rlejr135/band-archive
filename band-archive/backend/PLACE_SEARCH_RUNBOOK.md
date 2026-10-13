# Public place search operations

`GET /api/search-places` remains public for the existing rehearsal location
picker. The proxy validates queries (100 characters maximum), limits each
socket peer to 10 valid searches per 60 seconds, caps provider calls at 60 per
60 seconds, keeps up to 512 results for 30 seconds, and allows at most two
simultaneous provider calls. Defaults can be overridden with the matching
`PLACE_SEARCH_*` Flask config values. Provider calls have a 2-second connect
and 3-second read timeout, no retries, and a 256 KiB response-body limit. The
stream reader checks a 5-second elapsed deadline between received chunks; a
currently blocked read can overrun that check by up to the read timeout, so
this is not a strict 5-second wall-clock ceiling.

These controls are process-local: each worker has its own counters/cache, the
effective cap multiplies by worker count, and all state resets at restart.
`request.remote_addr` is the rate-limit identity; forwarded IP headers are not
trusted. Behind Fly or another ingress, `remote_addr` may identify a shared
proxy, causing legitimate users to share one bucket; this is not a per-user
guarantee. Verify trusted-proxy configuration before considering forwarded
headers. Tune the limits only within separately reviewed bounds. This is an
abuse-reduction boundary, not a provider-wide quota. Do not raise limits based
on observed worker-local counts alone. For a durable cross-worker quota, choose
a shared store in a separate reviewed change.

Provider credentials continue to come from the existing runtime environment
variables `NAVER_SEARCH_CLIENT_ID` and `NAVER_SEARCH_CLIENT_SECRET`; do not put
them in this document, logs, or error responses. The endpoint does not log
search terms or provider response bodies.
