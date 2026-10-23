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

The finite #3491 ordinary POSIX new-slot serving family continues through the existing
#6820 sequencing coordinator and Bridge lifecycle/adopter/native/deployment,
#6476/shared-constructor/installer-engine and incumbent source/maintenance/driver
owners. Preserve best-effort bounded identity-checked cold takeover, honest
outage/unknown/retry, actual candidate/output health and verified retained-good
recovery. No new updater, live operation or universal acknowledgement is authorized.

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
- [x] Recognize attributable historical generated command/receipt generations,
  refresh same-lineage launchers and retire obsolete owned project commands
  automatically, preserving foreign, edited and ambiguous artifacts.
- [x] Make command/receipt transitions recoverable and idempotent under the
  existing ownership locks; test Windows sibling files and POSIX launchers.
- [x] Make unresolved required command reconciliation explicit in update status
  and exit behavior; never call a preserved conflict "in sync."
- [x] Replace the profile install/stage detour with the Manager's narrow owning
  surface, preserving fragments, state/settings reconciliation and shortcuts.
- [x] Run local finishing once after relevant update inputs stabilize; avoid
  unchanged writes and unnecessary repeated discovery.
- [x] Publish measured no-change and migration/profile-repair results, with
  changefiles, review and source/release distinction intact.

### Phase 3 - Risk-first, resumable update execution
- [x] Collect one attributable runtime execution snapshot and check all
  selected targets' cheap deterministic blockers before runtime mutation.
- [x] Acquire required payloads before advisory inventory, stop on required
  failure and retain successful acquisitions.
- [ ] Front-load dependency admission with attributable retained candidate
  inputs through the canonical installer contract.
- [ ] Compose existing prepare/validate/activate contracts with the installer
  owners. Do not label a combined build-and-activate installer prepare-only.
- [ ] Introduce a readiness barrier and dependency-aware recoverable activation
  for supported adapters, retaining prior healthy generations and explicit
  unsupported-adapter behavior.
- [x] Retry explicitly transient payload failures within bounded in-run
  backoff, re-inspect the failed target, retain successful peers and failure
  history, and stop authority/policy/integrity refusals.
- [ ] Preserve verified prepared-candidate progress across retries and resume
  incomplete runtime/local-finishing work through canonical contracts.
- [x] Recover explicit transient activation-restoration contention against the
  original snapshot without replaying installation.
- [x] Recover Windows payload-update timeouts only after positive non-breakaway
  Job cleanup, preserving refusal precedence and the existing attempt budget.
- [ ] Retain and recover witnessed Windows activation restoration across
  interruption through canonical operation custody, without install replay.
- [ ] Establish portable payload process-tree cleanup and interrupted-command
  settlement before enabling unsupported timeout-replay lanes.
- [ ] Adopt reuse and bounded independent preparation only where measured
  benefit and existing isolation/resource contracts justify them.

### Phase 4 - Release, running-state proof and conclusion
- [ ] Verify promoted source contents and perform the authorized local unified
  update; prove command migration, selected runtime health and profile latency.
- [ ] Record no-version-change, changed-version and fresh-path coverage
  separately; do not substitute footprint results for build timing.
- [ ] Resolve or explicitly transfer every implementation and validation item
  to an accepted named objective, update reality docs, archive and finalize.

<!-- backlog-reconciliation:7556:transfer:3491:to:efforts/active/resilient-update-sequencing/README.md -->

Add this COMPLETE finite Bridge POSIX ordinary-update serving family DIRECTLY to the
existing reviewed ACTIVE ROOT Phase 3 per-adapter activation Plan AND Phase
4/Validation/continuing Coordination. Preserve EVERY incumbent Plan/Validation/Journal
row and existing #6820 coordinator, Bridge lifecycle/adopter/native/deployment owners,
#6476/shared-constructor/installer-engine custody, source/maintenance/driver and
telemetry/command/profile owners. This is additive acceptance coordination, not taking
over their implementation, creating another updater or executing a transition.

### Detached routed survivor across successive Bridge updates retained from #3491
- [ ] Reconcile the original ordinary fresh/new-slot update path against the ACTUAL
  serving local Bridge, not only PID-file/systemd activity. A correctly identified
  routed detached survivor from a prior cutover must be recognized even without those
  native tracking hints; its healthy endpoint is not proof it already serves the
  selected newly installed runtime.
- [ ] Credit #6877's delivered guarded published-slot REUSE reconciliation and native
  worker/retirement/rollback fixtures. Trace and qualify ordinary construction ->
  completion/selection -> actual start/deploy separately; reuse-only early-return repair
  does not establish new-slot behavior. Current do_start can return healthy for another
  generation, so package/CLI/selector/unit/manifest success must not stand in for actual
  serving convergence.
- [ ] Carry the intended selected source/runtime/generation through transition and
  confirm ACTUAL worker PID/native birth, role, runtime/source and active route where
  required by existing lifecycle custody. Revalidate exact predecessor/selection/owner
  at mutation/publication/retirement boundaries; preserve pending-retirement,
  concurrent-owner/refusal, failure/rollback and retry evidence. Unknown/absent routing
  or a bare/reused PID is not permission to terminate or claim convergence.
- [ ] Preserve forwarded/foreign installations and inactive/client-only scope: do not
  start, drain, stop or replace a local service merely because runtime files update.
  Retain correct local healthy/current no-op and existing source/installation admission
  without version-blind wrong-generation success or a second native owner.
- [ ] Apply CURRENT best-effort handoff: prefer compatible graceful work/drain
  continuity, but broken/incompatible/refused/non-settling old handoff must not veto the
  authorized selected generation. Permit bounded identity-checked old-owned-scope
  shutdown/cold takeover BEFORE incoming readiness under the existing lifecycle
  authority, with exit/write-exclusion confirmation, explicit outage/unknown/failure
  outcomes, client reconnect/safe retry and crash-consistent durable child recovery.
  Never restore a zero-outage or successor-ready-before-EVERY-retirement bar, kill by
  name/age/bare PID, or touch foreign/successor/separately owned scope.
- [ ] Keep actual admitted candidate/output CLI health before construction
  completion/promotion and preserve a VERIFIED RETAINED previous-good generation/durable
  state across construction, publication or cutover failure. Bad current/LKG evidence is
  not health; repair published bad content by a distinct admitted generation, never
  in-place rebuild/global-cache deletion/forced policy bypass. Retained good
  code/recovery is not a promise that the old process must stay uninterrupted during
  authorized cold takeover.
- [ ] Report actual transition outcome and serving identity truthfully:
  failed/uncertain/partial recovery or wrong-generation service cannot become Update
  complete solely through written selectors/manifests or version-blind healthy start.
  Keep source merge, containing release, installed target and actual serving/native
  evidence separate.

### Validation and continuing coordination for #3491
- [ ] In an existing authorized isolated original POSIX/native lane, establish a real
  local routed survivor with no installer PID/unit tracking, perform at least TWO
  successive new-target ordinary updates, and record actual worker/route/runtime/source
  identity and bounded outcome after each. Retain current-reuse regression, meaningful
  wrong-version/foreign/forwarded/inactive/unknown-owner and pending-retirement/failure
  controls; mock/CLI/manifest success alone is not original serving proof.
