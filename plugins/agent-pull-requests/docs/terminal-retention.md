# Terminal intent, workspace admission and compact recovery

This internal source embedding implements the first part of the reviewed
[terminal-retention sequence](../../../efforts/active/durable-pr-watch-delegation/terminal-retention.md).
It adds no service, discovery operation or automatic adoption. The existing
[conversation journal](conversation-journal.md),
[review owner](review-owner-store.md) and
[workspace](workspace-integration.md) retain their baseline behavior by default.

## Reader and storage selection

An embedding explicitly constructs `ReplayStore(..., terminal_retention=True)`
to select a capable file reader. This does not adopt state.
`TerminalRetentionStore(owner).adopt(lifecycle_checkpoint=..., expires_at=...,
checkpoint=..., retention_bytes=...)` expands an already conversation-capable
owner-v3/v4/v5/v6 envelope through its existing complete-file transaction.
The actual lifecycle owner must first exclude or transition incompatible
readers. Neither a callback label nor the source API proves that transition.

The orthogonal `terminal_retention_version: 1` contract stores its records,
generation/epoch bindings and capacity in `watch-replay.json`. Its protocol is
`agent-pull-requests.terminal-retention/v1`, independent of package version.
The reservation defaults to 8 MiB, with at most 1024 retirement records,
1024 authoritative live admissions and 1024 generation bindings. Full capacity
refuses without eviction, borrowing
another reservation or rebuilding the cache. Pending deliveries and original
operation/attempt/receipt records are never discarded.

Older strict readers, including a new reader not explicitly selected for this
extension, refuse the expanded envelope without rewriting it. A capable reader
still preserves non-adopted layouts. No downgrade or automatic rollback is
provided. There is no terminal-retention HTTP/CLI capability: an installed or
independently updated service must not adopt this source embedding until its
actual reader/lifecycle/consumer boundary is separately qualified.

## One terminal decision

The trusted acquisition owner supplies a full normalized generation and the
actual `ObservationTarget` captured before acquisition. The target's existing
account/repository/PR binding must correspond to the generation's full
provider/endpoint/project/repository/principal/namespace scope. Ambiguous
reuse of one watch binding across different full scopes refuses.

Only an accepted, complete authoritative terminal observation stages retirement.
Missing/partial evidence, stale observations and selector departure are not
retirement. In one envelope replacement, `ConversationStore.observe_workspace`
commits the journal tombstone, terminal scope/epoch fence, original operation
identities and notification progress. Plain `observe` cannot bypass this path
on an adopted owner. Interrupted replacement leaves the old complete envelope
or the same terminal decision and frozen event identities; reconcile the exact
scope/cursor rather than inventing another event.

The existing replay core projects only minimal terminal facts for captured
registrations, retaining each registration's criteria, request identity and
original deadline. It does not rewrite a previously frozen event. A matching
active legacy delivery with no finite deadline refuses terminal admission;
the existing owner needs an explicit bounded recovery policy, not a collector-
invented timeout. A later native replay poll also uses the retained terminal
facts instead of reintroducing a native snapshot body. Only exact ACK/cancel
continues to settle a delivery; terminal observation and drain do neither.
ReplayStore captures the full-scope epoch on its actual pre-IO observation
target and preserves it when refreshing waiting registrations. A result
superseded by reopen uses only the matching retained body-free terminal facts,
or reports stale observation; it never falls through to the old native body.
If a complete observation changes closed-unmerged to merged without an observed
reopen, the same retirement keeps its original cursor/custody and updates its
latest body-free evidence. Newly satisfied registrations get their minimal
event in that envelope; previously frozen events keep their exact bytes and IDs.

## Admission, drain and reopen

On the adopted owner, selection allocation/retention, execution admission,
begin-action and feedback capture check the live epoch inside replay custody.
Historical operation IDs remain fenced after reopen. Original senders can
still retain their admitted attempt's receipt; a fence is not rollback or
proof that an already admitted effect did not happen.

The existing workspace accounts for admitted acquisition/read/send work.
Its explicit `retention_target` callback supplies the actual pre-acquisition
watch selection. A terminal generation is observed without publishing another
body-bearing cache object or returning its descriptor. An accepted complete
live acquisition binds its generation to the current epoch before cache
publication. Late observations and old-epoch generations cannot republish or
serve a buffered body after reopen.

The last complete live admission is retained separately from partial journal
evidence. A transient missing page can update that evidence without fencing
previously valid lookup/chunk reads or preventing sync from reacquiring a
complete generation. Partial refreshes are not published. Definite cache
`full`, `conflict` or `failed` outcomes discard newly reserved generation
bindings; potentially visible `unconfirmed` outcomes retain their binding for
exact readback. Neither case evicts an existing binding.
The existing replay transaction durably persists a new binding reservation
before the separate cache pointer can become visible, retaining replay custody
across both bounded stages. Interruption immediately after cache publication,
or a later owner-file write failure, therefore leaves the surviving generation
readable under its original epoch after restart. A pre-publication interruption
can leave a funded binding with no cache object; exact retry reuses it and
does not infer publication or permit a send.

