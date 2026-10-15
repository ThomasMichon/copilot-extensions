# Resilient Update Sequencing

- **Slug:** `resilient-update-sequencing`
- **Repo:** copilot-extensions
- **Branch(es):** sequential proposal and implementation PRs against `dev`
- **Created:** 2026-10-15
- **Status:** Active
- **Vision:** [Installer and Configurator](../../../visions/installer/README.md):
  resilient update convergence, historical generated-command migration and
  change-driven local integration
- **Umbrella issue:** #6820
- **Related issues:** #6812, #1238, #1110, #6234, #4876, #6476

## Guiding Intent

Reach a verified good, current installation without recurring manual cleanup or
repeating expensive successful work. Discover cheap blockers first, prepare
expensive and failure-prone inputs early, activate healthy candidates through
short recoverable transitions, and finish with inexpensive local integration.
Ship the historical-command and Terminal profile improvements first rather than
waiting for a complete installer redesign.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Coordinator | Design, implementation, review, integration and authorized verification | Managed upstream contribution worktree |
| Observability owner | Timestamp framing, operation audit and measured work reduction | #6812 |
| Installer owners | Existing candidate construction, admission and activation contracts | #6476 and the vendored-installer-engine effort |

## Coordination

- **Topology:** sequential reviewed slices; the Coordinator owns this effort's
  implementation and completion gate.
- **Host (owns PRs):** Coordinator.
- **Delegates:** bounded non-overlapping implementation or evidence only;
  no child owns the entire campaign by completing one slice.
- **Handoff:** this README and [design](design.md) retain the parent completion
  gate. Record merged work, unresolved dependencies and exactly one next slice.
- #6812 retains telemetry, line framing and its independent audit. This effort
  owns historical command migration, the narrow profile-refresh integration and
  sequencing changes. Coordinate shared update entry points before publication.
- #1238 retains the broader transfer of project-command ownership to Manager.
  The first slice repairs the current owner's migration contract; it does not
  move ownership or rewrite agent invocation policy.
- #1110 retains explicit legacy-to-cell attribution. Same-lineage version
  migration never implicitly activates a cell or claims an unrelated owner.
- #6234 and #4876 retain dependency minimization, compatible reuse and artifact
  acquisition. #6476 and the existing installer-engine effort retain shared
  construction mechanics. Reuse their contracts; do not build another engine,
  artifact resolver, trust root or update scheduler.

## Context

The monitored no-version-change baseline in #6812 took 229.1 seconds, including
153.9 seconds of payload refresh, 21.7 seconds of profile refresh and 26.5
seconds of opaque runtime checks. No venv was built, so those measurements do
not establish changed-version or cold-build cost.

Project command ownership preservation can currently be followed by an
"in sync" message and a successful aggregate exit. The migration recognizer
covers only one old template family, not all attributable historical generated
commands. Profile refresh re-enters the installer self-stage path before
calling Manager, even though Manager already owns the pure fragment generator.

The [design](design.md) defines boundaries, execution order, migration cases,
incremental rollout and the validation matrix. These are proposed changes, not
a statement that prepare-only adapters or suite-wide atomic activation exist.

## Request

Operator direction, verbatim:

> The command-ownersihp refusals need to go away; the existing ones came from previous generations of the harness so cross-version migration was missed and we need automatic cleanup for them. It's nuts that is takes 20 seconds to refresh terminal profiles.
> I assume venv builds are the biggest hit; there should be an effort for that. We really need to sequence things so that the highest-risk, longest-pull activities occur first, so we can fail-fast and offer retry or mitigation. Then we should get more mechanical, quick, and reliable as we continue. So while the other agent builds more telemetry and auduting, help strategize a plan to re-sequence the update for better resilience.

> The binstubs/profiles stuff seems like low-hanging fruit we should knock out. Help sequence an effort and design here (for all the above), get it reviewed, and drive it.

The earlier stated completion requirement is:

> Its goal is always to *get to a good, current state*; giving up or skipping things is bad because an operator won't be around to see what's wrong.

The operator confirmed `resilient-update-sequencing`. Concrete algorithms,
slice boundaries and validation details below are agent-recommended means of
fulfilling that direction, not additional operator requirements.

## Plan

### Phase 1 - Reviewed design and coordination
- [x] Land this effort, design and standing intent through review before code.
- [x] Establish complementary edit boundaries with #6812 and retain neighboring
  effort ownership explicitly.

### Phase 2 - Historical command migration and cheap local finishing
- [ ] Recognize attributable historical generated command/receipt generations,
  refresh same-lineage launchers and retire obsolete owned project commands
  automatically, preserving foreign, edited and ambiguous artifacts.