- [ ] Qualify compatible handoff and the accepted bounded cold path at applicable
  changed boundaries with exact owned identity, preserved admitted
  candidate/retained-good recovery, honest interruption/unknown/retry behavior and no
  overlapping exclusive writers. Preserve original actual evidence tiers; do not add all
  hosts/platforms, every parent test, whole portfolio/release campaign or a universal
  owner ACK to this finite family.
- [ ] Continue through the existing sequencing coordinator and concrete
  Bridge/installer/adopter/source/native owners with normal independently reviewed
  slices and Journal updates. Old creation, historical draft-status snapshots, completed
  reuse-only work or this proposal does not complete this new family. Preserve every
  unrelated existing obligation and current best-effort direction.

Receiving gate: a NEW normal reviewed ACTUAL merge must materialize ALL direct unchecked
ROOT Plan AND Validation/continuing Coordination rows, followed by FULL CURRENT ROOT
readback including incumbent Plan/Validation/Coordination/Journal and unchanged owners,
BEFORE administrative supersession of #3491. If ROOT is not Active, acceptance is
incomplete or custody cannot be preserved, retain the source. This proposal is not
implementation, running-state/native qualification, rollout or live
force/service/process authorization.

At c3acde020b24eb56fd89004976b0c10e0fdae8d9, the Bridge installer delta is confined to
deploy_binstub's generated command wrapper. Actual dev-merged #8120
(0439a3c7a29f19da3d1cc70a5363751318dacc1d) adds first-use publication/context admission
and preserves healthy current/LKG command dispatch, with bounded native command
fixtures. It does not change ordinary do_update's PID/unit was_running predicate,
internal resolved-Python deploy/start path or do_start's version-blind healthy
routed-service return. Thus the previously documented fresh/new-slot versus reuse
serving distinction remains at the inspected head; no live stale-serving failure or
complete repair is newly claimed. Preserve all ten direct 3491
Plan/Validation/Coordination clauses and current best-effort
cold-recovery/candidate-health/verified-retained-good-generation semantics.

## Validation Plan

- [ ] Resolve every directly retained #3491 Plan/Validation/continuing Coordination
  clause above at its original finite scope and evidence tier, preserving alternatives,
  same custodians and nonexpiring holds. Administrative intake is not implementation,
  UI/native acceptance, deployment or unrelated parent completion.

- [x] Historical receipt/template fixtures cover same-lineage payload relocation,
  known pre-receipt formats, missing/retired projects and knowledge-only commands.
- [x] Foreign/cross-cell owners, edited bytes, corrupt receipts, ambiguous source
  lineage and case collisions remain protected and explicitly reported.
- [x] Failure between command and receipt publication, concurrent reconcilers,
  partial Windows siblings, cancellation and retry preserve recoverability.
- [x] Profile integration crosses the real Manager command boundary without
  installer/self-stage invocation; unchanged inputs preserve output bytes and
  timestamps, and changed selections retain stable GUID and shortcut behavior.
- [x] Update failure propagation distinguishes intentional exclusions from
  unresolved required targets; bounded payload retry retains attempt history
  without repeating successfully acquired peers.
- [ ] Candidate/checkpoint retry does not rebuild already-verified candidates.
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

### 2026-10-23 - Direct retained acceptance from #3491

- Added the complete #3491 direct retained acceptance to this ROOT Plan, its actual
  global Validation and continuing Coordination. Every incumbent obligation,
  alternative, historical outcome and custodian remains; controlled historical
  identifier abstractions preserve their technical roles. A new normal reviewed actual
  merge and complete current receiving-section readback must precede source
  supersession. The effort stays Active; no implementation, test, native result, worker
  activation, rollout or resource effect is claimed.

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
- Further review found dangling configuration links, tolerant registry reloads
  after validation, and malformed repository entries on the publishing path.
  Strict parsing and entry validation now propagate through actual collection;
  dangling links are blocked while genuinely absent optional inputs remain
  supported. Regressions change or deny each registry between validation and
  collection and assert preserved fragment/state/settings bytes and timestamps.
- Manager coverage passed 175 cases, including native first/second refreshes at
  2.079/1.609 seconds. A broad guard invocation exceeded its unchanged
  300-second budget without another assertion failure; partitioning must retain
  the complete collected guard portfolio rather than drop slow tests.

### 2026-10-15 - Destructive-boundary review corrections
- The complete native guard portfolio passed 1,010 cases with eighteen platform
  skips, using seven explicit file-group invocations under unchanged budgets.
  The slowest group took 269 seconds; no test or assertion was omitted.
- Subsequent review found that empty/null/missing-key registry documents could
  still authorize profile retirement, display-name validation happened after
  fragment publication, and the engine failed to forward globally resolved or
  detected machine identity. Present registries now require explicit mappings;
  publication validates consumed entry shapes and shortcut names before writes.
  Registration passes its known machine; refresh uses the explicit project's
  existing layered configuration resolver, independent of ambient context.
- A separate transaction finding required immediate sibling revalidation, not
  only a pair-wide precheck. Publication, recovery and rollback now revalidate
  at each replacement/deletion/permission boundary, including after staging,
  and verify final pair states before settling receipts. Interleaved edits
  remain untouched with an unsettled journal and visible retry failure. This
  is not filesystem compare-and-swap against an uncooperative writer.
- Independent integrated core checks passed 495 cases with twenty-eight
  platform skips; Manager coverage passed 189. Native profile command passes
  measured 2.125 and 1.672 seconds. Review and required CI remain open; no
  installation has been patched or deployment claimed.

### 2026-10-15 - Admission evidence and canonical empty-state roundtrip
- Review found another edit window between validation and transaction capture.
  Admission now returns immutable sibling-byte evidence; registration,
  reconciliation, retirement and explicit transfer carry it into publication.
  A changed capture fails before any journal is written, preserving prior
  receipts and edited command bytes rather than admitting the new bytes.
- Strict empty-state readers exposed the canonical writers' old null/omitted
  empty mappings. Both writers now emit explicit empty mappings. Real
  writer-to-reader regressions remove the last project/repository, retain
  edited commands for classification and apply the Manager's profile
  reconciliation, including a source-root-only registry.
- Independent integrated checks passed 572 cases with twenty-eight platform
  skips; the standalone writer-to-reader file passed 21 cases. The fast,
  filesystem-contained migration matrix now participates in required guards
  so hosted Linux exercises the POSIX host path before merge, not only during
  later full promotion validation. Its prior native Windows guard pass took
  30.5 seconds for 152 cases with twenty-four platform skips.
- The preceding published head cleared required CI. The new admission/writer
  head still owes current-head review and CI; promotion and live proof remain
  open.

### 2026-10-15 - Preserve the out-of-process validation boundary
- Required CI rejected the writer/profile integration test's direct Manager
  import from plugin test code. Replaced it with the real native Manager CLI
  process under contained home/config/output roots, retaining the canonical
  writer and last-entry-removal assertions. No architecture guard was weakened.
- The 21-case registry file passed with the actual process boundary, and all
  five Manager dependency-boundary contracts passed. The prior CI run's other
  2,307 Manager cases passed; current-head required CI and review remain open.

