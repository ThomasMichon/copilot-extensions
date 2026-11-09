# Existing-owner workspace service/client API

The [source tool/CLI façade](workspace-tools.md) exposes these operations inside
an actual existing host binding, with a separate existing-owner retained source
reader. It adds no installed owner, service, credentials or acquisition fallback.

`WorkspaceOperations` composes the existing `OwnerReadHandler`,
`GenerationCache`, `ReplayStore` and protected `execute_review`. Construction
performs no provisioning/adoption, source fetching, installation or credential
acquisition.
The internal [terminal-intent embedding](terminal-retention.md) adds explicitly
adopted epoch fencing and owner drain. Its independently selected
[read-only terminal receiver](terminal-retention.md#optional-executing-read-only-receiver)
uses workspace/v2 without enabling writes, adoption or retirement over the wire.
Default workspace/v1 operations remain unchanged; neither form activates an
installed host.
Construct it only inside the current owner pipeline with an authorized fixed
native repository/principal scope, read binding and actual write-context factory.
The factory accepts the selected PR number and returns the normal pinned context;
it is not reconstructed from caller JSON, cached data or a claimed role.

Alternatively, explicitly supply the optional in-process `write_context_factory`
instead of `write_context`. Both at once refuse. This synchronous resolver
receives `(scope, checkpoint=..., deadline=...)`, with the full selected PR
scope and enclosing monotonic deadline, and returns a synchronous no-argument
context factory. It is called lazily at most once per submission execution,
only when the executor or feedback actually requests a protected context.
All action and feedback contexts then use that same returned factory. A failed
resolution is not retried during feedback; a later request has its own resolver
selection, not a host-lifetime cached material reference.

The resolver must compose the actual credential/transport owner, for example
the optional [pinned native ADO factory](../../agent-worktrees/docs/azure-review-transport.md),
honor the supplied finite budget/checkpoint and retain current native
admission, permission and material-generation checks. The supplied checkpoint
also rejects withdrawal/replacement of the selected workspace writer. This
embedding does not implement native permission verification or host enrollment.
The existing per-context `write_context(number)` path keeps its prior calling
semantics; the request-local path never falls back to it.

Capabilities, allocation/preview, retained selection/receipt lookup and a
confirmed replay needing no protected readback do not resolve this factory.
Unknown actions may use a later execution's pinned context for reconciliation,
never automatic resend or replacement of their original durable identity.
Default and standalone read-only hosts still supply neither writer.

Pass optional `workspaces={(caller_principal, target): operations}` to the existing
`WatchService` or `WatchGateway`. Each key must already have a watch scope and the
same ReplayStore. The optional cache must share its root, namespace and persisted
service binding. Absent support preserves every existing watch response field.
The standalone launcher does not accept arbitrary callback configuration and
does not advertise workspace operations without actual adopter integration.
The optional terminal receiver is an in-process trusted embedding, not a new
standalone configuration field.

The existing handler serves `/v2/review/<target>/<operation>` over its normal
authenticated Fleet connector, request lifetime and task/drain owner. Requests
carry the existing `ReplayBinding` plus `request` data. That binding is checked
against the admitted caller; native provider identity remains separate from the
Gateway principal and store namespace. Request-side scope/credential overrides
are not supported.

## Finite operations

The source API is `WorkspaceOperations.execute(operation, payload,
expires_at=..., checkpoint=...)`; native callers use `NativeWorkspaceClient`.
Fleet clients use `WorkspaceClient` with the existing `ServiceCaller`, scoped
`ReplayBinding`, route prefix and expected native scope. This adds no transport,
auth client or operational command. Call its read-only `capabilities()` before
other remote operations; old handlers return unsupported rather than falling
back to ordinary watches or another provider.

| Operation | Required request fields |
|---|---|
| `capabilities` | none |
| `load` | `number`, `expected_current`, `budget_seconds` |
| `sync` | load fields plus exact retained `previous_id` |
| `lookup` | `number`; optional exact `generation_id` |
| `chunk` | `number`, exact `generation_id`, `offset` |
| `inspect` | `number`, original `operation_id` |
| `receipt` (optional prepared contract) | `number`, original `operation_id`, `action_id` |
| `observe-comment` (optional observation contract) | `number`, original `operation_id`, `action_id`, retained `generation_id` |
| `submit` | `number`, immutable `intent`, separate `source_attachment`, `budget_seconds` |
| `allocate` (optional selection contract) | `number`, original v1 `intent`, `source_attachment`, `budget_seconds` |

Load/sync use the current protected read owner and explicit expected-current
cache CAS. Sync is a full section refresh plus the existing normalized comparison,
not a fabricated provider delta. A partial section remains incomplete. Results
include publication status and observed revision even when CAS conflicts; that
does not make the observed generation current. The compact comparison retains
changed sections/invalidations and generation ID rather than embedding the full
generation in a physical frame.

`materialize(number, generation_id)` resolves a descriptor and reads only that
immutable ID. Each 8 KiB decoded chunk carries offset, exact total, native scope,
generation ID and full encoded-content hash. Both clients use the same bounded
schema/content validator. Old generations stay selectable when current advances;
eviction/disconnect/corruption never substitutes current or silently re-observes.
Fleet's existing 32 KiB physical frame is unchanged.
The executing owner memoizes validated immutable bytes within one bounded
16 MiB total read buffer. Each chunk rechecks the existing cache descriptor,
plain file identity and caller/owner lifetime, not the whole generation.
Changed content/evicted files refuse rather than borrowing another scope or current
ID; identical republication revalidates once and refreshes only the file stamp.
The buffer has no durable file, effect receipt or new lifecycle; final assembly
still verifies the complete hash/schema. This avoids quadratic disk/JSON work.
The client paces finite chunk requests at half the existing SDK challenge refill
rate by default, without another poller or retry loop. An existing transport owner
may supply async `read_admission` to share its actual IO pacing across consumers.
Each request still obtains its own normal challenge/token; none is cached or
reused. Inline pacing is cancellation-aware transport flow control, not a
delegation permission/read-count ceiling. Shared saturation still reports the
actual 429/unavailable outcome; it does not trigger automatic replay.

Client `attach_source(generation, source_owner, **limits)` uses the merged local
attachment API in the reviewer venue. Its actual selected source owner verifies
already prepared objects and retains immutable refs; it does not fetch, clone,
checkout or build. The separate attachment is passed to explicit submission,
not published into metadata or accepted as remote authority.

Submit reloads the retained generation and executes the merged protected owner
pipeline against the original intent/source. Fresh native principal, revision,
discussion/ownership and caller policy checks remain in that executor. Durable
intent/in-flight/partial receipts use the same ReplayStore; there is no upload
staging store or second operation ledger. A lost transport result is unknown,
not a failed effect. Retain the original operation ID, inspect/reconcile through
the same owner, and never invent a new identity or infer success from equal values.
The existing opaque action body can contain a
[compact prepared comment](../../ai-attribution/docs/comment-preparation.md).
An explicit caller prepares it before final preview/approval and intent
admission; submission preserves those exact bytes rather than invoking a
decorator or minting IDs. Focused source fixtures qualify create/reply/owned-edit
serialization, retained intent after owner restart and confirmed retry without
another send. The optional receiver-owned allocation path below adds durable fresh-comment
preparation; this is not all-publisher or installed/provider adoption.
The existing SDK's explicit pre-dispatch credential/challenge failures retain
their known-no-send classification. Only its `outcome-unknown` transport error
marks a possibly dispatched submit; ordinary post-dispatch HTTP/response failures
remain conservative. No new stage, credential issuer or speculative reconciliation
is invented for an SDK-confirmed unattempted request.

The optional trusted `feedback_owner` embedding composes the
[captured-feedback owner](feedback-capture.md) into that same submit path.
It selects exact-generation interests outside request JSON and requires
explicitly adopted v5 storage. Results separately expose native execution
and `feedback` handled/pending/unavailable disposition. Confirmed retries
perform fresh protected readback without another send; exact vote-event
attribution and later echo coverage remain unavailable. This source seam
does not advertise feedback serving: the baseline conversation descriptor
still refuses v4/v5, the separate terminal receiver is read-only, and default
hosts supply no feedback owner.
Only a successful load/sync journal acquisition binds a retained generation
to its actual epoch/cursor in the existing workspace's bounded in-process
metadata. A bare cache lookup, equal current contents, missing binding after
restart or failed publication cannot manufacture a new feedback capture.
Already captured retries retain their original durable position and may
reconcile without that ephemeral acquisition metadata.

Oversized submit envelopes refuse locally before dispatch. Large immutable read
generations are framed, but this first wire does **not** upload arbitrarily large
intents/source manifests. An oversized result remains unavailable/unknown with
durable receipts retained; it is not truncated to claim success.

## Optional prepared review receiver

The existing trusted embedding may construct `WorkspaceOperations` with
`prepared_reviews=True` after the current lifecycle owner has adopted
[prepared review storage](review-owner-store.md#optional-prepared-intent-storage-extension).
This is not a request field, owner loader or permission grant. Default bindings
retain v1 and do not advertise the optional operation.

The actual executing receiver reads its current adopted intent versions through
the same owner. Only a selected, adopted receiver advertises this additive
`prepared_review_contract` in the existing workspace/v1 capability response:

```json
{
  "protocol": "agent-pull-requests.prepared-review/v1",
  "intent_versions": [2],
  "receipt_protocol": "agent-pull-requests.prepared-comment-receipt/v1"
}
```

Its existing `submit` request then accepts v2 prepared intent through the
[same protected native executor](protected-review-executor.md#prepared-native-intent-and-original-receipt-projection).
The `receipt` operation delegates to the original scoped receipt projection;
it performs no fresh native read or handled/echo/ACK update. A read-only binding
can advertise receipt support without advertising `submit`. Capability support
does not establish native write permission or current eligibility.

Native/Fleet clients and the CLI/tool façade require that exact supported
contract before selecting v2 or receipt. Missing/malformed support refuses
locally without a mutation or a legacy-send fallback; ordinary v1 metadata and
operations remain usable. The existing capability exchange is reused, not a
second transport or per-comment forge query. Older clients may ignore the
additive metadata and keep using v1; older receivers do not acquire new fields
or operations from a package-version assumption.

### Optional acquired comment observation

A current receiver with `prepared_reviews=True` and already adopted v2 intent
storage additionally advertises `observe-comment` and this exact
`comment_observation_contract`:

```json
{
  "protocol": "agent-pull-requests.comment-observation/v1",
  "result_protocol": "agent-pull-requests.prepared-comment-observation/v1",
  "generation_versions": [1],
  "effects": "none",
  "original_version_proof": false
}
```

The request selects `number`, original `operation_id`/`action_id` and an exact
retained `generation_id`. The receiver delegates to the existing-owner
[body-free library projection](review-owner-store.md#acquired-comment-observation).
It acquires no native evidence and does not require a protected writer.
The result preserves the original state/receipt and the selected generation's
actual observation interval; current content correlation is neither original
version proof nor feedback/handled progress.

Native/Fleet clients and CLI/tools select the operation only after the actual
receiver advertises both the prepared and observation contracts. Missing,
malformed or prior support refuses locally before sending this strict request.
The receiver rechecks its selected prepared mode and actual adopted storage
when invoked; cached capabilities cannot bypass withdrawal. The authenticated
Gateway keeps its existing binding, checkpoint and drain owners, including for
this hyphenated operation. Remote responses must match the selected original
operation/action, scope and generation and preserve the no-effects/no-proof
boundary. Old clients ignore the additive capability and keep their prior
operations; rollback refuses this read without rewriting state, discovering
a replacement by tags or falling back to another owner.

## Optional pre-approval selection receiver

After explicit prepared-intent and
[selection storage adoption](review-owner-store.md#optional-pre-approval-selection-retention),
the existing host may supply `selection_preparer` alongside `prepared_reviews=True`
independently of `write_context` or `write_context_factory`. A preparation-only
embedding advertises `allocate` and prepared receipt support, not `submit`;
the existing strict capability shapes and allocation payload are unchanged.
An approved preview cannot substitute for the missing protected writer.
This trusted
synchronous preparation resolver receives the
selected cached generation and `checkpoint` keyword; it resolves actual current
destination policy, AI profile and producer origin outside the replay lock,
then returns bounded pure synchronous composition with the existing
`comment_preparer(kind, body, scope=..., comment_id=..., write_id=..., previous=...)`
signature. That returned composer must do no native or external policy IO.
No callback loader, credential input, storage adoption or new owner is exposed
through request JSON.

Only that configured receiver with currently adopted storage advertises
`allocate` plus this additive `selection_contract`:

```json
{
  "protocol": "agent-pull-requests.preapproval-selection/v1",
  "input_versions": [1],
  "intent_versions": [2],
  "effects": "none",
  "approval_required": true,
  "owned_edits": false
}
```

Native/Fleet clients and the source CLI/tool façade negotiate this exact
contract before sending allocation. Default, missing, malformed or withdrawn
support refuses the new path without fallback; existing v1 submission, v2
submission/receipts and explicit-ID local preparation keep their prior semantics.
The existing Gateway binding, finite deadline, checkpoint, transaction,
capacity and request/drain owners remain unchanged.

Before first retention the receiver reuses the protected executor's pure
static selection checks, including complete generation evidence and the full
source/generation revision (both target-tip fields as well as head/common/iteration),
then commits fresh comment/reply GUIDs and exact final v2 bytes through
`ReviewSelectionStore.allocate` before returning a preview. Bodyless votes
receive no comment identity. Owned/legacy edits remain unsupported on this
baseline path unless the separate selected-edit extension below is enabled.
Selected reply targets must exist and not be deleted in that exact baseline;
invalid targets refuse before composition. Fleet validates returned previews
against the same normalized v1 original as the receiver, so accepted endpoint
aliases recover the original selection rather than rejecting retained output.
Recovery with the same original selection returns the first committed bytes,
including after restart, lost responses, later profile/origin changes or
execution admission; it does not resolve a new composer or require the old
cache entry again. Changed original input refuses. Storage contention keeps
the existing nonblocking refusal and is retried only through that same key.
Current owner/expiry checkpoints remain mandatory even on recovery.

The Gateway's trusted transport validator measures its complete serialized
preview response and the exact submission request (including binding and source
attachment) before the selection transaction commits. Either frame exceeding
the existing transport limit refuses without retaining an unrecoverable
preview. This pure validation performs no native or policy IO under the lock.
Recovery rechecks the current transport's limits without rewriting retained
bytes. Native-only larger selections are not implicitly transferable through
a smaller Fleet frame.
Post-dispatch failures or malformed allocation responses conservatively report
`outcome_unknown` for the possible selection commit, not a native effect.
Always reconcile the original selection key; no error authorizes a fresh key.

Preparation performs no native reads/writes, execution admission, feedback,
echo, ACK or DB object mapping. Preview approval remains external to this data
operation. Only explicit exact-intent submission through the existing protected
executor can advance admission and native effects; its current source,
principal, ownership and policy checks still apply. The original receipt
projection preserves those same allocated IDs after native execution.
These are finite source bindings and synthetic conformance tests, not installed
producer adoption, live service activation, all-provider proof or rollout.

Support is rechecked by the receiver's actual mode, adopted owner and normal
request lifetime at use. Cached discovery cannot authorize a withdrawn receiver.
Overlapping discovery cannot restore stale support after newer withdrawal.
After a dispatched failure or lost result, retain the original intent/IDs and
reconcile its original receipt through the same owner; no automatic retry,
replacement GUID or native-object/DB merge is introduced.

Without an explicit `selection_preparer`, the prepared-review receiver still
requires caller-prepared exact source/profile/identity before final approval;
prepared v2 submission alone cannot allocate or turn legacy tagged text into
structured preparation. Wire byte limits,
credentials, caller/native-principal separation and writer/drain custody are
unchanged. Source fixtures qualify the actual CLI -> client -> Gateway ->
protected synthetic native owner, not a live provider or installed host.

### Optional selected edits

A trusted embedding may additionally select `selection_owned_edits=True`,
default false, with its existing prepared mode and resolver. As with baseline
allocation, an optional writer is required for submission, not preparation.
The exact baseline capability above remains unchanged. Only a currently
supported opted-in receiver adds `owned_edit_selection_contract`:

```json
{
  "protocol": "agent-pull-requests.owned-edit-selection/v1",
  "selection_protocol": "agent-pull-requests.preapproval-selection/v1",
  "effects": "none",
  "approval_required": true
}
```

New native/Fleet clients require this exact additive declaration before
dispatching an edit allocation; missing/malformed support is unavailable,
not invalid selected content or a reason to probe/fall back. Fresh allocations
remain usable by prior clients that ignore the extra capability field.
Refreshing discovery clears withdrawn edit support. The receiver separately
rechecks its current opt-in before lookup, composition or retention, including
requests sent using stale cached support.

The existing request shape and `allocate` verb are unchanged. The receiver
supplies its actual validated generation/source to the
[same-owner selected-edit binding](review-owner-store.md#optional-pre-approval-selection-retention).
That binding owns stable logical identity, fresh write identity, selected
legacy classification and unknown/conflicting history refusal. Full frame
limits, original-key/cache-independent recovery and explicit exact approval/
submission/native receipts retain the semantics above. Withdrawing support
refuses new edit allocations without rewriting or deleting retained previews.
No normal read-only host configuration, installed consumer or other publisher
is enrolled by this source opt-in.

## Lifetime, compatibility and limits

Every cache/store mutation and result retains current caller/service checks.
Provider IO stays outside the storage locks. Cancellation closes request lifetime
but the existing gateway keeps its admitted worker through drain; no replacement
writer or untracked thread is created. Explicit v2 adoption still belongs to the
normal lifecycle owner after actual old-reader drain/transition, not startup or
an HTTP operation.
The existing read owner's optional request checkpoint is composed into its
remaining-work callback, retaining caller lifetime through physical-page reads
without changing legacy native callers or actual operation budgets.

Executing capability support does not prove backend availability, actual pinned
write-owner adoption or installed serving readiness. Missing owners return
unavailable. Actual protected-owner permission denial returns 403/access-refused,
not a temporary 503 outage; a denial after an admitted effect still preserves
unknown receipts and is never rollback. Existing watch clients keep their exact strict capability shape.
Corrupt cache/replay state reports owner-state unavailability rather than invalid
caller input; no repair is inferred.
Valid loads exceeding the current read owner's response capacity report response
unavailability without publishing a cache generation or blaming caller syntax.
Overlapping capability discovery uses a
local request revision so an older response cannot restore support after a newer
discovery failed. This revision is metadata ordering, not authority or custody.
Neither package equality nor a new source library upgrades a running service.
Expand the optional receiver first; migrate clients only after that running
receiver advertises workspace/v1 and the requested operation. No watch form is
deprecated or removed by this expansion. Disabling/rolling back the optional
workspace binding makes new clients report unsupported and leaves watch requests
unchanged; clients clear negotiated support on a failed rediscovery.
Any binary rollback must retain running v2-compatible store readers or use the
existing lifecycle owner's drain/transition procedure. Downgrading the durable
envelope, discarding unknown receipts or guessing legacy reader compatibility is
never a supported rollback. This source change performs none of those operations.

Source tests exercise real atomic cache/store, unchanged strict watch clients,
actual handler/native client/Fleet-shaped framing, protected synthetic transport,
concurrent CAS and cancellation/drain. No test uses a live backend or installer.
Actual ADO end-to-end review, prepared-source acquisition, running v2 readers,
production latency/frame suitability and authorized release/rollout remain
unqualified and outside this source-only slice.
