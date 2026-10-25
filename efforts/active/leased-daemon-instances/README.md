# Leased Daemon Instances

- **Slug:** `leased-daemon-instances`
- **Repo:** copilot-extensions
- **Branch(es):** Dedicated integration branch; reviewed proposal before source changes
- **Created:** 2026-10-23
- **Status:** Active
- **Vision:** `visions/plugin-services/README.md` - `attributed-daemon-instances`
- **Umbrella issue:** #8403

## Guiding Intent

Every shipped daemon launch has attributable instance custody, including
source-only, native-supervised and container entrypoints. Explicitly disposable
host-side test instances cannot become permanently orphaned merely because they
use independent state roots. Production election, cutover and recoverable-work
ownership remain with their existing lifecycle owners.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Integration owner | Plan, public coordination, contract integration, validation and PR stewardship | #8403 |
| Shared process owner | Common instance/lease/cleanup substrate and native bindings | Integration owner |
| Caller owner | Complete daemon-entrypoint census, caller migration and coverage guard | Integration owner |
| Installer owner | Normal install/update reconciliation adapters | Integration owner |

## Coordination

- **Topology:** One integration branch with non-overlapping source slices.
- **Host (owns PRs):** Integration owner.
- **Delegates:** Shared process, caller and installer owners; no independent
  commits, publication or deployment from delegated slices.
- **Handoff:** Return bounded source/test results to the integration owner;
  preserve original instance IDs and unresolved lifecycle effects.
- Public artifacts remain generic. No downstream-private runtime, session,
  machine, test-directory or incident identifiers are required.

## Context

The read-only census found shared Python launchers, Python-free native
launchers, raw detached paths, double-fork entrypoints, native supervisors,
standalone services and test fixtures. Instrumenting `spawn_daemon` alone would
leave bypasses. Existing root-local singleton and runtime-retention leases do
not provide cross-root disposable-instance discovery.

Existing lifetime, installation-cell, process-identity, construction, retention,
native-supervisor and cutover owners remain authoritative. Shared registration
must not require a running sibling service or a particular installation venue.
This extends the service-model vision and the lifecycle-supervision pattern;
it is not a change to the meaning of singleton supersession.

## Request

The operator proposed:

> Should we consider forcing those installs to be tracked somehow, annotated with "lease" markers, and then have them get garbage-collected by future normal installs?

After accepting the shared instance-lifetime design:

> Agree. Let's get this added consistency across all daemon launches

The operator confirmed the effort slug and these defaults:

> 10-minute renewable lease; 4-hour hard maximum
>
> Note that if an agent runs a test, and in the middle of the test does an ask_user tool call, it could be waiting for a day while the tests is still running in the background. If only hands-off tests make these then the 4 hour max lifetime is good

The shared design was agent-proposed and operator-accepted: distinguish
ephemeral instances from installed versions; retain owner and exact process
identity; limit deletion to declared disposable artifacts; preserve production
cutover; use installer reconciliation as a backstop rather than the primary
cleanup mechanism.

For Python-free Linux lease enforcement, the operator selected `prebuilt`:
ship the native helper with supported release artifacts and compile only during
explicit source builds. Ordinary production startup remains compiler-free.

## Plan

### Phase 1 - Reviewed contract

- [x] Claim #8403 and inventory shared, direct, native-supervised, standalone
  and test-fixture daemon launch paths without runtime effects.
- [x] Review and merge this proposal and its governing intent before source
  implementation.
- [x] Record the complete callable-scoped launch census as a checked contract,
  distinguishing residents, supervised service children, finite workers,
  caller-contained tools and venue-owned agent bodies.

### Phase 2 - Shared instance custody and finite test lifetime

- [ ] Add one service-independent shared instance inventory outside disposable
  state roots. Reserve before detached creation; bind actual process-birth
  identity and startup admission before application work/readiness.
- [ ] Cover direct/native-supervisor startup as well as launcher wrappers.
  Source provenance is supported without pretending a source launch is an
  installed production generation.
- [ ] Track production instances without introducing a production expiry or
  replacing singleton, cutover, supervisor or recoverable-work authority.
- [ ] Require explicit ephemeral-test ownership and purpose. Unattended tests
  default to a renewable 10-minute lease and 4-hour hard maximum.