### 2026-10-15 - Destination transfer recovery and Terminal input custody
- Review found explicit-transfer journals retaining the previous installation's
  owner, preventing ordinary recovery of an interrupted authorized transfer.
  Transfer journals now use destination identity while retaining exact prior
  receipt bytes for ordinary-failure rollback. Recovery no longer needs a
  second transfer authorization after a mixed-pair interruption.
- The new refresh boundary exposed a shared deployment reader that treated
  invalid or denied Terminal settings reads as empty. Publication now strictly
  admits fragments, state and settings before writes, retains their byte
  snapshots and fragment inventory, and refuses observed drift before each
  mutation. Read-only views remain tolerant; genuinely absent optional files
  remain supported.
- Terminal settings accept their native JSONC comments/trailing commas.
  Unchanged settings retain bytes/timestamps; cleanup backs up original bytes.
  Native output checks caught and corrected implicit Windows newline conversion
  in snapshot bookkeeping rather than relaxing drift detection.
- Independent command/registration/profile integration passed 334 cases with
  twenty-eight platform skips. Manager coverage passed 223 cases, including
  malformed/denied/changed inputs, inventory drift and JSONC backup preservation.
  Native refresh passes measured 2.109 and 1.609 seconds. Module ceilings and
  touched-code lint pass; current-head review, required CI and rollout remain
  open.

### 2026-10-15 - Shared dangling-path admission
- The next review had no open inline findings but identified two medium
  summary findings: strict YAML and roster fallback still recognized only
  dangling leaf links, not dangling ancestor directories.
- All strict registry/config/roster and Terminal JSON reads now share an
  ancestor-aware dangling-link check, including Windows reparse-point
  metadata. An unavailable canonical roster directory cannot authorize a
  legacy/empty fallback. Genuinely absent paths beneath healthy parents remain
  optional, and read-only behavior stays tolerant.
- Four directory-link publication regressions preserve all existing outputs.
  Manager coverage passed 227 cases; native refresh passes measured 2.485 and
  2.062 seconds. Current-head review/CI and actual deployment remain open.

### 2026-10-15 - Finishing context and bounded native CI composition
- The next summary finding identified profile finishing outside the selected
  payload/context scope after command reconciliation restored the caller.
  Profile finishing now uses the same scoped selection and restores inherited
  state afterward, including on failure. Windows-path regressions resolve
  distinct selected/inherited machine configurations; the focused integration
  selection passed 123 cases with two platform skips.
- Required native diagnostics CI timed out after 260 successful cases, not an
  assertion failure. The exact six-file selection passed locally in 237.79
  seconds, confirming that multiple bounded successful native calls exceeded
  the enclosing two-minute step.
- Split source/provenance, command catalog, lifecycle contracts and native
  first-touch into four bounded steps. Exact collection proves the same 279
  items, partitioned disjointly as 204 + 33 + 37 + 5; no case or assertion was
  dropped. The original two-minute step bounds and five-minute job bound stay
  unchanged. Real contained native groups passed in 65.63, 52.44, 67.64 and
  50.16 seconds (264 passed, fifteen platform skips in total).
- This is a fix-forward portfolio-composition correction, not a flaky-test
  waiver or a production installer deadline increase. Current-head review,
  required hosted CI and rollout remain open.

### 2026-10-15 - Current-base native coverage retained
- A current-base conflict was confined to CI wiring: the merged hidden-lifecycle
  diagnostics fix added four stdio cases to the formerly combined selection.
  Rebased with an owned backup and retained those cases in the lifecycle
  partition rather than choosing one side or dropping coverage.
- Current-base collection is exactly 283 disjoint items, partitioned as
  204 + 33 + 41 + 5. Real contained native groups passed in 64.93, 54.09,
  87.48 and 51.41 seconds, all within their original 120-second bounds
  (268 passed, fifteen platform skips).
- Rebased finishing coverage passed 123 cases with two platform skips; Manager
  coverage passed 227. Lint, module ceilings and all thirteen install contracts
  pass. The branch still owes current-head hosted CI/review and actual rollout.

### 2026-10-15 - Same-step governed test prerequisites
- Required CI caught the workflow contract requiring each host-pytest step to
  declare its seasoned prerequisite admission, rather than relying on an
  earlier step. Added the existing governed prerequisite command to each new
  partition; did not weaken the policy guard or use an alternate resolver.
- All 140 public-toolchain and native-workflow composition contracts passed.
  Test selection and the existing step/job deadlines remain unchanged.

### 2026-10-15 - Ambiguous YAML and inaccessible fragment inventories
- Further review identified duplicate-key YAML authorizing unintended command
  or profile retirement. Shared command-registry parsing and its secondary
  explicit-empty check now reject duplicate keys before migration/defaulting;
  strict Manager registry/config/roster reads use one duplicate-rejecting
  loader. Invalid mapping tags are also refused rather than becoming empty
  desired state. Tolerant read-only behavior remains compatible.
- Required publication inventory traversal now propagates denied-directory
  errors during both collection and verification. A hidden foreign fragment
  cannot silently become an apparent orphan; uppercase JSON suffixes remain
  discoverable on Windows.
- Independent command admission/registry checks passed 354 cases with
  twenty-six platform skips; Manager coverage passed 237. The selected-cell
  input contract remains open: keeping the correct environment alone does not
  make Manager's legacy-only input collection cell-aware. Add an attributable
  selected-root/snapshot boundary and a real Manager-process regression before
  considering that finding resolved.

### 2026-10-15 - Selected-cell profile input boundary
- Engine finishing now transports its validated registry and per-project state
  roots through a versioned stdin request. Manager checks the owning
  agent-worktrees receipt with its existing vendored installation-context
  validator, constrains inputs to that cell, and propagates the same roots
  through strict preflight, machine lookup, registry joining and final
  selection/icon collection. Missing project mappings block, never fall back
  to legacy state. Project identity resolution stays with the engine.
- Manager discovery, Windows Terminal output and shortcut destinations remain
  machine-global. Direct legacy command compatibility is unchanged, and neither
  side imports the other's implementation.
- A native engine-to-real-Manager subprocess test uses distinct legacy/cell
  registries and selections, retains the cell's peer and shell profiles, and
  proves a second pass does not rewrite the fragment. Manager's independent
  native command test covers the same distinction and unchanged shortcuts.
  Invalid contexts, foreign roots, missing mappings and invalid/ambiguous YAML
  block before publication.
- Focused engine/context coverage passed 63 cases with two platform skips;
  Manager read-model/profile/deployment/decoupling coverage passed 198.
  Touched-code lint and all thirteen installation contracts pass. Fresh
  current-head review and hosted CI still gate the source merge; nothing has
  been rolled out.

### 2026-10-15 - Phase 2 source merge and continuing gate
- #6836 squash-merged into `dev` at
  `e2c74d2423f945a56d51ae4d2898a170d2f03753`. Exact-head Copilot review
  reported zero open findings, all four latest high-severity findings resolved,
  and every required hosted CI lane passed. This is a source merge, not an
  installed-state claim.
- Phase 2 implementation and its first four validation rows are complete.
  Native fixture profile measurements are recorded above; production profile
  and total-update timing still belong to the release/running-state gate.
