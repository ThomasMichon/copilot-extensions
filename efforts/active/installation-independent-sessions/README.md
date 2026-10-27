# Installation-Independent Sessions

- **Slug:** `installation-independent-sessions`
- **Repo:** copilot-extensions
- **Branch(es):** serial, independently reviewed slices targeting `dev`
- **Created:** 2026-10-24
- **Status:** Active
- **Vision:** `visions/session-hosting/README.md` - installation-independent-running-sessions; `visions/plugin-services/disposable-runtimes/README.md` - shared-maintenance-banner and service-independent-agent-standby; `visions/plugin-services/README.md` - process-count-scales-with-services-not-sessions and hooks-and-callbacks-are-transient
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

During an intentional maintenance window, the surviving agent can understand
why its tools disappeared without invoking those tools. It preserves progress
and obligations, pauses affected operations, reads the local-machine banner
through native facilities, waits for the window to clear, and resumes against
the restored capabilities. Maintenance-aware standby complements process
survival; neither substitutes for the other.

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
  merely by this plan. The operator subsequently authorized one selected live
  Windows test host for the [live round trip](live-roundtrip.md), including
  backups, uninstall/reinstall, re-release and necessary promotion fixes.
  This does not authorize effects on other hosts or waive repository, OS
  privilege, connectivity, custody or data-preservation gates.
- Reuse #9022's conduct-guidance companion and #9040's reviewed shared YAML
  banner contract, including their retained automatic-maintenance owners.
  This effort integrates standby with session survival; it does not introduce
  a second banner, scheduler, updater or maintenance authority.

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

