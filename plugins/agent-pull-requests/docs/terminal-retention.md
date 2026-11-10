# Terminal intent, compact recovery and scoped cache retirement

This internal source embedding implements the storage/workspace stages of the reviewed
[terminal-retention sequence](../../../efforts/active/durable-pr-watch-delegation/terminal-retention.md).
It adds no service, activation operation or automatic adoption. The existing
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
provided. The optional read-only serving contract below does not adopt state
or expose retirement as an HTTP/CLI operation. Installed reader/lifecycle
transition and consumer qualification remain separate.

## Optional executing read-only receiver

A trusted embedding may construct `WorkspaceOperations(...,
conversations=True, retention_target=..., terminal_reads=True)` with all three
terminal ReplayStore readers selected and their existing storage extensions
already adopted. It supplies no writer, feedback owner, prepared receiver,
comment mappings or selection preparer. Construction and service startup do
not provision, adopt, compact, retire, downgrade or change credentials.

The existing capability route then declares `agent-pull-requests.workspace/v2`
and an exact `terminal_read_contract`:

```json
{
  "protocol": "agent-pull-requests.terminal-workspace-read/v1",
  "effects": "none",
  "retirement": "existing_lifecycle_only",
  "terminal_retention_version": 1,
  "terminal_recovery_version": 1,
  "terminal_cache_retirement_version": 1
}
```

