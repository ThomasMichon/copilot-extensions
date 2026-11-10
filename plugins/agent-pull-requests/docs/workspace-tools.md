# Existing-owner workspace tools and CLI

The source façade `agent_pull_requests.workspace_tools.WorkspaceTools` invokes
the already implemented workspace clients; it is not another PR engine or MCP
service. An existing authenticated host supplies its current native/service
client and optional owner-selected `LocalReviewSource`. Python objects, tool
descriptors, source hashes and request files do not grant authority.

`await host.definitions()` returns MCP-shaped tool descriptors; the existing host
may register them through its own supported mechanism. This function does not
register tools, launch an agent/extension, start a service or claim installed
adoption. `await dispatch_tool(host, name, {"request": payload})` invokes the same
source façade. Native operations remain synchronous under their existing caller
lifetime; service operations use their existing async client and handler/drain.
The façade creates no thread, process, watcher, ledger or transport.
Synchronous LocalReviewSource operations are available only through a native
synchronous client. An async service host may bind Worktrees' `AsyncReviewSource`
under its existing resource lifetime. Construct it with
`AsyncReviewSource(selected_source, executor=existing_owned_thread_executor)`.
The existing host supplies and owns that executor; the adapter never creates or
shuts down a pool. Missing binding refuses rather than selecting another owner.
Actual native executor futures, not cancellable loop bridges, prove queued work
was cancelled before launch. The adapter retains original owner checkpoints;
it is not another resource/service owner.
Only one async adapter may bind a selected LocalReviewSource lifetime; a second
binding refuses instead of creating independent admission/drain state.
Owned async source selects the stock executing native/service preparation
binding, not a version marker or generic delegate's declaration. The actual
exchange, discovery and materialization methods are checked before source work
and rechecked before subsequent client IO. Overridden preparation methods and
configured service read-admission callbacks remain metadata-only; the source
lane cannot infer native settlement for their hidden producers.
The client accepts the existing operation's checkpoint for
capability/cache reads and materialization, checking it after pacing and before
each request. Version-1 peers retain metadata compatibility but do not advertise
async source tools. Native source/transport frames and their IO limits are unchanged.
Binding also refuses while synchronous source work is active. Once bound, public
synchronous inspection/read refuses; only the admitted worker's explicit internal
entry points can access that selected source. Native unbound behavior is unchanged.
One physical selected-source operation is admitted at a time; a busy source
refuses before copying another request, rather than maintaining an internal
queue. This is source execution contention, not a delegate permission or quota.
The original absolute deadline starts at admission, including executor wait;
expired queued work refuses before entering Git and never receives a new budget.
For async source tools this same reservation is acquired before capability/client
materialization IO. Client requests stay on their original loop; generation
assembly, validation, serialization and copies use the supplied executor under
that reservation. Cancellation retains both actual client preparation and native
CPU/Git work until terminal drain. Busy sources refuse before those client reads.
The caller's wait also ends at that deadline while native completion remains
retained for drain. Blocking owner checkpoints run inside the worker, not on the
event loop.
The existing ServiceCaller request has an internal native-completion scope.
Source qualification binds its actual selected ServiceCredentials reader (or
its existing synchronous machine-proof path). An opaque token callback,
decorator or unbound delegate remains metadata-only for this new source lane.
The released ServiceCaller constructor, callback and ordinary request APIs are
unchanged. Changing the configured credential callback withdraws source support.
Cooperating native producers register the actual executor future before
awaiting a loop wrapper; cancellation joins that producer before request
settlement. Source requests use the already supplied source executor for the
existing credential-reference reader. No executor, credential issuer or
transport is inferred from a declaration.
Source DNS work uses the same request's registered native execution/drain;
ordinary metadata keeps its existing resolver.
Source-owner permission denials during native credential work preserve their
original exception rather than becoming an authentication failure.
Active nested client results transfer into tracked execution before their
parents settle. Withdrawn results retain unfinished producer custody instead.
New asynchronous inspection accepts immutable generation bytes from the existing
verified native/service materialization and read cache. Validation pins the
requested generation identity before retaining those canonical bytes; inspection
decodes and validates them in its owned worker before any Git read. It never
traverses a caller's original mutable change list. Unbound raw async inspection
refuses with `async_inspection_requires_immutable_generation` before work admission.
The earlier metadata reservation permits owned preparation, not mutable inspection.
No new evidence quota or on-loop atomic-copy allowance is introduced; existing
Git output/path limits retain their original meanings.
Released unbound LocalReviewSource synchronous raw-dictionary inspection and
native prepared-source tools remain unchanged. No request flag selects a legacy
escape from an already-bound async lifetime.
Request snapshotting, generation selection/validation and attachment response
preparation run inside that retained worker. Content-addressed selection identity
is pinned before offload; a changed generation cannot replace the selected one.
Retained reads likewise pin the already validated manifest ID, scope and revision
before the executor queue and compare the actual worker snapshot before reading.
An async host without that binding still reports source/source-read unsupported.
The host must stop admission and await terminal `drain(timeout=...)` before
releasing the member. Cancellation withdraws the operation but does not release
its worker. Drain timeout retains pending work and custody; `status()` reports
that distinction. Never infer cleanup from a caller timeout or response buffer.
Original process/Job cleanup errors remain in `cleanup_errors`; drain re-raises
them and cannot report drained. Native worker completion is recorded independently
of cancellable async bridges, including during normal event-loop executor
shutdown. A canceled async wrapper cannot discard a later process/Job cleanup
error. The host must preserve the existing cleanup owner's handles and cannot
release the member from either outcome.
If an attachment's caller budget expires, it returns an incomplete envelope with
`effects: local_refs_possible` and `native_settlement: unconfirmed`. This does not
claim native settlement or permit release: original owner denials and cleanup
failures still propagate, and the same owner must drain actual retained work.

