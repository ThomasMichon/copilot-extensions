# Optional service replay consumer

`agent_dispatch.delegated_replay_consumer.create_replay_consumer` composes the
existing Dispatch waiter transactions with the PR-owned
[`ReplayClient`](../../agent-pull-requests/docs/watch-gateway.md). Install the
explicit `agent-dispatch[replay]` extra in an embedding that owns both components.
Default Dispatch imports and installs do not require PR or Fleet packages.
The ordinary `run --pr-watch-*` CLI still selects the existing local callback
optimization; it does not silently select this remote backend.

The consumer is a **finite callable**. An explicit coordinator embedding can
schedule it through the existing waiter-maintenance owner described below.
Neither API installs a service, provisions credentials or activates a fleet
backend. Do not create a process, thread or long-held Gateway request per task.

## Trusted composition

Construct a Fleet `ServiceCaller` with its explicit canonical HTTPS origin,
token callback, proof key and optional CA, then supply the selected PR route,
target and expected repository:

```python
consumer = create_replay_consumer(
    queue, caller, route_prefix="/api", target="project",
    repo="example/project", caller_scope="caller:dispatch",
)
```

`caller_scope` is a nonsecret stable reference to the embedding's authenticated
principal **and** server-configured replay namespace. It is correlation data,
not a token claim, namespace negotiation or authorization grant. The embedding
must prove that the caller actually maps to that scope, keep that mapping stable
across refresh/restart, and dedicate its pending view to this coordinator.
Do not share one bounded pending queue among unrelated coordinators or change
credentials to another principal under the same reference. Legacy v1
capabilities do not attest a namespace. Foreign deliveries are never ACKed.

The durable binding also records the actual caller Gateway origin, route prefix,
target and repository. A new binding cannot pick up an older binding's rows.
Changing the existing caller's Gateway origin refuses a cycle; changing a
configuration label is not permission to retarget an original request.
Credentials, proof keys and credential callbacks never enter waiter metadata.

### Explicit scoped v2 selection

Pass `service_binding=<expected binding document>` to the same factory to
select the PR client's [scoped v2 contract](../../agent-pull-requests/docs/watch-gateway.md#opt-in-scoped-v2).
The document has exactly `fleet`, `gateway`, `slot`, `namespace`, `principal`,
`target`, `consumer`, `account` and `repo`; target and repository must agree
with the explicit route arguments. Obtain these nonsecret expectations from
trusted deployment configuration, not the first capability response.

The factory copies and validates that document, selects the typed v2 client,
and adds `protocol: "agent-pull-requests.watch/v2"` plus `service_binding`
to the durable routing binding. This is a distinct strict variant of the
legacy five-field binding, not a rewrite of existing rows. Nested data is
detached from caller mutation. Both variants remain readable on restart;
indexed scope comparisons include the protocol and every expected service
field. Changing configuration cannot pick up or modify an older binding's
unresolved request. Preparing legacy intent with a scoped consumer, or scoped
intent with a legacy consumer, refuses before capability IO or suspension.

V2 capabilities must match the configured expectation before preparation;
every subsequent service request carries it and is checked against the actual
admitted scope. Caller labels remain correlation only, never credentials or
authority. Missing scoped client support or incompatible live capabilities
refuses this opt-in before a new wait is prepared. No v1 retry, process
fallback, automatic record conversion or replacement identity is attempted.
Rollback to a legacy service leaves scoped rows unresolved for the same
capable binding to reconcile later.

Omitting `service_binding` retains the existing v1 factory call and does not
require a scoped-only PR entry point or pass new arguments to an older v1 PR client.
Ordinary Dispatch imports still require neither optional distribution.
A successful cycle for one selected binding does not settle other bindings'
rows: an embedding changing selection must explicitly retain or transfer their
existing reconciliation obligation. Do not share a bounded pending view among
independent coordinators or treat a new configuration as permission to abandon
old waits. Default `server.build_app`/CLI still selects no remote consumer.

