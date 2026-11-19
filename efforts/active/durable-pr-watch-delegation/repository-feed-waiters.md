# Repository-feed waits through the existing Dispatch owner

Review-first refinement of [reviewer-action feeds](reviewer-action-feeds.md)
under [the canonical effort](README.md), coordinated by #12947.
The owner contract is reviewed; its first native reader/storage expansion is
described below. Full delivery support and activation are not implemented.
It is a later consumer slice, not another prerequisite for the independently
useful cache/event milestone.

## Failure and selected owner

A feed proposal and a native prepared-steering envelope do not survive a
consumer restart merely because they were printed. Losing the original
request can lead to a replacement notification, duplicate wake or premature
event ACK. Fix that custody gap through the task owner, not another PR-service
request database.

The existing [Dispatch replay consumer](../../../plugins/agent-dispatch/docs/delegated-service-replay.md)
already composes durable `run_waiters` rows, original request/deadline and
registration identity, generation-fenced completion, the task wake outbox and
effect-before-ACK recovery. Its optional coordinator factory also owns bounded
cadence, active-route admission, backoff and drain. Reuse those owners.

Its current watch/v1-v2 binding, typed events and Gateway client are **not**
the repository-cache/reviewer-feed contract. Do not relabel an action page as a
watch transition, pass a Unix path as a Gateway origin, or reuse a callback's
exit status as proof that a review was published. The existing prepared-steering
example remains useful for an embedding that already owns its durable request;
this refinement does not silently supply that owner to arbitrary callers.

## Bounded first realization

Add a distinct, explicitly selected repository-feed delegation variant through
the existing waiter owner. The first source slice serves an already associated
finite task under its current owner and generation. New-task creation,
conclusively absent association and general producer enrollment remain with
their existing owners and later acceptance.

The initial transport is the existing Linux private-Unix local client, using
its actual peer-UID checks and running capabilities. No TCP listener, proxy,
mount, credential loader, Fleet deployment or Windows transport is added.
Unsupported platforms retain an explicit unsupported result. Existing
Gateway watch/v1-v2 callers, native callbacks and default startup are unchanged.

Keep the native queue's original task, task-generation, waiter-generation and
ownership fields authoritative. Persist original feed binding and wait intent
in its existing waiter transaction, not a second file, queue or copied task.
The binding retains trusted configuration for the local endpoint, full
namespace/provider/forge-endpoint/account/repository identity, target, consumer
and selected reviewer. Obtain expected selection from the embedding, not the
first response or a PR body. Configuration changes cannot adopt another
binding's unresolved rows.

The wait retains its original request identity, issue time, finite deadline and
selected activity baseline. Preserve native recipe/dedup association rather
than creating a task key for each feed event. Original service registration,
expiry and delivery identities must survive uncertain register/ACK responses,
consumer restart and task suspension. Reopening or reconnecting does not renew
the deadline, forget an unknown effect or allocate a replacement request.

## Admission, useful changes and completion

Negotiate the executing service's repository/reviewer support and the native
owner's new delegation support **before** suspending or changing a task.
Declare a distinct versioned contract through existing capability surfaces.
Package versions, successful source tests and an installed newer payload do
not establish live support. Missing support refuses this optional variant;
do not retry through a callback, older watch protocol or process launcher.

The existing active-owner checkpoint and drain gate remain effective before
later service exchanges and local commits. Native lifecycle ownership, holds,
operator forms and changed task/generation state cannot be overridden by a
feed recommendation. No source binding grants review, rebase or merge authority.

Use the common [consumer selection semantics](../../../services/pr/CONSUMERS.md):
full scope, reviewer-owned action, candidate/head/base, original cause,
freshness, coverage and current task association. A genuinely new eligible
action may complete the matching wait through the existing atomic wake owner.
An unchanged projection, rank, poll time, reconnect, task revision or later
suspension does not itself justify another wake.

An action already covered by the original baseline is not new work. A worker's
confirmed publication and the cache's later observation remain different
facts; do not make a stale own echo request another verdict. Unknown baseline,
stale/partial pages, unsupported activity and unresolved prior outcomes remain
reconciliation, not an invented initial review or a fresh request identity.
Author-owned rebase recommendations never become reviewer instructions.

Commit eligible local completion, its exact source-event identity and the
native wake outbox **before** service ACK. Delivery, wake acceptance and actual
review publication remain distinct. A lost ACK reply reconciles through the
original registration/request and committed local completion; it does not
repeat the wake. Foreign or later waiter generations cannot be completed or
ACKed by an older delivery.

A NoOp must not fabricate completion or ACK unsettled work. If the selected
transport requires advancing delivery after irrelevant activity, qualify the
native owner's durable observation/cursor representation first. The first
slice may leave such delivery unresolved rather than add another cursor
store or guess that no task effect remains. Subscription expiry/cancellation
does not erase local unresolved custody.

Repository subscription expiry is not a watch/v2 timeout event. Reuse the
native original-deadline recovery owner for the configured timeout outcome,
through the same task/generation fence and wake transaction. Do not wait
forever for a timeout event this service does not emit, renew a registration
or label expiry as new review activity. A registration that may have committed
after local retirement still belongs to the original request's reconciliation
and scoped cancellation, not a replacement waiter.

