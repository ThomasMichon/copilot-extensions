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

## Coordination

- **Topology:** serial per-slice PRs; each slice leaves the default branch usable.
- **Host (owns PRs):** Installation-survival driver.
- **Delegates:** none assigned. Any later assignment records exclusive source
  scope and acceptance before edits.
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
  launch consumers; record the smallest compatible boundary and migration.

### Phase 2 - Literal disposable-installation reproduction

- [ ] Review and implement the supported Manager uninstall prerequisite
  (#8864) in [manager-uninstall.md](manager-uninstall.md), then return to this
  same real-removal baseline.
- [ ] Reproduce current behavior with real installed A, an interactive owned
  session, actual uninstallation of both systems, installed B and final exit.
- [ ] Resolve or coordinate #8410 only as the required Linux uninstall
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