- [ ] Require an explicit pause-tolerant lifetime for interactive tests; do not
  silently apply the unattended maximum to a test waiting for human input.
  Renewal belongs to the test lifecycle owner, not agent-turn activity.
- [ ] Enforce expiry independently of the launching command, including its
  abrupt disappearance. Preserve contained-test suppression and existing
  Windows outside-Job adoption/uncertain-outcome rules.
- [ ] Reuse native birth identity and per-instance mutation custody. Avoid a
  second coordinator, cryptographic admission chain or capacity scheduler.

### Phase 3 - Complete caller and installer integration

- [ ] Integrate all shipped resident/service startup paths and shared
  Python/PowerShell/Bash launchers, including managers, standalone services and
  explicit test fixtures. Preserve Python-free native launch capability.
- [ ] Include the Linux kernel helper in supported release artifacts; compile
  explicitly for source builds, never implicitly on normal daemon startup.
- [ ] Add a mechanical coverage guard that refuses new unclassified bypasses;
  comments or containment flags alone do not establish instance admission.
- [ ] Add finite, explicit reconciliation to admitted normal install/update
  paths, including healthy/reuse returns. No destructive import, status,
  session-start or resolver sweep.
- [ ] Reclaim only expired, explicitly disposable instances through the same
  cleanup owner: verify process identity, confirm stop, then settle exact owned
  runtime artifacts. Unknown custody, reused PIDs, live protected work, changed
  paths and unsafe filesystem indirection hold with diagnostics.
- [ ] Preserve source checkouts, installed code slots, retained proof files,
  unrelated entries and unsettled cleanup evidence. Partial cleanup remains
  resumable under the original instance identity.
- [ ] Update owner documentation, distribution/vendoring declarations and
  changefiles for actual affected consumers.

### Phase 4 - Qualification and completion

- [ ] Run the validation plan, publish the source PR and resolve substantive
  review/CI findings through merge.
- [ ] Update this effort from actual merged results; resolve every plan and
  validation item before setting Done and finalizing.
- [ ] Report source/release/installed state separately. No runtime rollout is
  authorized by this implementation request.

## Validation Plan

- [ ] Pure tests cover reservation/admission/renewal/expiry, production
  non-expiry, interactive-policy refusal, concurrent mutation, PID reuse,
  malformed/foreign records, safe paths and unknown-outcome holds.
- [ ] Actual contained native-process tests prove discoverability across
  isolated roots, expiry after caller loss, exact-process stop, descendant
  settlement and protected/unrelated-instance preservation.
- [ ] Real consumer checks cover fresh install, repeat install, healthy/reused
  runtime, update, missing registry, permission failure and interrupted cleanup
  without changing the selected production generation.
- [ ] Shared fixtures exercise Python and Python-free native protocol parity.
- [ ] Actual built-artifact tests find and execute the packaged native helper
  without a runtime compiler; source-build tests make compilation explicit.
- [ ] Extend an appropriate clean-room scenario with synthetic inputs; record
  exact supported lanes and explicit reasons for unavailable ones.
- [ ] Windows launch qualification retains zero visible windows/focus changes
  from a windowless parent and verifies caller-Job cleanup behavior.
- [ ] Measure incremental launch/reconciliation work; no whole-machine process
  scan, synchronous export or new per-request helper service is introduced.
- [ ] Run affected consumer tests, headless/coverage guards, installer contract,
  documentation checks and the repository's required change-scoped gates.

## Proposal

Reviewed in #8433. Source integration, qualification and review corrections
remain open. Completion follows the admitted shared ownership and complete
caller coverage, not one launcher or one platform passing.

## Journal

### 2026-10-23 - Scope and source census

- Claimed #8403 and confirmed `leased-daemon-instances` with the operator.
- Completed independent read-only shared-launch, caller and installer evidence.
- Captured the unattended defaults and the interactive human-wait exception.
- No implementation, service activation or deployment has occurred.

### 2026-10-23 - Reviewed implementation admission

- Proposal #8433 merged after an approving automated review with no findings
  and successful required CI.
- Advanced the same integration worktree onto the merged `dev` base.
- Implementation starts with the shared API, followed by caller and installer
  integration; the remaining Plan and Validation Plan gates remain open.