- [ ] Make command/receipt transitions recoverable and idempotent under the
  existing ownership locks; test Windows sibling files and POSIX launchers.
- [ ] Make unresolved required command reconciliation explicit in update status
  and exit behavior; never call a preserved conflict "in sync."
- [ ] Replace the profile install/stage detour with the Manager's narrow owning
  surface, preserving fragments, state/settings reconciliation and shortcuts.
- [ ] Run local finishing once after relevant update inputs stabilize; avoid
  unchanged writes and unnecessary repeated discovery.
- [ ] Publish measured no-change and migration/profile-repair results, with
  changefiles, review and source/release distinction intact.

### Phase 3 - Risk-first, resumable update execution
- [ ] Collect one attributable desired-state plan and front-load cheap
  deterministic blockers, costly acquisition and dependency admission.
- [ ] Compose existing prepare/validate/activate contracts with the installer
  owners. Do not label a combined build-and-activate installer prepare-only.
- [ ] Introduce a readiness barrier and dependency-aware recoverable activation
  for supported adapters, retaining prior healthy generations and explicit
  unsupported-adapter behavior.
- [ ] Preserve verified target progress and retry transient failures with
  backoff and fresh inspection; keep authority/policy/integrity blockers
  durable and actionable without inventing another scheduler.
- [ ] Adopt reuse and bounded independent preparation only where measured
  benefit and existing isolation/resource contracts justify them.

### Phase 4 - Release, running-state proof and conclusion
- [ ] Verify promoted source contents and perform the authorized local unified
  update; prove command migration, selected runtime health and profile latency.
- [ ] Record no-version-change, changed-version and fresh-path coverage
  separately; do not substitute footprint results for build timing.
- [ ] Resolve or explicitly transfer every implementation and validation item
  to an accepted named objective, update reality docs, archive and finalize.

## Validation Plan

- [ ] Historical receipt/template fixtures cover same-lineage payload relocation,
  known pre-receipt formats, missing/retired projects and knowledge-only commands.
- [ ] Foreign/cross-cell owners, edited bytes, corrupt receipts, ambiguous source
  lineage and case collisions remain protected and explicitly reported.
- [ ] Failure between command and receipt publication, concurrent reconcilers,
  partial Windows siblings, cancellation and retry preserve recoverability.
- [ ] Profile integration crosses the real Manager command boundary without
  installer/self-stage invocation; unchanged inputs preserve output bytes and
  timestamps, and changed selections retain stable GUID and shortcut behavior.
- [ ] Update failure propagation distinguishes intentional exclusions from
  unresolved required targets; a retry does not hide failure history or rebuild
  already-verified candidates.
- [ ] Preparation, readiness and activation fault injection proves incomplete
  slots are never selected, prior runtimes remain usable and input drift causes
  revalidation rather than stale publication.
- [ ] Native Windows and POSIX focused tests, simple end-to-end checks and
  applicable fresh-environment tests cover changed contracts; record any
  unavailable clean-room/live lane explicitly.
- [ ] Targeted lint, install-contract checks, required CI and review pass for
  every implementation slice. Measure profile stage and total no-change
  latency before/after with timestamped logs.
- [ ] Source merges, release promotion and actual running-state verification
  are separately recorded; no deployed-copy patches or direct default pushes.

## Proposal

Approved and merged in #6822. Start with Phase 2. The larger
preparation barrier requires actual installer adapter support; it is not
achieved by rearranging today's combined installer invocations.

## Journal

### 2026-10-15 - Proposal and execution authorization
- Captured the requested historical cleanup, profile latency and resilience
  direction without treating telemetry work as an implementation dependency.
- Claimed #6820 as the companion to #6812 and retained the existing build,
  artifact, ownership-transfer and transactional-installer campaigns.
- Implementation has not started; the reviewed proposal is the first gate.

### 2026-10-15 - Reviewed intent landed
- #6822 merged after two current-head approving reviews with zero findings and
  green required CI. A concurrent effort-index insertion was reconciled without
  changing the design or taking over the neighboring campaign.
- The worktree advanced onto the merged proposal before implementation.
  The initial migration/update baseline passed 74 tests with two platform skips.
- Begin historical command migration and the narrow profile finishing path;
  the larger sequencing, release and running-state gates remain open.

### 2026-10-15 - Narrow local finishing implementation
- Added the Manager-owned JSON profile/shortcut boundary, one collected input
  set and unchanged-output checks. The engine now performs one final local
  repair pass against the selected payload/context rather than the inherited
  historical launcher, and required repair failures remain update failures.
