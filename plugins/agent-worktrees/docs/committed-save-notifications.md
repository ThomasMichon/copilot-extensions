# Committed-save capture and process-local notifications

This source boundary separates canonical persistence, optional cache warming,
selected mutation evidence and subscriber delivery. It is not a native event
journal or a transaction shared with the observation index.

## Persistence and release boundary

The universal serializer captures selected before-data before its revision
merges. Only after `pr_authority.write_record` successfully returns does it
provide its exact persisted text to `record_cache.store`. Canonical validation,
CAS, pair proof and write errors retain their existing behavior.

Every successful `_RecordLock` acquisition adds a process-local notification
hold, including nested/reentrant, sibling, degraded and no-sidecar acquisitions.
A failed best-effort acquisition adds none. Physical release and inactive lock
flags precede decrementing that hold. A release error retains notification
custody rather than guessing that exclusion ended.

Paired save and lifecycle guards add a separate logical notification hold.
Their nested `ExitStack` cleanup completes before that hold releases. Thus the
last physical sibling release cannot dispatch callbacks while an enclosing pair
guard is still logically active.

Delivery is synchronous on the releasing thread, only when all its relevant
holds are gone. There is no worker, listener, scheduler, spool or cross-process
delivery path.

## Subscribers and snapshot independence

`record_cache.observe_writes(callback)` retains the legacy `(path, record)`
signature. Each callback receives an independent value reconstructed from a
bounded immutable freeze of the committed caller record, with the actual `_loaded_from` path and no
transient projection-dirty flags. Mutating a callback's object cannot rewrite the
cache, another callback's snapshot, the caller's object or the saved file.

`tracking_save_notifications.observe_receipts(callback)` explicitly opts into
`SaveReceipt | DeliveryIssue`. Merely importing the module does not enable
receipt capture. Receipt subscribers are selected before mutation; legacy
subscribers are selected at the committed notification boundary. Unsubscribe
prevents future enrollment, not completion of already queued delivery.

Subscribers must be synchronous and should enqueue only bounded owner work.
Ordinary subscriber exceptions and returned awaitables are diagnosed at this
optional boundary; later subscribers still run. That exception isolation never
encloses canonical persistence or lock release. Process-control exceptions are
not converted into successful observer delivery.

Cache stat, snapshot acquisition and warming failures are separately diagnosed.
They cannot suppress an otherwise available committed snapshot. Cache warming
uses that bounded value without changing timestamp/default representations or
making an unbounded full-record copy. Standalone legacy `store(path, record)`
calls retain the supplied committed-value contract, including lightweight
observer payloads. The universal writer separately passes its exact persisted
text for receipt evidence; reparsing that text must not normalize cache values.

## Receipt meaning and bounds

Frozen receipt/projection/evidence objects contain only immutable values.
`Evidence.value()` decodes a new independent JSON value on each call.

A receipt retains the save receipt ID, path, UTC capture observation time,
project/worktree/machine/incarnation and bounded asserted-head information,
selected before/after fields and revision fences. The after projection is
parsed from the successfully saved text, not the original requested object.
When available, `saved_sha256` and `saved_bytes` identify those exact UTF-8 bytes.

The selected domains are claims, active-effort binding and disposition. A domain
is listed in `changed_domains` only when both bounded projections are available
and their canonical JSON differs. Omitted evidence is `None` with a reason,
never an empty collection or inferred zero. Record creation can initialize
these domains; this is not a set of claim-added or effort-bound native events.

Actor qualification and history completeness remain explicitly unknown.
`lock_qualification` distinguishes a matching record lock from an unverified
direct caller. It includes a degraded in-process hold and does not upgrade the
existing sidecar policy into guaranteed cross-process exclusion.
These fields do not grant actor permission, prove external
acceptance or establish complete history.

| Resource | Bound |
| --- | --- |
| Saved text retained/decoded | 256 KiB UTF-8 |
| Cache/legacy immutable value | 256 KiB accounted content including node overhead |
| Decoded structure | 8,192 node visits, depth 16, 256 KiB expanded text |
| Selected domain | 3 KiB encoded JSON; at most 64 claims |
| Receipt content budget | 32 KiB |
| Subscribers | 32 per subscriber family |
| Deferred queue | 32 notifications and 1 MiB accounted content |
| One drain | 64 queued deliveries, including reentrant work |

Oversized saved text omits legacy delivery rather than constructing a misleading
partial `WorktreeRecord`. Receipt omissions, queue overflow and finite-drain
exhaustion have explicit diagnostics. Current receipt subscribers also receive
bounded `DeliveryIssue` evidence. Counts describe delivery incidents, not
canonical mutations or lifetime totals; unavailable byte counts are unknown.
Encoded-content accounting is not a process-RSS promise.

## Remaining integration

This boundary does not append native semantic history, transport receipts to a
different process, preserve pending delivery through process death, rerun
canonical work, move storage or add archival/cleanup policy. The existing
worktree-observation receiver still serves its separately qualified sampled
history. Durable source registration, native operation identities, actor
qualification and cross-process journal intake remain separate work.

See [the architecture's record contract](architecture.md#the-worktree-record--single-writer-contract-invariant)
and [selected worktree observations](worktree-observations.md).