Lock order is the short workspace lifecycle condition, replay transaction,
materialization lock, then cache transaction. Request accounting releases the
condition before IO. Bounded cache publication/materialization holds replay
custody; native acquisition/sending and drain waits do not hold disk locks.
Existing nonblocking lock contention remains a busy failure, not an implicit
retry or evidence of absent state.

The lifecycle owner calls `operations.retention.drain(scope, cursor,
expires_at=..., checkpoint=..., lifecycle_checkpoint=..., deadline=...)`.
Its actual exclusive-reader checkpoint must cover this workspace and exclude
foreign/incompatible readers. The method joins admitted local work under the
existing finite deadline, then rechecks the exact terminal epoch before
recording `drained`. Timeout or lost custody preserves `pending`. A prior drain
record is not a permanent guarantee against later acquisition or permission
to compact; subsequent physical stages must rejoin/fence their actual owner.

## Compact original operations and protected readback

A separate explicit `ReplayStore(..., terminal_retention=True,
terminal_recovery=True)` reader can select the internal recovery extension.
`TerminalRecoveryStore(owner).adopt(lifecycle_checkpoint=..., expires_at=...,
checkpoint=...)` requires existing terminal-intent adoption and the actual
lifecycle owner's exclusion of incompatible readers. It adds
`terminal_recovery_version: 1` and the
`agent-pull-requests.terminal-recovery/v1` section to the **same** replay
envelope. A terminal-capable reader without this additional selection refuses
the expanded envelope unchanged. This is not a serving capability or rollback.

The actual workspace owner calls `operations.retention.compact_recovery(scope,
cursor, policies, expires_at=..., checkpoint=..., deadline=...,
lifecycle_checkpoint=...)`. It rejoins admitted work even after a persisted
`drained` result, then holds the existing workspace exclusion and replay
transaction while replacing that terminal epoch's original review operations.
An active or delayed sender must finish first. Reopen or expired/lost custody
refuses; an uncertain replacement requires readback of that same epoch.

`policies` binds every captured operation ID to its **original** recovery
`owner`, finite `deadline_at` and `escalation_ref`, supplied by the existing
execution/lifecycle owner. These values cannot be invented by a collector or
changed on retry. Expiry preserves unknown identities and requires escalation;
it never confirms, cancels or deletes an effect.

The compact record retains original scope, operation/intent/action/attempt IDs,
candidate, generation/source hashes, native receipts, selection input/stage,
prepared comment/write GUIDs and necessary content hashes. Unattempted actions
become `retired`; unknown and confirmed remain distinct. No body, source
manifest or executable intent survives in the compact record. Body-free
feedback captures keep their original binding without claiming handled progress
or original-version proof. Existing frozen delivery payloads are untouched.
Attention projection joins those original captures to compact operations;
unknown recovery still reports reconciliation pending after reopen.
Owned-edit allocation also checks compact unknown receipts under the same full
scope, including partial native target IDs. A compact confirmed prepared
target without its retired original preparation refuses rather than becoming
an unrelated legacy comment.

At most 1024 compact operations use the existing bounded review reservation
alongside active operations, including reserved receipt growth. A short
original intent can require slightly more compact metadata: the transaction
must fund that from unused review capacity, never another budget or eviction.
Full capacity refuses the complete replacement. Native readback cannot grow
beyond its original reserved receipt allowance.

`reconcile_terminal(TerminalRecoveryStore(owner), scope, operation_id,
context_factory=..., checkpoint=..., expires_at=..., budget_seconds=...)`
uses the original protected ADO owner's pinned credential transport. It
freshly verifies principal/repository/PR and can confirm only an unknown
create-thread comment with one exact original native action marker, actor and
content digest. The body hash uses the workspace's canonical JSON digest;
the separately retained prepared `body_id` keeps its original UTF-8 SHA-256.
Current text, vote, actor or comment GUID alone cannot confirm an old effect.
Replies, edits, votes, unsupported receivers and incomplete/unattributable
reads remain unknown under the original recovery deadline. There is no
perform/send path, no new attempt and no feedback advance. No replay-file
lock is held over native IO; the receipt is checked again against the same
original unknown attempt when it commits.
Crossing the original deadline during IO or receipt commit reports escalation
on that same invocation while retaining the unresolved attempt.

This leaf removes selected bodies from the owner file only after actual drain.
It does **not** retire cache pointers/objects, server buffers or
generation/cursor bindings, strip a frozen pending event, or qualify installed
consumers. Those remaining physical-retirement and T3 gates stay open. No
ordinary file deletion, secure erase, zero-fill or TRIM operation is added.
