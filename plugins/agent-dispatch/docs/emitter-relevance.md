# Opt-in emitter relevance checks

A task-authoring command emitter may reconcile its **retained creation
receipts** against authoritative backing-object state before its normal
production command. This reuses the existing lease, tick, receipt owner and
[registered relevance receiver](task-relevance.md). It starts no separate
daemon and does not change emitters without `spec.relevance`.

## Configuration and source command

```yaml
name: reviews
kind: emitter
spec:
  id: review-source
  command: [review-source, discover-open]
  interval_seconds: 60
  task_output: json
  relevance:
    command: [review-source, check-retained]
    batch_size: 25
    timeout_seconds: 30
```

`relevance.command` is an argument list, not a shell expression.
`batch_size` defaults to 25 and permits 1-100 tasks per tick;
`timeout_seconds` defaults to 30 and permits positive finite values up to 300.
The source command uses the emitter's execution cwd/environment and resource
resolution. Supplier-local command operands retain their named repository
context. Only a granted lease enters reconciliation.

The supervisor passes the actual registration id to its child, separately
from the emitter's `spec.id`. Direct diagnostic invocations use
`emitter tick|serve SPEC --holder HOST --registration-id REGISTRATION`.
An opt-in caller without that id refuses rather than guessing it from the
emitter id. The supervisor adds the argument only for an opted-in emitter;
ordinary command lines and task-output behavior are unchanged.

The check receives JSON on stdin:

```json
{"version":1,"tasks":[{"receipt":{"seq":1,"task_id":"task-a","dedup_key":"object:42"},"snapshot":{"task_id":"task-a","repo":"owner/repo","source":"emitter","origin_ref":"review-source","generation":0,"revision":7}}]}
```

This abbreviated example omits additive receipt/snapshot metadata. For each
selected task, return an explicit observation:

```json
{"version":1,"observations":[{"task_id":"task-a","verdict":"obsolete","reason":"Provider reports the object merged","observation_ref":"provider:object-42:revision-8"}]}
```

The command must check each retained backing object, **including closed or
merged objects missing from open-only discovery**. It owns interpreting
`dedup_key` and any other domain references. Return `needed`, `obsolete` or
`unknown`; only an authoritative no-longer-needed observation warrants
`obsolete`. A missing listing entry, failed request, partial listing or elapsed
time is not closure evidence. Unknown source state should return `unknown`.
The command cannot supply registration scope, task revision or request identity.

Version, exact task membership, uniqueness and bounded nonempty
reason/observation references are validated before any reports. A timeout,
nonzero exit, malformed output or missing/foreign/duplicate observation sends
no reports for that batch. The regular production command still runs; the
tick exposes reconciliation errors and unhealthy status rather than silently
claiming success.

## Admission, retention and recovery

Before the source command, each task receives a snapshot from the current
receiver's effective registration scope. A registration-held, foreign, terminal,
closeout-pending or unsupported task is not sent to the checker. The current
emitter spec must match `emitter_spec_revision`; a stale process cannot take a
fresh snapshot under a changed definition. Generation, task audit revision and
effective registration revision fence movement between observation and report.
A newer accepted source observation invalidates an older task snapshot.
Domain-specific source revision/reopen semantics still belong to the adapter;
an opaque `observation_ref` is not a provider transaction or an atomic source lock.

Snapshots use one bounded `GET /task-relevance/snapshots`; reports use one
`POST /task-relevance/reports`, each capped at 100 tasks. Each request discovers
fresh registrar declarations once, outside queue write transactions. Each task
still resolves current direct registrations, overrides and task revisions under
its own queue transaction. The batch is not an all-or-nothing transaction:
one rejection or post-commit callback failure does not suppress sibling
decisions. A report's request id remains its recovery identity, not a batch id.

Each task has one original report persisted in `relevance.json` beside its
emitter receipts before POST. The existing receipt owner's thread/file lock
serializes the complete check/report cycle. A successful response removes the
pending entry. After restart or lost acknowledgement, the caller reads that
original request's receipt; it never resends an uncertain POST or invents a
replacement request. A missing receipt is not proof of no effects. Pending requests survive trimming of creation receipts and registration changes.
Recovered and successful acceptances are checkpointed in batches rather than
rewriting and syncing the whole sidecar per receipt. A crash before that
checkpoint can recover the same server receipts again. Original pre-send
requests and confirmed rejections retain their immediate checkpoints because
the latter have no acceptance receipt to recover.
Pending requests are capped at the receipt owner's 2,000-record budget.
At capacity, new checks pause while bounded receipt recovery and ordinary
production continue; unresolved outcomes are not evicted to make room.
Client failures confined to the pre-POST health/capability check expose
`RelevanceNotSent` and can clear that unsent pending entry for a later fresh
check. Unsupported preflight also remains a `DispatchUpgradeRequired`.
Exceptions after POST begins, including invalid acknowledgement envelopes,
do not carry the guaranteed-unsent signal.

A current receiver can return an additive `relevance_rejection` alongside
the unchanged string `detail` on HTTP 409. It identifies
`relevance_not_accepted`, `task_id` and `request_id` only after the synchronous
decision has failed and its atomic acceptance receipt is confirmed absent.
The matching caller can then discard that rejected request and perform a fresh
source check on a later tick. A post-commit callback failure or an unavailable
receipt read cannot produce this safe-rejection signal. Plain or mismatched
errors remain uncertain and retained.

Selection consumes a persisted finite sweep of unique retained task ids before
starting the next sweep. Repeated discovery receipts cannot move an open task
ahead of already retained work; newly discovered tasks join the next sweep.
There is at most one snapshot batch, one report batch and one checker process
per tick. Pending receipt recovery
has its own bounded rotating cursor. The creation log retains its existing
2,000-record cap and unchanged public receipt format; the tick reports
`receipt_history_trimmed` when its earliest retained sequence exceeds 1.
This is not full historical coverage. Periodic queue-backed evaluation remains
the planned backstop for tasks whose creation receipts are no longer retained.

The result separates accepted decisions, recovered acceptances, pending count
and errors. `closeout_pending` is not session/workspace cleanup. A bound task's
original owner still closes out through the existing steering/wake and managed
workspace owners; the caller does not release reservations or delete files.

## Compatibility and rollout

Publish this receiver before opting in an emitter. The caller requires the
running peer's effective relevance scope, `bounded_batches: true` **and** the
snapshot's spec revision;
a prior direct-only receiver, a receiver without spec correlation, or a rollback
receives no report. Existing emitters and ordinary operations remain available.
An older client can ignore additive snapshot/error fields and still use the
original string error detail. An unknown prior report is retained even if the
receiver becomes unsupported.

Disabling `relevance` returns the producer to its old task-creation behavior;
it does not clear pending requests or undo accepted closeout. Retain the
sidecar and re-enable a capable owner to reconcile it. Do not remove accepted
closeout history or run an older queue consumer that can restart obsolete goals.
No domain adapter or runtime configuration is activated by adding this source.

See also: [task relevance](task-relevance.md), [emitter recovery](emitter-recovery.md),
and [emitter command receipts](../README.md#emitter-command-receipts-agent-dispatch-emitter-receipts).