- Next slice: collect the update's attributable target plan and front-load
  deterministic blockers/acquisition before any combined runtime mutation.
  Preparation/readiness/activation and retry remain open. The canonical
  installer-owner request on #6707 has no accepted adapter handoff yet; its
  current draft still combines construction and activation. Do not substitute
  that combined action for a prepare-only contract or mark Phase 3 complete.

### 2026-10-15 - First acquisition barrier
- Started Phase 3 with a deliberately bounded safety improvement: after the
  existing payload acquisition pass, any required failure now stops before
  engine runtime planning/installers, modules, registered runtime deployment,
  anchor synchronization, pruning or command/profile finishing. Successfully
  acquired peers remain in place for retry. Inactive inventory failures retain
  their existing advisory treatment.
- This is not the complete frozen desired-state plan, a prepare-only runtime
  lane, a readiness barrier or automatic retry. Manager self-update still
  precedes engine entry. Those broader contracts remain open rather than being
  implied by an early return.
- Focused update coverage passed 234 cases, including explicit engine failure,
  timeout, OS-denial and registered-target failure before any downstream
  mutation, plus a real failed acquisition subprocess. Successful update,
  version-skip, inactive inventory, telemetry and final integration behavior
  remain covered. Touched-code lint passes.
- The completed migration delegate's managed finalization currently refuses
  landing proof even with the merged #6836 association. Its committed work is
  preserved; no reset, forced gate or manual removal is used. Cleanup evidence
  is being narrowed separately while the parent effort stays active.

### 2026-10-15 - Acquisition source merge and authorized release bootstrap
- #6862 squash-merged into `dev` at
  `d4377bf2c8bcdc09a05de76887c7d7b25bd1915f`. Its exact-head Copilot verdict
  was Approved; the sole Low documentation-impact omission was corrected in
  the PR description and resolved. Required CI passed. Pulled the parent
  worktree forward through the managed sync; Phase 3 remains incomplete.
- Actual promotion run 38765826569 failed before candidate construction:
  trusted `main` helper provisioning refused with `canonical local graph
  lacks library attribution`. #6849 already corrects that attribution on
  `dev`, but the old trusted-main helper runs before dev composition, creating
  the independently diagnosed bootstrap deadlock tracked by #6848/#6535.
- The operator explicitly authorized only the narrow trusted-main attribution
  bootstrap repair needed to unblock that already-reviewed correction,
  with normal validation and no dependency-policy weakening. Coordinated the
  authorization on #6848 before starting another repair. The existing
  #6849/#6476 source/bootstrap owner retains custody; no competing repair,
  unchecked retry, signer substitution or hash waiver has been started.
- Delegate content now exactly matches all eight owned merged paths after
  incorporating the parent's reviewed refinements (54 tests and lint pass).
  Its managed finalize still refuses the merged-head boundary even though
  blob landing is proven; managed pull-forward auto-aborted on a conflict.
  The child and an owned backup remain preserved, and its cleanup obligation
  remains open rather than bypassed.

### 2026-10-15 - Workflow-only bootstrap admission and fresh proof
- No active bootstrap repair was found through the public tracker and available
  source/session registries. Took the separately authorized release-bootstrap
  slice in #6867, leaving the durable #6849/#6476 source owner intact.
- The first exact source backport reproduced the original three attribution
  failures and passed 239 corrected policy/synchronization cases with one
  platform skip. The trusted main source gate correctly refused source/test/doc
  changes; no bypass was attempted. The operator then explicitly selected a
  workflow-only pinned-helper repair, following #6619/#6558. Reversed the source
  backport and revised the same PR, preserving the attempted shape in its audit.
- Current head `0292be0a33d8009ebad08d40b637d9df146a8c2f` changes only two
  workflow files. Four identical validator-provisioning steps select the exact
  reviewed helper commit and blob, preserve the ordinary canonical attribution
  path and restore the original helper before build/sign/publish. No dependency,
  hash, signer or qualification gate changes.
- The main source gate passes. Read-only hosted proof 38813975248 executes the
  actual production blocks: wrong helper identity refuses before provisioning,
  injected UV failure restores the checkout and retains its failure, and a fresh
  real hash-pinned, seasoned validator installation succeeds. Native focused
  workflow/control/readiness coverage passes 94 cases. Fresh current-head
  review still gates merge; normal containing promotion and installed acceptance
  remain open.
- Full CI on the superseded main-based source backport passed guards/lint but
  exposed separate standalone/materialized-release qualification failures.
  They are not waived or claimed fixed by this narrow workflow bootstrap.

### 2026-10-15 - Named bootstrap handover and fail-fast continuation
- The operator explicitly transferred existing #6867 stewardship and its
  validated review fix to `grimy-axle`; this coordinator stopped publishing to
  that PR. The complete fix and its evidence are attached in comment
  6203999889. Its five focused promotion/artifact/control/wheel/adapter modules
  pass 328 cases. The named owner retains fresh-head proof, review, merge and
  containing-release execution; affirmative resource transfer is still pending.
- Continued #6820 separately on current `dev`, consuming #6863's tree-owning
  installer deadline/receipt judgment and #6869's human framing unchanged.
  The logical target selection is now copied before acquisition, the engine
  payload is refreshed once, and required registered payloads precede advisory
  inventory. Required failure stops remaining acquisition immediately.
  Retired inactive payloads are not removed before successful acquisition.
- Regression-first reproduced nine failures with two controls passing. The
  corrected focused update portfolio passes 205 cases with one platform skip,
  including a real failed payload subprocess that retains its successful peer
  and never touches pending inventory/retirement. Existing success, telemetry,
  runtime skip and finishing contracts remain covered.
- Final complete changed test files pass 159 cases with one platform skip,
  including the added successful acquisition/retirement event-order contract.
  Module ceilings, all thirteen install contracts and whitespace checks pass.
  Local governed Ruff restoration remains blocked by package-host TLS; hosted
  lint, current-head review and CI still gate the source merge.
- This is another bounded acquisition improvement, not the complete immutable
  runtime-input plan, preparation/readiness protocol, checkpointed retry or
  installed acceptance. Those parent gates remain open.

### 2026-10-15 - Fail-fast source merge and next planning boundary
- #6890 squash-merged into `dev` at
  `421056fc861d92278e49f06ebe3f59600d67432b`. Exact-head Copilot review
  5541108074 Approved with zero open findings. Hosted lint and all required
  checks pass on the reviewed head.
- Required Windows launch CI initially reproduced the retained #6266/#6274
  seven-conhost signature. After its parent run completed, one recorded
  same-head failed-job diagnostic actually executed both native cases:
  2 passed, 11,569 deselected. Attempt 2 of 38822519125 and its required
  aggregate passed. No monitor source, safety assertion, deadline or gate
  changed. This is not a root-cause fix or blanket flake acceptance; #6274
  retains that investigation.
- Re-read the parent Plan and Validation Plan after merge. Phase 3 remains
  open. Next slice: factor one attributable runtime execution plan and
  whole-set cheap preflight from the existing engine/module/registered
  planning paths, preserving their actual invocation and ownership semantics.
  Do not double all expensive discovery by adding a second dry-run traversal.
