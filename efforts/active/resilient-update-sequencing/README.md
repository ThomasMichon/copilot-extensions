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

- [x] Historical receipt/template fixtures cover same-lineage payload relocation,
  known pre-receipt formats, missing/retired projects and knowledge-only commands.
- [x] Foreign/cross-cell owners, edited bytes, corrupt receipts, ambiguous source
  lineage and case collisions remain protected and explicitly reported.
- [x] Failure between command and receipt publication, concurrent reconcilers,
  partial Windows siblings, cancellation and retry preserve recoverability.
- [x] Profile integration crosses the real Manager command boundary without
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
