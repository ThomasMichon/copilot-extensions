# Existing-owner workspace tool surface

Parent: [PR Review Workspaces](README.md). This bounded source continuation exposes
the already implemented workspace operations rather than adding another PR
engine, forge client, store, MCP service or lifecycle owner. Review this public
CLI/tool and retained-source-read expansion before realization.

## Actual source owners and remaining acquisition gap

`WorkspaceOperations`, `NativeWorkspaceClient` and `WorkspaceClient` already
perform load/sync, materialization and selected submit/inspect. `WatchService` and
`WatchGateway` remain the serving owner. The existing protected read/write
factories and ReplayStore retain current principal, input, policy, expiry,
in-flight and receipt authority. A tool façade only calls these operations.

`LocalReviewSource` already inspects prepared exact objects and retains verified
head/common refs. It currently returns manifests, not a tool-readable blob
operation. Add bounded reading of one selected manifest path/side to this same
source owner and caller-owned Git process utility.

Source investigation found no selected exact-shallow review preparation factory.
`worktree_creation._prepare_worktree_source` prepares the default-branch base,
and `git_ops.fetch` fetches the whole configured remote without a reviewed
exact-object/depth parameter. They are not interchangeable with a reviewed
head/common snapshot. Do not wrap them as exact source acquisition or create a
stray checkout. The tool's source adapter consumes a repository already selected
and prepared by the actual registered workspace/resource owner; acquisition
remains explicitly unsupported until that owner supplies an admitted narrow API.

The normal owner resolves worktree capability, delegation/reference restrictions,
parent/resource custody and current scope before supplying a source resolver.
Paths, hashes, type names, JSON labels and callbacks do not authenticate that
selection. A missing/unbound/foreign/guarded source remains unavailable; the
façade does not adopt it or allocate/fetch anything.

## Public source surface and discovery

Extend the existing `agent-pull-requests` parser with `workspace`; no new launcher
or CLI owner. A pure Python tool façade exports bounded tool descriptors and
dispatch for an already authenticated existing tool host. It starts no MCP
listener, subprocess bridge, extension or agent and installs no tools.
The extension SDK guide was consulted; no SDK-dependent extension/agent code is
selected. Actual host registration/adoption remains outside source declaration.

The tool contract is `agent-pull-requests.workspace-tools/v1`, independently of
package versions. Source CLI uses strict subcommands, one bounded JSON request
file or stdin and one JSON response:

`agent-pull-requests workspace <capabilities|load|sync|materialize|source|source-read|submit|inspect> [--request <file-or->]`

Normal Python embedding supplies the actual bound façade to the existing entry
point before input execution. There is no `--owner`, Python-module loader,
environment factory, credential flag, root override or receipt-based grant.
An ordinary unbound CLI can report source contract support but emits
owner-unavailable for operations; it never captures a watch runtime, bootstraps,
chooses an ambient command/account or pretends an installed tool was adopted.

Tool descriptors advertise only operations supported by the actual supplied
workspace client and optional source resolver. Discover running service support
through the already implemented client handshake; missing support is unsupported,
not a legacy watch/provider fallback. A declaration is not permission/readiness.
Use ordinary existing tool-host auth/input gates before dispatch.

## Operation semantics

- `load`/`sync`/`inspect`/`submit` pass the existing immutable payload through the
  merged client/operation owner without interpreting a verdict or adding policy.
- `materialize` uses the existing scoped immutable generation, chunk and content
  validators. Original observation intervals, partial sections and CAS outcomes
  remain unchanged.
- `source` materializes that exact generation and invokes the existing source
  resolver plus `attach_source`, retaining a separate generation-bound envelope.
  No remote acquisition, checkout, build or metadata-clock rewrite occurs.
- `source-read` takes exact generation/source envelope, selected path/side and
  actual finite byte/work limits. The same LocalReviewSource validates its scope,
  workspace/range/ref and actual tree/blob mapping before reading a retained
  object. Return bounded UTF-8 text or explicit binary/symlink/gitlink/absent
  representation; never follow a link or fall back to mutable working files.

The new read capability has a separate descriptor; do not change the existing
strict local-source contract shape and break old attachment clients. Reuse the
existing Git/process/checkpoint owner, no second Git/auth/fetch implementation.
Exact immutable ref readback and Git blob hashing remain required.

Read response completion, transport/cleanup disposition, selection status and
effect receipts stay distinct. Failed preflight leaves actions unattempted;
already confirmed native receipts survive later failure; unknown writes remain
durable and reconcile only by native attribution. No auto replay/new operation
ID, unconditional CI/merge-readiness rule, role issuer or policy engine is added.

## Compatibility and lifetime

Expand the source parser/façade first while preserving all old CLI/watch routes,
defaults and strict capability shapes. Existing payloads do not automatically
emit new syntax or register a tool. Conditional host adoption requires the actual
executing contract and current owner binding. Unbound/older peers refuse honestly.
No old operation is retired and no lockstep deployment is assumed.

V2 store reader compatibility, service drain, resource retention and rollback
remain with their existing owners. A command or tool result cannot release a
claim, erase receipts, move source refs or advertise a new owner. Cancellation
and original denials retain existing semantics; no new waiter/daemon is created.

## Source acceptance

- [ ] Actual CLI parser/JSON frontdoor and bound façade execute real load/sync,
  materialization, selected submit/inspect and honest unsupported states.
- [ ] Source-local Git fixtures prove separate attachment, retained exact
  head/common reads, text/binary/deletion, path/scope/ref drift and no lazy fetch.
- [ ] Synthetic actual store/owner consumers preserve late-read unattempted
  versus unknown outcomes and confirmed/partial receipts without blind replay.
- [ ] Old CLI/native/watch contracts remain unchanged; focused contained tests,
  changefiles, lint/install/docs gates and current-head review/CI/merge pass.

Actual allocation/preparation, tool-host registration, protected owner adoption,
running reader compatibility, live ADO end-to-end review and separately authorized
serving/release/rollout remain open. This source slice cannot close those gates or
repair a running legacy adapter.