### 2026-10-23 - Integration and independent qualification

- Shared API import in a fresh disposable home created no state or processes.
- Focused installer-adapter tests passed; these do not establish complete
  native expiry or artifact reclamation.
- A new credential-free, no-network clean-room consumer observed abrupt-owner
  expiry, but production admission and actual native installer artifact cleanup
  still require diagnosis. No failed lane is counted as acceptance.
- The operator selected a prebuilt Linux helper rather than a hidden first-use
  compiler dependency. Native and packaging work remain within this effort.

### 2026-10-23 - Actual consumers and review corrections

- The complete source clean-room scenario passed all six controls, including
  explicit native construction, production protection, runner renewal/hard
  expiry, abrupt owner loss and real installer artifact reclamation.
- Corrected the Python installer adapter's wrapper location; native helper
  roots no longer need to contain the installer's own wrapper.
- The process-hygiene mock retained all six original default scenarios:
  50-client floods, ten daemon contenders and five repeated rounds passed
  without leftover processes after adding shared test custody.
- Gateway fixture tests passed real admission and environment restoration on
  success/failure; the production-control probe refuses ordinary host use.
- Both launch-coverage and headless guards passed. Guidance rendering had zero
  blocking findings and remained within the exact per-file budget.
- Independent core review found failed-launch reservation settlement and
  automatic-source provenance gaps. Corrections, final native-artifact
  requalification, caller regression fixes and source publication remain open.

### 2026-10-23 - Frozen-source qualification

- Definitively refused launches retire unconsumed reservations. Started or
  uncertain outcomes retain the original receipt without replay; executable
  provenance follows the actual child lookup contract.
- Production finite workers retain parent-survival behavior and have no test
  deadline. Cleanup settles only control metadata after actual process exit,
  preserving production artifacts.
- Independently reran the frozen Windows core packet: 198 passed, five
  platform skips and 33 tests outside the focused selection. The caller/guard
  packet passed 47 tests; the Bridge session-host packet passed 119 with four
  platform skips.
- The final source clean-room passed all six controls in its explicitly
  prepared source-build image. An initial attempt in a compiler-free image
  stopped at the declared build prerequisite; it did not exercise lifecycle
  behavior and is not counted as acceptance.
- Full installer-contract and documentation-consistency checks passed after
  regenerating the installer engine's output declarations.
- Twenty contained reconciliation repetitions measured 0.055 ms per empty
  call and 2.586 ms with one production record, with zero process-identity
  queries for production. Three finite-launch wall samples were 41.689,
  42.580 and 39.813 ms. These small local samples are not tail-latency
  qualification.
- Final built-artifact/native qualification, independent review and source
  publication remain open. No deployed runtime was changed.

### 2026-10-23 - Resident and normal-installer consumer checks

- Vault admission now spans preparation, handoff and the blocking resident
  body. Launcher-only paths do not initialize the service or create resident
  records. Independent startup, registry-count and cutover tests passed all
  43 cases; the focused review found no remaining coupled defect.
- The real retained-UV installer engine exercised fresh construction,
  installed health, repeat/healthy reuse and update admission with a synthetic
  product and the actual shared wheel, without replacing the engine or
  reconciler. Protected live production, selectors and installed slot bytes
  remained unchanged while expired test artifacts were reclaimed.
- A real permission failure interrupted artifact cleanup after its first
  removal. The second artifact and cleanup evidence were retained; restoring
  access and calling normal reuse completed cleanup while preserving proof.
- Non-root qualification exposed unreadable inventories silently appearing
  empty in Bash and Python. Those errors must remain diagnostic holds, distinct
  from a genuinely absent inventory; final package qualification follows the
  corrected bytes.

### 2026-10-24 - Bounded inventory progress and qualification hold

- A real 130-record inventory exposed repeated bounded sweeps never reaching
  an expired disposable instance behind protected records. Python,
  PowerShell and Bash now share inventory-private resume metadata, advancing
  past every visited record and wrapping without changing process custody,
  production lifetime or per-call count/time limits.
- Focused independent review found no actionable cursor or permission-handling
  defect. The final contained Windows packet independently passed 218 tests,
  with ten platform skips and 33 tests outside the focused selection.
