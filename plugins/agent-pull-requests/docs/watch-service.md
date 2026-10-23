# Owned PR serving and observation lifecycle

`agent_pull_requests.watch_service.WatchService` composes the existing
[Gateway adapter](watch-gateway.md), [finite native observer](watch-replay.md#finite-native-observation-adapter)
and Fleet's `ServiceConnector.run(stop)` in one foreground lifecycle. It
requires the explicit `agent-pull-requests[fleet]` extra. Ordinary native
CLI/callback installations remain unchanged.

This is the embedding API used by the separate
[standalone foreground distribution](../../../services/pr/README.md).
The core does not install or activate a fleet role, register a supervisor,
issue credentials, execute placement, migrate storage or select runtimes.

## Trusted construction and adoption

Construct `WatchService` with an already provisioned `ReplayStore`, fixed
fleet/Gateway/slot identity, immutable caller/scope and native-observation
bindings, Gateway origin/TLS trust, current async access-token callback and
externally provisioned proof key. Every serving account/repository must have
a native observation binding. Remote registrations never supply commands,
provider endpoints, credential references or an alternative scope.
If the store selects `ReplayServiceIdentity`, construction also requires its
fleet/Gateway/slot to match this service. The standalone distribution always
selects that disk binding; each adoption and transaction verifies the complete
bound envelope, and legacy unbound volumes require a separate migration.
The optional unbound embedding API remains compatible, but does not claim a
persisted fleet/service binding.

The embedding owner also supplies two synchronous, bounded, thread-safe
callbacks: `authority()` returns the current integer expiry, and
`checkpoint()` refuses when this instance no longer has current authority.
They must consult their existing owner, not manufacture a permit from caller
labels, a saved receipt or the local lock. They are not a new authorization
provider. Gateway still authenticates callers/channels and native provider
requests still renew and validate their selected credentials.

`await service.start()` takes the existing native `SingleInstance` lease
(`agent-pull-requests-watch` in the selected root), re-reads and validates the
complete replay namespace, then starts exactly one connector and one
non-overlapping observer task. It never provisions a missing store. Missing,
corrupt or foreign state refuses without replacement. A root containing native
`watch-subscriptions.json` refuses: never concurrently mount a live native
state root into a replay service.

The lease is host-local lifetime exclusion, not cross-host fencing or
permission to mount/move a volume. Initial placement, durable storage and any
replacement remain the existing placement/lifecycle owner's responsibility.
A second live instance refuses; no PID-based reclaim or process killing is
performed. A cancelled initial disk read retains its task and lease until
`close` on that same instance finishes draining it.

## Serving, observation and readiness

The Gateway adapter checks both the request lifetime and the embedding owner's
current checkpoint at lookup, commit and result boundaries. Withdrawal is a
disconnect, not rollback or a distributed barrier. An already-started write may
commit; retain and reconcile its original request/event identity under fresh
authority.

One owned observation task invokes the existing finite `ReplayObserver.cycle`
at the configured positive interval (default five seconds). Native reads remain
outside disk custody. Exact account/repository/PR groups share reads; subscribers
do not create additional loops. Provider unavailability preserves accepted
identities and deadlines. Local transaction contention reports not-ready and
retries at that same cadence, not a second scheduler. Corrupt state, uncertain
durability or lost owner authority stops the unit instead of creating state or
selecting alternate credentials.

`status()` reports only `ready`, `writer_held`, `draining` and `closed`.
Readiness requires current local authority, the held lease, live owned units,
an attached authenticated connector and a completed observation cycle without
unavailable or unbound targets. It is not evidence of provider enrollment,
production latency, installed activation, volume fencing or fleet acceptance.
The finite observer shares a four-read rolling window across authorized targets.
Its single writer applies completed reads without waiting for an earlier slow
target, while retaining every in-flight read through drain. Saturating all four
slots still waits for the existing native command bounds. Readiness continues
to require a completed cycle; a partial fast-target result is not whole-service
readiness. Representative production performance qualification remains open
in the parent effort.

## Drain, restart and replacement boundary

`await service.run(stop, drain_timeout=60)` owns startup, waits for the supplied
stop event or either unit's exit, then drains. The container/service manager
supplies that event; this API does not register another supervisor.
Stop requests and cancellation during initial adoption also enter that bounded
drain path without waiting for the shielded disk read first. A pending stop
prevents owned units from launching after adoption; a preset stop does not
acquire custody or start work. Startup ownership is reserved before scheduling
adoption, so concurrent entry cannot take over another invocation's cleanup.
A rejected second invocation never drains the already-starting/running owner.

`await service.close(timeout=...)` immediately withdraws admission/readiness,
stops later observation effects and requests connector shutdown. It joins the
connector, observer, actual shielded native/disk task and every already-admitted
Gateway operation before releasing the lease. Timeouts and cancellation never
release writer custody or advertise successful drain; retry `close` on the
same instance and event loop. A foreign process/event loop cannot release it.
Admission withdrawal precedes waiting behind another close. One monotonic
deadline includes that wait and the remaining drain work; foreground shutdown
also counts startup/waiter cleanup against its drain budget.
A queued caller's timeout/cancellation cannot release the active
drain's lock or writer custody.
Instances are single-use; construct a new one only after the old owner settled.

This is the service-side graceful boundary, not a complete cutover protocol.
The existing deployment owner must still authenticate the exact outgoing
process scope, verify writer exclusion and state compatibility, activate and
health-check the incoming generation, and own bounded cold takeover when
graceful drain cannot settle. No incoming readiness or manager acknowledgement
by itself authorizes killing an owner or moving its data. A process crash
releases the kernel lease; complete replay state preserves original events,
deadlines and ACK identities.

## Validation and limits

`test_watch_service.py` exercises actual disk, native locks and blocked worker
threads with a deterministic connector boundary. It covers contention,
cancelled adoption, competing owners, stop/cancel/failure, failed drain and
original-ID reconciliation after a committed write loses authority.
`test_watch_gateway_transport.py` additionally runs the real runtime over
Fleet's synthetic-issuer TLS/PoP/channel fixture and an actual native child.
It proves shared observation, channel reconnect, owner restart and exact ACK
without another native read. These are isolated source integration tests, not
production provider or standalone/container deployment evidence.

The standalone package supplies explicit host configuration, external credential
references, provision/serve/passive-readiness commands and an offline wheel-only
container recipe using Fleet's existing `container.service` role. Its explicit
foreground launch bounds local transaction work; Gateway attachment/caller
admission and typed forge-credential authorization remain independent.
It never turns a local operation deadline or passive readiness file into a
placement permit.

Authenticated image construction/activation, fresh-container qualification,
installed update/rollback and approved live provider/fleet acceptance remain open under
[the canonical effort](../../../efforts/active/durable-pr-watch-delegation/README.md).