## Existing coordinator cadence and shutdown

An embedding can pass `replay_consumer_factory` to
`agent_dispatch.coordinator.create_app`. The factory receives that coordinator's
queue and an admission checkpoint; construct the consumer with both:

```python
def replay_factory(queue, checkpoint):
    return create_replay_consumer(
        queue, caller, route_prefix="/api", target="project",
        repo="example/project", caller_scope="caller:dispatch",
        checkpoint=checkpoint,
    )
```

Supply a positive `wake_interval` and the coordinator's authoritative
`wake_is_active` callback. Missing owner authority or disabled waiter maintenance
refuses this selection. The factory runs once per coordinator lifespan and must
return a consumer over that exact queue and checkpoint. It does not select a
different database or acquire credentials. The embedding still owes the actual
authenticated scope mapping and dedicated server-side pending view above.
Programmatic service composition can pass that same trusted factory to
`server.build_app(cfg, replay_consumer_factory=replay_factory)`. The front door
constructs its normal queue and retains its actual `_owns_active_route`
checkpoint; it does not accept an alternative ownership callback or turn
configuration strings into code, caller credentials or admission grants.
CLI/default startup still supplies no factory. No environment/config selector,
credential loader or runtime activation is introduced.

The existing startup/60-second waiter-maintenance cadence admits at most one
remote cycle. A cycle still in flight is not duplicated by another tick or
another waiter. Network IO runs in its own coroutine, so the native recovery
owner continues independently; there is no new timer, per-task process or
per-task thread. After a successful cycle the next eligible tick is at least
60 seconds later. An unavailable cycle backs off from 120 seconds to a
900-second ceiling, checked only at the existing cadence.

Every cycle is counted in the coordinator's existing `DrainGate`. Its
checkpoint rechecks active-owner authority and known drain/closure before later
lookups, emissions and local progress/effects. Preparation checks it again
after capability negotiation, before suspending a task. Passive or draining
coordinators do not emit a new cycle. Shutdown closes admission, cancels and
awaits the owned maintenance/replay coroutines, then continues ordinary
coordinator cleanup. This owns no additional resident process or update loop.

Cancellation is not remote rollback: an already-started service commit can
finish after the coordinator releases its local cycle. The durable attempted
identity remains in the same waiter row; a successor reconciles it through the
same selected scope, never a process fallback. This is local coordinator
drain/cancellation, not PR-volume writer transfer or fleet placement authority.
The embedding can inspect `app.state.waiter_maintenance.status` for fixed
state/cycle/failure fields and the last bounded result. Exceptions are not
included in diagnostics. This is not a new HTTP status or authorization API.

## Preparation and finite consumption

Use `await consumer.prepare(...)` with the ordinary task ID, worker, host,
resume worktree and reason. Supply the existing validated delegation identity
plus this strict `replay` object:

```python
replay = {
    "binding": consumer.binding,
    "request_id": original_request_id,
    "issued_at": original_issued_at,
    "deadline_at": original_issued_at + timeout,
}
```

The caller chooses the original request identity and issue time once. Positive
finite timeout and the existing five delegated transition names remain required.
Local callback `installation` metadata and `shared=True` cannot accompany replay.
Preparation checks capabilities **before** suspending the task, then records the
intent atomically in the existing `run_waiters` row. It performs no registration.
Retry that same preparation after a lost local result; do not generate a new ID.

Each `await consumer.cycle()` visits at most 32 indexed unsettled rows and reads
one pending batch of at most 16 events. It returns checked/event/settled counts
and a `blocked` flag. The cursor advances over visited rows, including a failed
row. An exhausted scan yields a pending-only turn and wraps on the following
cycle, so a persistently unresolved final row cannot starve pending delivery.
The owner chooses when another finite cycle is admitted; no internal retry runs.

