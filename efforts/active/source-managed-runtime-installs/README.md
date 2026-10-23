# Source-Managed Runtime Installs

- **Slug:** `source-managed-runtime-installs`
- **Repo:** copilot-extensions
- **Branch(es):** independent, serial per-slice PRs against `dev`
- **Created:** 2026-10-16
- **Status:** Active
- **Vision:** [Plugin Service Model](../../../visions/plugin-services/README.md#managed-runtime-deployment), [Runtime Assembly](../../../visions/plugin-services/runtime-assembly/README.md#selected-development-source-assembly), [Installer](../../../visions/installer/README.md#independent-runtime-source-selection), [Clean-room Validation](../../../visions/clean-room-validation/README.md#independent-source-installation)
- **Umbrella issue:** #7028
- **Sub-issues:** #3905 (existing source-materialization gap), #7029 (source selection and immutable generations), #7030 (deployment boundary and adapters), #7758 (durable installation execution driver)
- **Future objective:** #7031 (exclusive local plugin-content sourcing; outside this runtime-only campaign)

## Guiding Intent

Separate Copilot plugin delivery from service-runtime deployment without
disabling ordinary hook behavior. An opted-in installation can deploy clean,
merged development source directly, retain a stable release or exact pin, and
iterate through immutable generations without waiting for marketplace payload
updates or rewriting a live environment.

Prove the source install on a disposable container before attempting a native
Windows runtime-only pilot and completing the adopter's installed service/tool
transition. Keep the dedicated source healthy and current through the existing
maintenance owner and canonical install flow. Ordinary plugin self-provisioning
remains the default; managed deployment is a local choice, not adoption of
another product or a change to service-supervision ownership.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Implementation coordinator | Owns the plan, integration, public PRs and completion gate | Managed source worktree |
| Manager maintenance implementer | Owns the bounded background-maintenance enrollment, lifecycle and ownership-handover slice | Managed source worktree; public coordination on #7028 |
| Installation execution implementer | Owns durable suite-update requests and exclusive transaction execution, separately from recurring enrollment | Managed source worktree; public coordination on #7758 |
| Linux clean-room runner | Reproduces and verifies actual source installation and activation | Existing Docker clean-room rig; WSL is a supported host |
| Windows validation runner | Verifies native installer, resolver and non-deployment hook behavior | Isolated Windows fixtures, then an explicitly authorized runtime-only pilot |
| Independent reviewer | Reviews each coherent proposal or implementation change | Repository PR review |

## Coordination

- **Topology:** serial, coherent PRs against `dev`; this plan clears review before implementation.
- **Host (owns PRs):** Implementation coordinator.
- **Delegates:** bounded evidence, implementation or validation scopes only; no shared mutable git ownership.
- **Handoff:** the next owner reads this README and the current slice evidence before continuing. A merged PR resolves only its actual Plan and Validation Plan items.
- **Existing owners:** preserve #3654 (vendor pointers), #5472 (immutability), #4876 (artifact admission), #5851 (source isolation), and #6812/#6820 (update outcomes/sequencing). Reuse their mechanisms; do not fork their campaigns.

## Context

The current Manager has self-update source configuration, but that is not a
suite-wide runtime-source policy. Plugin bootstrap paths still publish runtime
content and installed command redirects from the enabled payload.

Promotion composes versions and expands development vendor pointers into a
self-contained `main` tree. Raw-dev non-editable installs have an existing
tracked gap (#3905). Canonical runtime helpers already provide immutable
completion, activation, retention and claim-owned editable development
generations (legacy interactive behavior). The
[Immutable Dev Slot effort](../immutable-dev-slot/README.md) replaces that
editable target with frozen, non-editable source-hashed generations under
#5472. This campaign remains the canonical deploy-from-Dev home and extends
the same building blocks, not a second installer or mutable environment.

The [source-owned release vision](../../../visions/release-pipeline/README.md)
keeps the verified development commit as the release identity. Generated main
commits, materialized payload digests, package versions, profiles and dependency
locks describe different facts; no new competing artifact authority is needed.

## Request

**Immutable development alignment (2026-10-22), operator verbatim:**

> So, mutable dev slot should probably be superseded by "immutable dev slot", and the "deploy from dev branch" effort. Basically, always deploy using an immutable venv, tagged with the hash of the source. Just set the new dev slot as current and handle cutover normally by giving the incoming version a start command and letting the old version shut off. We should endeavor to support baton-passing both ways; a version knows it's "old" simply because it's "not current", not via any ordering relationship between the active and owner versions.

> Great. Update relevant visions and efforts to align; close out and abandon superseded efforts and off-course work.

**Accepted alignment:** retain this effort's clean merged Dev source, Manager,
maintenance, driver and installed-adoption owners/gates. All deployments use
immutable venvs identified by complete source and admitted build inputs.
Current selection starts the incoming owner through ordinary best-effort handoff;
full identity inequality, not version ordering, identifies the displaced owner.
Reverse baton-passing prefers compatible health-gated drain, but permits bounded
identity-checked old-owner shutdown and cold recovery when handoff contracts
are incompatible or fail. This documentation revision neither
restarts completed Phase 2 work nor claims remaining cutover/adoption proven.

**Best-effort follow-on (2026-10-22), summary:** the operator permits outgoing
daemons to quick-terminate their owned scope, and the selected newcomer to
force-terminate an identity-verified predecessor when handoff cannot complete.
Clients tolerate transient network-like outages/stream cut-offs; child daemons
tolerate power-loss-like interruption and recover retriable durable work.
A breaking development contract must not veto replacement. The
[canonical verbatim capture](../immutable-dev-slot/README.md#request) preserves
the complete operator input. Existing source/maintenance/driver and native
acceptance owners remain; no live shutdown or rollout is authorized here.

**Settled request summary:** add a local gate only around service payload
publication and installed runtime redirect updates; retain all other hook
behavior and ordinary Copilot marketplace delivery. Add an independent managed
runtime source supporting main, stable/exact older pins and merged dev.
Materialize dev dependencies and install immutable, hash-qualified,
non-editable generations from clean merged source, never worktrees or dirty
checkouts. Keep adapters thin and version-skew compatible. Begin with a
clean-room container on WSL, then prove a native Windows runtime-only pilot.

The operator confirmed the slug and explicitly selected **runtime-only** for
the Windows pilot. Local plugin-content sourcing is long-term intent tracked
separately in #7031, with exclusive source replacement rather than shadowing.

**Execution follow-through summary:** the requested outcome covers all installed
services and tools, including their management tools, rather than ending at a
successful canary. A regular flow must keep the dedicated merged-dev source
healthy and updated and rerun canonical installation at a bounded interval,
using the selected maintenance owner. The
[maintenance slice](maintenance.md) proposes the portable integration and
validation; it must clear review before implementation.

**Maintenance ownership revision (2026-10-20):** automatic suite installation
remains off by default, but an explicitly run trusted consuming-harness setup
may opt in for the user or local installation. Manager owns that background
capability. Windows enrollment uses current-user Autorun for a headless
maintenance process rather than requiring Task Scheduler registration. A
harness's approved setup can enable maintenance after installing Manager.
An external update controller remains an exclusive alternative. Agent Machines
retains general machine/dependency convergence rather than another concurrent
suite-update loop. The operator selected **implement-and-adopt**, following
review of this ownership amendment; source-backed installation prerequisites
and adopter-specific change gates remain intact.

The [inception record](inception-transcript.md) retains the operator's words,
with private host identifiers redacted. Implementation choices below marked
**agent-recommended** are not additional operator requirements.

**First-installation decision (2026-10-22):** the operator selected
`driver-foundation`: "Cover first installation too: establish a standalone
driver foundation before plugin provisioning". Python-free standalone genesis
is not exempted from the execution-owner objective. The foundation must remain
independent of Manager and other plugins. The detailed packaging, bootstrap
admission and adapter sequence below are **agent-recommended implementation
work**, not additional operator-selected mechanisms.

**Execution-owner clarification (2026-10-22):** the operator requires one
background installation driver, not merely independent per-installer locks.
Ordinary callers submit durable requests; waiting observes the operation and
does not become its installer. Direct execution is reserved for supervised
debugging under the same exclusive authority. The operator directed:
"Do what you need to do to get unblocked that doesn't involve waiting on
someone else's PR". The [execution-driver slice](execution-driver.md) therefore
proceeds against the existing canonical updater, independently of open staging,
watchdog and recurring-maintenance changes, without superseding their owners.

## Plan

### Phase 0 - Review the intent and plan
- [x] Land the vision amendments and this canonical proposal through repository review.
- [x] Confirm issue/slice ownership and preserve the existing dependency campaigns.

### Phase 1 - Reproduce direct-dev installation in the clean room
- [x] Extend the existing Tier-P rig with a credential-free, source-install scenario using a recorded clean merged dev commit.
- [x] Reproduce #3905 through an actual non-editable installer path and record the first classified failure.
- [x] Establish observable source identity, runtime generation, installed command and source-checkout isolation assertions.

### Phase 2 - Assemble immutable merged-source generations
- [x] Reuse canonical source materialization for the complete declared vendor closure without modifying the input checkout.
- [x] Add source-qualified non-editable generation identity while preserving package versions, profiles, dependency policy and completion evidence.
- [x] Make installer, runtime resolver and installed command redirects agree on the exact selected generation on Linux and Windows.
- [x] Prove identical reuse, distinct same-package-version commits, source removal, interrupted build and failed-candidate preservation.

### Phase 3 - Separate plugin-triggered service deployment
The [deployment-policy slice](deployment-policy.md) maps the existing trigger,
publication and command-ownership boundaries; it does not disable whole hooks.

- [ ] Add a local managed-deployment policy at the shared publication boundary, retaining standalone defaults and installation scope.
- [x] Land and qualify the shared installation-owned decision on Python,
  no-Python POSIX and PowerShell 5.1 before wiring every publication caller.
- [x] Gate each plugin's reconciliation plan and in-process execution without suppressing marketplace refresh or independent targets.
- [x] Qualify shared legacy lifecycle publication admission and explicit management purpose on the existing source-qualified lease.
- [ ] Cover session bootstrap and indirect first-use publication without disabling other hook or extension behavior.
- [x] Qualify Logger's direct native installer admission before staging, preserving standalone stamping and canonical management custody.
- [x] Gate the common CodeSpaces/Logger/Pull Requests/Vault bootstrap family's first stamping and drift reconciliation before lock/reaping/dispatch, retaining independent hook behavior and healthy dispatch.
- [ ] Gate generated first-use wrappers before boot trace, provision status and orchestration-lock writes, preserving admitted provisioning and healthy runtime invocation.
- [x] Qualify Logger's generated primary, payload-local and auxiliary first-use commands, selected-cell policy, deferred tracing and explicit helper-skew readiness before closing the broader wrapper item.
- [x] Qualify the shared Pull Requests/SSH/Vault generated-wrapper owner and Vault payload gates with actual adopter no-write refusal, standalone provisioning, healthy dispatch and failure-safe snapshot selection.
- [x] Qualify CodeSpaces' bespoke generated-wrapper and payload gates against the selected installation without treating context validation alone as deployment admission.
- [x] Qualify Worktrees' native bootstrap and payload-local early first-use
  publication gates, retaining standalone reconciliation and healthy dispatch.
- [x] Qualify Worktrees' cell-installer pre-staging and retained-backend
  publication gates using existing governance and lock custody.
- [x] Authenticate Worktrees' explicit managed-cell installation through the existing
  genesis/install custody across native staging, candidate handover and callbacks.
- [x] Gate Worktrees' pre-launch and staged runtime-update planning without suppressing marketplace refreshes, healthy reuse or unrelated repository bootstrap actions.
- [x] Revalidate cached launcher plans immediately before native execution, including older standalone compatibility and policy changes after planning.
- [ ] Complete the remaining adopter hook/extension publication map and qualify their retained non-deployment behavior and explicit readiness outcomes.
- [ ] **Agent-recommended decomposition:** qualify remaining Bridge/Dispatch/Containers/MCP/Machines/Budget Guidance bootstrap first-stamp and drift paths with actual no-write policy refusal, standalone positives and retained independent hooks.
- [ ] **Agent-recommended decomposition:** qualify remaining payload/global first-use command routes separately from bootstrap, distinguishing selected-context validity from publication permission and preserving healthy dispatch and explicit helper-skew outcomes.
- [ ] Keep adapters thin and enforce drift-compatible service interaction without implicit runtime replacement.

### Phase 4 - Integrate Manager-owned runtime source selection
- [ ] **Agent-recommended alignment qualification:** prove forward/reverse current-generation replacement, including same-package/different-source selection, with preferred compatible handoff and bounded identity-checked cold takeover when contracts break or drain fails; use identity inequality, not version ordering.
- [ ] Maintain a separate installation source with main, stable/exact pin and opt-in merged-dev selection resolved once per run.
- [ ] Keep service-source selection independent of Manager self-update and Copilot plugin-content selection.
- [ ] Route the selected source through the same assembly, readiness, activation and observable update-outcome path.
- [ ] Preserve exact selection across retries and source failures; never fall back to an ambient checkout or marketplace payload.
- [ ] Qualify Manager's own clean-source, immutable non-editable installation and recovery independently of the selected service-runtime source.

### Phase 4a - Manager-owned opt-in background maintenance
The [maintenance slice](maintenance.md) owns the detailed proposal and evidence
matrix; no schedule or cadence is enabled by this plan.

- [x] Land the portable source-backed maintenance intent and implementation plan through review before adding that capability.
- [ ] Review the ownership revision, default-off policy, trusted-harness setup consent and current-user Windows Autorun lifecycle before implementation.
- [ ] Implement Manager-owned enrollment, background lifetime, bounded recurring invocation and status through the existing canonical update path, without requiring Agent Machines or an open Picker.
- [ ] Upgrade the resident maintenance worker safely when Manager changes, preserving durable update requests and exclusive scheduling through best-effort handoff or bounded cold recovery without waiting for another logon.
- [ ] Preserve explicit local choice and support an exclusive external-controller owner; hand over legacy suite-update responsibility without disabling general machine/dependency convergence.
- [ ] Add the portable setup enrollment seam and qualify an explicitly consenting adopter after Manager installation; keep private adopter implementation and evidence in its own repository.
- [ ] Prove default-off, trusted-setup enrollment, no-elevation Windows startup, durable headless operation, failure visibility and unchanged/advancing cycles on native Windows and Linux.
- [ ] After the Phase 3 and Phase 4 prerequisites, extend that same maintenance owner for bounded source health, selected-ref synchronization and canonical source-backed installation; do not add another updater.
- [ ] Preserve independent Manager/service/plugin-content selections, configured roles and exact per-run source identity across scheduled operation and retries.
- [ ] Prove unchanged-source reuse, a merged-source advance and explicit dirty/unavailable/overlapping/failed-candidate outcomes on Linux and Windows.

### Phase 4b - Durable installation execution driver
The [execution-driver slice](execution-driver.md), tracked by #7758, separates
transaction execution from the existing recurring-maintenance assignment.

- [x] Review the execution-owner clarification and bounded implementation plan.
- [x] Implement durable requests, compatible pending-request coalescing and attributable read-only status/results.
- [ ] Route ordinary supported suite-update entry points through one caller-independent execution owner over the complete canonical transaction.
- [x] Migrate the public Manager/engine update commands, staged acquisition and hook `reconcile-plugins --apply` without replacing their canonical executors.
- [ ] Migrate native launchers' direct validated plan-action execution and remaining suite-adopted first-use publishers while preserving standalone defaults.
- [x] Land and qualify the native launcher phase adapters independently of remaining first-use publishers.
- [x] Review the standalone foundation amendment before implementing cold first-use execution.
- [x] Qualify the Python-free Windows process and operation-custody prerequisites independently of foundation construction.
- [x] Qualify native construction progress, queue-fence interoperability and explicit restart-evidence recovery before enabling a cold bootstrap producer.
- [x] Qualify the fixed standalone construction participant, immutable reuse, failed-candidate preservation and installed source independence on hosted Windows and Linux before native genesis integration.
- [x] Qualify the owner-bound native Windows bridge to the existing participant protocol, retaining owner-thread mutation, cross-language identity, revocation and malformed/stalled/excess-peer isolation.
- [ ] Establish the standalone execution foundation before plugin provisioning without requiring an existing Python runtime, Manager or another plugin.
- [ ] Migrate first-use publishers onto that foundation, retaining the canonical installer, publication admission, healthy dispatch and precise skew/recovery outcomes.
- [ ] Qualify foundation genesis, caller loss, concurrent cold callers, failed construction, upgrade and subsequent first-use requests on native supported platforms.
- [x] Preserve explicit supervised-debug execution under the same kernel-backed authority, standalone compatibility and existing installer custody.
- [ ] Qualify real-process concurrency, caller loss, interrupted-worker recovery, exact request identity and native headless operation without waiting on #6990 or #6928.
- [x] Qualify Windows caller-Job loss, contained controls, two-cycle headless operation, restart-evidence admission and actual queued CLI/JSON consumer contracts.
- [ ] Complete native Linux qualification when a supported isolated execution lane is available; do not substitute Windows or synthetic boot evidence.
- [x] Document the actual migrated entry points and remaining separately owned integrations; no partial slice is blanket installation-driver completion.

### Phase 5 - Prove assembled use and native Windows readiness
- [ ] Run the assembled runtime path in a fresh WSL-hosted clean-room container from a clean merged dev commit.
- [x] Qualify every declared installer adopter against that exact merged commit with frozen scenario inputs, successful runner exit and source-independent installed command evidence; retain each adopter's separate report.
- [ ] Exercise real commands and service interaction after source-checkout removal, including marketplace/runtime skew and non-deployment hooks.
- [ ] Prove native Windows source installation, generation selection and hook-deployment separation before a consented runtime-only pilot.
- [ ] Repair the native snapshot qualification fixtures and cell-slot legacy-staging boundary tracked in #7612; qualify the complete matrix before claiming native readiness.
- [ ] **Agent-recommended:** close the native PowerShell final-path long-path qualification gap in #7010 across supported hosts; shorter fixture roots do not qualify that separate boundary.
- [ ] **Agent-recommended:** resolve the network-backed PowerShell retained-source first-use test's bounded qualification gap in #5379 before claiming complete native SSH provisioning evidence.
- [ ] Update owner documentation and record actual serving-generation evidence, not only successful copies or install markers.
- [ ] Prove every installed service/tool in the adopter's declared inventory, including Manager, rather than treating a canary or all-adopter installer tests as the native transition.

### Completion and scope control
- [ ] Resolve all Plan and Validation Plan items; record or transfer remaining discrete work to a named tracked objective before marking Done.
- [x] Deferred to `#7031`: implement exclusive local-checkout sourcing of Copilot plugin content after the runtime-only campaign.
- [x] Deferred to `#7250`: **Agent-recommended follow-up, operator-approved tracking:** preserve legacy Python-free redirect sets through a separate native publication mechanism; do not add a bootstrap interpreter prerequisite.

## Validation Plan

- [ ] **Bidirectional selection:** forward, lower-version and same-package/different-source replacements use identity inequality; compatible handoff health-gates before retirement, while incompatible/refused/hung handoff allows bounded owned-scope shutdown or identity-checked force termination before cold-start readiness.
- [ ] **Cold recovery and retries:** breaking all predecessor handoff contracts does not veto selected Dev deployment. Qualify durable recovery after abrupt child death, consumer reconnect after stream/reply loss, safe retry/deduplication or unknown-outcome reconciliation, exclusive ownership and explicit failed-recovery reporting.
- [ ] **Source admission:** reject dirty, worktree-backed, unmerged or substituted source; preserve the exact resolved commit throughout a run.
- [ ] **Materialization:** actual installer imports the complete vendor closure from a non-editable install with no source-checkout dependency.
- [ ] **Identity:** identical inputs reuse unchanged completion bytes; different commits or assembly/dependency/profile inputs cannot overwrite a completed slot.
- [ ] **Selection:** installed binstubs and native resolvers execute the exact completed selected generation, including hash-qualified dev sources.
- [ ] **Failure:** incomplete/failed candidates remain unselected and preserve the previous healthy runtime and redirects.
- [ ] **Policy:** real hook and first-use paths do not publish runtime copies or redirects in managed mode; their non-deployment behavior still runs.
- [ ] **Skew:** newer-plugin/older-service and older-plugin/newer-service interactions work within declared capabilities or return precise readiness outcomes.
- [ ] **Source modes:** main tracking, stable/exact pins and dev tracking converge through one source-assembly path without moving midway through a run.
- [ ] **Maintenance:** the existing owner runs bounded, observable source health/sync/install cycles at the selected cadence; unchanged input is reused and source/candidate/overlap failures preserve healthy runtimes and edits.
- [ ] **Background ownership:** default-off enrollment accepts explicit user or trusted-setup consent, current-user Windows Autorun requires no UAC registration, the worker survives its setup caller without visible consoles, and only the selected owner performs suite updates.
- [ ] **Worker upgrade:** native replacement preserves durable in-flight request/operation identity across graceful or cold recovery, prevents concurrent scheduling or duplicate unsafe effects, and reports actual readiness or a recovery outcome when the successor fails.
- [ ] **Execution driver:** concurrent ordinary callers observe one durable operation, caller exit preserves it, distinct explicit selections stay distinct, interrupted execution remains explicit, and supervised debugging cannot overlap the same transaction authority.
- [ ] **Setup adoption:** approved consuming-harness setup finishes with verified enrollment or a precise activation/readiness blocker; unrelated repository discovery cannot enroll maintenance and external-controller ownership cannot overlap Manager.
- [ ] **Management continuity:** Manager and the selected service runtimes can use distinct clean merged-source selections; the management tools remain usable for update and supported recovery.
- [x] **Clean-room:** a structured Tier-P report proves fresh Linux installation and real service use; report the exact source and serving identities.
- [ ] **Windows:** native installer/resolver fixtures pass; any installed pilot has explicit change consent and attributable running-state evidence.
- [ ] **Agent-recommended - robustness:** test concurrent preparation/publication, unavailable-source retry and state-aware rollback through existing lifecycle primitives.
- [ ] **Agent-recommended - rollout evidence:** retain a concise qualification/soak record before marking a development snapshot stable; do not infer stability from a clean install alone.
- [ ] **Contribution gates:** targeted bounded tests, required install-contract checks, appropriate clean-room evidence and reviewed merged PRs cover the final implementation.

## Proposal

Keep deployment policy, source selection and serving-generation selection
separate. Reuse the canonical materializer, runtime construction/retention and
admission paths. Runtime-source selection does not modify marketplace
definitions, introduce a second supervisor, or claim release promotion.

**Agent-recommended:** keep the Manager's own recovery/update source on a
known-good selection while canarying service runtimes; treat this as an
independent configuration choice rather than forcing the Manager and suite
onto one branch.

## Journal

### 2026-10-22 - Best-effort amendment reviewed and published

#8090 merged as `4b78ba2d5dd2f49d695fe312af5f8d34d3c0385d` with approval and
required CI passing. The documentation amendment and final #7028 coordination
are complete; the new cold-recovery/retry, native, maintenance/driver and
installed-adoption obligations above remain open with their existing owners.
No runtime or installed activation changed.

### 2026-10-22 - Best-effort service replacement alignment

Aligned this effort with the operator follow-on captured verbatim by Immutable
Dev Slot. Compatible handoff remains preferred; incompatible or exhausted
handoff permits bounded identity-checked retirement and cold recovery, including
the maintenance worker's durable update requests. Added explicit outer-client
retry and abrupt child-shutdown qualification. This does not waive source
admission, state-safe recovery, exclusive execution or installed/native evidence,
and does not claim the new fallback implemented.

### 2026-10-22 - Align immutable development and retained Dev deployment

Re-read the two newly captured operator statements against their literal input.
The canonical direct merged Dev home remains this effort; immutable interactive
development and retained #5472 admission/adopter work move to Immutable Dev
Slot. Mutable/editable deployment is abandoned as a target, not preserved as a
special exception. Added explicit bidirectional current-identity cutover
qualification without resetting delivered Phase 2 or taking over maintenance,
driver, native pilot or installed-adoption owners. No runtime was changed.

### 2026-10-22 - Execution core and public consumer implementation
- Implemented the shared durable request journal, kernel-owned finite worker, checked nested handoff, canonical outcome/log observation and explicit restart-confirmed interruption settlement under #7758.
- Migrated public update, staging and hook-apply entry points; planning/indicator/native-admission reads remain non-installing and the JSON consumers retain their original result shapes. Transaction capability transport is consumed at entry and forwarded only at deliberate update seams, not inherited by service children.
- Unified Worktrees' process dependency with the driver's canonical reference after UV correctly rejected two URLs for the same distribution. The ten production modules were byte-identical; the removed vendored copy contained older documentation/tests, not a competing process implementation.
- Windows bounded runs passed: 59 driver cases including the explicit native lifetime lane, 120 Manager update/auth/consumer cases, 332 engine update/stage/reconcile cases with two existing platform-dependent skips, and 46 test-selection tooling cases. Scoped lint, installer contracts, owner-doc consistency, dependency-copy consistency and changed module-size checks passed.
- Native lifetime qualification closes the submitting kill-on-close Job, proves production worker survival and contained-control retirement, observes two cycles with no window/focus transitions, and settles the canonical result. Boot-identity reads and synthetic prior-boot reconciliation were exercised; the shared host was not rebooted.
- Linux/clean-room execution is not claimed: the configured Docker endpoint and both registered Desktop engines were unavailable, and the only local WSL distribution was Docker's infrastructure distribution. No shared daemon was repaired or reconfigured to manufacture a lane.
- Native launch plan-action adapters, remaining first-use convergence and Linux acceptance remain open. This implementation neither enables recurring maintenance nor completes the parent source-managed installation campaign.

### 2026-10-22 - Independent installation execution slice reviewed
- PR #7760 merged after current-head Copilot approval with no findings and successful required CI.
- The execution implementer proceeds under #7758 against the canonical updater on `dev`, without waiting on or replacing the staging/watchdog PRs.
- Phase 4b is implementation work; the broader effort, recurring-maintenance enrollment and source-managed installation gates remain open.

### 2026-10-16 - Proposal and scope
- Captured the agreed runtime/plugin boundary and confirmed the effort slug.
- Opened #7028, #7029 and #7030; reused #3905 for raw-dev materialization.
- Tracked future exclusive plugin sourcing separately in #7031.
- Confirmed Docker is reachable on a WSL host; no native runtime policy, marketplace settings or running services were changed.
- Implementation remains gated on the proposal PR merging.

### 2026-10-16 - Reviewed proposal and clean-room slice
- Proposal PR #7053 received an exact-head Copilot approval with no findings and successful checks, then merged as `41c1e787ae`.
- Pulled the owning worktree forward and selected Phase 1. The effort remains open; proposal merge is not implementation completion.
- Began a credential-free Tier-P source-install scenario using the actual Agent Bridge installer from an exact merged dev commit.

### 2026-10-16 - First source-install witness
- The fresh container installed merged dev commit `41c1e787ae` through the real Agent Bridge installer without Copilot credentials.
- The first assertion failed because the selected generation was only `0.4.1-dev3`, without a source identity.
- Inspection found shared-library imports resolving into the checkout and generated metadata/cache files written there. Moving the source checkout made the installed import fail with `ModuleNotFoundError: credential_relay`.
- This narrows the current manifestation of #3905: editable canonical references can hide the dependency until the checkout is unavailable. The fix must materialize a non-editable closure outside the source, not merely make the initial installer exit successfully.
- Began canonical source preparation and a source-qualified runtime identity that preserves the original package version; native Windows unit/initializer coverage currently passes, while full source-install acceptance remains open.

### 2026-10-17 - First implementation wave merged
- PR #7080 merged as `9fe128630122a5fbddfcbba3b2a7e7b4a1186ac3` after the owner-authored Copilot verdict gate and successful CI. The worktree remains active for the complete effort.
- Landed canonical merged-source preparation and integrity-qualified payload reuse, synchronized source helpers, and Agent Bridge installation/operational identity on both native installer paths.
- Regression coverage includes source changes during lock/build/completion boundaries, preservation of the previous selection, stopped-predecessor rollback, Windows snapshot-to-first-use integrity, normalized development versions, generated-cache stability and explicit refusal diagnostics.
- The first post-merge scenario execution reported six passing phases, including source removal and serving identity. It is not the formal qualification witness: its mounted scenario source briefly changed during concurrent post-merge branch reconciliation. The branch was restored without dropping local work.
- Next slice: repeat the exact merged commit in a fresh named container with stationary scenario inputs, record its structured evidence, then continue deployment-policy separation and independent runtime-source selection. No native host rollout or marketplace change has occurred.

### 2026-10-17 - Stationary-input source qualification
- A new credential-free container ran `merged-dev-runtime` against exact merged commit `9fe128630122a5fbddfcbba3b2a7e7b4a1186ac3`: six phases passed, zero failed, with no classified jams.
- Before/after hashes of every mounted scenario and shared-library input were identical. This fresh run, not the earlier overlapping reconciliation attempt, is the qualification witness.
- The selected and actually serving generation both identify `0.4.1-dev3+source.9fe128630122a5fbddfcbba3b2a7e7b4a1186ac3.70d68874330d355a`. Package version remains `0.4.1-dev3`.
- The real non-editable installer left its source checkout unchanged. Installed commands and vendor imports remained usable after that checkout was made unavailable; authenticated service health, routed PID/start-time identity and the running marker agreed on the selected generation.
- Agent Bridge is the first complete installer adopter. Other consumers currently carry the canonical source helper only; their native installer/resolver adoption, managed publication policy, skew qualification and Manager-owned source selection remain open. The native Windows pilot has not begun.

### 2026-10-17 - Remaining native adopter partitions
- Partitioned the next implementation slice into disjoint native CLI installer groups, keeping shared source identity and integration coordinator-owned. The bounded workers may edit only their assigned plugin trees; git publication and combined verification remain single-owner.
- Containers retains its durable `base` and `provider-exec` profiles as an independent generation dimension rather than replacing them with the source identity. Native stamp fixtures cover complete source snapshots and deferred-source independence; source/profile tests cover exact reuse, distinct commits and refusal after lease/build/activation-boundary changes.
- Cell snapshot provenance is installation evidence, not assembled runtime content. The canonical hash excludes only its exact root sidecar after ordinary-file validation; preparation refuses preexisting source-owned deployment metadata. Temporary publication ownership markers remain covered by source validation rather than becoming a broad hash exclusion.
- This is ongoing Phase 2 work, not a deployment-policy or Manager integration claim. Other adopter and full assembled-use gates remain open.

### 2026-10-17 - CLI adopter wave and complete resolver bundles
- Implemented native source identity, receipt-preserving snapshots and immutable construction/publication boundaries for Codespaces, Containers, MCP, SSH, Machines, Pull Requests and Budget Guidance. Existing package-only and claimed editable-development behavior remains distinct.
- Exact-selector recovery now refuses unordered source hashes and implicit fallback to an older plain slot. Every independently deployed resolver bundle, standalone wheel/sdist and retained Fleet helper loader includes the new source companion; regression cases cover actual copied layouts and RECORD-bound companion replacement.
- Focused native Windows and Linux validation covers source/profile identities, interrupted/refused candidates, identical reuse, snapshot independence, real writer contention, standalone artifact reconstruction and installed resolver execution. The clean-room scenario now selects these CLI adopters; their fresh merged-source qualification remains the next gate, not a claim made by this implementation.
- Fixed tightly coupled fixture assumptions instead of changing production timeout or dependency-policy semantics: extracted functions load their real source helper, Windows editable retention may use its source-volume sibling, and the POSIX lock fixture avoids inherited Windows command-search paths.
- Worktrees, Dispatch, Index, Logger and Vault still need native source-install adoption. Deployment-only hook separation, version-skew compatibility, independent Manager source selection and the specifically consented Windows pilot remain open.

### 2026-10-17 - Native source-bootstrap review corrections
- Reused the existing windowless native capture implementation as a canonical vendored companion instead of adding independent capture variants to each source reader. Source validation preserves JSON stdout, concrete stderr and exit status; launcher discovery is available before source initialization.
- Real native tests cover launcher-only source selection and full Containers deferred stamping, including snapshot receipts and generated commands. Separate two-cycle GUI-parent observations cover source validation under both Windows PowerShell 5.1 and PowerShell 7 without attributable visible consoles or foreground changes.
- A real launcher probe exposed PowerShell 5.1's unnecessary quoting of `-3`; the canonical encoder now quotes only arguments that need it while retaining empty, embedded-quote and trailing-backslash behavior. Large simultaneous stdout/stderr and failed-exit cases retain their exact evidence.
- Corrected the fresh Codespaces clean-room action to `install`, which publishes its command; the later immutable-reuse invocation remains `provision`. The new adopter qualification remains gated on the exact merged source rather than these fixtures.
- Pinned the complete Containers source receipt across the outer install lock and the shell-to-profile/stamp handoff, not only its generation string. Actual native lock-entry mutations retain a valid source-tree-only receipt and unchanged generation, yet fail before snapshots or redirects; profile and stamp handoffs also refuse before any runtime writes.
- Replaced MCP/Codespaces source-stamp directory moves with one canonical snapshot publisher on both native paths. Its per-generation snapshot lease serializes the absent-target check, complete candidate copy, winner verification and rename; concurrent callers cannot nest a losing stage inside an already-published snapshot.
- Real competing-process cases retain the actual kernel lease and compare the winning receipt, full file inventory, immutable reuse evidence and redirects. Shared API/CLI cases cover conflicting winners, changed source after lease acquisition, incomplete copies and preservation of the initial full identity. Native Windows and Linux suites pass; fresh merged-source qualification remains open.
- Native stamp-marker failures propagate even when Bash callers disable `errexit` through a conditional; a failed payload redirect cannot advance the stamped generation. Codespaces' real version command also retains the selected source identity when build metadata exists, while ordinary package-version/commit output is unchanged; the clean-room witness now checks that command field explicitly.

### 2026-10-18 - CLI wave merged and qualified
- PR #7139 merged as `ab6412b2469f37c89b756b22b9f6aa1966d5fc9c`. Fresh credential-free containers selected that exact commit for Codespaces, SSH, Machines, Pull Requests, Budget Guidance and Containers: each reported six passing phases, zero failures, no jams and runner exit zero.
- Each witness preserved its input checkout, installed a completed source-qualified non-editable generation, exercised installed commands and vendor imports with the checkout unavailable, and reused identical source without changing completion bytes. Before/after hashes of the mounted scenario, shared rig library and runner were identical.
- MCP's structured report also records six passing phases and no jams, but its original outer runner returned nonzero. Retained Docker event history did not recover that invocation's status; that discrepancy is not promoted to an unqualified runner-success claim.
- Continued Phase 2 with disjoint native-adopter ownership: Worktrees with the coordinator, Dispatch/Index and Logger/Vault with bounded implementation workers. Shared source helpers, scenario integration, publication and final verification remain coordinator-owned.
- Managed deployment policy, version-skew qualification, independent Manager source selection and native Windows rollout remain open. No host rollout is implied by these disposable-container results.
- Closed MCP's qualification gap with a second fresh container and a frozen rig exported from immutable commit `d49fcd897a3489b7ace389bbb412e220aba8816b`. Exact runtime source `ab6412b2469f37c89b756b22b9f6aa1966d5fc9c` again produced six passes and no jams; the traced runner exited zero and all mounted-input hashes stayed identical. This new witness qualifies MCP without reinterpreting the earlier outer-status discrepancy.
- Worktrees' native source reader now has an actual GUI-parent, two-cycle observation on Windows PowerShell 5.1 and PowerShell 7. The probe exposed array-valued launcher discovery; selecting one application before invoking its path fixed it. Both hosts now pass without attributable visible windows or foreground changes.

### 2026-10-19 - Final native installer adoption
- Extended Worktrees, Dispatch, Index, Logger and Vault's native installers to consume the complete prepared source receipt, preserve package-version metadata, publish canonical leased snapshots and select immutable source-qualified generations. Built-runtime redirects are published only after successful activation; deferred stamps retain their separate snapshot/first-use behavior, and ordinary package-only behavior remains distinct.
- Independent review found and corrected two unsupported manifest-writer parameters, a nested PowerShell snapshot assertion and a misplaced Bash dependency guard. Focused regressions load the actual canonical writers and source helpers; native Windows and Linux source contracts pass.
- Full isolated Windows Logger and Vault fixtures also prove deferred source independence, unchanged completion on reuse, operational commands and preservation after a failed candidate. These expensive fixtures are explicit-tier validation rather than unconditional required smoke tests.
- The full Linux fixture reached its 240-second item budget only after completing reuse and command checks. Separating candidate-failure assertions into a second test sharing the same real healthy installation preserves every assertion and the existing 240-second item/300-second group limits. Both Linux plugin parameters then pass, including the injected-failure diagnostic and unchanged healthy selection, completion, redirects, helpers and snapshots.
- Extended the existing clean-room scenario to all thirteen runtime adopters. Dispatch and Vault disable service installation, Dispatch disables its supervisor, and Index uses a synthetic client-only configuration. These modes qualify installation and installed commands, not daemon cutover or Index engine use.
- Fresh qualification of these five adopters still requires the exact reviewed merged commit. Deployment-policy separation, service skew, Manager source selection and the separately authorized native pilot remain open.
- Review corrections now health-check retained Vault packages in isolated interpreters before activation and refresh source manifests on Logger/Vault reselection. Native A-to-B-to-A cases retain unchanged completion evidence; corrupt retained imports cannot borrow a checkout through `PYTHONPATH` or replace the healthy selection.
- Dispatch's active-source publication now keeps its recovery stamp coherent with the selected payload snapshot, while a later explicit stamp remains deferred. First-use recovery matches the available snapshot against exact selectors rather than ordering source hashes, including recovery from an older mismatched stamp.
- Worktrees now publishes wrappers and resolvers after candidate health and marker selection but before resident-monitor reconciliation. Actual native interpreter/activation cases enforce that order and prove failed health leaves the prior redirects and selectors intact.
- Corrected the coupled native manifest and transaction-lock fixtures without replacing their source validators or publication writers. Package-only SHA guard coverage retains its minimum adopter count and real failure assertions; optional source validation no longer incorrectly excludes those legacy hash paths.
- A subsequent publication-failure review refined Worktrees' order to health, completion, helper publication, source revalidation, selector activation and monitor reconciliation. Missing/copy-failed helpers now abort before selector commitment; no compensating selector rollback can overwrite a competing activation. Native failure fixtures cover actual publishers and a separate canonical activation during publication.
- Updated the lifecycle-admission fixtures to carry and load the actual source/native companions required by the extracted and full installers. Foreign-source refusal and admitted reentry remain separate bounded cases with unchanged item/group deadlines, retaining every action and mutation-preservation assertion.
- Fresh and retained Index host runtimes now share the complete isolated server/dependency health gate. Real native retained-slot cases reject corrupt server or dependency imports before changing selectors or manifests; healthy hosts and client-only bypass remain covered.
- Native publication/source-transaction and command binding/lifecycle suites pass on Windows; Linux publication and lifecycle suites pass. Index's complete host-health cases pass on both platforms. Its coupled Linux retry fixtures now run in their own temporary working directory, avoiding unrelated editable-project retention work without changing retry assertions or deadlines. Exact merged-source qualification remains outstanding.
- Conditional package-only Logger snapshot callers now propagate preparation, copy, pruning, materialization, rewrite and publication failures explicitly instead of depending on Bash `errexit`. Fifteen injected failure boundaries preserve prior pointers and snapshot bytes; normal materialization and Vault's analogous stamp-directory failure are covered separately. Canonical source-qualified publication remains unchanged.
- Corrected three stale Worktrees guard assertions for the new publication-mode forwarding and refreshed the teardown-bootstrap fixture at its actual native-capture boundary. Focused Windows/Linux checks pass without increasing deadlines. A local full Worktrees guard invocation reached its existing 300-second wrapper limit and is not recorded as passing; the complete provider CI lane remains a separate merge gate.
- Logger and Vault now reconcile required helpers after isolated health and before activation on both native paths, including completed-source retries when the target is already selected. Real fresh/retained helper-copy failures preserve prior selection and manifests; a subsequent retry repairs helpers and activates successfully. Stamp failures preserve the prior payload pointer.
- Hardened the canonical shared binstub writers on Bash and PowerShell: supplied resolver sources are required, failed writes/copies propagate, and individual complete files replace destinations atomically. All eight shared-engine adopters are covered by changefiles. Native writer failure cases and eleven actual installer failure/retry histories pass on both platforms; ordinary quoting and provisioning-lock checks remain covered. These fixes do not establish deployment-policy or native-host rollout completion.
- Service-enabled Vault failure cases now exercise required directory, unit-write and publication errors through both conditional install/update callers. Logger's custom command, auxiliary, preparation and unit writers also propagate required failures. Optional discovery and documented best-effort systemd handling remain distinct.
- Replaced Windows overwrite moves with actual `File.Replace` and non-overwriting first-publication moves in the shared writer, Logger and Worktrees. Native tests hold real staged-source/destination handles and require sharing error 32; healthy replacement and first publication remain successful. PowerShell's explicit null-string binding avoids converting a null backup argument into an invalid empty path.
- Audited Dispatch/Index's conditional publication chains, corrected required directory/copy/marker/lock/admission propagation, and put Dispatch activation inside its publication lock. Late-write failure restoration reuses one canonical ordinary-file backup/restore implementation in the shared installer engine rather than duplicated plugin implementations. Real failure injections preserve prior selectors, manifests, command bytes and modes; unsafe or incomplete restoration remains an explicit failure.
- Independent bounded review found no remaining High/Medium issue in this repair set. Post-consolidation tests verify actual shared-helper loading; the build-artifact fixture retains its own attributable source root instead of accidentally resetting dependency-policy provenance to the coordinator checkout. The remaining stale Index server-health assertion now follows the shared fresh/retained host probe. Exact-head provider review and CI are still required before merge.

### 2026-10-19 - Directory-collision and portable-fixture corrections
- File publishers reject directory destinations, including links to directories, rather than treating a move into a directory as successful installation. Shared resolver/command publication and Logger's custom families preflight their required destinations before changing ordinary peers. Unit, helper and redirect staging retains explicit failure outcomes.
- Bounded Linux regressions cover shared stamp/selector preservation and Logger/Vault custom command, resolver, unit and snapshot failures. Native Windows cases verify real directory refusal and retained snapshot-marker replacement, alongside the existing sharing-violation cases; cleanup is verified.
- Dispatch's descriptor-specific failure injection now uses Bash file identity through `/dev/fd`, not Linux procfs. Its two direct injector cases also pass under native Git Bash; only the external-`flock` injection skips when that utility is unavailable.
- Corrected a runtime-resolution guard false positive by reporting the actual attempted interpreter variable in Logger's failure diagnostic. No launch path or ordinary recovery policy changed. These repairs do not constitute a native host rollout or close the remaining deployment-policy and Manager gates.

### 2026-10-19 - Source-stamp redirect preflight
- Worktrees now validates both source-stamp redirect destinations before snapshot or redirect publication on Bash and PowerShell. A collision at the second marker cannot change the existing first-use payload pointer; both marker positions preserve the other pointer and directory contents in real native regression cases.
- The complete bounded Worktrees source-transaction file passes on Windows and Linux, including ordinary snapshot reuse and source-refusal behavior. Logger's scaffold assertion now recognizes the tested native `File.Replace`/non-overwriting move implementation instead of requiring the retired `Move-Item -Force` spelling.
- Repaired the exact CI fixture omissions exposed by fail-closed publication: Dispatch's constrained PATH includes real `mktemp`, and extracted Dispatch/Index paths load their actual owning classification/publication helpers. The marker-failure fixture now asserts the intended refusal rather than accepting a missing-function error. Index's hook fixture selects a native PowerShell host on its executing platform. Focused Dispatch and Index suites pass without loosening production checks or test deadlines.

### 2026-10-19 - Command-entrypoint health before activation
- Logger, Vault and Index now exercise isolated CLI entrypoints before fresh or retained activation. Index also retains its role-specific host dependency, server-import and runtime-origin checks; health probing does not start services or an engine.
- Real candidate environments distinguish a healthy package initializer from a broken command module. Corruption regressions preserve prior selection, deployment metadata, redirects and immutable completion evidence, then verify successful reselection after restoring the CLI.
- Dispatch's existing entrypoint/embodiment imports and Worktrees' full lazy-command-surface import already cover this failure class. Dispatch now has an explicit broken-command regression rather than relying only on an embodiment-module failure.
- The real-CLI fixture initially accumulated copied interpreters beyond the unchanged 2 GiB temporary-storage budget. Case-scoped teardown now reclaims only its own runtime/template directories, with link/escape refusal and visible cleanup errors. The complete thirteen-case reselection module passes on Linux and Windows within the existing bounds; the earlier storage-limited run is not counted as passing.

### 2026-10-19 - Source redirect sets and exact receipt publication
- Added one canonical source-qualified redirect publisher, reusing the existing stdlib runtime helper and kernel lease primitives. It stages the complete requested marker set and recovery copies, replaces files atomically, and restores prior values or absence on caught failures. Unsafe concurrent changes or failed recovery retain explicit recovery evidence rather than overwriting a peer.
- Pinned the complete source receipt inside the new publication lease, before every replacement and before commit. Regression mutations change the source tree field without changing the generation string, covering lease acquisition, staging and both publication boundaries.
- Source adapters across all thirteen runtimes use this boundary while retaining existing locks, profiles and Python-free legacy branches. Machines includes provenance in the full source transaction and serializes its source fast-stamp keep/replace decision; fast stamping still does not advance the version marker. Bridge's deferred source stamp now reuses the canonical leased snapshot publisher instead of rebuilding a same-generation snapshot.
- Real write faults, held Windows second-marker handles, rename-then-error outcomes, concurrent kernel ownership, tampered temporary/recovery files and failed restoration are exercised without widening test budgets. Source snapshot reuse preserves file identities and bytes. This is caught-failure recovery, not a new simultaneous multi-file or crash-journaling guarantee.
- The operator approved tracking the separate Python-free legacy gap in #7250 and the unexplained fifteen-second Linux PowerShell source-resolver CI timeout in #7251. The exact timeout case passes locally without a deadline change; its cause remains unresolved and it is not an accepted flake or CI waiver.
- This repair expands the changed source-stamp surface beyond the final five adapters. Qualification after the actual merge must therefore use a frozen exact-merged rig for all thirteen runtime adopters; older seven-adopter witnesses remain historical evidence, not the new merged-head qualification.

### 2026-10-19 - Committed-source manifest provenance
- A further review found source manifest writers revalidating mutable input after immutable publication. Source manifests now use precommit metadata or the selected receipt and validated immutable payload, retaining the original command-binding source path rather than transferring ownership to a runtime snapshot.
- Worktrees prepares its complete source manifest before final validation and selector commitment. Bridge retains its already-selected receipt and admitted content hash. The other native source writers use their committed snapshot while preserving the source/profile dimensions their installer owns.
- Reconciliation with current `dev` preserved upstream lifecycle helpers and explicit cell dispatch; only generated installer receipt hashes required conflict resolution. A provider module-size guard also caught the new source CLI exceeding the runtime core's shrink-only ceiling. Source/fingerprint command wiring now lives in the existing source companion; no ceiling or test budget was widened.
- The complete runtime-source lane passes 172 Windows cases and 140 Linux cases with 32 platform skips. Worktrees' complete source-transaction module passes 26 Windows cases and 14 Linux cases with two skips. The intact thirteen-case Logger/Vault real-CLI module passes on Windows within 221 seconds; Linux's combined thirteen-case CLI/four-manifest run passes within 264 seconds. Eight Windows postcommit Logger/Vault cases pass as their own module, and all sixteen seven-adopter manifest/cell cases pass on each platform.
- Combining the two distinct Windows Logger modules into one group exceeded the unchanged 300-second bound. That run is not passing evidence: the existing modules were then run separately within that same bound, without splitting the thirteen-case module or raising limits. The ordinary full-suite file groups already place these modules separately.
- The thirteen-case reselection count covers two adopters, Logger and Vault, not thirteen adopters. All-thirteen-adopter qualification remains the separate fresh clean-room run from the actual merged commit.
- Exact-head provider CI, a subsequent passing review and fresh qualification from the actual merged commit remain open gates. This is still installer-adoption work, not native deployment, managed-policy or maintenance completion.

### 2026-10-19 - Source manifest runtime-slot identity
- A further review identified Worktrees' POSIX source manifest retaining the legacy `.venv` path even though source activation is link-free. Source manifest construction now records the selected versioned directory on both platforms, without changing the legacy link variable's construction or teardown duties.
- Fresh and obsolete-alias writer fixtures assert the selected directory and preserve the old alias. The full Worktrees source-transaction module passes 29 native Windows cases and 16 Linux cases with two platform skips, including both PowerShell hosts where available.
- The other twelve adopters' initializer/writer paths were checked for this class. Their existing production paths already select the proper runtime directory. Seven-adopter initializer/writer fixtures now start with divergent aliases and assert the actual slot; all sixteen cases pass on each platform. Dispatch and Index also retain their real source-initializer slot assertions.
- Corrected the PR's test-scope description and its corresponding reply: thirteen Logger/Vault cases cover two adopters. These local writer and installer fixtures do not replace the fresh all-thirteen-adopter clean-room qualification from the actual merged commit.

### 2026-10-19 - Producer-owned outer recovery
- Replaced post-return ownership enrollment with a canonical producer-issued receipt. Redirect, activation and Index session-stamp producers persist the created stage descriptor's physical identity before replacement, including recovery writes. Native verification rejects an identical-byte peer replacement, and outer recovery refuses the complete set when required ownership evidence is missing, corrupt or foreign.
- Kept compatible publication leases for default and receipt-requesting writers, with ordered recovery that does not reacquire the outer Index installation lock. Shell publication now moves an exact-basename stage from a private sibling directory to the destination parent, verifies physical poststate and retains unknown artifacts rather than reporting a directory-target move as success.
- Shipped the companion in all thirteen canonical payload closures, the four additional deployed-helper copy lists on both platforms, and standalone wheel/sdist resources. Actual retained-input wheel builds for Fleet Manager and Index include the companion after the original resources become unavailable.
- Independent review passes the two reproduced ownership/directory races and the new receipt protocol without a remaining high/medium production finding. The full runtime-source lane passes 193 native Windows cases with two skips and 163 Linux cases with 32 platform skips. Native producer interoperability passes seven cases; Linux actual-caller regression cases pass separately.
- The complete native Dispatch conditional-publication module passes nineteen cases with sixteen POSIX-injection skips. Its rename-then-error case initially exceeded the unchanged twenty-second subprocess deadline: timestamped tracing showed continuous restoration progress through an unnecessarily broad fixture, not lock contention. A focused real-engine witness retains successful rename followed by an error, prior payload restoration, physical selector identity and complete cleanup; the existing multi-file matrix remains intact and no deadline increased.
- Repaired the clean-room unit witnesses for all thirteen installer actions, command-specific version prefixes and Index's bare version output. These thirty-seven unit cases and the installer fixtures are not fresh all-adopter qualification; exact-head review, complete provider CI and a frozen exact-merged clean-room run remain required. Deployment-policy separation, Manager source selection and native host rollout remain open.

### 2026-10-19 - Standalone artifact closure and fingerprint execution
- A further review found four synthetic checkout fixtures omitting the new mandatory publication companion. Fleet Manager and Index fixture copies now include it, with wheel/sdist content, RECORD and missing-resource refusal assertions. Production resources remain mandatory; no partial-wheel fallback was added.
- Kept the fingerprint command in its already-loaded primitive rather than reopening a sibling by its development filename. Standalone wheels name that primitive `_versioned_runtime.py`; real isolated named/packaged CLI regressions cover plain and JSON output plus missing-input refusal.
- The complete Fleet distribution module passes nineteen cases on both Windows and Linux. Index's existing standalone smoke runner passes 478 Linux cases with six skips, including its normal, release and runtime-resource distribution builds.
- Reconciliation with the reviewed unit-only CI transition exposed a local contract fixture still requiring the retired native integration jobs. It now checks positive source-change admission into the bounded unit packet, not implicit native integration in required PR CI. No workflow, packet admission or execution budget changed.
- The final complete runtime-source lane passes 198 native Windows cases with two skips and 168 Linux cases with 32 platform skips. This remains local source/installer evidence; updated exact-head review, provider CI and fresh all-thirteen merged-source qualification remain open gates.

### 2026-10-19 - Complete standalone primitive loading
- A further review confirmed that moving fingerprinting back into the loaded primitive did not repair the other companion calls. The canonical loader now supports both explicit shipped filenames and requires exactly one ordinary co-located primitive, rejecting ambiguous or unsafe aliases instead of searching ambient imports.
- Traced every primitive-loader use through redirect locking, publication context/check/restore/cleanup, source hashing and snapshot construction. The expanded thirty-two-case real CLI matrix covers both layouts, including quiet/JSON output, receipts, partial-error recovery, corrupt/unowned state refusal, full source pinning and activation.
- The complete source lane passes 224 native Windows cases with two skips and 194 Linux cases with 32 platform skips. The core stays within its existing shrink-only ceiling; publication protocol, output contracts and test budgets remain unchanged.
- These local repairs still require current-head review and provider CI before merge. Full thirteen-adopter clean-room qualification remains a frozen exact-merged-commit obligation, not a claim made from CLI case counts.

### 2026-10-19 - Exact helper-file publication
- A further review reproduced a Codespaces helper publisher reporting success after moving its temporary file inside a directory at the required companion path. The same call-site class affected Bridge, Index and Logger helper/resolver copies; all four Bash paths now reuse the existing exact-basename publisher.
- Bridge's native helper copies use guarded GUID staging and `File.Replace`/non-overwriting `File.Move`, retaining the optional-resolver and scoped/global command branches. Index still requires health and construction completion before helper publication and activation only after all helper copies succeed.
- Forty-three actual-caller cases pass on each of native Windows and Linux, covering first installation, healthy replacement, directory collisions and races, copy errors, mode preservation, optional resolvers and activation ordering. The Bash cases reject unexpected Python use and preserve peer-directory contents. Both available PowerShell host names are exercised; this is not a Bash 3.2 or BSD runtime claim.
- Publication protocol and shared engine behavior are unchanged. The combined loader/helper correction still needs exact-head review and provider CI before the separate all-adopter merged-source clean-room gate.

### 2026-10-19 - Integrating the retained Python installer owner
- A concurrent reviewed backend migration replaced Worktrees' native construction/selection path with the retained Python lifecycle owner. Reconciliation preserves that owner rather than restoring a second native build pipeline.
- Integration is in progress: carry the complete source pin through canonical candidate admission and bootstrap resources, then publish required native helpers through the existing admitted lifecycle after candidate health and before Python-owned selection/manifest commitment.
- Initial real-callback tests and independent review identified an executing-helper/original-owner argument mismatch, declared-versus-normalized package-version handling, postcommit mutable-origin reads and lost source-qualified operational version reporting. These remain repair gates, not passing evidence or reasons to weaken authority checks.
- Existing pre-reconciliation proofs remain scoped to their recorded implementation. Current-backend source, callback and namespace tests plus a new exact-head review/CI are required before merge; the parent effort and all-adopter clean-room gate remain open.

### 2026-10-19 - Integrated publication validation gate
- The retained Python backend now delegates native helper and required global command publication before Python-owned runtime selection and manifest commitment. Native exact-file publication refuses directory collisions/races; PowerShell retains its newline-normalized unchanged-content no-op.
- Installer metadata is regenerated and canonical synchronization passes for all eight engine adopters. The install-contract check passes for all thirteen declared runtime plugins. Complete integrated callback, namespace, command-version and failure-preservation runtime proofs remain pending on these final bytes.
- The Plan explicitly retains one fresh, frozen-input, exact-merged qualification report per declared installer adopter. Static checks, writer fixtures and earlier installer-wave reports do not substitute for that combined gate, deployment-policy separation or independent Manager runtime-source selection.
- The complete shared source lane passes 202 Linux cases with 32 platform skips; the complete command-lifecycle module passes 47 with nineteen skips. Both runs have verified cleanup. The first source-lane attempt had one fifteen-second PowerShell-launch timeout, tracked by #7251; one unchanged full retry passed without widening deadlines. The first attempt remains failed evidence, not an accepted flake waiver or a stability claim.
- Canonical runtime/resolver, installation-context and bootstrap-guard synchronization checks pass, as do the current module-size and package-version consistency guards and changed-Python lint. Integrated native callback and publication proof remains separate and pending.
- The complete native Windows shared-source lane also passes, with 232 cases and two skips. A stale first-bootstrap assertion still expected the removed native activation pipeline; it now asserts provision's resolver-only frontend, admitted helper callback and acknowledgement ordering under the retained Python owner. The complete updated bootstrap module passes 22 Linux cases with four skips; its native rerun and the remaining exact-file helper-publication class repairs are still pending.

### 2026-10-19 - Complete callback failure preservation
- Required resolver, hook, conduct, setup and global-command copies now route through one exact-file publisher per native language. The change closes directory-destination false success, but per-file exactness alone does not satisfy the existing failure-preservation contract.
- A real callback regression with verified cleanup confirms that a later global-command staging failure leaves an earlier global command replaced even though selectors and deployment manifest remain unchanged. The failing assertion is retained; no passing redirect-set preservation claim is made.
- The bounded repair extends canonical created-object publication proofs to forward file copies and selective recovery of uncontested outputs. Capture the complete declared callback target set before writing, prove each created stage before rename, and preserve foreign directories/changed objects rather than overwriting them during recovery. Existing strict recovery APIs and Python-free non-callback native behavior remain unchanged.
- Helper transaction custody must span callback acknowledgement and Python-owned activation, monitor and manifest settlement. Restore previous helpers only when the retained owner actually preserves/restores the previous runtime; a late exception with the candidate genuinely committed must retain the candidate's helpers. Both settlement outcomes require regression evidence.
- Canonical API review requires matching target-writer exclusion through settlement without nested lease reacquisition, and verifies owned backup cleanup before deleting its context/receipts. A corrupt or changed backup retains the complete evidence; postcommit evidence-cleanup warnings must not become a new mutable-source gate or reverse a genuinely committed runtime.
- Canonical proof validation, native callback integration and complete Windows/Linux regression evidence remain open gates before rebase completion, publication and the separate exact-merged all-adopter qualification.
- Six complete Worktrees bootstrap/completion/refusal/deployment/publication/slot-clean guard modules pass 116 cases with six skips on both native Windows and Linux, with verified cleanup. The native first attempt exposed stale activation-call counts and incomplete extracted-function fixtures; those now assert the actual Python-owner route and include the real shared publisher with fault injection at its I/O. Retained native compatibility-helper unit cases are explicitly not full current-backend installation evidence. These results precede the pending canonical forward-copy/transaction extension.

### 2026-10-20 - Transaction closure and refreshed dependency qualification
- Implemented the canonical created-FD copy transaction and bounded native callback inventory. Helper target leases and recovery evidence remain held through the Python owner's actual terminal settlement; normalized no-op copies retain physical identity. An independent scoped static review found no High/Medium issues in the canonical transaction/recovery implementation. That review does not substitute for frontend-to-settlement or live-daemon evidence.
- The complete shared source suite passes 253 Windows cases with two skips. The first Linux report omitted the native PowerShell installation from PATH and skipped 68 cases; it is superseded by a full-platform rerun with 221 passes and 34 skips. Both retained proof scopes are explicit.
- Regenerated the final native installer metadata through the canonical tool. The complete native source-transaction module passes 29 cases. Canonical shipping synchronization, installer contracts, module ceilings, version consistency, changed-Python lint and whitespace checks all pass on the frozen implementation.
- Five backend liveness failures were traced to an old same-version ZDD copy in the cached test environment, not missing canonical production APIs. The canonical implementation already delegates to native exit-evidence helpers. Applied the documented test-runner dependency refresh without changing ZDD or weakening assertions; the complete Windows backend module then passes thirty cases.
- The refreshed complete Linux backend module still reaches its unchanged 300-second group limit. Its real installer/drain test is under bounded diagnosis; this is not green qualification or a reason to raise limits. Final callback proof on refreshed dependencies and the remaining Linux transaction/guard results are still being completed before rebase continuation.
- A named diagnostic located the Linux stall after successful clean, drained commitment: the fixture retained an unreaped predecessor child, and its selected Python interpreter lacks `os.pidfd_open`, so the conservative PID-existence fallback still sees that zombie. The approved fixture-only repair gives the spawning test an owned `wait()` thread and joined cleanup. Production liveness, retention assertions and deadlines remain unchanged; the complete-module rerun is still required.
- A final native guard run exposed one stale assertion expecting the replaced raw Python launch rather than the argv-safe windowless adapter. Updated that fixture to retain prerequisite ordering against the actual call; its complete bootstrap module passes 23 cases with three skips. All supervisor results cited here verify cleanup.

### 2026-10-20 - Frozen-input cross-platform integration qualification
- Both proof lanes completed against frozen inputs with unchanged supervisor and runner limits, verified cleanup and no remaining owned jobs. Windows runtime modes, the portable full-input backend, canonical engine and source transactions pass thirteen, thirty, thirty-three and twenty-nine cases respectively. The earlier modes failure coincided with another test fixture being edited inside the measured source graph; it remains failed input-churn evidence, not a production mutation diagnosis.
- The real backend fixtures now freeze the complete dependency closure into a test-owned native Git repository and assert its actual commit and unchanged contents. Their owned predecessor reaper preserves every drain, retention and readiness assertion without depending on optional pidfd support or the driver's cross-platform worktree metadata. The complete Linux backend passes 29 cases with one platform skip.
- Corrected account-home isolation in the copied PowerShell context fixture to match the existing Python and Bash fixture lookups before freezing source identity. Production governance remains unchanged. With native PowerShell available, the complete Linux callback/namespace/version module passes 41 cases with one skip; all ownership, full-pin and acknowledgement negatives reach their intended boundaries.
- Complete global-command, required-asset, helper-settlement, inventory, explicit-mode and canonical-engine modules pass 15/50/18/8/13/33 cases on Windows. Linux passes 10/34/12/8/11/32, with two mode and one engine platform skips. Linux's complete source-transaction plus six-guard selection passes 132 cases with eight skips; the corresponding Windows whole-module coverage passes 145 with six skips.
- Updated the runtime primitive's owner guide and install contract for the finite copy API and actual-outcome helper custody. Publication still requires rebase completion, exact-head provider review and CI. Fresh all-adopter qualification, deployment-policy separation, independent Manager source selection and native adoption remain open; this entry does not close the effort.

### 2026-10-20 - Constrained Gateway build-context completeness
- Exact-head review found a consumer integration missed during base reconciliation: Gateway's Docker COPY, Docker ignore allowlist and pinned clean-room source inventory omitted the newly mandatory publication companion. Added it to all three inventories without making the build hook optional or introducing a fallback.
- Two real wheel-build regressions reproduce the missing companion from repository-root recipe/filter inputs and a real Git-materialized pinned context. After the inventory correction, the complete distribution module passes 21 cases on Windows and Linux, including byte equality of every canonical runtime resource in both context-built wheels. The original nineteen distribution cases remain intact.
- This repairs build inputs, not Gateway deployment or service activation. Updated-head review and provider CI remain required; fresh all-adopter qualification and the parent effort's later runtime-policy/Manager/adoption gates remain open.

### 2026-10-20 - Verified publication-lease namespace
- Further exact-head review confirmed the Gateway correction and identified session-stamp lease creation preceding its admitted-directory revalidation. Permission changes and symlink substitution could mutate a rejected namespace before the writer refused publication.
- Added an optional verified-directory opener to canonical target leases without changing their grouping, lock ownership or settlement protocol. The stamp writer creates and opens lease storage relative to its original pinned parent handle, retains the child handle through lease release, rejects linked or multiply linked storage and revalidates native Windows directory ownership.
- An independent scoped review identified ordinary-directory substitution between verification and path reopening. Reproduced it separately, then removed that reopening: child-directory operations use the original admitted descriptor, and copied ancestor bindings detect substitution before opening a lease file. The subsequent scoped review found no remaining High/Medium defect in the named correction.
- A real contender initially refused canonical storage created under a permissive umask. Newly created default lease directories now use private creation permissions; existing unsafe directories are not silently repaired. The contender regression retains actual subprocess diagnostics and proves serialization for both changed and unchanged metadata.
- The final full source suite passes 253 native Windows cases with three skips and 222 Linux cases with 34 platform skips. The complete stamp-security module passes 44 Windows cases with 36 platform skips and 65 Linux cases with fifteen skips. All runs retain existing bounds and verified cleanup. Exact-head review and provider CI remain separate merge gates, followed by frozen exact-merged all-adopter qualification; later deployment-policy, Manager and adoption work remains open.

### 2026-10-20 - Installer prerequisite merged; assembled qualification
- PR #7234 merged as `86dc022d2ba8acb8915e389754f65c95b59a8ad3` after the exact corrected-head Copilot verdict reported no open findings and all required provider checks passed. The two remaining Phase 2 items are closed by the integrated native source/selection and failure-preservation regressions; this does not close any deployment-policy, Manager source-selection or native rollout gate.
- Froze the complete merged repository outside the active checkout for the thirteen-adopter clean-room qualification. Each adopter requires a fresh container, explicit exact commit and plugin parameters, six passing stages, zero jams, successful runner exit and stationary full-repository inputs. Active-checkout edits are not mounted into that witness.
- The effort remains Active. The next implementation stretch is deployment-only policy and independent Manager-owned service-runtime source selection; the requested recurring maintenance extension must clear its own reviewed intent before implementation. Native adoption remains separately consented.

### 2026-10-20 - Exact-merged qualification stopped on Dispatch
- Nine adopters passed all six phases from the frozen `86dc022d2ba8acb8915e389754f65c95b59a8ad3` repository: Bridge, Codespaces, Containers, MCP, SSH, Machines, Pull Requests, Budget Guidance and Worktrees. Each actual runner exited zero, with no jams; installed commands and immutable reuse remained usable after source removal. Bridge additionally verified authenticated serving identity.
- Dispatch installed its source-qualified generation but its runner exited one at phase three: the scenario rejected a two-line `--version` response containing the correct generation on its second line. Its ordinary argparse version formatter wraps a long command/version pair; the scenario's first-line-only field extraction, not the installed generation, caused this refusal.
- Index, Logger and Vault were not started. All ten attempted runs retained matching full-source before/after inventories; no successful adopter was rerun and no scenario or source input was changed during qualification.
- Normalized captured line separators before extracting the existing exact command/generation fields. The scenario's complete regression module passes 66 cases, including actual argparse wrapping, CRLF, incorrect command/generation and strict bare-generation Index cases. Production version output and optional provenance suffix semantics are unchanged.
- The complete thirteen-adopter gate remains open. Preserve the failed report; the scenario correction must merge before fresh qualification from frozen merged inputs. This unit result does not turn the failed container run into a pass.

### 2026-10-20 - Version-witness correction merged
- PR #7367 merged as `a4bdee25a5b723f2d1dc2698278eeb15e6812dbf` after an exact-head Copilot approval with no open findings and all required provider checks passed.
- The next witness uses a new full-repository archive of that merged commit, with all thirteen adopters in separate fresh containers. Earlier `86dc022d2b` reports remain retained evidence for their original commit; they cannot be combined with new runs into an exact-commit all-adopter claim.
- Deployment-only policy, independent source selection, proposed recurring maintenance and consented native adoption remain open. No host install, source binding or schedule was changed by this merge.

### 2026-10-20 - All thirteen merged-source installers qualified
- Every declared adopter passed six phases with no jams and actual runner exit zero from a complete frozen archive of `a4bdee25a5b723f2d1dc2698278eeb15e6812dbf`. All thirteen containers were fresh and distinct; no earlier-commit result was reused.
- Full-repository inventories covering 8,476 entries and executable modes remained unchanged before and after each run. An independent coordinator check matched all reports, inventory pairs, exact source/generation assertions and retained container identities.
- Each adopter proves non-editable installation, source-removal imports/commands and immutable reuse. Bridge additionally proves authenticated serving identity. Dispatch runs without service/supervisor, Index uses only its synthetic client role, Vault requests no credentials or service, and Logger exercises post-removal `session-sync`. These scopes do not claim all-daemon cutover, heavy-engine operation or Manager installation.
- The all-adopter qualification item is closed. Original deployment-policy/source-selection work, the separately reviewed maintenance extension and consented native adoption remain open; the effort stays Active.

### 2026-10-20 - Deployment and maintenance intent reviewed
- PR #7397 merged as `600c0efcae6bc2fde86fae07bdeceaf49d147390` after exact-head Copilot approval with no open findings and successful required checks. The reviewed proposal preserves existing deployment, source-selection and scheduling owners; it enables no policy, source binding, cadence or native operation.
- Re-read the Plan and Validation Plan. Only the Phase 4a intent-review item closes. Deployment-only policy, independent Manager/service source selection, maintenance implementation and observed native operation remain open.
- The next implementation slice is Phase 3: trace a real bootstrap and indirect first-use path to the owning publication seam, preserve non-deployment behavior and source-qualified ownership, then implement and validate the shared authority decision before partitioning adopter wiring.

### 2026-10-20 - Shared deployment decision implementation
- Implemented the read-only installation-context decision and native diagnostic/probe adapters under #7030. The installation-local record matches existing source/plugin/mode/root/environment ownership; it does not select runtime source or authorize management.
- Native fixture coverage distinguishes absent, managed, explicit standalone, malformed, unsupported, inaccessible and foreign policies without runtime or redirect writes. The focused Linux reference/Bash/PowerShell matrix passes 108 cases; Windows API/PowerShell 7, the separate 23-case PowerShell 5.1 matrix, packaging and existing mode smoke checks also pass.
- Publication callers, skew behavior, explicit management admission and the complete native transition remain open. This helper alone does not enforce the plugin/runtime boundary or complete Phase 3.

### 2026-10-20 - Shared deployment decision merged
- PR #7415 merged as `03019a44d3cb16746a9a8bd95c6a40f65afae4a8` after exact-head Copilot approval with no open findings and successful required checks. The review caught a PowerShell singleton-root-array coercion; the canonical original-root guard and seven additional parity cases close that bug class across all native copies.
- Re-read the Plan and Validation Plan. Only the shared-decision foundation item closes; actual publication enforcement, retained hook behavior, explicit management, skew and all later adoption gates remain open.
- Next wire the real bootstrap, indirect first-use and per-installation reconciliation/publication paths to the canonical decision. Preserve independent hook behavior and existing ownership admission; an installed healthy runtime must not require publication permission.

### 2026-10-20 - Per-installation reconciliation implementation
- Wired the first real caller: each needed runtime plan checks its own deployment policy after payload refresh and monotonic-version decisions. In-process execution revalidates permission and selected root/receipt immediately before either runner. Healthy runtime selection, unrelated plugins and uninstall remain independent.
- Real helper fixtures cover legacy and active-cell policy scope, late opt-in, older adapters and foreign ownership. Final native Windows passes 191 cases with one platform skip; Linux passes 186 with six Windows-only skips. The bounded diagnostic transport is extracted rather than growing the grandfathered reconciler.
- Review exposed physical-versus-aliased home spelling in the new root, context and policy comparisons. Validated canonical path comparison replaces string equality; the expanded 22-case deployment suite passes on both Windows junctions and Linux symlinks without runtime writes.
- Full hook/first-use/native-installer and launcher execution admission, explicit management, skew, source selection and maintenance remain open. No policy, source binding, native deployment or schedule was enabled.

### 2026-10-20 - Reconciliation caller merged; native publication next
- PR #7424 merged as `c0c20a5a54e1ee4acb26775bb74c7098204e198a` after exact-head Copilot approval with no open findings and successful required checks. The review-discovered alias issue is closed by real Windows-junction and Linux-symlink regressions.
- Re-read the Plan and Validation Plan. Only the per-plugin reconciliation planning/in-process execution item closes; the whole publication boundary and retained-hook/skew validation remain open.
- Next integrate bootstrap and indirect first use with actual native publication admission, reusing the source-qualified lifecycle lease for explicit management rather than an environment hint. Preserve non-deployment hook execution and native launcher-run plans; no installation or maintenance opt-in is implied.

### 2026-10-20 - Shared native admission implementation
- Composed the canonical deployment decision with the existing legacy lifecycle before lock/reservation writes, under the acquired lock and on each ownership assertion. Direct manifest publication checks the same policy even without an enclosing installer.
- Explicit management purpose is recorded on the existing live lock owner and inherited by source-qualified native children. A plugin lease cannot upgrade its purpose; malformed, unsupported or foreign policy remains blocking. Teardown remains policy-independent and cannot publish.
- Real native installer fixtures verify managed install/update/provision/stamp refusal before staging and explicit CLI admission through the borrowed child lease. Corrected the older manifest fixture to include its real builder-function closure; no production fallback was introduced.
- Installation-cell admission, bootstrap lib-copy reconciliation, early first-use writes, retained hooks, skew and Manager source selection remain open. No live policy, deployment or maintenance cadence is enabled.
- Review found that native ticket checks did not compare the requested action to the lease operation. All borrowers now require the expected operation, and both native installers pass it before staging and before dispatch. Real teardown-to-stamp/install/update/provision/helper negatives and a valid uninstall reentry guard the correction.
- A concurrent admission-interpreter update required only a combined installer capability digest in the rebase. Its executable canonicalization also exposed a fixture isolation gap; the synthetic interpreter now reports its own profile-preserving wrapper. Native Windows passes 129 cases with 28 platform skips; the complete Linux selection passes 134 with 23 skips in two bounded batches.

### 2026-10-20 - Shared legacy publication admission merged
- PR #7439 merged as `7b41160374038e2e5bd3697708976885d9071550` after exact-head Copilot approval with no open findings and successful required checks. Native operation matching closes the review-discovered teardown-to-publication gap across both installers and direct helper borrowers.
- Preserved two concurrent installer changes during digest-only rebases. Current-base deployment coverage passes 38 Windows cases with four platform skips and all 42 Linux cases; the complete lifecycle/binding/output selection also passed on both platforms.
- Re-read the Plan and Validation Plan. Only shared legacy lifecycle admission closes. Next gate bootstrap lib-copy reconciliation and early indirect first-use writes before root/lock creation, retaining healthy-runtime dispatch and non-deployment hook behavior. Installation-cell admission, skew, independent Manager/service source selection, maintenance and native adoption remain open.

### 2026-10-20 - Native bootstrap and early first-use gates
- Added native canonical deployment probes before first stamping and old lib-copy replacement, outside the legacy swallowing error handler. The invoked payload owns the probe; old manifest source hints do not choose policy authority. A deployed hook without a capable native helper retains standalone behavior only when policy is absent.
- Early first use checks before provisioning commitment, trace/root/lock writes and again under the existing provision lock after healthy-runtime recheck. Typed results use the actual governed canonical policy path, retaining aliased homes and active-cell scope. Healthy dispatch never requires publication permission.
- The focused forty-four-case native matrix passes across Linux Bash/PowerShell and Windows PowerShell hosts, with platform-inapplicable cases skipped on Windows. Real policy adapters cover no-write refusal, standalone stamping/lib-copy, older deployed hooks, canonical aliases, namespaced scope and a policy arriving while the provision lock is held. A separate composition regression positively executes independent project, registration, nudge and hygiene hooks after deployment refusal.
- Corrected the initialized PowerShell test host to capture direct console output without starting another profiled shell. POSIX native fixtures isolate the OS account lookup just as their Python counterpart does; production guest identity and ownership validation remain unchanged.
- Installation-cell installer admission, launcher-executed plans, remaining hook/extension callers, declared skew, source selection, maintenance and native adoption remain open. No host deployment or maintenance opt-in is implied.
- Review exposed null-mode standalone fallback rejection and production resolver traces written before deployment admission. Bootstrap now composes canonical legacy mutation admission, first use uses the admitted fallback's legacy root, and preliminary resolvers defer durable traces until a healthy selected runtime. The inventory regressions now use the production resolvers, not silent stubs; healthy dispatch also asserts the original marker/slot trace phases.
- Added missing-provenance first-stamp/lib-copy/first-use coverage with mode-policy and tombstone negatives. Preserved the existing same-process and provision-lock-release regression while updating its helper call counts for the additional policy probes.
- The corrected inventory/fallback matrix passes all 68 Linux cases in two bounded selections and 34 applicable cases with 34 platform skips on each Windows PowerShell host. Existing Windows dispatch/trace contracts pass thirteen cases; the Linux Worktrees caller selection passes seven with six Windows-only skips. Its cancellation and post-provision fixtures now answer publication probes separately without weakening their original assertions.

### 2026-10-20 - Native bootstrap and early first use merged
- PR #7463 merged as `125b63bf50f58be36e5f913db29c505b6cb9f56a` after exact-head Copilot approval and successful required checks. The carried-forward probe-count finding already matched the reviewed commit; its thread now cites that exact correction and is resolved without a redundant code edit.
- Re-read the Plan and Validation Plan. Only the Worktrees native bootstrap/early-first-use item closes. The broader hook/publication boundary, installation-cell installer admission, launcher execution and declared skew remain open, followed by independent Manager/service source selection, maintenance and native adoption.
- Next reuse the existing installation-cell governance and live publication custody for native admission before staging and Python-owned mutation. Do not coerce namespaced receipts into legacy command binding or introduce a parallel lock/updater.

### 2026-10-20 - Cell installer and retained-backend admission
- Added canonical deployment probes after context/root/generation validation and before native self-staging, with another check before runtime-input writes. PowerShell validation runs in the existing host, avoiding an incidental profile write before admission.
- The Python lifecycle checks deployment policy at entry and every governance recheck, including the existing genesis/install lock boundary, candidate handover and helper callbacks. Direct backend entry cannot bypass managed policy. An existing live legacy management/publication lease remains usable; namespaced context flags do not grant management authority.
- Retained lifecycle closure now includes command-binding and lifecycle companions needed to authenticate a legacy management borrower without source imports. Separate-process coverage uses that copied closure.
- Native refusal fixtures preserve complete home inventories; positive fixtures retain standalone staging. Backend cases cover ordinary construction, explicit modes, settlement, live/stale management custody and policy changes under the actual namespace locks. Native Windows PowerShell 7 and 5.1 pass 55 cases each with 22 platform skips; all 77 Linux cases pass in three bounded batches. Existing retained lifecycle coverage passes thirty cases; stationary final-source repair on all three native Windows entrypoints plus candidate closure preparation passes four focused cases.
- Explicit managed-cell installation remains a separate unchecked item. Manager source selection, other caller/launcher gates, skew, maintenance and native adoption remain open; no host rollout or policy change has occurred.
- Review found a Windows Bash representation mismatch: the canonical Python probe returns a Windows root while the installer already holds a converted shell path. Normalize the probe root through the existing path helper before comparison. Real Git Bash absent-policy and explicit-standalone cell staging both pass, along with the corresponding POSIX Bash cases.

### 2026-10-20 - Manager maintenance ownership revision proposed
- Recorded the operator's default-off, consuming-harness opt-in and Windows Autorun requirements in the inception record without publishing private adopter identities.
- The operator selected implementation and adoption after plan review. Manager owns background suite maintenance; Agent Machines retains general dependency/host work, and an external controller remains an exclusive alternative.
- This bounded trigger/enrollment slice can use today's canonical selected update path. It does not close Phase 3/4 source-management prerequisites or authorize unfinished source-backed publication.
- Reused #7028 for public coordination. Review precedes implementation, and this proposal registers no startup entry, launches no worker and changes no adopter policy.

### 2026-10-20 - Cell admission merged; explicit custody next
- PR #7477 merged as `49f7e83d1a4b9b3c6e20eb3177630618a4c68c52` after exact-head Copilot approval, explicit resolution of the Windows Bash finding and successful required checks.
- Re-read both Plan and Validation Plan. The narrow cell-installer admission item is qualified; explicit managed-cell installation remains unchecked. Next authenticate management purpose on the existing genesis/install owners and borrow that custody through native staging, retained candidate handover and helper publication.
- This is not Manager source-selection integration or native adoption. No installed policy, source binding, service or maintenance setting changed.

### 2026-10-20 - Explicit managed-cell custody
- Added a stdlib-only management context manager and read-only borrower on the existing genesis/install locks. Durable publication purpose binds exact source/installation/context generations and live common ownership; environment tickets only select that evidence. No new updater, lock namespace or legacy reservation was introduced.
- Worktrees' native installers authenticate custody before staging and runtime-input writes. The backend borrows instead of reacquiring those locks and helper plans record the actual outer owner PID, preserving authorization through candidate handover and native callbacks.
- Real non-smoke repair construction, immutable completion, selection and native helper callbacks pass on Windows PowerShell 7, Windows PowerShell 5.1 and Windows Git Bash, plus Linux Bash and PowerShell. Each leaves the broken predecessor unchanged, publishes both native resolvers without legacy global binstubs, and retains the original management owners until the complete child flow returns.
- The first Windows fixture used a deeply nested test-node home and reached a dependency installer's extended-path comparison failure. A compact supervised temporary home produced the real qualification; these results do not establish arbitrary long-root support. This toolchain boundary remains part of #7030's deployment-readiness assessment rather than an inferred successful native rollout.
- Re-read the Plan and Validation Plan. Only Worktrees' explicit managed-cell custody item closes. Remaining caller/launcher and skew gates, independent Manager/service sources, maintenance and native adoption remain open.
- Review identified three concrete gaps: active cells awaiting explicit deactivation were rejected by deployment-owner resolution, the real-install fixture inherited Git routing variables, and redirected Python output could reject non-ASCII installation paths.
- Deployment owner reads now retain the existing active namespaced owner in `deactivation-required` on Python and both native adapters; separate mutation/activation checks are unchanged. Native publication, lifecycle-result and callback pipes explicitly use UTF-8. The new regressions first reproduced all three deactivation cases and the real Windows `cp1252` encoding failure.
- The real-install fixture now strips all inherited `GIT_*` values before Git writes and child launch. It deliberately poisons those inputs with a separate synthetic repository and verifies that repository's complete inventory is unchanged. Expanded real construction/callback cases use non-ASCII homes on all five platform/shell combinations; Windows Git Bash also exposed a callback extraction pipe needing the same UTF-8 correction.
- A PowerShell 5.1 fixture wrapper initially decoded its UTF-8 source as ANSI; its BOM correction is test-only. The expanded Windows PowerShell 7 matrix passes 90 cases with 28 platform skips, the corrected PowerShell 5.1 Unicode cases pass, and the expanded Linux custody/native-positive selection passes 74 with four platform skips. Eighty-three canonical deployment, wheel-closure and output-contract checks pass. These corrections do not enable host adoption or maintenance.
- A later review identified a namespace/cutover lock-order cycle between an outer management owner and standalone settlement or explicit modes. A focused overlap regression first reproduced the standalone caller holding cutover while waiting for the manager's namespace locks.
- All Worktrees publication paths now acquire namespace custody before service cutover. Explicit modes keep the same governance object and actual owners across construction and nested settlement, then release cutover before namespace custody. The overlap matrix covers ordinary settlement, repair, dev and dev-release with missing and explicit-standalone policy; nested borrowing preserves the original owners.
- The existing actual construction/repair/cutover race fixture now follows that same order and retains its bounded namespace-wait and serving-generation assertions. The deactivation-owner finding carried into the next review was already fixed; its original thread now names the exact corrective commit and regressions rather than prompting another redundant code change.
- The real Linux race exposed an additional exit-evidence gap: its portable Python build lacks `os.pidfd_open`, so read-only liveness fell back to PID existence and retained an already-exited, unreaped predecessor. The existing native pidfd could prove exit. Read-only liveness now reuses the same canonical libc adapter as identity-bound termination, preserving conservative behavior when native evidence is unavailable. Focused regressions first reproduced the missing-adapter path and cover both live and exited owners plus native-handle failures.
- The Windows race fixture's pause adapter initially replaced `subprocess.Popen` with a function, breaking a standard-library subclass import. It now subclasses the original process class; this is a fixture correction, not a production process-launch change.
- Actual construction also requires the Worktrees-retained real ZDD vendor tree to match the canonical fix, not merely a passing source-library test. The native race uses the same compact supervised home boundary as the managed-install fixture; a deeply nested node home reached the already-tracked native subprocess path limit. Its final serving assertions distinguish operational repair-slot version from the package version independently recorded by the deployment manifest.
- Final real construction/repair/cutover overlap passes on native Windows in 187.51 seconds and Linux in 97.26 seconds, including exact selected/serving generation, manifest, native health and retained predecessor evidence. The focused overlap/management matrix passes 33 cases on each platform. Real managed repair/callback flows pass on all five shell/platform combinations. Native exit-evidence regressions pass 31 cases on Linux and 18 with 13 platform skips on Windows; the real Worktrees vendor source matches canonical. These are bounded fixture results, not a host rollout or completion of the remaining source-selection, launcher, skew or maintenance gates.
- A subsequent review found the same exited-process class at the separate stdlib publication authority boundary: its conservative PID-existence probe accepted an unreaped manager. A real child first acquires the existing management lease, a live borrow succeeds, the child exits without releasing its receipts, and `waitid(..., WNOWAIT)` confirms exit while PID existence still succeeds. The new regression reproduced the unauthorized borrow without relying on mocked locks or a reused ticket alone.
- Publication purpose now pins positive native process birth evidence. Linux reads boot/start identity and nonterminal kernel state; Windows checks query/synchronization ownership and creation time through one handle. Borrowing repeats that identity check, rejects reused PIDs and unknown native evidence, and does not change conservative stale-lock probes or introduce another lock/dependency. The existing generator updates every standalone and packaged helper copy.
- The combined native-identity, custody and overlap selection passes 53 cases with five platform skips on Windows and 55 with three skips on Linux. It includes the real exited-unreaped-manager refusal, unknown/reused birth identities, refusal before management lock writes, and Windows handle/creation-time failure cases; successful live borrowing remains covered.
- A real Windows process query also reproduced DWORD truncation of an out-of-range PID. Native identity now rejects that value instead of proving another process's birth, with a regression against the current process's wrapped identifier.
- Birth-bound management purpose passes real construction and retained native callbacks on all five shell/platform combinations: Windows PowerShell 7, PowerShell 5.1 and Git Bash, plus Linux Bash and PowerShell. The subsequent DWORD bounds guard changes only invalid-identifier admission; the final focused selection covers that refusal and the valid native custody paths.

### 2026-10-21 - Revalidate after blocking cutover admission
- A further review found a concrete stale-validation window when the management owner exits while settlement waits for cutover. Without a native frontend, no later helper check prevented selector and manifest publication. A real management child and coordinated wait first reproduced that publication; repair, dev and dev-release already refused through their nested governance checks.
- Settlement now rechecks immediately after acquiring cutover, after readiness/retention preparation and before selector publication. Waiting does not renew borrowed authority. The existing retained transition settlement and rollback ownership remain unchanged.
- The expanded owner-exit/custody/overlap selection passes 57 cases with five platform skips on Windows and 59 with three skips on Linux. Seventy-nine focused settlement/rollback checks pass. Real managed construction and retained callbacks remain valid on Windows PowerShell 7 and Linux Bash after the new checks. These are fixture results, not source-selection integration, a host rollout or completion of the parent effort.
- A concurrent native installer origin-detection correction required human-output declaration reconciliation during rebase. Both native code changes were preserved and the declaration was recomputed through its canonical helper. The eleven opt-in installer resolution/source-origin regressions pass on each native platform; this is not a reason to repeat unrelated all-adopter qualification.

### 2026-10-21 - Explicit custody merged; launcher execution next
- PR #7489 merged as `777134cc41f0eb93ec910f367bea631dfcc66eb9` after exact-head Copilot approval with no open findings and successful required checks. Canonical completion reconciled the worktree to the merged development source.
- Re-read the Plan and Validation Plan. Worktrees' explicit managed-cell custody item is qualified, including birth-bound ownership, namespace-before-cutover ordering and revalidation after a blocking wait. This does not close the wider publication, hook or skew gates.
- The next slice covers Manager's native launcher execution of pre-launch and reconciliation plans, plus their planning/staging callers. Reuse the existing execution admission rather than introducing another policy parser or updater; preserve marketplace staging, unrelated bootstrap actions and standalone compatibility.
- Independent Manager/service source selection, source-backed maintenance and native adoption remain open. No installed policy, source binding, service, startup entry or cadence changed.

### 2026-10-21 - Launcher producer admission
- Worktrees' bootstrap-staleness planner now uses the shared installation-owned publication decision before emitting its own runtime update. Permitted updates retain explicit runtime phase and payload/root/context metadata; unrelated repository bootstrap actions remain executable.
- Background staging still downloads marketplace content. A payload change or independently detected venv drift can request runtime application only after the same publication decision; drift recovery cannot restore an action denied by policy. Healthy unchanged runtimes do not acquire a publication requirement.
- The real-helper regressions cover missing and explicit standalone policy, managed/invalid/unsupported/foreign refusal, custom bootstrap preservation, read-only runtime inventories and independent payload-change/venv-drift triggers. The first payload-trigger fixture changed an intentionally un-fingerprinted text file; it now changes the real plugin manifest without changing its package version.
- A probe-failure regression first reproduced drift recovery restoring an action after the stage's error handler refused it. The final application decision now preserves every recorded refusal. The complete producer/reconciliation/probe selection passes 119 cases on Windows and 113 with six Windows-only skips on Linux, under the existing bounded runner.
- This producer boundary is a separate coherent slice from execution admission. A previously prepared launcher plan still needs policy/root/context revalidation immediately before native execution; that remains the next explicit Plan item, not a claimed completion of Phase 3.
- Review exposed the shared verifier's ready-only status check: a validated namespaced cell awaiting explicit deactivation legitimately permits standalone publication with a present matching policy. New real-helper regressions first reproduced all three producer failures: pre-launch, payload-change staging and drift-only staging.
- The shared verifier now admits `deactivation-required` only with a namespaced context, preserving every existing identity, root, receipt and deployment-policy check. The producer matrix covers both legacy and deactivation-required scope; a forged legacy deactivation status still refuses. The final four-module selection passes 147 Windows cases and 141 with six Windows-only skips on Linux under the unchanged bounded limits.

### 2026-10-21 - Launcher producer admission merged; native execution next
- PR #7564 merged as `fdb4fd45633a2f7a725114803e67aa4bc864879c` after corrected-head Copilot approval and successful required checks. The carried-forward deactivation finding names the already-tested correction; its exact-commit reply and resolved thread retain that evidence. Canonical completion reconciled the worktree to merged development source.
- Re-read both Plan and Validation Plan. Only the producer-side launcher planning item closes. Next reuse the existing execution decision at native stage, pre-launch and reconciliation execution boundaries, proving a policy change after planning cannot invoke the installer while unrelated actions and compatible standalone behavior continue.
- Both the wider publication/hook/skew boundary and independent source/maintenance/native-adoption gates remain open. No host deployment, policy, source binding, startup entry or cadence changed.

### 2026-10-21 - Native cached-plan execution admission
- Added a read-only check on the existing reconciler CLI, reusing its canonical execution decision before repository discovery. Native wrappers invoke it before bootstrap/reconciliation runtime actions and before synchronous or detached staged-installer spawn; they do not implement a deployment-policy parser or another updater.
- Real-helper regressions cover a policy introduced after planning, changed payload/root/context, malformed input and old standalone helper compatibility. A present policy without capable helper support refuses; an engine missing the new execution check is a distinct explicit readiness warning, not silent permission.
- Native Windows PowerShell 7/5.1 and Linux Bash fixtures retain independent repository actions, progress/exit reporting, environment restoration, fresh replanning and quiet stdout. They cover managed policy, missing engine capability, malformed decisions, legacy unphased Worktrees actions and both staged execution modes without starting a refused installer.
- The retained-action fixture exposed two coupled PowerShell issues: a null environment assignment can leave an empty variable on newer .NET, and a single remaining argument could be scalar-unrolled before native invocation. Actual removal and an explicit remaining-argument array preserve absence and the complete argv on both PowerShell hosts.
- The final four-module engine selection passes 163 Windows cases and 157 Linux cases with six platform skips. Native launcher coverage passes 82 Windows cases with 42 platform skips and 50 Linux cases with 74 skips, under unchanged runner bounds. These results qualify this consumer boundary, not the remaining adopter hooks or a fresh host deployment.
- Next complete the remaining adopter hook/extension publication map and skew/readiness evidence before integrating independent Manager/service source selections. Source-backed maintenance and native adoption remain open; no installed policy, source binding, startup entry or cadence changed.
- Review identified the CLI front-door project gate preceding the new handler. Real neutral-directory entrypoint regressions first reproduced rejection before admission for both standalone and managed steps; the exact check now bypasses project resolution without changing ordinary reconciliation routing.
- Two additional native regressions reproduced PowerShell 5.1 recoding non-ASCII runtime identity in the JSON pipe and Bash mixing a peer's newly written staged environment with its earlier cached payload/root. The admission pipe now uses scoped UTF-8 with restoration, and Bash decodes the complete status once and reuses that same snapshot for admission and execution. Peer status replacement cannot splice another action's metadata into the admitted step.
- The final five-module engine/routing selection passes 341 Windows cases and 335 Linux cases with six platform skips. Native preparation plus existing launch/unwrap contracts pass 109 Windows cases with 42 skips and 77 Linux cases with 74 skips under unchanged bounds. Review, required provider checks and actual merge remain separate gates; neither these regressions nor the existing thirteen-adopter proof authorizes host rollout.
- Source CI correctly refused the changed pure routing module until its existing promotion-unit admission was refreshed. Added one no-effect routing case to that same closed test/import cohort and refreshed only the changed source/test hashes and exact node inventory; the packet budget, effect restrictions, production coverage targets and existing cases are unchanged. This is versioned admission maintenance, not a skipped check or pipeline retry.
- Local packet qualification then reproduced an actual collection-boundary defect: `--rootdir` alone allowed pytest to inspect unrelated ancestor entries on a mounted checkout before reaching the admitted nodes. A bounded collect-only comparison confirmed that the explicit source-root collection floor prevents that access. The canonical runner now sets that floor, and its existing real nested-collector regression checks it while retaining the no-production-call refusal. Repository-external test dependencies preserve the existing import-admission rule; no effect restriction or test selection is bypassed.
- The complete canonical packet passes all 323 admitted nodes from exact `7d590eca80cabd0edc077a3fc82b710748a9a1a4`, with coverage and verified supervisor cleanup. A later review identified staged `_direct` Worktrees repair being incorrectly narrowed to marketplace discovery; real staging-to-execution cases first reproduced missing-policy, explicit-standalone and old-helper failures. Staged self-update now repeats its own owning discovery while retaining cached payload/root/context comparisons and policy admission. Ordinary reconciliation and other plugins cannot borrow that direct fallback.
- The direct-stage correction passes 139 deployment/staging cases on each native platform. The native staged-execution selection passes 28 Windows cases with 20 skips and 14 Linux cases with 34 skips. It covers direct standalone repair, late managed refusal, unsupported old helpers with present policy, changed installation metadata, marketplace precedence and refusal to borrow staged discovery for another plugin.

### 2026-10-21 - Native execution admission merged; remaining adopters next
- PR #7576 merged as `644dc1c6f5a647e788dee281fcd64fe588e10350` after exact-head Copilot approval on `a5bd0f431f18a725a481384ae692a7b47dda2c27`, no unresolved findings and successful required checks. Canonical completion reconciled the worktree to the merged development source while preserving concurrent Manager callback changes.
- Re-read both Plan and Validation Plan. Only the cached native-launcher execution item closes; the whole publication boundary, retained-hook/skew evidence, independent source selection, maintenance and native adoption remain open.
- Next complete the remaining adopter hook/extension map and qualify admission before each actual staging/root/redirect write. The common bootstrap families and their real installers must be traced together: dispatching an installer alone does not prove a bypass, while an earlier staging write is a distinct boundary. Reuse the canonical decision and existing lifecycle custody, not another policy parser or updater.
- The source-backed maintenance extension still follows the Phase 3/4 prerequisites and the existing maintenance owner. No installed policy, source binding, runtime, service role, startup registration or cadence changed.

### 2026-10-21 - Logger earliest-write admission
- An actual managed first-stamp regression reproduced Logger's staging-directory creation before any deployment admission. Its native publication actions now enter the existing source-qualified lifecycle before staging when a bootstrap interpreter is available; borrowed children revalidate the live source/root/operation/purpose ticket. Explicit management composes with that same owner rather than a new updater.
- A manifest opt-in packages those existing helpers independently of catalog binding, retaining byte-identical command catalogs. Python-free default standalone stamping still uses the canonical native decision without adding an interpreter prerequisite.
- The denial matrix addresses default and explicit roots, all four publication actions and managed/invalid/foreign/unsupported policy. It retains old stage evidence byte-for-byte. Positive cases exercise actual standalone staging, authentic management reentry and forged-ticket refusal rather than mocked permission booleans.
- The existing native source-installation fixtures retain the new payload-local helper closure. Their Windows Logger selection passes 13 cases with two explicit full-install skips; this is not the all-runtime native transition.
- The complete three-module publication/generation/deployment selection passes 178 Windows cases with 55 platform skips and 166 Linux cases with 67 platform skips. Retained stamping/snapshot/provision/source-boundary coverage passes 22 cases with three full/platform skips on each native platform. The earliest-write smoke remains in the ordinary suite; the exhaustive denial matrix uses the explicit T3 portfolio rather than adding repeated process startup to every required PR run.
- After separating those lanes, the explicit native adopter matrix passes 39 Windows cases with 37 platform skips and 37 Linux cases with 39 skips. The focused PowerShell 5.1 lane passes seven cases, preserving the no-Python standalone and managed-refusal checks on the older native host too.
- The remaining bootstrap observability/reaping boundary, other adopters, skew/readiness evidence, independent source selection and maintenance/adoption gates remain open. No installed policy, service, source selection, startup entry or cadence changed.

### 2026-10-21 - Native admission compatibility and payload release
- Existing PowerShell 5.1 deployment writers emit a UTF-8 BOM. Real lifecycle regressions reproduced refusal both on an incumbent manifest and after a successful child rewrote it. The canonical deployment-manifest reader now accepts that legacy format through the existing stable-byte capture, duplicate-key rejection and digest cache; protocol receipts and ordinary `read_json` retain strict BOM-free UTF-8. Lifecycle callers retain an older helper's ordinary reader when the additive compatibility capability is absent.
- The new waiting supervisors initially retained a payload CWD or script descriptor, defeating the later self-stage handoff. Windows PowerShell now moves both native and provider CWD out before waiting; Bash re-executes a command-string shell before native Python, releasing the script descriptor even with MSYS's emulated native exec. Launch-project declarations travel as read context through the existing lifecycle CLI, not as deployment permission. Actual held-stage tests replace the original payload while installation is still running; Linux also checks the waiting process's CWD and open descriptors.
- Python-free staged probes now revalidate the original marketplace source through the canonical plugin-publication action, without deriving management authority from the origin hint. The same helper supplies the canonical addressed root before forwarding it, so relative, trailing-slash and not-yet-created roots do not become false refusals. Managed/invalid policy remains blocking.
- Windows Bash retains its documented launch-outside-payload readiness boundary. Its supported HOME launch passes the actual replacement test; the unsafe payload-CWD form refuses before staging rather than leaving an outer shell pinned.
- The narrow JSON/deployment/cache selection passes 47 cases on each platform with verified supervisor cleanup. Final lifecycle/deployment contracts pass 42 Windows cases with four platform skips and 46 Linux cases; the focused native PowerShell 5.1 adopter lane passes fifteen cases and Linux's ordinary adopter lane passes twelve. Existing stamping/snapshot/provision/source-boundary coverage still passes 22 cases with three full/platform skips on each native platform. Review and actual merge remain required before closing the bounded Logger item.

### 2026-10-21 - Launch context and fresh snapshot ownership
- The next review reproduced three distinct gaps: interactive PowerShell provider/native CWD divergence, lost owning-source evidence in a fresh stamped-snapshot process, and relative Python/Bash `PATH` selections changing after rerooting. These remain direct-installer corrections, not a completed Logger or Phase 3 verdict.
- Native launch capture now preserves the provider-relative project and addressed root, and retains the exact selected shell/interpreter before changing directory. Regression fixtures observe the actual forwarded root and selected Bash process, not only a zero exit status.
- Legacy snapshot publication captures the existing canonical read-only source resolution. The shared Python/POSIX/PowerShell source resolver revalidates its selected snapshot, plugin and normalized source identity without requiring the original publisher. The sidecar conveys no management permission; current deployment policy and lifecycle custody remain authoritative. Namespaced provenance remains separate.
- The complete adopter publication module passes 57 Windows cases with 47 platform skips and 51 Linux cases with 53 skips. This includes real stamp-to-command operation after publisher removal, explicit standalone/managed policy, and Python-backed/Python-free stamps. The canonical frozen-source and existing payload-source selection passes 33 Windows cases with ten platform skips and 43 Linux cases; its ten-case PowerShell 5.1 tamper matrix and eight native provider/fresh-command cases also pass.
- Retained source reselection passes thirteen cases on each platform using the actual canonical source-capture helper. Its fixtures now declare a real directory marketplace and distinguish published helpers from narrowly verified private recovery stages. Existing source-install/source-identity coverage passes 26/50 Windows cases, with four/three platform skips. Lifecycle command/stdio contracts pass 49/15 Windows cases and 48/11 Linux cases, with explicit platform skips. Deployment/adapters/vendoring/packaging passes 83 Windows cases with 29 skips; the Linux package-failure module passes 85 cases. Canonical vendor equality, all thirteen install contracts, bootstrap synchronization, module-size and changefile guards pass.
- A broader native snapshot matrix is not green: outdated extracted-installer fixtures, POSIX-only filename/replacement assumptions and a real installed cell-slot self-stage refusal are tracked in #7612. That separate qualification dependency remains an explicit open Plan item; focused direct-installer results do not close it.
- The first-use wrappers' existing boot trace, provision status and orchestration-lock writes are explicitly outside this direct-installer slice. Their own earlier admission gate is now a named open Plan item, not concealed by the snapshot publication assertions. No installed policy, source binding, service role, startup entry or maintenance cadence changed.

### 2026-10-21 - Logger direct-installer boundary merged
- PR #7598 merged as `286c020d8a5eb74cf69f67816534905435d47be1` after exact-head Copilot review reported no open findings and required checks passed. The accepted comment-shaped verdict satisfies the owner-authored review policy; author-generated inline reply reviews were not treated as independent verdicts.
- Re-read the Plan and Validation Plan after the actual merge. Only Logger's direct-installer admission item closes. Common bootstrap trace/status/lock/reaping admission is the next implementation slice; #7612 remains a separate native snapshot/cell-slot qualification dependency.
- Manager/service/plugin-content source separation, source-backed maintenance and installed native adoption remain open. This merge changes no local installation or maintenance enrollment.

### 2026-10-21 - Common bootstrap publication admission
- Added thin native adapters composing the existing canonical publication and legacy-entrypoint probes, rather than duplicating deployment-policy parsing. The four-member shared bootstrap family calls them before first stamping and before drift-reconcile reaping, lock writes or background dispatch. Healthy current runtimes remain a read-only no-op.
- Isolated process fixtures cover managed, malformed, foreign and future-version deployment policy; absent and explicit standalone behavior; helperless compatibility; failed or missing adapters; and unchanged standalone reconcile dispatch. Stale-process and background-launch paths use recording doubles, never host process termination.
- The actual separate hook expressions still invoke guidance and provider registration after bootstrap refusal. The Pull Requests admission wrapper remains intact. Tests capture its real child-process stderr rather than assuming an in-process console writer captures inherited handles.
- Split common bootstrap admission from generated first-use wrapper trace/status/lock admission so the latter cannot be silently marked complete by this bounded change. Both Plan items remain open pending their own reviewed implementation; #7612 remains the separate native snapshot/cell-slot qualification dependency.
- This closes a bounded part of the source-managed-runtime vision through the existing shared installation owner. It changes neither marketplace delivery nor local runtime sources, installed policy, service roles, startup configuration or maintenance cadence.

### 2026-10-21 - Common bootstrap boundary merged
- PR #7631 merged as `632f93d4bce28bd84ed9c4dc019c836b3629180d` after exact-head Copilot approval with no open findings and successful required CI. Canonical `pr-complete` reconciled the source worktree to the merged development branch without finalizing the active effort.
- Re-read both implementation and adoption Plans and Validation Plans. Only the four-member bootstrap item closes; the broader first-use, remaining adopter/skew, Manager/source selection, maintenance and native qualification items stay open.
- Next reproduce and gate Logger's generated first-use wrapper writes, including shared resolver trace emission and payload-command provisioning. Preserve healthy runtime invocation and standalone/cell behavior; do not treat the legacy-only bootstrap adapter as admission for a namespaced cell.

### 2026-10-21 - Logger early first-use publication slice
- The exact retained-snapshot reproduction now compares the complete addressed runtime tree on managed refusal, including root metadata, traces, provision status and locks; it no longer excludes the writes being qualified.
- Generated primary wrappers require a declared deferred-trace resolver contract, resolve healthy runtimes first, then compose canonical snapshot publication and legacy-mode admission before cold writes. Payload-local and auxiliary commands retain installation-context validation and probe publication against that already-selected root rather than forcing a namespaced cell through legacy admission.
- Shared native resolver modes preserve ordinary callers by default, suppress durable traces for read-only probes, and defer cold traces for ready-only probes. Admitted cold paths resume normal tracing; healthy managed invocation retains tracing without running an installer. Missing admission support or older deployed resolvers return explicit readiness failures rather than assuming unsupported helpers suppress writes.
- Native Windows retained-snapshot cases have exercised all three command surfaces, Python-backed and Python-free stamps, standalone provisioning, managed refusal, healthy managed reuse and missing/older helper refusal. Selected-cell cold cases separately prove managed no-write refusal and standalone installer dispatch with unchanged legacy state and preserved native failure status. Linux parity and the remaining publication gates are being qualified before review.
- Both native platforms now pass the retained-snapshot and selected-cell cases, shared resolver mode/selection checks and existing Logger dispatch/snapshot compatibility regressions. The release fixture's catalog now names the release artifact it actually stamps, and verifies frozen source evidence before removing that publisher, rather than depending on an unrelated development-payload entry.
- Review exposed an equivalent-path regression in both native gates: a literal trailing separator differed from the probe's canonical root despite an allowed standalone decision. Retained-snapshot payload and auxiliary cases reproduced the refusal on both platforms. Legacy dispatch now adopts the initial status response's canonical root; namespaced dispatch retains its separately validated root. Trailing-root refusal/provisioning, selected-cell and existing healthy-dispatch regressions pass on Windows and Linux.
- The Logger-specific item remains open until its reviewed change merges. Other generated-wrapper families, the broader Phase 3 map/skew gate, #7612, Manager integration and native adoption remain independent open objectives. No live installation policy, source checkout, runtime, service, startup entry or maintenance cadence is changed by these isolated fixtures.

### 2026-10-21 - Logger first-use boundary merged
- PR #7641 merged as `62fa19a4b5ea4e3dde5311282c187579295e2130` after exact-head Copilot approval with no open findings and successful required checks. Canonical completion reconciled the source worktree to merged `dev`; the effort remains Active.
- Re-read both Plans and Validation Plans after the merge. Only the Logger-specific first-use item closes. Managed retained-snapshot and selected-cell refusal preserves the complete addressed tree; standalone provisioning and healthy managed dispatch remain covered on Windows and Linux, including equivalent trailing-separator roots.
- The next bounded slice is the shared Pull Requests/Vault generated-wrapper owner and Vault payload gates, followed by CodeSpaces' bespoke wrappers. Their actual adopter paths need their own no-write and retained-behavior evidence; the new shared resolver modes alone do not qualify them.
- The broad publication, remaining hook/skew, native snapshot dependency #7612, independent source selection, Manager maintenance and installed adoption gates remain open. This merge enables no live configuration, source binding, startup entry or cadence.

### 2026-10-21 - Shared generated-wrapper and Vault payload admission
- Actual Pull Requests and Vault stamps reproduced cold boot-trace/provision-lock writes before deployment admission. The canonical shared template now requires deferred-trace support, resolves healthy runtimes first and composes the existing snapshot publication/legacy decision before cold writes. Unsupported helpers remain explicit readiness failures.
- Retained snapshots needed the same canonical source evidence Logger already published. That publisher now belongs to the shared installer engine, with Logger retaining a thin delegate; no new provenance schema or deployment permission was introduced. Shared wrappers capture evidence only under retained runtime snapshots. Vault stamp publishes its selected payload before its wrapper rather than missing the first source capture.
- Vault payload gates retain validated context selection and probe publication at that addressed root before trace, status or lock writes. The actual legacy and cell regressions use opposing legacy policy, canonical trailing-separator roots and native installer failure status; namespaced invocation never falls back to legacy admission.
- The twenty-case actual-adopter selection passes on native Windows PowerShell 5.1 and Linux. It covers managed, invalid, foreign and future policy; standalone installer dispatch; healthy completed-runtime dispatch; and missing/older helper readiness. Recording downstream installers keep these admission fixtures separate from host service control.
- Existing offline Vault snapshot/provision/compatibility cases pass on both platforms. They exposed a fresh Windows slot being mistaken for the historical `.venv`, causing an unnecessary stop attempt; activation now inspects the actual legacy path. The cache-only payload fixture now explicitly requires its optional cryptography dependency on POSIX, matching the existing cache test portfolio rather than asserting success from the documented no-op mode.
- Retained Vault invocation, Pull Requests template/lock, Logger snapshot compatibility and extracted Logger source-publication regressions pass on both platforms. Review and merge remain required before the bounded item closes. CodeSpaces' bespoke family, the broader publication/skew map, #7612, independent sources, Manager maintenance and native adoption remain open.

### 2026-10-21 - Shared first-use review corrections

- Review exposed SSH as another shared-template adopter: its immutable snapshot IDs include a timestamp and process ID while its stamped marker remains the package version. Additive legacy-source receipt version 2 binds those two identities separately; all canonical readers retain version-1 support and reject mismatched or non-string selection fields. Actual SSH stamps now join the first-use admission matrix rather than relying on a template proxy.
- Removed implicit source capture through the old `payload-dir`. Stamps explicitly supply prepared candidates before wrapper publication, then replace selected snapshots and redirects only after that publication succeeds. This preserves previous snapshot contents, source evidence and markers on missing-resolver and command-destination failures. POSIX Vault retains its ordinary publisher-pointer stamp, and namespaced/source-qualified publication keeps its existing ownership mechanism.
- Native failed-restamp tests exposed the Windows PowerShell watchdog returning a null child exit code after waiting. Retaining the child handle before waiting preserves the actual failure; the byte-identical prologue is updated across its thirteen install/init adopters, with production-fragment and actual-installer regressions. This correction does not change launch flags, deadlines, process lifetime or service control.
- The bounded shared/SSH/Vault actual-adopter matrix passes on Windows PowerShell 5.1 and Linux, including preservation of the previous selection on failed restamps. Version-1/version-2 source-reader identity, live-policy and malformed-selection cases pass through Python and both native adapters. Existing Windows shared-writer and POSIX conditional-stamp failure injections also pass. Review, merge and the remaining parent effort gates are still open; no host installation, deployment policy or maintenance cadence was changed.
- Retained SSH provisioning also exposed dependency admission inspecting the unrelated caller project's working directory instead of the selected snapshot. Package installation now scopes that directory to its source and restores the caller on PowerShell. The real POSIX retained-source provision passes after this correction. The existing network-backed PowerShell first-use test still hits its own short containment budget on both platforms, tracked by #5379; this is not a passing complete SSH provisioning claim or a native-adoption waiver.

### 2026-10-21 - Shared first-use boundary merged; bespoke family next

- PR #7657 merged as `ac0481a3cc441c9fda2d783a606406bc7f475eff`. Copilot review `5606916684` on exact head `2353f5e63c4ade48bf72ef5b00eb6048d833ac27` reported no open issues; required CI passed. The accepted clean-comment owner verdict satisfied the documented review gate, and the managed merge and `pr-complete` both succeeded.
- A concurrent Dispatch installer update required regenerating only its derived native-output hash during rebase. Range comparison confirmed the remaining patch series was unchanged; all canonical copies, thirteen install contracts and affected-consumer changefiles remained consistent. Final source-reader type qualification passes 54 Windows cases with 27 POSIX skips and 81 Linux cases.
- Re-read the Plan and Validation Plan. Only the bounded shared Pull Requests/SSH/Vault wrapper and Vault payload item closes. CodeSpaces' bespoke wrapper/payload admission is next; retained hook/skew mapping, #7612 and #5379, independent sources, Manager maintenance and actual native adoption remain open. No deployment, enrollment, cadence or service role was enabled by this merge.

### 2026-10-21 - CodeSpaces selected-installation first-use admission

- The bespoke global commands now require the canonical ready resolver and snapshot publication probe before cold trace, status or orchestration-lock writes. They retain PowerShell's provisioning mutex, command fallback and module invocation; POSIX no longer selects an arbitrary marketplace installer. Both use their installed root for configuration and provisioning.
- The payload gates distinguish installation-context validation from deployment admission, probe the selected legacy or namespaced root, and set that root's environment before invoking the installer. A refused cold invocation must leave both selected and unrelated legacy roots unchanged.
- Windows stamping captures source evidence in its private candidate before command publication and snapshot replacement. Actual restamp failure tests cover missing resolvers and command-directory collisions, including a previous snapshot without a sidecar.
- Native argument qualification exposed legacy PowerShell retokenizing an already-correct incoming quoted argument. The module launch now encodes the argument array only for legacy native argument passing; empty, single and metacharacter-bearing argument cases exercise the actual generated command, with payload-local coverage too.
- Qualification remains bounded and split by adopter/host rather than extending the supervisor deadline. The separate native long-path defect is already tracked in #7010 and remains an explicit readiness goal; ordinary-depth results do not close it. Review and actual merge are still required before the bounded CodeSpaces Plan item closes.
- The final CodeSpaces actual-adopter subset passes 23 cases on PowerShell 7, 23 on Windows PowerShell 5.1 and 23 on Linux. The remaining 42 shared-adopter cases pass on Windows; Linux's earlier full 59-case run covered that unchanged subset before the six additional CodeSpaces cases. Plugin-owned resolver/binstub guards pass five native Windows cases with two explicit POSIX skips and all seven Linux cases. Install-contract, documentation consistency, installer-engine synchronization, scoped Ruff and whitespace gates pass.

### 2026-10-21 - CodeSpaces first-use review corrections

- Exact-head review identified UTF-8-without-BOM generation of a command containing the literal installation root. The generated PowerShell command now carries a BOM for Windows PowerShell 5.1; command fallback and metadata encoding remain unchanged. An actual custom-root stamp and healthy dispatch exercise non-ASCII paths, verify the BOM bytes and reject an unrelated ambient root.
- POSIX conditional callers could suppress `errexit` inside the wrapper writer. Resolver directory creation, staged copying and atomic publication now propagate failures explicitly before command publication. Both installers reject resolver-directory destinations rather than treating a copy into that directory as success.
- Actual failed-restamp fixtures cover a file at the resolver-bin path and a directory at the resolver-file path across all four shared/bespoke adopters. They preserve selected snapshot contents, selector bytes and previously published command bytes.
- The complete CodeSpaces subset passes 25 Windows PowerShell 5.1 cases. PowerShell 7 passes 42 CodeSpaces/restamp cases, and Linux passes 31 CodeSpaces/resolver-failure cases. All retain the existing supervisor limits and verified cleanup. Installer contracts, documentation consistency, canonical engine synchronization, scoped Ruff and plugin-owned binstub guards pass.
- This correction still requires a fresh exact-head review and actual merge. The bounded CodeSpaces item, broader publication/skew coverage, independent source selection, Manager maintenance and native adoption remain open. No host deployment, source binding, startup enrollment or cadence changed.
- A further exact-head review confirmed both corrections and identified numeric PowerShell argument objects entering the payload-local JSON bridge unchanged. The payload gate now converts the forwarding array to strings, matching ordinary native invocation; actual integer-variable calls cover legacy/cell selection under managed and standalone policy. Complex quoted-input fixtures are scoped to CodeSpaces rather than requiring its new transport from three unchanged shared wrappers.
- Windows PowerShell 5.1 passes all 29 CodeSpaces cases plus the three unchanged-adopter healthy argument cases. The affected payload and shared-healthy selection passes fifteen cases on PowerShell 7 and fifteen on Linux. The Windows 5.1 test process derives its own standard module paths rather than inheriting a PowerShell 7 module path; no host module installation or persistent environment change is involved.
- The next review identified Windows `provision` reusing the full installer and replacing user-global commands with a selected cell's target. Provisioning now shares construction/activation but omits global command-directory creation and publication, matching POSIX's runtime-only action. Actual payload-gate-to-installer regressions replace only expensive construction and service effects, retain the real dispatch and publishers, and preserve existing global command bytes and timestamps through successful legacy/cell provisioning.
- The successful native fixture also reproduces the already-tracked legacy self-staging boundary in #7612. Its global-command preservation result does not qualify complete native cell isolation; that separate Plan item remains open. Managed cold refusal still requires complete selected/unrelated-root no-write assertions.
- An incomplete selected POSIX slot exposed inherited `errexit` aborting inside the canonical resolver before publication admission. The payload caller now treats slot misses as resolver control flow while an actual resolver error remains an explicit readiness failure. The existing fake POSIX resolver advertises the required trace contract, preserving its healthy cell-selection test.
- Windows PowerShell 5.1 passes 36 CodeSpaces/shared-healthy cases, including actual runtime-only provisioning. PowerShell 7 passes all sixteen affected payload cases; Linux passes seventeen payload/error cases after the resolver correction. Complete plugin-owned payload/binstub modules pass thirteen Windows cases with three platform skips and all sixteen Linux cases. Canonical synchronization, installer contracts, documentation, Ruff and whitespace gates pass. Fresh exact-head review and merge remain required.

### 2026-10-21 - CodeSpaces first-use boundary merged; remaining routes next

- PR #7687 merged as `55b2129f5cfc9157849cb21fb981581ce393e0f0` after Copilot approval `5608096090` on exact head `77dc0882464832d65a9e910d43296edd2211fa5a`, no open findings and successful required checks. Managed merge and `pr-complete` succeeded; the source worktree is on the actual merged dev commit.
- Re-read the Plan and Validation Plan. Only the bounded CodeSpaces generated-wrapper/payload item closes. The shared policy, broader session/command publication map, retained-hook/skew proof and all source-selection, maintenance and native-adoption gates remain open.
- The next bounded slice qualifies the remaining bootstrap families before classifying source-identity or context checks as deployment admission. Command-side first-use routes remain a separate named goal. Reuse canonical decisions and preserve independent provider/guidance work; do not infer a product defect from an unexecuted map alone.
- Keep #7612's reproduced successful-installer legacy-staging boundary, #7010's native long-path qualification and #5379's retained-source first-use budget explicit. Global-command preservation and isolated ordinary-depth fixtures do not close those goals or authorize a host change.

### 2026-10-21 - Remaining bootstrap qualification baseline

- Extended actual bootstrap fixtures to the six remaining families rather than classifying an unexecuted source map as enforcement. Managed first-stamp cases reproduce installer dispatch for Bridge, Dispatch, Containers and MCP on both platforms, Budget Guidance on Windows and Machines on POSIX. Several drift paths also reach the recording process seam before any deployment-policy refusal.
- These are failing baseline results, not completed qualification. Materialized fixtures retain real command metadata; their recording process lookup preserves the actual host process identity. Machines' distinct stamp/probe behavior still needs targeted qualification, and the POSIX Budget Guidance fixture cannot yet provide its missing native admission-helper path. Resolve those differences before treating all failures as the same product defect.
- Reuse the existing canonical decision before first-stamp and drift reaping/lock/dispatch writes, preserving standalone positives, healthy reuse and independent hooks. Command-side first-use, skew and later source/maintenance/native gates remain separate open items.

### 2026-10-21 - Remaining bootstrap admission implementation

- Bridge, Dispatch, Containers, MCP, Machines and Budget Guidance now compose the canonical publication decision before first-stamp or drift side effects. Machines also probes its selected active cell independently of context validation; its PowerShell adapter invokes the same validator in-process instead of starting another shell merely to read status.
- Budget Guidance is registered with the existing installation-context vendor owner, so its self-contained payload carries byte-identical native helpers rather than discovering a sibling runtime. Separate no-Python-PATH cases qualify managed refusal, standalone stamping and healthy reuse on both platforms.
- Actual-adopter qualification passes 117 cases on Windows PowerShell 5.1 and 117 on POSIX, with platform counterparts skipped. The matrix retains standalone stamping/reconciliation, healthy managed and malformed-policy reuse, helperless compatibility, independent hook execution, and selected-cell refusal without touching the unrelated legacy root.
- The fixture now represents each family's real healthy markers and two-stage/worker-owned launch evidence. It caches only immutable materialized input; every case still owns an independent adopter copy and home. POSIX interpreter isolation preserves stdin arguments, and publication snapshots still compare the entire isolated home. No timeout or containment limit was increased.
- The retained common-family, canonical-vendoring and stdin-isolation selection passes 74 Windows and 75 POSIX cases. Plugin-owned bootstrap regressions pass across the affected families; Windows also runs both real Machines two-stage bootstrap hosts. Budget's existing shell test now selects native Git Bash rather than sending Windows fixture paths to the WSL launcher.
- Review and merge remain required before the bounded bootstrap item closes. Command-side first use, broader helper skew, independent source selection, Manager-owned maintenance, the tracked native readiness goals and actual installed adoption remain open. No live source, deployment policy, service role, startup enrollment or cadence changed.

### 2026-10-21 - Stock-shell publication admission compatibility

- Review identified the mandatory native probe imposing the management helper's Bash 4.4 floor on otherwise supported stock macOS Bash 3.2 bootstrap paths. Read-only status and publication/mode probes now use the same canonical resolver and policy decisions on Bash 3.2; installation-management actions retain their existing Bash 4.4 floor.
- The compatibility path preserves exact JSON typing, provenance, installation ownership, environment, opened-file identity and digest revalidation. It uses indexed validation-cache keys and one reserved input descriptor only where Bash lacks associative arrays and dynamic descriptors; modern shells retain their native implementations. No per-plugin policy parser, Python prerequisite or permissive policy fallback was introduced.
- Actual Bash 3.2 qualification passes 145 cold/drift and canonical policy-parity cases, including all ten bootstrap families, missing and explicit standalone policies, managed/malformed/foreign/future refusals and a governed namespaced root. Budget Guidance retains a PATH without Python. These are real old-interpreter results on POSIX, not native macOS platform qualification.
- An expanded fixture initially removed Python from families that already require it and therefore correctly skipped their bootstrap. Those cases now preserve each adopter's existing prerequisites while selecting Bash 3.2 for every nested shell; only Budget Guidance's existing Python-free contract removes Python.
- The complete modern POSIX bootstrap matrix passes 191 cases with 191 counterpart skips across cold/healthy/helper-skew, selected-cell, and drift/independent-hook cohorts. Two aggregate invocations exceeded the existing supervised execution budget without assertion failures; they are not passing evidence. The successful cohorts retain the same per-case and supervisor limits.
- Shared safety qualification exposed an extracted-test fixture that no longer located the canonical guest helper, then reproduced watchdog status being swallowed by the new descriptor-opening function. The fixture now retains the full canonical bundle, and watchdog expiry propagates before another open attempt. Both modern and old Bash refuse a FIFO within the existing five-second deadline; the old shell also ignores an ordinary inherited `BASHPID` value rather than treating it as native process identity.
- Final canonical policy/identity qualification passes 113 cases; the complete digest and POSIX guest suites pass another 23. The extracted PowerShell fixture now also locates its canonical companion, and guest tests assert returned per-invocation diagnostics rather than an unrelated host stderr stream. All final cohorts retain structured runner results and verified cleanup. Existing BSD-userland path cases pass on both old and modern shells.
- Extracted PowerShell probes use UTF-8 BOMs for literal non-ASCII paths and launch hidden children on Windows. Their three digest regressions pass on native PowerShell 5.1 with a Unicode temporary root and on POSIX PowerShell, with verified cleanup; this is fixture portability, not a change to the PowerShell admission implementation.
- The bounded bootstrap item remains open pending review and merge. Broader first-use/skew, source selection, maintenance and native adoption gates are unchanged; no installed runtime or policy changed.

### 2026-10-22 - Durable execution core and public update adapters

- The reviewed #7758 slice now implements a durable pending-only coalescing queue, kernel-backed transaction ownership, a finite caller-independent worker, streamed output and exact canonical outcomes. Manager and engine public update commands, staged acquisition and hook `reconcile-plugins --apply` retain their canonical executors beneath that owner. Supervised debugging uses the same authority, not an inline bypass.
- Nested update calls borrow a live authenticated participant capability. Entry consumes ambient transport; only explicit update-to-update handoffs export the context-local capability. Ordinary installer/service descendants do not inherit update authority. Interrupted transactions fence later execution; explicit settlement requires a different native kernel boot identity under the same lock and never replays or manufactures success.
- Native Windows qualification passes 59 core cases, 120 Manager cases, 332 engine cases with two existing platform skips, and 46 test-selection cases. The production-lifetime lane destroys the submitting Job, observes surviving canonical workers and durable nonzero outcomes across two cycles, and observes zero new visible consoles or foreground changes; contained controls still retire. Synthetic executors perform no live installation.
- Release preview materialization and an isolated non-editable package installation resolve the complete driver/process/lease closure without source imports. The packaged read-only recovery-status command returns its empty JSON result without home-state writes. Deleted vendored process modules were byte-identical to the canonical library; their single canonical dependency removes conflicting local package URLs without changing process implementation.
- Independent read-only review of the implementation found no high-confidence correctness, concurrency or behavior defects. Installer, documentation, vendored-library, scoped lint and module-size gates pass. Native Linux and full clean-room qualification remain open because no supported isolated Linux engine was available; Windows and synthetic prior-boot evidence are not substitutes. No actual reboot or host rollout was performed.
- Review and merge of this implementation remain required. Native direct plan-action execution, remaining suite-adopted first-use publishers, Linux qualification and the broader Phase 3/4/source/maintenance/adoption gates remain open. This independently landable milestone does not complete the parent effort or replace the owners of #6990 and #6928.

### 2026-10-22 - Execution boundary portability and context qualification

- Added macOS native boot-session UUID admission and canonical process-helper support using kqueue parent-exit observation, native unreaped-child `waitid` and libproc group census. Native layouts and PID-count semantics follow the platform definitions; unavailable, malformed and wrong-identity observations refuse rather than allowing an inline installer. Every canonical process-library consumer receives a changefile. Native macOS tests are present but were not executed on the available Windows host.
- Trusted runner declarations now project each request's approved context before product startup and project routing, clearing absent keys inherited from the worker's launching caller. The engine retains its original invocation directory across the queue boundary. Manager explicitly snapshots its already-consumed provider command rather than silently selecting another installed engine.
- Actual separate-process engine fixtures queue the same project name under two installation scopes, route each to its own anchor and retain each caller's marketplace context. A separate queued Manager fixture retains a consumed provider that differs from its marker-selected stand-in.
- Driver failures preserve ordinary stage-update stdout silence and replace previous successful provisioning or stage status with an explicit failure. The existing canonical status writer supplies atomic publication; a pending request is not reported as a completed installation. Installer fixtures now use the canonical process-library path after deduplication.
- Final bounded Windows results include 76 driver cases with actual caller-Job loss, 122 Manager cases, 345 engine cases with two platform skips, four installer owned-child cases, and eighteen final JSON/status cases. The canonical process-helper selection passes 122 cases with eight platform skips, including native Windows controls. Linux provider CI passes the complete admitted unit packet after exact source/test admission was refreshed with eight pure diagnostic-routing cases; native Linux/macOS lifetime and actual reboot qualification remain open.
- A secondary status-write failure now remains explicit on stderr while preserving the original JSON failure envelope; the twenty-case final JSON/status selection includes unwritable status destinations. Further queued-context qualification retains explicit engine-command overrides in both adapters' identity and startup-clearing sets, and freezes resolved marketplace context before the first submission rather than deleting a newly transported value in nested participants.
- Actual Manager fixtures queue two different overrides and an absent override behind a worker with a fourth selection; all three execute their own provider and cannot coalesce incorrectly. The actual engine-to-Manager-to-engine fixture retains caller settings rather than distinct anchor settings through two native Windows cycles, with zero surfaced consoles or foreground changes. The affected engine/JSON selection passes 37 cases and the Manager/update/auth selection passes 123; installer and daemon behavior remain synthetic or unchanged.

### 2026-10-22 - Execution core and public adapters merged; native callers next

- PR #7811 merged as `9c70ad31b355de63ea76e42c367bb0cbd3670974`. Current-head Copilot review `5611925627` reported no open issues, all nine concrete review threads were addressed and resolved, and required CI passed. The owner-authored clean-comment verdict satisfied the documented review policy; no approval or native-platform evidence was inferred from it.
- Re-read the Plan and Validation Plan. Durable requests, public update/staging/hook-apply adapters, same-authority debugging, Windows caller-loss/headless qualification and the bounded migration documentation are landed. Ordinary native plan-action execution, remaining suite-adopted first-use publishers and native Linux qualification remain unchecked; this is not completion of Phase 4b or the parent campaign.
- Advanced the continuing worktree to actual merged `dev`, retaining the published head before dropping its already-merged private commit series. No host runtime, source binding, deployment policy, service, startup entry or cadence changed. A merge to `dev` is pending release and authorized rollout, not an installed driver.
- The next bounded slice routes the native launchers' direct staged/pre-launch/reconciliation actions through this same execution owner. Preserve their existing admission checks, progress and standalone behavior; do not replace the inner staging/watchdog work or create another updater.

### 2026-10-22 - Native launch requests and nested-Job qualification

- Replaced native PowerShell and Bash plan-command execution with fixed bootstrap/staged request phases and the existing queued reconciliation executor. The worker derives canonical actions, rechecks publication permission and retains the real installer deadline/output/outcome. Staged identity binds acquisition bytes and the discovered payload fingerprint; supplied status argv/environment cannot become a command protocol.
- Foreground bootstrap and reconciliation precede staged submission. Bootstrap defers only Worktrees when a staged request will own it, retaining independent repository services. Default staged submission detaches; blocking mode waits on the same owner. An older engine's missing request surface is an explicit failed-command outcome, never a raw installer fallback.
- Actual venv-redirector caller loss exposed Windows accepting a breakaway launch while retaining an ancestor Job lifetime. The canonical process helper now uses its existing outside-Job broker before launching from a Job-contained caller. Query errors refuse explicitly; ordinary contained tests and deliberate contained launches keep their existing suppression. The real staged CLI now survives submitting Job cleanup across successful and nonzero installer cycles.
- Native Windows broker qualification passes two production/contained cases, each with two cycles, zero surfaced consoles/foreground changes and pinned cleanup. The complete shared process suite passes 421 cases with 29 explicit platform/opt-in skips; the native caller/progress/unwrap selection passes 59 with 11 platform skips. The launch-phase/JSON selection passes 32 with the separate production-lifetime case intentionally opt-in.
- Full shared-library validation also exposed Python 3.14's shielded-future error reporting after caller cancellation. Waiting without cancelling the native sampling future preserves cancellation, accounting and exact exception identity without orphaned shield errors; the existing success/failure cancellation regressions and complete library suite pass. Independent review found no significant issues in either the main migration or this named fix-forward.
- Installer, vendored-library, headless/tracking/asyncio source guards, scoped lint and whitespace checks pass. Final native deployment regression and publication remain required. Native Linux/macOS and fresh Linux clean-room evidence remain unavailable as previously recorded; no actual host install, reboot, source policy, service role or startup enrollment was changed.
- Remaining first-use publishers and the parent source-selection, maintenance and installed-adoption gates stay open. This slice does not complete Phase 4b or take over another contributor's open staging/watchdog work.
- Version-skew follow-through routes native reconciliation through the new fixed phase too: an old engine can already implement `reconcile-plugins --apply` without a durable owner. Missing `launch-update` capability now refuses every native publication phase, including reconciliation, without executing that older foreground path. Actual old-engine launcher fixtures retain visible failure and no raw fallback.
- Review follow-through preserves optional adopted-CWD project discovery and sends native installer output through the canonical runner's streaming descriptor instead of its buffered callback. Actual bootstrap/reconcile producer fixtures observe a flushed child line before allowing completion, and the two-cycle staged caller-loss fixture checks the live durable log before releasing its installer. Optional empty Bash project arrays use the existing nounset-safe expansion for stock Bash compatibility. Final phase/JSON/status selection passes 42 cases with one separate native opt-in skip; native launch/progress passes 63 with 13 platform skips, and seven canonical apply/environment compatibility cases pass.

### 2026-10-22 - Native execution adapters merged; standalone foundation selected

- PR #7860 merged as `aa6f7e47423db6d80f455f1ef8b3514ae488b1a6` after Copilot approval `5613818727` on exact head `b2f4dfa4f6b054966e0f6c42931e54d0c3e210cd`, successful required checks and resolution of both concrete review threads. The blank review generated by the submitter's thread replies was not treated as Copilot evidence.
- Re-read the Plan and Validation Plan. Only the bounded native launcher item closes; first-use, native Linux, source selection, maintenance and installed adoption remain open. The worktree continues rather than finalizing the parent effort.
- Managed pull-forward refused a deleted merged-PR branch, and the completion helper safely auto-aborted replaying the squash-merged private series. Preserved the exact published tip, verified the merged commit on fetched `dev`, excluded only that fully published series and reconciled an up-to-date worktree. No unpublished work, other contributor branch or stash was changed.
- First-use wrappers can run before Python or any plugin runtime exists, whereas the current driver lives inside an executing Python runtime. The operator explicitly chose the standalone foundation rather than a standalone-genesis exception. The next slice reviews that cold-start boundary before implementation; it composes existing installer/runtime and process-ownership mechanisms rather than adding a second plugin installer, recurring scheduler or required Manager dependency.
- No host runtime, deployment policy, startup enrollment or source binding changed. The merged native adapters still require release promotion and authorized rollout before they are installed.

### 2026-10-22 - Standalone foundation amendment reviewed

- PR #7913 merged as `4f6387f24e258c12726a9ae7cfd0349ae21c83ee` after Copilot approval `5618621858` on exact head `0304a5e3c53bf048ad33916245c5181abe5a7726`, no review findings and successful required checks. The managed completion helper reconciled its already-merged proposal onto actual `dev`.
- Re-read the Plan and Validation Plan. Only the foundation-amendment review item closes. The next execution step inventories existing prerequisite acquisition, immutable runtime assembly, native admission and source/command provenance before selecting and qualifying the smallest standalone foundation.
- Cold construction, first-use migration, native qualification and the parent source/maintenance/adoption gates remain open. No implementation, installed runtime, service, policy or enrollment change is claimed from this proposal merge.

### 2026-10-22 - Python-free native admission foundation

- Inventoried the canonical prerequisite helpers (`Ensure-Uv`/`ensure_uv`, pinned archive verification and signed/uv interpreter acquisition), Python-owned retained construction, immutable runtime completion/retention, source receipts and current launch admission. The existing Worktrees admission requires an interpreter; it cannot qualify a Python-free cold host.
- Extended the existing process/operation libraries with packaged Windows-native admission, strict owned-child execution and the exact `OperationLock` byte protocol. No Manager dependency, plugin executor, queue, scheduler, installed runtime or publisher change is introduced.
- The native lifetime fixture removes Python and uv from the child PATH, closes the real submitting Job and proves retained canonical lock custody through the same detached operation. Two production cycles survive; two contained controls retire with their caller. The canonical window/foreground observer detects no surfaced console or focus transition. This is not a claim that Python was absent from the host or that a fresh foundation was installed.
- Initial native qualification exposed two real platform constraints: WMI refuses `CREATE_NO_WINDOW`, and detached stock PowerShell can exit before evaluating its body. Reused the broker with a headless console and authenticated actual-worker handshake instead of accepting a successful spawn as execution evidence. Owned prerequisite writers use atomic Job-list admission, not a suspended-spawn/assignment gap.
- Native lock/owned-process qualification covers Python/native exclusion, native close/crash release, same-process hardlink contention, invalid paths, descendant settlement and timeout. Foundation packaging, fixed source admission, durable native interruption fencing, immutable construction, active-request upgrade, Linux/macOS execution and fresh-host clean-room proof remain open. First-use wrappers stay unchanged until those gates pass.
- Review of the native prerequisite slice caught drive-relative/current-drive-rooted path acceptance and uncertain CIM activation being reported as confirmed cleanup. Corrected the whole exposed native process boundary, including direct C# owned execution, and added all-field rejection plus actual-activation/lost-response controls. The native operation lock already required normalized absolute paths; explicit regressions now retain that boundary too.
- A later review retained a real PowerShell Core constructor incompatibility despite an approval verdict, so the open finding still blocked merge. Restricted daemon admission explicitly to stock Windows PowerShell 5.1 before initialization, removed the unsupported Core fallback, and tested actual Core refusal. Added real pre-acknowledgement controls for verified worker termination, identity-mismatch non-termination and an unconfirmed launcher; cleanup now explicitly requires a successfully pinned worker handle before any termination.

### 2026-10-22 - Native admission prerequisites merged; construction next

- PR #7927 merged as `258a4dd71b4f667bb24f6abb921ea276b633d32f` after exact-head clean Copilot Comment `5620602108` on `b5164307d9f432f15dfd2b7979fba7b05915d7b5`, zero open findings, all four threads resolved and successful required checks. Applied the repository's owner-authored clean-Comment policy and provider-confirmed Maintainer completion authority; no CI bypass or additional review exception was introduced.
- Re-read the Plan and Validation Plan. Only the separately qualified Windows native prerequisite closes. The standalone foundation, publisher migration, complete native qualification and broader source/maintenance/adoption gates remain open, so this worktree continues.
- The final native packet passed 28 cases with four explicit POSIX-only skips, including real Core-host refusal and destructive-cleanup controls. Actual wheel builds retained every declared native asset byte-for-byte. No runtime, source binding, service, enrollment or host policy changed; merge is not release or rollout.
- The completion helper safely aborted replaying the squash-merged three-commit series. Preserved its exact published tip, confirmed the merge on fetched `dev`, excluded only the fully published series with no unpublished commits, and reconciled the active worktree normally.
- Independent follow-up probes in disposable targets acquired the canonical pinned uv and a newly downloaded managed interpreter with no discoverable Python/uv commands, and exercised native-owner interoperability with the existing participant protocol plus forged/stale refusal. These are construction inputs, not a shipped foundation, first-use migration or fresh-host acceptance.
- Next: bind a fixed, self-contained foundation source closure, durable native interruption state and retained immutable construction to the same execution authority. Keep plugin work behind the ready driver and preserve the existing source and maintenance owners.

### 2026-10-22 - Foundation progress joins the existing execution fence

- Implemented the credential-free native construction record under the existing execution root and exact kernel lock. Its source/operation/boot/outcome model is bounded and fail-closed; missing progress with existing foundation artifacts is not a legacy fallback.
- Preserved SQLite schema 2 and existing requests. Ordinary queue seams now observe native interruption before accepting or executing installation, and an executor cannot report success after leaving native construction uncertain. Recovery requires explicit different-native-boot evidence and never replays work.
- The targeted state/core/recovery/process packet passed 107 cases, including bounded malformed/duplicate/oversized input refusal. One actual Windows PowerShell owner holds the canonical byte lock while Python accepts pending work; killing that exact owner makes subsequent wait/drain retain the native fence and execute nothing. This is state/custody integration, not complete cold construction or a fresh-host proof.
- Fixed source closure, native progress production, retained immutable construction, upgrade and first-use publisher migration remain open. No ordinary caller has been switched or host runtime changed by this contract layer.
- Review of #7972 identified two admission/publication defects before merge. Foundation admission now rejects unsettled SQLite work, including an actually killed queue owner, while allowing only the exact currently executing transaction to enclose construction. Native progress readers share deletion; write-through replacement retains custody across bounded reader-sharing retries and reports persistent contention explicitly.
- The corrected focused packet passed 116 cases with three explicit POSIX-only skips. A deterministic Windows reader overlap reproduced the actual replacement refusal and verified completion after the reader closes; a persistent-conflict control preserves running progress and raises rather than returning success. Foundation construction and fresh-host acceptance remain open.

### 2026-10-22 - Foundation progress contract reviewed

- PR #7972 merged as `263f76fd4cf9e0cf4f5cb4a8695121ac68c710ef` after current-head Copilot approval with no open issues, both review threads resolved and successful required CI. The managed completion path applied the authorized Maintainer exception to the separate CODEOWNER vote, not a CI or finding bypass.
- Reconciled the parent checklist for this bounded progress/fence prerequisite only. The standalone producer, immutable source closure/construction, first-use publishers and native Linux/fresh-host qualification remain open under #7758; the parent effort is Active.
- Next: implement the fixed standalone foundation source bundle and native construction producer using this same execution root/custody, canonical prerequisite acquisition and retained immutable runtime construction. No runtime rollout, source-policy change or maintenance enrollment occurred.

### 2026-10-22 - Fixed foundation construction participant

- Added the fixed three-package standalone source inventory and a construction participant requiring the live borrowed witness and matching admitted progress before staging. It uses canonical frozen, non-editable, dependency-governed construction, installed health, process retention and link-free selection; no native admission producer or first-use publisher is enabled.
- Registered the library with the existing standalone Python installer-helper sync owners. Its helper assets are generated byte-identical copies, not a second installer implementation. The new size-baseline entry records only the existing canonical runtime helper's copied line count; it does not widen that canonical module or exempt new foundation logic.
- Focused source/admission/custody tests exercise changed references, extra packages, source substitution, missing support and refusal without custody. Actual Windows construction reached the canonical build boundary but could not fetch the setuptools wheel metadata because the artifact endpoint's TLS handshake failed. TLS policy and dependency seasoning were not weakened; an isolated copy of the existing backend cache did not resolve that missing registry metadata.
- Added a bounded manual Windows/Ubuntu foundation portfolio to the existing installation qualification workflow, separate from its unchanged scheduled/default context portfolio and from required unit-only CI. Use that fresh hosted lane to qualify real construction, reuse and installed source independence before marking this slice qualified. Native Python-free genesis and the parent completion gate remain open.
- Draft #7989's initial hosted qualification ran real construction/reuse/source-removal/retained-worker assertions on exact `6592de659d69a21f0fbf69fa6d3ce05ba9b05ecd`: 57 Windows cases passed; Ubuntu passed 54 with three explicit Windows-only skips. The required guard correctly refused a dynamic runner label; changed the manual lane to two literal hosted jobs rather than weakening runner policy. Extended the real fixture with failed changed-source candidate preservation; qualify that updated head separately before readiness.

### 2026-10-22 - Standalone construction participant reviewed and qualified

- Final hosted run [39870616292](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39870616292) exercised exact `774a5877132cce2074f1ded3ee9cf425082dc35a`: Windows passed 57 cases; Ubuntu passed 54 with three explicit Windows-only skips. Actual construction, unchanged completion reuse, failed changed-source health preserving the prior selection, source removal, installed health and retained finite-worker execution all ran. The unrelated exhaustive context portfolio did not run.
- PR #7989 merged as `3d27573e3e6f0d29e7b67f10de2ff8dde5a38a52` after exact-head Copilot approval with no findings and successful required CI, using the authorized Maintainer exception only for the separate CODEOWNER vote. Managed worktree reconciliation preserved this continuing effort.
- Re-read the parent Plan and Validation Plan: this closes only the bounded fixed-construction prerequisite. Preinstalled hosted Python and a fresh per-test cache do not qualify Python-free native genesis, caller loss during genesis, native concurrent cold admission, first-use publishers, active-request upgrade, macOS or host rollout.
- Next: implement the native foundation progress producer and owner-bound participant bridge over the existing native kernel custody, then join canonical prerequisite acquisition to the qualified construction participant. Keep the existing queue/source/publication/maintenance owners and the parent effort Active. No source policy, host runtime, recurring enrollment or live deployment was changed.

### 2026-10-22 - Owner-bound native participant bridge

- Added the Python-free Windows owner side of the existing participant v1 protocol. Its supported constructor accepts the actual native operation object, exact execution root and operation identity; it cannot substitute a PID, flag or another path. Owner-thread issuance retains a read-only assertion of the original kernel handle, identity and path. The IPC thread cannot acquire, release or transfer custody.
- Closing that handle revokes existing and future participants, including after a new owner reacquires the same path. Same-user named pipes retain bounded pickle-free JSON/HMAC exchange and simultaneous explicit participants; the existing Python consumer consumes the inherited transport before package subprocesses. The key is ephemeral, never a progress field or persisted receipt.
- Local native qualification: 28 combined witness, existing transport and foundation-input tests pass, plus 10 operation-lock tests under the canonical containment supervisor. Expanded malformed controls found and fixed the native `InvalidDataException` hierarchy case instead of allowing an unhandled handler failure. Ruff, canonical physical-copy agreement and the 13-plugin install contract pass.
- The existing manual foundation portfolio now includes the new bridge controls; ordinary scheduled/default adapter qualification and required unit-only CI remain unchanged. This slice adds a packaged asset to installed foundation health. Hosted construction will recheck actual wheel/health behavior before merge. Native Linux/macOS cold admission, fresh-host genesis, caller-loss construction, first-use adapters and the broader source/maintenance/adoption gates remain open.
- Documentation impact: library ownership/API and validation sections describe the exact new native boundary; no adopter entry point is enabled. This closes part of the reviewed execution-owner intent without creating an installer, scheduler, Manager dependency or permission to deploy.
- Hosted run 39876072544 passed against the initial bridge head: Windows 69 tests, Ubuntu 54 tests with 15 explicit Windows-only skips, including actual immutable construction and installed packaged-asset health. Review then identified pipe-instance exhaustion retiring the witness and premature disposal of task-owned pending-I/O handles. Fixed both before merge: bounded 16-client admission refuses excess without revoking existing custody, and task-owned wait handles survive asynchronous cancellation/completion. Fourteen native controls pass locally, including 16 overlapping genuine clients, excess refusal/capacity recovery and actual stalled-handshake timeout with a healthy participant retained. The updated head requires its own hosted qualification and review.
- Hosted run 39877620575 also passed at the IPC-fix head. The next review found a cross-language spelling mismatch for an accepted directory root ending in a separator: .NET preserves it while Python `Path` removes it. The factory now removes trailing directory separators without crossing drive/UNC anchors before issuing/exporting the witness. Fifteen native controls pass locally, including the real trailing-root Python join. Final-head review and hosted qualification remain required before merge.
- Review approved the root-fix head, but hosted run 39878691002 exposed the native progress fixture's 15-second startup bound before its first custody receipt (71 tests passed; that receipt timed out while the child remained live). Its bounded startup allowance is now 60 seconds, distinct from the unchanged held-operation and test/process limits, with an explicit compiler-loading diagnostic and captured native log on deadline. No assertion, lock contention, interruption fence or failed qualification is skipped; the changed head must pass the real lane before merge.
- Hosted run 39879348725 passed at the readiness-fix head: Windows 72 tests, Ubuntu 54 tests with 18 explicit Windows-only skips. Review's remaining malformed-handshake concern was reproduced with `{"__type":{}}`: the .NET serializer raises `MissingMethodException`, a `MemberAccessException`, rather than its usual JSON argument exceptions. Deserialization now converts that malformed-metadata exception class into a connection-local invalid-frame rejection. Twenty-three native controls pass locally, including numeric overflow, recursion and top-level/nested serializer metadata, with genuine custody retained after every malformed peer. This is a real fix-forward, not dismissal of an unlocalized review summary.

### 2026-10-22 - Native custody bridge reviewed and merged

- #8005 merged as `6d430a0ef56109a4a0e855fd94730e9660b112d8` after an actual current-head Copilot approval with no open issues, all required CI and resolved review threads. Final hosted run 39881112573 exercised the approved `c25ea9fea0ebcca42b3076035e2bde9973f68554` head: Windows 80 tests passed; Ubuntu 54 passed with 26 explicit Windows-only skips. Actual immutable construction and installed native assets remain healthy; no failed qualification was bypassed.
- Re-read the parent Plan and Validation Plan. Only the bounded native participant bridge item closes. The standalone producer, Python-free genesis, cold concurrency/caller loss, upgrades, native Linux/macOS admission, first-use publishers and the broader source/maintenance/adoption gates remain open. The worktree and parent effort continue; merge is not release installation, runtime rollout, recurring enrollment or pilot authorization.
- Next: implement native construction progress and pre-staging source admission under the exact same execution root/lock, then connect canonical prerequisite acquisition and the already-qualified fixed builder. Preserve explicit refusal for unsupported/malformed prior state and queue/worker obligations rather than inventing a second installer or bypassing an existing executor.

### 2026-10-22 - Python-free Windows progress and source admission

- Added the packaged native progress API under actual `NativeOperationLock` custody. It emits and reads the existing version-1 foundation record, queries the real kernel boot identity, hashes the same fixed unlinked three-package inventory, and publishes flushed write-through replacements with bounded sharing retries. Completion checks the admitted record and source digest; failure preserves the canonical code, and uncertain settlement retains the interruption fence.
- Cold-only admission refuses any existing SQLite queue or WAL/SHM/journal sidecar without opening, rewriting or rotating it. Queue-aware upgrades remain with the established Python authority; this avoids another SQLite provider and never assumes that a free lock settles abandoned plugin work. Native hashing binds source bytes; the already-qualified fixed builder still owns manifest/dependency validation before staging.
- Local qualification: 66 focused native/protocol/input tests pass. Actual stock PowerShell with a Python-free PATH produces records consumed by Python, a differential corpus covers strict schema/duplicate/type/size/number/trailing-content behavior, Unicode inventory order matches Python, and real two-process custody excludes a competing cold caller. Further controls cover source substitution, linked inputs, missing artifacts/support, reader-sharing publication failure, real queued states, and native owner loss fencing pending Python work. Core PowerShell compilation, focused Ruff and the 13-plugin install contract pass.
- Documentation impact: updated the library API/boundary and installed native-asset health. The existing opt-in hosted foundation portfolio includes these controls without enlarging required unit CI or its default scheduled adapter lane. Hosted installed-package qualification and current-head review are required before merge. This realizes the next part of the reviewed owner intent; no acquisition, first-use publisher, native Linux/macOS producer, rollout or enrollment is enabled, and the parent remains Active.
- Hosted run 39886358923 passed at the initial progress head: Windows 91 tests, Ubuntu 54 with 37 explicit Windows-only skips, including actual immutable construction and installed native-asset health. Review then identified two concrete reader differences: nested missing roots raised `DirectoryNotFoundException`, and a leading serializer `__type` field became an unvalidated XML attribute. Both were reproduced before fixing. Missing-path observation now checks existing ancestors without creating absent ones, still refusing linked or file ancestors; parsing rejects unexpected root attributes. The 66-test packet passes with the exact leading-metadata and nested-absence regressions. The corrected head needs its own review and hosted qualification before merge.
