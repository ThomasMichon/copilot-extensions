# CLI-first workspace observer adopter

Parent: [PR Review Workspaces](README.md). This **agent-recommended source
contract** cleared review in #10819; its bounded source implementation was
separately admitted. Neither this document nor its source publication authorizes configuration adoption,
credentials, installation, serving changes, live PR effects or rollout.

## Intent and existing owners

The operator's direction is CLI tools for observing PRs. Reuse the existing
`agent-pull-requests workspace` frontdoor rather than making MCP a prerequisite
or introducing another observation engine.

The [native read-only workspace host](../durable-pr-watch-delegation/native-workspace-host-slice.md)
already has reviewed source implementation and foreground/restart qualification
in #10609/#10631. Its #6007 owner retains the native exporter, credential context,
Gateway route, ReplayStore/cache/journal writer, storage and service lifecycle.
This proposal does not rebuild or take over that host.

The [workspace facade](tool-surface-slice.md) and prepared-source SDK (#9928)
already supply client operations, immutable generations and lifetime contracts.
This slice closes part of the existing PR vision's
`cli-through-one-service-contract` and `reviewer-owned-load-sync-and-submit`
direction. It does not extend standing intent or retire existing PR commands.

## Actual client gap

The ordinary workspace CLI has no operational owner binding. It calls the
existing facade through `main(..., workspace_host=...)`; without a supplied
host, non-discovery commands report unavailable. A Python embedding can supply
a real host, but source declarations alone do not make the ordinary installed
command an adopted observer.

The one-shot CLI deliberately suppresses owned asynchronous source execution:
its per-invocation event loop cannot retain that source lifetime through later
reads and drain. Do not solve client adoption by weakening this refusal,
capturing a watch runtime, loading an arbitrary owner module or manufacturing
protected-owner provenance.

## Bounded outcome

Plan an optional, explicitly selected **read-only service-client profile** for
the existing workspace command. A qualified profile constructs the existing
ServiceCaller, WorkspaceClient and one-shot WorkspaceTools view. The existing
service still owns backend reads, normalization, cache publication and events.

First-slice commands are `capabilities`, `load`, `sync`, `materialize` and
capability-selected `events`. This is observation, not a complete reviewer
workflow. Submission, effect inspection, exact source allocation/acquisition,
async source adoption and live reviewer qualification remain with their owners.

MCP may later adapt this same domain surface. No MCP adapter, listener,
extension, agent registration or extra dependency is selected here.

## Proposed profile and CLI contract

Proposed syntax, **not an installed command contract**:

```text
agent-pull-requests workspace --client-profile NAME capabilities
agent-pull-requests workspace --client-profile NAME load --request request.json
agent-pull-requests workspace --client-profile NAME sync --request -
agent-pull-requests workspace --client-profile NAME materialize --request request.json
agent-pull-requests workspace --client-profile NAME events --request request.json
```

- Without the selector, preserve the current unbound CLI and all existing
  trusted Python embeddings. Supplying both an embedding and a profile is
  ambiguous and refuses; the selector cannot replace another current owner.
- `NAME` selects only a profile already adopted through the operator's
  configuration owner. It is not a path, executable, module, environment
  factory or permission grant. Workspace request JSON cannot select a profile,
  account, endpoint, credentials, namespace or source root.
- Add one versioned client-connection record, provisionally
  `workspace-client-profiles.json`, within the caller's existing validated
  installation/configuration namespace. Resolve that namespace through its
  current owner, including legacy/cell separation; never guess an ambient
  runtime root or create configuration during a read.
- Preserve standalone application use without Copilot or a marketplace.
  Installation-cell checks apply only where that owner selected a cell;
  otherwise use the actual application's configuration owner, not a fabricated
  plugin context or automatic bootstrap.
- The record holds connection inputs and references: the exact selected
  Gateway/service route, external service-credential references and the
  existing validated ReplayBinding/workspace scope. Reuse their validators.
  There are no inline secrets, arbitrary commands or owner callbacks.
- This is client connection data, not a second PR-policy/preferences store.
  Worktrees retains registered target/provider/account identity and PR policy.
  Conflicting or unresolved target/account bindings refuse rather than
  selecting ambient authentication or relabelling a principal.
- Configuration supplies expectations, not authority. The existing service
  credential owner and authenticated Gateway/PR host must establish current
  caller, target and operation permission. A profile label, version, hash or
  receipt cannot create that context. Actual configuration adoption remains
  outside this source proposal.
- Missing, foreign, malformed or unsupported profiles fail before credential
  reads or service contact. Keep profile paths, credential material and private
  configuration values out of operation responses and errors.
- The profile's v1 exposure is fixed read-only. Reject `submit`, `inspect`,
  `source` and `source-read` before dispatch, even if another server/embedding
  supports them. Do not introduce a writable-profile boolean or advertise
  unavailable operations as adopted.

## Discovery, observations and lifetime

Negotiate the exact executing workspace contract through the existing client.
Package versions, source support and configuration presence are insufficient.
Missing workspace support is unavailable, not a native/GitHub/legacy-watch
fallback. Optional events require the existing conversation contract; older
hosts retain supported load/sync/materialization without fabricated cursors.

Preserve existing command payloads, immutable generation IDs, scoped CAS,
section intervals, partial evidence and framing. Separate CLI response delivery
from owner/cache/effect completion. Interrupted reads or uncertain cache/journal
publication retain the existing owner's reconciliation semantics; do not add
automatic replay, compensation, a local receipt ledger or reconstructed clocks.
An event cursor is not an ACK, handled-review state or mutation authority.

Use the canonical finite CLI/process owner before request, profile and credential IO.
The proposed observer invocation budget is 60 seconds in total, including
input, configuration, authentication, discovery, pages/chunks, decoding and output;
it is not renewed per request. Preserve the existing ServiceCaller limits and
the SDK's separate 180/600 qualification bounds. Carry one absolute deadline
through the worker/client rather than starting nested full budgets.

Reuse existing request, response and generation limits, including legitimate
JSON encoding expansion. Do not truncate a result to manufacture success or
invent a new evidence quota. Deadline or cleanup failure retains the original
physical owner and uncertain disposition; it is not a successful drain.

The adopter adds no service, issuer, scheduler, subscription registrar,
watcher, separate thread pool or resource allocator. Existing client IO retains
its current physical owner; it is not promoted into async source settlement.
The adopter neither captures a serving writer nor changes storage adoption.
Each invocation reads its current adopted profile; it does not create a
hot-reload or background credential owner.

## Implementation plan after source admission

- [x] Review the profile ownership, fixed read-only exposure and finite
  invocation contract before changing the CLI or its source dependencies.
- [x] Add the strict optional selector and namespace-safe profile reader under
  the existing PR client/configuration owner; no profile writer or adoption
  command is part of this slice.
- [x] Construct only the existing authenticated client/facade chain and retain
  current request parsing, capability validation and one-shot source refusal.
- [x] Wire finite execution and response/error handling through the current
  process/transport owners without a new lifetime or retry engine.
- [ ] Qualify actual CLI invocation and installed dependency composition in
  contained fixtures, then run the normal source/review/typing/CI gates.
- [ ] Record source release separately from the operator's later named
  profile adoption and real-provider observation qualification.

## Validation plan

- [x] Current unbound commands and Python embeddings remain compatible;
  missing/foreign profiles and unknown request/configuration fields refuse
  before credential or provider access.
- [x] A real CLI process uses the existing authenticated Gateway/PR host and
  native read-owner seam with synthetic credentials/provider output. Fresh CLI
  invocations share the service's generation/cache, not another local store.
- [x] Load, no-change sync, dialogue-only change, target/head movement, partial
  pages and scoped materialization preserve actual IDs, intervals and gaps.
- [x] Wrong caller/target/account, revocation and unsupported contracts refuse
  without endpoint/account fallback or credential export.
- [x] Optional events preserve epoch/sequence, bounded pages and unavailable
  states; reading them never acknowledges or handles a review.
- [x] Cancellation/deadline and failed physical cleanup do not report drained,
  release shared owners or hide an uncertain cache/journal commit.
- [x] No observation-profile request can reach submission, source fetch,
  mutation, deployment, a new service or an owner/module loader.
- [x] First-use/source packaging controls qualify the actual command and its
  dependencies without treating fixture configuration as live adoption.

The finite source exit is an assessed review, required green CI and honest
compatibility evidence for this optional observer client. The parent effort's
real adopter, source, submission, concurrency and operational gates remain open.

## Local source qualification

The optional selector now uses the existing application configuration owner and
the current legacy/cell admission APIs. It composes ServiceCaller,
WorkspaceClient and one-shot WorkspaceTools without adopting a backend.
Borrowed request/output streams remain inside the canonical finite process
owner; the single 60-second observer deadline includes physical input and
output. Cleanup failures retain the original retryable owner exception.

The 30 current observer unit controls and six native scenarios are covered
across the recorded focused packets. Native cases run the actual CLI against
the existing TLS Gateway and native read-owner seam with synthetic credentials
and finite provider output. Compatibility, scope/refusal, immutable cache,
partial observations and events retain their original contracts. Fresh
first-party wheel composition exercises the installed console command without
editable source finders; it reuses prepared external dependencies and does not
qualify fresh third-party acquisition or a real host installer.

Focused borrowed-stream controls, the enforced Linux/Windows type projects,
dependency-age, install-contract, documentation and touched Python lint checks
pass. The PR plugin and Procutil remain in their existing pending type
inventory, not newly certified strict projects. These are local source and
contained-consumer results, not review acceptance, required CI, release,
profile adoption or live-provider qualification. The two remaining Plan items
and the parent's eight Plan/eleven Validation gates remain open.