- The constructor owner's concrete preparation/retention handoff request on
  #6707 (comment 6204311620) has no accepted contract yet. A frozen execution
  plan must not be called a preparation/readiness barrier. The coordinator
  retains the parent release-consumption and installed-state gates regardless
  of the separate #6867 bootstrap stewardship handover.

### 2026-10-15 - Collected runtime commands and whole-set preflight
- Extracted the existing engine/module/registered command planning into one
  collected execution portfolio. Entries retain the selected root, immutable
  environment snapshot, version/force decision, exact argv, deadline and
  module-only update/install fallback. Named-force admission uses the same
  selection instead of rediscovering installations.
- Cheap required ownership/context, scope/gate, installer/interpreter and
  deadline failures now stop before any combined runtime installer or local
  finishing. The explicit `runtime_preflight` phase reports those failures
  without claiming that an unexecuted engine/module phase completed.
- Regression-first reproduced all six late-blocker cases after engine
  invocation. The corrected ten-file native portfolio passed 309 cases with
  six platform skips, including a real fresh synthetic update subprocess
  proving a later missing module installer prevents engine activation.
  Existing cell roots, fallback, watchdog/receipt, force, skip, acquisition and
  finishing contracts remain covered. All thirteen install contracts pass.
- Documentation impact: updated `docs/install-contract.md`'s unified-update
  boundary and its limits. This closes part of the reviewed installer vision
  and design; it adds no constructor, dependency resolver or scheduler.
  Hosted POSIX coverage, lint and current-head review/CI remain publication
  gates. The synthetic first-touch process is not a full clean-room installer
  build; constructor code is unchanged. Actual local update/health/timing
  remains blocked on promotion and belongs to Phase 4, not this fixture.
- This is an execution snapshot and configuration preflight, not a frozen
  prepared-candidate identity, dependency admission, readiness barrier or
  resumable retry. The existing constructor owner still owes an accepted
  prepare/retention interface; Phase 3 and the parent effort remain open.

### 2026-10-15 - Runtime preflight source merge
- #6902 squash-merged into `dev` at
  `5a0537ec291f5ae49455f58f15bc7c90d65fc17e`. Exact-head Copilot review
  5541454153 Approved with zero findings; CI 38827508820 succeeded, including
  hosted lint, POSIX guards and native Windows lanes. Final standalone
  compatibility selection passed 52 cases. Source merge is not rollout.
- Re-read the full Plan and Validation Plan. Split the first broad Phase 3 row
  to mark only collected command preflight and fail-fast payload acquisition
  complete; dependency admission and retained candidate identity remain open.
- Next independent slice is bounded retry of explicitly transient acquisition
  failures using existing failure classification and deadline primitives,
  retaining successful peers and rechecking failed targets. Do not invent a
  scheduler or retry authority/policy/integrity failures.
- #6707 remains draft at the same head, without an accepted prepare/retention
  handoff. #6867 remains at the handed-over head; the former bridge peer and
  coordinator addresses no longer resolve. Preserve its local validated fix
  and obligation records until the operator-named recipient affirmatively
  accepts them. No transferred source was changed or published here.

### 2026-10-15 - Bounded independent payload recovery
- The operator selected keeping the canonical preparation draft with its
  existing owner while this effort continues independent acquisition-retry
  work. No preparation/constructor source or competing implementation was
  created.
- Regression-first exposed nine failures with one timeout control passing:
  arbitrary policy/authentication/integrity failures could previously become
  success through an unconditional install fallback; transient connection
  errors also changed verbs rather than retrying the failed acquisition.
- Engine and registered payloads now share a two-attempt recovery policy,
  one-second transient backoff and a 240-second attempt budget with at most
  120 seconds per command. Each attempt re-inspects only its own payload.
  Known transport/gateway/contention failures may retry; only a matching
  plugin's explicit missing-ledger error authorizes activation-preserving
  install. Engine acquisition remains update-only.
- Authority, permissions, source-policy, integrity, certificate and unknown
  failures remain terminal. Timeout recovery is deliberately not automatic:
  the current payload subprocess owner cannot prove descendant termination.
  Starting an overlapping writer would be unsafe; this slice does not claim
  process-tree custody or persistent candidate/checkpoint recovery.
- The five-file native portfolio passes 223 cases with one platform skip.
  Real failed-child acquisition proves successful peers are not revisited;
  required outcome folding retains advisory attempt history while allowing
  genuine recovery to succeed. Budget, fresh inspection, final-cause retention,
  refusal precedence, bounded attempts and engine-only update are covered.
  All thirteen install contracts pass.
- Documentation impact: extended the install contract's payload-recovery
  behavior and explicit limits. No installer construction or dependency
  declaration changed; the fresh synthetic subprocess covers this local
  consumer boundary rather than repeating a full clean-room build.
  Hosted POSIX/lint, current-head review and CI still gate publication;
  release consumption and actual installed-state proof remain open.

### 2026-10-15 - Retry error custody and native numeric diagnostics
- #6906's first current-head review identified three medium issues: numeric
  permission refusals could lose precedence to a transient/missing-ledger
  marker, native Windows `(os error 32/33)` contention was not recognized, and
  install-wrapper OS errors could originate in activation restoration rather
  than spawning the child.
- Added twenty-two focused regressions; twenty failed and two host controls
  passed before correction. Recovery now reuses the existing host-aware
  numeric matchers, including terminal I/O/permission errors, and only direct
  update-path sharing/lock spawn failures may retry.
- A real preservation-wrapper fixture captures a disabled plugin, simulates
  install enabling it and denies the restoration replacement. Both successful
  and timed-out command cases remain explicit failures with no second command;
  the updater cannot claim success while activation restoration is unproven.
  This does not claim that an inaccessible restoration destination was repaired.
- The complete five-file native portfolio passes 245 cases with one platform
  skip. Fresh current-head review and hosted CI remain required before merge.

### 2026-10-15 - Bounded payload recovery source merge
- #6906 squash-merged into `dev` at
  `8c87da4684822ad0810b57be344e49bb97b165c0`. Exact-head review 5541951939
  Approved `7dfccb5cbe1e596f3f35f77f7c0a678e0f336ef5`, resolving all three
  earlier medium findings with zero open issues. Current-head CI 38835222486
  succeeded, including hosted lint and required POSIX/native Windows lanes.
- Re-read the full Plan and Validation Plan. Marked only bounded in-run
  payload recovery, failure propagation and peer retention complete.
  Prepared-candidate checkpoints, timeout/activation-restoration custody,
  readiness/recoverable activation, reuse and installed acceptance remain open.
- Keep #6707 with its current canonical owner, per the operator's explicit
  decision; no alternate constructor is authorized by its unavailable handoff.
  #6867 remains at its previously handed-over head. That release dependency
  and its validated local correction have not been consumed or deployed here.
- The coordinator retains the parent objective and all unresolved resource
  obligations. Next required coordination is a reachable qualified recipient
  for the already-directed bootstrap stewardship handover and an accepted
  canonical preparation contract. A source merge does not satisfy either gate
  or the actual running-state/timing acceptance.
- While those handshakes are pending, the next independent slice is payload
  timeout/process custody and activation-restoration recovery, composing the
  existing tree-owning runner and preservation primitives rather than adding
  another process engine or retrying an unproven writer.

