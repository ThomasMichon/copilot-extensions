# Installation-Independent Sessions

- **Slug:** `installation-independent-sessions`
- **Repo:** copilot-extensions
- **Branch(es):** serial, independently reviewed slices targeting `dev`
- **Created:** 2026-10-24
- **Status:** Active
- **Vision:** `visions/session-hosting/README.md` - installation-independent-running-sessions; `visions/plugin-services/README.md` - process-count-scales-with-services-not-sessions and hooks-and-callbacks-are-transient
- **Umbrella issue:** #8669
- **Related issues:** #5579 (existing launch-depth owner), #8410 (Linux uninstall), #8312 (Windows updater handoff), #8864 (supported Manager uninstall)

## Guiding Intent

A running muxed Copilot session belongs to its execution leg, not to the
continued existence of either launcher installation. Removing and replacing
worktree-manager and agent-worktrees must not disturb that session. Initial
launch and final exit delegate policy to the compatible implementation
selected at that boundary, without pinning an old interpreter or mutable
installed shell script for the session's lifetime.

Completion means production implementation plus actual same-process,
interactive survival and correct final lifecycle handling across real
uninstall/reinstall and update, not a copied-fixture demonstration or a merged
source change alone.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Installation-survival driver | Canonical plan, bounded implementation, serial PRs, validation and release acceptance | #8669 and this effort |
| Existing lean-lifecycle owner | Retains wait-only forwarding and launcher-depth reduction | #5579 and `efforts/active/lean-session-lifecycle/README.md` |
| Existing prerequisite owners | Retain already claimed installer/updater repairs | #8410 and #8312 |
| Linux baseline worker | Evidence-only disposable installation/uninstall baseline at the reviewed source pin | Reports to Installation-survival driver; no source or host changes |

## Coordination

- **Topology:** serial per-slice PRs; each slice leaves the default branch usable.
- **Host (owns PRs):** Installation-survival driver.
- **Delegates:** Linux baseline worker owns one disposable Linux baseline and
  its evidence/cleanup only; no source edits, git mutations or host operations.
  The driver retains production wrappers, hooks and Windows validation.
- **Boundary:** #5579's existing owner retains removal of wait-only forwarding
  layers. Agree the shared launch contract and overlapping edit ownership
  before changing those paths; no competing implementation or silent transfer.
- **Handoff:** read this README, the first unchecked Plan/Validation items,
  and the current journal; preserve open PRs, test resources and unresolved
  lifecycle effects. A phase or release is not whole-effort completion.
- No host uninstall, shared-daemon restart or installed rollout is authorized
  merely by this plan. Destructive tests use explicitly disposable roots and
  owned processes. Installed acceptance uses the target's normal approval flow.

## Context

- The earlier exit-output repair was released and installed. This effort
  preserves its quiet successful-exit behavior rather than re-solving it.
- Six prior Linux copied-wrapper controls passed, including process/terminal
  continuity, start-under-A/end-under-B and absent-handler controls. Those were
  feasibility evidence, not production installation-removal or Windows proof.
- #8410 records a literal Bash uninstall failure that blocked the real
  installation experiment. #8312 records an independent Windows updater
  handoff failure. Keep their existing ownership and reproduce only the
  prerequisites the current slice needs.
- Lean Session Lifecycle Phase 3 already owns wait-only process reduction and
  launch parity. This effort adds the stronger installation-removal survival
  requirement; it does not take over handoff queues or monitor optimization.
- This plan extends the session-hosting vision with installation-independent
  running sessions, and closes the existing transient-hook/process-lifetime
  intent. Follow the installation-cell, uniform runtime resolution,
  process-slot ownership, lifecycle logging and performance patterns. No new
  daemon, machine-global selector or parallel lifecycle authority is needed.

## Request

**Settled premise (summary):** keep the mux server -> copied session wrapper
-> Copilot construction stable while both launcher systems can disappear or
be replaced. Use redirectable start/end hooks at the boundaries, and prove the
contract in a real clean room.

The full literal inception request is preserved in
[inception-transcript.md](inception-transcript.md). Latest direction:

> Great. Continue driving my proposal as a proper effort.

The operator confirmed the slug `installation-independent-sessions`.

**2026-10-24 prerequisite decision:** the operator selected
`supported-uninstaller-first`:

