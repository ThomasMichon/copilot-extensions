# Optional service replay consumer

`agent_dispatch.delegated_replay_consumer.create_replay_consumer` composes the
existing Dispatch waiter transactions with the PR-owned
[`ReplayClient`](../../agent-pull-requests/docs/watch-gateway.md). Install the
explicit `agent-dispatch[replay]` extra in an embedding that owns both components.
Default Dispatch imports and installs do not require PR or Fleet packages.
The ordinary `run --pr-watch-*` CLI still selects the existing local callback
optimization; it does not silently select this remote backend.

This is a **finite callable**, not an installed service, coordinator scheduler,
credential provisioner or automatic fleet activation. The embedding owns its
single shared scheduler, admission, authenticated caller, lifecycle, backoff and
shutdown. Do not create a process, thread or long-held Gateway request per task.

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
credentials to another principal under the same reference. Gateway capabilities
do not currently attest a namespace. Foreign deliveries are never ACKed.

The durable binding also records the actual caller Gateway origin, route prefix,
target and repository. A new binding cannot pick up an older binding's rows.
Changing the existing caller's Gateway origin refuses a cycle; changing a
configuration label is not permission to retarget an original request.
Credentials, proof keys and credential callbacks never enter waiter metadata.

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
`test_delegated_replay_transport.py` exercises the actual TLS/PoP caller,
Gateway/connector and disk owners, including lost responses and revocation,
channel loss and cancellation during commit. These are isolated source tests,
not installed service, production credential or representative fleet latency/CPU
qualification. No lifecycle/first-install change is made by this callable slice.