## Lifecycle, storage and cost boundary

Reuse the existing optional replay factory and waiter-maintenance cadence;
do not add a resident, timer, per-task thread/process, forge poller or generic
request executor. Actual foreground/server composition and its active-route
authority remain explicit. Default startup selects no feed consumer.

The first profile processes at most sixteen waiter rows and sixteen deliveries
per finite cycle, within the existing eighty-two-operation ceiling including
capability, view, registration, reconciliation and ACK calls. Retain original
per-operation deadlines and aggregate resource/admission bounds. Partial work
is explicit; do not expand pages, retry budgets or deadlines to empty a queue.
Local feed reads must not initiate provider acquisition.

Network/service IO stays outside native queue transactions. Keep the existing
bounded scan fairness, failure backoff and shutdown join/cancellation behavior.
Unknown transmitted outcomes remain with the same native owner after drain.
Do not force-release accepted tasks to demonstrate a clean shutdown.

Preserve the native recovery partition: local PID probes and preparing-arm
fallback must not recover service-feed rows, including while their consumer is
disabled or an older runtime is selected. Unknown service custody is not a
missing callback process. Changing/removing a consumer must settle or explicitly
transfer every existing binding's reconciliation obligation through its owner;
a new protocol must not strand older waits.

Expand strict readers/validators before enabling the new retained variant.
Qualify coexisting old/new rows and supported rollback through real storage.
An incompatible reader must not rewrite or discard the new intent. If a
legacy runtime cannot safely use that state, report its affected unsupported
boundary; do not claim transparent rollback or pin data to one executable
forever. Actual reader transition/adoption remains the lifecycle owner's job,
not a submit-time migration or authority label.

## Native reader/storage expansion

