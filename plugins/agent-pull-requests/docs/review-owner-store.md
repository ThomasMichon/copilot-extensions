# Review data in the existing owner store

This source-library slice realizes the storage portion of the
[reviewed submission contract](../../../efforts/active/pr-review-workspaces/submission-slice.md).
`ReplayStore` remains the sole owner of `watch-replay.json`, its operation lock,
atomic writer and namespace/service binding. No second file/ledger, daemon,
watcher, serving route or backend action is added.

## Explicit adoption

```python
existing_store.adopt_review_operations(
    lifecycle_checkpoint=existing_reader_transition_check,
    expires_at=current_request_expiry,
    checkpoint=existing_current_owner_check,
    replay_bytes=64 * 1024 * 1024,
    review_bytes=64 * 1024 * 1024,
    receipt_bytes=4096,
)
```

The existing lifecycle owner must actually exclude incompatible long-lived
readers or transition them to executing v2 support. A callback/capability label
alone is not authority or proof. This API never stops, deploys or installs a
reader. Missing/corrupt/linked state refuses; reads do not silently provision.

The outer `agent-pull-requests.owner-store` version 2 envelope preserves the
complete inner `WatchReplay` v1 state and adds review records and explicit
capacity declarations. Native/service bindings remain distinct from the native
principal in an individual review scope. Old v1-only readers fail without
rewriting the v2 document.

Every replay transaction preserves review data; every review transaction
preserves current replay data. Source restart reads both from this same file.
There is no automatic downgrade or receipt deletion.

## Selected intent and data transitions

`admit_review(selected, expires_at=..., checkpoint=...)` accepts a version-1
`agent-pull-requests.review-intent`: stable `operation_id`, exact normalized
`scope`, full `generation_id`/`source_id`, head/common/iteration revision and
ordered unique action IDs. The first action shapes are a comment, reply,
owned-comment edit with an expected content hash, and named verdict.
Vote actions currently support `APPROVED` and `CHANGES_REQUESTED`. `COMMENTED`
casts no native vote and is not admitted as a vote action; a comment-only
selection contains its explicit comment actions and no invented vote.
Existing persisted `COMMENTED` intents remain readable. Their unattempted vote
action is unsupported rather than creating a new unknown attempt; existing
unknown state is retained without replay.
Unsupported extra fields, completion-setting writes and malformed identities
refuse. Data validation does not prove source review, ownership, consent,
permission or current backend eligibility.

The complete selected intent is fingerprinted. Reusing an operation ID with any
different intent refuses rather than replacing it. Initial actions are
`unattempted`. `inspect_review()` returns retained state without modifying it.

`begin_review_action()` durably changes one eligible ordered action to
`unknown` and creates its retained attempt ID before returning `new_attempt`.
That result is **data fencing, not permission to send**. Repeated unknown or
confirmed actions return `new_attempt: false`; an unresolved predecessor blocks
later actions. Pre-replace failure leaves the old state; uncertain persistence
requires exact readback and never permits a blind send/retry.

`record_review_receipt()` is an existing-sender-only data transition tied to that
admitted attempt. It refuses an unattempted action, mismatched attempt, conflicting
known native ID or changed confirmed receipt. Partial native response IDs remain
retained with `unknown`; consistent later evidence may add missing IDs without
dropping earlier ones. Comment receipts need thread/comment IDs, owned updates
must match selected targets, and vote receipts must match the selected native
principal before confirmation.

An attributable readback additionally binds operation/action ID, scope content
ID and intent ID, and requires a native event ID or action marker. Equal current
vote/content/reviewer identity alone is insufficient. These fields are
consistency rules on the trusted owner's evidence, not a new provenance issuer.
No-send evidence remains conservative `unknown` here: this storage primitive
cannot prove retirement of a still-admitted effect owner.

## Capacity and replay isolation

Admission reserves original intent plus bounded worst-case receipt/attempt growth
for every action. Receipts use their original reservation, not a new admission
quota after an effect. Partial outcomes retain bounded native IDs; oversized
responses do not become unbounded persisted data.

Replay and review byte reservations are separate. Replay admission/validation
accounts for each active registration's existing maximum pending-event growth.
Retained bytes are counted at their actual size. A waiting registration reserves
both possible unknown-baseline promotion and one pending event; once its
noncontinuous event is frozen, only bounded acknowledgement growth remains.
Existing pending data is not counted twice. A non-firing observation whose retained baseline would leave inadequate
event headroom refuses before persistence, preserving the previous funded state.
Full review capacity refuses additional intent but leaves replay transactions and
already-reserved receipt persistence available. Neither namespace borrows or
silently shrinks the other's capacity. Unknown/terminal receipts are not evicted.
The whole owner file also has a finite read bound.

Locks are held only for complete data transactions. A future protected executor
must retain its own existing admission/lifetime while backend IO runs outside
these shared transitions, reloading current state for subsequent receipt commits.
Whole-file rewrite/fsync and retained-history contention still require measured
integration qualification; this slice makes no serving latency claim.
Current-owner checks run again after lock-ownership inspection immediately
before mutation and before returning its result. Owner-envelope encoding and
validation finish before that final mutation check, including replay commits.
A commit already admitted into
atomic persistence may finish while the owner is withdrawn; the later denial is
not rollback or proof of absent state. Re-read that same operation under a valid
owner instead of inventing a replacement identity or blindly repeating effects.

## Optional prepared-intent storage extension