The public contract is `agent-pull-requests.workspace-tools/v1`. Discovery reports
the actual supplied client's supported operations and source-read descriptor.
Unsupported/missing bindings do not choose an ambient backend or command.
All original CLI/watch interfaces and defaults remain unchanged.
The optional [tracked comment/review source embedding](tracked-publication.md)
composes those existing owners without enrolling ordinary Worktrees commands.
Overlapping service tool calls share negotiation through the existing client's
`ensure_capabilities()`; explicit rediscovery remains separate and can withdraw
cached support. This orders metadata, not caller authority or effect admission.
Custom native `ensure_capabilities` callbacks remain available for ordinary
metadata discovery but cannot qualify the owned async source lane. They are
rejected before source reservation or callback execution.
Source qualification inspects discovery and preparation bindings without
executing descriptor getters. Stock methods and their bound instance copies
remain supported; custom descriptors remain available to explicit metadata calls.
The existing host lifetime retains a bounded 16 MiB total verified-generation
read cache. Repeated source reads recheck the scoped descriptor through the
current client/owner, without transferring all chunks again. Changed/missing
descriptors refuse; cached bytes never substitute for current authority.
One-shot views suppress only the async source binding and preserve that same
verified-generation cache.

## Existing CLI frontdoor

```text
agent-pull-requests workspace capabilities
agent-pull-requests workspace load --request request.json
agent-pull-requests workspace sync --request -
agent-pull-requests workspace materialize --request request.json
agent-pull-requests workspace source --request request.json
agent-pull-requests workspace source-read --request request.json
agent-pull-requests workspace prepare --request request.json
agent-pull-requests workspace allocate --request request.json
agent-pull-requests workspace submit --request request.json
agent-pull-requests workspace inspect --request request.json
agent-pull-requests workspace receipt --request request.json
agent-pull-requests workspace retained-comment --request request.json
agent-pull-requests workspace events --request request.json
```