- Twenty local contained repetitions measured 0.062 ms per empty reconcile
  and 5.195 ms with one production record, with zero production process-identity
  queries. Three finite-launch samples were 40.815, 31.934 and 34.248 ms.
  These small samples are not tail-latency qualification.
- The final launch/coverage guards, installer contract for all 13 runtime
  plugins and documentation consistency checks passed.
- Final frozen prebuilt-artifact, installed-native and real normal-installer
  consumer requalification is blocked by the local Linux container engine.
  Ordinary start/restart did not restore it; the operator will restore that
  prerequisite. Earlier artifact results do not qualify the final bytes.
- Source publication, review/CI, merge and effort completion remain open.
  No deployed runtime was changed.

### 2026-10-24 - Final artifact qualification

- After the Linux container engine was restored, final frozen-source core
  qualification passed 567 tests, with 53 Windows/WSL and three macOS skips.
  Installed native/cursor qualification passed 32 tests with two platform
  skips. The combined installer/artifact source packet passed 213 tests
  with two explicit acceptance skips.
- Built and verified the final Linux x86-64 wheel and native helper; all 31
  packaged source files and the build hook match the candidate. The packaged
  and source bindings execute with Python and compilers physically absent.
  Non-root permission probes ran on Python 3.12 and 3.13; Python 3.14's actual
  image was unavailable, so its absence semantics retain pure contract tests.
- Independently replayed the installed-wheel normal-installer consumer:
  fresh, repeat, healthy reuse, update and permission-interrupted cleanup
  recovery passed. Protected production, selectors, slot bytes and retained
  cleanup proof remained unchanged.
- Repeated the final source clean-room: all six controls passed. Caller
  extractions preserve module ceilings without baseline increases; focused
  caller regressions, all source guards and high-signal lint passed.
- ARM hardware, actual macOS, production host/socket and full production
  feed/transport lanes are not claimed as qualified. The local all-dependency
  audit remains unable to complete npm TLS handshakes; no npm manifest changed
  and required CI remains an independent, unwaived publication gate.
- Implementation is frozen. Source PR review, required CI and merge remain
  open; this journal does not mark the effort Done or authorize rollout.

### 2026-10-24 - Rebased consumer qualification

- Reconciled the implementation with current development, preserving the new
  portable Bridge hosting-policy module and both documentation additions.
  Regenerated installer metadata and the additional Python-adopter copies from
  their canonical owner.
- Rebuilt the package against the merged process library: all 35 source
  members, the actual wheel hash and native executable hash were independently
  verified. Linux core passed 635 tests, with 53 Windows/WSL, five actual macOS
  and eight explicit managed-detachment-workflow skips. Installed native/cursor
  passed 32 with two Windows parity skips; the combined source packet passed
  213 with two separately exercised Linux acceptance skips.
- Independent final installed-wheel consumer replay passed in 17.23 seconds.
  A fresh rebased source clean-room passed all six controls. The Windows
  packet again passed 218 tests, with the same ten platform skips and 33
  tests outside the focused selection.
- Updated the launch census for removed bootstrap paths and newly shipped
  finite/interactive consumers. Manager, census and source-runner controls
  passed 148 tests; actual Bridge bundle startup and policy denial passed.
- Added explicit eligible local checkouts and synthetic provider fixtures to
  the older remote lifecycle mocks, retaining real hosting-policy evaluation
  and denial cases. The full Session Host file passed 123 tests with four
  existing skips; upstream policy/receiver regressions passed 86. No runtime
  policy was relaxed.

### 2026-10-24 - Source review and CI corrections

- Opened source PR #8720. Review identified missing namespaced Machines
  reconciliation and relative Manager roots. Machines now reconciles only
  after admission, including healthy reuse. Manager anchors CLI/environment
  roots before admission without resolving away filesystem indirection;
  215 tests and two finite-production proofs passed.
- The same installer audit found Index's separate coordinator. Its ordinary
  provision path reconciles after governance admission; pending recovery does
  so only after its existing transaction successfully commits. Refused,
  unknown and failed recovery do not invoke cleanup.
- Machines routing passed 12 native Windows and six Linux cases, with six
  PowerShell-only skips on Linux. The combined packet passed 225 with two
  explicit acceptance skips. Index passed 116 with seven platform skips,
  including seven new coordinator cases; the real installer consumer passed.
  Routing fixtures do not claim a fresh package build.