### 2026-10-15 - Original-snapshot restoration recovery
- Kept command retry separate from activation-state recovery. The canonical
  state owner now retries restoration once after one second only for explicit
  Windows sharing/lock failures (32/33), including wrapped state-read errors.
  It retains the immutable pre-install snapshot and reads current documents
  again; partial settings restoration does not cause command replay or capture
  of the newly enabled plugin state.
- Permission, malformed-state and other failures remain terminal. Exhaustion
  raises the restoration error; a recovered restoration retains the command's
  original success or timeout outcome. No new journal, constructor, scheduler,
  dependency or activation policy was introduced.
- Regression-first reproduced fourteen failures with three controls passing.
  The canonical library portfolio and updater consumer tests cover activation
  tri-state, partial two-file progress, retained refreshed inventory, unrelated
  settings, bounded exhaustion and exactly one install command. Native
  subprocess fixtures cover existing and previously absent activation state.
- Native canonical coverage passes 105 cases, updater preservation/retry
  coverage passes 58, and the isolated standalone consumer passes eight.
  All thirteen install contracts and canonical/vendor source checks pass.
  The fresh-state subprocess is the applicable first-touch proof; this change
  adds no installer/dependency path requiring a full clean-room build.
  Hosted POSIX/lint, current-head review and CI still gate publication.
  No live installation was changed; rollout remains separately authorized.
- Documentation impact: updated the canonical and standalone-vendored library
  README and the unified-update installation contract. The canonical source
  change propagates to all eight consumers through one changefile; the sole
  physical source copy is synchronized without importing unrelated test drift.
- This closes one narrow restoration-contention gap under the reviewed
  installer vision. The process-tree owner still cannot positively attest
  timeout cleanup, so timeout replay remains blocked. Durable interrupted
  restoration, preparation/readiness, release and actual running-state
  acceptance remain open; the broad custody Plan item is not complete.

### 2026-10-15 - Original-snapshot recovery source merge
- #6916 squash-merged into `dev` at
  `c7dd55a69d2cb0437d47d519809717cd838837c2`. Exact-head Copilot review
  5542276395 Approved `25ebe2e9a69b7176089228f53f7e8cb81e96f27b` with no
  open findings. CI 38839564402 succeeded, including hosted lint, POSIX and
  native Windows lanes. No retry or gate bypass was needed.
- Re-read the Plan and Validation Plan. Marked only original-snapshot
  restoration-contention recovery complete. Timeout cleanup proof and durable
  interrupted restoration remain separately open rather than being implied by
  this bounded recovery. Preparation/readiness still has no accepted handoff
  from #6707; that draft and the handed-over #6867 remain at their prior heads.
- Next independent slice: make the existing process owner positively confirm
  cleanup before payload state restoration or command replay. Reuse the
  existing Windows Job termination-and-wait primitive and POSIX session owner,
  preserve test containment, and retain an explicit failure on unproven cleanup.
  Source merge is not release promotion or installed-state acceptance.

### 2026-10-15 - Windows-first timeout recovery boundary
- The operator explicitly selected verified Windows payload-update recovery
  first, retaining POSIX and install timeout blockers until their independent
  recovery custody is established. A POSIX session census cannot be presented
  as full descendant proof because the existing owner preserves detached daemons.
- Added an opt-in strict Windows lane to the existing process owner, not a
  second runner: require a Job, prohibit breakaway, confirm zero active
  processes through the existing termination-and-wait API, then drain/reap the
  root within one shared cleanup grace. Default runtime installer behavior is
  unchanged. Failed cleanup raises an explicit error rather than a timeout
  result that could authorize another writer.
- Windows payload updates consume that lane and reserve cleanup time inside
  the existing total budget. Only confirmed owner timeouts may retry; captured
  refusals retain precedence, and CLI exit 124 alone is not proof. Native spawn
  errors retain their numeric codes. Install and POSIX timeout behavior remains
  explicitly blocked.
- Regression-first reproduced all three missing strict-owner cases. Real
  native children cover normal exit, owner deadline and watchdog exit, plus
  end-to-end timeout replay after the first grandchild is dead and refusal
  after timeout with no replay. No late synthetic writes may appear.
- Documentation impact: updated the unified-update install contract and design
  admission boundary. This closes another bounded piece of the reviewed
  installer convergence vision, not preparation/readiness, durable interruption
  recovery, release or running-state acceptance.
- The complete eight-file native process/retry/runtime portfolio passes 307
  cases with seven platform skips. Failure-injection covers Job termination
  and output-drain refusal, native spawn-code retention, finite attempts and
  reservation of cleanup time inside the total budget. Existing acquisition,
  telemetry, whole-set preflight, runtime deadline and anchor behavior remain
  covered. All thirteen install contracts pass.
- Existing subprocess-double fixtures needed explicit routing for classification
  and planning tests; real Job-owner tests bypass those doubles. The final
  combined portfolio verifies that boundary rather than faking an empty Job
  for a subprocess double. No production containment guard was disabled.
- Fresh synthetic Jobs and native child processes exercise the changed
  first-touch process boundary; no dependency or installer-construction code
  changes require a full clean-room build. Hosted POSIX/lint, exact-head review
  and CI remain publication gates. Live rollout is not authorized by this
  source slice and remains a separate running-state obligation.

### 2026-10-16 - Timeout recovery review corrections
- #6932's first review identified one high and three medium issues. Replaced
  the raw Windows creation flag with the shared headless helper, as required
  by the actual failing CI guard. Local headless admission passes for all
  thirteen adopters; no exemption or rule change was introduced.
- Extended explicit subprocess-double routing to quick-skip and pivot-pruning
  consumers. The embedded native preflight process now stubs the acquisition
  adapter directly, pins a synthetic CLI and requires the actual required
  preflight diagnostic, not just exit 1 and an absent activation marker.
  Its fresh interpreter resolves the declared canonical process helper rather
  than an older already-installed test helper.
- Added an opt-in live desktop regression using the existing Win32 window and
  foreground observer: two windowless-parent cycles, real console descendants,
  forced deadlines and native process-handle exit checks. The install contract
  names the exact command and prerequisites. The current host has no observable
  foreground window, so that lane reports unavailable rather than claiming
  headlessness proof; it stays outside the fast required lane.
- Current-head review and hosted CI remain required. Unsupported POSIX/install
  timeout recovery, canonical preparation/readiness, release and running-state
  acceptance remain open.
- Final eleven-file review-corrected coverage passes 340 cases with eight
  explicit platform/desktop skips. The enlarged selection exceeded the
  existing 300-second single-sub-suite budget, so the existing runner partitions
  exactly the same files into disjoint six- and five-file groups: 133 passed
  with seven skips in 201.94 seconds, then 207 passed with one skip in 220.56
  seconds. Per-test, 300-second sub-suite and 900-second aggregate limits are
  unchanged; no assertion, case or required CI lane was dropped.

### 2026-10-16 - Current-base authentication contract integration
- Exact-head review 5544132183 Approved `2bc9efb9b2186316e1fa235c1989f1bf7abad44f`
  with no open findings, but concurrent #6876 changed acquisition's auth
  environment and actual-use observation contract, producing one real conflict.
  Preserved an owned backup and reconciled both behaviors rather than dropping
  the auth gate or the confirmed-cleanup boundary.
