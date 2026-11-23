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
- [ ] Repair the observed atomic-bytecode admission race tracked by #14202,
  retaining strict source/path-hook checks and normal rollout readiness.
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
- [x] Run affected unit/contract tests, Python lint and required repository
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

### 2026-11-23 - Manager-owned mixed-version correction

- Moved remote command packaging into Manager. Remote launch no longer calls
  the selected local engine or requires either engine to parse a newly added
  launch capability flag. Existing target launch options and literal prompts
  remain intact; local policy, staging and receipt checks are unchanged.
- Resolve registered machine/environment selections with the shared
  machine-transport owner, including opaque keys and profile aliases. Retain
  prepared installation-cell bindings without reading a legacy registry.
- Final focused source packet passed 291 cases with one native-Bash skip on
  Windows, including actual PowerShell literal transport to a strict legacy
  parser. Ruff and install-contract checks passed. Installed acceptance and
  reviewed delivery remain open.
- The authorized Windows update retry failed its successor-health check and
  rolled back its selection. Preserve that failed outcome; it is not proof of
  installation, cleanup or permission to replay the original application task.
- #14193 review identified that a cell's `knowledge_repo` supplier could still
  consult a legacy registry through the reused Picker reader. Supplier lookup
  now uses the selected cell registry, with conflicting-legacy and missing-scoped
  controls. The revised focused packet passed 248 cases with one native-Bash
  skip; whole-file strict checks passed for Linux and Windows.
- #14193 merged as `3ed1d6ffcae7a1c1eee9ed8ba0864cf625ebd254` after
  current-head Copilot approval and required green CI. The addressed supplier
  thread is resolved. Managed completion reconciled the two local commits past
  the verified squash result without losing content.
- Next: verify promotion and authorized installed terminal behavior, resolve
  the retained rollout failure through its owning path, then complete tracking
  and resource close-out. The source correction does not close these gates.
- Claimed #14202 for the observed immutable-runtime admission race during
  rollout. A deterministic real-filesystem rename reproduces the same
  `FileNotFoundError` before the correction; five negative controls already pass.
  Keep this repair separate from the merged Manager compatibility correction.
- The race repair and existing immutable-environment packet passed 125 cases
  with four platform-specific skips under the supervisor and actual timeout
  plugin. Only disappearing numeric CPython temporaries in `__pycache__` are
  tolerated; missing source/metadata and retained unsafe entries still refuse.
- The module cap required extracting static environment qualification from
  construction into the existing retained identity component. The public API
  and retained-resource inventory are unchanged; no new component or protocol
  argument is introduced. Post-extraction Linux validation passed 150 cases
  with five platform-specific skips, including the retained guardian's
  source-replacement controls, under a 2-GiB/360-second cgroup.
- The same combined selection on Windows passed the 125 environment cases but
  hit 26 guardian-fixture failures before entering the validator: its inert
  `ctypes` double rejects the Windows lease module's import. Linux exercises
  that unit control; this is not a Windows guardian or installed-TTY pass.
  The existing installed Windows qualification gate remains open.
- Whole-file strict typing passed for 81 physical files across 17 projects,
  including materialized installer consumers on Linux and Windows. The module,
  install-contract and documentation guards passed. Removed only this
  contributor's generated scratch copies from the development source layout;
  no installer contract or runtime resource inventory changed.

### 2026-11-22 - Implementation merged; installed acceptance next

- #14059 merged as `f60677377abc302f6eb96af3f622e1596d715858` after
  current-head Copilot review with no open findings and required green CI.
  Added the requested performance-impact assessment without claiming native
  latency from unit-test counts. Native cold/warm timing remains unmeasured.
- The description edit triggered another required CI run. The first merge
  attempt correctly refused while that check was expected; managed merge
  completed after the new run passed.
- Reconciled the squash losslessly: 35 authored files matched the merge
  exactly, and all eight changed manifest nodes were retained alongside
  concurrent upstream additions. The source worktree now follows merged Dev.
- Next: verify Main promotion, perform authorized installation and an isolated
  owned terminal launch, then settle resources and tracking. This source
  milestone is not installed acceptance or effort completion.
- Promotion was blocked before this implementation by #14053: the native cold
  Open/reattach continuity test reached an unscoped mux query. The operator
  extended scope to repairing release-qualification blockers; keep that
  correction separate from the already-merged SSH-first implementation.
- Concurrent #14070 supplied the native Worktrees observation correction.
  Adopt that merged owner rather than publish the redundant local fixture
  adapter. Later promotion runs passed; exact release-content verification
  and installed acceptance remain separate gates.

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
- A follow-up review found that a separate slow policy fetch could consume the
  next observation's budget. Each foreground post-fetch observation now starts
  a fresh bounded read budget, clamped to any enclosing acquisition limit.
  Real-Git controls with simulated 301-second policy fetches cover unlimited
  completion, a still-valid outer bound and an exhausted outer bound.
- Restored finite defaults for seed cleanup outside explicit foreground
  scopes. Foreground source fetches explicitly select caller-owned completion
  capture and use the existing non-terminating stage heartbeats during silence;
  background stream-idle warnings remain unchanged.
- Added exact Windows SSH command-line capacity checks (including quoting and
  UTF-16 code units) before process creation, without truncating input. The
  target-local completion fence also refuses remote use before configuration,
  so no required capability can disappear through legacy forwarding.

### 2026-11-21 - Kickoff

- Captured the explicit SSH-first replacement intent and filed #14047, #14049
  and #14048 after checking existing parity and identity-convergence work.
- Planning only; no implementation, release or installed acceptance claimed.
