# Internal PR watch reconciliation and replay core

`agent_pull_requests.watch_replay.WatchReplay` is a transaction library for the
PR service's pull-delivery adapter. It reuses the existing `WatchRegistry`;
there is no second poller, thread-per-wait, callback executor, forge client or
fleet controller. It closes the source-domain portion of the PR vision's
`durable-transport-appropriate-delivery` and
`one-operator-with-separated-scopes` items, not their serving/deployment proof.

## Owner and authority boundary

An embedding adapter selects one fleet/service/installation namespace and holds
its existing single-writer lifecycle authority. It supplies the complete
persisted envelope and an atomic durable `commit(envelope)` operation. The
library does not acquire a lease or establish a service identity. Never open
multiple independent instances against the same writable store, merge these
envelopes into the native daemon's list-format `watch-subscriptions.json`, or
advertise native callbacks and pull delivery as independently active writers
for the same namespace.

`state=None` explicitly provisions an empty store. It is **not** a fallback for
an unreadable, missing or malformed previously provisioned store. Restored state
must match `agent-pull-requests.watch-replay/v1`, the selected namespace, its
record/registry bindings, callback-free pull mode and supported bounds.

`ConsumerScope(consumer, account, repo)` is trusted internal adapter input,
**not authentication**. The serving adapter must derive and authorize it from
verified caller context and configuration, not body-supplied identity labels.
Likewise, `observe(account, repo, number, snapshot)` consumes an already
authorized provider result. Different account/repository groups never share a
registry; consumers within the same group share a single applied observation
but retain independent registration/event identities. Renewal, provider-result
validation and effect-side admission remain the serving adapter's obligation.
Pull baselines persist the head and all supported observation fingerprints;
staging or restart cannot silently adopt a new votes/approval/thread/policy/gate
or readiness baseline in place of the previous observation.

The disk adapter optionally accepts
`ReplayServiceIdentity(fleet, gateway, slot)`. A bound `ReplayStore` wraps the
unchanged core envelope in `agent-pull-requests.watch-service-store/v1` and
checks the exact service identity and inner namespace on every transaction
under the existing operation lock. Commits atomically retain both together.
Bound and unbound readers refuse one another's formats rather than silently
adopting, migrating or downgrading them. The ordinary unbound core/disk API
retains its existing bytes and behavior; the standalone host always selects
the bound format. These trusted local labels prevent accidental wrong-volume
adoption, not filesystem tampering, cross-host concurrent writers or unauthorized
Gateway access. They are not credentials, a release ledger or activation grants.

The core accepts no callback argv, delivery URL, shell expression or credential.
This library neither advertises health capabilities nor extends the existing
watch daemon's register/unregister wire contract. The optional
[Gateway adapter](watch-gateway.md) supplies configured scope mapping, bounded
operations and disk transaction custody around this core. Service activation,
production provider authorization, coordinator consumption and
installation/cutover integration remain separate work.

## Finite native observation adapter

`watch_observer.ReplayObserver` connects the disk-backed `ReplayStore` to the
existing native snapshot bridge. It is an owner-invoked **single cycle**, not
another poller, service, credential issuer or activation path. The embedding
lifecycle owns scheduling, process-level single-writer authority, renewal and
drain. Its local nonblocking cycle guard refuses overlapping invocations of
the same adapter; it does not establish distributed ownership.

