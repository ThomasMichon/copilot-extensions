# Opt-in outbound service Gateway

The `service-gateway` extra adds an asynchronous TLS service-role profile and
outbound connector SDK. It is selected explicitly; ordinary
`serve --role gateway --config ...` retains the existing index/credential
Gateway. This profile does not advertise those legacy operations, migrate their
state or install a second controller beside them.

```text
fleet-manager serve --role gateway --service-policy policy.json --tls-config listener.json
```

The command is a foreground service. The selected external deployment manager
owns its supervision, storage and eventual lifecycle; CLI invocation does not
install dependencies, enroll machines or open provider policies.

## Bootstrap and coherent live policy

Listener configuration is strict bounded JSON with schema
`fleet-manager.gateway-service-listener`, version `1`, stable `fleet`/`gateway`
identifiers, `host`, `port`, `origin`, `certificate`, `key` and `remote`.
Certificate/private-key values are external file references, not secret bytes.
The origin is explicit HTTPS with no path, credentials, query or fragment; it
must agree with the selected port and certificate's exact SAN. TLS is at least
1.2. Non-loopback binding requires `remote: true`. Wildcard SAN matching and
proxy termination are not supported in this first profile.

SDK tests may call `await start(Listener(...), policy_path)` with port zero;
the returned handle supplies the actual origin, app and asynchronous `close()`.
Foreground CLI serving requires an explicitly selected nonzero port.
Changing bootstrap/listener settings requires a controlled runtime change;
ordinary service policy reload does not.

The complete live document uses schema `fleet-manager.gateway-service-policy`,
version `1`, and exactly these fields:

| Field | Meaning |
|---|---|
| `manifest` | The validated [desired role manifest](gateway-manifests.md). Its revision governs the entire policy candidate. |
| `identity` | Existing `jwt-pop-v1` issuer, audience, public JWKS and `trust_valid_until`. |
| `bindings` | Exact `{provider, reference, jwk}` mappings for declared references; JWKs are public P-256 proof keys. |
| `callers` | Explicit `{principal, key_source, slots, methods}` grants for authenticated caller subjects. |

The selected source owner must authenticate and publish the local policy file
completely, preferably with an atomic file replacement. Parsing, a digest and
the file path do not independently establish that owner's authority. There is
no ambient repository lookup, remote key fetch, provider login or issuer service.
Private connector keys and access-token custody remain external.

The runtime reloads the explicit file off the event loop. Candidate validation
precedes one coherent context publication, binding desired state, trust,
resolved proof keys and caller grants together. Lower revisions and changed
content at the same revision refuse. Invalid/unavailable input preserves the
last valid context and reports a value-free error.
Equivalent no-ops preserve identity and replay state. An unchanged slot retains
its channel across unrelated updates; changed slots or caller grants lose their
old handles. Changed issuer trust replaces its nonce epoch and invalidates
previous handles/proofs rather than resetting replay state under old authority.

Revision custody remains process-local. The source/lifecycle owner must retain
accepted revision authority across restart; this implementation does not claim
a durable publication fence or automatic accepted-operation recovery.

## Authentication and attachment

The existing maintained-library verifier checks real access JWTs and DPoP
signatures, issuer/audience/time, nonce, access-token/key binding and replay.
Service identity must match both the configured slot principal and its resolved
proof key. Caller identity must match its configured subject/proof key, slot
grant and actual method. A connector is not implicitly a caller or administrator.
No legacy index/credential role is impersonated.

`GET /_fleet/challenge` supplies only the nonce over verified TLS, not inventory.
`POST /_fleet/service-admit` accepts a bounded slot/access/proof handshake whose
PoP target is the configured HTTPS origin and actual POST path. It returns a
30-second one-use attachment ticket, never placed in a query string.
The connector opens `/_fleet/service-channel` and submits the ticket as its
first bounded WebSocket message within five seconds.