An actual Python embedding supplies `workspace_host=host` to the existing
`agent_pull_requests.__main__.main(argv, workspace_host=...)` before executing
input. An ordinary unbound command only reports source contract support and
owner-unavailable for operations. It does not capture a watch invocation,
bootstrap/select a runtime or load an owner from a file/module/environment.
There is no `--owner`, credential/root override or receipt-based permission.
The one-shot CLI does not adopt async source owners: its per-invocation loop
cannot own their lifetime through later reads and drain. It omits source tools
for that binding and refuses source/source-read honestly; metadata
operations remain available through their existing client. Use the actual async
host/tool interface on its original loop, with terminal drain, for async source.

Each non-discovery command takes one bounded finite JSON request, with duplicate
fields rejected. CLI exit 0 means a JSON operation response was delivered, **not**
that a selected review effect completed. Inspect action-specific receipts and
entered-write counts. Errors do not infer effect completion: unavailable owner
is exit 3, original owner refusal exit 4, and input/result failure exit 1.
Original host cancellation/authority and retained unknown effects remain separate.

| Command | Request fields |
|---|---|
| capabilities | none |
| load | number, expected_current, budget_seconds |
| sync | load fields plus previous_id |
| materialize | number, generation_id |
| source | number, generation_id, budget_seconds |
| source-read | source fields plus source_attachment, path, side |
| prepare (optional local binding) | number, intent (v1 draft), identities |
| allocate (optional receiver binding) | number, intent (v1 draft), source_attachment, budget_seconds |
| submit | number, intent, source_attachment, budget_seconds |
| inspect | number, operation_id |
| receipt (optional prepared contract) | number, operation_id, action_id |
| observe-comment (optional observation contract) | number, operation_id, action_id, generation_id |
| retained-comment (optional mapping contract) | number, operation_id, action_id |
| events (optional) | number, after (null or epoch/sequence), limit (1-16) |

