# Optional PR replay Gateway adapter

`agent_pull_requests.watch_gateway.WatchGateway` embeds the
[replay core](watch-replay.md) in Fleet's real
[`caller-context-v1` service connector](../../../fleet-manager/docs/gateway-service-profile.md).
Install the PR package's optional `fleet` extra for this adapter or its client. The
ordinary CLI, process waiter and native callback daemon stay independent of
Fleet. Importing this module requires that explicit optional dependency.

This is a source-level serving adapter, **not an activated service**. It adds no
listener, daemon, installer, placement assignment, forge client, remote PR
mutation or Dispatch consumer. It does not replace the native watch owner.

## Trusted configuration and operation boundary

The embedding service constructs one `ReplayStore` with its canonical absolute
durable directory and fleet/service/installation namespace. `provision()` is
explicit first creation and refuses any existing state. Normal opening never
provisions or treats a missing, corrupt or foreign-namespace store as empty.
The complete envelope lives in `watch-replay.json`, never the native daemon's
list-format `watch-subscriptions.json`.

Construct `WatchGateway(store, fleet=..., gateway=..., slot=..., bindings=...)`
beside the trusted connector. Each immutable
`WatchScopeBinding(principal, target, ConsumerScope(consumer, account, repo))`
selects exactly one configured scope. `target` is an opaque lowercase alias,
not a repository or credential identifier. Multiple authorized callers share
state only if trusted configuration deliberately binds them to the same
`ConsumerScope`.

Pass `adapter.handler_binding()` to `ServiceConnector(handler=...)`; do not
expose `adapter.handle` as an unauthenticated HTTP backend. Only the real
connector's verified TLS channel supplies fleet/Gateway/slot/principal/expiry.
Constructing SDK dataclasses locally does not authenticate a caller. The
adapter verifies its configured owner, selected principal/alias and current
expiry again at the operation/commit boundary. A body, query or header cannot
select a consumer, provider account, repository, namespace or callback.

Gateway retains current channel/caller-policy admission and cancellation. The
adapter's scope configuration is fixed for its lifetime. Revocation uses the
same failure contract as network disconnect, not a distributed commit barrier:
Gateway stops admission/emission and cancels affected requests without waiting
for remote disk effects to settle. The connector closes each request's local
`RequestLifetime` before cancelling its handler, including channel loss,
replacement, deadline failure and connector shutdown.

The adapter checks that lifetime before lookup, commit and result delivery.
Work queued before a known disconnect cannot start a later transaction.
An already-started commit can still finish: cancellation is not rollback, and
disconnection is not proof of nonacceptance. Reconcile the original request/event
identity under fresh current authorization; never invent another wait identity,
switch accounts or arm a process fallback. The lifetime is local cancellation
custody, not an offline permit or a second policy/enrollment authority. Durable
accepted-revision custody, real provider enrollment and lifecycle integration
remain separate production requirements.

## Versioned bounded operations

Targets below are **connector-relative**, after Gateway removes the assigned
route prefix. Queries, encoded aliases, unknown fields and duplicate/non-finite
JSON members are refused. A nonempty body requires `application/json`.
No operation accepts arbitrary argv, provider URLs or credentials.
Integer timestamps/deadlines that would round or overflow the registry's
finite-float representation are refused before persistence.

| Method / target | JSON fields |
|-----------------|-------------|
| `GET /v1/watch/<target>/capabilities` | No body. Returns protocol, implemented operations and replay bounds, not global scope/credential inventory. |
| `POST /v1/watch/<target>/register` | `request_id`, original `issued_at`, positive integer `number`, `until` array; optional original `deadline_at`. |
| `POST /v1/watch/<target>/inspect` | `request_id`, original `issued_at`. |
| `POST /v1/watch/<target>/pending` | Empty object/body, or `limit` from 1 through 16. |
| `POST /v1/watch/<target>/acknowledge` | `request_id`, original `issued_at`, exact `registration_id`, exact `event_id`. |
| `POST /v1/watch/<target>/cancel` | `request_id`, original `issued_at`, exact `registration_id`. |

