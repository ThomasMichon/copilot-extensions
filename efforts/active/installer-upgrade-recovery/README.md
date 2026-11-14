# Installer Upgrade Recovery

- **Slug:** `installer-upgrade-recovery`
- **Repo:** copilot-extensions
- **Branch(es):** sequential bounded contribution PRs targeting `dev`
- **Created:** 2026-11-03
- **Status:** Active
- **Umbrella issue:** #10377
- **Related issues:** #9489, #7743, #9448, #7758, #10180, #6820, #7028, #7947, #6389, #5472, #10451, #9716, #10054, #10499, #10486, #10652, #10653, #10996
- **Vision:** [authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement)

## Guiding Intent

Drive high-impact installer/update compatibility failures to a verified usable
incoming installation through the latest supported explicit install/update
flow. Fresh-install success, a prepared candidate or a merged bug fix does not
prove that an affected older installation can recover.

This workstream closes the installer vision's existing authoritative replacement
intent; it does not extend it. Incoming readiness and every applicable current
selector, command, route, launcher and registration selecting the incoming
generation define success; fresh calls must reach that generation. Old recovery
protocols, provenance, stale installer transactions, queue fences, process-census
uncertainty and occupied old control homes cannot veto explicit replacement.
Old stateful wrappers and keepers may retain old code for existing clients.
Full adopted-client reattachment remains separate work under #10486, not an
incoming prerequisite; #9716 retains census ownership and #10377 coordination.
Unknown old cleanup is deferred and reported separately. Actual incoming
authenticity/admission, build, authentication, necessary durable-state
compatibility/migration, permission, I/O and health failures remain failures.
Preserve protected sessions and transfer or exclude conflicting work admission.
Outgoing central retirement is independent of incoming completion and
revalidates identity, role and then-current selection before eligible mutations.
Never sweep process names or force-kill protected state holders.

## Participants

| Participant | Role | Reached via |
|---|---|---|
| Workstream driver | Generic issue reconciliation, selected source fixes, acceptance and publication | Managed source handle `dusty-contraption`; #10377 |
| Existing entry and sequencing owners | Retain fresh-entry, command migration and update sequencing implementation | #7743 and #6820 |
| Existing construction and execution owners | Retain canonical construction, source-managed installation and installation-request execution | #9489, #7028 and #7758 |
| Existing recovery and lifecycle owners | Retain their scoped queue, historical cleanup, durable-state and central lifecycle work | #9448, #10180 and #7947 |

These are coordination dependencies, not new assignments or ownership transfers.

## Coordination

The [installation convergence map](../source-managed-runtime-installs/installation-convergence.md)
uses this effort's affected-installation cases to qualify the same incoming
path as a fresh install, not a separate legacy reconstruction algorithm.
Complete staging precedes live compatibility checks, state transfer and
activation. Preserve this effort's existing recovery/adopter/native owners,
holds and all open acceptance; no old footprint is declared recovered by
documentation or a completed package environment.

- **Topology:** serial bounded source units, with normal independent PRs where
  their edit and acceptance scopes do not overlap.
- **Host:** Workstream driver owns this effort, its PRs and completion gate.
- **Delegates:** none selected. A future bounded delegate does not inherit the
  parent objective merely by completing a source slice.
- **Claims:** recheck the issue, current source/release and open PRs before
  implementation. Reuse an existing issue rather than duplicate it. Preserve
  contributor expressions and active claims; coordinate an overlap before
  changing its implementation.