- Refreshed five exact unit-source bindings and admitted the native-builder
  import dependency. Eight new pure selector cases joined the explicit unit
  packet; effectful acceptance remained outside required CI. All 1,517 units
  passed locally in 207.438 seconds within the unchanged 300-second bound.
- Refreshed implementation-only Bridge fingerprints and the extracted
  lifecycle dependency with explicit unchanged-wire reasons. All historical
  packets, provenance, protocol generations and capability floors are retained;
  strict validation passed both contracts and all 814 fixtures.
- Required CI and review of the updated PR still gate merge. No runtime rollout
  has occurred.

### 2026-10-24 - Direct API and source-copy review corrections

- Direct Session Host admission now starts before listening and remains through
  completed shutdown, including connections and readers. Startup failure,
  cross-task close and launcher borrowing retain exact guard ownership. The
  full Session Host file passed 127 tests with four existing skips.
- Manager roots now use lexical absolute normalization, accepting parent-relative
  CLI, environment and callback roots without resolving filesystem links.
  Manager regressions passed 224 tests and two finite-production proofs.
- Scratch exclusion now occurs before canonical source/test copy traversal.
  Unfiltered build contexts retain strict link rejection. All affected copy and
  source-binding callers were audited; native Windows packets passed 384, 97
  and 42 tests. Independent offline Linux replay passed all 36 boundary cases.
  The exact eligible-unit packet again passed all 1,517 cases in 217.375 seconds.
- Added the explicit shared-library and standalone-service release targets,
  including the other changed update-driver project. No versions were edited
  by hand and no consumer entry substitutes for a library's own release.
- Credential-helper validation uses a dedicated disposable image with stock
  `gh`, no network or host authentication mounts. Repository-discovery probes
  found that a private HOME/config alone still inherited an enclosing checkout.
  A strict-parent ceiling and non-repository preflight close that fixture gap.
  The complete source/credential file passed 59 tests on Windows and 59 in the
  offline Linux image, without exposing credential values or masking skips.
- The new filesystem matrix remains on demand. Historical wire evidence and
  the unchanged unit budget remain intact; fresh PR review and CI still gate
  source merge and effort completion.
- Reconciled the newer upstream unit inventory without dropping its cases or
  changing the budget. All 1,595 merged units passed in 230.735 seconds.
  The qualified process-library source closure is unchanged.

### 2026-10-24 - Container account and proof-runner custody corrections

- Index container generation now prepares the selected account's passwd home
  before default registry admission. Five built-image cases include actual
  service startup/configuration without a registry override and unsafe-account
  refusals. Gateway's witness build likewise selects the invoking nonzero
  UID/GID while preserving the production image's default account; six real
  image cases cover account reuse, state ownership and refusal.
- Fixture renewal belongs to a separate supervisor. A private FIFO retains the
  proof runner's kernel-observed lifetime across the existing container mount;
  service children cannot renew themselves. Abrupt runner loss stops renewal
  and the exact child without a shared PID namespace or Docker socket.
- The offline Linux custody/model packet passed 96 tests, including seven
  native FIFO cases. The runner-loss case observes distinct owner/service
  identities, renewal beyond two short leases, then exact service death within
  eight seconds of killing only the runner and before the hard lifetime cap.
  These focused cases do not claim the entire host-driven Gateway witness.
- The supported Windows account/fixture/installer packet passed 179 tests with
  four platform skips. POSIX UID/mode-only witness tests are covered by the
  Linux packet, not claimed as Windows qualification. Four source guards,
  module ceilings, high-signal lint, installer contracts and documentation
  checks passed. Every pinned eligible-unit source binding remains exact.
- Updated the witness's SDK source closure for four upstream module additions,
  preserving its fixed allowlist. Source review, current-head CI and merge
  remain open; no runtime rollout is authorized or performed.
- Rebased onto newer development while retaining original-acquisition Host
  custody and every upstream unit. All 1,702 exact units passed in 180.016
  seconds within the unchanged 300-second limit. The contained Session Host
  packet passed 127 with four platform skips. Strict protocol validation
  passed both contracts and all 814 fixtures after refreshing only current
  merged-source bindings; historical provenance and captures remain intact.