The existing lifecycle owner may explicitly call
`adopt_prepared_reviews(lifecycle_checkpoint=..., expires_at=..., checkpoint=...)`.
This orthogonal data extension adds `review_intent_version: 2` to the current
owner envelope; it does not require adopting conversation, attention or capture
features merely to store a prepared review. Those existing layout versions and
capacity declarations retain their meanings and preserve the extension.
Old strict envelope readers refuse the extra field before rewriting anything.
The lifecycle checkpoint must actually exclude or transition those readers,
as with the existing adoption API. A submit request never adopts it.

An explicitly adopted owner accepts review-intent/v2. Each comment/reply/owned
edit retains its existing body plus `preparation` with exactly `identity` and
`body_id`, selected from the actual
[shared preparation result](../../ai-attribution/docs/comment-preparation.md).
The identity retains its public comment/write GUIDs and original source
projection. The hash and canonical trailing marker must agree with the exact
body, including the producer's single-outer-marker and conservative literal
boundaries. Quoted and closed code examples remain authored content; duplicate
outer markers and unfinished fences/comments refuse before admission.
No second body or profile history is stored. Bodyless votes carry no
preparation and do not create filler comments.

One selected operation has at most one prepared action per logical comment;
different replies need different comment/write identities. A write GUID cannot
be reused by another retained action/operation. These are owner-local data
consistency checks, not a distributed uniqueness service, evidence of who
prepared the content or authority to post it. The actual producer still owns
profile/audience/source policy and preparation before final approval. Original
operation fingerprints bind the entire selected body and metadata through
restart and unknown/confirmed receipts; changed retry bytes or identities refuse.
All added bytes count toward the existing review/receipt reservation.

Legacy v1 intents and their unresolved receipts remain unchanged and do not
acquire preparation evidence from body markers. The default intent parser and
protected executor still select v1. A separate
[explicit native execution selection](protected-review-executor.md#prepared-native-intent-and-original-receipt-projection)
can use v2 and read original receipt correlation; the data extension alone
activates neither. A separately selected
[workspace receiver](workspace-integration.md#optional-prepared-review-receiver)
can expose the adopted contract; data adoption alone advertises no capability
or installed/provider qualification. Rollback cannot remove
the extension or downgrade a file with unresolved v2 records.

## Optional pre-approval selection retention

`ReviewSelectionStore(existing_store)` uses the same file, operation lock,
transaction and review capacity. Its explicit `adopt(lifecycle_checkpoint=...,
expires_at=..., checkpoint=...)` requires prepared-review adoption and adds
`review_selection_version: 1`. Old strict envelope and operation readers
refuse this extension; the existing lifecycle owner must exclude them before
adoption. No request adopts or downgrades it.

The trusted source owner may call `retain(original, prepared, expires_at=...,
checkpoint=...)` with a v1 original selection and the actual composed v2
intent. Non-body selection, source/revision, action order and native targets
must agree. The owner retains only the original selection's digest and a
`prepared` stage alongside the single final intent/body and funded eventual
receipt reservation. It stores no original draft or profile archive.
`recover(original, ...)` reads that exact retained operation without composing
again. Changed input refuses; a same-key race or repeated retain returns the
first committed final bytes, not a later origin/profile's candidate. Existing
nonblocking lock contention refuses; reconcile the same key after it clears.
An uncertain commit must be reconciled by that same original key.

This is a **data-only prerequisite**, not preparation provenance.
`retain` still accepts the caller's already-prepared intent. The optional
[workspace receiver](workspace-integration.md#optional-pre-approval-selection-receiver)
composes the allocation API below under the
[reviewed pre-approval contract](../../../efforts/active/durable-pr-watch-delegation/comment-intent-binding.md#durable-pre-approval-selection-and-allocation).

The optional source-only `allocate(original, compose=..., expires_at=...,
checkpoint=...)` creates fresh version-4 comment/write GUIDs for **new comments
and replies**. Resolve actual policy, effective profile and source through their
existing owners before calling it; `compose` must be a bound synchronous,
bounded pure preparer, not a resolver or native/network IO callback. Under the
same owner lock, allocation first reconciles the original key, then composes
the exact final v2 bytes, validates the selected identities and commits before
returning a preview. Restart, contention and lost results use `recover` or the
same `allocate` key: neither invokes composition or allocates another identity
when the original record already exists. Current-owner checks remain mandatory;
recovery does not grant current policy permission.

Bodyless votes allocate no GUIDs and create no filler comment. This automatic
path refuses owned edits, including legacy edits, until their original
logical/native binding is supplied by the actual protected source pipeline.
The explicit caller-ID prepared path remains unchanged. This API adds no
storage format, public request syntax, capability advertisement, callback
loader, policy owner, publisher rollout or installed adoption.

A retained selection is not execution-admitted. Existing execution inspection,
attempt, receipt/projection and feedback-capture paths refuse it. Only
`admit_review()` with that exact final intent changes its stage to `admitted`;
the operation, GUIDs, body, creation time and reservation remain unchanged.
That transition is still owner-only data admission, not evidence of approval
or permission to send. Pending records cannot contain attempts or receipts.
Legacy operations and unknown fences remain unchanged, and preparation itself
does not ACK, emit Dispatch, advance feedback or perform native IO.

## Explicit remaining boundary

This is **not a backend executor**. It does not perform actual write-credential/
principal binding, action-relevant discussion/revision checks, caller policy or
native backend reconciliation. Those remain mandatory in the next protected
owner/provider slice. Do not wrap the legacy string-returning submission method
as a receipt, expose these data setters as an unauthenticated serving route, or
infer write authority from store contents, hashes or `new_attempt`.

The real-file source tests use only synthetic selected data/effects. They prove
legacy-reader exclusion, both storage bindings, restart, mutual namespace
preservation, immutable intent/receipt identity, uncertainty and reserved
capacity. They are not installed service, live ADO or rollout qualification.