- Windows updates now receive the auth session's exact child environment,
  including an explicitly empty mapping, through its existing acquisition
  wrapper. Install restoration retains the canonical injected-runner path.
  Cleanup failures after launch retain actual credential-use evidence;
  containment refusals before execution do not claim use.
- Regression-first exposed a post-launch pipe error escaping as an OS spawn
  error. It now becomes an explicit unverified-cleanup failure, so a sharing
  code cannot authorize replay without proof. Actual spawn errors still retain
  their original native codes and policy.
- The complete auth/probe/usage/Windows-owner selection passes 169 cases with
  two platform skips. Focused coverage preserves scoped routes, unchanged parent
  environment, actual-use versus availability, empty environments, refusal
  precedence and finite retry. Current-base consumer integration, fresh review
  and required CI remain before merge; no deployed state was changed.
- A cold native fixture timed out before publishing its grandchild witness.
  Synthetic installers and acquisition children now establish bounded readiness
  before exercising the same command deadlines; the desktop observer binds
  native child handles before acknowledging readiness. The assertions, command
  deadlines and harness limits remain unchanged.
- Current-base runtime/acquisition/telemetry coverage passes 336 cases with
  seven platform/desktop skips, in the runner's existing one-file sub-suites.
  Together with the auth selection, all fourteen selected files pass 505 cases
  with nine explicit skips. Every file stays below its 300-second limit and the
  invocation retains its 900-second aggregate bound; the largest files took
  174.49 and 207.44 seconds. No timeout was widened to hide a failure.
- The externally stewarded #6867 is now confirmed merged into `main` at
  `ad7376904b0e622f7f44abf516cbef560b322f36`, from head
  `332c6fd5e4d89b0cb7b7cce5b0409ab3ebd034aa`; its source attribution names the
  operator-selected recipient. This resolves the source bootstrap dependency,
  not containing-release consumption, installed acceptance or retained local
  worktree/claim cleanup. #6707 remains unchanged with its canonical owner.

### 2026-10-16 - Windows timeout recovery source merge
- #6932 squash-merged into `dev` at
  `e4d423748e9453e1127d54c27b543f6db31b83dc`. Exact-head review 5545098715
  Approved `7dd9d63f42b3713152b99b84a651225b41261cb3` with zero open
  findings. Both current-head CI runs succeeded; the later run 38872721613
  completed before merge. No pending check was bypassed.
- Re-read the complete Plan and Validation Plan. Marked only confirmed Windows
  payload-update timeout recovery complete. POSIX/install timeout replay and
  durable interrupted restoration remain blocked; preparation, readiness,
  retained-candidate reuse and installed-state acceptance remain open.
- Managed pull-forward refused replaying the three already-squashed commits.
  Preserved an owned backup and verified identical stable patch IDs for their
  complete cumulative diff and the actual merge before advancing onto `dev`;
  no unmerged change was dropped.
- #6707 remains draft at its unchanged canonical head, without an accepted
  preparation/retention handoff. Keep that owner's construction boundary.
  Continue independent durable-recovery coordination and containing-release
  tracking; a source merge does not authorize rollout or prove running health.
- The next independent slice first reviews the durable activation-restoration
  boundary in `design.md`. Recovery requires the original snapshot and a
  durable positive child-settlement witness; a dead parent or missing PID cannot
  authorize another writer. The first lane restores witnessed interruptions
  without enabling install-timeout replay. Selection of an applicable canonical
  home-wide custody primitive is an explicit pre-implementation design gate.

### 2026-10-16 - Durable recovery design reviewed; custody selection next
- #6989 squash-merged into `dev` at
  `6e6c5e46c8d2f4af46f950b549bc6d184578630b`. Exact-head Copilot review
  5545906362 Approved `73c69788f6ce58a9d3d47188389f0bc5d0263c35` with no
  open findings; required CI 38881676280 succeeded before merge.
- Re-read the Plan and Validation Plan. This is a reviewed protocol, not a
  completed implementation or a running-state claim. The next gate is selecting
  canonical operation-scoped, crash-releasing home-wide custody. Existing
  installation-context directory locks retain dead-owner reservations for
  explicit repair; the daemon-oriented single-instance lease and ZDD's
  cutover-specific lock have different scopes. Do not silently repurpose those
  contracts or add an updater-local mutex.
- Containing promotion 38874649872 failed only Worktrees job 116899383732 at
  `6b2fa0ac2887a8fda669dfb8356e5c6083f6729d`. Sixteen complete sub-suites
  passed; the final seven-file group was stopped with exit 124 after receiving
  only 5.86083 seconds of the unchanged aggregate budget. This is the existing
  #6919/#5694 portfolio-performance boundary, not an assertion failure or an
  accepted flake. Routed the newer witness to #6919 in comment 6212960680.
  No competing optimization, budget increase, coverage deletion, forced retry
  or release waiver was started.
- #6707 retains preparation ownership and has not accepted the adapter request.
  The parent remains Active. Canonical custody selection, durable recovery,
  preparation/readiness/reuse, legitimate promotion and separately authorized
  local healthy-current proof remain open.

### 2026-10-16 - Canonical operation custody and witnessed restoration
- The operator selected the shared operation-custody extension, not an
  updater-local lock or a repurposed daemon lease. The canonical lease library
  now provides process/thread-owned, crash-releasing custody using its existing
  OS locking primitive. Same-process Windows handles are also serialized;
  ownership checks retain file identity without deleting or PID-reaping locks.
- The canonical activation-state owner persists the original snapshot before
  launch, and permits recovery only after a positive process-owner witness.
  Interrupted settings/inventory restoration reuses that snapshot and verifies
  both postconditions before clearing the record. Unproven command settlement,
  invalid records, cleanup failures and failed verification retain explicit
  blockers rather than recapturing enabled post-install state.
- Windows inventory installs compose this opt-in protocol with the existing
  strict Job owner and scoped-auth wrapper. Install timeouts still fail without
  replay. Prior recovery consumes the command allowance, and final restoration
  backoff plus Job cleanup remain reserved inside the existing target budget.
  Legacy standalone preservation and runtime daemon behavior are unchanged.
- Complete canonical coverage passes 177 cases with six ordinary platform
  skips. Real native tests kill the actual owning process before/after witness
  publication and between restoration writes. They bind a grandchild's native
  handle before Job cleanup, proving no writer remains before witnessed
  restoration. Windows consumer/auth coverage passes 26 cases; existing
  retry/registered/quick-skip/pruning consumers pass 133 with one platform skip.
- The standalone activation CLI passes eight cases. An isolated import using
  only bundled library roots proves its new dependency closure without
  canonical source paths; nested materialization and canonical/transitive
  admission contracts pass 28 cases. Source/copy parity, all thirteen install
  contracts and headless-launch admission pass.
- Fresh synthetic homes and actual native children exercise first-touch and
  interruption directly. No constructor or external dependency was added, so
  a full fresh-machine installer build is not repeated. Hosted POSIX coverage,
  targeted lint and exact-head review/CI remain publication gates. Local
  governed Ruff is unavailable; its required hosted check is not waived.
  Live rollout is still separately authorized and blocked on legitimate
  containing promotion, not claimed from these fixtures.