- A native contained first-touch CLI fixture measured 1.828 seconds on its
  first refresh and 1.688 seconds on its second; the second pass preserved
  fragment/shortcut modification times. Both fit the fixture's explicit
  ten-second process budget. This is not a production-host speedup claim.
- Focused engine tests passed 51 cases; Manager profile/update tests passed
  129 cases; install-contract validation passed for all thirteen plugins.
  Migration integration, code review, promotion and live verification remain
  open. No deployed runtime has been patched.

### 2026-10-15 - Historical migration integrated
- Integrated exact historical template/receipt admission and recoverable
  publication through the existing ownership ledger. Required blockers include
  admitted-owned obsolete retirement failures, not just registered commands;
  unrelated foreign inventory remains protected and advisory.
- Direct repair/reconcile exit behavior now agrees with unified finishing.
  Independent contained integration/routing tests passed 315 cases with five
  platform skips. A concurrent external host-setting change correctly rejected
  an earlier run; attribution and operator-directed restoration preceded the
  stable rerun. No containment guard was bypassed.
- Touched-code lint, module ceilings and all thirteen install contracts pass.
  Read-only admission accepted the recorded historical snapshot and current
  receipts without changing deployed launchers. Code review, release and live
  post-update verification remain required before this slice is complete.

### 2026-10-15 - Required CI consumer-contract correction
- The first implementation CI found two telemetry wiring expectations that
  still required early finishing and advisory command failure. Updated the
  tests to require the reviewed late-finishing order and failed required
  reconciliation, including its final run outcome and failure summary.
- The expanded contained integration/routing/telemetry selection passed
  400 cases with five platform skips. The failure was fixed forward; no check
  was waived and no production behavior was reverted to satisfy stale tests.

### 2026-10-15 - Code-review corrections
- Real code review identified five medium-severity defects: Windows spelling
  during explicit removal and receipt-only retirement recovery, malformed
  nested transaction receipt data, registration without ambient context, and
  tolerant roster loading on a publishing path.
- Added explicit registration-target propagation without changing active
  context, and strict publishing roster collection before any output writes.
  Read-only roster behavior stays tolerant. Native/structural regressions prove
  existing fragment, state and settings bytes/timestamps survive bad or denied
  canonical and legacy roster inputs.
- Focused registration/finishing/telemetry tests passed 140 cases; Manager
  roster/profile tests passed 101. The native two-pass fixture measured
  1.922 and 1.750 seconds. Migration review fixes are integrated separately
  before the current-head review/CI gate is retried.

### 2026-10-15 - Additional review boundary corrections
- The intermediate-head review identified four additional concrete defects:
  non-UTF-8 explicit receipt repair, cell-scoped discovery of the global Manager,
  repeated native installer command reconciliation, and tolerant selection-value
  coercion during publishing.
- Kept Manager registration in its owning machine-global namespace while
  preserving validated manifests and explicit directory overrides. Both native
  reconciliation helpers now honor unified finishing custody; standalone
  behavior is unchanged, with native helper regressions in the required guard
  lane.
- Reused strict YAML/model parsing for actual publishing collection, not only a
  preliminary check. Invalid selection values cannot remove or reinterpret
  profiles; read-only coercion remains compatible.
- Focused core checks passed 283 cases with two native-POSIX skips; Manager
  checks passed 164. Native fixture timings were 2.015 and 1.797 seconds.
  Final integrated UTF-8 repair, required CI and current-head review remain open.

### 2026-10-15 - Authoritative command targets
- The next review identified a destructive ambiguity: tolerant project-registry
  reads could interpret malformed input as an empty desired command set.
- Reconciliation now validates one strict target snapshot under the existing
  registry lock before pruning or command mutation. Missing, malformed, denied
  or invalidly shaped inputs preserve prior commands and receipts; retiring all
  prior inventory requires an explicit empty projects mapping.
- Independent contained migration/integration/routing checks passed 470 cases
  with thirteen platform skips. Touched-code lint, module ceilings and all
  thirteen install contracts pass. Required guard consumers, current-head
  review, promotion and actual running-state verification remain open.
- The broader native guard pass exposed a synthetic FIFO stat fixture that
  dropped Windows file attributes, causing a TypeError before the intended
  nonregular-file refusal. Preserved those attributes in the fixture rather
  than changing production admission. Its focused guard partition passed nine
  cases with four native-POSIX skips; the remaining guard portfolio runs in
  a separate invocation under the unchanged per-run budget.