The response is the replay core's receipt/batch, without an extra envelope.
Requests fit the existing 32 KiB Gateway body bound; event batches fit the
core's 24 KiB/16-event bound. Responses are JSON with `Cache-Control: no-store`.
Pending reads first durably sweep elapsed deadlines; they never ACK events.
All other replay, retention and exact-completion semantics are unchanged.

Malformed input is `400`, unbound/expired callers are `403`, unknown paths are
`404`, wrong methods `405`, oversized bodies `413`, and non-JSON bodies `415`.
Draining, capacity or transaction-lock contention returns explicit `503`
refusals. A detected request disconnect returns
`503 {"status":"uncertain","reason":"watch_request_disconnected"}` to a
direct embedding; a disconnected transport cannot deliver that response.
Missing, invalid or inaccessible durable state returns
`503 {"status":"uncertain","reason":"watch_store_unavailable"}` without
inventing nonacceptance. A post-replace uncertain durability acknowledgement
returns the replay core's `uncertain/state_commit_uncertain` outcome.
Neither this nor a transport failure licenses a second request identity or a
process fallback. Reconcile the original request/event identities.

## Finite typed replay client

`agent_pull_requests.watch_replay_client.ReplayClient` uses Fleet's finite
[`ServiceCaller`](../../../fleet-manager/docs/gateway-service-profile.md)
for the operations above. The embedding supplies the caller's current async
token callback, externally provisioned proof key and TLS trust. This client
does not discover a Gateway, mint credentials, start a waiter, schedule polls
or persist consumer intent. It is an optional source API, not a new CLI verb
or a deployed Dispatch consumer.

Construct `ReplayClient(caller, route_prefix="/api", target="project",
repo="example/project")` with the trusted route prefix, opaque alias and
expected repository. The prefix is Gateway-facing; the expected repository
checks returned events and is never sent as an authority label.
`await client.capabilities()` validates the exact versioned protocol,
operations, batch bounds and receipt retention. Missing/incompatible
capabilities refuse rather than silently selecting a legacy waiter.

The consumer durably saves `ReplayRequest(request_id, issued_at)` and its
criteria before the first `register(request, number=..., until=(...),
deadline_at=...)`. The client never generates an identity or resamples a
deadline. Registration, inspection and exact completion return immutable
`ReplayReceipt` or `ReplayOutcome` values; an uncertain/conflict/unavailable
outcome is not acceptance. `pending(limit=...)` returns `ReplayBatch` or an
uncertain outcome. Each immutable `ReplayEvent` exposes generation/event
identity and a detached `payload()` preserving the complete native
notification. Consumers still must match their persisted original intent
and current generation before applying effects. A response is not
authorization for that effect.

Every method makes exactly one SDK call, which uses fresh current credentials
and its own finite deadline. Responses require bounded JSON and valid
receipt/event identity, including expected repository and native notification
validation. There is no retry, background task, capability cache or process
fallback. `ReplayClientError` retains only a static code, optional HTTP status
and uncertainty flag, never server body or credential values.
Any unsuccessful HTTP mutation response remains uncertain, **including 403**:
policy withdrawal can occur after dispatch while a remote commit is already
in flight. Cancellation propagates without claiming rollback. Reconcile
`inspect(original_request)` under current authorization; if registration
must be retried, use the same original intent. Commit the consumer effect
idempotently before acknowledging its exact request/registration/event.
Neither an error nor an unknown inspection licenses a second wait identity.

## Transaction custody and drain

Each disk transaction uses the existing `OperationLock` across load, namespace
validation, replay operation and complete-envelope atomic commit. It checks
caller expiry and the supplied request-lifetime checkpoint before loading,
again before committing and before returning a result. An independent
`ReplayStore` embedding may omit the optional `checkpoint` callback; that
provides no connector-disconnect fencing. There is no
in-memory core shared across independent transactions and no stale cached
writer that can overwrite a later transaction. A second process/thread
receives explicit contention, never overlapping writes. This is local
transaction exclusion, not a distributed-volume lock, active daemon lease,
placement fence or graceful deployment/cutover protocol.