Ticket preparation does not withdraw a healthy active channel. Promotion occurs
only after attachment confirmation is written, before the channel becomes
routable; current candidate authority is checked again after that await.
An unused/expired ticket cannot evict the
predecessor. Candidate and active generations fence stale attachment, late
frames and delayed cleanup. No failed close can retire a newer slot owner.

Public service requests use the `DPoP` authorization scheme and separate `DPoP`
proof header. `make_proof(..., method=...)` binds the actual method; its default
remains POST for existing callers. The proof target is the configured HTTPS
origin plus canonical path, excluding the query. Request Host/forwarded headers
are not identity or origin authority.

This is a common-profile SDK/direct-TLS implementation. Synthetic issuer tests
do not establish live Cloudflare, GitHub, Entra or broker interoperability.
Browser edge-login integration is not yet supplied; there is no anonymous UX
workaround. The later Cloudflare adapter must close the proxy/origin and caller
identity gates before production exposure.

## Routes, streams and explicit limits

The Gateway matches only configured namespace prefixes and declared methods,
then forwards over the current assigned channel. It never falls back to a
direct backend. An absent expected service is explicitly unavailable.
The connector's backend origin is selected by its deployment, not incoming
requests. Prefix stripping preserves a checked root-relative suffix and opaque
bounded query. Percent-encoded paths, dot segments, repeated separators,
backslashes and ambiguous path forms refuse in this initial implementation.

Only declared HTTP behavior is supported. Generic TCP, upgrades, encoded
request/response bodies and unsupported forwarding refuse explicitly. Request
credentials, cookies, client identity headers and hop-by-hop metadata are not
forwarded. Backend response headers use a narrow allowlist; authentication and
cookies are not passed through.

| Resource | Bound |
|---|---|
| All-request admission | Burst 256, refill 128/s; at most 128 active handlers |
| Control admission | Burst 32, refill 16/s |
| Pending attachment tickets / unverified sockets | 64 / 8 |
| WebSocket message / request body | 64 KiB / 32 KiB |
| Response chunk / total response | 16 KiB / 8 MiB |
| Concurrent pending requests | 32 globally, 8 per channel |
| Per-request queue / unacknowledged chunks | 8 / 1 |
| Request deadline / backend connect / backend idle read | 60s / 5s / 15s |

The combined JSON envelope must also fit the frame bound, including base64
and header escaping. Preflight rejection occurs before dispatch, so an
oversized local envelope is a known refusal, not an uncertain mutation.

`response-stream` routes send chunks before backend completion; ordinary `http`
routes buffer within the separate response bound. Acknowledgment follows
downstream consumption/write, not merely WebSocket receipt, providing bounded
backpressure. This is bounded response streaming, not an unlimited stream.

Current service and caller grants are rechecked at admission and chunk
checkpoints. Withdrawal, token expiry, disconnect, replacement and cancellation
cannot become a successful stream EOF. Lost-channel mutations report
`outcome-unknown` instead of being retried. A local checkpoint cannot fence an
already performed external effect.

Connector `connect_once()` owns one connection and every request it starts.
`run(stop_event)` adds bounded backoff with five consecutive-failure attempts.
Only an actually attached channel lasting at least 60 seconds resets that
budget; long failed admissions do not. It never retries requests from a lost
channel.

The runtime SDK's `status()` reports applied revision/generation, expected role
availability and the last classified reload error. It is a bounded embedding
diagnostic, not an unauthenticated global inventory endpoint.

## Evidence and remaining gates

Tests exercise actual TLS, access/proof verification, two outbound WebSocket
connectors, HTTP/HTML/query routing, live updates, invalid-candidate preservation,
key/caller withdrawal, replacement, genuine SSE, client cancellation, slow-peer
backpressure and resource limits. They are isolated in-process socket witnesses,
not separate-process/container or ARM64 deployment evidence.

Container packaging, Cloudflare/protected-origin interoperability, native
architecture qualification and live adoption remain in
[Gateway Role Tunnels](../../efforts/active/gateway-role-tunnels/README.md).
No provider-account, DNS, access-policy, production-key, firewall or running
facility configuration is modified by this source implementation.
