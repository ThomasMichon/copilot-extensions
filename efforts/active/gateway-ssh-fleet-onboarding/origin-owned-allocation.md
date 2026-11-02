# Origin-owned remote allocation prerequisite

Back to [Gateway and SSH Fleet Onboarding](README.md).
Canonical work item: [#5839](https://github.com/ThomasMichon/copilot-extensions/issues/5839).
Architecture: [origin-owned remote allocation](../../../docs/patterns/workspace-allocation-and-session-acquisition.md#origin-owned-remote-allocation).

## Selected outcome and scope

The operator's requested interface is:

```text
agent-worktrees create --json --machine=<targetmachine> --repo=<targetrepo|default=currentfromcwd>
```

The notation after `--repo` describes the default, not a literal argument value.
The calling managed worktree is resolved from CWD before destination selection.
Worktrees performs target allocation over SSH or a future adopted Gateway route.
Successful allocation means the actual receiving worktree and the originating
worktree's allocation responsibility are durable. Exact child-claim linkage is
asynchronous and reported separately; it must not hold up a composing host's
agent startup after durable receiving creation. Allocation alone starts no agent.

The operator selected the architecture, then directed:

> Great. Help ensure this is the architecture

and subsequently:

> In any case, continue driving your effort, tackling blocking issues as you encounter them

The operator then rejected an acknowledgement-dependent launch gate:

> Wait, why block agent-launch? This just creates a point of failure

and selected launch after durable receiving creation, clarifying:

> I'd rather asynchronously try to link claims back while getting the agent booted up

This supersedes confirmation-before-embodiment, not the pre-effect pending
obligation, exact-request recovery or ordinary hosting/session/task admission.

#9155 records the initial reviewed architectural intent, #9169 reviewed the
execution plan, and #9179 reviewed the non-blocking revision. This amendment specifies the ordered source and validation work
before runtime implementation. The decomposition
below is **agent-recommended realization**, not a new identity, transport,
credential or lifecycle authority.

`patient-bearing` owns the bounded Worktrees allocation/claim source and its
Bridge/Dispatch allocation consumers under #5839. Keep #5972/#5674 machine
identity, #6076 session acquisition, #7237 retained ACP relationships and
#6030/#6240/#6424 installation/lifecycle custody separate. This slice uses their
published boundaries; it does not take over their active worktrees or redesign
their unrelated state machines.

## Existing source and compatibility boundary

- `creation_requests.py` journals repository-qualified allocation intent before
  effects and distinguishes completed replay, pre-effect refusal, pending
  uncertainty and retired identity. Extend that owner; do not add a competing
  remote creation database.
- `worktree_ops_cli.py` resolves the current creator and invokes the ordinary
  sessionless creation core. Destination selectors must not rebind that origin.
- `worktree_creation.py` rejects a cross-space `owner_ref` because its ordinary
  reciprocal claim requires an origin-local record lock. Preserve that existing
  refusal for unsupported calls; the new receiving operation must establish
  explicit remote-allocation authority rather than bypass it.
- The existing `ResourceClaim.producer` slot and registered tracking-write
  transactions protect pending responsibility, exact identity and stale writes.
  The disposable API closes on verified absence; the retained relationship API
  closes on provider-local retirement. Neither proves a child worktree is
  actually concluded. Add an explicitly versioned owned-worktree allocation
  contract within the existing ledger/authority instead of changing either
  lifetime's meaning or using ordinary unchecked `claims add`.
- Bridge's legacy remote resolver forwards the caller owner to the receiving
  creation command. Its metadata-preserving compatibility refusal remains
  necessary until that consumer selects the new origin-coordinated operation.
  Dispatch must consume that same allocation through its existing composition,
  not add a third creator.

New capabilities are declared at the actual receiving operation and originating
write authority. Expand providers before migrating callers, retain strict unknown
argument rejection and qualify the supported writer/rollback closure before
activation. A current payload or equal package version is not proof of running
capability. Unsupported owned creation remains a visible pre-effect refusal,
never a fallback to an unowned checkout.

## Ordered implementation plan

### A. Protected origin and receiving contracts

- [x] Land the initial execution/validation amendment before implementing new runtime
  semantics.
- [x] Review the operator-selected non-blocking linkage revision before
  implementing its differing startup and reconciliation semantics.
- [x] Define strict, bounded JSON requests/results for origin reservation,
  receiving creation/inspection, ownership confirmation and original-operation
  recovery. Keep creator, destination, logical request, actual child and session
  identities distinct.
- [x] Reuse the origin's existing resource ledger and registered local write
  authority for a protected pending allocation. Make exact request replay
  idempotent and changed facts a conflict; no network I/O under its record lock.
- [x] Bind the private allocation epoch/capability and qualified receiving
  identity through the existing creation journal. A lost response retains the
  same original operation; do not reinterpret a timeout as absence.
- [x] Add receiving record evidence sufficient for its normal lifecycle to
  recognize the qualified foreign creator without opening a foreign registry,
  copying its records or weakening ordinary cross-space owner checks.
- [ ] Permit ordinary embodiment once receiving creation and its original
  ownership journal are durable. Link the origin's exact child claim
  asynchronously while the agent boots, without adding an acknowledgement gate.
  Interrupted linkage cannot create another child or permit a second parent.
- [x] Keep bounded linkage reconciliation with the existing responsible owner
  and durable original operation across restart. Do not rely on an unowned
  background coroutine, reverse callback or new scheduler/daemon.

### B. Ordinary CLI and shared consumers

- [x] Extend ordinary `create` with destination machine/repository selection
  while preserving CWD-based origin identity, omitted-selector local behavior,
  source-only allocation and explicit standalone policy.
- [ ] Use existing machine/environment resolution, attributed command selection
  and shell/SSH transport. A future Gateway adapter must enter the same named
  target operation; no second topology store, shell payload or allocator.
- [ ] Route Bridge's new owned remote allocation through the origin's Worktrees
  command, retaining actual target identity and the ordinary sessionless result.
  Durable receiving creation may precede its existing session embodiment while
  origin-claim linkage proceeds independently.
- [ ] Inventory Dispatch's ordinary and explicit venue creation paths; migrate
  allocation consumers to the same authority without replacing task reservations,
  leases, prompt admission or conversation recovery.
- [ ] Keep direct local creation, existing sessionless embodiment, deliberate
  unowned creation and older-peer refusal behavior covered. Do not silently drop
  provided owner metadata or convert a requested execution mode.

### C. Conclusion and recovery

- [x] Make pending/unknown allocations block parent conclusion and transfer.
  Protect them from generic release, stale full-record saves, disabled gate modes
  and cleanup paths; only the named allocation authority changes their phase.
- [ ] Preserve the target's normal child finalization/publication and paired
  knowledge requirements. Neither an empty directory nor a clean checkout proves
  the worktree concluded.
- [x] Reconcile the origin's obligation from exact receiving lifecycle evidence,
  even when the child directory or process no longer exists. No mandatory
  target-to-origin connection, automatic deletion or age-based release.
- [x] Preserve explicit accepted ownership transfer or refuse an unsupported
  transfer while retaining responsibility. Do not reuse disposable absence or
  retained-conversation retirement as a shortcut.
- [ ] A previously failed legacy operation is inspected through its original
  owner and effects before any separately selected replacement. New code does
  not migrate an unknown legacy outcome by guessing a child or clearing a claim.

### D. Reviewed delivery

- [ ] Update owning CLI, allocation, claim/lifecycle and Bridge/Dispatch docs
  with actual implemented capabilities, recovery and refusal behavior; add
  mandatory changefiles for every affected payload/shared-library consumer.
- [ ] Pass the focused validation below and normal review/required CI for each
  coherent source slice. Qualify construction and clean-room tiers where relevant;
  no new exhaustive matrix in required PR unit CI.
- [ ] Observe actual promotion and perform authorized normal unified rollout.
  Verify installed command/claim behavior independently of source success.
- [ ] Reconcile the preserved legacy attempt, then perform the separately bounded
  native installed-SDK qualification through the now-supported allocator.
  Journal actual resources and outcomes; the parent delivery gate stays open.

## Provider source boundary

The checked source items above are implemented by the
[owned-worktree provider](../../../plugins/agent-worktrees/docs/worktree-allocation.md):
protected origin transactions, first-save receiving receipts, original-operation
recovery, sealed cancellation and retained actual child conclusion. Receiving
creation is sessionless and local admission does not require claim acknowledgement.
This is provider preparation, not installed or end-to-end remote allocation.
The source foundation merged in #9186 as
`0050dd33e6fce80e21a7e6808fb6eca624c66c4c`; the parent Journal records its
current-head review and exact qualification boundary.

Focused isolated tests cover the original first-save and ready-checkpoint crash
boundaries, a live operation owner in another process, omitted versus malformed
receipts, exact identity and reciprocal-pair refusals, and composition recovery
without recarving. The required unit packet adds only pure custody/proof decisions
with production coverage; actual receiving CLI and synthetic SSH/interpreter
boundaries remain separately selected integration tests.

The restart-safe asynchronous linker merged in #9214 as
`ce469bffe906be5aad7b72b0b4047c206fb61387`. The existing elected resident
owns strict current-ledger discovery, finite receiving requests, current
governance rechecks and mandatory worker settlement. Its validation covers
malformed inventory, later project registration, same-claim restart recovery,
non-blocking locks and drain/retirement boundaries.

The separately negotiated original creation-result capability merged in #9222
as `97836c1a5a6b2a710b65fd55d2ce4911f6d887fc`. It returns the actual completed
receiving journal's ordinary sessionless result and preserves unfinished
status without a usable result. It neither creates a replacement nor grants
fresh session admission. The provider reference owns the exact wire contract.

The ordinary destination coordinator now consumes those source contracts. It
captures the original CWD owner before destination selection, uses the protected
origin writer and selected receiver, and returns the actual journaled child with
separate linkage status and stable retry identity. The
[provider reference](../../../plugins/agent-worktrees/docs/worktree-allocation.md#ordinary-destination-selection)
owns supported options and explicit pre-effect refusals.

The first origin-local owned SSH Bridge consumer merged in #9241 as
`d8c29a492ad29c0bcd4ff2f6a4291170952107fb`. It persists the original operation
and receiving child before native startup, while preserving independent claim
linkage and ordinary hosting/session admission. Its
[owning reference](../../../plugins/agent-bridge/docs/owned-workspace-allocation.md)
records interrupted allocation, unknown native identity and serialized
stop/resume behavior.

Foreign-origin Bridge composition, Dispatch migration, actual startup alongside
delayed linkage, real outbound-only/native qualification and release/installed
acceptance remain open. No source-only checkbox establishes those outcomes or
closes #5839. Continue the supported owner-local installed/native lane and the
remaining named consumers; do not introduce another linkage service or an
acknowledgement-dependent startup gate.

The [selected native SDK witness](README.md#2026-10-27---native-linux-aarch64-sdk-boundary-qualified)
now exercises that installed origin-local path and the exact 11-case reviewed
SDK packet on Linux `aarch64`. It does not qualify every allocation caller or
platform, delayed-link startup latency, child conclusion or the Manager product.

## Validation Plan

All techniques below are **agent-recommended**. Pure schema/state tests belong
in the existing bounded required unit lane; process, SSH and native tests use
explicitly selected integration lanes with finite resource/cleanup ownership.

- [ ] Real origin and receiving CLI processes with isolated registered projects
  prove omitted-selector local creation and remote different-repo selection;
  the origin remains the CWD worktree and the result identifies the actual child.
- [ ] Process-boundary assertions prove pending responsibility before effects,
  exact receiving identity before binding, durable receiving creation before
  embodiment, honest pending/linked results, and no agent session/prompt as a
  creation side effect.
- [ ] Delayed, failed and lost claim-link acknowledgements do not delay ordinary
  child startup. Reconciliation, including after origin restart, binds the same
  child without duplicate creation, session launch or prompt delivery. Parent
  finalization remains blocked while responsibility is unresolved.
- [ ] Deterministic concurrent request and restart tests cover every interruption
  boundary: before receiving effects, after creation, after origin bind and
  during lost acknowledgement, including a running child before linkage.
  Recover one child and one claim; changed facts,
  retired identities and unknown history never allocate a replacement.
- [ ] Parent finalization, abandon, transfer, generic release and stale writer
  controls cannot discard a pending/unknown allocation. Malformed or unknown
  metadata is preserved, visible and blocking.
- [ ] Receiving normal mutation/finalization checks recognize only the new exact
  receipt-backed foreign-owner relation. Ordinary forged, mismatched and legacy
  cross-space references keep their existing refusal.
- [ ] Actual child conclusion, paired-knowledge requirements and retained
  tombstone evidence settle only the exact matching origin claim. Unreachable
  or missing target evidence remains unknown, not absent or safe to remove.
- [ ] The actual Bridge and Dispatch callers exercise the same allocation owner
  before session launch while preserving their existing prompt/task/session
  admission and recovery contracts.
- [ ] An outbound-only origin completes allocation and settlement without a
  reverse connection. Synthetic SSH command boundaries and an adopted Gateway
  transport double produce equivalent identity/claim outcomes without granting
  an arbitrary remote executable or weaker authentication.
- [ ] Focused mixed-version tests cover capable origin/receiver, unsupported
  receiver, unsupported origin writer, rollback and old-record behavior.
  Qualify all participating ledger writers before enabling the new metadata.
- [ ] Selected native Linux and Windows/WSL lanes retain their own platform,
  interpreter and installed provenance observations. A Python job running on
  ARM64 is not a requirement to add a C/C++/Rust component or install a compiler.
- [ ] Public artifacts remain generic; exact private inventory, resources and
  operational authorization stay in the adopting control plane.