The existing waiter library accepts a distinct retained feed intent through
its native storage validator. The
[reader contract](../../../plugins/agent-dispatch/docs/delegated-service-replay.md#repository-feed-reader-expansion)
documents its original request/baseline and complete local binding. It reuses
the current waiter row and indexed replay-progress columns; no request journal
or task copy is introduced.

Focused source controls exercise callback/scoped-watch/feed coexistence on
actual SQLite storage, reopen and original-request conflict, strict malformed
or mixed variants, detached known/unknown baselines and binding isolation.
Both preparing and active feed rows remain excluded from local recovery.
The current callback HTTP protocol rejects this storage-only variant before
task/outbox changes. That refusal is not delivery negotiation or proof that
every historical runtime can execute the new intent.

The first reader expansion does not add a producer, factory selector,
capability advertisement, transport, wake/ACK implementation or enrollment.
Compatible reader/lifecycle transition, typed client selection and actual
consumer recovery remain part of the serial acceptance below.

The [explicit expected-consumer SDK client](../../../services/pr/LOCAL.md#explicit-expected-consumer-client)
supplies the next local selection prerequisite. It checks the complete
configured repository/consumer against running support and carries the consumer
expectation to the existing operation's admission boundary. It does not prepare
a native task, add a Dispatch import/dependency or activate a feed consumer.
The native adapter, historical-reader transition and recovery exits stay open.

The [asynchronous bound request client](../../../services/pr/LOCAL.md#asynchronous-bound-requests)
adds finite native asyncio Unix exchanges for a future shared-loop embedding.
It preserves the same expected-selection and consumer assertion, the original
caller-owned payload and monotonic deadline, cancellation and uncertain-effect
custody without an executor thread. It neither prepares a native waiter nor
selects the coordinator factory. Native admission, historical-reader transition,
effect-before-ACK recovery and operational enrollment remain separate exits.

The [explicit native preparation adapter](../../../plugins/agent-dispatch/docs/delegated-service-replay.md#explicit-finite-task-preparation)
now composes that async client with the existing waiter transaction for a
retained finite reviewer task. It negotiates executing native/service support
before suspension, then atomically rechecks recipe/dedup association, current
owner/session/generation/steering, holds, forms, execution/relationship guards
and original deadline. It does not subscribe, select useful activity, wake,
ACK or enable a coordinator factory. Those recovery and adoption exits below
remain open.

## Provider-first original-request recovery

The preparation adapter does not yet transmit a subscription. The existing
repository API can reconcile `subscribe` by its original `request_id` while
the receipt remains retained, but `inspect` and `cancel` require the service's
`registration_id`. An expired, fully acknowledged receipt can be collected.
Blindly repeating `subscribe` after that collection can create a registration
again. Neither an HTTP timeout nor a missing local result proves rejection.

Expand that provider before adding native registration attempts:

- Advertise optional `request_recovery` support, with protocol
  `agent-pull-requests.repository-request-recovery/v1`, through the running
  repository capability response. Existing callers retain their current
  operations and semantics. Unsupported peers must not receive the new form.
- Add `inspect-request`, a cache-only operation taking the original
  `request_id` under the same admitted consumer and repository binding.
  Return the original registration/intent/status when retained, or explicit
  `not_found`, and the provider's current time. Inspection never registers,
  renews, advances delivery, trims receipts or calls a forge. A foreign
  consumer/target cannot recover another scope's registration.
- Add optional integer Unix-seconds `not_after` to `subscribe`, retaining it
  as part of the immutable original request. Under the existing writer
  transaction, a new registration is refused when provider time is at or
  beyond that cutoff; its expiry is the earlier of the cutoff and the
  existing TTL-based expiry. Reconciliation of a retained request still
  returns its original registration after the cutoff, without renewal.
- Preserve request, delivery, ACK and cancellation receipts through the
  existing bounded registration owner. Expand its strict reader first;
  legacy records retain their exact shape and behavior. Older strict readers
  that cannot interpret the added intent refuse before changing state.
  This source expansion is not operational reader adoption or transparent
  rollback qualification.

The later native consumer freezes the cutoff from its original finite
deadline, never from reconnect time. It negotiates support before suspension
and rechecks native ownership/forms/holds/wakes and the retained preparation
basis before each service effect. It does not replace a failed new invocation
with a legacy invocation. A lost subscribe response is recovered through
`inspect-request`, not another create attempt.

The bounded registration/inspection leaf retains service receipts without
arming a native waiter. An active provider receipt is not native activation.
Later arming must recheck the actual receipt expiry under the native arm
transaction's write lock and immediately before commit, preserving original
deadline, task, hold/form, generation and wake guards. The registration leaf
does not close that later arming/completion or wake-before-ACK obligation.

Cancellation of an unknown registration keeps the native reconciliation
obligation. If inspection finds it, use the existing scoped cancellation
operation and reconcile an uncertain cancellation through inspection. If
inspection is absent before the cutoff, creation may still arrive: absence
is not settlement. Retain the original request until a provider observation
at or beyond its cutoff excludes future acceptance; any accepted registration
has also expired by then. Already-delivered unsettled events remain protected
by the existing receipt retention. No cancellation tombstone, second journal,
replacement request or indefinite registration history is required.

The explicit retired-registration cancellation leaf uses the registrar's
optional `agent-dispatch.repository-feed-cancellation/v1` in-process support and the existing
scoped `cancel` operation. It requires a known original registration and an
already retired native waiter, rechecks the configured owner, and leaves
uncertain replies recoverable through original-request inspection.
It preserves delivered/unacknowledged events and native task/wake state;
`cancelled` does not mean `replay_settled` or ACK. Full remote settlement,
useful-activity completion and coordinator enrollment remain separate.
The [native operation contract](../../../plugins/agent-dispatch/docs/delegated-service-replay.md#explicit-retired-registration-cancellation)
defines its finite call and compatibility boundary.

The cutoff is a cooperative immutable-request guarantee, not permission to
reuse collected request IDs with later cutoffs or changed intent. Provider
clock rollback, unavailable inspection or an unverified scope remains
reconciliation, not proof of settlement.

Provider validation must cover lost committed subscribe responses, reopen,
cancel/expiry, a delayed invocation crossing its cutoff under the actual
transaction owner, exact cutoff refusal, TTL capping, scoped absence, changed
intent, receipt collection followed by a late replay, and delivered/unacked
retention. Exercise the existing local ingress with both legacy and new
request forms. No native consumer or default factory is enabled by that
provider expansion; wake-before-ACK and coordinator/drain proof remain later.

## Serial plan and validation

- [x] Review this owner/contract refinement before new protocol or storage code
  (#13293).
- [ ] Qualify the new strict delegation variant through actual waiter storage,
  serializer and restart, preserving legacy/scoped-watch rows and unsupported
  reader behavior and exclusion from local PID/preparing recovery. No parallel
  request journal, stranded binding or automatic adoption.
- [x] Add the typed local feed client/adapter and inert preparation through
  existing owners. Preserve exact configured identity and refuse unsupported
  service/native combinations before task suspension (#13325). Historical
  reader adoption and registration/delivery are not closed by this source leaf.
- [x] Expand and qualify the provider's original-request inspection and
  deadline-bound acceptance before native registration. Preserve old forms,
  strict storage refusal, bounded receipt retention and late/unknown custody
  (#13330). Installed reader adoption and native consumer delivery remain open.
- [ ] Exercise actual cache/client/native-queue composition: original request
  recovery, materially new activity, unchanged views, unknown baseline,
  stale/partial coverage, own echo, author actions, forms and generation races.
- [ ] Prove one wake before exact ACK, including lost committed register/ACK
  responses, consumer restart, cancellation and original expiry. Preserve
  unresolved custody without another wake, request, registration or store.
- [ ] Exercise actual coordinator cadence/active-owner/drain integration and
  bounded cost/fairness. Source fixtures do not qualify operational enrollment.
- [ ] Reconcile the parent plans. Release, installed/native-platform/provider
  qualification and any explicitly authorized consumer activation stay separate.

## See also

- [Reviewer-action feeds](reviewer-action-feeds.md)
- [Optional service replay consumer](../../../plugins/agent-dispatch/docs/delegated-service-replay.md)
- [Version-skew-safe contracts](../../../docs/patterns/service-contract-evolution.md)
- [Inert feed consumers](../../../services/pr/CONSUMERS.md)
