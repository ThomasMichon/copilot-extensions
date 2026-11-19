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

For the PR service's optional
[terminal admission profile](../../agent-pull-requests/docs/watch-gateway.md#optional-terminal-admission-profile),
the current client decodes policy from the capabilities media profile while
retaining strict legacy JSON. `ReplayConsumer.prepare` validates the requested
criteria and original finite deadline before `prepare_run_waiter`: unsupported
criteria leave the task unsuspended and emit no registration. Discovery applies
to watch-only bindings too; no workspace route or extra policy request is
required. The selected client must expose `registration_admission`; an older
client without that attribute refuses new preparation before suspension.
Only a capable client reporting `None` selects baseline policy. An absent
provider media profile through that capable client remains supported;
unsupported metadata refuses capability negotiation. The normal cycle still reconciles existing
receipts, cancellation and wake-before-ACK; it does not apply a new-admission
check to settlement. Actual TLS/PoP controls cover both v1 and scoped-v2 callers.
Provider/consumer rollout and operational enrollment remain separate; this
source change activates no coordinator, consumer or polling loop.

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
`test_delegated_replay_credentials.py` joins that scoped service/queue path to
the actual native GitHub/ADO credential-command readers and TLS credential exchange.
It checks renewed per-request credentials rather than ambient fallback,
unchanged shared observations, drained restart with provider refusal, retained
original identities and one wake per wait after authorization recovers.
GitHub forge CLI responses, ADO HTTP responses and the issuer/provider are
synthetic. ADO still constructs its real native request and authorization header
after each typed acquisition; it uses configured observation, never legacy
`az` snapshot fallback. The ADO control qualifies bearer credentials, not
basic authentication, broader provider conformance or actual forge TLS transport.
This does not qualify production provider permissions, an independent evaluator
or deployment.
These are isolated source tests,
not installed service, production credential or representative fleet latency/CPU
qualification. The scheduler adds no process, network call or tree/history
scan of its own: it reuses the finite cycle's bound above, plus the configured
active-owner checkpoint per later effect/exchange. That callback must remain
bounded; it cannot be replaced with cached authority after known loss.
No installer, first-provision or PR-role activation change is made here.

## Repository-feed reader expansion

The native waiter library also reads the distinct
`agent-dispatch.repository-feed-waiter/v1` storage variant specified by
[repository-feed waits](../../../efforts/active/durable-pr-watch-delegation/repository-feed-waiters.md).
This is storage support, not a feed consumer or advertised delivery capability.
Default startup and the `delegated_run_waiters/v1` HTTP/callback validators do
not select it. Those validators reject the new form before suspension;
do not send it to an older or callback-only coordinator.

The new delegation retains the ordinary coordinator, preparation, subscriber,
repository, PR-number and finite-timeout fields. Its explicit `protocol` and
`feed` replace watch-only `until`, `shared`, `installation` and `replay` fields;
mixed forms are invalid. `feed` contains the original `request_id`, `issued_at`,
`deadline_at`, trusted `binding` and selected activity `baseline`.
The binding names the cache protocol, absolute normalized Linux socket path,
full namespace/provider/forge-endpoint/account/repository identity, target,
consumer and reviewer. It stores no credential or callback command.

`baseline: null` means unknown. A known baseline has `event_cursor` and
`action_key`; a null action key means that the qualified original view had no
selected action. A nonnull key is the consumer contract's stable action
correlation digest, not a task revision, ranking cursor, proof of publication
or authority. Merely storing it does not qualify acquisition or selection.
Reusing a preparation with changed intent refuses, and the same original
request cannot be rebound to another task.

Both feed and watch-replay variants use the existing waiter service-scope,
request, attempted, registration and settlement columns. Their complete
serialized bindings partition lookups; no table or request journal is added.
Feed request/registration reservations use namespace and consumer, not reviewer
or target. This mirrors the service's consumer/request idempotency boundary and
conservatively retains the reservation across configured socket aliases in
that namespace. Changing reviewer, account, target or socket cannot bind an
original request to another task. Distinct namespace/consumer pairs retain
independent request spaces. Indexed scope selection plus exact binding
comparison keeps one reviewer's rows out of another binding's reads.
`scan_repository_feed_waiters` reads at most sixteen indexed candidates before
comparing their complete bindings. Its `FeedWaiterPage` reports matching
`waiters`, `scanned` count and `next_after` from the last scanned row, even when
all candidates belong to another binding. Callers must retain that continuation;
an exhausted candidate window returns zero `next_after` for an explicit wrap.
The older list-only replay method refuses feed bindings rather than hide this
progress. Exact registration lookup uses the existing unique scope/registration
index and compares the decoded binding after fetching its single candidate.
Feed rows remain outside the existing indexed local-only recovery queries,
including while their consumer is absent. They cannot become missing local
processes or preparing-arm timeouts. Changed selection leaves old rows
unsettled for their original owner.

The current native reader preserves callback, v1/v2 replay and feed rows
together across reopening. Invalid/unknown feed intent, including an explicit
feed protocol with its required payload missing, refuses without
rewriting that row. This does not promise that arbitrary historical runtimes
can execute the new variant: the current callback protocol still refuses it,
and reader/consumer transition must retain reconciliation custody before a
selection changes. No state adoption, callback fallback or automatic rollback
is performed.

`test_repository_feed_waiter_storage.py` exercises actual SQLite storage,
original-request conflicts, detached baselines, full binding partition,
unarmed/armed local-recovery exclusion and callback HTTP refusal without task
or outbox changes.

### Explicit finite-task preparation

`agent_dispatch.repository_feed_preparation.create_repository_feed_preparer`
adds an opt-in in-process preparation adapter. Supply the separately installed
PR-service SDK, trusted complete `binding`, canonical harness `lane`, explicit
HTTPS `repository_target`, reviewer recipe `land` policy and the embedding's
active-route/drain `checkpoint`. The target maps that service repository to
the native reviewer recipe; obtain the mapping from configuration, not a feed
item. Non-default ports and IPv6 literals remain unsupported because the
existing recipe target canonicalizer does not distinguish them.

```python
preparer = create_repository_feed_preparer(
    queue, binding=configured_binding, lane="review-harness",
    repository_target="https://forge.example/owner/repository",
    land="author", checkpoint=owner_checkpoint,
)
waiter = await preparer.prepare(
    task_id, delegation=original_intent, worker_id=worker_id,
    resume_worktree=resume_worktree, host=host, reason="waiting for new activity",
)
```

Default imports do not import the optional SDK. Construction starts no service,
and preparation makes no subscription, ACK, task creation or wake. Its native
owner must expose `repository_feed_preparation_protocol` equal to
`agent-dispatch.repository-feed-preparation/v1`; an older storage-only owner
refuses before service IO. This is an in-process contract, not new support in
the callback HTTP protocol.

Preparation freezes the original intent and observes the retained task basis,
then uses the bound asynchronous private-Unix client to negotiate the running
repository/consumer and read one reviewer-action page with `limit=1`. These are
cache-only reads, not action selection or freshness/coverage qualification.
The configured original baseline remains unchanged, including an unknown one.
The same native reviewer recipe supplies the stable dedup key; there is no
event-specific task key or absent-association creation path.

After service IO and the current owner checkpoint, the existing
`prepare_run_waiter` transaction rechecks the exact association, finite recipe,
owner/session, task generation and steering revision. Holds, pending operator
forms, execution-admission closure and assignment-relationship protection remain
effective under that write lock. The original deadline is rechecked after lock
acquisition, not renewed after negotiation. Only then can that transaction
suspend and retain the original waiter. Repeating an unchanged preparation
reconciles its original generation; changed or retired custody is not replaced
by a fresh request. A suspended task permits reconciliation of that same live,
owner-matching waiter only, not a new preparation. In particular, an event-note
wake that supersedes the waiter during negotiation must retain its generation
and pending wake; preparation cannot invalidate it by creating another waiter.
A started task also cannot suspend while its current owner wake is pending,
delivering or has unresolved send custody. These outbox checks occur under the
same lock; a new event note need not change the steering revision to remain
protected. A confirmed delivered wake does not permanently block later waits.

There is at most one preparation in flight per adapter and two finite service
operations per call. The original monotonic deadline bounds the SDK operations;
network IO does not hold a queue transaction. The native queue's existing short
synchronous reads/write transaction are retained; no executor, per-task thread,
timer, poller, credential loader or default factory is introduced. Cancellation
before the synchronous commit leaves the task unchanged.

`test_repository_feed_preparation.py` exercises actual SDK/private-Unix/cache/
SQLite composition, unsupported native/service selection, task/form/hold/
generation races, execution and relationship guards, original deadlines,
cancellation and lost-local-result reconciliation. These source integration
controls do not qualify installed reader transition or consumer activation.
Subscription recovery, useful activity selection, native feed completion/
expiry, wake-before-ACK and shared coordinator cadence/drain enrollment remain
subsequent implementation and validation exits.

### Retained admission and subscription attempts

Native preparation retains its original `FeedWaiterAdmission` in nullable
`run_waiters.feed_admission_json`, in the same suspension transaction. This
versioned record holds the original task/lane/recipe-dedup/owner/session/
generation and steering revision. It also retains the revision expected after
the owner's intentional suspension. Suspension itself changes that revision;
later preparation retries reuse the original record rather than replacing it
with a snapshot of a subsequently changed task. It contains no copied task,
credential, service receipt or separate request journal.

The in-process `repository_feed_attempt_protocol` is
`agent-dispatch.repository-feed-attempt/v1`. A consumer must require that
executing support before attempting this variant. Calling the existing
`replay_waiter_progress(waiter, attempt=True)` on a feed row atomically checks
the retained basis, current owner/session/generation/steering, finite recipe
and live dedup association, holds/forms, execution and relationship admission,
native resume requests (including headless steering or pool lane recovery),
current ordinary wakes, waiter wakes/successors and original deadline. Only
then does it record possible transmission. Repeating an attempted registration
refuses: recover the original request through provider inspection, never
replay subscription creation because a response was lost.

Service IO remains outside SQLite. Recording a recovered registration receipt
does not require an unchanged task: an uncertain remote effect still belongs
to its original native row. Arming that receipt rechecks the same custody,
wake and deadline guards and requires its retained attempt/registration.
It rechecks admission immediately before the existing transaction commits;
crossing the original deadline during the tentative transition rolls it back.
Nonfinite clocks cannot admit a feed attempt or arm. The task is re-read under
that same write lock rather than trusting its earlier checked snapshot.
A changed task can therefore retain a cancellation/reconciliation obligation
without becoming active or waking. These library guards do not implement
subscription transmission, cancellation, expiry completion or ACK.

The optional registrar additionally requires the native in-process
`repository_feed_receipt_protocol` equal to
`agent-dispatch.repository-feed-receipt/v1` before preparation or service IO.
The existing waiter row's nullable `replay_registration_expires_at` retains
the actual validated subscription expiry, not a copy of the native deadline.
Recording it uses the same progress transaction as the original registration
ID and preserves late results after task admission changes. The value is
immutable for that receipt; conflicts refuse without overwriting prior custody.
No separate receipt table or request journal is added.

Feed arming requires known receipt expiry and checks it both under the original
write lock and before commit. The provider's shorter expiry, including its
integer cutoff, cannot be extended by a fractional native deadline.
Historical rows retain null expiry across schema adoption and remain readable,
but cannot arm until their original receipt is recovered through the selected
owner. A registrar paired with a native owner lacking the receipt capability
refuses before suspension; no package-version guess or registration replay
supplies missing support.
Callback and watch-replay rows keep their existing null metadata and behavior.
This does not arm the registrar or qualify historical runtime transition,
remote settlement, wake delivery, ACK or consumer activation.

Existing callback and watch-replay rows retain null admission and unchanged
behavior. Historical feed rows without an admission record remain readable
and outside PID recovery, but cannot attempt or arm through the new guarded
path. No migration invents their original steering basis. Malformed or unknown
non-null admission refuses on read without rewriting it. The added nullable
column leaves prior projected fields unchanged; this is not a promise that
arbitrary older readers enforce it, operational adoption or a qualified
rollback. The lifecycle owner must preserve reconciliation custody.
`test_repository_feed_attempts.py` exercises reopen, strict retained records,
native task/wake/closure races, original deadlines and late receipt retention.

### Explicit original-request registration recovery

`agent_dispatch.repository_feed_registration.RepositoryFeedRegistrar` extends
the same preparation adapter for an explicitly selected embedding and injected
bound asynchronous PR-service client. Its in-process protocol is
`agent-dispatch.repository-feed-registration/v1`. It is not selected by the
coordinator factory, callback route, CLI or default startup.

Its `prepare` requires the executing native attempt protocol above
and the service's `repository-request-recovery/v1` capability before suspension.
The ordinary preparation-only adapter remains compatible and inert. Calling
`await registrar.reconcile_registration(task_id, preparation_id)` then makes
one registration or original-request inspection exchange. A missing original
baseline refuses creation; no new baseline or request identity is allocated.
The registrar rejects missing baselines, timeouts above the provider's 86,400
second TTL limit, out-of-range provider cursors/cutoffs and an already elapsed
creation cutoff before preparing or suspending a native task. The inert
preparation-only adapter retains its broader storage contract.
The selected queue must additionally declare
`repository_feed_registration_protocol` equal to the registrar's protocol.
Only that capable owner receives the optional `creation_cutoff` on its existing
preparation operation. It verifies the immutable rounded cutoff under the same
suspension write lock, both after acquisition and immediately before commit.
Crossing the cutoff rolls back task suspension and waiter creation, without
rewriting the original deadline. Older preparation-only callers send no new
argument and retain their original lifetime and commit behavior.

The original request freezes `after` from the retained baseline cursor, TTL
from the original timeout rounded up to seconds, and `not_after` from the
original deadline rounded down to seconds. The provider therefore cannot
accept or retain a registration beyond that finite deadline. The native
attempt transaction commits before possible transmission. Every later call,
including after a lost reply or process restart, uses `inspect-request` for
the original identity rather than another `subscribe`.

The returned receipt must match the configured consumer, full repository
identity, original request and immutable intent, with expiry no later than its
cutoff. Its registration is retained through the existing progress owner, even
after a task changes. This leaf does not arm a native waiter: a provider's
`status: active` is a service receipt state, not native activation or permission
to wake. Unknown responses retain the attempted identity; no exception is
treated as proof of nonacceptance.

Each explicit call has a thirty-second monotonic IO bound, shortened by an
already selected client deadline. Inspection can reconcile after the original
acceptance deadline; it never renews it. The bound client rechecks executing
recovery support at the effect/inspection boundary as well as the adapter's
preflight. This is at most three HTTP exchanges per call, outside SQLite,
not another scheduled cycle or acquisition loop.

An absent receipt, including absence after cutoff, remains `unresolved` here.
Cancelled/expired receipts are retained but cannot arm or complete the native
waiter. Registration reconciliation does not cancel subscriptions; the explicit
retired-registration operation below is separate. Neither operation settles
native arming/expiry, selects useful activity, wakes a task, ACKs delivery or
supplies a full consumer. Those operations still require the existing owner's
separately qualified completion/settlement path; do not activate this adapter as
a standalone waiter loop.
Before a later consumer can arm a recovered receipt, its actual expiry must be
checked by the native arm transaction under its write lock and immediately
before commit, as well as the existing task/wake/original-deadline guards.
A pre-IO or pre-lock receipt check is not that qualification.

`test_repository_feed_registration.py` composes actual native SQLite waiters
with real repository receipt storage and a real private-Unix SDK/ingress case.
It covers reopen, lost committed replies, absent requests without creation
replay, unsupported selection before suspension, changed-task late receipts,
foreign intent, original cutoff, unknown baseline, cancellation and expiry.
No case starts a production consumer or asserts a task wake/ACK occurred.

### Explicit retired-registration cancellation

The executing registrar optionally exposes `cancellation_protocol` equal to
`agent-dispatch.repository-feed-cancellation/v1` and
`await registrar.cancel_registration(task_id, preparation_id)`.
This is an opt-in in-process operation, not a callback/CLI route, new factory
selection or requirement on existing registration-only callers.
Missing support leaves cancellation unavailable; package versions are not
capability evidence.

Cancellation requires an already retired original native waiter, its complete
configured binding and a known original registration ID. A live waiter refuses
before service IO: this operation does not retire or expire it.
An uncertain registration with no retained ID must first use original-request
inspection through `reconcile_registration`; no replacement is created.
Known historical registration IDs remain cancellable without inventing missing
admission or expiry metadata. An already settled row needs no service request.

The operation negotiates the same executing recovery/receipt support, rechecks
active-route/drain authority and reloads the same retired registration before
transmitting the existing scoped `cancel` request. It uses at most three HTTP
exchanges with the bound client, outside native transactions, within thirty
monotonic seconds or the shorter caller deadline. Invalid or elapsed caller
deadlines refuse before service IO. Registration reconciliation retains that
same finite deadline rule.

Only a matching cancellation response reports `cancelled`; uncertain replies,
coroutine cancellation and invalid responses retain the same native registration
and unsettled custody. Existing original-request inspection can observe the
result after reopening. Repeating explicit cancellation targets only that
original idempotent registration; neither the request ID nor the waiter is
replaced.

A cancellation response is not a native settlement or event ACK. The operation
does not set `replay_settled`, change task/wake state, consume delivery, or forget
an already-delivered unacknowledged event. The provider retains that delivery
through its existing storage owner. Wake-before-ACK, remote settlement and
coordinator lifecycle integration remain separate acceptance.

`test_repository_feed_cancellation.py` composes actual native expiry/SQLite and
repository receipt storage. It covers original versus foreign scope, missing
receipts, lost committed replies, repeated cancellation, pending delivery,
old nullable metadata, drain and deadline refusal, and concurrent operation
ownership without selecting a consumer.

### Original native deadline recovery

The executing queue exposes `repository_feed_expiry_protocol` equal to
`agent-dispatch.repository-feed-expiry/v1` for the optional in-process
`expire_repository_feed_waiter(waiter)` operation. It is not a timer, callback
event, factory selection or service request. The embedding must retain its
current active-owner/drain checkpoint and existing bounded cadence.

At the original retained deadline, this owner can retire a preparing or active
feed waiter and enqueue its timeout notification through the existing native
wake table in one SQLite transaction. It rechecks original task/admission,
recipe/dedup association, owner/session/generation/steering, holds/forms,
execution/relationship protection, native resume requests and existing
wakes/successors. Invalid clocks or a deadline that has not elapsed refuse.
A different native transition's wake is not replaced.

Concurrent calls, reopen and a lost committed result return the original
timeout wake instead of adding another. Interrupted pre-commit work rolls back
both retirement and outbox insertion. The native task stays suspended under
its existing owner; ordinary wake delivery and its independent admission remain
with their existing owner. This operation does not deliver a wake itself.

`event_id` stays null: repository subscription expiry is not a watch/v2 timeout
event. Original attempted/registration metadata remains intact and
`replay_settled` remains false. Remote cancellation, original-request inspection
and delivery settlement remain separate; this timeout cannot authorize a
service ACK or forget a possibly accepted registration.

`test_repository_feed_expiry.py` covers actual SQLite preparation/active states,
original-deadline refusal, hold/form/owner/instruction races, an operator wake,
pre-commit rollback, lost-result reopening and concurrent one-wake publication.
No default recovery loop, callback protocol or consumer is activated.