- Current-head CI executed the eligible units successfully but rejected two
  oversized intermediate registry blobs created during conflict resolution.
  Folded the owned publication history without those intermediate blobs;
  the unchanged large-file gate now passes. No size exemption was added.
- Retained newer upstream automatic-maintenance fences alongside attributed
  spawning. The focused monitor/maintenance/housekeeping packet passed 37
  cases, including disabled and malformed-policy refusal before any spawn.
  Updated its test seam and the census for the upstream bounded policy read
  and replaced caller-contained streaming helper.
- Confirmed the retained Windows packet includes real windowless-parent,
  Job-boundary and visual-observer cases, not only launch-flag assertions.
  Separately exercised the explicit Python-free PowerShell bootstrap lane:
  both production and contained cases passed, two cycles each. The native
  fixture observed authorized survival versus Job-bound termination, lock
  custody and zero observed attributable windows or focus changes.

### 2026-10-24 - Embedded Gateway and coherent relative-path admission

- Embedded Gateway startup now admits before TLS, reload and listener work.
  Its retained guard spans completed shielded cleanup, startup failure and
  cross-task cancellation; app-runner startup is also an explicit census
  owner. Nested CLI/app entry borrows one process record. The worker's
  service/enrolled-identity packet passed 387 cases; independent embedded
  TLS and census integration passed 43.
- Gateway config and service-policy parents, MCP home, CLI socket and resident
  socket selection now collapse parent components lexically without resolving
  linked runtime parents. Coupled MCP consumers retain one launch-directory
  selection across cwd changes. Updated the home contract and its eight pure
  selector expectations rather than leaving consumers with divergent roots.
- Independent contained MCP integration passed 56 with five platform skips.
  Existing Windows closed-loop/proactor cleanup warnings remain disclosed;
  they do not replace test assertions. Focused Gateway CLI controls and eight
  Linux home selectors also passed. Refreshed only the two changed unit-source
  bindings; native process-library bytes and the unit budget are unchanged.
- Current source CI passed guards and actual units. The new review findings
  still require publication, fresh review and current-head CI before merge;
  neither that pass nor this journal closes the effort or authorizes rollout.
- Independent offline Linux replay passed all 11 Gateway lifecycle/path cases,
  including real parent-relative TLS startup, with no skips. Frozen source and
  installed process-library hashes matched; the test-owned container was
  confirmed removed. The updated 1,702-unit packet passed in 216.469 seconds.
- After the next upstream integration, all 1,704 eligible units passed in
  179.328 seconds under the unchanged 300-second limit. Strict protocol
  validation retained both contracts and all 814 fixtures. Kept newer module
  ceilings by extracting Vault's unchanged pure endpoint policy; its lifetime
  and advertisement packet passed 15 tests.

### 2026-10-24 - Newly shipped SSH service integration

- The rebase introduced the standalone SSH service. Its foreground now retains
  one instance guard through service operation and unsettled relay retirement.
  `NativeChildCustody` remains the native relay's leader/group owner. The
  complete census distinguishes that supervised child from both resident
  container entrypoints and names the foreground admission owner.
- Source packets passed 78 on Windows with one platform skip and 72 on Linux
  with seven platform skips. Independent foreground/root/census integration
  passed 42 tests, including valid absolute paths containing parent components.
  No native SSH-traffic or managed-rollout claim follows from these fixtures.
- The actual SSH hash-locked image now provides UID/GID 65532 with private
  passwd home `/data`, retaining an existing account name and prior-home
  contents. Native-prerequisite admission still precedes account preparation.
  Actual main/config/production admission passed without a registry override;
  installed main matched the frozen guarded source. Parent replay also passed.
- Two distribution cases passed inside the installed image, replacing the
  unavailable host installed-binding lane rather than bypassing its isolated
  import. Existing-account proof passed; linked home, foreign account/group
  collision and missing native prerequisites refused. Downstream hosting was
  synthetic; authentication and relay traffic are not claimed as exercised.
- Added the SSH standalone's own changefile. Core source/native bytes remain
  unchanged. All four source guards, module ceilings, installer contracts,
  documentation and high-signal lint passed; owned test containers are gone.
