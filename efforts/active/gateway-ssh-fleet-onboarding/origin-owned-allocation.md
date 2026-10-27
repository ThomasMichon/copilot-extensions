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
Success means both the actual receiving worktree and the originating worktree's
matching claim are durable. Allocation alone starts no agent.

The operator selected the architecture, then directed:

> Great. Help ensure this is the architecture

and subsequently:

> In any case, continue driving your effort, tackling blocking issues as you encounter them

#9155 records the reviewed architectural intent. This amendment specifies the
ordered source and validation work before runtime implementation. The decomposition
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

- [ ] Land this execution/validation amendment before implementing new runtime
  semantics.
- [ ] Define strict, bounded JSON requests/results for origin reservation,
  receiving creation/inspection, ownership confirmation and original-operation
  recovery. Keep creator, destination, logical request, actual child and session
  identities distinct.
- [ ] Reuse the origin's existing resource ledger and registered local write
  authority for a protected pending allocation. Make exact request replay
  idempotent and changed facts a conflict; no network I/O under its record lock.
- [ ] Bind the private allocation epoch/capability and qualified receiving
  identity through the existing creation journal. A lost response retains the
  same original operation; do not reinterpret a timeout as absence.
- [ ] Add receiving record evidence sufficient for its normal lifecycle to
  recognize the qualified foreign creator without opening a foreign registry,
  copying its records or weakening ordinary cross-space owner checks.
- [ ] Confirm the origin's exact child claim before the receiving allocation
  becomes eligible for embodiment. Interrupted confirmation is recoverable and
  cannot create another child or permit a second parent.

### B. Ordinary CLI and shared consumers

- [ ] Extend ordinary `create` with destination machine/repository selection
  while preserving CWD-based origin identity, omitted-selector local behavior,
  source-only allocation and explicit standalone policy.
- [ ] Use existing machine/environment resolution, attributed command selection
  and shell/SSH transport. A future Gateway adapter must enter the same named
  target operation; no second topology store, shell payload or allocator.
- [ ] Route Bridge's new owned remote allocation through the origin's Worktrees
  command, retaining actual target identity and the ordinary sessionless result.
  Only confirmed ownership may precede its existing session embodiment.
- [ ] Inventory Dispatch's ordinary and explicit venue creation paths; migrate
  allocation consumers to the same authority without replacing task reservations,
  leases, prompt admission or conversation recovery.
- [ ] Keep direct local creation, existing sessionless embodiment, deliberate
  unowned creation and older-peer refusal behavior covered. Do not silently drop
  provided owner metadata or convert a requested execution mode.

### C. Conclusion and recovery

- [ ] Make pending/unknown allocations block parent conclusion and transfer.
  Protect them from generic release, stale full-record saves, disabled gate modes
  and cleanup paths; only the named allocation authority changes their phase.
- [ ] Preserve the target's normal child finalization/publication and paired
  knowledge requirements. Neither an empty directory nor a clean checkout proves
  the worktree concluded.
- [ ] Reconcile the origin's obligation from exact receiving lifecycle evidence,
  even when the child directory or process no longer exists. No mandatory
  target-to-origin connection, automatic deletion or age-based release.
- [ ] Preserve explicit accepted ownership transfer or refuse an unsupported
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

## Validation Plan

All techniques below are **agent-recommended**. Pure schema/state tests belong
in the existing bounded required unit lane; process, SSH and native tests use
explicitly selected integration lanes with finite resource/cleanup ownership.

- [ ] Real origin and receiving CLI processes with isolated registered projects
  prove omitted-selector local creation and remote different-repo selection;
  the origin remains the CWD worktree and the result identifies the actual child.
- [ ] Process-boundary assertions prove pending responsibility before effects,
  exact receiving identity before binding, both owners' durable confirmation
  before success, and no agent session/prompt as a creation side effect.
- [ ] Deterministic concurrent request and restart tests cover every interruption
  boundary: before receiving effects, after creation, after origin bind and
  during lost acknowledgement. Recover one child and one claim; changed facts,
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
