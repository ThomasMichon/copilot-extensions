# SSH-First Terminal Launch

## Goal

> When I hit "Create" all the info is packed up, Worktree Manager exits, and
> then SSH command is executed in the terminal to hand over control to the
> TTY-enabled worktree creation flow over SSH with all the arguments. No
> receipts admissions, or policy checks, back to Windows, just straight to SSH
> and let the command over SSH handle everything: Create worktree in target
> repo with provided options: mux/anchor/seed/etc.

> We still need to ensute some back-compat

> This should have just been a change to worktree-manager, to call
> agent-worktrees differently. Why a new argument that causes incompatibility?

- **Slug:** `ssh-first-terminal-launch`
- **Repo:** copilot-extensions
- **Branch(es):** sequential managed PRs targeting `dev`
- **Created:** 2026-11-21
- **Status:** Done
- **Completed:** 2026-11-23
- **Vision:** [Picker: SSH-first terminal handoff](../../../../visions/picker/README.md#ssh-first-terminal-handoff)
- **Umbrella issue:** #14047
- **Sub-issues:** #14049 (terminal delegation and selectors), #14048 (completion)

## Guiding Intent

Make remote interactive creation an ordinary target-local command reached
through SSH. The Manager collects options and yields the terminal; it does not
orchestrate a preliminary remote allocation. Target-local authorization,
creation, prompt provenance and backend ownership remain intact. Remove
completion clocks, not correctness or explicit cancellation.
Manager exit refers to the in-place handoff; supported detached new-window
launches retain their existing chooser lifetime and run SSH first in the new
terminal. This does not add a new detached-launch capability.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Driving contributor | Plan, source implementation, validation and PR delivery | Managed source worktree |

## Coordination

- **Topology:** reviewed intent PR, implementation PR, completion reconciliation.
- **Host (owns PRs):** Driving contributor.
- **Delegates:** none; run memory-intensive validation serially.
- **Handoff:** preserve this effort, its current slice, open PRs and unresolved
  gates. A phase or source merge does not complete the goal.
- **Related owners:** #6057 retains broader remote seed parity and its separately
  deferred native lane; #8375 retains launch-attempt audit. This bounded effort
  replaces the interactive precreation mechanism, not those owners or their
  resources. #5972 remains the shared machine-selection convergence owner.
- **Closeout:** The driving contributor retains #14047 for archive review/merge,
  tracker reconciliation, effort-binding release and source-worktree finalization.
  The independent legacy supervisor transition remains with #10377.

## Context

The existing prompted remote path calls target policy and structured admission
over noninteractive SSH before returning an interactive terminal command.
Admission creates the target worktree and stages its prompt. The originating
process waits for a receipt, then selects the already-created identity.

The target's tracking writer applies an eight-second request deadline and a
nine-second socket wait. Its operation can continue after the caller times
out. Separate SSH policy and admission subprocesses impose 60- and 180-second
wall clocks. The outer Manager foreground resolver already waits without a
default completion timeout; increasing that outer timeout cannot fix this.

This extends the Picker vision and supersedes the preliminary-handshake
proposal in [Remote Picker Seed Parity](../../../active/remote-picker-seed-parity/README.md).
Existing admission/status interfaces remain available for retained recovery
and compatible callers; old uncertain requests are not migrated or replayed.
The [CLI contract pattern](../../../../docs/patterns/service-contract-evolution.md)
requires reuse of existing target syntax where equivalent, explicit unsupported
behavior and no effectful probe followed by fallback replay.

## Request

The Goal preserves the settled launch contract verbatim. The operator also
requested:

> The only thing which might time out is initial connection to WSL. Past that
> point, it's just git operations and file IO. We need to fix the flow so it
> can't time out.

> Great. Update vision/architecture to align, and drive this mini-effort with
> tracking issues. Implement these changes.

The reported machine/environment selector defect is generalized here:
use a physical machine plus its environment, or an execution alias alone;
do not mix the two forms. Private incident identifiers and the original
application task are intentionally excluded from this public plan.

The operator then reported Resume refusing an older selected engine with
`unrecognized arguments: --require-terminal-handoff` and requested backward
compatibility. Preserve the SSH-first contract while supporting mixed installed
Manager/engine generations; a capability refusal is not completed delivery.
The operator clarified the correction's scope: Manager should change how it
calls the existing target command, not require a new engine launch argument.

## Scope Approvals

The operator assigned vision/architecture updates, public issue tracking and
implementation of this launch change on 2026-11-21. Normal source validation,
review fixes and PR delivery are included. Live service restarts, destructive
recovery and unrelated application work are not granted by this plan.
Existing rollout gates and any operational holds remain independent.

On 2026-11-22, the operator selected "Repair release-qualification blockers and
complete delivery", extending source scope to existing release-qualification
failures blocking this change, starting with #14053. Normal review, required CI
and existing native-run time/resource limits remain. This does not authorize
shared mux shutdowns, service restarts or unrelated source changes.

The operator subsequently authorized repair of rollout blockers, including the
supported interrupted-update queue release and canonical retries after fixes.
Release preserves the failed outcome and is not cleanup proof. Shared-process
kills, service restarts, lock deletion and weaker readiness checks remain
excluded. Installed acceptance still requires actual serving-state evidence.

## Plan

### Phase 1 - Reviewed intent and architecture

- [x] Land this mini-effort, vision amendment and explicit architecture
  transition through normal review before implementation.
- [x] Retain #6057/#8375 ownership and supersede only the interactive
  preliminary-handshake mechanism.

### Phase 2 - Terminal-owned execution

- [x] Implement #14049: package the complete remote selection, exit the UI and
  execute TTY-enabled SSH before target creation or policy work.
- [x] Preserve New, anchor, cold Resume/Open, mux/no-mux and arbitrary prompt
  text through normal target-side parsing; use canonical machine selection.
- [x] Implement #14048: accepted foreground creation has no elapsed completion
  deadline, including nested writer waits; retain connection bounds, explicit
  cancellation, real errors and locks without changing passive/background
  deadlines.
- [x] Update authoritative architecture, legacy-interface boundaries and
  changefiles for the actual changed components.

### Phase 3 - Delivery and completion

- [x] Restore backward-compatible launch with older selected engines without
  reviving remote precreation or replaying an effectful operation.
- [x] Complete source validation and normal required review/CI, then merge
  the implementation and reconcile this effort.
- [x] Repair tracked release-qualification blockers preventing promotion,
  starting with #14053; preserve private fixture namespaces and required gates.
- [x] Repair the observed atomic-bytecode admission race tracked by #14202,
  retaining strict source/path-hook checks and normal rollout readiness.
- [x] Complete the dependent synthetic-bootstrap fixture closure after the
  validator extraction; retain the existing component inventory and native gate.
- [x] Verify promotion, authorized installation and ordinary terminal launch
  behavior; record actual evidence separately from source tests.
- [x] Deferred to `#14047`: Resolve all owned PR/resource obligations, close the tracking issues,
  archive this effort and finalize through normal managed lifecycle.

## Validation Plan

The following focused checks are agent-recommended ways to prove the requested
contract; they add no new product scope.

- [x] Prove no remote creation, policy or admission subprocess runs before
  Manager exit; the final terminal command starts SSH with a TTY.
- [x] Exercise target parsing with New, anchor, cold Resume/Open, mux/no-mux,
  project selection and canonical physical/environment or alias selection.
- [x] Preserve Unicode, multiline text, quotes and shell metacharacters as
  literal prompt data across supported POSIX and PowerShell adapters.
- [x] Prove accepted creation can finish after the old completion budgets;
  genuine target failures and connection loss remain failures without replay.
- [x] Cover new/old caller and supported target forms at the real parser or
  process seam; retain legacy request inspection without mutating old intent.
- [x] Run affected unit/contract tests, Python lint and required repository
  guards serially. Do not equate mocked plan JSON with an interactive launch.
- [x] Validate an isolated owned terminal launch after authorized rollout,
  record delivered source identity and settle created validation resources.

## Proposal

Extend the existing exit-to-terminal delegation path rather than add another
transport or daemon. Resolve machine/SSH configuration locally and encode the
ordinary target command safely. Run target policy, creation and seed operations
only after SSH has taken the terminal. Reuse existing target CLI forms when they
preserve all selected options; publish receiver support before any genuinely new
syntax and never retry an effectful command under a weaker form.

Foreground creation must wait for its accepted writer operations to settle,
without arbitrary server or socket completion expiry. A bounded connection
attempt, explicit cancellation or real transport failure is distinct from
elapsed work. Preserve unknown-outcome recovery for genuine interrupted writes.
Do not globally unbound passive reads or unrelated background operations.

## Journal

- [2026-11-23 - Delivery and completion](<journal/2026/11.23 Delivery.md>)
- [2026-11-22 - Implementation and review](<journal/2026/11.22 Implementation.md>)
- [2026-11-21 - Intent and implementation](<journal/2026/11.21 Intent.md>)