Trusted deployment configuration supplies immutable
`NativeObservationBinding(account, repo, command, prefix, credential_command)`
values, at most one per exact account/repository pair. Commands are attributable
absolute executables/interpreters, not remote registration fields. An optional
credential command selects the existing per-request native credential path;
omitting it retains the native alternative. Account labels are not
authentication or proof that a credential is downscoped: the embedding owner
still supplies the authorized account/source/forge policy described in
[the credential adapter](watch-gateway.md#explicit-native-observation-credential-command).
There is no cross-account/repository credential or provider fallback.

Each cycle durably sweeps elapsed deadlines, loads coalesced waiting targets,
and releases transaction custody before native reads. It performs one read
per bound account/repository/PR, shared across that group's consumers. Already
pending subscriptions do not trigger more reads. Unbound groups remain
untouched except for deadline sweeps; the cycle reports their count rather than
selecting another binding. The existing replay record bound limits each pass.
Every native read retains the bridge's time/output bounds.
The one cycle admits at most four native reads at a time, with no per-subscriber
executor or unbounded submission queue. Completed results are applied by the
cycle's single writer, independent of submission order; each freed slot admits
the next still-eligible target. A slow target cannot hold back another completed
read or monopolize all four slots. If all slots are occupied, later targets
wait for bounded native work to settle; this is not an unlimited-concurrency
or production-latency guarantee.

Apply reloads the current durable envelope under the same operation lock.
Only the exact registration IDs captured before that read can receive the
snapshot. Concurrent cancellation is preserved; a registration arriving during
the read waits for the next cycle rather than adopting an older result.
Before each read, a short read-only transaction also refreshes the captured
registrations' eligibility. Targets already completed by a previous read's
timeout sweep or concurrent cancellation/ACK produce no native or credential
work. New registrations never substitute for a captured completed registration.
Pending event IDs, original deadlines and completion receipts are unchanged.
Provider unavailability does not cancel, reset or replace any subscription.
Deadline sweeps continue while reads are pending, with current-authority checks
at least every 100 ms between bounded replay transactions, and after each result.
An unchanged sweep does not persist the envelope. Comparison excludes only
the relative monotonic timeout projection; absolute deadlines, timeout events
and receipt-retention cleanup remain durable changes. Other replay mutations
retain their existing commit behavior and uncertainty contract.
An otherwise valid native snapshot may exceed the smaller replay event budget.
That target's application is refused without changing its state and counted as
unavailable; timeout sweeps and later targets still run, including on repeated
cycles. Other invalid-state, authority and commit failures still stop the pass.

The caller supplies the cycle's finite authority expiry and optional current,
bounded, thread-safe lifetime checkpoint. Checks occur before read admission
and native execution, between results, and at lookup, commit and
result boundaries, including after provider failure. Known loss stops later
reads and application; an already-started write can still finish uncertain,
using the same disconnect/reconcile-original-identity contract as the Gateway
adapter. Never wait for remote rollback or arm a second waiter.
On loss, invalid state or uncertain commit, queued reads are withdrawn and the
cycle joins every already-started read before releasing its non-overlap lock.
An embedding drain timeout therefore cannot mistake a cancelled Future for
settled native work or release the service's writer lease early.
These owner-only methods are not added to the Gateway operation allowlist.

## Reconciliation and retention

The caller durably retains an opaque `request_id` **and original `issued_at`**
before attempting registration. Together with the trusted consumer/account/repo
binding they identify one request. A new acceptance requires that timestamp to
be no more than 24 hours old and not in the future. Repeating the same request
returns its existing registration/outcome without resetting its deadline or
replacing pending delivery. Different criteria or deadline return `conflict`.
A new timestamp or request ID is a new request, not a reconciliation attempt.
Integer timestamps/deadlines must be exactly representable as finite floats,
matching registry persistence. Inputs that would round or overflow are rejected
before committing, not accepted into an envelope that cannot reload.

`inspect` returns only that scope/request's receipt. Unknown records return
`uncertain`, never evidence of nonacceptance or permission for process fallback.
The optional absolute `deadline_at` retains native unlimited-wait support when
omitted; an already elapsed deadline is retained and produces a timeout on the
next sweep. Dispatch's own finite-timeout requirement is unchanged.
Restoration and each staged transaction reconstruct monotonic deadlines from
the supplied owner's wall clock, not an independent host-clock sample.

Active records, including pending events, are not evicted by receipt retention.
ACK/cancel replaces the active record with a compact outcome retained for 24
hours from completion. Repeated exact completion returns that outcome, including
after a lost ACK response. Expired outcomes become `uncertain`; retrying their
original timestamp cannot create another wait. Pruning occurs transactionally on
the next mutation. There are at most 1,024 total active/outcome records per
namespace; capacity exhaustion explicitly refuses new acceptance instead of
evicting another consumer or inventing absence. The owner supplies a reliable
wall clock for absolute deadlines and the replay window.

## Pending delivery and completion

`pending(scope, limit=16)` returns only that consumer/account/repository's
immutable events. Reads do not ACK. A batch contains at most 16 events and 24
KiB of JSON, including its `more` field. `more` refers only to that scope;
there is no global count or continuation cursor. ACK a processed batch before
reading its successor. A single oversized event is refused before its
observation transaction commits, rather than accepted into an undeliverable
queue. This first core supports one-shot observations, not a new continuous
remote-stream protocol.

`acknowledge(..., registration_id, event_id)` ACKs only that exact pending event
under the original consumer/request. A missing/empty event identity is rejected,
never interpreted as cancellation. `cancel(..., registration_id)` explicitly
cancels that exact registration. Stale, foreign, unknown and incompatible completed identities
return `uncertain` without changing another wait. Cancellation does not claim
to undo an already committed consumer effect.

Every mutation uses a staged copy, serializes the complete registry/receipt
envelope and calls the owner's commit before publishing success in memory.
An ordinary write exception propagates and leaves live state unchanged.
`StateCommitUncertain` means replacement occurred but durability acknowledgement
failed: live state adopts the replacement and the operation reports
`uncertain/state_commit_uncertain`. Reconcile with the original request and
event identities; do not create a second waiter.

This is at-least-once delivery, not exactly-once effects. The coordinator must
durably commit its existing generation-fenced effect before ACK. A crash after
effect commit but before ACK replays the same event. A crash after ACK commit
does not replay it, and the compact receipt reconciles its lost response.

## Focused validation

Run through the repository's bounded plugin-test supervisor:

```text
python tools/run-plugin-tests.py agent-pull-requests --test-file tests/test_watch_replay.py --test-file tests/test_watch_registry.py --test-file tests/test_watch_delivery.py --test-file tests/test_watch_observer.py --test-file tests/test_watch_credential_transport.py
```

The replay suite covers scoped reconciliation, account-separated observation,
stable deadlines, foreign/stale identities, retention/capacity/batch bounds,
corrupt state, pre/post-replace failures and a real restarted consumer process
that commits an idempotent synthetic effect before or after ACK. It also drives
the actual native callback scheduler alongside a pending pull event to prove
that pull records are not executed or acknowledged by that adapter.

The observer tests exercise native subprocess reads and a real
restarted consumer's exact event/ACK, account isolation, concurrent registration
and cancellation, expiry/disconnect and pre/post-replace failure. Synthetic
TLS/PoP/controller/recipient-ACK tests feed native credential-backed results
through the real observation cycle into durable replay state, including provider
loss before another forge command.

These are source-domain, synthetic transport, persistence and process checks,
not production credential enrollment, installed-service, container placement,
first-provision or real fleet acceptance. Those remain required before
advertising a fleet service or enabling its dispatch consumer.