`allocate` selects only the executing receiver's optional
[pre-approval selection contract](workspace-integration.md#optional-pre-approval-selection-receiver).
It takes no caller identities, profile, source-origin or approval flag.
The original source attachment pins the reviewed source and lets the actual
Gateway validate both final preview and subsequent submission frame sizes
before committing; an oversized composed selection refuses without retention.
The response's `result.intent` is the durably committed final v2 preview;
`approval_required: true` and `effects: none` describe preparation, not execution
permission. Explicitly submit that exact intent with the existing protected
source attachment only after actual approval. Recover a lost preview with the
same original v1 intent and operation ID, never a new operation or new GUIDs.
The existing local `prepare` command retains its explicit-ID, nonpersistent
semantics; allocation needs no local `comment_preparer` binding.
Selected owned/legacy edit allocation additionally requires the receiver's
[optional edit capability](workspace-integration.md#optional-selected-edits).
New clients refuse missing or malformed support before dispatch; the baseline
fresh-allocation capability and request shape remain unchanged.

Unknown fields cannot supply workspace paths, credentials or source authority.
Load/sync/materialize/submit/inspect retain the
[existing operation/client contracts](workspace-integration.md): normalized
scope, immutable generation/CAS/framing, original observation intervals and
durable intent/in-flight/native-ID/unknown outcomes. No policy/rubric, unconditional
CI/merge-readiness requirement, compensation or automatic replay is introduced.

The [optional prepared review receiver](workspace-integration.md#optional-prepared-review-receiver)
enables v2 `submit` and original `receipt` reads only when the actual selected
owner advertises its supported contract. Default/prior bindings keep v1;
missing or malformed support refuses before sending v2, not through a legacy
fallback. Submission never prepares text, allocates GUIDs, adopts storage or
turns receipt correlation into fresh native readback or handled/echo progress.

`observe-comment` and `pr_workspace_observe_comment` use the separately
[negotiated observation contract](workspace-integration.md#optional-acquired-comment-observation).
They join one original action to an exact cached generation without acquisition
or a writer. Preserve the result's original state/receipt and observation age;
an unavailable/conflicting result is not a request to discover equal text,
resend a comment or advance feedback. Prior/withdrawn support refuses without
state downgrade or fallback.

### Retained acquired-comment reader

`retained-comment` and `pr_workspace_retained_comment` read body-free evidence
already retained by the [explicit acquisition mapping owner](review-owner-store.md#optional-retained-acquired-comment-metadata).
The executing workspace must explicitly select `comment_mappings=True` with
prepared-review and conversation support and currently adopted mapping storage.
Discovery adds `retained-comment` and `comment_mapping_contract` only for that
selection. Its contract is `agent-pull-requests.retained-comment-reader/v1`,
result protocol `agent-pull-requests.retained-comment-observation/v1`, storage
extension `review_comment_mapping_version:1`, acquisition
`workspace-projection/v1`, and `effects: none`.

Native/service clients and the tool façade require the optional declaration
before dispatch. Required contract values are validated; additive metadata is
ignored. The receiver rechecks its actual selection and storage, even
for direct or stale clients. Prior, missing, malformed, withdrawn or rolled-back
support disables only this optional reader; baseline commands retain their
existing schemas and behavior. No baseline field is repurposed, no command
implicitly adopts/downgrades state, and no prior form is retired in this release.
Normal unbound hosting remains unchanged.

The scoped result retains the original action identity/receipt and a nullable
`mapping` with its acquisition generation and original discussion interval.
It needs neither cached bodies, a writer nor fresh provider access. A null slot
is missing retained evidence, not proof of no native effect. Later acquisitions
may replace the slot without rewriting the original confirmed receipt.
`effects: none`, `fresh_native_read: false`, `original_version_proven: false`
and `feedback_progress: false` remain explicit. Reading never searches tags,
resends comments, reconciles unknown writes or advances handled/ACK/echo state.
The source implementation and synthetic native/CLI/Gateway controls do not
enroll a normal installed host or qualify live provider behavior.

An explicitly adopted terminal read-only host may instead offer the separate
[`terminal-comment-mapping-reader/v1`](terminal-retention.md#optional-terminal-mapping-receiver)
contract. The same CLI/tool command then returns a declared active/compact
representation, minimal original prepared identity and `mapping_supported`,
without inventing a retired source manifest. Unsupported historical retention
and a supported null slot remain distinct. This does not append an operation
to terminal-read/v1 or reuse retained-comment-reader/v1's result guarantees;
the executing optional declaration, not the command spelling, selects the
result. Prior hosts keep their baseline, and neither form falls back to native
searches or writes.

The optional [conversation event reader](conversation-journal.md) needs explicit
owner-v3 storage and serving adoption. It is read-only and capability-selected;
unbound/prior hosts remain usable for their existing operations. A returned
cursor is not an ACK or a handled-review baseline.

## Optional local comment preparation

An existing host may supply `WorkspaceTools(client, comment_preparer=prepare)`.
This callable composes the actual
[AI-profile preparer](../../ai-attribution/docs/comment-preparation.md) and
Worktrees source owner before final preview and approval. It receives
`prepare(kind, body, scope=scope, comment_id=..., write_id=..., previous=...)`.
The host validates the destination against its admitted binding and supplies
the actual audience policy, effective profile and source snapshot. It maps an
owned edit to the shared preparer's `comment` activity. The JSON request cannot
select policy, a profile, source identity, a Python module or an owner.
The callable is a pure preparation binding, not permission to perform native IO.

The local `prepare` command/tool is advertised only with that binding and the
selected receiver's prepared-review support. It is not a new Gateway route.
Its `intent` is a strict v1 draft. `identities` contains exactly one entry per
body-bearing action, keyed by action ID, each with `comment_id`, `write_id`
and `previous` (the complete retained shared preparation, or `null`).
The caller supplies public version-4 GUIDs; this source seam does not allocate
or persist them. Invalid, missing, duplicate or additional selection fields
refuse rather than preparing only part of the selection.

The output contains the strict v2 `intent`, `effects: none` and
`approval_required: true`. It retains operation, scope, candidate, source and
native edit targets, replaces only selected bodies through the actual preparer
and binds their exact output. Bodyless votes remain bodyless. The tool does not
submit, write the durable owner, fetch predecessor comments or infer approval.
Retain this exact returned intent under the caller's existing selected-operation
custody, preview and approve it, then explicitly submit it through the same
receiver. Default/unbound hosts and existing v1 submission remain unchanged.

For another preparation of the same selected write, supply its retained shared
`previous` record; changed body, profile or source refuses rather than rewriting
that pending write. An intentional edit retains logical identity and supplies a
fresh write GUID. Without a retained record, this pure seam cannot discover a
previous preparation or enforce cross-call uniqueness. Confirmed retries use
the durable submitted intent, not another preparation call. Durable allocation,
automatic installed-host adoption and other publisher surfaces remain open.

## Exact local source adapter

`source` materializes the selected generation, then calls the existing
`attach_source` through the normal owner's supplied LocalReviewSource. It returns
a separate attachment envelope; neither metadata nor its observation clocks are
rewritten. The source owner must already be selected by the actual registered
workspace/resource pipeline, excluding reference, guarded, foreign and unbound
workspaces as that owner requires. The façade cannot adopt one from request data.

`source-read` validates that separate envelope against the exact selected
generation, then calls `LocalReviewSource.read_retained(manifest, path, side,
budget=..., max_bytes=...)`. Repository-rooted path and `before`/`after` choose only
one path/side already present in the verified manifest. The owner checks scope,
workspace, exact head/common refs, tree mode/object mapping and actual Git blob
hash before returning bounded UTF-8 text or explicit base64 binary/symlink data.
Both outer attachment and nested manifest must match the generation's native
scope/revision; relabeling a stale retained range does not make it current.
Gitlinks and absent/container sides are explicit and not expanded. Mutable work
files, replacement refs, hooks, lazy network fetch and link following are not used.
The existing checkpoint/process owner enforces work/byte and cleanup boundaries.
Bounded Git timeout/capture failures become value-free incomplete-source errors;
they do not print a private command traceback or prove cleanup/effect completion.

The new retained-read descriptor is separate; the prior strict source-attachment
contract remains unchanged. Source Git environment isolation was moved into a
lightweight module with the original Git helper aliases preserved, avoiding an
unrelated registry/credential import in this prepared-object reader.

**Prepared-only implementation scope; acquisition deferred pending resource-owner
capacity admission.** Exact acquisition is unsupported and unadvertised. There
is no acquisition callable, constructor binding or `source-prepare` command in
this implementation. Original general-purpose Git fetch behavior is unchanged;
it is not substituted for exact review acquisition. Even depth-one fetch can
write oversized unchanged blobs before post-transfer inspection limits apply.
The existing Git/resource owner must establish actual pre-write transfer/storage
bounds and reviewed admission before a future implementation under #9535.

The strict prepared-source/read descriptors remain unchanged; the additive
execution descriptor signals only owned async work over already prepared
objects. These source APIs do not prove any real resource/credential factory or
installed-host adoption. A missing object remains unavailable; no lazy fetch or
fallback acquisition is attempted.

## Source proof and remaining gates

Contained consumers exercise the actual CLI/module, tool descriptors/dispatch,
atomic cache/store and source-local Git objects with synthetic protected backend
contexts. They cover load/sync deltas, partial evidence, retained text/binary/
deletion, unattempted-versus-unknown responses, receipt preservation and refusal.
They prove source contracts, not live auth or host adoption.

Actual tool registration, source allocation/preparation, owner/v2 reader
qualification, eligible live ADO end-to-end review and authorized serving/release/
rollout remain unresolved. No installation, backend retry or legacy repair is
part of this source surface.