**2026-10-25 priority and maintenance-window direction (summary):** prioritize
this isolation work and extend the vision so an agent whose supporting tools
disappear can pause, read a local-machine maintenance banner without those
tools, wait while the YAML window is active, and resume once it clears.
The [literal follow-up](inception-transcript.md#operator---priority-and-maintenance-window)
is retained without converting the priority statement into permission for a
host update, removal or rollout.

The standing intent already includes this extension through reviewed #9042.
The existing [shared YAML banner contract](../source-managed-runtime-installs/maintenance-banner.md)
is the implementation-contract owner. Its explicit same-window completion and
native 30-minute standby policy are **agent-recommended bindings**, not extra
literal requirements added to this request.

The detailed fencing, negative controls and rollout admission below are
**agent-recommended implementation/validation refinements**, not additional
verbatim operator requirements.

**2026-10-26 selected live-host direction (summary):** use the explicitly
selected Windows test machine to audit the install footprint, separate durable
state from reconstructible runtime data, back up the data first, and carefully
uninstall services/tools while preserving SSH-Gateway connectivity and muxed
Copilot. Prove a clean middle, then reinstall and reconnect to the retained data
so execution carries on. The operator explicitly authorized uninstall,
reinstall, re-release and necessary Main-promotion workflow repairs until this
round trip works. The [literal follow-up](inception-transcript.md#operator---selected-live-test-host)
redacts only the private host name. Concrete host identity and operational
records remain private; the generic execution contract is [live-roundtrip.md](live-roundtrip.md).

**Legacy-session decision:** after the agent identified runtime-Python
ancestors in existing sessions, the operator explicitly selected controlled
closure and relaunch **after verified backups and fixed-launcher
qualification**, preserving resumable state. The [literal selection](inception-transcript.md#operator---legacy-session-migration)
and live charter retain that ordering. Migration is not same-process survival;
the qualified replacement's later uninstall/reinstall test must preserve its
own original processes.

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

- [x] Review and implement the supported Manager uninstall prerequisite
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

### Phase 4b - Maintenance-aware session standby

- [x] Reconcile the expanded request with the already reviewed disposable-runtime
  vision and shared YAML-banner proposal; retain #9022/#9040 and existing
  maintenance implementation ownership rather than opening a competing design.
- [ ] Coordinate the YAML reader/guidance migration with those owners before
  exercising it. The earlier JSON-only source guidance is not YAML capability
  or evidence of delivery to an already-running session.
- [ ] Integrate the normal conduct projection and native-tool banner read with
  the actual surviving Copilot session; no runtime, service, command, network
  path or credential broker under maintenance may be needed to enter standby.
- [ ] Preserve progress, exact launch identity, claims and pending effects while
  affected operations pause. Do not reinstall, reprovision, repeatedly retry
  missing commands, launch replacement agents or take over the maintenance.
- [ ] Exercise the reviewed native standby/all-clear contract, then refresh
  selected invocation/capability evidence before resuming. Clearing the banner
  does not replay unknown mutations or enable independently disabled policy.

### Phase 5 - Release, installed acceptance and closure

- [ ] Verify containing normal Main release and exact released behavior.
- [ ] Obtain any required selected-host rollout approval, use the canonical
  updater, and validate a fresh native launch/exit. Existing sessions may
  still carry their older wrapper and are not rollout evidence.
- [ ] Execute the explicitly authorized selected-host [live round trip](live-roundtrip.md):
  protected connectivity and consistent backups first, attributable removal,
  recorded clean middle, reinstall/state reconciliation and same-session proof.
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
  waiter, helper polling loop or unowned detached process replaces the removed
  coupling. Sparse native agent standby prompts are a separate maintenance
  behavior, not a resident session-lifetime dependency.
- [ ] The same running agent receives usable guidance before an intentional
  outage, detects missing tools, reads the retained YAML banner natively, pauses
  affected operations and waits without repair or dependency on the removed
  payload, service, binstub or credential broker.
- [ ] In isolated Windows/Linux maintenance controls, the reviewed single native
  standby poll reports material changes without a helper daemon or busy loop.
  Unavailable native scheduling is explicit rather than silently substituted.
- [ ] An active, stale, malformed, unreadable or replaced window cannot
  accidentally release an established hold. Applicable clearance ends only the
  owned standby poll; restored capability is verified before continuation,
  independent disables stay disabled, and unknown effects are not replayed.
- [ ] Existing exit-noise controls still pass: successful finalization stays
  quiet, cleanup remains a bounded summary and updated batch tails remain valid.
- [ ] Normal release, explicitly approved selected-host installed validation,
  durable evidence and final effort review pass before declaring Done.
- [ ] Selected live host: verified restorable backups precede removal;
  SSH-Gateway and the same mux/Copilot processes remain usable at every phase;
  owned runtime/services are absent at the clean middle with explicit protected
  exceptions; reinstall preserves durable state and pending-effect custody.

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

The maintenance strategy consumes the existing shared banner and normally
projected conduct guidance. Its outage explanation and hold remain readable
outside removable supporting installations. It adds no new execution authority,
and does not delay final Copilot exit indefinitely merely to deliver a lifecycle
notice; the existing bounded/quiet absent-handler contract remains intact.

## Journal

### 2026-10-26 - Live charter reviewed; runtime coupling confirmed

- #9074 merged as `bad5db98a52f844d614832b43b1c0cc310dbf4f1`
  after exact-head Copilot approval with no findings and green required CI.
  Managed completion used the normal Maintainer CODEOWNER-review exception;
  no CI gate was bypassed. Re-read the parent plan: the effort remains Active.
- Read-only live process ancestry and the refreshed source trace identify
  nested `launch_seed_exec` waiters below the existing Windows pane wrapper.
  Preserve those ancestors rather than terminate them to manufacture removal.
  The launch-contract inventory now includes both seed/setup boundaries.
- Normal admitted promotion run `40308349413` failed artifact construction:
  its pinned helper rejected `setup.py`. The existing source/helper-coherence
  repair #9061 owns that failure class; sent the exact normal-lane evidence
  there rather than opening a competing implementation. A later duplicate
  admission refusal is not the underlying build failure or permission to retry.
- A first bounded backup attempt did not complete and is not accepted evidence.
  The revised task-specific backup has native Windows WAL/restore and
  whole-command synthetic tests, explicit reconstructible-runtime exclusions,
  and progress/entry journaling. Live backup acceptance remains pending.
  No host services or installations have been stopped or changed.

### 2026-10-26 - Selected live-host round trip authorized

- The operator selected one live Windows test host and explicitly authorized
  audit, state separation, backups, careful uninstall/reinstall, re-release and
  necessary Main-promotion repairs until a real clean-middle round trip works.
  Preserve SSH-Gateway connectivity and muxed Copilot; no other host is covered.
- Added the generic live-roundtrip charter and validation obligations for
  review before effects. Read-only preflight reached the declared host through
  its existing Gateway forward; the remote shell is not elevated.
- No host files, services, installations or maintenance banner have been
  changed. The supported Manager uninstall prerequisite is source-merged;
  the complete parent process-survival and maintenance-standby gates remain open.

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

### 2026-10-25 - Native argument prerequisite merged

- #8906 merged as `bea4d7bb70c2ec727b2f38caeff9ec84eaca26c2` after
  exact-head approval without findings and required green CI. The fresh
  Windows run `40173234492` exercised published source
  `cbd2966ed527b29cc37579685e8e6d2b0a4993d6`: all three real PSMux
  controls passed without skips, including both file-based producers and
  owned teardown. The rebased ordinary pane packet also passed 53 tests;
  both workflow-contract checks and the install-contract guard passed.
- Re-read the parent plan after merge. This closes only the literal Windows
  argument-delivery prerequisite. It does not implement the copied shell,
  current start/end handlers, actual two-product removal, update survival,
  release or approved selected-host validation.
- The first supported Manager uninstall implementation is under integration
  review. Its focused ownership/custody controls pass, but real installed
  removal on Linux/Windows, mixed-version controls and full assembly
  validation are still required before returning to the interactive baseline.

### 2026-10-25 - Priority resumed with maintenance-window intent

- The operator prioritized installation-independent sessions and added
  local-machine maintenance notification, native standby and resumption after
  the YAML window clears. The literal follow-up is in the inception transcript.
- Reconciled the canonical source workspace through guarded sync, preserving
  the committed parent journal. Found #9036's already merged JSON-only conduct
  guidance and #9042's reviewed disposable-runtime/shared-YAML vision and plan.
  Reuse those owners and contracts; do not create another banner or protocol.
- The expanded Plan and Validation Plan connect actual process survival to
  tool-independent banner reading, preserved obligations, sparse native standby
  and safe capability revalidation after clearance. Normal guidance delivery
  and native behavior still need proof; reviewed intent alone is not acceptance.
- Supported Manager uninstall remains a prerequisite. Its three review fixes
  are committed locally but unpublished; the Windows synthetic-payload fixture
  timeout and revised native qualification remain unresolved. This update
  neither declares them passing nor authorizes host maintenance or rollout.

### 2026-10-24 - Uninstall contract reviewed

- #8871 merged after an exact-head Copilot approval with no open issues and
  successful required CI. Managed completion used the live-confirmed
  Maintainer exception for an additional CODEOWNER approval; no CI or reviewer
  finding was bypassed.
- Re-read the parent plan and prerequisite after merge. #8864 remains open:
  the contract is reviewed, but implementation, actual Linux/Windows removal
  and the parent installation-survival acceptance are still outstanding.
- Next, inventory the concrete installer/ownership/lifecycle seams and
  implement the smallest supported removal operation. No operator-host
  uninstall, live-session intervention or rollout follows from plan approval.

### 2026-10-26 - Maintenance integration intent merged

- #9045 merged as `2687e35fa9216bb93c0907d4547d5f1b264e8ae2` after
  exact-head Copilot approval with no findings and required green CI. The
  managed merge used the repository-authorized exception for an additional
  CODEOWNER vote, not a waiver of findings or status checks.
- Re-read the canonical Plan and Validation Plan after guarded pull-forward.
  The maintenance extension is reviewed intent only: normal YAML guidance
  delivery, native standby and safe resumption remain unchecked, as do the
  original actual two-product session-survival gates.
- Resume the supported Manager uninstall prerequisite first. Preserve its
  three review fixes while reconciling current native qualification portfolios;
  resolve the real test failure and rerun changed-head native acceptance before
  returning to production wrapper/current-handler migration.

### 2026-10-26 - Supported Manager uninstall source merged

- #8955 merged as `c2cc00e92b47f1e9036b3ae4d9e8f329ee796309` after
  exact-head Copilot approval, resolution of every review thread and required
  green CI. The metadata-triggered CI run also passed before managed completion;
  the repository-authorized additional-CODEOWNER exception did not waive CI.
- Qualified source `0d78da44bffbb5120fc45d4b1e0fcefc1d21f9d6` passed
  203 affected Windows tests with three POSIX-only skips, all 206 Linux tests
  without skips, and actual installed daemon/configuration-list/removal/reinstall
  flows on both platforms. The fresh Windows lane was `40292624279`; the
  Linux native test passed in 58.20 seconds. Artifact and installed/source
  identities, durable-state bytes and exact disposable cleanup were verified.
- This closes the supported-uninstaller source prerequisite only. Normal
  containing release, approved installed acceptance, actual same-process
  interactive two-product survival, copied wrappers/current handlers and YAML
  maintenance standby remain open.
- Re-read the canonical Plan and Validation Plan. Return to the real
  interactive baseline and current launch-contract inventory, preserving
  #5579's forwarding ownership. Do not treat this merged prerequisite as
  completion or retire the continuing parent worktree.

### 2026-10-26 - Real interactive baseline and read-only probe repair

- The supported Manager uninstaller from #8955 is merged. A disposable ARM64
  Linux container installed Manager and Worktrees non-editably at merged
  source `e4dcb7226c2b928d53f4cd0ba184b1f1619d7ee5`, then ran the actual
  installed Manager launch script with a terminal attachment. The observed
  mux server, installed pane shell and native Copilot remained live while a
  literal no-tools witness answered the initial readiness prompt.
- The uninstall preview refused before reaching quiescence. The launch-time
  maintenance policy probe had created three bytecode files and their cache
  directory inside the sealed Manager payload. Original file bytes and
  inventory identities were unchanged. Do not weaken the inventory check,
  delete the cache manually, or label that refusal a survival pass.
- The bounded repair disables bytecode writes in the probe before its first
  Manager import. Four subprocess controls exercise absent, enabled, disabled
  and malformed policy twice with the runner's bytecode environment ignored.
  They preserve payload bytes and the existing exit codes and diagnostics.
- Extended native uninstall acceptance executes the real installed probe
  with an external base interpreter and no bytecode-disable flag, compares
  the sealed slot, and continues through actual removal and reinstall.
  The native Windows cycle passed, including daemon start/stop, configuration
  listing, unchanged durable data and idempotent repeated removal; the
  combined bounded assertion run passed 32 tests.
- The initial source-install clean-room failed because its base image lacked
  the C compiler required by the native process helper. Providing the
  explicitly recorded build prerequisites inside that disposable fixture
  allowed the real installation. That is not pristine-image acceptance.
- Actual removal of both systems around the interactive session, a copied
  installation-independent shell, replacement-handler exit, the full suite,
  shared YAML standby, native Windows survival and released/installed
  acceptance remain open. The next slice resumes that same real-removal
  baseline after the read-only probe repair.

### 2026-10-26 - Actual Linux round trip and whole-suite prerequisites

- #9075 merged as `144183e71d55a22088b3d1082ac2dac155908190`.
  A new disposable installation at that exact merged source passed the real
  read-only launch probe, authenticated metadata-daemon shutdown, supported
  Manager removal and Worktrees removal, followed by actual reinstall.
  The same five mux, wrapper, launch and Copilot process incarnations survived.
  The native assistant transcript records responses before removal, while both
  primary commands and installed generations were absent, and after reinstall.
- Six durable files remained byte-identical. The project registry changed
  only its normal registration timestamp; all other parsed registry data
  remained identical. Worktrees retained generated helpers and snapshots, and
  the resident launch interpreter remained alive. This is a bounded two-product,
  same-source Linux baseline, not whole-suite removal, copied-wrapper,
  replacement-generation exit, native Windows or installed-release acceptance.
- The same surviving session then received all 13 declared runtime adopters
  plus Manager from that merged source. Every selected runtime identified the
  exact source commit and had completion evidence. The same five process
  incarnations remained unchanged, and the native transcript recorded another
  no-tools assistant response after installation.
- Actual supported Index uninstall returned success and removed its command,
  but retained generations, snapshots and selectors. Other prerequisite
  source inspections found missing legacy bootstrap uninstall actions and
  default state-erasing removal paths. These are removal gaps, not a reason
  to substitute manual directory deletion or claim the middle state clean.
- The bounded Logger repair removes only declared reconstructible children
  under its existing install transaction, preserving configuration, digests,
  databases, session state and unknown root content. It validates deployment
  or in-root stamped ownership before removing tasks or commands; foreign,
  malformed, missing and externally redirected ownership refuses explicitly.
  Native Windows deployed/stamped ownership and idempotence controls pass,
  as do the affected existing Logger installer tests and isolated POSIX
  controls. Whole-suite native removal remains the next acceptance gate.
- #1886 retains the symmetric harness-wide declaration, shared-reference and
  confirmed plan/apply contract. Per-product prerequisites do not replace that
  owner. Containers' runtime-hosted SSH broker and Machines' admission boundary
  require explicit retention/quiescence handling before their code can be
  removed; transport, credentials and external resources are not inferred to
  be disposable from a product name.
- Keep this effort Active. Continue the supported per-product removal
  prerequisites, then the real all-absent/reinstall witness around this same
  session. The protected transport closure, launch-owned shell/current hooks,
  shared YAML maintenance admission and native standby remain unresolved.
- The prerequisite review exposed and corrected literal child-name matching,
  same-product foreign ownership, active Logger syncs, persistent snapshot
  coordination and retry authentication after partial cleanup. Native
  task/process/custody controls and isolated Linux integration cover these
  boundaries. This adds no whole-harness reference-accounting or abrupt-death
  recovery claim; the real all-absent witness remains open.

### 2026-10-26 - Removal foundation merged, portfolio integration continues

- #9087 merged as `6ab06aa912e5b243ca6f8cebd5a3b489f04fc2bf` after
  current-head review without open findings and required green CI. The
  [source qualification record](<../source-managed-runtime-installs/journal/2026/10.26 runtime-removal.md>)
  separates the actual Windows/Linux packets from live-session acceptance.
- Selective native cleanup, finite child settlement and the Logger/MCP
  prerequisites now preserve default durable/unknown state and coordination.
  Root aliases, Windows filesystem casing, active consumers, historical
  registrations and retry ownership were corrected rather than represented
  as successful removal. The container terminal-process assertion has a
  bounded portability follow-up under this effort.
- Re-read the Plan and Validation Plan after merge. Actual all-absent removal
  and reinstall around the same session remains open, as do complete default
  adapter coverage, protected SSH/Gateway closure, copied wrappers/current
  boundary handlers, native Windows survival, YAML standby and released
  installed acceptance. No checkbox for those gates changes here.
- Continue the remaining adapter integration through the existing ownership
  boundaries, then return to the preserved live baseline. This effort remains
  Active; a merged removal prerequisite does not retire its responsibility.
