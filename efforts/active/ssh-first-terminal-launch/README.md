# SSH-First Terminal Launch

## Goal

> When I hit "Create" all the info is packed up, Worktree Manager exits, and
> then SSH command is executed in the terminal to hand over control to the
> TTY-enabled worktree creation flow over SSH with all the arguments. No
> receipts admissions, or policy checks, back to Windows, just straight to SSH
> and let the command over SSH handle everything: Create worktree in target
> repo with provided options: mux/anchor/seed/etc.

- **Slug:** `ssh-first-terminal-launch`
- **Repo:** copilot-extensions
- **Branch(es):** sequential managed PRs targeting `dev`
- **Created:** 2026-11-21
- **Status:** Active
- **Vision:** [Picker: SSH-first terminal handoff](../../../visions/picker/README.md#ssh-first-terminal-handoff)
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
proposal in [Remote Picker Seed Parity](../remote-picker-seed-parity/README.md).
Existing admission/status interfaces remain available for retained recovery
and compatible callers; old uncertain requests are not migrated or replayed.
The [CLI contract pattern](../../../docs/patterns/service-contract-evolution.md)
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

## Scope Approvals

The operator assigned vision/architecture updates, public issue tracking and
implementation of this launch change on 2026-11-21. Normal source validation,
review fixes and PR delivery are included. Live service restarts, destructive
recovery and unrelated application work are not granted by this plan.
Existing rollout gates and any operational holds remain independent.

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

- [ ] Complete the Validation Plan and normal required review/CI, then merge
  the implementation and reconcile this effort.
- [ ] Verify promotion, authorized installation and ordinary terminal launch
  behavior; record actual evidence separately from source tests.
- [ ] Resolve all owned PR/resource obligations, close the tracking issues,
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
- [ ] Run affected unit/contract tests, Python lint and required repository
  guards serially. Do not equate mocked plan JSON with an interactive launch.
- [ ] Validate an isolated owned terminal launch after authorized rollout,
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

### 2026-11-21 - Reviewed intent and implementation

- #14052 merged after current-head approval and required checks. Scoped the
  exit requirement to in-place handoffs without adding detached capability.
- Started #14049/#14048 through a pure terminal-plan contract and negotiated
  foreground writer completion; existing programmatic admission stays available.
- The creation trace also contained a default source-fetch wall clock. Remove
  that foreground default while preserving explicit resource bounds and
  independent bounded policy observations.
- Validation remains in progress; no source completion or installed acceptance
  is claimed by this journal entry.
- Focused validation passed: 232 Manager cases (two optional skips), 259 target
  handoff/writer/native cases, 35 real-allocation audit cases, 17 Git deadline
  controls, 11 selected actual-Git source controls and 51 shared protocol cases.
  Additional terminal/foreground witnesses passed, including actual PowerShell
  literal transport, actual Git completion custody and Picker-exit ordering.
- The full actual-Git file exceeded the existing 300-second group budget;
  selected relevant cases passed without raising that limit. A synthetic
  allocation fixture was strengthened to use real Git and its canonical
  receiving ledger. Native process tests use a concrete PowerShell executable,
  not an activation alias. These are isolated validation changes, not host
  configuration changes.

### 2026-11-22 - Review and required-gate corrections

- Corrected registry-aware alias handling: preserve opaque registered keys and
  SSH profile aliases, dropping an environment only when the existing resolver
  proves the destination unchanged.
- Refused legacy recovery fast-path arguments before packaging; actual native
  preprocessor controls confirm why ordinary resolution would otherwise be
  bypassed. Explicit target recovery remains separate.
- Added actor-free foreground Git ownership coverage to the admitted unit
  packet and refreshed its changed source inputs without weakening its gates.
- Completed the mandatory whole-file strict typing of touched source and
  tests; the strict ratchet passes for both Linux and Windows targets.
- Required PR checks, current-head review and installed acceptance remain open.

### 2026-11-21 - Kickoff

- Captured the explicit SSH-first replacement intent and filed #14047, #14049
  and #14048 after checking existing parity and identity-convergence work.
- Planning only; no implementation, release or installed acceptance claimed.
