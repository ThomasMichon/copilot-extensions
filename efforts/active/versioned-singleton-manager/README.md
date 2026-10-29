# Versioned Singleton Manager

- **Slug:** `versioned-singleton-manager`
- **Repo:** copilot-extensions
- **Branch(es):** Serial pull requests against `dev`.
- **Created:** 2026-10-08
- **Status:** Active
- **Vision:** `visions/plugin-services` / `cutover-coherent-service-tracking`
  and `zero-downtime-cutover`. Vision-closing.
- **Umbrella issue:** [#5655](https://github.com/ThomasMichon/copilot-extensions/issues/5655)
- **Related:** [design PR #5556](https://github.com/ThomasMichon/copilot-extensions/pull/5556),
  [supervisor restart accounting #5574](https://github.com/ThomasMichon/copilot-extensions/issues/5574).

## Guiding Intent

Keep native service-manager tracking coherent while a versioned daemon cuts
over to a replacement. A reviewed design is not a running implementation:
complete the shared primitive, consumer integration, and real platform proof
before declaring the capability delivered.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Implementation driver | Shared primitive, consumer integration, serial PR ownership | Owning implementation worktree |
| Linux validation venue | Real process boundaries and systemd restart accounting | Isolated systemd-capable test container |
| Windows validation venue | Native Job and Scheduled Task proof | Dedicated Windows test venue |

## Coordination

The complete #4022 family below continues through this existing Active ROOT's Plan,
actual global Validation and Journal under the SAME declared implementation, source,
adopter and native custodians. Closed source trackers are historical provenance, not
required live claim tokens. Every incumbent scope, alternative, hold and resource
obligation remains; this intake grants no implementation, deployment, process action or
new blanket approval gate.

- **Topology:** Serial, independently reviewable slices.
- **Host (owns PRs):** Implementation driver.
- **Delegates:** Bounded evidence or test work only; no concurrent edits to
  shared primitive or consumer callback contracts.
- **Handoff:** Preserve this README, the current phase, open PRs, and test
  evidence. Resume the first unresolved Plan/Validation Plan item.

<!-- backlog-reconciliation:7556:first-ten:Coordination -->

### Continuing source-specific #876 custody

- **C.876.complete-retention:** Keep the ACTUAL incumbent current assigned876 owner and
  installer/runtime/task/daemon/elevation, shared manager/adopter and native validation
  authorities. The receiving ROOT author/driver is not appointed executor by this
  document. Concrete overlapping source edits retain their original agreement/safety
  boundaries; no blanket ACK, silence consent or age expiry is added.

- **C876.assignment:** The actual #876 assignee remains ThomasMichon. Preserve the
  source-specific same-assignee Dev-delivery/unconditional-completion caveat for a
  completed-disposition claim. This proposal is neither that delivery nor a source
  judgment.

## Context

The [versioned singleton manager pattern](../../../docs/patterns/versioned-singleton-manager.md)
defines the intended shared capability. An early prototype exists outside the
published source and predates much of the design. It is reusable evidence,
not accepted implementation or current platform validation.

`CutoverOrchestrator` invokes adopter-owned `spawn_passive` callbacks; it does
not create the processes itself. Integration must cover the dispatch
coordinator and bridge venue callbacks and their launchers, not only `zdd`.
Windows must remain explicitly unavailable until its native ownership and
handoff implementation passes its platform gate.

## Request

Public-safe summary of the continuing request: implement the reusable
versioned singleton manager, wire it into daemon supervision, and prove
cutover/crash behavior with unit tests, stress tests, a systemd container,
and a real Windows host. Continue until the implementation and validation
are complete, rather than stopping after the design PR.

## Plan

### Phase A - Shared identity and Linux manager

- [x] Recover and assess the prototype; reuse only mechanisms that meet
  the current contract. The old best-effort identity and PPID-only paths
  are not reused as a production backend.
- [x] Add backward-compatible publication-time identity. Landed in
  [PR #5803](https://github.com/ThomasMichon/copilot-extensions/pull/5803),
  including preservation through rollback/watchdog promotion.
- [x] Require the published baseline and proven ownership before adopting
  successors; reject unverifiable candidates rather than sampling a new
  identity from an already-reused PID. Implemented by the Linux library slice;
  Windows custody and production launcher integration remain separate gates.
- [x] Implement distinct routing and manager-state paths, atomic versioned
  state, singleton ownership, ancestry checks, stable identity baselines,
  zombie reaping, bounded successor discovery, and crash cleanup. Landed by
  [PR #5821](https://github.com/ThomasMichon/copilot-extensions/pull/5821).
- [x] Implement marker-driven Linux re-exec while polling the child; restore
  the validated watched process without invoking `spawn` a second time.
  Preserve pending cutover discovery across the exec boundary. Landed by
  PR #5821.
- [x] Keep unsupported-platform handling explicit and fail closed; no
  best-effort Windows facade over the superseded PPID-walk prototype. Landed
  by PR #5821.
- [x] Synchronize vendored consumers and add the required changefiles.
  Landed by PR #5821.

### Phase B - Windows ownership and handoff

- [ ] Implement typed native-handle ownership and a shared suspended-spawn
  registration primitive. Cover the first daemon, every passive generation,
  and the breakaway deploy orchestrator; no escaped intermediary may evade
  manager-crash cleanup.
- [ ] Define authenticated, reconnectable registration endpoints across daemon
  and manager generations; bind peer identities and handle rights rather
  than trusting claimed numeric handle values or same-user access alone.
- [ ] Implement Job configuration, containment verification, explicit
  re-assignment, scoped handle inheritance, and failure cleanup.
- [ ] Implement a durable phased handoff, serialized with fresh startup and
  terminal cleanup. Persist preparation before a bridge can preserve the
  Job; use a kernel synchronization primitive, not assumed unschedulable
  adjacent filesystem/handle operations.
- [ ] Preserve trusted daemon custody through the bridge, a bounded
  ownership-transfer deadline, and fail-closed cleanup even after a partial
  successor acquires its own Job handle.
- [ ] Re-run the registered task after successful intentional retirement;
  account for `IgnoreNew`, native task-state lag, and repeated updates without
  consuming crash-restart accounting.

### Phase C - Consumer integration

- [ ] Integrate both adopter-owned passive-spawn callbacks with the shared
  contract; preserve unmanaged launch behavior explicitly.
- [ ] Wire stable Linux launchers/systemd units and attached, windowless
  Windows Task actions to the manager; preserve normal endpoint discovery,
  environment snapshots, and version resolution.
- [ ] Align pattern documentation with the implemented contract, removing
  contradictory trust or cleanup claims without weakening the vision.
- [ ] Resolve #5574 or explicitly transfer its separate validation obligation
  to a named tracked objective before closing this effort.

### Phase D - Platform proof and release

- [ ] Land each slice through normal review; record actual merged outcomes.
- [ ] Run isolated systemd and native Windows proofs, including installer
  launch shapes, not only mock/native helper tests.
- [ ] Verify release and consumer deployment under the normal update flow;
  retain all required approval gates for live service changes.

<!-- backlog-reconciliation:7556:transfer:4022:to:efforts/active/versioned-singleton-manager/README.md -->

### Direct retained acceptance from #4022: native tracking and register-once launcher

This additive intake preserves the ENTIRE incumbent ROOT Plan, completed work,
Validation Plan, Participants, Coordination, dependencies and append-only Journal. It
transfers tracker scope only after new normal reviewed delivery; it declares no runtime
capability, installation or platform complete. Existing #5225 Linux-observation, #5655
shared-manager and #5938 other-POSIX-service custody remain separate and unchanged. The
original source, additive register-once comment and full retained checklist remain
authoritative at their original bounds.

#### Plan

- [ ] P1. Register the least-privileged native supervisor ONCE against a stable
  attributable launcher that dynamically resolves the selected immutable generation.
  Ordinary version/configuration changes must not rewrite/re-register the unit/task or
  renew elevation. Qualify actual generated values, not the current stable-link comment
  over a concrete versioned path.
- [ ] P2. After spawn-new-retire-old cutover, retain or transfer the native manager's
  tracked identity and restart/failure accounting through a proven native handoff OR a
  stable manager/keeper. A healthy route/PID/port, whitelisted old exit, or inactive old
  unit plus detached healthy successor is not coherent tracking. Do not substitute
  advice to ignore native status.
- [ ] P3. Integrate the ACTUAL Bridge installer/launcher and adopter-owned passive-spawn
  paths, preserving correct source, configuration, environment and namespace identity.
  Cover BOTH real systemd MainPID/cgroup/restart and actual Windows Scheduled
  Task/Job/handle/lifetime semantics; leave unsupported/unqualified native capability
  explicit. Existing shared Linux library and Windows leave-existing-registration
  behavior are credited but not whole consumer/native adoption.
- [ ] P4. Preserve passive singleton etiquette, work/drain boundaries, exact
  process-birth and full generation identity, current admission and one existing
  mutation authority. Unknown/stale route or manager evidence cannot authorize
  killing/restarting a healthy successor or adopting another process. Preserve preferred
  compatible handoff AND accepted bounded cold takeover/recovery; no universal
  zero-outage or incoming-ready-before-every-retirement gate.
- [ ] P5. Carry native tracking and stable registration through ordinary update,
  rollback/recovery, repeated/coincident cutover, manager death, failed/partial adoption
  and interrupted transition. Preserve original unresolved-operation ownership, durable
  recovery and all existing native-platform gates.
- [ ] P6. Make native-command, registration, permission, launch and adoption failures
  explicit through the actual consumer. Static files, a standalone helper, mocked OS
  state, successful old exit or route readiness alone cannot claim native acceptance.
  Normal source/release/installed qualification and existing live-change permissions
  remain distinct.
- [ ] P7. Keep #5225's exact Linux healthy-successor/inactive-manager observation,
  #5655's shared primitive/integration and #5938's other-service register-once work
  coordinated without narrowing Windows, moving any claimant or closing an adjacent
  tracker. Preserve every current textual/nonexpiring claim, resource hold and native
  validation owner; age, missing local task or no assignee is not release authority.
- [ ] P8. Journal new actual delivery at each finite boundary. Preserve all current ROOT
  Plan/Validation/Journal content and original claimants. This annex is NOT received
  merely because #5801 created the ROOT or a new patch exists. Keep #4022 OPEN until a
  NEW normally reviewed PR includes this COMPLETE direct intake and the actual global
  Validation entry below, actually merges, and the WHOLE current ROOT is read back with
  every clause/owner intact.

#### Validation

- [ ] V1. Through actual normal installer/update/configuration consumers, verify the
  native definition/action stays stable across successive immutable generations and
  configuration changes, resolves the selected attributable generation, remains least
  privileged and requires neither ordinary re-registration nor renewed elevation.
  Include real emitted launcher values and explicit native-command errors.
- [ ] V2. Real systemd proof: the manager's MainPID, cgroup, service state and
  restart/failure accounting stay coherent across repeated detached-successor cutovers.
  A live external endpoint while the unit is permanently inactive fails the original
  outcome. Include intentional retirement versus genuine crash and hard manager-death
  cleanup/recovery.
- [ ] V3. Native Windows proof: actual Scheduled Task state and attached/windowless
  action, Job containment, scoped handle custody/inheritance and lifetime retain the
  selected successor across consecutive daemon/manager updates. Missing readiness,
  task-state lag, IgnoreNew, partial transfer and rejected assignment cannot leave an
  indefinitely untracked live daemon.
- [ ] V4. Verify passive callbacks and native launchers together under update,
  rollback/recovery, coincident cutover, manager death and partial/failed adoption,
  including process/PID reuse, missing or changed birth identity, foreign routes and
  malformed/unknown manager state. Refuse unproven adoption/termination while preserving
  the original operation's custody.
- [ ] V5. Exercise compatible handoff/drain and admitted bounded cold takeover
  separately. Cold predecessor retirement may precede incoming readiness where
  necessary; success still requires truthful post-recovery readiness and coherent native
  tracking, with no displaced-owner resurrection or concurrent exclusive authority.
  Durable-state/rollback compatibility and verified retained recovery boundaries remain
  the existing owners' gates, not a new blanket availability promise.
- [ ] V6. Distinguish actual source/library delivery, containing release, installed
  consumer and real native/platform acceptance. Preserve the incumbent ROOT's own
  failure, stress, consumer, release/deployment and required approval gates. A source
  helper, static generated file, successful route or unrelated host update is not the
  actual platform outcome. Do not require unrelated whole-program/all-host completion
  beyond the original supported native families and existing ROOT scope.
- [ ] V7. Before administrative supersession, read the full actual merged current ROOT
  and verify P1-P8, V1-V7, C1-C4 and the ACTUAL global Validation entry below, together
  with all incumbent content/owners. Missing clause, owner, original alternative,
  evidence-tier or global entry means retain #4022; no title/link/child/Draft receipt or
  old creation merge substitutes.

#### Continuing Coordination

- [ ] C1. The existing implementation driver retains shared primitive/consumer
  integration and normal PR stewardship. Existing Linux/systemd and native Windows
  validation owners retain their exact lanes. Actual adopter passive-callback and
  launcher owners remain responsible for their current resources and native admission;
  this text creates no replacement controller or implementation/rollout grant.
- [ ] C2. Preserve #5225, #5655 and #5938 current custody and any new legitimate
  assignee/textual claim appearing before publication. Their references are coordination
  obligations, not instructions to close, unassign, expire a reservation, restart a
  service or release resources. Source #4022 is currently unassigned; that does not
  waive future or textual custody.
- [ ] C3. Keep serial single-writer boundaries for shared primitive/state/callback
  contracts and retain existing completed Linux identity/library progress. Bounded
  source/platform evidence returns to the same driver. Preserve current open PRs, native
  holds, unresolved effects and separate source/release/installed/native facts in the
  continuing Journal.
- [ ] C4. The integration owner must obtain a NEW normal reviewed actual merge
  containing this exact complete intake and actual global Validation entry, then verify
  the WHOLE current ROOT before source supersession. The original runtime outcome
  remains unchecked in the receiving Plan/Validation. No new blanket ACK is required, no
  original source-specific owner condition is weakened, and no third-party custody or
  unrelated original is moved.

Public provenance: https://github.com/ThomasMichon/copilot-extensions/issues/4022 ;
https://github.com/ThomasMichon/copilot-extensions/issues/4022#issuecomment-6068258631 ;
https://github.com/ThomasMichon/copilot-extensions/issues/4022#issuecomment-6318439874 ;
https://github.com/ThomasMichon/copilot-extensions/pull/5801 ;
https://github.com/ThomasMichon/copilot-extensions/pull/5803 ;
https://github.com/ThomasMichon/copilot-extensions/pull/5821 .

<!-- backlog-reconciliation:7556:first-ten:Plan -->

### Direct retained acceptance from #876

_Agent-recommended additive tracking proposal; original source requirements,
alternatives, finite proof tiers and custody remain binding._

These rows retain work rather than certify it complete. Existing Request,
Status, Participants, Plan, global Validation, Journal, checked credit and
first unresolved gates remain intact. Review of this amendment is document
intake only, not implementation, native/installed proof, source closure,
owner reassignment or claim/resource release. A separate source judgment
must preserve each literal source-specific hold; no generic ACK, every-host
or whole-parent completion gate is added.

#### Source #876

- [ ] **P.876.diagnose-never-ran:** Diagnose267011 SCHED_S_TASK_HAS_NOT_RUN after a
  requested Windows Scheduled Task start; task Ready/presence or a Start-ScheduledTask
  call without actionable error is not actual action launch. Preserve the domain/AAD and
  unavailable-domain-controller boot scenario as original finite context.

- [ ] **P.876.hypothesis-and-diagnostic:** Explain LIKELY S4U token-acquisition failure
  and the interactive-logon alternative without asserting an unobserved native root
  cause; later successful starts or normal-user daemon health do not prove the
  elevated/principal path or original failure cause.

- [ ] **P.876.mode-intent-and-consented-repair:** When selected/default mode is
  interactive, migrate or explicitly override an unusable preserved S4U configuration
  without changing INTENTIONAL non-interactive S4U/Password installations. Preserve
  ordinary write-once behavior and explicit actual repair/elevation consent, not routine
  task churn or a silent privilege-changing retry.

- [ ] **P.876.original-tests:** Preserve original mode-resolution/diagnostic tests plus
  the separately required directly inherited1030 native truthful-repair cases, with real
  principal/trigger/action/mode/activation and honest status/exit tiers; no new all-host
  or whole-parent test programme.

- [ ] **P.876.retained-union:** Retain the complete CURRENT876 original, BOTH
  refinements and directly inherited1030 contract, all four original Expected outcomes
  and five native checklist rows, actual assignee and
  installer/runtime/task/daemon/elevation/resource boundaries. Preserve specific
  assigned-completed-disposition caveats without converting them into universal ACK or
  technical-completion-before-any-administrative-retention gates.

- [ ] **P.876.inherited-01:** Distinguish runtime installation, startup-task
  registration and daemon start. Access denied or declined repair must not certify
  success; optional degraded operation must be explicit.

- [ ] **P.876.inherited-02:** Preserve intended non-interactive/headless task mode,
  ordinary write-once behavior and explicit consent/elevation for repair; no routine
  task churn.

- [ ] **P.876.inherited-03:** Verify user/principal, logon mode, trigger and stable
  action, not task presence alone. Normal-user daemon health does not prove the elevated
  startup path.

- [ ] **P.876.inherited-04:** Fence owned stale routes after failed startup instead of
  reporting them current.

- [ ] **P.876.inherited-05:** Qualify native denied-UAC/access-denied, misleading
  scheduler-success, principal/action mismatch and healthy existing-task cases,
  including truthful status and exit results.

## Validation Plan

- [ ] Complete EVERY directly retained #4022 P1-P8, V1-V7 and C1-C4 clause above at its
  original scope/evidence tier, preserving incumbent Plan/Validation/Journal, native
  families, accepted alternatives, all current owners/claims/holds and adjacent
  #5225/#5655/#5938 coordination. Tracker consolidation is not implementation,
  deployment or native acceptance; actual complete reviewed merge and whole-current-root
  readback precede source supersession.

- [ ] Unit proof: stale PID, missing identity, same-PID replacement between
  polls, publication/registration races, malformed state, and foreign routes
  never authorize adoption or termination of unrelated processes.
- [ ] Real Linux proof: repeated detached descendant cutovers, intermediate
  exits, child zombies, exec during cutover, and no duplicate spawn on exec.
- [ ] Crash/stress proof: bounded orphan cleanup, repeated/coincident
  cutovers, injected manager death, and cleanup while descendants fork.
- [ ] Real systemd proof: service MainPID remains coherent across cutovers;
  hard manager death leaves no old cgroup survivor before restart.
- [ ] Native Windows proof: consecutive daemon and manager updates retain
  Task tracking and Job containment; inherited/registered handles do not leak.
- [ ] Windows failure proof: missing readiness, interrupted preparation,
  bridge death, rejected Job assignment, task-state lag, partial adoption,
  and deadline expiry cannot leave an untracked live daemon indefinitely.
- [ ] Focused adopter regressions, vendored consistency, and required guards
  pass under the bounded test runner.
- [ ] Every platform claimed production-ready has real platform evidence;
  unresolved items remain open or use an explicit named transfer.

<!-- backlog-reconciliation:7556:first-ten:Validation -->

### Global validation of retained #876

#### Source #876

- [ ] **V.876.diagnose-never-ran:** Qualify the COMPLETE matching Plan clause at its
  ORIGINAL scope, alternatives, supported native venue and finite bounds; preserve
  current finite credit and same source/resource custody: Diagnose267011
  SCHED_S_TASK_HAS_NOT_RUN after a requested Windows Scheduled Task start; task
  Ready/presence or a Start-ScheduledTask call without actionable error is not actual
  action launch. Preserve the domain/AAD and unavailable-domain-controller boot scenario
  as original finite context.

- [ ] **V.876.hypothesis-and-diagnostic:** Qualify the COMPLETE matching Plan clause at
  its ORIGINAL scope, alternatives, supported native venue and finite bounds; preserve
  current finite credit and same source/resource custody: Explain LIKELY S4U
  token-acquisition failure and the interactive-logon alternative without asserting an
  unobserved native root cause; later successful starts or normal-user daemon health do
  not prove the elevated/principal path or original failure cause.

- [ ] **V.876.mode-intent-and-consented-repair:** Qualify the COMPLETE matching Plan
  clause at its ORIGINAL scope, alternatives, supported native venue and finite bounds;
  preserve current finite credit and same source/resource custody: When selected/default
  mode is interactive, migrate or explicitly override an unusable preserved S4U
  configuration without changing INTENTIONAL non-interactive S4U/Password installations.
  Preserve ordinary write-once behavior and explicit actual repair/elevation consent,
  not routine task churn or a silent privilege-changing retry.

- [ ] **V.876.original-tests:** Qualify the COMPLETE matching Plan clause at its
  ORIGINAL scope, alternatives, supported native venue and finite bounds; preserve
  current finite credit and same source/resource custody: Preserve original
  mode-resolution/diagnostic tests plus the separately required directly inherited1030
  native truthful-repair cases, with real principal/trigger/action/mode/activation and
  honest status/exit tiers; no new all-host or whole-parent test programme.

- [ ] **V.876.retained-union:** Qualify the COMPLETE matching Plan clause at its
  ORIGINAL scope, alternatives, supported native venue and finite bounds; preserve
  current finite credit and same source/resource custody: Retain the complete CURRENT876
  original, BOTH refinements and directly inherited1030 contract, all four original
  Expected outcomes and five native checklist rows, actual assignee and
  installer/runtime/task/daemon/elevation/resource boundaries. Preserve specific
  assigned-completed-disposition caveats without converting them into universal ACK or
  technical-completion-before-any-administrative-retention gates.

- [ ] **V.876.inherited-01:** Qualify the COMPLETE matching Plan clause at its ORIGINAL
  scope, alternatives, supported native venue and finite bounds; preserve current finite
  credit and same source/resource custody: Distinguish runtime installation,
  startup-task registration and daemon start. Access denied or declined repair must not
  certify success; optional degraded operation must be explicit.

- [ ] **V.876.inherited-02:** Qualify the COMPLETE matching Plan clause at its ORIGINAL
  scope, alternatives, supported native venue and finite bounds; preserve current finite
  credit and same source/resource custody: Preserve intended non-interactive/headless
  task mode, ordinary write-once behavior and explicit consent/elevation for repair; no
  routine task churn.

- [ ] **V.876.inherited-03:** Qualify the COMPLETE matching Plan clause at its ORIGINAL
  scope, alternatives, supported native venue and finite bounds; preserve current finite
  credit and same source/resource custody: Verify user/principal, logon mode, trigger
  and stable action, not task presence alone. Normal-user daemon health does not prove
  the elevated startup path.

- [ ] **V.876.inherited-04:** Qualify the COMPLETE matching Plan clause at its ORIGINAL
  scope, alternatives, supported native venue and finite bounds; preserve current finite
  credit and same source/resource custody: Fence owned stale routes after failed startup
  instead of reporting them current.

- [ ] **V.876.inherited-05:** Qualify the COMPLETE matching Plan clause at its ORIGINAL
  scope, alternatives, supported native venue and finite bounds; preserve current finite
  credit and same source/resource custody: Qualify native denied-UAC/access-denied,
  misleading scheduler-success, principal/action mismatch and healthy existing-task
  cases, including truthful status and exit results.

- [ ] **GV.876.complete-retention:** Complete EVERY matching retained 876
  Plan/Validation clause, complete original/refined/inherited union and continuing
  source/resource conditions at the ORIGINAL finite scope. Preserve all incumbent ROOT
  Plan/Validation/Request/Participants/checked credit/Journal. This paper intake is not
  technical completion, native/release/installed proof, source closure or claim
  transfer.

## Proposal

Serial Linux-first delivery, followed by native Windows implementation and
both consumer integrations. State-machine and source-identity refinements
above are **agent-recommended implementation hardening**, not new operator
requirements or permission to weaken the existing vision.

## Journal

### 2026-10-08 - Implementation resumed

- Design PR #5556 is merged; #5655 remains open.
- Established this target-local implementation contract. No manager code
  or platform production-readiness claim is delivered by this proposal.

### 2026-10-08 - Identity foundation landed; Linux manager implementation

- Plan PR #5801 and publication-identity PR #5803 are merged.
- The next slice implements a Linux-only pidfd-backed manager, durable
  state/lease recovery, bounded discovery, and actual re-exec/cutover
  subprocess proof. It does not wire a production launcher or close the
  systemd/Windows validation gates.

### 2026-10-24 - Direct retained acceptance from #4022

- Preserve the complete #4022 original and additive acceptance directly in this ROOT
  Plan, own and global Validation, and continuing Coordination. Credit delivered
  foundations while leaving all product/native/installed outcomes unchecked. New normal
  review, actual merge and whole-current-root readback precede source supersession.
  Existing owners, completed work, alternatives and independent gates remain; the effort
  stays Active.

<!-- backlog-reconciliation:7556:first-ten:Journal -->

### 2026-10-28 - Additive retained #876 contract proposal

Added direct per-source Plan, actual global Validation and continuing custody rows after whole-record and complete receiving-document research. Normal review and current complete receiving readback remain publication gates; this entry records authored tracking only, not a future merge or source disposition. All incumbent finite source/design/release/native credits remain at their actual tiers, with original alternatives, unknowns, source-specific holds and existing source/resource custodians preserved. No implementation, operational effect or claim release is authorized. The campaign's receiving register records delivery and later source judgments separately.