Before registration, a short queue transaction records that the original
request may be emitted. Network I/O never holds that transaction open.
Registration retries retain the original request, criteria and deadline.
Receipts bind the registration to the same row; arming uses the existing task,
coordinator and waiter-generation fence. Pending events must match that binding,
registration and PR target. The typed client preserves/validates the native
payload; Dispatch projects only its existing completion fields and local
subscriber identity.

Completion commits the existing wake outbox and exact event identity **before**
ACK. Retired rows remain unsettled until a matching ACK receipt is confirmed.
If the ACK committed remotely but its response was lost, restart reconciles the
original request receipt without requiring an event still present in `pending`.
Cancellation/supersession cancels only the original remote registration and
cannot complete its replacement generation. A terminal row never emitted by
any consumer can settle atomically without creating a remote registration.

## Disconnect, revocation and recovery

A failed exchange or unresolved receipt stops that cycle; later rows are not
emitted in it. Async cancellation propagates. The embedding closes admission on
known credential/policy loss. In-flight service work may still commit, so renew
authorization and reconcile the original identity; do not roll back, create a
replacement subscriber or launch process fallback. Unknown/out-of-retention
receipts remain visibly unsettled, never proof that a second wait is safe.

The ordinary local preparing-arm timeout, local owner probes and PID/deadline
recovery exclude service-replay rows through the indexed local-only query;
they neither project the remote roster nor probe it. Their progress belongs to this consumer, not a
missing callback or process. An embedding that stops scheduling must retain that
visible obligation or transfer it explicitly. The service remains responsible
for the original timeout event; coordinator restart does not extend its deadline.

## Performance and source evidence

A cycle adds no subprocess, per-task thread, installation, tree/history scan or
credential inventory. It uses indexed bounded queue lookups and at most 82
finite SDK operations: one capability check, up to two receipt operations per
visited row, one pending read and at most one settlement operation per event.
Each SDK operation retains the existing bounded response/request contract;
challenge, credential acquisition and HTTP costs remain owned by the SDK and
embedding. The cadence and active roster determine aggregate cost.

`test_delegated_replay_consumer.py` asserts original-ID recovery, effect-before-ACK,
generation fences, bounded scans and failure fairness.
`test_delegated_replay_binding.py` covers strict scoped/legacy variants,
coexisting persisted rows, changed selection without adoption, detached nested
data and the older v1 factory signature without scoped imports.
`test_delegated_replay_transport.py` exercises the actual TLS/PoP caller,
Gateway/connector and disk owners, including lost responses and revocation,
channel loss and cancellation during commit. `test_waiter_maintenance.py`
asserts one cycle for a forty-waiter roster, backoff, native-recovery progress
under slow remote IO, active/drain fencing and coordinator-owned shutdown.
The transport suite also drives actual coordinator lifespans through the real
TLS path, including shutdown during a held service commit and successor recovery.
Its programmatic `server.build_app` control selects the scoped factory over the
actual front door's queue, refuses work under an inactive route, resumes the
finite cycle under current fixture-owned authority, and verifies owner loss
and lifespan drain without exposing a new authority override.
It also composes the real `WatchService` observation loop with two Dispatch
waits for one authorized PR target. Repeated identical native-child
observations produce no wake, service/consumer restart preserves the original
request/deadline/registration identities, and a later matching state change
settles one wake per wait even when an actual committed ACK response is lost.
Both v1 and explicitly scoped v2 run that watcher path and the lost committed
register/ACK/cancel recovery controls.
The native child supplies synthetic provider snapshots; this is not live forge
credential validation or acceptance of an independent periodic evaluator.
These are isolated source tests,
not installed service, production credential or representative fleet latency/CPU
qualification. The scheduler adds no process, network call or tree/history
scan of its own: it reuses the finite cycle's bound above, plus the configured
active-owner checkpoint per later effect/exchange. That callback must remain
bounded; it cannot be replaced with cached authority after known loss.
No installer, first-provision or PR-role activation change is made here.