At most eight operations are offloaded at once; they do not block the
connector's asyncio loop. Cancelling a request leaves already-started disk work
tracked and holding its own custody until it finishes. `await
adapter.drain(timeout=...)` stops new admission and waits for that work,
including disconnected requests. A timeout is a failure to drain, not
permission to release custody, replace storage or advertise safe retirement.
The embedding lifecycle must call drain and finish its own canonical update
protocol. This module starts no resident process or new lifecycle supervisor.

## Focused validation

```text
python tools/run-plugin-tests.py agent-pull-requests --test-file tests/test_watch_replay_store.py --test-file tests/test_watch_gateway.py --test-file tests/test_watch_gateway_transport.py --test-file tests/test_watch_replay.py --test-file tests/test_watch_replay_client.py --test-file tests/test_watch_replay_client_transport.py
```

The tests reuse Fleet's actual synthetic-issuer/TLS/PoP/WebSocket fixture:
configured subject isolation, header/body spoof refusal, lost response
reconciliation, adapter/connector replacement from disk, exact ACK retry and
policy withdrawal all cross the real serving path. A separate real child
process proves transaction-lock contention and restart reconciliation.
Cancellation/drain and eight-operation bounds have focused handler tests.
The real TLS tests pause before replay lookup and inside an already-started
atomic write, then exercise both policy withdrawal and channel loss. Publication
does not wait for the held write. The same original request reconciles after
reauthorization/reconnection, without replacement or loss of transaction custody.
The typed client tests traverse that same real path for original-intent retry,
disk restart, scoped isolation, exact ACK/cancel and policy/channel/client
cancellation both before lookup and during an in-flight commit. Unit checks
assert one SDK call per operation, finite byte/batch limits, strict protocol
responses and detached event payloads. These are deterministic work-count
checks and synthetic native TLS integration, not production latency/CPU
qualification or installed fleet acceptance. A deployed observer/consumer
still owns its polling frequency and representative performance measurements.

## Explicit native observation credential command

The existing `watch_provider.fetch_snapshot(repo, number, command, ...)` bridge
accepts an optional `credential_command` JSON-serializable argv list supplied
by the embedding service's **trusted configuration**. It forwards that exact
non-secret command to Worktrees' snapshot-only
[`--credential-command`](../../agent-worktrees/docs/pr-observation.md) option.
The native driver, not this bridge, renews and validates typed forge material
before each provider command/HTTP request. A missing or failed selected command
does not select the default GitHub path, ambient login or a process waiter.
Omitting the keyword preserves the existing bridge behavior.

The embedding owner supplies an absolute attributable executable/interpreter
prefix, enrolled worker policy, account/source/forge binding and an available
credential controller. Credential-backed reads refuse an outer `.cmd`/`.bat`
Worktrees shim, whether selected directly or as `prefix[0]`: serialized helper
arguments must not cross implicit shell parsing before native validation.
Unselected ordinary launches remain unchanged. Fleet's public credential command reuses the existing
typed exchange; there is no Fleet import or new issuer in the native process.
Never accept argv, keys, credential references or provider endpoint selection
from a remote watch registration. None is added to the Gateway wire contract,
subscription/replay state or emitted snapshot. This source-level adapter does
not configure or activate an observation daemon, production provider or fleet
placement. The owner-invoked [finite replay observation adapter](watch-replay.md#finite-native-observation-adapter)
consumes this bridge with trusted account/repository bindings, releases disk
custody across reads and applies only pre-read registration identities.

Synthetic process/TLS tests exercise the PR bridge, native Worktrees driver,
real Fleet credential CLI and recipient-sealed exchange together, including
per-command renewal, durable replay observation and refusal before another forge
command after provider loss. Separate native CLI tests exercise snapshot configuration and scope
mismatch refusal.

These synthetic checks are not installed-service, production forge-credential, Dispatch,
container placement, first-provision installer, live-edge or fleet acceptance.
Those remain separate activation requirements; no such production capability
is advertised by this adapter.
