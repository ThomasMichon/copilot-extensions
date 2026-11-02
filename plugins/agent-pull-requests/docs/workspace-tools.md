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
under its existing resource lifetime. That adapter uses tracked shielded blocking
work and original owner checkpoints; it is not another resource/service owner.
An async host without that binding still reports source/source-read unsupported.
The host must stop admission and await terminal `drain(timeout=...)` before
releasing the member. Cancellation withdraws the operation but does not release
its worker. Drain timeout retains pending work and custody; `status()` reports
that distinction. Never infer cleanup from a caller timeout or response buffer.
Original process/Job cleanup errors remain in `cleanup_errors`; drain re-raises
them and cannot report drained. Forced cancellation of the retained worker itself
also latches unconfirmed completion rather than treating a canceled task as
terminal native cleanup. The host must preserve the existing cleanup owner's
handles and cannot release the member from either outcome.

The public contract is `agent-pull-requests.workspace-tools/v1`. Discovery reports
the actual supplied client's supported operations and source-read descriptor.
Unsupported/missing bindings do not choose an ambient backend or command.
All original CLI/watch interfaces and defaults remain unchanged.
Overlapping service tool calls share negotiation through the existing client's
`ensure_capabilities()`; explicit rediscovery remains separate and can withdraw
cached support. This orders metadata, not caller authority or effect admission.
The existing host lifetime retains a bounded 16 MiB total verified-generation
read cache. Repeated source reads recheck the scoped descriptor through the
current client/owner, without transferring all chunks again. Changed/missing
descriptors refuse; cached bytes never substitute for current authority.

## Existing CLI frontdoor

```text
agent-pull-requests workspace capabilities
agent-pull-requests workspace load --request request.json
agent-pull-requests workspace sync --request -
agent-pull-requests workspace materialize --request request.json
agent-pull-requests workspace source --request request.json
agent-pull-requests workspace source-prepare --request request.json
agent-pull-requests workspace source-read --request request.json
agent-pull-requests workspace submit --request request.json
agent-pull-requests workspace inspect --request request.json
```

An actual Python embedding supplies `workspace_host=host` to the existing
`agent_pull_requests.__main__.main(argv, workspace_host=...)` before executing
input. An ordinary unbound command only reports source contract support and
owner-unavailable for operations. It does not capture a watch invocation,
bootstrap/select a runtime or load an owner from a file/module/environment.
There is no `--owner`, credential/root override or receipt-based permission.

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
| source-prepare | source fields; only for a separately bound acquisition owner |
| source-read | source fields plus source_attachment, path, side |
| submit | number, intent, source_attachment, budget_seconds |
| inspect | number, operation_id |

Unknown fields cannot supply workspace paths, credentials or source authority.
Load/sync/materialize/submit/inspect retain the
[existing operation/client contracts](workspace-integration.md): normalized
scope, immutable generation/CAS/framing, original observation intervals and
durable intent/in-flight/native-ID/unknown outcomes. No policy/rubric, unconditional
CI/merge-readiness requirement, compensation or automatic replay is introduced.

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

**Exact acquisition is separately bound, never inferred.** An existing resource
owner may construct LocalReviewSource with its `acquisition` callable
(`git_review_acquisition.fetch_exact`) and selected configured `remote`.
Only then does capability discovery expose `source-prepare`. Requests cannot
choose paths/remotes/accounts or adopt a member. The normal pipeline must first
reject occupied, foreign, reference, guarded, unbound and unknown resources.
There is no new resource selector or installed preparation command.

The existing Git owner fetches only the immutable full head/common IDs at depth
one, with no configured refspec expansion, tags, submodules, FETCH_HEAD/ref
updates, checkout, target merge/rebase or history deepening. Before credential
acquisition it verifies dedicated already shallow storage with no shared
worktrees or alternates. Original owner/checkpoint/auth/transport and bounded
process cleanup remain authoritative. Full/shared storage is refused rather
than modified. The existing inspector then verifies all actual native path,
tree/blob/content mappings before its immutable retention transaction. Missing
or partial objects/ref completion never become a ready attachment.

The strict prepared-source/read descriptors remain unchanged; additive
acquisition/execution descriptors signal the optional binding. These source APIs
do not prove any real resource/credential factory or installed-host adoption.

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