Only `capabilities`, `load`, `sync`, `lookup`, `chunk` and `events` are served
under terminal-read/v1. The separately negotiated
[compact mapping reader](#optional-terminal-mapping-receiver) does not extend
that exact operation list.
The descriptor reports the **actual** adopted owner version (3, 4, 5 or 6);
its `agent-pull-requests.terminal-conversation/v1` descriptor remains body-free,
trusted-ingest-only and non-acknowledging. Read-only means no forge mutation;
load/sync still commit their normal acquired journal/cache state. An operation
cannot inspect compact recovery or submit/allocate an action through this
receiver. Retirement continues through the existing internal lifecycle API,
not through a request field or new collector.

Native/Fleet clients validate the executing receiver's declaration before
selecting this optional form. Fleet calls carry `workspace_contract` equal to
the terminal-read protocol on the same scoped request envelope; Native calls
retain that selection only after successful discovery. The receiver refuses
missing/mismatched selection before acquisition. The existing CLI/tool facade
uses the same negotiation and exposes no write tools. Unknown metadata refuses
without sending an operation or falling back to v1.

Default-off non-adopted workspace/v1 owners and prior clients keep their
baseline. A prior client cannot use a terminal v2 descriptor as v1; a rolled-back
strict file reader refuses adopted extensions without rewriting them. The old
conversation/v1 descriptor still refuses terminal and higher owner layouts.
No baseline contract is deprecated and no simultaneous upgrade is assumed.
Reader exclusion during storage adoption remains the lifecycle owner's duty.

`WatchService` validates the selected reader under its actual writer before
starting its connector/observer. The existing attention reader requires a
protected writer and is not composable with this read-only binding.
Source fixtures exercise this boundary, same-file reader/service
restart, all four declared layouts, terminal/cache retirement and reopen,
Native/Gateway reads and CLI selection. The fixture connector/native source
does not prove installed hosting, live provider transport, retained-volume
adoption, feedback/write serving or a complete automatic retirement lifecycle.

`tests/test_terminal_service_lifecycle.py` additionally qualifies the explicit
lifecycle against the actual service writer, Gateway task accounting and
populated workspace buffers. Admission is withdrawn before joining requests.
A disconnected acquisition remains owned until its thread settles; workspace
drain expiry, service-close expiry and cancelled service close retain the
writer and original cache/buffer bytes. Successful retirement uses that same
materialization owner, not a replacement with an empty buffer. Fresh file
readers then preserve the terminal cursor and exact pending event through
restart and reopen; delayed old-epoch retirement cannot remove the new
generation. Only the original exact ACK settles that event. The connector
and native source remain synthetic, and this adds no executing-host policy,
remote retirement operation, automatic worker or retained-reader adoption.

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
watch selection. A consumer that needs the original request budget first
checks `workspace_terminal_read.target_capture_contract()` for
`agent-pull-requests.terminal-target-capture/v1`, with `phase:
before_acquisition` and `budget: request_checkpoint_and_expiry`. It may then
select `retention_target_deadline=True`; the callback receives the original
`expires_at` and budget-aware `checkpoint` as keyword arguments before native
IO. The default retains the single-scope callback shape. This separate
in-process declaration does not change the remote terminal-read descriptor.
Capture matching admitted registrations through the existing ReplayStore;
an empty synthetic set is not a substitute for existing watches. A callback
failure or withdrawn budget prevents acquisition, not an implicit retry.
A terminal generation is observed without publishing another
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

## Optional lossless acquired-comment mapping

The independent `terminal_comment_mapping=True` ReplayStore reader requires
terminal/recovery reader selection. Its explicit
`TerminalCommentMappingStore(owner).adopt(lifecycle_checkpoint=...,
expires_at=..., checkpoint=...)` requires existing terminal recovery and
`review_comment_mapping_version: 1` adoption. It adds
`terminal_comment_mapping_version: 1` to the same owner-v3/v4/v5/v6 envelope,
without changing the base layout. The lifecycle owner excludes incompatible
readers; older strict readers refuse without rewriting state. Selection,
startup, acquisition and reads do not adopt or downgrade it.

The original compaction transaction transfers each captured prepared action's
existing nullable acquired-evidence slot into `comment_observation` on its
compact action. Its 2 KiB maximum growth remains independently funded alongside
the compact record's original receipt allowance, within the same review
capacity and operation/action bounds. Invalid or unfunded replacement refuses
atomically. Compaction of an operation carrying adopted mapping slots now
refuses unless this lossless extension is adopted, rather than silently
discarding the slots. Non-mapping operations retain their prior behavior.

`TerminalCommentMappingStore.read(operation_id, scope, action_id,
expires_at=...)` reads either the active original action or its compact
replacement under the same scoped owner. Its internal
`agent-pull-requests.terminal-comment-observation/v1` result identifies
`representation: active|compact`, original candidate/intent/action identity,
minimal prepared comment/write GUIDs and body hash, original outcome/receipt,
`mapping_supported` and the nullable retained slot. It does not reconstruct
retired source/preparation fields or change the prior retained-comment/v1
response. No body, cache access, native read or send is needed.

A compact prepared action created before lossless preservation has no
recoverable slot: the new read reports `mapping_supported: false`, not an
invented null observation. Adoption never backfills that missing evidence.
An admitted but unobserved slot instead reports support with `mapping: null`.
Transferred evidence keeps its original generation and discussion interval;
neither a positive current-content correlation nor its object digest proves
an original native version. Protected recovery receipt/state changes clear
the affected slot in the same envelope. Reopen does not recalculate compact
history, confirm an unknown effect or restore executable preparation.

### Optional terminal mapping receiver

After storage adoption, a trusted embedding may additionally select
`WorkspaceOperations(..., terminal_reads=True, terminal_mapping_reads=True)`.
The existing read-only constructor still excludes writers, prepared receivers,
active mapping acquisition, feedback and selection preparation. It requires the
capable `terminal_comment_mapping=True` file reader. Selection does not adopt
storage; missing adoption disables only the optional mapping declaration.

The executing receiver adds `terminal_comment_mapping_contract` alongside the
unchanged terminal-read/v1 descriptor and exact baseline operations:

```json
{
  "protocol": "agent-pull-requests.terminal-comment-mapping-reader/v1",
  "operations": ["retained-comment"],
  "result_protocol": "agent-pull-requests.terminal-comment-observation/v1",
  "representations": ["active", "compact"],
  "terminal_comment_mapping_version": 1,
  "effects": "none"
}
```

Native/Fleet clients and the CLI/tool facade discover this separate optional
operation before sending `retained-comment`. Its request fields remain
`number`, `operation_id` and `action_id`; Fleet requests select the new
protocol through the existing `workspace_contract` envelope field. Native
calls retain the separate selection only after successful discovery. The
receiver rechecks actual selection and storage on every read. Only this
selection can serve the active/compact result described above; neither
terminal-read/v1 nor retained-comment-reader/v1 changes meaning.
Both client paths validate original identities, revision, minimal prepared
identity, receipt shape and nullable acquired evidence with the existing
owner validators; a present but malformed value is not a successful read.

Older terminal-read consumers ignore the additive descriptor and continue
using their original selection and operations. New consumers with a prior,
missing or malformed declaration refuse the optional read before dispatch,
without disabling baseline commands. Withdrawal or a provider rollback after
discovery refuses the stale optional request without another search, native
read or mutation fallback. Refreshing discovery drops its selection. A strict
file-reader rollback remains a separate storage incompatibility and refuses
expanded state unchanged. No old protocol is deprecated by this expansion.

This adds no remote preparation, submission, feedback, attention writer or
retirement operation. Reads preserve receipts, events, pending delivery,
handled positions and ACKs. Isolated Native/Gateway/CLI and service restart
fixtures cover active and compact reads after populated-cache retirement,
mixed-version directions and withdrawal. They do not establish retained-host
adoption, provider or installed acceptance.

## Scoped cache and server-memory retirement

The independent, default-off `terminal_cache_retirement=True` ReplayStore
reader requires both terminal and recovery reader selection.
`TerminalCacheRetirementStore(owner).adopt(lifecycle_checkpoint=...,
expires_at=..., checkpoint=...)` requires recovery adoption and adds
`terminal_cache_retirement_version: 1` with protocol
`agent-pull-requests.terminal-cache-retirement/v1` to the same owner envelope.
An older reader refuses unchanged. Lifecycle exclusion is checked through
post-callback validation and commit, including terminal-intent adoption.
There is no CLI/HTTP activation or automatic storage adoption.

The existing workspace lifecycle owner calls
`operations.retention.retire_cache(scope, cursor, expires_at=..., checkpoint=...,
deadline=..., lifecycle_checkpoint=...)`. All captured operations must already
have compact recovery representations. The call rejoins actual admitted work;
a prior durable drain is insufficient. It then retains the lifecycle condition,
replay custody, materialization lock and cache lock in that order. No disk lock
is held while waiting for drain or performing forge IO.

Before any physical mutation, the replay envelope durably captures the exact
terminal cursor, bounded validated generation IDs and original current pointer.
Progress shares the existing terminal byte reservation, with at most 1024
scope/epoch records and 1024 captured objects per record. Full capacity refuses
without eviction. Valid pre-adoption objects in that exact full scope can be
captured under the lifecycle owner's exclusion; filenames or generation hashes
alone never supply authority. Wrong cache namespace/service/limits, corruption,
unexpected paths and linked files refuse before removal.

The cache owner durably replaces only the selected scope's pointer with `None`
before unlinking its captured, revalidated immutable objects. Atomic staging
files and other scopes remain untouched. Uncertain pointer replacement stops
before object removal; exact retry reads the same pointer and re-acknowledges
its durability. Partial removal resumes only with the cleared pointer and
original captured inventory. A new pointer/object is not silently recaptured.
The existing materialization owner then removes that scope's buffered bytes
and captured/prior-epoch generation-to-cursor bindings. Client-held bytes
already delivered cannot be erased remotely.

The owner persists `captured`, `cache_retired` and `complete` stages. A returned
`status: complete` proves **only scoped cache/server-memory retirement**.
It does not settle a pending event or unknown effect, establish whole-system
body-free retention, retire backups/logs, or qualify installed consumers.
Frozen pending payloads retain exact bytes and IDs until their original
ACK/cancel/settlement; unknown receipts retain their original recovery policy.
`delivery_settled` and `recovery_settled` remain false rather than inferring
either outcome from physical work. Reopen changes the authoritative epoch and
fences delayed retirement before any pointer/object or memory mutation.

Only ordinary removal of the selected cache's captured files is implemented.
No whole-cache rebuild, repair, secure erase, zero-fill or TRIM is invoked.
Executing-host reader/service, skew/restart and supported rollback qualification
remain T3 gates, separate from isolated source validation and rollout permission.
