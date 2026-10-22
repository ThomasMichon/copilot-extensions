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

The core accepts no callback argv, delivery URL, shell expression or credential.
This library neither advertises health capabilities nor extends the existing
watch daemon's register/unregister wire contract. The Gateway, service role,
coordinator consumer and installation/cutover integrations remain separate work.

## Reconciliation and retention

The caller durably retains an opaque `request_id` **and original `issued_at`**
before attempting registration. Together with the trusted consumer/account/repo
binding they identify one request. A new acceptance requires that timestamp to
be no more than 24 hours old and not in the future. Repeating the same request
returns its existing registration/outcome without resetting its deadline or
replacing pending delivery. Different criteria or deadline return `conflict`.
A new timestamp or request ID is a new request, not a reconciliation attempt.

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
python tools/run-plugin-tests.py agent-pull-requests --test-file tests/test_watch_replay.py --test-file tests/test_watch_registry.py --test-file tests/test_watch_delivery.py
```

The replay suite covers scoped reconciliation, account-separated observation,
stable deadlines, foreign/stale identities, retention/capacity/batch bounds,
corrupt state, pre/post-replace failures and a real restarted consumer process
that commits an idempotent synthetic effect before or after ACK. It also drives
the actual native callback scheduler alongside a pending pull event to prove
that pull records are not executed or acknowledged by that adapter.

These are domain/persistence/process checks, not authenticated serving,
Gateway, container, credential or installed-lifecycle acceptance. Those remain
required before advertising a fleet service or enabling its dispatch consumer.