> Add a supported product uninstaller first (larger scope)

The selected contract and its explicitly agent-recommended refinements are in
[manager-uninstall.md](manager-uninstall.md). This adds a prerequisite, not a
replacement for the continuing installation-survival objective.

The detailed fencing, negative controls and rollout admission below are
**agent-recommended implementation/validation refinements**, not additional
verbatim operator requirements.

## Plan

### Phase 1 - Reviewed contract and shared ownership

- [x] Resolve the target's effort adoption, read its conventions, deduplicate
  existing efforts/issues and confirm the canonical slug with the operator.
- [x] Capture the literal request, distinguish feasibility evidence from
  acceptance, and open the bounded tracking issue.
- [ ] Coordinate the shared contract and edit boundary with #5579's existing
  owner; preserve separately owned installer/updater repairs.
- [x] Land this proposal and standing-intent amendment through normal review
  before production implementation.
- [ ] Inventory current mux/no-mux, direct/new-window, resume and remote
  launch consumers; record the smallest compatible boundary and migration in
  [launch-contract.md](launch-contract.md).

### Phase 2 - Literal disposable-installation reproduction

- [ ] Review and implement the supported Manager uninstall prerequisite
  (#8864) in [manager-uninstall.md](manager-uninstall.md), then return to this
  same real-removal baseline.
- [ ] Reproduce current behavior with real installed A, an interactive owned
  session, actual uninstallation of both systems, installed B and final exit.
- [x] Resolve or coordinate #8410 only as the required Linux uninstall
  prerequisite; do not substitute deleting a prototype fixture for uninstall.
- [ ] Record baseline process identities, terminal interactivity, installed
  file removal and locks, and current failure without altering host installs.

### Phase 3 - Production launch-owned wrapper and boundary hooks

- [ ] Prepare launch arguments/environment before the stable wrapper; copy
  its self-contained platform shell into launch-owned storage outside both
  removable runtime/config roots, with installer/pruning ownership explicit.
- [ ] Resolve attributable, installation-scoped start/end entry points afresh
  at each boundary; no cached runtime Python, daemon endpoint or imported
  installed helper survives merely to wait.
- [ ] Keep the real mux server, wrapper, Copilot and kernel containment alive
  independently of installer removal. Preserve terminal, arguments, profiles,
  Ctrl+C and real exit code; coordinate wait-layer changes with #5579.
- [ ] Implement bounded exact-incarnation lifecycle admission through existing
  owners. A delayed old end cannot retire a replacement; observed exit is
  distinct from crash/loss, human cancellation and terminal disconnection.
- [ ] With no compatible handler at exit, close quietly without provisioning
  or falling through to another installation; do not falsely record delivery.
- [ ] Reconcile restart and missed-boundary evidence through the existing
  lifecycle owner, preserving unknown effects rather than blind replay.
- [ ] Land directly related tests, docs and required changefiles.

### Phase 4 - Native and clean-room acceptance

- [ ] Run the complete matrix in Validation Plan on production code and real
  disposable installations, including native Windows rather than Linux proxies.
- [ ] Exercise existing consumer paths and regressions; retain source/runtime
  identities and evidence that proves which build each result exercised.
- [ ] Fix failures found in the touched contract; transfer only genuinely
  independent work to named tracked objectives without dropping acceptance.

### Phase 5 - Release, installed acceptance and closure

- [ ] Verify containing normal Main release and exact released behavior.
- [ ] Obtain any required selected-host rollout approval, use the canonical
  updater, and validate a fresh native launch/exit. Existing sessions may
  still carry their older wrapper and are not rollout evidence.
- [ ] Re-read every Plan/Validation item, promote durable truth to owner docs,
  settle owned test/PR resources, mark Done and archive through normal review.

## Validation Plan

All detailed controls below are **agent-recommended** ways to prove the
operator's installation-survival contract.

- [ ] Unit tests cover boundary dispatch, installation identity, exact launch
  incarnation, duplicate/stale end handling, missing/unsupported handlers and
  unchanged literal argument/environment/exit propagation.
- [ ] A simple subprocess integration test invokes the real copied shell and
  real redirectable handler; mocks cannot bypass the resolution being tested.
- [ ] Linux clean room: install A, launch an interactive owned session,
  actually uninstall both systems, prove their runtime roots are gone, install
  B, and exit. The same mux, wrapper and Copilot processes remain interactive;
  A handles start, B handles end exactly once for the right incarnation.
- [ ] Native Windows repeats the actual uninstall/reinstall test, including
  executable/script locks and the wrapper-owned kill-on-close Job lifetime.
- [ ] Live update/rollback replacement preserves the same running session;
  exit resolves the then-current compatible handler in the same installation.
- [ ] Exit while neither system exists is quiet, bounded and non-provisioning.
  It preserves Copilot's exit code and honest unconfirmed-end state.
- [ ] An unsupported or foreign installation never becomes an implicit
  fallback. Two installations and concurrent sessions do not alias handlers.
- [ ] Delayed/duplicate end for A cannot unregister replacement B; observed
  nonzero Copilot exit is not misclassified as unobserved crash or user cancel.
- [ ] Handler/mux-management-daemon absence and restart do not kill the live
  mux server; recovery neither double-launches nor falsely retires a session.
- [ ] Ctrl+C, terminal detach/reattach, resume, quoted/double-dash arguments,
  interactive profiles and no-mux/new-window/remote consumers retain their
  existing supported semantics; any intentionally unsupported branch is explicit.
- [ ] Removing the containing wrapper genuinely tears down its owned children
  where required; survival is not achieved by discarding kernel containment.
- [ ] Measure persistent process ancestry and boundary latency; no new resident
  waiter, polling loop or unowned detached process replaces the removed coupling.
- [ ] Existing exit-noise controls still pass: successful finalization stays
  quiet, cleanup remains a bounded summary and updated batch tails remain valid.
- [ ] Normal release, explicitly approved selected-host installed validation,
  durable evidence and final effort review pass before declaring Done.

## Proposal

Keep policy in short-lived current handlers and the execution lifetime in the
small copied wrapper and OS-owned resources. The stable handoff contains only
the launch identity, installation identity and data needed by the existing
lifecycle owner; it is not another coordination store.

The first reviewed contract must specify bounded boundary calls, storage
ownership and reclamation, supported-handler compatibility and affirmative
end semantics. The actual uninstall test decides whether the mux executable,
wrapper or inherited process resources still depend on removable installations.
Keeping a version directory alive is not an acceptable substitute.

## Journal

### 2026-10-24 - Canonical proposal

- Operator asked to resume the installation-survival proposal as a proper
  effort and confirmed its slug.
- Native effort-adoption probe passed. The source repository owns this generic
  effort; no personal state or host identity is part of the public plan.
- Opened #8669 after deduplication. Retained #5579, #8410 and #8312 ownership.
- Prior Linux prototype evidence is feasibility only. No production changes,
  host uninstall, installed update or new lifecycle service were performed.

### 2026-10-24 - Supported Manager uninstall selected

- The original proposal merged in #8675; the installation-survival effort
  remains active.
- A later real-installation preflight could not establish a supported Manager
  uninstall entrypoint. It stopped before interactive launch or removal and
  cleaned its owned container; no manual deletion was reported as uninstall.
- The operator chose a supported product uninstaller first rather than the
  test-only removal alternative. Opened #8864 after deduplication and retained
  this as a prerequisite of the same canonical effort.
- This change proposes the bounded removal/compatibility/validation contract
  for review before implementation. No uninstaller, runtime capability,
  process-survival acceptance or host rollout has been implemented by it.

### 2026-10-24 - Linux uninstall prerequisite

- The proposal and standing-intent amendment merged in #8675. The effort is
  active; the complete installation-survival contract remains open.
- A real disposable installation reproduced #8410: Bash rejected the
  script-scope `local` declaration during tool-binstub removal. The bounded
  repair removes that declaration without changing uninstall policy.
- Validated code snapshot `806bccf01f793dbd9eaf73dad513e9f286142bde` through
  native canonical source materialization in two fresh Linux clean rooms.
  The real-branch regression executed with one pass and no skips. Actual
  Worktrees and Manager installations passed; the complete Worktrees
  uninstall exited zero and removed its previously inventoried versions,
  Linux binstub, update helpers and deploy manifest.
- Labelled disposable project configuration, session JSON and tmux
  configuration retained identical hashes. Manager remained unchanged.
  Shared selectors, runtime resolvers, metadata and Windows-specific shims
  remain explicitly outside a complete-root-erasure claim. Both owned
  containers were removed; no host installation or service was changed.
- This evidence qualifies only the Linux uninstall prerequisite. Manager
  teardown, reinstall, interactive Copilot and TTY continuity, handler
  adoption, live update and native Windows survival remain unqualified.
  The native Windows launch baseline is still failing; no fixture-only
  success or timeout/containment relaxation closes that gate.

### 2026-10-24 - Refreshed Linux prerequisite validation

- After the upstream update, repeated the same packet at exact code snapshot
  `7211b0ad7a60b3a72ade8f18962b8b5281f5f385`: one regression pass without
  skips, both real installs successful, actual Worktrees uninstall exit zero,
  unchanged labelled durable-state hashes and complete owned-container cleanup.
- Preserved the separately merged Manager uninstall decision/contract while
  resolving only the effort documentation overlap. The Bash repair is unchanged.
  Supported Manager uninstall, actual interactive survival and the parent
  effort's remaining gates are not completed by this prerequisite.

### 2026-10-24 - Reviewed intent and initial consumer trace

- #8675 merged as `32b43c74e7e0f35a947eb605f8ef5ea83ad5b718` after an
  exact-head Copilot approval with no findings and required green CI. Managed
  completion used the repository-authorized exception for an additional
  CODEOWNER vote; it did not waive review findings or required CI.
- Reconciled the terminal PR through `pr-status` and pulled the source
  worktree forward with the normal guarded sync. No manual rebase bypass.
- Re-read the canonical plan after merge. Implementation and native acceptance
  remain open; a merged proposal is not completion.
- Began the production consumer trace in [launch-contract.md](launch-contract.md).
  Found version-pinned Manager end callbacks, installed wrapper paths and a
  distinct agent-worktrees handoff/new-window consumer; copying one script
  alone would not migrate the contract.
- Announced the bounded integration boundary on #5579. A separate cleanup
  rollout owner confirmed no wrapper/lifecycle edits and no custody conflict;
  that does not transfer #5579's broader forwarding-removal programme.
- Assigned one evidence-only Linux worker to the pinned real-installation
  baseline. No host uninstall/update, repository edits or credentials are
  delegated; a concrete setup/uninstall failure is a result, not a success.

### 2026-10-24 - Literal baseline and native prerequisites

- Pinned Linux baseline at the reviewed source completed real installations
  of both products. The actual Worktrees uninstall failed with exit 1 on its
  top-level `local stub_path`, reproducing #8410. Both owned containers were
  removed; no host installation or shared Docker service was changed.
- That baseline did not launch tmux or Copilot and did not reach Manager
  uninstall, reinstall, state-preservation or handler-adoption acceptance.
  It is a prerequisite failure, not a survival pass.
- The selected native Windows wrapper/argv baseline initially exposed a
  parser-test timeout. Strict error handling found that the fixture included
  unrelated undefined hosting-admission calls and silently masked a missing
  Newtonsoft type on PowerShell 5.1. A local prerequisite repair uses the
  built-in string-preserving JSON reader and strict parser-only fixtures.
- The repaired selected native matrix passed 57 tests with two opt-in skips;
  both PowerShell 5.1 and 7 exercised literal strings and malformed-command
  refusal. This is local working-source evidence, not a reviewed/released fix.
- Enabling the skipped real PSMux consumers exposed fixture dependency
  coupling and then an unresolved delivery failure: a pane handoff may be
  consumed while the controlled child's output never appears within the
  existing budget, or the session exits immediately. A direct native child
  control remains live after its creating client exits.
- A temporary copied-wrapper trace reached wrapper entry with the correct
  native command and arguments. Disabling the pane Job in an isolated
  diagnostic did not resolve the failure; containment remains required and
  enabled. Waiting for exact pane teardown and making the fixture child
  noninteractive did not establish a passing delivery result either.
- Temporary wrapper tracing and the diagnostic Job override were removed.
  The native delivery failure must be diagnosed before calling this baseline
  green. No deadline was raised and no application session was stopped.
- The Linux worker returned a minimal uninstall fix and a proposed real-branch
  regression/rerun plan. Neither has been applied or qualified yet. The
  production copied-wrapper/current-handler migration remains outstanding.

### 2026-10-24 - Longer-running native diagnostic

- Operator direction:

  > Perhaps just run the tests unbounded and see what happens right now

- Temporarily removed the native fixture's short subprocess, output and
  teardown waits, while retaining real PSMux, both production producers,
  exact process identities, observation and kernel containment.
- The first diagnostic still hit the runner's separate 30-second item
  watchdog. Its stack was waiting for the PowerShell producer's stdout to
  close, before that cycle's PSMux launch.
- The contained runner requires positive budgets. With diagnostic-only
  `--test-timeout 1200 --timeout 1500 --plugin-timeout 1800`, all three native
  cases passed: both producers completed two cycles and the direct control
  passed. Pytest reported three passes, no skips and 53 deselections in
  80.63 seconds; the complete command took 97.83 seconds.
- This establishes eventual delivery for that diagnostic, not a pass under
  the ordinary budgets or proof that every earlier failure has one cause.
  Separate setup, delivery and teardown timings before changing a permanent
  test budget. Interactive Copilot and actual installation survival remain
  open.
- Restored the temporary test edits exactly to the published candidate.
  No production deadline, process-containment policy or acceptance gate changed.

### 2026-10-24 - Scoped native budgets and uninstall-contract preflight

- Phase timing did not justify calling every failure a timeout: PSMux 3.3.5
  also returned an immediate error followed by an absent server. An isolated,
  digest-verified 3.3.6 comparison completed both cycles without changing the
  host installation.
- Observed native readiness reached 23.344 seconds. A later run separately
  measured file creation at 13.810 seconds and its pane query at 0.172 seconds,
  confirming that the former eight-second fixture limit was too short.
  Pane teardown remained below 1.2 seconds and descendant cleanup below
  0.05 seconds.
- Replaced temporary unbounded instrumentation with scoped, finite integration
  limits: 180 seconds per opt-in two-cycle case and 45 seconds for its
  PowerShell preparation/readiness. Ordinary unit defaults and existing
  client, query, teardown and containment limits remain unchanged.
  The normal runner passed all eight selected controls in 86.70 seconds;
  both workflow contract tests also passed.
- The manual Windows lane now pins test-tool PSMux 3.3.6. This is neither an
  operator-host upgrade nor a new production compatibility-floor policy.
- A fresh Linux preflight at published source
  `0e5d68c82bb8a7f810f5b60342c0e86a7a8995a5` borrowed credentials through
  the normal provider, installed both real products and the missing tmux
  prerequisite, and registered a synthetic local project through the actual
  CLI. It did not establish interactive Copilot entitlement or launch a session.
- The baseline stopped because no supported Manager uninstall entrypoint was
  established from its installed help and pinned product entrypoints. No
  manual file deletion was substituted for uninstall. The exact owned
  container was removed and the shared Docker service left running.
  Resolve that product contract before claiming a real two-product survival
  baseline; the copied-wrapper/current-handler implementation is still open.

### 2026-10-25 - Prerequisites merged and implementation resumed

- #8763 merged as `cf5bfdea83164a21e717591f6e2055bbce8ebbb6` after
  exact-head approval, green required CI and final changed-dependency
  qualification at `f00b06be11bb1e3f639be5db35cbc3573337e235`.
  The real Linux regression passed without skips; both actual installs
  passed, Worktrees uninstall returned zero and labelled durable-state hashes
  were unchanged. Both owned disposable containers were removed.
- This closes the bounded #8410 prerequisite, not complete Worktrees-root
  erasure or same-process installation survival. Shared selectors, resolvers
  and cross-platform shims remain inventoried rather than claimed removed.
- #8871 merged the supported Manager uninstall contract. Implementation now
  targets the existing installer with read-only preview, attributable artifact
  removal, existing publication/retention custody and an external interpreter
  boundary. Older layouts without sufficient ownership evidence must refuse;
  the installer must not rebuild a target merely to remove it.
- The scoped native argument-delivery candidate passed its local eight-case
  packet and the fresh Windows lane at `5470775c0ec8f940d50e3b6e82b08252790eb424`.
  Those results supersede the earlier unresolved diagnostic for that narrow
  candidate, not interactive Copilot or installation-removal acceptance.
- Next: qualify and land the native argument-delivery prerequisite, implement
  and qualify supported Manager removal, then resume the same real interactive
  two-product baseline and production copied-wrapper/current-handler work.
  The parent effort, release and approved installed validation remain open.