- Documentation impact: updated canonical and standalone library contracts,
  the unified-update installation contract and the selected design. All eleven
  direct consumer identities carry patch intent; no source versions were
  hand-edited. Preparation stays with #6707, performance stays with #5694/#6919,
  and the parent completion gate remains open.
- Final consumer inspection caught a resume-path defect before publication:
  an interrupted install may already have copied its payload, causing a later
  update verb to bypass install-only restoration. Every Windows acquisition
  now enters canonical custody before auth or verb selection. Native tests
  resume through the real acquisition entry point with the payload present,
  prove restoration precedes update, and prove an auth refusal still leaves
  original activation restored without launching a command. A pre-witness
  transaction blocks both verbs. The complete updated four-file consumer
  portfolio passes 89 cases with one platform skip, including ten native
  preservation/budget cases; no cleanup proof or retry admission was weakened.
- Rebuilt the actual declared Worktrees test environment from scratch through
  governed `--reinstall --prepare-only`, then exercised the native consumer
  again. A subsequent validation failure exposed error masking: an exception
  before durable witness publication was replaced by a generic unproven-state
  error. The original triggering cause was not recoverable from that run.
  The canonical context now retains the record while propagating the actual
  body failure; confirmed timeout/restoration exceptions keep their contract.
- Added bounded publication recovery for explicit Windows sharing/lock errors
  32/33, with custody and previous-record revalidation before the same transition
  is retried. It neither recaptures activation nor replays a command; permission
  and exhaustion cases stay terminal. Witness-publication and final-restoration
  delays are both reserved inside the original budget. A separate presence flag
  ensures a disappeared inventory record cannot be mistaken for an originally
  absent activation flag. Fault-injection covers all these boundaries; the
  final canonical 177/6 and consumer 89/1 selections above pass unchanged
  time/resource limits.
- The final timeout-policy check also covers the inner watchdog's exit 124:
  an install cannot replay merely because that output contains a transient
  marker. Captured refusals precede timeout reporting after verified cleanup.
  The native preservation selection now passes twelve cases, and the Windows
  payload-owner selection passes seventeen with one platform skip.

### 2026-10-16 - Source review custody corrections
- #7033's exact-head review identified two high-severity defects in the new
  generic operation primitive: forked children retained descriptors after the
  parent released custody, and concurrent acquire attempts on the same
  instance could clear the successful owner's fields.
- Descriptor setup, ownership publication and release now share one process
  guard, including the fork boundary. Child callbacks close inherited raw
  file objects without explicitly unlocking the parent's open-file description;
  inherited objects still reject child execution. Only the attempt that
  reserved custody may clear fields while the guard is held.
- A deterministic two-thread gate reproduces the same-instance interleaving
  and checks actual independent-process kernel exclusion while the winner
  stays active. Its native Windows selection passes five cases with three
  POSIX skips. A new real-fork case keeps the child alive after parent release;
  required hosted Linux CI must execute it before merge.
- The separate failed CI step was literal F401 for a now-unused
  `SimpleNamespace` import in the updated auth fixture. Removed that import;
  required lint is rerun normally, not bypassed. Fresh review and all
  exact-head checks remain open.

### 2026-10-16 - Transitive release fixtures and native budget evidence
- The next exact-head CI run passed the shared-library and real POSIX fork
  lanes, but Logger's two fresh-release tests failed because their synthetic
  canonical root omitted plugin-activation's newly declared lease dependency.
  Added that library to the fixture and a direct materialization regression.
  Production context staging already copies the full canonical library root.
- The staged Worktrees import fixture had the same incomplete closure, hidden
  by importing the missing package from the development environment. It now
  stages the declared dependency and verifies activation, preservation, lease
  and operation modules all load from the installed copy after source cleanup.
  The complete staged-install file passed four cases with one platform skip.
- Actual Windows Logger provisioning passed in 291.00 seconds within the
  unchanged 300-second group budget. The separate snapshot-only first-use
  test exhausted that budget without an assertion failure. Native inspection
  observed signed-Python venv/ensurepip construction before offline package
  effects; avoiding fixture cache copies or selecting the real offline-UV
  venv route did not establish a passing first-use result. Those experiments
  and diagnostic instrumentation were removed, not shipped as speculative
  performance fixes.
- Retain the native first-use discrepancy with the existing #5694/#6919
  performance owner. No deadline increase, omitted coverage, competing
  constructor, retry waiver or installed update is authorized. Current-head
  review/CI, native budget closure and the parent running-state gate remain open.

### 2026-10-16 - Offline package-effect closure corrected
- The next hosted Logger run reached the snapshot-only CLI invocation and
  reported `ModuleNotFoundError: single_instance_lease`; 482 other cases
  passed with twenty-nine platform skips. This confirms a second fixture
  defect, not a production UV dependency-resolution failure: the offline
  double copied only each explicitly requested project's sources, ignoring
  its declared local transitive dependencies.
- The double now copies the declared local runtime dependency closure, with
  fixture-root bounds and cycle protection. Real canonical package code,
  constructor, admission, health, snapshot-only invocation and existing
  assertions remain intact. No production installation loop was changed.
- A focused native regression constructs a real fresh venv without pip,
  invokes the actual offline-UV executable for plugin-activation and verifies
  isolated imports and installed origins for activation, preservation, lease
  and operation. It and the materialization regression pass in 49.09 seconds
  combined under unchanged thirty-second item and 300-second group limits.
  The first broader regression shape exceeded its item cap; reduced only its
  redundant fixture/launcher preparation, not the package-effect/import checks.
- The earlier Windows first-use deadline evidence does not prove the later
  import succeeds; hosted end-to-end verification must cover that separately.
  Existing native budget and running-state obligations remain open. The
  preceding exact-head Copilot review reported no open issues, but the next
  source head still owes its own review and CI.

### 2026-10-16 - Fork cleanup error isolation
- The subsequent exact-head review's full agentic lane did not start before
  its runner deadline. Its fallback raised two portability findings and one
  fork-cleanup finding; none was treated as passing merely because the verdict
  was a Comment.
- Both portability findings assume `stat.FILE_ATTRIBUTE_REPARSE_POINT` is
  absent on POSIX. The supported CPython standard library defines it
  unconditionally, including Python 3.10; native hosted POSIX custody and
  preservation tests also execute that expression. Replied with that source
  and execution evidence rather than silently disabling the reparse mask.
- A native close error could stop the child callback before it processed other
  inherited descriptors. Cleanup now attempts every descriptor, resets the
  child registry/guard and only then reports the first native error through
  Python's unraisable fork-callback path. Errors are not silently suppressed.
  The standalone source copy is regenerated from the canonical library.
- A portable injected-close regression verifies later handles close, registry
  and guard reset, and original error identity. The real live-child fork
  regression now covers two independent held locks, with and without an
  injected error. Native Windows focused coverage passes six cases with four
  POSIX skips; hosted POSIX must execute the faulted fork case before merge.
  Required current-head CI/review and the parent running-state gate remain open.