- **Resident-companion intake:** the finite [#5820 lifecycle family](acceptance.md#l5820---whole-installer-and-resident-companion-lifecycle)
  is planned here without changing its original implementation owners,
  claims, holds or activation gates. Preserve delivered staging/source credit
  and the complete version-slot/whole-lifecycle contract. #5827's CWD
  prevention is not acceptance of the resident Windows companion chain.
  #2395's temporary per-unit restart-inhibition/expiry remains separately
  open unless implemented OR explicitly transferred to its rightful
  supervision owner. This documentary intake neither accepts native proof
  nor authorizes updater execution, live companion operations or custody transfer.
- **Boundary:** this is a defect and upgrade-acceptance workstream, not a second
  installer, reset engine, artifact resolver, daemon, scheduler, provenance
  authority or repository-wide merge queue.
- **Handoff:** this README, [acceptance matrix](acceptance.md) and #10377 retain
  selected cases, owners, outcomes and the next source unit.
- **Effects:** source development, contained tests and normal source publication
  only. Shared installations, credentials, services, queues, sessions and
  deployment are not changed by establishing this plan.

The finite #7117 publication-lock/lifecycle/Manager/all-five caller-loss
continuation retains the existing shared-lock source claimant and actual #5529
conditional operation boundary, installation/queue/process/hook and original
application/source/native custodians. Existing #6820, #7758, #10499, #9726, #9468
and MCP-specific delivered reservations are not reassigned or reopened.
Canonical construction/recovery remains under its existing owners; no held
source-managed-runtime-installs ROOT/derived material is admitted through this
amendment. No repair, source appointment, rollout, lock/request settlement or
resource release is authorized.

## Context

Existing issues already describe unsupported UV options, an update entry behind
installed Python/Manager dependencies, first-foundation upgrades with existing
requests, and attributable historical cleanup. Some source corrections have
merged. An open issue does not prove that the original code defect survives,
and a source merge does not prove recovery through the released ordinary entry.

The workstream therefore starts from reproducible generic cases and actual
consumer contracts, not another broad rewrite or a copy of historical refusal
tests. The initial candidate families and their existing owners are in the
[acceptance matrix](acceptance.md). A candidate is not an implementation claim.

The authoritative references are the
[install contract](../../../docs/install-contract.md#authoritative-replacement-and-completion),
[service-contract evolution](../../../docs/patterns/service-contract-evolution.md)
and current architecture adoption boundaries. Existing #6820 sequencing,
#7028 installation and #7947 state work remain canonical for their mechanisms.
New caller syntax must not assume that an arbitrary installed older runtime
already supports a capability. A reviewed architecture cutover uses its
documented fresh migration entry rather than pretending old protocols work.

## Request

Public-safe requested outcome, verbatim:

> So, we need to take the high-pri issues and drive fixes for them. We also need to ensure that for any update incompatibilities that we ensure our latest install/update flow will be able to recover the user into a good flow once applied

The requested coordination destination is:

> Yes, hoist relevant issues to copilot-extensions and drive a workstream up there

Implementation decomposition below is agent-recommended. The request does not
authorize production updates, recovery mutations or new access.

## Plan

### Phase 1 - Reviewed scope and issue ownership

- [x] Establish #10377 and reconcile adjacent public workstreams before claiming
  their existing source units.
- [x] Land this canonical plan and acceptance matrix through repository review
  before implementation: #10379, current-head approval and required CI.
- [ ] Classify each selected candidate as a surviving source failure, released
  fix needing consumer proof, owned dependency or unsupported-contract migration.
  Hoist only additional generic gaps that do not already have an upstream issue.

### Phase 2 - Priority fixes and latest-flow recovery

- [x] Select the first bounded, unclaimed reproducible source gap, or obtain
  explicit coordination with its existing owner. Record its old-footprint case
  before changing production code.
- [x] Fix the owning operation and directly dependent callers; add focused
  red-on-old coverage and actual parser/process or installer consumers.
- [ ] Prove recovery through the latest supported normal install/update entry,
  including incoming readiness, complete current selection and preserved user
  state. Internal helper success is not whole-flow acceptance.
- [ ] Repeat for the selected priority cases, preserving existing construction,
  admission, durable-state and lifecycle owners instead of reimplementing them.
- [x] Qualify the bounded read-only project-registration prerequisite under
  #10996/U17 before migrating setup consumers. Compose the canonical repository
  and legacy project registry readers; preserve writers and ordinary routing.
  Prove unique alias/exposure resolution, genuinely fresh unclaimed defaults,
  ambiguity/collision/read-failure refusal and no mutations through the native
  CLI boundary. Source release is not caller adoption or installed recovery.
- [ ] Qualify the incoming inspection bootstrap under #11083/U18. Run the
  existing reader from canonically constructed incoming code without consulting
  or changing the selected runtime, registrations, selectors or service state.
  Keep preparation/read failures distinct from absence, installation and caller
  adoption; U17's delivered source/release remains complete.

### Whole installer and resident-companion lifecycle - #5820

The complete finite family is retained in
[L5820](acceptance.md#l5820---whole-installer-and-resident-companion-lifecycle).
It closes an existing authoritative-runtime-replacement vision delta, not
a new recovery/provenance veto on explicit incoming replacement.

- [ ] S1: keep only fast payload copying in an attributable version-slot
  OR equivalent pending candidate. Build, health, cutover and task waits
  stay outside the replaceable payload throughout their lifetime; preserve
  immutable completed slots.
- [ ] S2: staging failure hard-fails OR uses an enforced bounded protected
  attempt; never silently execute unprotected in place. The original
  alternative does not require both implementations.
- [ ] S3: independently bound background reconciliation and its descendants,
  tree custody, uncertainty and owned cleanup. A staged child's UV timeout
  alone is not the independent installer/reconciler lifecycle bound.
- [ ] S4: cover the exact registered resident `dtssh` start/stop/health
  command AND waiting PowerShell/host-helper descendants throughout
  supervision, health, restart and update.
- [ ] S5: retain selected source/provenance, argv/config/service identity,
  containment, child exit, readiness/reporting, exact-generation ownership
  and owned cleanup, without using old-state uncertainty to veto incoming
  completion under the governing replacement contract.
- [ ] S6: prove an ordinary native Windows plugin update while that companion
  is actively supervised, without manual killing, a retry race or stopping
  unrelated/shared trees; prove automatic post-update supervision/readiness.
- [ ] S7: preserve inherited #2395's separate temporary per-unit
  restart-inhibition/expiry acceptance unless implemented OR explicitly
  transferred to the rightful supervision owner; no owner or resource release
  follows from documenting this family.
- [ ] V5820-L: distinguish payload exclusion, immutable-slot preservation,
  staging-failure alternatives and independent installer/reconcile descendant
  lifetime controls; retain all identity, exit, uncertainty and owned-cleanup
  evidence without substituting a child UV timeout.
- [ ] V5820-N: exercise the exact registered native Windows resident companion
  AND its waiting descendants through ordinary update while actively supervised,
  proving automatic post-update readiness and no manual/unrelated-tree stop.
  Keep this witness distinct from rename/CWD fixtures and from #2395's
  separately open inhibition/expiry acceptance.

#### Finite interrupted-installer recovery continuation - #7117

- [ ] **IR7117-P1:** Retain a supported standalone inspect/repair operation OR
  exact actionable invocation for interrupted publication locks: exact
  installation/lock and demonstrably inactive owner, retained evidence, refusal
  of live/foreign/concurrent/ambiguous ownership; fresh-process interruption/
  repair/ordinary update and safe complete-slot reuse, including reboot/PID-reuse/
  incomplete-write controls. Original cause remains unestablished. Credit
  delivered #7814 source/release; safe old-slot reuse is an optional result,
  never a veto of authentic fresh incoming replacement.
- [ ] **IR7117-P2:** Retain read-only exact pending unattributed lifecycle
  reservation inspection and evidence-bound recovery: exact manifest/payload/
  source/environment/policy under existing publication lock, unrelated fields
  preserved, changed/foreign/live refusal and pending/not-ready until authorized
  ordinary update completes. Credit #9474/#9490 source/release without
  transferring retained candidates.
- [ ] **IR7117-P3:** Preserve fresh-Manager legacy registered source/profile
  capture BEFORE replacement acquisition. Authenticate source/environment/owner
  without depending on unavailable incumbent publication/uninstall inventory;
  preserve mutation checks, no invented historical profile or weakened uninstall.
  Paused #6820 and #9623 teardown custody remain separate.
- [ ] **IR7117-P4:** Retain ALL FIVE MCP/Containers/Dispatch/Machines/budget
  sessionStart paths and original Windows SSH caller-Job loss, with environment/
  configuration/consumer/hook budgets and Python-free budget hook. Through
  existing attributed lifetime owners survive ALL caller-owned cleanup
  boundaries OR report truthful recoverable state without indefinite installer
  fencing. One escape flag/Job is insufficient; preserve test containment and
  reject default WMI/scheduler/service/queue-reset or replay of uncertain
  attempts.
- [ ] **IR7117-P5:** Preserve independently authorized fresh incoming replacement
  despite stale incumbent lock/provenance/recovery/queue state: durable
  configuration/user/application/session/repo/worktree data and connectivity,
  every requested ready runtime and applicable selector/route/launcher/service/
  task registration. Automatic inhibition remains automatic-only. Incoming
  authenticity/addressed-install authority/build/state compatibility/permission/
  I/O/health failures remain failures.
- [ ] **IR7117-P6:** Keep outgoing retirement independently restricted to
  eligible obsolete CENTRAL services/registrations, never session hosts/session
  processes/clients/connectivity; identity/role/then-current selection
  revalidated. Retain actual installation/queue/process/hook/MCP/source/native
  custodians and original alternatives. #10700/#10825 qualify their actual MCP
  source/release/native slice, not all-five, #7758 whole completion or installed
  rollout.

##### Local Validation - #7117

- [ ] **IR7117-LV1:** Resolve EVERY original/refined IR1-IR9/IRV1-IRV5 clause
  against the actual existing publication/lifecycle/Manager/hook/incoming/
  retirement consumers. Preserve exact owner birth/descendant/mixed-generation,
  changed-source/foreign/live/concurrent/ambiguous, termination/reboot/PID-reuse/
  incomplete-write and ordinary-update controls, with safe reuse OR fresh
  replacement honored at its own scope.
- [ ] **IR7117-LV2:** Exercise all five actual caller-loss boundaries and
  environment/config preservation, including Python-free budget hook and
  original Windows SSH case; survival OR truthful recoverability, no indefinite
  fence or weakening of caller-owned cleanup/test containment. MCP-only delivered
  results are not an all-five pass.
- [ ] **IR7117-LV3:** Pair ready/current incoming selection with stale-lock/
  queue/provenance, live-old-keeper/unknown-census/occupied-home controls and
  genuine incoming failures; preserve automatic-only inhibition, protected
  state/clients/connectivity and independent central-only retirement.
- [ ] **IR7117-LV4:** Preserve the existing shared-lock source claim and actual
  #5529 conditional operation boundary. Its exact underlying wording is not
  established by this intake: resolve it through the current owner before any
  affected repair/source appointment/operation or transfer judgment. This is
  not a ban on reading eligible outer #7117, no newly invented condition, expiry
  or ACK. #7117 stays OPEN pending its actual acceptance/custody conditions;
  source/review/release/installed/native remain distinct.

#### Existing-entry boundaries - #7743

- [ ] **P7743-U1:** Retain the complete #7743 fresh-entry contract in
  [U1-7743](acceptance.md): attributable incoming code before installed
  Python/venv/package/Manager probes; ordinary command/source/ref/project/argv
  and foreign protections; CMD/PowerShell/POSIX and headless/captured
  wait/stream parity. Preserve NullString selector absence/restoration,
  schema-3 candidate validation, read-only/revalidated legacy capture,
  automatic-cell precedence, registered provider/package/root authority,
  race snapshots and no redundant cross-component helper-byte admission.
  Keep Windows execv replacement wait/stdio, `update_bootstrap._wait`,
  Manager `engine_client.run_engine_passthrough` and
  `runtime_install --settle-slot` as separate hops. Preserve confirmed
  no-launch settlement, the primary error, fresh predecessor/routing and
  custody of unknown partial effects. Native16 target success is not the
  later consumer result; #6812 presentation/single-pass/missing-bin and
  30-second item/300-second group controls, and #8370 logging, stay separate.
  Marker-only POSIX selected-generation readiness is not broad lifetime,
  reconcile or broker takeover. Do not presume `same_kernel_boot` after
  reboot or renew quarantined results. The recorded 575.016-second exit-1
  and 140.0-second stale-lock experiment remains unsettled: no quarantine
  or replay before its original owner AND descendants meet the original
  settlement condition. That experiment's hold is not a veto on independently
  authorized incoming replacement. Preserve the existing source claim,
  owners, alternatives and evidence tiers; no new ACK or rollout follows.

### Phase 3 - Release and completion

- [ ] Drive each source PR through review, required CI, merge and containing
  release observation. Use changefiles for affected plugins; do not hand-bump
  versions or equate `dev` merge with release.
- [ ] Record source, release and contained-consumer outcomes separately.
  Installed/affected-host qualification remains a separately authorized lane,
  not an inferred result or an implicit deployment obligation.
- [ ] Resolve every selected Plan/Validation item or transfer it explicitly to
  a named tracked objective, update affected reality documentation, and archive
  only when this parent gate is satisfied.

## Validation Plan

- [ ] For each selected incompatibility, start from representative broken or
  incompatible prior state and exercise the real latest entry, not only a
  pristine installation or a mocked successful recovery.
- [ ] Prove required incoming runtime/service readiness and all applicable
  selectors, commands, routes, launchers and registrations, including fresh
  calls reaching the incoming generation. Distinguish package identity from
  running generation and whole-set completion from partial progress.
- [ ] Qualify MCP's cross-platform ordinary-entry fresh-settlement gate under
  #10652, retaining #10054/#10499/#7758/#7028 ownership and U12's delivered boundary.
  Pair successful incoming installation with a live old keeper, unknown old
  census and occupied old control home against genuine incoming source,
  authentication, build, permission, I/O and health failures. Use the incoming
  generation's owned namespace, not takeover of an unknown foreign listener.
  Record native platform results and unavailable lanes separately.
- [ ] Reuse #6389 for stale-capable-serving/fresh-admission acceptance. Exercise
  automatic `--force` with inhibited updates and held clients under #10653:
  force does not change request classification or authorize unsafe retirement.
  Keep #9716 census and #10486 full reattachment separate from incoming
  completion.
- [ ] Preserve configuration, credentials, repositories, worktrees, sessions,
  databases and accepted/uncertain effect history; repeat without manual cleanup.
- [ ] Retain actual incoming acquisition/build/migration/I/O/permission/health
  failures as failures. Preserve useful errors and confirmed child cleanup.
- [ ] Exercise healthy-current reuse, repeat convergence, source-worktree
  independence, automatic-only inhibition and protected-session/central-only
  retirement controls from [acceptance.md](acceptance.md).
- [ ] For changed peer boundaries, qualify current/prior callers and providers,
  supported rollback, missing metadata and unsupported requirements. Do not
  blindly replay an effectful operation with older syntax.
- [ ] G5820: validate the complete [S1-S7 resident-companion family](acceptance.md#l5820---whole-installer-and-resident-companion-lifecycle):
  payload exclusion throughout lifetime, immutable completed slots, hard-fail
  OR bounded protected staging failure, independent installer/reconcile
  descendant bounds and custody, exact registered native Windows `dtssh`
  AND waiting helpers under active supervision during ordinary update,
  identity/exit/readiness, automatic post-update supervision and owned cleanup.
  Preserve #2395's separate inhibition/expiry implementation OR rightful-owner
  transfer; rename/CWD or staged-child timeout evidence alone is insufficient.
- [ ] Use focused unit checks, real subprocess/native fixtures and practical
  clean-room lanes. Keep broad native matrices opt-in, not added to required
  unit-only CI. State exact reasons for unavailable lanes.
- [ ] Audit every issue, effort, test, example, PR and commit for public-safe
  scope. Do not attach private reports, raw logs or deployment identifiers.

- [ ] **IR7117-G:** Resolve EVERY IR7117-P1-P6 AND IR7117-LV1-LV4 at the
  original finite source/consumer/native tiers: standalone recovery OR actionable
  invocation, exact pending lifecycle and fresh-Manager legacy-source/profile
  safety, ALL FIVE caller-loss hooks with survival OR recoverability,
  authoritative incoming selection and independent central-only retirement.
  Preserve original custodians, delivered scope credit, existing #5529 operation
  condition and every real source/resource hold. No generic acknowledgement,
  whole-parent/every-host completion, new deployment or source-only-to-installed
  inference is added.

- [ ] **V7743-U1:** Resolve the complete P7743-U1 and local U1/U1-7743 union
  through the real latest entry at the original finite tier. Distinguish all
  ten entry/platform/selector/authority/helper/settlement/venue/adjacent-owner/
  marker/reboot boundaries, source/release credit and actual consumer results.
  Preserve original alternatives, primary failures, unknown partial effects
  and the exact still-gated owner/descendant experiment. Existing incoming
  readiness and complete selection remain the replacement bar; old recovery
  or cleanup is not an incoming veto. No retry, quarantine, native invocation,
  generic ACK, every-host or whole-parent prerequisite is authorized here.

## Proposal

Start with update reachability and explicit/manual classification, followed by
the Windows construction/legacy-state cases that still reproduce. Existing
source corrections receive ordinary-entry recovery proof before another patch
is proposed. Select each concrete unit only after current owner and source
reconciliation; the matrix is not a blanket reservation of those components.

## Journal

### Proposed finite #7117 interrupted-installer receiving

Proposed direct Phase 2 Plan, local Validation and actual ROOT-global Validation
retain the full original/refined interrupted-publication, lifecycle,
fresh-Manager and all-five caller-loss union alongside authoritative incoming
replacement. Existing U12/U14/U15 and #7814/#9474/#9490 credits retain actual
scope; no new missing-patch or all-five/installed claim follows. Every incumbent
byte/check state/first gate/owner remains. The exact #5529 operational condition
is unresolved, not waived or reconstructed; normal review, actual merge and
whole-current receiving/source/custody readback precede any separately permitted
judgment. No product/test/native, repair, queue, rollout or source/resource effect
occurred.

### 2026-11-13 - Resident-companion lifecycle intake proposed

The direct #5820 Plan, local AND actual global Validation and strict
[L5820 acceptance family](acceptance.md#l5820---whole-installer-and-resident-companion-lifecycle)
retain S1-S7 under the existing workstream. All ten new rows remain unchecked.
Delivered fail-closed MCP/Budget staging and narrower constructor/reconcile/
lifetime source credit remain; #5827's CWD credit is not the exact resident
Windows-chain witness. Original owners, holds, activation and the separate
#2395 acceptance survive. Normal reviewed merge and whole-current receiving
readback precede any separate source-tracking judgment; no product execution,
technical completion, deployment, source retirement or custody transfer is claimed.

### 2026-11-03 - Workstream established

Opened #10377 after checking existing installer/recovery issues and adjacent
efforts. The existing UV compatibility source PR #9501 is merged; #9489 still
needs its remaining evidence reconciled rather than a duplicate source fix.
Fresh-entry and first-foundation cases have existing owners and retained
qualification boundaries. This plan proposes canonical coordination and
acceptance, not completed implementation, release or installed recovery.

### 2026-11-03 - Reviewed plan merged; first recovery case under reconciliation

#10379 merged to `dev` as `589f9c41c544e671f1180621fc5c3202ed3ef674`
after current-head Copilot approval, required green CI and an empty review-thread
set. #10377 remains open. This clears the plan gate, not source implementation
or installed recovery.

The first candidate trace found that the registered/module runtime planners
derive a current no-op from the deploy-manifest version, while the executor
returns that status before invoking the installer. Missing/incomplete selected
runtime recovery therefore needs a concrete consumer regression. Reconcile this
non-serving-footprint case with #6389 and its existing serving/lifecycle owners
before taking a source slice; a related tracker is not a new ownership grant.

### 2026-11-04 - Selected-footprint recovery source unit

The bounded non-serving footprint slice is claimed on #6389 in
[comment 6625866728](https://github.com/ThomasMichon/copilot-extensions/issues/6389#issuecomment-6625866728).
Its service adapters, independent retirement work and the other contributor's
#6928 remain untouched. This is case U8 in the acceptance matrix, not a claim
on all serving convergence.

Three native regressions failed against the pre-fix source: the ordinary
selected-target update returned success and `SKIPPED (current)` for a missing
interpreter, incomplete slot or wrong selector without reaching the installer.
The planner now qualifies that shortcut through the owning payload's existing
read-only generation/completion/interpreter helpers. Missing or unsuccessful
probe support reaches the normal installer; no old-footprint permission or
additional recovery command is introduced. Source/profile identity checks,
legacy selector-free venv support and Bridge's mandatory serving reconciliation
remain intact.

The focused Windows source packets passed 257 tests with three explicit platform
skips. Four native selected-target controls also prove real shell invocation,
usable fixture-venv Python, preserved configuration bytes, unchanged healthy
slot bytes on immediate repeat and a failed repair remaining failed. These
controls use a synthetic installer to isolate the updater contract; they do
not qualify every production installer, package import, service readiness or
an installed-host update. The first combined group exceeded its existing
300-second bound; one-file groups passed under unchanged group, item, aggregate
and resource limits. Ruff F/E9, whitespace and the 13-plugin install-contract
guard passed.

Performance is scoped to explicit update planning, not session hooks or launch.
Each equal-version candidate adds one bounded read-only child per collected
plan; the target consumer replans after payload acquisition, so it checks twice
per update. Prepared-source identity checks are reused, not repeated in this
probe. Ordinary marketplace payloads require no tree scan; source-qualified
marketplace payloads retain the canonical source helper's assembly validation.
Native timing and deterministic counts are recorded with the selected-target
controls: 14 probes, median 0.680 seconds and maximum 1.031 seconds in the
final Windows fixture run. Small fixture samples are not a portfolio or
tail-latency guarantee. The inventory consumer shares this planner but has an
eight-second Manager deadline; it retains advisory version/support planning
without the new readiness probes. A focused control covers registered, module
and engine inventory paths. Only actual update plans require the footprint.

Source review, required CI, merge and containing release remain pending.
Clean-room first provisioning is unchanged; this unit exercises affected
existing footprints. Linux/macOS native and installed/external-venue operations
were not run in this Windows source-only lane. No host rollout, service,
credential, queue, protected-session or retirement effect is claimed. The
parent effort and #10377/#6389 remain open.

### 2026-11-04 - Marketplace profile currentness

Ordinary Containers installations select `+base` or `+provider-exec` generations,
not the unprofiled package version. Applying the generic source/repair generation
helper to those healthy selections unnecessarily reached the installer on every
repeat. Both healthy marketplace profiles reproduced that miss; six changed
intent, incomplete and missing-interpreter controls retained reconstruction.

The footprint probe now uses the owning Containers target selector for ordinary
marketplace profiles, binding desired profile, package/source identity and
deployed generation before the same completion/interpreter check. Prepared-source
targets retain their existing qualification. The changed profile and native
target-consumer packet passes 76 tests with three platform skips; the other
already-qualified packets are unchanged. This corrects healthy reuse without
loosening incomplete selection or source identity.

### 2026-11-04 - Selector-free legacy directory qualification

A stale `.venv` or `venv` link can survive a missing selector while pointing to
an older completed runtime. Following that link does not prove that the matching
manifest's requested generation exists. Six controls using actual Windows
junctions reproduced false current skips across the engine/module/registered
planners. The legacy fallback now uses the canonical link classifier to exclude
symlinks and junctions while retaining real-directory reuse. The affected native
consumer and serving/footprint packet passes 78 tests with three platform skips,
including unchanged old-slot evidence and real legacy-directory controls.

### 2026-11-04 - First source unit merged; next producer recovery unit

#10398 merged to `dev` as `fbcbf22b0ef518519daf67f865a2f9b551f7af89`.
Copilot approved exact head `9bbf63f6b12aeb8212cfae880f6f1a00d96b0de1`;
both review threads were resolved and required CI run 41805066341 passed.
An earlier same-head CI run also passed, but the provider required the newer
run before accepting the merge; no CI requirement was bypassed. All eleven
authored files match the verified squash merge, and the source worktree has
pulled forward. Containing release and installed application remain separate,
unperformed outcomes. This completes the bounded #6389 planner reservation,
not the parent convergence objective.

The next serial unit is the Codespaces-only normal install/update repair
adapter, claimed on #5472 in
[comment 6627075322](https://github.com/ThomasMichon/copilot-extensions/issues/5472#issuecomment-6627075322).
The actual contained Windows producer currently refuses a selected malformed
or incomplete same-version slot and preserves its bytes; two existing
consumer controls pass at that refusal contract. They do not prove recovery.
The next regression must require a distinct admitted healthy generation and
usable selected CLI without rewriting the retained old identity or resetting
state. Pair PowerShell/POSIX adapters and retain source identity, healthy-repair
reuse, failure preservation and applicable publication/service hooks.

Shared construction/retention/guardian ownership, the Worktrees first adopter,
#6928 watchdog work, #8826 diagnostics and #6389 lifecycle adapters remain
separate. No production edit for this next unit preceded #10398's merge.
The canonical effort and #10377 remain active; no shared-host installation,
service change, credential operation or rollout is authorized by this journal.

### 2026-11-04 - Codespaces native producer recovery

The U9 normal `install` and `update` regressions failed against the pre-fix
producer at construction admission for a selected incomplete same-version slot.
The Codespaces adapter now selects a fresh repair generation before entering
the unchanged lower construction guard. It reuses only an exact completed,
isolated-CLI-healthy target; occupied incomplete, malformed, conflicting or
unhealthy targets are not rewritten. Existing construction, completion,
retention, activation and failed-candidate cleanup remain canonical.

PowerShell and POSIX entries are paired. Source receipt validation keeps its
strict base identity independently of the repair suffix, and source reuse also
requires the exact deployment receipt. The manifest records the actual selected
generation while preserving package/source identity. The normal updater's
existing readiness reader already accepts that qualified repair identity; no
additional caller syntax or updater patch is needed.

The focused Windows packets pass 27 unique controls, with four native POSIX
skips. They cover normal install/update and immediate reuse, source-qualified
repair/reuse, malformed completion, failed dependency installation and incoming
CLI health, fresh first touch, bootstrap/activation failure, and existing lower
reuse/admission controls. Real disposable venvs and the generated command prove
the selected fixture CLI runs; old-slot/configuration bytes remain unchanged,
and failed incoming construction leaves selectors unchanged. Fake UV and guarded
private profiles prevent package acquisition and host-service effects.

Validation retained the existing 120-second native-child, 180-second item and
300-second group bounds. The initial combined normal/source packet exceeded
the group bound; a monolithic source-install-plus-repeat case also exceeded the
item bound. Splitting source assertions around one module-scoped disposable
installation passed in 162.15 seconds without increasing limits. The final
normal/unit/lower-reuse packet passed 17 controls and four platform skips but
exposed one stale exact diagnostic assertion. The fixture now accepts only the
canonical confirmed-abort diagnostic, including its exit-1 suffix, and retains
absence-of-candidate/selector assertions; that case and four fresh/failed-entry
controls then passed in 191.82 seconds. These are bounded functional-packet
measurements, not per-update latency or installed-host performance claims.

Actor-free generation controls are on-demand, not an expansion of required
unit-only CI. A full fresh VM/container, native POSIX execution and live external
installation were not run in this Windows source-only lane; no such deployment
is authorized here. The private synthetic fresh profile covers first-touch
construction directly, but not real package feeds, service registration,
CodeSpace connectivity, protected sessions or central retirement. README,
testing recipes and the existing immutable-generation pattern describe that
adoption boundary. Source review, required CI, merge and containing release
remain pending; #10377 and the parent effort remain active.

### 2026-11-04 - Review catches Bash 3.2 nounset compatibility

#10426 received a current-head Comment review identifying a real macOS
portability failure: the two new optional arrays could be empty under
`set -u`, which Bash 3.2 treats as an unbound expansion. Both arrays now retain
their mandatory command arguments, so they are never empty. Optional receipt
and keep arguments still preserve their individual boundaries.

Three focused controls execute the actual selection and activation functions
under nounset, with absent/present receipts and no GC keep arguments. They pass
on Git Bash 5.3.15 in 1.68 seconds (49.20 including isolated preparation and
cleanup). The same on-demand packet accepts an explicit Bash 3.2 interpreter;
native macOS/Bash 3.2 execution is unavailable in this source lane and is not
claimed. These add three controls to the prior 27 unique passes. Windows
runtime behavior, shared construction helpers and the existing four platform
skips are unchanged. Review-fix lint/whitespace pass; current-head review and
required CI must pass again before merge.

### 2026-11-04 - Invalid predecessor reference is not runtime admission

The next current-head Comment review exposed a surviving ordinary-entry
failure when the selected base slot is a regular file. The focused real
update regression failed at `ordinary runtime slot not installed` before
fresh construction, despite the adapter selecting a distinct generation.
The bounded prerequisite is coordinated on #5472 in
[comment 6629192533](https://github.com/ThomasMichon/copilot-extensions/issues/5472#issuecomment-6629192533).
It does not transfer shared guardian or Worktrees coordinator ownership.

The existing canonical retention primitive now permits an explicit
predecessor-reference pin without requiring that old entry to qualify as an
installed directory. Only construction's predecessor pins select that mode.
The same kernel ownership excludes cleanup; ordinary runtime/candidate
retention remains strict by default, and payload-hash admission is independent.
No invalid old entry is rewritten, no lock protocol is replaced, and
installer guardian/process transport and candidate admission are unchanged.
Adopting the maintained footprint plan would instead require migrating
Codespaces' legacy removal profile and broaden the cleanup contract; that
alternative was rejected for this fix.

The canonical reference packet passes 19 controls, including three new strict
default/preservation/admission controls. Normal update from the regular-file
slot, immediate reuse and the two existing failed incoming controls pass
three native cases in 129.90 seconds. Existing Bridge retention/construction
packets pass 85 controls with eight explicit platform skips, including real
competing holders, cleanup, process death and failed-candidate receipt custody.
Together with the settled Codespaces cases this is 135 unique passes and
twelve platform skips, not installed-host qualification.

Canonical copies are synchronized across thirteen plugins, the Manager and
Update Driver, with release changefiles for every affected consumer. MCP's
maintained producer digests refresh without changing contract revision 36.
The required unit manifest refreshes only three changed admitted source
fingerprints; its 4,019 nodes, coverage targets and 300-second budget remain
unchanged. That new source snapshot still requires hosted CI and a fresh
passing review before merge. No rollout or whole-parent completion is claimed.

The shared module-size guard rejected five lines of facade growth. Rather than
widen its shrink-only ceiling, the touched custody implementation moved into
the existing construction component behind the same public facade and kernel
callbacks. The full module-size sweep now passes without a baseline edit;
the canonical 19-control packet passes again, and native consumer qualification
is repeated against that extraction before publication. Those final reruns pass
85 Bridge controls with eight platform skips, and the three normal-entry/failure
controls in 162.14 seconds. No baseline, test budget or candidate-admission requirement was
relaxed to obtain those results.

### 2026-11-04 - Undecodable selectors and stale retained helpers

The next current-head review found two additional ordinary-update failures.
Both reproduced through the real PowerShell entry: invalid UTF-8 current and
last-known-good selectors blocked constructor/publication reads, and stale
retained helpers passed the CLI launch check but failed completion verification.
Canonical selector readers now handle undecodable old content without relaxing
link, type, permission or other I/O checks. PowerShell's reuse probe additionally
requires the exact retained helper bundle its completion step consumes.
The existing canonical helper verifier is read-only; old slots are not restamped.
POSIX completion does not require that bundle and retains its existing probe.

Both native regressions now pass normal update and immediate repeat, preserving
old-slot and configuration bytes. Two pure helper controls and two canonical
publication controls also pass. The final focused evidence totals 141 unique
passes and twelve platform skips, including repeated source-qualified, receipt,
helper and shell packets. Existing child/item/group bounds remain unchanged.
Canonical copies and maintained producer digests are synchronized; only three
admitted source fingerprints change in the required unit manifest, not its
4,019 nodes, coverage targets or 300-second budget.

The same Windows source-only limitations remain: no native macOS/Bash 3.2,
fresh external VM, real package feed, service registration or live installation
is claimed. Required CI and a fresh current-head verdict remain merge gates.
The earlier planner fix is released through #10430; it is not installed by
this source work. The separate artifact-publication gap remains with #10432's
existing owner. Neither containing release nor this PR authorizes rollout or
completes #10377.

The following review had no open findings but identified unexecuted native
completion-failure controls. Both Install/Update cases reproduced a missing
generation-selection fixture prerequisite before reaching completion refusal.
The focused extracted-function fixture now records selection followed by
completion, retaining the real activation function, unchanged selector and
throwing publication callbacks. All four native broker/publication controls
pass in 5.08 seconds (53.00 including isolated preparation/cleanup). This fixes
test integration, not production behavior, and is additional to the 141-control
evidence above. Current-head CI/review and reconciliation with Dev remain due.

### 2026-11-04 - Producer recovery merged; outer consumer qualification next

#10426 merged to Dev as `8d71dd2f275c3289801767440a0adef0b2abf162`
at 13:13:27 UTC. Copilot approved exact head
`258d1964c3784090c85bbb7210dabd670c56dd73` in review 5768817967;
all three review threads were resolved. Required CI 41834233630 passed,
including exact-input unit qualification and the final PR gate. The earlier
same-head CI and native Linux/Windows legacy-monitor lanes also passed.
The maintained rebase preserved current Dev's 4,044 unit nodes, coverage
targets and 300-second budget; this unit changed only three source hashes.
All 54 authored paths match the verified squash merge.

This completes the bounded Codespaces adapter and predecessor-reference
prerequisite reservations on #5472 at the source tier. It does not complete
the shared immutable-slot campaign or its guardian, watchdog, lifecycle and
other adopter work. The previous #10398 planner fix is separately released
through #10430; #10426 still needs containing-release observation. No installed
or serving generation was changed by either source result.

The next serial acceptance unit joins the normal outer collected update to
the real Codespaces producer in a disposable guarded profile. A matching
manifest must not suppress incomplete-slot repair; the resulting selected CLI
and generated command must work, an immediate rerun must retain the healthy
generation without dependency writes, and actual incoming failure must remain
failed with prior state preserved. Acquisition and unrelated portfolio members
remain explicit fixture boundaries, not evidence for feeds, services or fleet
completion. The parent Plan/Validation items and #10377 remain active.

### 2026-11-04 - Outer collected consumer recovery qualified

The new bounded Windows packet joins the actual normal collected update
orchestration and currentness subprocess to the real installer process/receipt
owner and Codespaces producer. A matching deployment manifest with an incomplete
selected slot now reaches fresh repair, publishes a usable selected Python and
generated command, and preserves the old slot/configuration. Immediate repeat
is a truthful outer `SKIPPED (current)` without dependency writes. Failed
incoming dependency preparation remains failed and retains the original
selectors, manifest, old slot and configuration without a candidate left behind.

Both cases pass in 88.12 seconds (133.91 including isolated preparation/cleanup)
under the original 120-second native-child, 180-second item and 300-second
group bounds. Initial fixture construction failed at real machine admission
before reaching the installer; the fixture now explicitly supplies synthetic
participation, alongside controlled acquisition, unrelated engine and final
host integration. The currentness, producer, process/receipt checks and final
CLI remain real. No production behavior was changed to obtain this result.

These are U8/U9 collected-consumer results, not proof of repository-shell fresh
bootstrap, governed feeds, services, every portfolio member or installed-host
recovery. The packet reuses the existing guarded Codespaces fixture rather than
creating another installer. It remains on-demand and outside required unit-only
CI. The test/documentation PR and #10426's containing release remain due; the
other candidate owners and parent Plan/Validation gates remain open.

### 2026-11-04 - Joined acceptance merged and producer release verified

#10448 merged as `7fa780be4f7792d4453e6233a8e387b146e88750`
at 13:39:04 UTC from Copilot-approved head
`277d1b9bd1c040a6899404ea63d366d94cc5375a`. Required CI 41838618639
passed, with an empty review-thread set. The continuing source worktree
pulled forward. The two joined native cases now have reviewed, versioned
consumer coverage; this is not repository-shell or installed-host acceptance.

Containing promotion #10449 merged at 13:37:26 UTC. Its actual qualified
Dev `8c64db93fc7bf165a362a17820211b352b7b9122` contains #10426's
`8d71dd2f275c3289801767440a0adef0b2abf162`; released Main is
`ec816c940921a5c7b42d3201ca2a6f4326e52aac`. The generation selector,
both source-receipt adapters and both canonical runtime/construction helpers
have matching blobs across the fix, qualified source and release. The native
entry scripts differ only by the canonical repository-to-local installer-engine
reference materialization. The released Codespaces version is `0.5.248-dev1`,
with Worktrees `1.24.456-dev1`. This is released/pending authorized rollout,
not a change to any running installation. #10448's later qualification-only
change still needs its own containing release observation.

The construction-family reconciliation also found existing delivered fixes,
not another unclaimed source patch: #9623 supplies the packaged pywin32 static
bootstrap allowance and #9878 preserves completion/settlement diagnostics.
Both commits are in qualified Dev `03548e10d689419f3e887b0ac625c1df3c5669df`
and release #10446; canonical construction blob
`57f7bbb255475750d881f2bae74d29254be1ea8d` matches its Main snapshot.
Ten strict packaged-pywin32 controls pass in 1.50 seconds (4.11 including
isolated preparation/cleanup), without executing the bootstrap hook.
The existing #7305 installed-consumer owner retains real-feed, Dispatch,
normal published update and headed-CLI preservation acceptance, informed by
[comment 6632045751](https://github.com/ThomasMichon/copilot-extensions/issues/7305#issuecomment-6632045751).
No duplicate fix, installation or acceptance transfer is inferred.

### 2026-11-04 - Windows venue fallback recovery

#10451 records a surviving default-layout defect, reproduced before production
edits: a real native Python child launches from a short control directory but
fails with `WinError 267` from the existing 270-character prepared payload
directory. The path exists; filesystem long-path support is not native process
working-directory support. The bounded slice is coordinated with #8242 in
[comment 6632477155](https://github.com/ThomasMichon/copilot-extensions/issues/8242#issuecomment-6632477155).
Its cache/inventory, transport, closure and contributor-owned changes remain
separate. Parked installation-cell coexistence/migration is not resumed.

On Windows the owning fallback layout drops redundant marketplace/installation
directories but retains the full SHA-256 of qualified source and complete
declaration, beneath the same Git-local staging lifecycle. Registration validates
the payload against that canonical home. POSIX layout is unchanged. Normal
preparation selects a fresh short home without reading or rewriting an older
malformed completion receipt, moving its payload, changing the marketplace
source, or adding a recovery command. Immediate repeat reuses the completed
short home. Revision/source changes remain distinct and old bytes survive.

Seven focused controls pass in 7.92 seconds (81.03 including isolated
preparation/cleanup), including real preparation, registration, native child
launch and repeat; two selected plugins in one marketplace; declaration revision;
and strict deep-root refusal before installation or registration. Native limit
qualification caught an initial off-by-one: 258 UTF-16 units work, whereas 259
fail because native normalization also needs a separator and NUL. The corrected
preflight covers completed reuse and a surrogate-pair path, not just character
count. It is an honest supported-path boundary, not a claim of arbitrary depth.

All 234 existing preparation tests also pass in disjoint bounded packets.
Stale tests that assumed literal `installation` directories now derive the
canonical home; the independent fixture derives catalog identity from its
actual private settings instead of ancestor names. A first combined run reached
its 300-second limit, and concurrent cached-venv preparation interrupted later
packets. Those packets were rerun only after the canonical runner restored its
own test environment; no host runtime was repaired. Item, group and resource
limits were not increased. New coverage remains on-demand, outside required
unit-only CI.

This closes a bounded authoritative-replacement/source-layout gap, not whole
installation convergence. Synthetic acquisition and private fresh homes qualify
the first-touch/older-footprint consumer contract without a network or live
Copilot. Native POSIX, a fresh external VM, actual feeds/hooks, installed runtime
selection and service readiness were not exercised in this Windows source-only
lane; no rollout is authorized. Source review, required CI, merge and containing
release remain due. The parent effort and #10377 remain active.

### 2026-11-04 - Joined acceptance packet release observed

#10452 merged at 14:16:22 UTC, publishing qualified Dev
`150efa4dcbc8b129cf0735c4735beea8e3197542` as Main
`bbdb61526541f2ba0c0e9fe0f69f671d4376237f`. That actual qualified source
contains #10448's merge, and the outer Codespaces recovery test has identical
blobs in the source merge, qualified Dev and released Main. This completes
the qualification packet's containing-release observation for U8/U9; it is
not another native run or an installed/runtime/service acceptance result.
The remaining matrix boundaries and parent Plan/Validation gates stay open.

### 2026-11-04 - Windows venue source merged; remaining ownership reconciled

#10456 merged to Dev as `affb395f47114f88671c4aecd75c17b1000b74bd`
at 15:10:45 UTC. Copilot approved exact head
`8c92f789c9c9f1fcfc77089280bbf9c206fb48b8` in review 5770571274.
Required CI 41852993195 passed all source/contract, eligible-unit, identifier
and PR gates; there were no review threads. All thirteen authored paths match
the squash merge and the continuing source worktree pulled forward.
The seven native/identity and 234 preparation controls remain the functional
evidence. All forty admitted strict type-check projects also pass for both
declared platforms; pending inventory scopes are not implied to be strict.
Containing release remains due. Nothing was installed or activated.

The bounded default-layout reservation on #10451 is complete at the source
tier, not whole-host acceptance. The next reconciliation preserves an existing
owner rather than opening a duplicate comparator task: #2404's newer-local/
older-marketplace launch requirement is retained as seven explicit clauses in
still-open #7030. Its current stage derives version drift from inequality
separately from publication policy. Delivered #7564/#7576 and the current
physical-environment consumer PR #10438 do not establish the complete monotonic
source-selection/native-apply requirement. This is read-only classification;
no downgrade was attempted and no source claim or held ownership was cleared.
The existing managed-deployment, source construction, first-entry and lifecycle
owners remain canonical while this workstream selects its next unconflicted
consumer/recovery unit. The parent stays Active.

### 2026-11-04 - Windows venue release and remaining construction boundary

The exact Dev commit for #10456 passed CI 41855027461, then promotion
41857182391 qualified `affb395f47114f88671c4aecd75c17b1000b74bd`.
Release #10463 merged as Main
`5861bb058f0905d2f0dab16f7d1e56684b8f8b6b`. The canonical preparation
and registration bodies, maintained vendored copy and native regression have
identical source/release blobs. Bridge's materialized resolver copies match
canonical source too. Released Bridge is `0.9.299-dev1`, Codespaces
`0.5.250-dev1`, Containers `0.2.198-dev1`, Worktrees `1.24.458-dev1`
and Customizing Copilot `0.2.63-dev1`. #10451 is closed at its bounded
source/ordinary-contained-consumer contract; #10377 and other acceptance
owners remain open. No runtime was installed or activated.

Current source-wheel reconciliation preserves #9290's existing owner.
The delivered #9572 correction excludes changing Job ancestry from the cache
binding while retaining it for owned children. The distinct lifecycle and
publication-envelope correction remains claimed; no competing patch is taken.
Four existing native constructor controls pass in 187.69 seconds under the
unchanged 180-second item and 300-second group bounds: fresh-workspace
per-project reuse, changed-leaf invalidation, request-witness/Job key stability
and meaningful build-environment invalidation. Actual UV uses a synthetic
backend and loopback fixture feed. This is neither varying lifecycle-ticket
qualification nor a full ordinary-update, real-feed or latency pass.
The remaining owner/handle request is
[comment 6635053007](https://github.com/ThomasMichon/copilot-extensions/issues/9290#issuecomment-6635053007).

The original immutable-UV contribution #9501 is merged and contained in that
qualified release source; its exact-head native bootstrap/UV results remain
the original contributor's evidence. Fresh-entry, existing-queue and public
Python prerequisite work stay with #7743/#7758/#9448/#10186 rather than a
second repair entry. #3004's first actual restarted-model prompt is distinct
from #1159's completed stateless write guard. Source, containing release,
contained consumers and affected-host readiness remain separate facts. The
remaining selected Plan/Validation items and original holds are unchanged.

### 2026-11-04 - Bounded automatic launch-freshness source unit

The retained #2404 scope is claimed under #7030 in
[comment 6640613933](https://github.com/ThomasMichon/copilot-extensions/issues/7030#issuecomment-6640613933),
as U11 in the acceptance matrix. ROOT coordination, first-use adapters and the
existing native application's external-cache correction remain with their
declared owners.

Read-only inspection found inconsistent decisions: reconciliation compared
against the serving version preferentially, staging treated version inequality
as apply-worthy, and pre-launch/native execution admission did not share the
source/freshness decision. The candidate now uses one read-only guard, preserves
independently selected sources and both installed/serving freshness, and compares
prepared facts again at actual apply. Existing publication, context and root
admission remain independent. Explicit update/recovery is not gated by this
automatic-action decision.

The eighteen new consumer regressions (including four real child-process
protocol controls) and all 106 retained publication-policy cases pass.
The combined file exceeded its unchanged 300-second group limit;
the same complete case inventory passes in three disjoint selections. The
staging/monotonic compatibility packet passes 75 cases after replacing only
affected shorthand test versions with real package-version spellings. The
durable launch consumer packet passes 53 cases with one explicit platform skip.
Ten actor-free decision units, Python lint and the thirteen-plugin install,
documentation and version guards also pass. Exact whole-packet unit admission,
review, merge and containing release remain pending. These isolated source
controls do not establish native installed adoption or close #7030, #7028 or
this parent effort.

The declared enforced MyPy portfolio subsequently passed all forty strict
projects for both Linux and Windows type targets, after explicit preparation
of its missing checker environments. Worktrees and repository tools remain
pending projects; this does not claim their typing admission. Tracking-write,
headless-launch, daemon and asyncio-timeout source guards pass.

The complete normal unit packet remains locally unqualified. All 4,086 selected
tests passed, but the first run exceeded the unchanged 300-second aggregate
budget. A second run without another owned validation job still failed aggregate
accounting: pytest took 287.63 seconds, with admission, source-bytecode
preparation and temporary teardown also inside the packet clock. No source,
node selection, coverage or budget was relaxed. Missing tooling was prepared
through the seasoned dependency owner; UV-owned tooling outside the checkout
preserved the source-read boundary. Required exact-head hosted CI must qualify
the packet before this source unit can merge. These local budget failures are
not represented as successful unit evidence.

PR #10508's initial head `418da023bcc85f9189e5422040b88bc3d0efa88b`
received actual hosted qualification in run `41912963474`: all 4,086 units
passed in 95.477 seconds with coverage recorded once and fresh evidence
uploaded. This is separate from the failed local Windows aggregate runs.
The required gate remained red because this slice grew `update_cli.py` to
1,001 lines. Moving the new pure-helper import to the normal module import
section restores the existing 1,000-line cap without changing it or the
decision's behavior. The module-size guard, Python lint and all eighteen new
consumer controls pass after that correction. Current-head review and complete
required CI still precede merge; the successful old-head unit job is not the
new head's gate.

### 2026-11-04 - Launch-freshness review corrections qualified locally

Eight regressions reproduced all four substantive findings from #10508 before
correction: preexisting selector/manifest disagreement, successful owned
acquisition invalidating its own planned runtime action, omitted constructor
inputs, and comparison of a repair slot with a package version. The revision
uses the existing canonical selection primitive for versioned roots, retains
source/profile and exact package consistency, fingerprints owned construction
inputs separately, and defers successful acquisitions to the next normal
planning pass. Explicit update/recovery remains unchanged.

All 149 isolated consumer controls pass: forty new cases and 109 retained
cases, run in bounded disjoint groups. The retained inventory includes three
additional namespaced execution controls. Independent namespaced planning and
execution contracts now run separately; intentionally malformed JSON avoids
an unused lookup. Item and aggregate deadlines are unchanged. Earlier combined
and per-item timeout attempts remain failures, not successful evidence.
The final staging/monotonic compatibility packet passes 75 cases; all seventeen
actor-free units pass and their three admitted source hashes match. Python
F/E9 lint, six-source module caps, thirteen-plugin install contract,
documentation consistency, tracking/headless/daemon/asyncio source guards and
whitespace checks pass. The forty admitted strict projects' earlier Linux and
Windows type-target results still apply to unchanged admitted source; pending
projects are not newly admitted.

The canonical lookup is an actual read-only caller-owned subprocess, bounded
to fifteen seconds and 64 KiB per stream. Fixture completion/interpreter
observations are synthetic: the selected fixture interpreter is never run.
This proves selection and protocol behavior, not installed runtime health,
native lifecycle activation or whole-launch latency. No host rollout, external
venue or shared installation was changed. The previous failed local aggregate
unit runs and initial-head hosted success remain separate evidence.
Current-head hosted CI and a fresh review must qualify this revision before
merge; containing release is still due. #7030, #7028 and this parent remain
open with their existing native and construction owners.

### 2026-11-04 - External construction-input review correction

Exact head `2b739eb101ceb268a5b97b4ff5b3d53ac14e6bc3` passed required
CI 41935837433, including 4,108 executed units in 94.401 seconds with coverage
recorded once and fresh evidence uploaded. Review 5777656241 confirmed all five
prior findings resolved, but identified another input-closure gap: declared
local dependencies outside the payload were not fingerprinted.

Three actual cached-action controls reproduced that gap before correction.
The construction-only fingerprint now follows transitive declared local
sources, handles cycles, includes external file inputs, and refuses missing
declared inputs. It does not import dependency code, execute another process
or grant installer admission. Generated dependency environments remain excluded,
and ordinary staging keeps its existing curated fingerprint.

All forty-three new freshness consumer cases pass in 147.48 seconds of pytest
(191.17 seconds including preparation and cleanup). The staging/monotonic
compatibility packet passes 75 cases in 13.85 seconds (21.81 seconds total).
Python lint, unchanged module ceilings, documentation consistency and all four
source AST guards pass. The earlier 109 retained cases and seventeen pure units
are separate retained evidence; their admitted production sources are unchanged
by this correction. No test limit or required gate was weakened.
The revised head still needs its own hosted CI and passing review before merge;
the previous head's green result is not substituted. Parent, native acceptance,
release and rollout boundaries remain unchanged.

### 2026-11-05 - Python 3.10 construction-observer compatibility

Head `0d1df88228f1c67dc59aa2f02db4a0a7417db280` passed required CI
41938167466, but review 5777812790 correctly identified that importing stdlib
`tomllib` unconditionally broke the declared Python 3.10 floor. Three
missing-stdlib consumer regressions failed before correction. The observer
now falls back to `tomli`, declared as a conditional runtime dependency below
Python 3.11. The dev extra also supplies the real backport on newer test
interpreters; no supported Python floor or fallback failure is hidden.

All six direct/transitive/missing-input controls pass under actual CPython
3.10.22 in 23.70 seconds (84.88 seconds including canonical isolated preparation
and cleanup). Installed runtime metadata selects the backport on 3.10 and its
real import succeeds. After restoring the ordinary CPython 3.12 test environment,
all forty-six freshness consumer controls pass in 121.07 seconds (158.72 total),
including real backport parsing with stdlib TOML explicitly unavailable.
This is contained test-runtime qualification, not an installed-host update.

Lint, module ceilings, thirteen-plugin install contract and whitespace checks
pass. The Python-only dependency-age audit covers 541 declarations with zero
violations at the unchanged seven-day minimum. The local all-ecosystem audit
remains failed: four public npm metadata TLS handshakes failed with certificate
validation enabled. The attempted plugin-directory audit found zero declarations
and is not evidence; the repository-root Python audit is the counted result.
TLS and age policy remain unchanged. The new head must pass its actual complete
hosted declaration audit, required CI and fresh review before merge. Earlier
green heads do not clear those gates; parent and rollout boundaries remain open.

### 2026-11-05 - Launch-freshness source merged; containing release next

#10508 merged at 00:31:13 UTC as
`7ebeee4b2b46fcea8eb06b0df418651ca1f416b0`. Copilot review 5777948367
approved exact head `7f3f8e2920d9f0c754e3a94e84f9ac0dad48b1e2` with no
open findings; all seven threads are resolved. Required CI 41940407320 actually
executed 4,108 units in 54.401 seconds with coverage recorded once and fresh
evidence uploaded, plus the complete 545-declaration age audit with zero
violations. A description edit queued a later same-head run, so the first
managed merge was refused until 41940484664 also passed its required gate.
The configured review-rule exception did not bypass CI.

Thirteen authored paths, including every production/test body, have identical
reviewed-head and squash-merge blobs. Peer base changes are retained in the two
shared documentation files and unit manifest; this slice's complete documentation
sections, seventeen admitted nodes, changed source hashes and coverage-target
addition are preserved. Initial managed pull-forward auto-aborted a replay
conflict, leaving the approved tip and recovery ref intact. After proving the
clean tip and merged content, continuation skipped only those confirmed merged
commits; managed sync succeeded on the squash merge.

This completes the bounded launch source/freshness reservation on #7030 at the
source tier. It does not complete ROOT coordination, the native application/
cache consumer, source construction, ordinary whole-update or affected-host
acceptance. The canonical README has been reread in full and remains Active.
Containing release is the immediate next gate; existing entry, execution,
construction and lifecycle owners retain the other matrix boundaries. No
installed runtime, service, credential, queue or protected session was changed,
and no rollout is implied by this merge.

### 2026-11-05 - Launch-freshness containing release verified

#10542 published the canonical source milestone and U11 acceptance row to Dev
as `91091ecacc322ce3ccb1e244cbfd8541494e4926`, after exact-head approval
and green required CI. The canonical README was reread and remains Active.
Post-merge Dev CI 41941930339 also passed the source fix's actual merge.

Validation/promotion 41943616923 succeeded. Release #10548 merged at
01:02:40 UTC, publishing qualified Dev
`20c140f142b273ec132b8d740240ed0dd3135e02` as Main
`0adc4e2e8284bdbd72b8a99cbb4172841aac2088`, Worktrees `1.24.468-dev1`.
That actual qualified source contains #10508's merge. All six changed production
bodies match the fix, qualified source and released Main; the materialized
runtime dependency set retains the Python 3.10 backport. The earlier release
#10540 qualifies an older snapshot and is not substituted for this proof.
No test rerun or installed/runtime/service result is inferred from matching
released bytes.

This completes U11's source and containing-release observation, not #7030's
native adoption or this parent's remaining Plan/Validation gates. No updater
was invoked. Existing fresh-entry, ordinary interrupted-request/coalescing,
first-foundation and native application/cache owners retain their scope.
Read-only reconciliation found #10499's related report of five legacy Windows
startup reconciles losing their worker with an SSH session; it is a candidate,
not a claim or source change. Current source and overlapping ownership must
be reconciled before selecting any migration, preserving the Python-free
budget hook, source/action policy and existing execution owner.

### 2026-11-05 - Bounded MCP first-use prerequisite proposed

Broader first-use/bootstrap and matching-source reconciliation retains #9026's
Dispatch recipe, #9448's first-foundation work, #7758's ordinary admission/
recovery/coalescing owner and #4195's broader hook-spawn scope. The narrow
[public scope note](https://github.com/ThomasMichon/copilot-extensions/issues/10499#issuecomment-6647379317)
selects MCP's fixed `stamp`/`provision` prerequisite, not the whole five-plugin
migration or live queue repair.

The [MCP proposal](../source-managed-runtime-installs/mcp-first-use.md) extends
the existing finite recipe roster through its canonical foundation and queue.
Its actual initializer is `init`; borrowing another plugin's installer front
or service flags would not preserve that contract. Review and merge the plan
before source implementation, then qualify actual materialized native
Windows/Linux construction, installed consumers and original-caller loss.
Ordinary bootstrap adoption follows separately. No production code, runtime,
queue, service or host state is changed by this proposal, and the parent
remains Active.

### 2026-11-05 - MCP prerequisite review and first source controls

#10558 merged as `ac9ac7c03ef2e740871696755438434cd1ee9cd8` after
current-head approval and required green CI. The canonical README was reread;
this is a reviewed plan, not completed recipe or bootstrap adoption.

Four regressions failed against the unchanged source at unsupported MCP recipe
admission. The bounded adapter adds its actual init-backed actions to the
existing Python/native front doors and retained queue. Those four controls now
pass in 93.35 seconds under an explicit 60-second native item bound. The first
default-30-second receipt attempt timed out; its isolated stamp subsequently
passed in 29.94 seconds. The combined refusal packet also exceeded its
unchanged 300-second group limit and is being qualified in disjoint groups,
not reported as passing.

Native qualification also reconciles the plan's overly broad Python-free
wording with the existing setup-owned prerequisite amendment. Windows lifetime
admission requires supported `python` on PATH before a plugin runtime exists.
Preserve and test that current boundary; do not add another acquisition path
or claim a fully Python-free machine. The Python-free Budget hook remains
outside this MCP slice. Materialized native qualification, failed-candidate
preservation, source review, release and affected-host recovery remain open.

The latest declaration/protocol packet passes eight controls, including the
four lifecycle/CMD admission cases. Actual materialized missing-Python refusal
and corrected CMD stamp both pass. The first cold native attempt failed while
the fixture read queue status before observing a durable ordinary receipt;
foundation readiness alone was not a request-acceptance signal. The observer
now waits for that existing public receipt before querying its request and
still propagates schema/terminal failures afterward. This is a fixture
correction, not queue recovery or a relaxation of schema admission. Cold native
qualification remains pending under the same budgets.

The corrected cold observer's next run failed honestly at the unchanged native
foundation builder's five-minute owned-process deadline. Source wheel assembly
completed, but foundation construction did not finish and no ordinary MCP
receipt was accepted. This is neither MCP qualification nor evidence that a
larger test deadline would repair production construction. Preserve the actual
failure and existing foundation owner; run the explicit Windows/Linux recipe
portfolio against the candidate before merge. No constructor, queue, recovery
policy or process deadline was changed to obtain a passing result.

### 2026-11-05 - Hosted MCP cold path exposes missing UV handoff

Draft #10574 published the actual candidate, not an accepted native result.
Explicit run `41959758429` at `53b83d549022286da0abca0a26232ae808256af5`
passed all admission/protocol/refusal and materialized stamp/prerequisite
steps on Windows and Ubuntu. Both cold cases completed foundation and stamp,
then failed during provision because the legacy MCP initializer lacked UV
while its dependency-policy helper still required it. This is a real
acquisition handoff gap, separate from the earlier local foundation timeout.

Two focused regressions fail before the correction; the deferred-stamp
negative control already passes. The recipe now invokes the frozen payload's
existing canonical UV acquisition helper only for provision, supplies the
selected tool on PATH and stops before init on acquisition failure. The actual
MCP initializer, constructor, foundation, queue and deadlines are unchanged.
All three queue/native-process controls pass in 43.39 seconds of pytest
(74.31 seconds including preparation and cleanup). Eleven retained fixed
first-use controls also pass in 66.24 seconds (108.98 total) on the first
published head.

The revised candidate still needs actual Windows/Linux cold construction,
installed consumers and failed-candidate preservation, plus current-head CI
and review. The draft stays held. Parent and ordinary publisher adoption,
original-caller Job loss and affected-host recovery gates remain open.

Required CI `41961691461` also found that the new native portfolio's dynamic
runner matrix violated the existing trusted-CI contract. The workflow now uses
separate fixed Ubuntu/Windows jobs with shared steps and environment, following
the repository's existing pattern. The canonical trusted-CI checker passes;
both jobs retain identical commands, environment and 35-minute job budgets.
The guard and required unit-only selection are unchanged. This workflow
correction does not turn the still-pending native run into a pass.

Run `41961771318` at the UV correction completed as a failure on both
platforms. Linux progressed through actual provisioning, installed consumers
and healthy repeat, then its negative control expected an exception string
that MCP's health gate intentionally suppresses. The actual initializer
reported `Fresh runtime slot failed its health gate` and refused activation.
The fixture now requires that public health-gate diagnostic while retaining
the real incoming module failure and all old-selection/configuration checks.
Windows exceeded the unchanged 900-second aggregate envelope without a useful
stage report; no passing outcome or specific hung operation is inferred.

The test now emits a finite stage marker at each native transition, without
another polling process or changed deadline. Run `41963933663` qualifies
candidate `92551780178074204922f0e615cfd8214cc23ee3` through the corrected
fixed-runner jobs. Its outcome is pending; the source PR stays draft.

The fixed-job run completed with Linux passing its full recipe, cold
construction, installed-consumer, healthy-repeat, failed-candidate preservation
and native settlement portfolio. Windows completed cold stamp, then timed out
inside concurrent provision before any installed-consumer assertion. Required
CI `41963918779` at the same candidate passed source guards, eligible units
with coverage and the required PR gate. Neither result substitutes for the
remaining Windows native qualification.

The next diagnostic revision reports at most three 4 KiB active log tails per
minute during the bounded provision wait. The wait now refuses at ten minutes,
within the unchanged aggregate envelope; no timeout, process or storage limit
is raised. This is test-only progress reporting, not a production poller or
another execution owner.

The bounded diagnostic run `41966134747` again passed Linux and failed Windows.
Its Windows log shows that actual MCP construction, manifest publication and
module verification finished, but the enclosing native consumer rejected
CLIXML mixed into the forwarded error stream. A focused queue regression with
real native host/warning records reproduces that output-format defect.

A contained stock-PowerShell probe confirms that `-EncodedCommand` serializes
those streams even with explicit text input/output flags; the ordinary fixed
`-Command` form does not. The adapter now uses that fixed form while retaining
base64-encoded JSON path data, actual init, canonical acquisition and the same
queue custody. All three acquisition/stamp/refusal controls pass in 33.13
seconds of pytest (52.97 total). The unsuccessful text-flag attempt remains
failed evidence. Actual Windows native convergence still needs qualification;
neither completed installer output nor the passing focused test clears it.

### 2026-11-05 - Native functional pass and participant-handoff review gap

Run `41969056781` at `c4c7de194e4ed158516bccc451e2e1c46d4401ea`
passes the complete native recipe portfolio on both Windows and Linux.
Required CI `41969027369` and the later same-head `41969073865` also pass.
The draft hold was removed for review; no merge or release was inferred.

Review `5780027744` correctly identifies a high-severity production handoff
gap. The contained installer path bypasses native-driver admission, so its
functional pass did not prove that the real initializer would join the outer
request instead of submitting another request against its held lock.
Two actual-native-admission regressions fail before the correction, and the
explicit-root case independently reproduces the receiver's default-root
assumption after supplying the missing witness.

Windows MCP provision now explicitly re-exports the existing authenticated
participant witness and native re-entry marker. The shared native receiver
addresses the execution root carried in the bounded witness, authenticates
that original IPC owner, and retains exact running-request/backend checks.
No new root override, queue, protocol, launcher, installer or containment
exception is introduced. Invalid witness metadata remains a refusal.
Canonical synchronization updates only the declared helper copies.

Both materialized native-admission cases pass in 89.33 seconds of pytest
(115.19 total), with runner containment unchanged and exactly the original
queue request retained. Eighteen focused shared-receiver controls pass in
5.40 seconds (96.58 total), including malformed/oversized/missing-root/
foreign-root refusal and retained participant contracts. Current revised
native portfolio, required CI and a fresh review must still qualify this
correction; the earlier head's passing results do not close this finding.

The next native run `41972283777` passes Linux, including the new shared
receiver controls. Windows stops in the synthetic protocol fixture: it still
rejects every witness with exit 91, including the newly required legitimate
native handoff. The MCP-only fixture now requires witness plus re-entry marker
for Windows provision, keeps stamp witness-free and still rejects unrelated
lifecycle custody. Actual authenticated admission remains separately exercised
by the materialized native tests; no production check is removed for this
fixture correction.

The intervening review confirms the native custody finding resolved and
correctly requests that preservation cover MCP's real configuration directory.
The cold fixture now seeds a synthetic stdio configuration under the
installation's `bridges` directory instead of an unrelated home-directory
sentinel. Installed consumer checks load it through the real configuration
loader, require the actual configuration root and unchanged declared command,
and retain byte comparisons after healthy reuse and incoming failure. The
declared server is never started and no authentication provider is consulted.
The corrected Windows synthetic protocol packet passes five focused controls
in 91.10 seconds of pytest (117.45 total). Revised configuration preservation
still needs actual native qualification before completion is credited.

### 2026-11-05 - MCP fixed-recipe source merged; containing release next

#10574 merged at 05:39:20 UTC as
`322a4e5adebcd066a920ca52c5e808d41fe1d617`. Exact head
`5834222f8a6cab0b286d96bcd8582921169104a1` received clean Copilot
review `5780289807`, with no open findings and both prior threads resolved.
Required CI `41973744346` passed; the later same-head metadata-triggered
`41973863391` also passed before the managed merge. The configured
Maintainer review-rule exception did not bypass CI.

Native run `41973788482` passes every selected Windows/Linux step, including
the actual native participant handoff, shared receiver controls, real cold
construction, installed command/module/distribution and configuration loading,
healthy reuse, incoming-health failure preservation and native settlement.
The installed loader reads the unchanged synthetic configuration from the
actual `bridges` directory without starting a server or authentication provider.
Earlier failed runs remain failed evidence; their eventual corrections do not
retroactively qualify those runs.

All twenty-five non-shared authored files and the complete MCP testing section
match the actual squash merge; unrelated peer additions in `TESTING.md` are
retained. Managed pull-forward safely aborted a replay conflict. After verifying
the clean tip, backup and already-merged content, continuation skipped only
that confirmed merged range and managed sync succeeded on the squash merge.
No unmerged work was discarded.

The canonical README has been reread in full and remains Active. U12 records
this bounded source/contained-consumer result, not ordinary publisher adoption,
production caller-Job survival, existing queue repair or affected-host recovery.
Containing release is the immediate next gate; existing first-foundation,
execution/coalescing, MCP construction/application and broader hook owners
retain their remaining scope. No updater, host integration or rollout ran.

### 2026-11-05 - MCP containing release verified

Validation/promotion `41977870141` succeeded. Release #10626 merged at
06:18:46 UTC, publishing actual qualified Dev
`f21518183df46b54a1328ebdd2626c96aa62d28f` as Main
`19649c0bc538638cb5e58f8f16c96038b06acb8a`. That qualified source contains
#10574's merge. All four changed canonical implementation bodies match the
source merge, qualified Dev and released Main. MCP's packaged recipe and native
receiver match canonical source; its plugin/package versions agree at
`0.2.181-dev1`, with Worktrees `1.24.473-dev1`.

The preceding successful release #10617 qualified an older Dev snapshot and
does not contain this fix; it is not substituted. The normal scheduling filter
waited for the then-current Dev source CI before dispatching the newer gate.
No release-control change, force retry, capacity provision or updater was used.
The source journal/U12 milestone separately merged through #10613 after
current-head approval and required green CI.

This completes U12's bounded source and containing-release observation, not
ordinary publisher adoption, production caller-Job survival or whole-update/
affected-host acceptance. The parent remains Active. The next stretch
reconciles current MCP bootstrap ownership and the existing production-lifetime
qualification lane before selecting any ordinary-publisher adoption; no new
source claim or live recovery operation is established by this release record.

### 2026-11-05 - MCP ordinary bootstrap slice selected for review

Current #10499 and the matching open MCP/bootstrap/lifetime PRs were inspected
before selecting U14. No MCP bootstrap adoption PR was present. The bounded
claim is [comment 6652903046](https://github.com/ThomasMichon/copilot-extensions/issues/10499#issuecomment-6652903046);
existing Dispatch, first-foundation, ordinary recovery/coalescing, whole-hook
and MCP construction/application/publication/lifetime owners retain their scope.

The ROOT's [MCP bootstrap proposal](../source-managed-runtime-installs/mcp-bootstrap.md)
routes only legacy first stamp and drift provision through the released recipe
and original queue. It preserves MCP's actual init and selected-source readiness,
Windows' setup-provided Python prerequisite, automatic controls and independent
guidance. Review and actual proposal merge precede implementation.

The direct validation requires real hook deadline/output, installed configuration
and consumers, and genuine production submitting-Job/group loss through the
existing native lifetime owner with all outer cleanup boundaries retained.
U12's contained caller-process-loss evidence is not substituted. No production
hook was run, no source implementation or native acceptance is claimed, and no
shared installation, queue, credential, service or rollout effect is authorized.
The parent remains Active; the next gate is review of this bounded proposal.

The proposal subsequently merged through #10658 at 08:25:00 UTC as
`ff1ae2a9fb9a818dbd905af7e77c2ceff13f9017`. Its final head
`c43e52a4875ada22ff16b99594f74aacff5e663c` received genuine approval
`5781807321`, with no review threads and both required source CI runs green.
The preserving rebase retains the separate U13/#10652 fresh-settlement plan;
this bootstrap slice is U14. All six plan files match the merge.

The parent Plan and Validation Plan remain unresolved. The next source step
implements only the reviewed MCP bootstrap adapter, preserving the original
queue, real readiness and all incumbent custodians, before exact-candidate
hook and production-lifetime qualification. A plan merge is neither a source
fix nor native/affected-host acceptance; no updater or live repair ran.

### 2026-11-05 - MCP bootstrap functional result and native lifetime candidate

The local adapter implements the reviewed U14 proposal: fixed stamp/provision
handoff, real source-qualified readiness, preserved controls and guidance,
unchanged deadline/output and removal of this hook's direct installer/PID-age
custody. The actual Windows functional packet passes in 686.14 seconds. All
twelve original lifecycle records settle and final inventory is empty; restoring
the fixture's original source identity before canonical teardown fixes the
earlier test-only missing-runtime-root hold. That result is contained functional
and cleanup evidence, not production caller-Job loss.

The candidate now includes an explicit ephemeral hosted Windows/Linux lifetime
packet through the existing native owner. It holds the ordinary execution lock,
invokes the real hook, pins acknowledged owners, destroys every fixture caller
boundary while work is pending and requires completion without resubmission.
The existing restrictive shared-outer negative and qualified Windows desktop
witness remain separate controls. An explicit renewed lifecycle, bounded-child
controls, ordinary/coalesced work and exact cleanup remain required.

Seven local admission/filter/CLI controls pass; the bounded shared cold-environment
packet passes three checks and correctly skips the hosted-only native item.
Focused Ruff and the install-contract gate pass. No actual hosted result is
claimed yet. The new lane retains the existing item/step/portfolio bounds and
uploads only receipts and logs. Exact-candidate Windows/Linux native results,
implementation review/CI, merge and containing release remain open. The parent
Plan and Validation Plan remain Active; no updater or shared installation ran.
The live declaration-age audit checked 548 declarations but could not verify
four npm metadata reads because their TLS handshakes failed with certificate
validation enabled. It is not recorded as passing; no trust/verification
setting was weakened. Hosted CI must resolve that validation boundary.

The first hosted candidate, #10700 at `12b71e11466e`, fails run
`42016085082` before either production-lifetime step executes. Linux reports
five PowerShell repaired-readiness failures (37 passed, seven skipped); its
assertion truncates the subprocess diagnostic. Windows completes the actual
functional body in 208.10 seconds but fails strict teardown on one
`process-reused` lifecycle hold. The earlier twelve-settled local packet is
not substituted for this exact candidate's failed result.

The next diagnostic revision retains the Windows functional JUnit properties,
remaining original records and read-only native birth observations, and prints
the complete Linux subprocess error. It changes neither production cleanup
policy nor the all-settled assertion and does not skip the failing cases.
Both roots require diagnosis before another native acceptance claim.

The diagnostic run `42018348269` at `b7d9d68d25b7` identifies the Linux
failure: PowerShell resolves both `/usr/bin/bash` and `/bin/bash`, then tries
to invoke their combined `Source` array as one executable. The adapter now
selects the first application, retaining the existing native POSIX path.
Windows passes the functional packet and reaches native qualification, where
the generic control reports three scope identities: the runner's inherited
Job plus both fixture Jobs, not a missing caller ancestor. Scope validation
now requires the exact inherited prefix, the returned outer Job name and one
additional inner Job. All tracked scopes remain checked for daemon escape;
both fixture Jobs, bounded-control death, pinned-daemon survival and the
restrictive shared-outer negative remain required.

Nine local lane/scope guards pass, including inherited-scope preservation and
missing/reordered-boundary refusals. The Windows hook contract packet passes
42 cases with seven platform skips in 85.77 seconds; focused Ruff and whitespace
checks pass. These are source corrections, not native lifetime qualification.
The first run's original `process-reused` hold remains unmodified and is not
retroactively settled by the second run's functional pass. Exact revised-head
Windows/Linux proof, review/required CI, merge and release remain open.

Run `42020741107` at `b947f06d92f7` clears all functional packets on both
platforms, including Linux's previously failing readiness cases. Native proof
still fails before the actual MCP hook. The exact-scope check exposes an
incorrect attribution in the preceding diagnostic: the third Windows scope
comes from the leased intermediate's own expiry Job, not an established
runner Job. The fixture had launched a bounded intermediate through
`spawn_daemon(breakaway=False)`. It now uses ordinary headless `Popen`, retaining
the outer Job and unchanged inner caller-owned Job without introducing another
daemon lifetime. A regression rejects using daemon admission for that bounded
intermediate; exact inherited/outer/inner validation remains.

Linux's native entry creates `libs/update-driver/.venv` before materialization,
which refuses symlinks in canonical input. Its assertion did not print the full
materializer result. The hosted step now declares its UV environment under the
runner's temporary root instead of the canonical library, and the fixture prints
every materialization finding on refusal. This does not change materialization
policy or treat a skipped source as successful.

The same head's required CI fails the daemon census: the removed MCP
`Start-Process`/`nohup` sites remained listed and the new bounded MCP control was
unclassified. The census now removes those retired sites and classifies both
bounded fixture launches as caller-contained. Ten focused lane controls pass,
as does the actual materialized stamp consumer (55.26 seconds). Native
qualification and required CI must pass on the revised head; no failed result
is waived or credited as acceptance.

Run `42022964161` at `93066796f1b4` passes both functional packets and gets
past the preceding materializer and two-caller-Job failures. Linux reaches the
real hook and acknowledges its owner, then the fixture incorrectly requires
the log pathname in `/proc/<pid>/cmdline`; native launch redirects stdout to
that log instead. The identity check now compares the acknowledged live
pidfd-pinned owner's actual stdout descriptor with the unique acknowledged log,
with liveness checks before and after the descriptor observation.

Windows retains exactly both caller Jobs and an admitted daemon with a
different expiry Job. Its control incorrectly requires no Job metadata at all,
rejecting the daemon's own renewed-lease custody. It now rejects any retained
caller identity and checks the daemon's native nonmembership in every pinned
caller Job. It does not confuse an independent expiry Job with caller
dependence. Both fixture Job closures, bounded-control death, pinned-daemon
survival and restrictive-outer refusal remain mandatory. Neither failed run
is native acceptance; the revised exact-head lane still owes actual MCP work
completion and exact cleanup on both platforms.

Run `42024316058` at `b15bf45f37d9` fails Windows' functional coalescing
assertion, with all nine lifecycle records settled, and Linux's native item
deadline while waiting for its first ordinary receipt. Windows' two sequential
hook calls did not guarantee pending overlap. An attempted execution-lock
barrier also blocks the native foundation producer, so that local validation
times out and is not acceptance. It is removed rather than extending a deadline.
The functional and native assertions now require identical identities for
equivalent overlapping requests; distinct identities require durable proof
that the earlier request finished before the next was accepted. Actual
coalescing is recorded, not assumed for sequential non-overlap.

The Linux fixture pre-created its ordinary queue before cold foundation
admission. Both native producers require an existing queue to have its existing
foundation, so that setup entered the separately owned existing-queue recovery
boundary instead of genuine first touch. The unrelated ordinary request now
joins only the later provision cycle after the real cold hook establishes the
foundation. The actual caller-boundary destruction and post-loss completion
requirements are unchanged.

Failed/interrupted foundation state and an acknowledged owner exiting without
an ordinary receipt now fail promptly. Bounded owner-log tails are retained
before waits and on teardown; Linux uses the existing signal timeout so finally
and exact cleanup diagnostics can run. Item/step/portfolio deadlines are not
increased. Neither failed validation is credited as a source or native pass.

The corrected local Windows actual functional packet passes in 631.51 seconds
(660.66 seconds including preparation and cleanup). Hook times are
8.328/6.609/5.594/6.813 seconds against the unchanged 15-second deadline.
Installed consumers, healthy repeats, incoming-failure preservation,
source-independent consumers, all original lifecycle settlements and empty
final inventory pass. This remains contained functional evidence, not native
caller-Job/group proof. Ten lane guards, focused Ruff and whitespace pass.

A bounded npm-only metadata retry still fails all four public TLS handshakes
with certificate validation enabled. Normal PR CI skipped declaration age
because this diff changes no dependency declaration, so its green result is
not substituted for that audit. The existing CI workflow's explicit manual
dispatch audits all declarations and can qualify the hosted metadata lane
without changing a manifest or trust setting. Exact-head native results,
required CI/review, merge and release remain open.

Exact-head full manual CI `42029890331` at `c3a3024c1c64` passes, including
all 548 live dependency declarations with zero violations, source guards and
eligible units. That hosted result does not claim the local npm TLS problem
was repaired. Native run `42029879542` still fails both platforms; its functional
packets pass, while native lifetime acceptance remains open.

Windows' generic controls qualify two normal cycles and the restrictive outer
negative, with both caller Jobs closed, bounded controls retired and independent
daemons surviving. The desktop witness observes zero new windows/foreground
changes. The actual MCP owner then fails nested native admission. Source tracing
finds that disposable PowerShell shell custody always creates a restrictive
expiry Job, unlike the Python guard. The correction retains kill-on-close and
normal contained-mode prohibition, permitting breakaway only in the existing
explicitly non-contained mode. No caller Job limit, native nonmembership check,
external restriction or fallback policy is changed. Thirty-eight focused
custody/protocol tests pass, and a real contained Windows original-shell-loss
case still retires the exact helper and bounded child.

The canonical lifecycle archive is regenerated through its owner tool for all
consumers; an additional changefile names the four archive consumers not covered
by the existing eleven-consumer changefile. The MCP producer digest is refreshed
without a footprint contract-revision change.

Linux's retained diagnostics now show both cold callers coalesced to one
ordinary pending request, followed by worker-launch refusal. The restricted
native PATH omits the real `systemd-run` required under the host's cleanup unit,
and replaces native runtime inputs with the cold profile's directory. The
explicit hosted fixture now preserves that prerequisite and configured runtime/
bus inputs, without exposing product Python or installing a user manager.
An unavailable real scope remains a failure. An exited acknowledged owner with
an accepted-but-incomplete request also fails promptly rather than consuming
the whole item deadline. The prior Linux signal-timeout cleanup settles both
original lifecycle records; Windows retains four settled records plus one
`launch-outcome-unknown` hold. Neither historical hold is modified or waived.

Run `42034236248` at `a9b14b243917` qualifies the full Linux portfolio. Actual
native stamp/provision completes in 98.97 seconds after all caller groups are
destroyed; both cold callers coalesce, bounded controls die, acknowledged
owners survive and all six original lifecycle records settle with empty final
inventory. Hook times are 1.104/1.134/1.115 seconds. This is native Linux
caller-group proof, not another contained substitute.

Windows' native stamp also completes after both caller Jobs close, with two
coalesced callers and acknowledged-owner survival. The generic restrictive
negative and desktop witness pass; no visible windows or foreground changes
are observed. Provision then fails after runtime/command publication because
the fixture's credential-free OS map omitted `COMPUTERNAME`, which the existing
deployment metadata writer requires. All eleven original lifecycle records
settle. The map now preserves that required native metadata; the production
installer is not changed to hide a missing prerequisite. Full exact-head
Windows qualification, current CI/review, merge and release remain open.

Run `42036393829` at `1766f0fd6dae` passes both complete native portfolios.
Current source CI `42036372710` separately refuses the changed SDK module's
stale unit-source admission and missing transitive changefiles for Fleet Manager
and Chat. Neither failure is waived or replaced with the older full-CI pass.
The SDK change is reviewed against the packet's existing import/fixture closure:
it adds only the existing `contained_test_mode` read and a Job flag conditional,
with no new module, test node, coverage target, effect permission or budget.
Only that reviewed source's declared digest is refreshed, and the unchanged
unit packet still owes actual bounded execution and effect/coverage verification.
The two transitive consumers receive their own ordinary patch changefile.
These are admission/version metadata corrections; native consumer code and
qualification inputs remain unchanged from the successful native head.

The first current-head review at `22fea15261` is `COMMENTED` with one blocking
Medium finding: Windows batching inherited the Python SDK's `HOME` default
instead of the PowerShell SDK's `USERPROFILE` roots. The receiver already
validates the MCP runtime as a direct child of that profile. The batched worker
now derives the profile from that validated runtime root, explicitly supplies
its Copilot home to both SDK reads and its durable home to deployment resolution,
without changing global SDK defaults or adding a protocol field.

Both batch outcomes and the real native admission controls exercise distinct
`HOME`/`USERPROFILE`; all four pass in 11.02 seconds with the declared existing
Python prerequisite on PATH. The first local invocation lacked that PATH prefix
and stopped at the prerequisite instead; no installer or test assertion changed
to hide it. The hosted Windows caller-loss packet now uses the same distinct
home case, so new exact-candidate native qualification is still required.

Current guard CI also identifies the separate canonical installation-context
caller archive as stale. Its owner regenerates that archive and all thirteen
SDK copies; the MCP producer digest is refreshed with contract revision 36
unchanged. This completes the already reviewed SDK source propagation rather
than changing the lifetime policy again. Archive sync, source admission,
install-contract and lint checks remain required before the new push.

Head `7793205431` passes all current source CI, including the canonical SDK
vendoring check. Its fresh review resolves the launch-time Medium finding
but identifies a related High gap in ordinary `_inspect_payload`: the SDK
defaults could still select `HOME` after the native launch. The distinct-home
Windows native packet reproduces that provenance refusal before ordinary
acceptance; Linux passes.

Ordinary inspection now explicitly uses the same native `Path.home()` profile
already used by its canonical-root checks, supplies its Copilot root to both
SDK reads and its durable root to deployment resolution. Execution-time
revalidation uses this same function, so the profile remains coherent before
enqueue and before effects. The environment and generic SDK defaults are not
rewritten. Existing stamp/provision recipe controls now retain distinct homes
through both ordinary acceptance and execution. All six batching, native
admission and real recipe controls pass in 38.17 seconds; Ruff, source-admission
and whitespace checks pass. The full distinct-home native lane remains required
on the new source head; the earlier successful packet is supporting history.

### 2026-11-05 - U14 source merged after exact-head native qualification

#10700 merges to `dev` at 15:17:16 UTC as
`b1e7f13079e3cd6968e6967ba878a684064b35c1`, from final runtime head
`ed8cfa89a5e88fd2a754362294e2278970cf2a2a`. Its fresh current-head Copilot
`COMMENTED` verdict lists no open issues and resolves both profile findings.
The review's remaining qualification caution predates the completed native run;
[the final proof comment](https://github.com/ThomasMichon/copilot-extensions/pull/10700#issuecomment-6661652661)
records those results before managed role-scoped self-merge. All required
source CI and both reviewed threads pass their independent gates; no CI bypass
is used.

Exact-head native run `42051032188` passes both full Windows/Linux portfolios.
Actual caller-loss packets pass in 94.955/87.222 seconds, including the complete
Windows `HOME != USERPROFILE` path through ordinary admission and execution.
Both stamp/provision cycles complete after caller-boundary destruction,
coalesced cold callers share the original work, bounded controls die and the
same acknowledged owners survive. All eleven Windows and six Linux original
records settle with empty inventories. Windows records zero new visible windows
or foreground changes over 8,123 samples. The restrictive shared-outer negative
remains passing.

This closes U14's source, review and contained/native consumer slice, not its
containing-release observation or the parent's remaining Plan/Validation items.
The next action watches the normal Dev CI/promotion pipeline for a containing
Main release and verifies shipped implementation bytes before selecting another
bounded source unit. Commands, explicit-installer adoption, existing fenced-
queue recovery, macOS and affected-host rollout remain with their original
objectives. No installed updater, service repair, credential operation or rollout
ran, and no earlier failed/held record is retroactively settled.

### 2026-11-05 - U14 containing release verified

Normal promotion `42054939302` succeeds with admitted Dev
`b1e7f13079e3cd6968e6967ba878a684064b35c1`. Its generated candidate
#10732 merges at 15:44:48 UTC as Main
`be5f4c9991e3726f90eba0c1c035eb7403ee5595`, publishing MCP `0.2.185-dev1`
and Worktrees `1.24.482-dev1`. The preceding #10730 release instead qualifies
older Dev `556f5d8da862bc2257d9dd187bfa514faf9f8576` and is not credited.

Raw Git-object comparison confirms all 35 changed production files match
the qualified source merge. The four packaged MCP receiver bodies
(`first-use.ps1`, `first-use.sh`, `mcp-admission.py` and `first_use.py`)
also match their qualified canonical bodies.

The direct ROOT U14 Plan and Validation items are reconciled to the completed
exact-head native proof. This effort and the ROOT remain Active. The next
source unit must recheck current issues, owners and the supported entry before
claiming a concrete remaining gap. Ordinary-command and explicit-installer
adoption, U13 fresh settlement, existing fenced queues, macOS and affected-host
acceptance retain their separate owners and gates. This release observation
does not update any installation or authorize rollout.

### 2026-11-05 - U15 held-session drain protocol selected

After #10734 merges and managed source sync passes, current Dev still has
the concrete #10653 timeout branch: the MCP control client reports a
nonzero attached-session count as `drained: true` when force is requested.
The [bounded claim](https://github.com/ThomasMichon/copilot-extensions/issues/10653#issuecomment-6662663871)
selects only this protocol truthfulness correction and its direct
orchestrator consumer. This closes an existing protected-session invariant,
not a new retirement architecture.

The original current source and containing release #10732 are retained as
the before-state. Add red-on-old loopback control and direct orchestrator
tests before changing the timeout branch; retain the zero-session success
control, ordinary rollback and truthful malformed/unauthorized refusals.
The planned correction does not change native installer/watchdog paths,
publication readiness, reaping, adopted-client handover or live hosts.
Those broader #10653/#10652/#10486/#6928 gates remain separately tracked.

The bounded pre-fix packet reproduces two real failures: the loopback client
reports two held sessions as drained, and its actual orchestrator consumer
commits and requests shutdown. The zero-session control passes. After removing
the false forced-success branch, all 36 applicable cutover controls pass with
eight platform skips in 4.77 seconds. This includes ordinary rollback, truthful
unauthorized/malformed replies and preserved zero-session behavior.

A separate real native attached-session control passes in 1.83 seconds. It
starts a private Server and synthetic stdio echo upstream, initializes an
actual MCP attach, refuses forced drain while that attach is live, and
successfully exchanges another tool call over the same connection. The runner
retains its normal process and cleanup limits. The counter-only loopback
fixture and synthetic passive orchestration remain distinct from that real
session-consumer proof.

CLI help retains `--force` syntax without promising session destruction; the
MCP timeout diagnostic no longer recommends a force retry. Existing control
errors are not replaced with the busy-timeout message. Fatal Ruff and the
thirteen-plugin install-contract gate pass. The canonical refresh changes only
MCP's source digest and keeps footprint revision 36; a patch changefile is
generated without hand-editing any version.

These are source/private-process tests in fresh isolated profiles, not an
installer migration or full incoming-publication test. Native Linux/macOS and
live external installation are unperformed: the available hosted bootstrap
lifetime portfolio is a different contract, and no new venue or installed
host is activated for this slice. U13 and the remaining #10653 acceptance
retain their owners. Current-head CI, review, merge and release remain required.

Final pre-merge inspection tightens the diagnostic-only change: replace only
the known force-retry advice, never the entire existing error. Added controls
retain unrelated readiness failures as well as control errors. The combined
cutover and real attached-session packet passes 39 tests with eight platform
skips in 6.08 seconds; fatal lint and all thirteen install contracts pass again.
No native installer/watchdog or serving-publication behavior is added.

The first review of #10736 reports one Low documentation finding: explicit
Contract impact and Graceful cutover impact sections are required for the
observable busy-timeout change. The revised description names the legacy
`cutover --require-live --force` activation seam, unchanged wire shape and
supported count-bearing peer baseline, deliberate nonzero/rollback outcome
instead of false forced success, and the absence of a protection guarantee
when rolling back to the older defective control client. U13/#10652 incoming
publication and #10653's broader reaping/installer acceptance remain separate.
The new source head still requires its own CI and review.

### 2026-11-05 - U15 CI recovery and footprint-only rebase

Run `42065880698` completes its unit and guard jobs but never schedules the
required PR gate. GitHub's read API remains in progress while ordinary and
force cancellation say not in progress; rerun says already running. No refused
control operation is counted as an effect or a passing result.

Rerunning the completed same-head run `42065711918` succeeds: attempt 2 passes
units, guards and the actual required PR gate at 23:20:18 UTC. No source change
or gate bypass is needed to recover CI. The precise internal GitHub cause is
unknown.

By then Dev has advanced and the source PR needs a rebase. Both conflicts are
only in generated MCP producer digests, not the behavioral code or tests.
Retain the new base's peer-launch and packaged-runtime changes, regenerate the
combined current source digest through the canonical footprint tool, and keep
contract revision 36 unchanged. All four touched source/test bodies remain
identical to the approved revision. The rebased packet passes 39 controls with
eight platform skips in 5.72 seconds, including the actual attached-session
exchange; fatal lint and all thirteen install contracts pass. The rebased head
still needs its own source CI and review before merge. Broader #10653 acceptance
and all parent completion gates remain open.

The rebased source CI `42129460056` passes all three jobs at 23:35:28 UTC.
Current-head review `5793610263` identifies a Medium diagnostic gap: the
shared rollback logger emits the old force-retry advice before MCP corrects
the returned result. Both actual-orchestrator stderr regression cases fail
on that revision, with force false and true.

The MCP-local orchestrator normalizes only the known busy-session retry
advice before calling the unchanged shared rollback implementation; result
serialization uses the same correction. Control and unrelated errors remain
intact. No shared ZDD code, shutdown ordering or retirement authority changes.
The combined packet passes 41 controls with eight platform skips and 30
deselections in 7.50 seconds, including stderr and the real attached-session
exchange. Fatal lint, all thirteen install contracts and whitespace pass;
canonical MCP source-digest refresh retains footprint revision 36.

### 2026-11-05 - U15 source merged after recovered CI and stderr correction

Actual Copilot approval `5793708760` on `eb0775e3f8` reports no open issues;
both review threads are resolved. Latest source CI `42131212594` passes
units, guards and the required PR gate at 23:53:09 UTC. Managed merge confirms
#10736 merged at 23:55:11 UTC as
`3389a4e16fd053c26dba6c1d13f0cd26ad2d9d05`. No required CI gate is bypassed.
The repository-authorized missing CODEOWNER review exception remains distinct
from the actual passing Copilot review.

The ordinary squash pull-forward auto-aborts safely. All nine published paths
are then verified identical to the merged snapshot, with no later local commit
or uncommitted work. An empty-commit-range rebase advances the same worktree
without reset, stash or discarded work; backup and framework recovery refs
remain. This source unit is delivered, not a released or installed claim.

The parent's own Plan and Validation Plan remain Active on re-read. The
acceptance matrix records only U15's bounded source/consumer outcome. Next
observe the normal promotion that actually contains this merged snapshot and
verify the shipped implementation bytes; no forced release or updater is
authorized. U13/#10652 fresh settlement, broader #10653 installer/reaping and
#10486 reattachment acceptance retain their separate owners and gates.

### 2026-11-06 - U15 containing release verified without rollout

Normal candidate `42134630102` admits source
`6a6bbb0c306ca93d65e388ba97a2ff81533d9018`, which contains #10736, but fails
only `Promote dev -> main` during Sigstore verifier initialization after the
existing bounded retry. Source units, freeze, build and attestation generation
pass. Existing recurring issue #10651 receives the diagnostic; no duplicate
issue, source correction, trust-policy weakening or forced release retry is
performed.

Already-running normal successor `42137782452` admits
`ce18a228c5f143a07efcc2d96b36bca9c6f25fc2` at Main base
`32c045f081ba8d9c4b5759dfb3c9bfa21ec68d55`. Its source also contains #10736.
All release stages pass, including artifact verification, exact immutable-unit
reuse, publication/readback and normal merge. Generated #10825 merges at
00:51:39 UTC as `b408a6500bdd8a9ec5812cfaab1bbd882a610381`; the run finishes
successfully at 00:52:47 UTC.

The non-draft source release
`python-artifacts-ce18a228c5f143a07efcc2d96b36bca9c6f25fc2` publishes MCP
`0.2.189-dev1`. Downloaded archive SHA-256
`f467e0c9e26cd73086d47c7daf1bd1d40fffb25a8ec534ad882e472c7e801b80` and
index SHA-256 `24e00194e776f44b4b86bee833d6fe86c0e8ae8d1e71de878c5dfe45269ee473`
match the live release metadata. The index names the admitted Dev snapshot.
Both MCP implementation files (`cutover.py`, `__main__.py`) and the unchanged
ZDD cutover/rollback dependencies match the source-merge Git blobs, the
containing Main commit and the actual nested wheel bytes. This is archive
inspection only: no package is extracted, imported or installed.

This closes U15's bounded source, contained-consumer and containing-release
evidence. It does not close #10653's broader installer/reaping acceptance,
U13/#10652 incoming publication, #10486 reattachment or this parent's
Plan/Validation Plan. The prior verifier failure's internal cause remains
unconfirmed under #10651; this different source/controller run passing is
not same-SHA transient proof or an infrastructure-fix claim. Next select the
next owned/reproducible priority case from this matrix without lifting any
installed-host or deployment boundary.

### 2026-11-06 - U3 native Dispatch completion boundary qualified

The existing #9623 pywin32 bootstrap allowance and #9878 completion diagnostics
are retained rather than rewritten. The bounded source reservation is
[comment 6675005238](https://github.com/ThomasMichon/copilot-extensions/issues/10377#issuecomment-6675005238).
The new opt-in Dispatch packet extracts its actual native admission/completion
callers and invokes the real shipped construction-session helper, including
Dispatch's retained-helper publication before completion. It does not execute
the whole installer or substitute a successful completion reply.

All six Windows PowerShell 5.1/7 controls pass in 47.09 seconds
(128.95 seconds including preparation and cleanup) on the source based at
`aea7ecc6e9d4ba862d94cca0c20efb45b8b1a6a7`. A synthetic packaged
`pywin32.pth` completes without executing its bootstrap. Missing bootstrap
and modified executable-hook inputs retain the original child error and
nonzero result, discard only the owned incomplete candidate, and leave the
prior slot, current-version selector, configuration and deployment metadata
unchanged. Each actual helper exits and the attached session settles.

The initial fixture depended on undeclared psutil and failed collection; it
now uses native PowerShell PID observation without adding a dependency.
A second fixture omitted the required retained `runtime_launcher.py` and
failed all six controls with that actual child diagnostic. Copying the
complete canonical six-file bundle fixes only the fixture. Neither failed
result is reclassified as a pass; no production deadline, policy or helper
is changed.

The completion core, runtime/publication helper and source-runtime PowerShell
body match observed Main, but the complete Dev/Main bundles differ. The new
packet is not claimed as execution of identical released bytes, package-feed
qualification, selected Dispatch CLI recovery or a complete ordinary update.
#7305 retains that installed-consumer scope. U13/#10652 implementation is
already carried by #10754 and contributor #10810; this source unit does not
compete with either. Focused Ruff, all thirteen install contracts, twenty-three
plugin documentation consistency, headless-launch checks and whitespace pass.
The enforced type-check portfolio passes all forty admitted projects for both
declared platform profiles; that is static checking, not native Linux execution.
Review, required CI and merge of this test-only slice remain; the parent
Plan/Validation and all deployment boundaries stay open.

The current-head review identifies two valid test gaps: ordinary Windows
discovery would execute the nominally opt-in packet without a T3 marker, and
an exception-raising hook is not proof of non-execution because Python may
catch path-hook exceptions. The packet now declares its existing T3 tier and
uses an external execution sentinel for both bootstrap and modified-hook
inputs. Its absence is checked after every case, including removed candidates.

The revised packet passes twelve native controls in 98.16 seconds
(214.02 with ordinary preparation/cleanup): six against current source and
six against the complete released Main scripts at
`b408a6500bdd8a9ec5812cfaab1bbd882a610381`. The release replay uses the same
test body with the released script subtree as its input; its temporary fixture
is removed after execution, not committed as a historical Git prerequisite for
ordinary suites. An initial partial script copy omitted `base-python.ps1` and
failed before construction; the complete subtree corrects only that fixture.
No released script is rewritten or package acquired. A separate default run
skips all six T3 controls in 0.59 seconds (21.84 with preparation/cleanup).
This strengthens the bounded completion proof; it still does not activate a
Dispatch CLI or establish #7305's whole installed-update outcome.

#10851 merges as `0fbbdf2882b09ba312dd62cb9df8131257d6c735` after current-head
approval and successful required CI. Normal release #10871 then merges at
03:42:57 UTC as `cd8b58d272f6d5b3e9899aedcb7dcbd8fa6a3aa1`, admitting Dev
`b58884ecedd5f3b27b8dc1c12161a7a8a8c23f0b`. That source contains #10851 and
the released native-test Git blob matches the merged one. Run `42155377340`
passes every actual release gate. This settles the test-payload release
observation, not an installed update or new production correction.

### 2026-11-06 - U9 remaining Windows lifecycle controls

The bounded continuation is reserved in
[comment 6676519194](https://github.com/ThomasMichon/copilot-extensions/issues/10377#issuecomment-6676519194).
It extends the existing native recovery module rather than duplicating the
released repair implementation. `install -Force` must recover a selected
incomplete slot into a usable distinct generation and reuse that healthy
generation on repeat, preserving the original incomplete slot and configuration.
Both `install` and direct `provision` must return nonzero for actual venv or
package failure and publish neither a current selection nor a deployment
manifest. Marketplace uninstall now follows actual venv construction and an
invocation through the generated command instead of testing only a stamp.
It removes the selected runtime, selector and binstubs while retaining
configuration, unknown state and the original marketplace entry/manifest.

Seven controls pass through the standard contained Windows runner, based at
`07e4932be0`, in four bounded selections: forced repair/repeat, 1 pass in
132.22 seconds; venv failures, 2 in 44.68; package failures, 3 in 228.48; and
constructed marketplace uninstall, 1 in 156.71. Including preparation and
verified cleanup, the respective runs take 149.81, 63.06, 244.31 and 248.61
seconds. The 120-second native calls, 180-second item, 300-second group and
ordinary process/memory/storage limits remain unchanged.

The initial combined selection exceeds its 300-second group limit; it is not
a pass. The first isolated uninstall exceeds the item limit. A phase-timed
diagnostic then exposes the fixture's missing real external Python: construction's
CMD wrapper is not sufficient for uninstall's existing interpreter admission.
The fixture now additionally exposes its actual external base interpreter only
to uninstall. Replacing the newly added full-payload tree hash with precise
entry/manifest byte checks also removes about 38 seconds of unrelated hashing.
Temporary diagnostic output is removed; no production admission, census,
deadline, service or cleanup behavior changes.

UV package delivery supplies a synthetic CLI inside the real temporary venv;
host scheduled-task effects are guarded. These are native installer/selector
and removal consumers, not real package acquisition, live Connection Owner,
whole-suite update, a consuming harness's clean-room scenario or affected-host
rollout. Existing U8/U9 and #10448 results retain their original scope.
Current-head review, required CI, merge and containing release of this
test-only slice remain outstanding; the parent Plan/Validation Plan stays open.

PR #10889's first head `79b3ca9789` receives a genuine approval, but CI
`42163624170` fails its Bridge source-coverage guard: the historical HTTP-52
closure blob for `tools/bridge_http52_artifact.py` is unavailable in the fresh
checkout. Evidence preparation lists only HTTP-47 through HTTP-51. A focused
regression first fails because HTTP-52's distinct checkpoint is never prepared.
Adding that packet to the existing exact-commit preparation fixes this source
prerequisite without changing the validator or recapturing evidence.
All 58 preparation controls then pass in 48.70 seconds through the existing
bounded containment owner, including native unreferenced-tree controls.
An initial full local invocation instead hits four Git filename-length errors
under its overly long ad hoc scratch prefix; switching to the runner's canonical
compact roots fixes the invocation, not the tests or Git policy.
The revised head still needs its own review and full required CI.

### 2026-11-06 - U9 source merged; provider-guidance contradiction selected

#10889 merged as `75b9573f6bc2381d8fd1df70927c55da18f5e901` after the revised
head received approval and both required CI runs `42166438070` and
`42166577828` passed. The seven native lifecycle and 58 preparation controls
retain their recorded boundaries. A bounded twenty-minute normal-release
observer found no merged containing candidate; this is unobserved release,
not evidence of failed release or authority to retry/deploy. The earlier
six-minute type-contract timeout is already owned on #10895. No competing
pipeline patch, deadline change or runtime update is selected.

The next independent reconciliation finds #3030 closed only by consolidation
into open #3004, whose KO3/KO4 and KOV3 retain provider/landing preflight and
rejected-push recovery. I reserved a narrow documentation correction in
[comment 6678410713](https://github.com/ThomasMichon/copilot-extensions/issues/3004#issuecomment-6678410713):
`binding-knowledge` still unconditionally says knowledge repositories have no
PR gate. The correction removes that contradiction, resolves the Git provider
before authentication, honors repository/server publication policy, keeps
unknown evidence explicit and preserves local commits on rejection.
Registration/state-root readiness is not presented as server-policy proof.
This is below-altitude guidance repair, not a new provider interface or a
replacement for #3004's Plan/Validation. No authentication, binding, repository
creation, policy mutation or live provider publication trial is performed.

### 2026-11-06 - Canonical knowledge-related discovery slice

The guidance correction #10908 merges as `bc9ad742a9` after approval on its
actual head and all required CI `42173364030`. It does not complete #3004's
provider/landing preflight; containing release is separate from local rollout.

The next bounded source claim is
[#1419 comment 6679262258](https://github.com/ThomasMichon/copilot-extensions/issues/1419#issuecomment-6679262258),
K3/K5 discovery only. #3005 is closed by tracking consolidation into that open
parent, not by an implemented fix. The `gleaming-forge` K1 feasibility spike,
K2 overlay precedence, actual K4 write/cleanup lifecycle and all original
allocation/claim owners remain untouched. This realizes the already accepted
canonical discovery requirement while retaining the Worktrees vision's native
allocation and recoverable responsibility boundaries; it adds no lifecycle
engine or new registry.

Configuration-source selection carries the already resolved external-state
name into normal related reads. The existing canonical registration owner
validates identity/path/class without fallback discovery. The synthesized
projection preserves authored narrative/PR metadata and policy provenance,
but its knowledge role, local locus and non-delegation win over conflicting
relationship labels/venues. Read-only list/show/resolve emits explicit
aligned-worktree policy, never a writable anchor or standalone knowledge-agent
plan. The topology writer refuses to persist this projection; `related doc`
does not scaffold a new narrative into the registered knowledge anchor.
Invalid canonical input is a structured nonzero CLI error or an indeterminate
acquisition report, not a successful empty listing.

The first local packet fails on seven incomplete test constructors and two
diagnostic-stream assumptions. Correcting only those fixtures yields nine
passes in 87.59 seconds. Added ambiguity and actual invalid-class controls
then expose a real traceback at the new CLI boundary; the targeted correction
retains nonzero status and returns an actionable JSON/plain error.
Eight focused controls pass independently, followed by both actual installed
CLI bare-anchor/linked-worktree controls in 35.34 seconds, 117.50 including
preparation and cleanup. All 283 existing related/state-root regressions pass
in 76.13 seconds. The write-back refusal has its own focused regression.

The exact pre-change `d13d1d50b0` source reproduces missing bound knowledge in
the actual native worktree reader (`bound knowledge missing: []`), a failing
regression in 26.42 seconds under the original limits. An earlier baseline
invocation stops before collection due to duplicate timeout-plugin loading;
a separate bare baseline reaches its unchanged twenty-second child deadline,
neither credited as a functional reproduction. No deadline, process, memory
or storage limit is raised. Lint, thirteen install contracts and thirteen
headless adopters pass after a missing offline lint cache is restored through
the normal seasoned tool dependency path.

An opt-in `related-knowledge` portfolio uses the existing full-installation
workflow owner for exact-source Linux/Windows reader qualification, with
standard runner limits and no change to default/scheduled or required unit
coverage. It requires both real native entry cases in JUnit, rejecting skipped,
missing or ambiguous evidence. Current-head review, required CI, external
qualification, merge and release remain gates; no installed runtime, binding,
pair, overlay, credential, service or cleanup mutation is selected.

The final native fixture now retains an actual Git knowledge repository and
checks all three Git/checkout trees plus binding and registry bytes. Both
entry modes pass in 42.66 seconds, 134.41 including preparation and cleanup.
The current-checkout briefing override is constrained to retain the aligned
state policy instead of suggesting direct anchor editing. Its regression and
the full briefing suite expose seven failures from an obsolete
`related.ScanAuthority` reference; importing that existing type directly from
its canonical owner fixes the direct failures and swallowed briefing drops.
All 48 focused/briefing controls then pass in 3.48 seconds. Another 117
focused/workflow controls pass in 24.44 seconds, including actual inert child
success/failure and skipped-native rejection. No failure is dismissed as
pre-existing, and no policy or test limit is weakened.

Separately, #10908 is verified in normal release #10942, Main
`1a83c1d250ae8093a47afab1107c3ebb9553a53e`, merged at 08:06:00 UTC.
Actual admission from `42183304105`, attempt 1, names containing Dev
`4b5efce814772df02d88de580d8075155c76c15a` and Main base
`75417ed16ffa55b7dde8628b0c78f0fef50dd786`. All release jobs pass, both
defining binding-guidance blobs match merge/admitted/Main, and the released
payload is `harness-knowledge` `0.1.6-dev1`. This release does not install
anything locally or include the still-unpublished knowledge-related slice.

PR #10956 publishes the slice as head `3207ce155b` after an automatic rebase
onto `827acd1855`; its new feature/test/workflow bodies remain unchanged.
Initial required CI `42189687470` rejects the changed admitted
`related_acquisition.py` fingerprint before unit execution. This is a genuine
admission omission, not a flake or permission to rerun unchanged input.
The correction retains the closed source cohort, coverage targets, effect
guards and 300-second manifest budget, updating only three production bindings
and their existing pure-unit file while adding five exact actor-free nodes.
Canonical registry acquisition remains substituted in those models, and its
real import is lazy when no bound-state metadata exists. Existing fixtures
now include the actual StateRoot repository-name field. All 157 related
admission controls pass functionally in 4.46 seconds; full guarded packet and
revised-head CI/review remain required.

The documented Windows-native full packet executes 4,177 passes but refuses
qualification because three admitted POSIX filesystem/kernel-lease cases
intentionally skip on Windows. The 402.68-second run is within that separate
600-second profile, not the Linux 300-second CI contract. No successful
unit-evidence packet is claimed, no expected node is dropped and no platform
marker is spoofed. The exact Linux source-CI packet remains the required
no-skip proof. Missing `coverage` initially stops before execution; the
existing CI-declared test toolchain is then restored only in the private
prepared environment. Native registry acquisition remains outside the pure
unit frontier, and required revised-head review/CI must still pass.

### 2026-11-06 - Preserve discovery metadata through dependent consumers

Required Linux CI `42194087584` and `42194370472` pass head `830b7d631d`,
including the unchanged no-skip, 300-second unit admission. Review still finds
a real integration gap: downstream briefing, session summary and configuration
inspection turn typed configuration sources into plain strings, while the
PR-default reader omits their state identity. This loses the metadata needed
to synthesize an unauthored binding or replace a conflicting authored route.

The actual briefing producer fails both cases before correction. All four
consumers now retain the existing directory, tier and state markers; no new
source owner, topology writer or lifecycle operation is added. Both producer
regressions, the session summary and configuration-inspection regression pass.
The focused discovery and existing catalog-route packet passes 59 cases;
the existing PR/session packet passes 44, with eight Bash-only Windows skips.
The earlier full briefing packet passes 52 cases on the corrected producers.

A broad local dependent-consumer attempt fails, then stops at the ordinary
30-second item limit in the existing real CLI set/reset flow. A named diagnostic
also times out in an existing preference-preview child at its unchanged
15-second limit. Those Windows timing failures are not passing evidence or
explained away as routing success. No anonymous progress is reused as named
coverage. The existing opt-in Linux/Windows job now runs the four dependent
consumer files separately from its native discovery packet, under the same
standard limits. Both native discovery cases still must execute without skips.
All 111 workflow controls pass, including actual inert child failure propagation.
Hosted consumer results remain a merge gate; no timeout increase or CI bypass
is selected.

The already-admitted `config.py` source binding and its existing acquisition
fixture are refreshed for this review correction. Five unit failures expose
the fixture's old two-argument marker stub; it now verifies the retained state
identity explicitly. All 157 acquisition controls pass after that correction.
The 4,180-node set, coverage targets, effect guards and 300-second unit budget
are unchanged. Revised-head required CI, passing review
and native qualification remain due before merge; containing release and all
parent acceptance boundaries remain separate.

### 2026-11-06 - Refuse synthesized entries before authored upsert

Review `5798620519` on `1a247aa8e6` confirms the two source-marker findings
resolved, then identifies a write-back bypass in an existing-key upsert.
Merging a synthesized entry into an authored object loses the projection
marker before the writer checks it, allowing projected role/routing to persist.
The new two-case regression passes insertion refusal but fails existing-key
refusal against that source.

`upsert_related` now rejects the synthesized input before reading or merging
authored topology. Both cases preserve the exact authored bytes after refusal.
All 197 discovery and existing related writer/merge/CLI controls pass in
35.16 seconds, 132.45 including isolated preparation and cleanup. Ordinary
authored partial updates retain their existing merge behavior. Source admission
is unchanged, and the existing module ceiling passes without widening.
The prior head's CI and resolved findings do not satisfy the new-head review,
required CI or still-unperformed hosted qualification. No merge or rollout
is claimed; this parent remains Active.

### 2026-11-06 - Enforce narrative refusal in the owning scaffolder

The next review on `7c4ed5355c` confirms upsert refusal resolved but notes a
moderate finding not published as an inline thread: direct scaffolder calls
can bypass the CLI-only guard. Investigation confirms the owning helper can
create a missing bound-knowledge narrative or force-overwrite an authored one.
Three actual helper cases fail before correction; reading an existing file
without forcing already remains read-only.

The refusal now lives in `scaffold_doc` itself. The CLI uses that canonical
boundary instead of its duplicate guard, preserving existing narrative reads
while refusing all synthesized creation/forced writes. The four direct helper
cases check complete harness/knowledge tree bytes. All 203 discovery, existing
related writer/merge/CLI and actual native Git bare/linked entry cases pass in
114.22 seconds, 205.36 including preparation and cleanup, with no skips.
Both native consumers retain their original per-item and child deadlines.
The existing admitted CLI hash is refreshed for the guard move; the unit node
set, source cohort, coverage/effect guards and 300-second budget are unchanged.
Fresh-head review, required CI and hosted discovery/consumer qualification
remain due. This is source validation, not a live binding, pair or rollout.

### 2026-11-06 - Knowledge-related source and hosted qualification complete

#10956 merges at 10:53:30 UTC as
`27af49e7eaa33733a32e43bbb86a541558f74042`. Actual review `5798951079`
on final head `abcb17534424a180ce93610adc272022becab9a9` has no open code
findings and requests the native/consumer validation then still pending.
All three inline findings are corrected and resolved. Required CI
`42203689201`, `42203911472` and final description-triggered `42205361933`
pass that unchanged head. The managed merge uses the authorized ordinary
review-rule exception, not a required-CI bypass.

The existing reviewed opt-in owner runs `42204493016` at that exact head and
both Linux/Windows jobs succeed. Downloaded JUnit contains 22 successful
discovery cases on each platform, all 159 dependent consumers on Linux, and
151 dependent consumers plus eight explicit Bash-only skips on Windows.
Those eight execute successfully on Linux. Both bare-anchor/linked-worktree
native entry cases execute uniquely without skips on both platforms.

The two earlier local CLI timeout cases pass with the original limits:
set/reset takes 2.750 seconds on Linux and 3.875 on Windows; the repository-name
preference flow takes 1.613 and 2.409 seconds. These are platform consumer
results, not a local-performance pass. Earlier local failures and refused
Windows full-unit evidence remain recorded rather than relabeled successful.
The final local 203-case packet and hosted qualification cover the corrected
producer, upsert and helper-owned narrative boundaries. No macOS or installed
host result is inferred.

Post-merge managed sync safely aborts an incremental squash replay conflict.
The reviewed combined patch and actual squash patch have identical stable ID
`dfac4b3bdc3fcfe2c7f9dab62d07d7e7bbf28db2`; all seventeen feature, test,
workflow and journal bodies match. Only shared TESTING and the unit manifest
also retain peer contributions. A local backup preserves the reviewed tip,
and skipping only the proven-merged range permits normal managed sync.
The canonical Plan and Validation Plan are reread and remain Active.

This completes the bounded #1419 K3/K5 source/discovery reservation. Its K1
feasibility/retrofit, K2 overlay precedence, actual K4 aligned write/cleanup
and full receiving lifecycle retain their existing owners and acceptance.
The next gate is normal containing-release observation, separately from
rollout; this parent is not Done and no worktree is finalized.

The bounded source completion is published and read back on both existing
coordination threads: [#1419 comment 6683277573](https://github.com/ThomasMichon/copilot-extensions/issues/1419#issuecomment-6683277573)
and [#10377 comment 6683279947](https://github.com/ThomasMichon/copilot-extensions/issues/10377#issuecomment-6683279947).
The original K1 feasibility owner is still present; no reservation or broader
acceptance is cleared by age or by this completion note.

Read-only release-health reconciliation diagnoses older run `42201092834`
literally: both Windows Resume fixtures fail with missing
`work_coalescing_singleton`, not a timeout or assumed flake. Existing owner
#10960 already fixes that isolated fixture and adds a real isolated-session-shell
regression, merging as `375ed2eb53fead5ee8cd3b0daeeb997382d42623`.
Normal release #10981 qualifies that exact Dev source and merges as Main
`4a8e317f0b169c71036acb751dbcd1de221cb0af` at 10:42:43 UTC.
That is verified resolution of the earlier failure, not this slice's containing
release: its source precedes #10956. No competing patch or forced retry is
selected. A bounded read-only observer waits for a merged normal candidate
whose admitted Dev contains this feature, then still requires actual admission
and released implementation-byte proof before crediting release.

The bounded twenty-minute observer sees no containing merged release. A new
actual failure in run `42207277039` is independently inspected across every
job: source units, unsigned construction and native Windows Resume pass,
while artifact attestation fails with `InternalError: error retrieving identity
token` and `CI: no tokens available`. Existing #10990 owns that recorded
incident. The actual workflow commit declares the required OIDC/attestation
write permissions; no missing grant or source fix is inferred.

Normal successor `42208938885` then passes its actual attestation job and
advances to immutable-candidate checks without this workstream forcing a
retry or changing access. [Diagnosis 6683772491](https://github.com/ThomasMichon/copilot-extensions/issues/10990#issuecomment-6683772491)
is published and read back. The successor is a later admitted source run,
not same-input rerun evidence or a completed Main release. A bounded attached
observer now follows that exact run; admission, final published bytes and
Main merge still precede containing-release credit. The report stays open
and signing/authenticity gates remain unchanged.

### 2026-11-06 - Knowledge-related containing release verified

Normal successor `42208938885` completes successfully. Release #10991 actually
merges at 11:30:48 UTC as Main
`f921808d2292394abc738149891de4efdc933630`. Its downloaded immutable admission
names Dev `8073d6b937c20a5ee994c2edda9ab5a48e98b643`, Main base
`8e2ac72460f6fbb78f9e3bb8e1c403875ad9a3c7`, original producing attempt 1.
That actual admitted Dev contains #10956's merge, unlike the two older
normal releases observed earlier.

Every required release gate succeeds, including actual artifact attestation,
immutable-candidate unit reuse, publication/readback and the head-pinned
non-administrative Main merge. All fifteen defining production, fixture,
workflow, skill and README bodies match the source merge, admitted Dev and
published Main exactly. The released Worktrees payload is `1.24.504-dev1`.
No native test is rerun merely to restate unchanged-source evidence.

This satisfies U16's source and containing-release observation. It is not an
installed binding/pair/overlay, general retrofit/write/cleanup, full receiving
or affected-host recovery result. The earlier failed attestation run remains
failed and #10990 remains open; a later admitted source succeeding is not
same-input rerun evidence. No updater, permission change, token fetch, manual
release or rollout is performed. The parent stays Active with its remaining
Plan and Validation Plan under their existing owners.

### 2026-11-06 - Native registration inspection prerequisite proposed

Source reconciliation finds a bounded setup-consumer gap under #10996.
The existing front-door reverse lookup picks the first project mapping and
suppresses acquisition errors; strict repository inventory does not include
the complete legacy project-adoption mapping. A setup caller cannot use either
as proof of unique identity, preserved exposure policy or an unclaimed fresh
default. Copying their parsers into each caller would create another authority.

U17 proposes one additive, read-only native inspection contract, composed from
the existing strict repository and project registry owners. Given the selected
canonical anchor and proposed default name, return the unique registered name
and authoritative exposure, or the genuinely fresh unclaimed default. Refuse
ambiguous aliases, inconsistent roots, default-name collisions, malformed or
unknown acquisition and invalid identity/path/policy data. Retain native
platform expansion and namespaced installation context; do not discover,
register, migrate, reconcile commands, clone or start a service.

The proposed version-1 result/error contract is qualified against the selected
actual CLI, not a package-version guess. A read-only request may discover
unsupported syntax without effects, but the caller must retain that truthful
incompatibility rather than retry with safety semantics removed. Expand and
release the provider first. Consumer migration, cold-entry availability and
old-runtime recovery remain separate explicit gates, not inferred from a new
read verb. That migration must acquire/use the independently runnable incoming
reader through the existing construction owner when the installed receiver
cannot supply the contract; old capability support must not become a veto of
an explicit latest-flow replacement.

Focused controls will exercise actual registry acquisition and the real CLI
parser/process boundary: empty fresh state; a custom alias alongside another
checkout owning the default name; consistent canonical/legacy entries;
duplicate aliases and conflicting roots; invalid/unreadable data; explicit
exposure true/false; platform/root expansion; exact configuration preservation
and no-effect sentinels. Keep pure work-count/decision controls small; native
subprocess coverage stays in the explicit contained lane rather than enlarging
required unit-only CI.

This closes an existing state-preservation/adoption requirement of
[authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement),
not a new registration architecture. Existing #356/#8545 acquisition/setup
writers, #7500 forced-registration preservation, and #4205 family-wide routing
retain their owners and scope. No writer or ordinary front-door behavior is
changed by this proposal. Source handle `dusty-contraption` owns only U17.
The parent remains Active; the proposal clears review before implementation.

The proposal clears that gate in #10997: exact head
`0d0e7ac88b6d89a6b9e6f4211670bbfe10b60e2f` receives Approve with no active
issues, required CI `42219424666` passes, and the normal managed flow merges
at 12:44:02 UTC as `3ca033f94686a7ee3f054e3825b619745fa86b0f`.
Public body, codename source marker, empty labels and closing references are
read back. Managed terminal status and pull-forward succeed; #10377 remains
open. The Plan and Validation Plan are reread before implementation. This
resolves only the U17 proposal gate, not its source, release or caller outcome.

### 2026-11-06 - Native registration inspection candidate qualified locally

The additive `repos registration inspect` candidate declares
`copilot-extensions.project-registration-inspection`, version 1, through the
actual executing read-only response. Existing routing, inventory and writers
are unchanged. It composes the strict repository owner with the original strict
project parser before migration can discard legacy anchors. Canonical exposure
remains authoritative over a stale adoption mirror; known empty legacy defaults
and singleton mode survive. Unsupported versions and unknown arguments fail
before acquisition, while malformed/unknown sources do not become absence.

The first actual native-parser regression is red against the old source:
both exposure variants return the old unknown-subcommand error rather than
the required unique alias. The first 26 controls then pass. A later namespace
fixture fails before registry acquisition because it supplies the cell root as
the payload root; correcting only that fixture yields the residual pass without
changing product behavior, budgets or containment. That failed attempt is not
credited as a passing native packet.

Direct review also confirms that Windows extended-path and ordinary spellings
can identify the same directory while comparing as different strings.
The candidate uses native file identity for those aliases, retaining ambiguity
when two project names refer to that anchor. The final complete 34-control
Windows packet passes in 10.38 seconds, 66.64 seconds including preparation and
verified cleanup, under the unchanged standard limits. Its actual neutral-CWD
new-read/old-inventory consumer takes 4.197 seconds for both invocations; this
is a local functional sample, not fleet throughput, cold provisioning or a
tail-latency claim.

Nine separate tool controls execute the optional workflow's evidence-admission
and source-pin predicates against explicitly synthetic fixture reports. Missing,
duplicate, skipped, empty and failed evidence is refused. These are workflow
controls, not fabricated product-native evidence. The opt-in
`registration-inspection` portfolio uses the existing bounded owner on Linux
and Windows only, requires the exact published source SHA, and adds no
scheduled or required-unit work or larger resource limits. Its hosted native
executions remain pending.

F/E9 lint, install-contract, documentation consistency and strict runtime
resolution checks pass. The actual admitted-unit source-binding check is
unchanged; no inventory, coverage or budget is relaxed. Source publication,
current-head review/required CI, exact-source hosted qualification and
containing release observation remain gates. The provider does not yet migrate
setup callers or qualify an installed host; #10377 remains Active.

### 2026-11-06 - Strict option review correction and retained closure gate

The first published head `89bd803bc47cfd053735292647a0c421659a03ff` receives
one Medium review finding: argparse accepts recognized-option prefixes before
acquisition. The correction disables abbreviation for the entire versioned
parser, not only one spelling. Four pre-read rejection variants cover the
unknown option and the anchor/default/contract prefixes. The six impacted
parser, actual CLI and namespace controls pass in 16.09 seconds, 125.97 seconds
including preparation and verified cleanup, at unchanged limits. The earlier
34-control complete packet remains attributed to its original source; the
current 37-control inventory still needs exact-head hosted qualification.

All failing jobs in CI `42226529545` are inspected. Eligible units execute
successfully. The guard job fails because HTTP-52's existing execution closure
includes all Worktrees/Codespaces source modules; the new inspection module
therefore changes its captured inventory. The required PR gate's failure is
the consequent failed source guard, not a unit-test failure.

The existing `bridge-http52-capture` portfolio owns the necessary controlled
capture, independent verification and canonical artifact export. Its source pin,
contained consumers, existing budgets and publication preimage checks remain
unchanged. The next revision must clear actual current-head review, then use
that owner at the corrected exact source and materialize its genuine outputs.
No stale hashes are hand-edited, no module is relocated to blind the guard,
and no signing, evidence, CI or containment rule is bypassed. Native inspection,
retained closure, source merge/release and caller recovery stay separate gates.

### 2026-11-06 - Exact-source native and retained closure qualified

The revised source `61ed09ec2a75fc1ecbda378abd5f01f1ab96928d` receives a
current-head Comment with no open findings; the prefix defect is resolved.
Actual optional run
[42230420118](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42230420118)
passes both Linux and Windows source-pin and native-consumer jobs.
Downloaded JUnit contains 37 unique passing cases on each platform, identical
complete case sets and zero skipped/failed/error cases. Both real neutral-CWD
CLI and namespaced-owner witnesses and all four prefix variants execute.
These are complete native results, not an aggregate inferred from local samples.

The existing controlled source-proof run
[42230558753](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42230558753)
also succeeds at that exact source. Declared preparation, artifact-boundary
controls, immutable peer-prefix consumer, current HTTP-52 capture, independent
verification, canonical validator and historical publication guards all pass.
The actual exporter records generation 52, 190 cases, 131 canonical outputs
and manifest digest
`c9b23ae01243fcca3c38c639f93bfab494bd2daf2138053e10bcb185d1199bb7`.

The downloaded canonical artifact's manifest bytes match that actual producer
receipt. With the local HEAD still exactly the captured source and the original
inputs intact, the existing native publication owner validates all paths,
identities and original preimages and materializes 131 outputs. All 131
published bytes then match the manifest; only those current contract outputs
change and prior HTTP-51 evidence remains unchanged. No hashes are hand-edited,
no capture is relabeled, and no new registration/runtime behavior is introduced
by the evidence refresh.

The previously failing local required checker now reports
`OK (2 contracts, 1043 fixtures)`. Admitted-unit source bindings are unchanged.
An agent-bridge patch changefile accompanies its generated current-evidence
delta, without changing protocol generation. The implementation and all native
inspection fixtures remain the qualified source. The next normal same-PR push
refreshes actual review and required CI for the canonical metadata delta before
source merge and containing-release observation. No redundant native rerun is
credited, and the parent/caller/installed gates remain open.

### 2026-11-06 - Preserve accepted upstream inputs during source reconciliation

The generated-evidence head `3a4637641109baf81679d1c9a2ee7b2483a9c39a`
receives an actual Approve with no active issues. It cannot merge because
`dev` has advanced and its generated HTTP-52 records conflict; required
pull-request CI has not been admitted for that conflicted head. The workflow
is active and the PR remains ready, so this is not treated as an unavailable
runner or solved by rerunning an older failed head.

The current published base
`63599c74e559b367df9e674409399db7ea2f90ce` includes legitimate retained-writer
inspection and continuation-classification changes from #11001/#11012.
The conflict-safe managed sync first aborts cleanly. The supported owned-branch
rebase then retains both inspection commits; all 131 conflicts are the known
generated current contract outputs, not the read implementation. Those exact
paths are resolved to the published upstream inputs, preserving its new probes
and immutable provenance rather than selecting the older capture or combining
incompatible records by hand. No other contributor source is replaced.

The prior 37/37 native and 190-case retained captures remain genuine evidence
for source `61ed09ec2a`; they are not relabeled as qualification of this
composed tree. A new exact checkpoint and fresh current-source capture are
required after reconciliation. Inspection implementation, native fixture and
qualification-controller equivalence will be checked separately from changed
shared inputs. Normal same-PR publication, fresh review/CI, source merge and
containing release remain open; the parent is Active and no installed effect
or gate bypass is introduced.

### 2026-11-06 - Current capture succeeds but historical packet exceeds its bound

The reconciled-source portfolio `42239617443` is failed, not qualified.
Every failed job/step is inspected: declared preparation, input boundaries,
immutable peer prefix, current capture, independent verification, canonical
validation and artifact export all succeed. The sole failure is the later
`Exercise immutable bytes and historical publication guards` request.

That request combines `test_http48_source_retention.py`,
`test_http48_capture_guards.py` and `test_http47_capture_guards.py` under the
existing selector. The bounded runner reports `wall-clock limit exceeded
(300s)` and exit 124, 302.92 seconds including cleanup. Anonymous progress
shows 72 cases at 62 percent and more dots, but has no retained named outcomes
or per-case timing; those dots cannot license a residual passing subset.
This is not relabeled as a successful portfolio or retried unchanged.

The exported current artifact remains provisional evidence; it is not
materialized while the required historical packet is unfinished. The next
source correction must inspect fixture/packet cost and preserve all selected
assertions and original item/group/plugin/resource limits. The existing runner's
independent file-group composition and named JUnit/progress support are the
first bounded avenue to assess, not a larger timeout or a skipped history guard.
Source #11003, containing release, caller migration and the parent remain open.

### 2026-11-06 - Diagnose the historical seed and retain named guard results

The existing filtered runner deliberately keeps `-k` selections together;
`--max-files-per-sub-suite 1` therefore does not split this request. That
ineffective change is removed, not credited as a fix or used to expand a
deadline. Named local collection resolves 115 selected cases, rather than an
inferred count from the prior anonymous dots.

The HTTP-47 composed seed repeatedly parses immutable historical packets for
each semantic source. It now indexes their exact `(source_path, source_sha256)`
identities once and retains the same coverage decision, source files, fixtures,
assertions and fresh consumer repositories. The 2,244-file, approximately
22-MB source/history seed is not trimmed. The workflow retains named progress,
slow-case timing and JUnit output on failure without lifting the original
30/300/900-second item/group/plugin or resource limits.

The workflow admission/retention and existing registration admission controls
pass locally, 10 cases; touched Python passes `ruff --select F,E9` and the
diff is whitespace-clean. The first local Windows run times out during repeated
packet reads; the indexed revision gets past those reads but still reaches the
30-second setup deadline while staging its real Git seed. Neither is a
passing guard portfolio. The existing Ubuntu guard-only portfolio is the next
bounded diagnostic execution before another full current-source capture.
The provisional earlier artifact is still not materialized. This below-altitude
test/controller correction changes neither production inspection nor runtime
behavior; required CI, complete historical qualification, source merge and
parent completion remain open.

The Ubuntu diagnostic run `42247019069` stops at canonical validation before
reaching the guards: the literal error is `HTTP-52 historical closure lacks
required owners`. No historical case runs or passes in that attempt. The
workflow now exercises the unchanged guard selection after independent capture
verification but before canonical validation/export. This preserves every
gate, exposes guard failures even when committed current metadata is stale,
and prevents exporting a qualified artifact before the guards succeed.
The updated stage-order/admission packet passes all 10 cases in 4.99 seconds,
with touched F/E9 lint and whitespace checks passing. A fresh exact published
checkpoint is required for the next diagnostic execution.

### 2026-11-06 - Preserve native identity and partition the measured publication cost

The named Ubuntu attempt `42247572862` genuinely executes the historical
request at `9d737dba5d`. Its shared seed and both history-preservation cases
pass; the latter take approximately 29 seconds each. It then reaches the
HTTP-51 dirty-source case after named successes through 69 percent, but the
combined group reaches 300 seconds, exit 124 / 303.15 seconds including cleanup.
This identifies aggregate composition, not a failing assertion or a pass for
the unfinished portfolio.

The existing sequential cohort planner now splits only
`test_http48_capture_guards.py` into four disjoint generation groups.
The catch-all retains untagged/future cases; overlapping future keywords are
assigned once. Every other selected file remains. All groups retain the
30-second item and 300-second group bounds and share the original 900-second
plugin aggregate, including preparation/cleanup, with unchanged process,
memory and storage caps. There is no additional worker or capacity request.
Nine focused collection/deadline controls use real pytest collection and the
normal runner seam; together with the existing controller and non-Bridge
planner controls, all 20 pass in 10.32 seconds. An initial synthetic-fixture
error omitted declared native files and supplied the lower-level Limits
default rather than the CLI's actual 300 seconds; only that fixture is corrected.
This is not a qualification of the real historical packet, which still owes
its next bounded execution.

The new source review also identifies a real native root concern: the text
`normcase` shortcut can select a foreign root or accept inconsistent roots
without consulting file identity. Both focused controls fail on that shortcut.
They retain real distinct directories and native `samefile`, replacing only
the lossy normalizer seam. The reader now uses native identity first and
permits only exact resolved spelling when a declared path is absent, never
case-folded text as authority. The complete native Windows registration packet
passes 39 cases with no skips, 10.13 seconds / 98.02 seconds including
preparation and cleanup. Existing extended-path aliases, CLI/namespace,
policy and preservation controls remain. Fresh exact-source Linux/Windows
qualification is required; the earlier 37/37 is not relabeled.
Touched F/E9 lint and whitespace checks pass. Same-PR review, historical
qualification, fresh current capture/import, CI, merge/release and the parent
remain open; no installed effect or larger deadline is introduced.

Further local Windows seed trials deduplicate parent-directory creation and
reuse copied immutable bytes for the identity index. Both still reach the
original 30-second setup bound, the latter during the real history checker;
neither is credited as a passing case. Those unqualified extra copy/cache
changes are not retained. The smaller identity index and measured cohort
composition remain. Native Windows historical-seed construction is an explicit
unresolved owned follow-up before worktree finalization, not a flake exemption
or installed-runtime result. The ordinary Ubuntu historical lane and the
fresh 39-case native OS lanes still owe genuine current-source qualification.

### 2026-11-06 - Current capture and every historical cohort genuinely qualify

Exact source `50e4bbebc56244f8cdb849a1bdc8aed52269f74b` now independently
qualifies in native run `42251432592`: Linux and Windows each have 39 unique
passing cases, no skips/errors and identical complete downloaded sets matching
the local packet. Both actual source-pin/execution steps and native CLI,
namespace, prefix and physical-identity controls are audited. Earlier 37/37
results keep their original attribution.

Full run `42251455469` succeeds, including every preparation, boundary,
capture, independent verification, historical guard, canonical validation and
export step. Current capture and independent verification each pass 197 cases
in 216.68 and 215.44 seconds. The five retained historical JUnit reports contain
6, 31, 26, 26 and 26 cases: 115 distinct passing cases, no skips/errors, exactly
three HTTP-47 history/seed cases, three archive cases and 109 publication cases.
Per-group case durations are 64.006, 100.569, 67.005, 66.134 and 69.841 seconds.
The historical request takes 376.97 seconds including preparation and cleanup,
within the original shared 900-second aggregate; each group and item keeps its
original bounds. This is complete current qualification, not an anonymous
partial subset or a pass copied from the failed attempts.

The actual export manifest digest
`5c920b85dcec0bfbbb3835373a58c26ecf1ba08e8d73b00b3246edb2ff459864`
matches the downloaded producer bytes. At the unchanged source HEAD, the
native artifact owner validates 3,059 input preimages and materializes all
132 declared outputs. Independent byte/hash comparison matches every output;
only declared output paths differ and HTTP-51 remains untouched. The failed
earlier portfolio's provisional artifact is never used.

Current required CI `42251397433` separately exposes the runner's actual module
cap failure, not a flake or just stale evidence. The cohort evaluator and its
policy constants move mechanically into the existing `plugin_test_selection`
owner; the root wrapper, platform selection, `_plugin_dir` test seam and
existing constant re-exports remain. No baseline/cap exemption, parallelism,
deadline or source/protocol behavior changes. All 123 focused runner,
selection, cohort and workflow controls pass in 139.84 seconds; F/E9 lint and
whitespace pass. The actual modified runner CLI separately collects exactly
the 115 previously executed hosted cases, once each, in 98.89 seconds including
preparation/cleanup. It is collection equivalence, not another full execution.
The canonical checker passes with 2 contracts / 1,044 fixtures after the move.

The registration implementation, native fixture and hosted registration
steps remain those genuinely qualified at `50e4`; the subsequent delta is
canonical evidence plus mechanical test-planner componentization, qualified
separately rather than relabeling the source pin. The next committed branch
must pass the exact module guard and fresh review/required CI before merge
and containing-release observation. The local Windows historical-seed
performance follow-up and all caller/installed/parent gates remain open.

### 2026-11-06 - Preserve the next accepted upstream cohort before requalification

The qualified-output/componentization candidate `01b7c92c6a` passes the exact
branch-scoped module guard and is published with an audited current validation
record. Provider readback then reports an actual merge conflict; required CI
is not admitted for that conflicted head. Rerunning the older failed head
cannot resolve this condition. The PR's retained base field is not treated as
the current remote branch tip.

A local backup preserves the complete qualified candidate. Managed sync aborts
its conflict safely. The owned-branch rebase onto published
`5c27efdad108c5e5e63ede1ae4f29025ad53eafe` then encounters exactly 132 conflicts,
all within the actual producer's declared generated-output inventory. Those
exact paths take accepted upstream inputs, not the older current capture or
hand-combined hashes. The native read feature, identity fix, guard correction
and mechanical planner move survive; no contributor implementation is
superseded.

The newer cohort includes original keyed creation custody, portable native
knowledge preferences and original-queue relationship admission. Its current
capture owner declares 229 cases, not the older 197. All newly accepted cases
and source inputs are retained. The complete 39/39 native and 197/115 retained
results remain genuine evidence for `50e4`, never relabeled as proof of this
composed tree. Fresh exact-source native and complete current capture are
required before final CI/review/merge/release. The reconciled branch's module
guard already passes without changing a ceiling. Source #11003 and this parent
remain open, and no installed effect or larger validation budget is introduced.

### 2026-11-06 - Preserve the 240-second capture bound by indexing admitted source names

Reconciled source `7f31ec078ec27f8dd355389b504cb025bb13f8f8` independently
passes native run `42259056903`: both actual source-pin/execution steps and
downloaded Linux/Windows reports have the same complete 39 unique passing
cases, no skips/errors and every native/prefix/identity witness. This is fresh
qualification of the newer shared source, not a relabeled earlier pass.

Its full 229-case run `42259071477` fails in current capture before independent
verification, historical guards, canonical validation or export. The literal
failure is the existing caller-owned 240-second POSIX deadline in
`capture_bridge_http48.run_probes`; no assertion failure or passing residual
set is established by its buffered output. No artifact is imported and the
deadline is not raised.

The capture audit's `exec` hook currently scans the entire source inventory
for every source-name lookup, including unowned dynamic code. Against the
actual 2,012-path earlier proof, 5,000 lookups take 6.92153 seconds locally.
An admitted interpreter now binds one suffix index after its existing
self/preloaded-source verification. The lookup retains longest-suffix choice,
lexical ties, exact case/segment boundaries, Windows separators and unmatched
refusals. Source bytes, compiled code, origins, roles, launch binding and
durable audit records are still checked exactly as before; neither hashes
nor authorization are cached or inferred from the index.

Four focused matching/no-rescan/observer-refusal controls pass in 0.60 seconds
of tests, 121.39 seconds including declared preparation and verified cleanup.
The same 5,000-lookups/2,012-path local sample takes 0.003941 seconds with the
prebound locator and returns the same expected sources. This is a local lookup
measurement, not a full-portfolio or latency claim. Three new pure locator
controls join the original 115 historical cases; no existing case is removed.
The complete 229-case capture and all 118 historical guards still require
genuine execution at the next committed source with original deadlines and
resource bounds. Normal current review/CI, import, merge and release remain
open, as do the caller, Windows historical-seed and parent gates.

### 2026-11-06 - Indexed current capture qualifies all 245 cases within the original bound

Exact indexed source `0055ce93cbf6478e4fad06a3cbbc94d2a2e1c904` independently
passes fresh native run `42263400947`: both source-pin/execution steps and
downloaded Linux/Windows reports have 39 unique passing cases each, no
skips/errors, identical complete sets and every real CLI/namespace/prefix/
identity witness. This is the source including the newer prepared Windows
entrypoint inputs, not a relabeled earlier pass.

Full run `42263417055` succeeds in 14m44s. Capture and independent verification
each pass all 245 declared cases, with test times 183.10 and 180.35 seconds,
and 196.59 / 193.73 seconds including preparation and cleanup. Both finish
inside the unchanged 240-second caller-owned POSIX limit. Five historical
JUnit reports contain 6, 34, 26, 26 and 26 unique passing cases: all original
115 plus the three new locator controls, 118 total, no skips/errors.
Their case-duration sums are 61.146, 100.192, 67.783, 66.954 and 71.304 seconds.
The whole historical request takes 376.97 seconds including preparation and
cleanup within its original shared 900-second aggregate; group/item/resource
bounds remain unchanged. This is genuine complete indexed-source execution,
not the local micro sample, a partial timeout result or an older capture.

Actual current artifact `13526268689` and historical outcomes `13526478412`
are downloaded and audited. The producer's logged manifest digest
`1973f62366cf7a6576ead010afc0934b5adb58f339aa8270697c524b9c0826e0`
matches the downloaded manifest. At unchanged source HEAD `0055`, the native
publication owner validates all 3,072 original input preimages and materializes
all 139 declared outputs. Independent hash/byte comparisons match every output;
only those declared paths differ and HTTP-51 remains untouched. No failed
229-case or earlier provisional artifact is used.

The next revision contains canonical evidence and this journal only. Production
read code, native fixture, cohort policy and capture instrumentation remain
the qualified `0055` source; its source pin is not relabeled. Current required
CI/review and actual merge/release still precede source completion. The owned
local Windows historical-seed follow-up and original caller/cold-entry/
installed recovery gates remain open, with no installed update or larger
validation budget authorized.

### 2026-11-06 - Final evidence revision passes CI and review before incoming-source reconciliation

Evidence-only revision `87f97ad1cdaf2f3ecca8cbbfb2a262d4f41e5132`
passes required CI `42267204327` and receives an Approved review at
18:06:28 UTC. It does not merge: the base advances to `1593247e20`,
and GitHub reports an actual conflict in the canonical HTTP-52 outputs.
The membership review thread remains open pending the final reconciled
publication; a green verdict does not resolve that thread automatically.

All previously qualified source and producer outputs remain on the retained
pre-reconciliation branch and in their original artifacts. The guarded sync
aborts without changing the branch. A subsequent owned rebase preserves every
incoming source change and resolves exactly 139 generated-output conflicts
using the accepted upstream canonical outputs as a temporary baseline.
No production, fixture, workflow or instrumentation conflict is discarded.
That baseline is not qualification of the combined source.

Incoming Bridge status contracts and their seven declared probe instances
remain included: the next current capture and independent verification must
execute all 252 cases. Incoming runtime-delivery changes also require fresh
native qualification. The genuine `0055` 39/39, 245/245 and 118-case passes
retain their exact source attribution, not a claim about this new tree.
Current-source capture, verification, historical guards, canonical import,
review/CI, merge and release remain open under the original bounds.
Caller, local Windows historical-seed and installed-host recovery gates are
unchanged; no update, deployment or larger validation budget is authorized.

### 2026-11-06 - Combined source qualifies every incoming case and canonical output

Exact combined source `8c79b42decdd1d75c65cfd4254703edf1a894bee` passes
fresh native `42269611394`. Both source-pin/execution steps and downloaded
Linux/Windows reports contain 39 unique passing cases each, no skips/errors
and complete identical expected sets, including actual CLI/namespace/prefix/
identity witnesses. Incoming runtime-delivery code is genuinely included.

Full `42269629159` passes every step, including all 252 current capture and
252 independent-verification cases in 173.27/170.30 seconds of tests under
the unchanged 240-second caller-owned POSIX bound. The five historical
reports contain 6/34/26/26/26 unique passing cases, all 118 original-plus-
locator controls, no skips/errors. Their case-time sums are
44.400/71.947/49.380/48.876/51.690 seconds. Original group, item, aggregate
and resource bounds remain; no incoming case or old assertion is removed.

Actual qualified artifact `13530820180` and historical outcomes
`13530850148` are downloaded and audited. The producer's logged manifest
digest `19431323dafbf410cc2b47d4e6a9bdb44c9a2c92035cac3453c7efbcbd7cc36c`
matches downloaded bytes. At unchanged HEAD `8c79`, the native publication
owner validates all 3,074 input preimages and materializes all 140 declared
outputs. Every output matches byte-for-byte, only declared paths change and
HTTP-51 remains untouched. The canonical checker passes
2 contracts/1,051 fixtures.

The next revision changes canonical metadata and these validation records
only; executable source, workflows, native fixtures and instrumentation remain
the qualified `8c79` source. Earlier qualifications retain their own source
pins and artifacts. Final current-head review/CI, evidence-thread resolution,
actual merge and containing release remain gates. Caller, local Windows
historical-seed and installed recovery remain separate unfinished objectives.

### 2026-11-06 - Native registration prerequisite merges after all final gates

Source PR #11003 actually merges at 18:50:38 UTC as
`82bbc541865e6f5e79d065201dd19190e603dac4`, from exact reviewed head
`e6715eb2d9fc091c983610b62fd5e89ef5137202`. Final required CI
`42272970981` passes guards, unit coverage and the required PR gate.
The exact-head review is Approved at 18:43:52 UTC, with no active findings;
both review threads are resolved. Reply `4496237579` cites the actual
combined-source capture and canonical import. No CI gate is bypassed.

The bounded prerequisite qualification item is resolved at the source tier.
This Plan and Validation Plan are reread; the parent remains Active.
Containing Dev CI `42274356044` is admitted at the actual merge snapshot;
release observation remains pending. Earlier successful promotion runs are
not evidence that this newly merged source is released.

Guarded post-squash sync aborts rather than untangling the old multi-commit
history in place. With the source merge confirmed by the provider, a clean
owned branch and exact upstream merge commit verified, the retained merged-
only branch is advanced to that commit without discarding unmerged work.
No worktree is finalized. Caller migration, local Windows historical-seed
qualification, whole-flow and affected-host recovery remain unfinished.

### 2026-11-06 - U17 containing release verified without rollout

Merged-source Dev CI `42274356044` succeeds. Normal promotion
`42275291989` explicitly admits `82bbc541865e6f5e79d065201dd19190e603dac4`
against Main `4a6c8e3dc8a479c58eed7796b941ac1efea46590`. Admission,
frozen composition, real Windows Resume startup, source units, artifact
construction/attestation, exact-candidate reuse and source-release raw readback
all succeed. No retry or gate bypass is needed.

Promotion #11071 merges generated candidate
`565db90d18a042f1c3196105b62538a29228d9f4` at 19:14:41 UTC as Main
`3a9a766662351a9edde22ea6210caad45bec5a00`. The successful landed-record job
associates that Main commit with
`python-artifacts-82bbc541865e6f5e79d065201dd19190e603dac4`.
The published source archive and manifest exist with immutable SHA-256 assets;
the released registration reader's Git blob exactly matches the admitted
source. Worktrees is released as `1.24.510-dev1`. This is source/artifact
publication, not a local update, deployment or installed-consumer pass.

The bounded U17 source/release leg is delivered. The parent Plan and Validation
Plan remain Active: selected-case classification, whole-flow incoming readiness,
current selection and preserved-state proof are still unresolved. Caller
adoption must compose an independently runnable incoming reader when the old
runtime cannot answer the versioned request; the old provider must not veto
incoming construction. Existing construction, registration and routing owners
retain their mechanisms. Local Windows historical-seed qualification and every
separate installed-recovery gate remain; no worktree is finalized.

### 2026-11-06 - U18 incoming inspection construction selected

Claimed #11083 after deduplicating the remaining registration work. The
payload's ordinary command still selects an installed provider, so its presence
cannot establish that U17 is independently runnable before installation.
The source-owned inspection entry composes the existing constructor, optional
closed Source/profile/application inputs, retained owned child and unchanged
version-1 reader. It adds no registry parser, dependency builder, installation
receipt, writer or activation path. This closes part of the installer vision's
authoritative incoming replacement gap without changing that target.

The focused composition and existing-reader packet passes all 48 controls.
Basic Python lint and the installation-contract checker pass. Native cold
construction is not yet qualified: an initial fixture redundantly froze,
committed and copied the full graph, then the 180-second child expired. A
diagnostic run hit the original 300-second group limit before rendering a
report. Removing the unnecessary fixture preparation retained actual source
construction and produced usable phase evidence: source selection took 4.640
seconds, retention took 59.485 seconds, and local first-party builds were still
active at the unchanged 180-second child deadline. The final native call lasted
180.35 seconds; cleanup completed. None of those attempts is a pass or an
installed result, and no deadline or resource limit was increased.

The existing explicitly dispatched Linux/Windows portfolio now has a separate
incoming-construction step with exact-source pinning, actual native case
admission and no skipped/failed evidence. It does not expand required-unit CI
or add a matrix. Native qualification, complete U18 controls, source review,
required CI, merge/release and caller adoption remain open. Local cold-source
construction cost is an unresolved measured boundary, not a flake waiver.

Published checkpoint `41930e203d3da562d0475b39e5edcf60b81b6df9`
passes exact-source run `42290204186` on both hosted native platforms. Each
downloaded artifact contains all 49 expected controls with zero skipped,
failed or error cases: 39 existing-reader controls, nine incoming composition
controls and one real incoming constructor/reader case. The latter takes
21.660 seconds on Linux and 20.988 seconds on Windows, preserving the broken
predecessor and canonical disabled exposure. This is native source-consumer
evidence, not a local performance pass, installation or completed U18.

The next checkpoint expands the real entry to both exposure choices, genuinely
fresh native state, ambiguity and an actually unusable incoming UV executable.
Every case runs twice and preserves existing file bytes, native identities and
modification times, plus temporary-control cleanup. Its source-pinned native
result is still required; no first-checkpoint evidence is relabeled for that
expanded corpus. Item, child, group, plugin and hosted-job limits remain unchanged.

Expanded checkpoint `c0fa12bd08eaa9ef0c9b348a17c81568d7d5ad10`
runs as `42292146310`. Linux publishes all 53 passing controls, including
all five actual-entry scenarios with repeats. Their native item times are
39.241, 39.023, 37.658, 38.312 and 7.127 seconds. Windows publishes the
original 39 passing reader controls, but its incoming step returns nonzero
after roughly 304 seconds and no incoming JUnit report is retained. The
failed-step log has no named native outcome or literal timeout diagnostic.
That is missing qualification, not evidence that any Windows incoming case
passed. Recover the exact failing boundary and named progress before another
attempt; do not guess a timeout class or increase a resource/deadline limit.
The original first-checkpoint native result remains independently valid for
its own source and narrower corpus. No source PR is opened or merged yet.

The full Windows job log also lacks runner output: its hidden subprocess
inherited no explicit captured streams. Both registration qualification steps
now capture and print stdout and stderr before checking the exit result; the
incoming step retains flushed per-attempt progress through pytest's tee capture.
The existing failed-runner guards now assert both diagnostic streams survive.
All 15 focused portfolio/source guards and touched-file basic lint pass. No
test case, execution limit or partition changes. A new exact-source run must
identify the original failing boundary before choosing a runtime or scheduling
fix; elapsed time alone still does not establish a timeout class.

Diagnostic checkpoint `0729368271987b49041aac97c67d3901425f1408`
passes exact-source run `42297364665` on both native platforms. Downloaded
reports contain the complete 53 controls per platform, with no skipped, failed
or error cases. Linux's five actual first/repeat scenario items take 20.586,
20.286, 20.561, 20.561 and 3.350 seconds; Windows takes 43.008, 44.894,
48.819, 45.147 and 2.259 seconds. All original limits remain. No partition
change was needed. The earlier failed run's missing inner evidence does not
permit a retrospective timeout or flake classification; future failures now
retain their runner output and per-attempt progress.

This qualifies ordinary incoming-source construction and the existing reader,
not the optional complete prepared Source/application path end to end. That
path has focused composition/custody coverage and retains the existing
constructor's implementation; its actual prepared-source consumer remains a
caller-adoption validation obligation. Local cold construction cost and Windows
historical-seed performance also remain unresolved. Source review, required CI,
merge and release are still required; parent and installed-recovery gates stay
open.

### 2026-11-06 - U18 source merged; independent promotion fixture repair pending

Source #11136 receives an exact-head Approved review with no findings at
`e8a384a2dccf12f68cf1d675b2acee0b07dedad9`. Required CI `42299341557`,
trusted workflow/source-attribution and identifier guards pass. It merges at
21:58:46 UTC as `9677262d0904dccefab74ff5d2d60ae733a9a689`; no parent
closing reference is present and #10377 stays open. The Plan and Validation
Plan are reread after merge and remain Active. All owned source additions are
verified in the squash merge. Native squash-aware completion reconciles the
source worktree after ordinary pull-forward aborts on duplicated historical
commits; no new local work is discarded.

The containing release is not delivered. Promotion `42301853174` fails only
`Require real Windows Resume pane startup`: both prewarming variants raise
`UnsafeTestCommand` because the native fixture invokes PSMux control/cleanup
without an explicit private socket or runner-owned namespace. Its other
executed jobs pass; downstream publication/merge remains skipped. The
successor `42302804638` has the same failing native job when inspected.
This is distinct from #10960's delivered dependency repair, not a flake waiver.
The existing #10945/#10946 owner has explicitly claimed the fixture-scoping
follow-up at 21:55:12 UTC. Preserve that claim, the safety guard, real Resume
assertions and all finite bounds rather than create a competing patch or retry
promotion unchanged.

Next, observe the existing owner's repair and a containing qualified release,
then migrate callers through their normal review gate. Optional prepared-source
caller proof, local cold construction, historical-seed performance and all
whole-flow/installed acceptance remain open. No runtime update or deployment is
performed.

### 2026-11-06 - Fresh project adoption must remain separate from runtime preparation

Caller integration review identifies an additional front-door seam. The native
PowerShell installer explicitly re-roots marketplace self-staging to the user
home, then resolves a supplied project's repository from existing project
configuration, a Git CWD or the canonical registered repository. A fresh
unregistered project has none of those roots. Supplying its name therefore
reaches `register-project-entry` without `--repo-dir`; the owning hosting-policy
check refuses before a caller's later explicit registration can run.

Omitting the name is not an explicit runtime-only contract either: the normal
front door can infer a project from CWD and existing configuration. The proposed
bounded prerequisite is a source-owned runtime-only option that prevents
project inference and adoption while retaining the existing native runtime
preparation, ownership and readiness path. Default inference stays unchanged;
contradictory explicit project arguments must refuse. No reserved-name trick,
fabricated configuration or alternate constructor is accepted. Deduplicate and
coordinate the precise source stretch before implementation, then qualify the
real native front door before caller adoption. This is not yet a native
qualification result, source fix, release or installed recovery.

### 2026-11-06 - Explicit runtime-only provider expansion claimed

The precise source prerequisite is coordinated as #11178 under #10377.
The existing #5852 parent retains the broader install/adopt and configured
behavior separation; no open competing runtime-only PR was found. This
additive slice does not remove legacy default inference or close that parent.

The proposed provider implementation adds native shell `install`/`update`
runtime-only selection, rejects contradictory project arguments before
admission, and carries the selection through the existing Windows execution
driver and ordinary staging. The selected payload's adjacent installer
contract advertises the capability independently of package versions.
Separately deployed callers must require that support before effects; rollback
to an older provider is an explicit incompatibility, not trial-and-replay.

This closes a composition gap under the existing authoritative-runtime-
replacement intent, not a new lifecycle architecture. Runtime construction,
readiness, selection, admission, custody and central-only retirement boundaries
are unchanged. Shared migration and global integration remain ordinary owner
operations; project registration remains a later explicit native writer action.
Focused parser/driver and actual-shell controls are being run. Native fresh
installation, caller policy preservation, source review, CI, merge and release
remain open. No shared installer, runtime, configuration, service or rollout
effect is performed by this source preparation.

The initial focused local run reaches all six driver controls successfully.
Actual Windows shell selections and front-door refusals work; their first
assertions incorrectly treated PowerShell's own profile cache as installer
state, so those checks now assert only installer-owned footprints. The broader
compatibility run reaches an existing cold-driver fixture's original item
deadline, first during retained-input preparation and then during native
execution. Removing unrelated library test trees reduces fixture preparation,
without changing its runtime source closure or any deadline. A bounded
diagnostic confirms a real running request with no recorded error or output
before the item expires. This is unresolved qualification, not a flake waiver
or a successful runtime-only installation.

Five pure workflow controls pass, rejecting failed, skipped, missing and
wrong-source native evidence. Install-contract synchronization, touched-Python
lint and documentation consistency pass. The new manual Linux/Windows lane
retains exact source selection and required native cases. Publication remains
work-in-progress until the native result is understood and qualified; caller
migration and parent acceptance remain open.

Separately, the previously blocked #11136 provider is now present in the
qualified source release #11182. Promotion `42315848596` passes real Resume,
artifact attestation, publication/readback and non-administrative exact
candidate merge. Its recorded admitted Dev is
`d7a258cee28247008412c44258fbda10d4bdaf6b`, not the workflow run's Main-base
head. The generated Main merge is
`b4d2d056e282960369201ed68359a9ad76db10f6`, with Worktrees
`1.24.512-dev1`. The source merge is an ancestor of the actual admitted Dev;
the helper, native test, composition tests, CLI documentation, workflow and
acceptance bodies match their source merge and published Main. Changes to
TESTING and the workflow test file are unrelated additions; all admitted Dev
bodies match Main. This is released source, not local rollout, caller adoption
or completion of #10377.

The first exact-head hosted qualification, `42321992914` at
`b3588819fb6367b441b2654ad689e26b9aba5570`, fails on both platforms before
test execution. The source-pin probe omitted the owned-process helper's two
required output budgets; the report uploader then had no declared report path.
The revised probe supplies finite budgets and the uploader runs only after a
report directory has been declared. The pure regression stub now requires the
same keyword-only budgets, so it cannot hide this interface mistake. Its five
admission controls and touched-file lint pass again. The failed run supplies no
native acceptance credit; qualification must use the revised published head.

### 2026-11-07 - Runtime-only provider native qualification

Exact source `c49a2c51dcd25cffcf1b6094b49ac14c857b28ca` passes manual
qualification `42322817268` on both disposable hosted platforms. The downloaded
JUnit bodies confirm fourteen Linux passes and seventeen Windows passes, with
no errors or failures. Linux exercises Bash and PowerShell Core selection,
unchanged default inference, repeat runtime-only selection and early refusal;
Windows exercises Windows PowerShell and PowerShell Core. Unsupported shells
are explicit platform skips.

All three actual Windows cold-driver cases pass: ordinary entry, runtime-only
entry and caller abandonment, in 5.041, 3.627 and 3.441 seconds respectively.
They retain the existing request owner, native reentry, deadline propagation,
output capture and cleanup assertions. Their Linux skips reflect that fixture's
Windows-only contract, not native Linux installation coverage. The earlier local
item timeouts remain unresolved local results; no deadline was raised and no
local success is inferred from this separate hosted result.

This qualifies the bounded provider seam, not a complete native runtime install
or fresh-project adoption. The shell-selection controls execute extracted
production selection code; the cold-driver fixture deliberately substitutes a
fixed no-install native participant. Normal source review, current-head required
CI, merge and containing release remain due. Independently deployed callers must
check the exact selected payload's advertised capability before migration.
Caller fresh/prior-state policy preservation and whole-flow recovery remain
under #11178, #5852 and the active #10377 acceptance matrix. No shared runtime,
service, credentials, registration or installation is changed.

### 2026-11-14 UTC - Proposed complete #7743 existing-entry receiving

P7743-U1, the local U1-7743 matrix and actual global V7743-U1 retain the
complete finite fresh-entry/refinement union under its existing recovery,
entry, stdio and settlement owners. All new acceptance remains unchecked.
The historical unsettled native experiment remains held without becoming a
general incoming-replacement veto. No source-owner release, retry, execution,
implementation, installed acceptance or resource transfer is claimed.
