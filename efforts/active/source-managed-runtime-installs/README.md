# Source-Managed Runtime Installs

- **Slug:** `source-managed-runtime-installs`
- **Repo:** copilot-extensions
- **Branch(es):** independent, serial per-slice PRs against `dev`
- **Created:** 2026-10-16
- **Status:** Active
- **Vision:** [Plugin Service Model](../../../visions/plugin-services/README.md#managed-runtime-deployment), [Runtime Assembly](../../../visions/plugin-services/runtime-assembly/README.md#selected-development-source-assembly), [Installer](../../../visions/installer/README.md#independent-runtime-source-selection), [Clean-room Validation](../../../visions/clean-room-validation/README.md#independent-source-installation)
- **Umbrella issue:** #7028
- **Sub-issues:** #3905 (existing source-materialization gap), #7029 (source selection and immutable generations), #7030 (deployment boundary and adapters)
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
generations. This campaign extends those building blocks, rather than treating
editable development claims as clean source deployments.

The [source-owned release vision](../../../visions/release-pipeline/README.md)
keeps the verified development commit as the release identity. Generated main
commits, materialized payload digests, package versions, profiles and dependency
locks describe different facts; no new competing artifact authority is needed.

## Request

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
reusing Agent Machines maintenance where supported. The
[maintenance slice](maintenance.md) proposes the portable integration and
validation; it must clear review before implementation.

The [inception record](inception-transcript.md) retains the operator's words,
with private host identifiers redacted. Implementation choices below marked
**agent-recommended** are not additional operator requirements.

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
- [x] Qualify Worktrees' native bootstrap and payload-local early first-use
  publication gates, retaining standalone reconciliation and healthy dispatch.
- [x] Qualify Worktrees' cell-installer pre-staging and retained-backend
  publication gates using existing governance and lock custody.
- [ ] Authenticate explicit managed-cell installation through the existing
  genesis/install custody across native staging, candidate handover and callbacks.
- [ ] Keep adapters thin and enforce drift-compatible service interaction without implicit runtime replacement.

### Phase 4 - Integrate Manager-owned runtime source selection
- [ ] Maintain a separate installation source with main, stable/exact pin and opt-in merged-dev selection resolved once per run.
- [ ] Keep service-source selection independent of Manager self-update and Copilot plugin-content selection.
- [ ] Route the selected source through the same assembly, readiness, activation and observable update-outcome path.
- [ ] Preserve exact selection across retries and source failures; never fall back to an ambient checkout or marketplace payload.
- [ ] Qualify Manager's own clean-source, immutable non-editable installation and recovery independently of the selected service-runtime source.

### Phase 4a - Maintain selected source through existing update ownership
The [maintenance slice](maintenance.md) owns the detailed proposal and evidence
matrix; no schedule or cadence is enabled by this plan.

- [x] Land the portable source-backed maintenance intent and implementation plan through review before adding that capability.
- [ ] Extend the existing Manager update path and Agent Machines maintenance owner for bounded source health, selected-ref synchronization and canonical installation; do not add a parallel updater.
- [ ] Preserve independent Manager/service/plugin-content selections, configured roles and exact per-run source identity across scheduled operation and retries.
- [ ] Prove unchanged-source reuse, a merged-source advance and explicit dirty/unavailable/overlapping/failed-candidate outcomes on Linux and Windows.

### Phase 5 - Prove assembled use and native Windows readiness
- [ ] Run the assembled runtime path in a fresh WSL-hosted clean-room container from a clean merged dev commit.
- [x] Qualify every declared installer adopter against that exact merged commit with frozen scenario inputs, successful runner exit and source-independent installed command evidence; retain each adopter's separate report.
- [ ] Exercise real commands and service interaction after source-checkout removal, including marketplace/runtime skew and non-deployment hooks.
- [ ] Prove native Windows source installation, generation selection and hook-deployment separation before a consented runtime-only pilot.
- [ ] Update owner documentation and record actual serving-generation evidence, not only successful copies or install markers.
- [ ] Prove every installed service/tool in the adopter's declared inventory, including Manager, rather than treating a canary or all-adopter installer tests as the native transition.

### Completion and scope control
- [ ] Resolve all Plan and Validation Plan items; record or transfer remaining discrete work to a named tracked objective before marking Done.
- [x] Deferred to `#7031`: implement exclusive local-checkout sourcing of Copilot plugin content after the runtime-only campaign.
- [x] Deferred to `#7250`: **Agent-recommended follow-up, operator-approved tracking:** preserve legacy Python-free redirect sets through a separate native publication mechanism; do not add a bootstrap interpreter prerequisite.

## Validation Plan

- [ ] **Source admission:** reject dirty, worktree-backed, unmerged or substituted source; preserve the exact resolved commit throughout a run.
- [ ] **Materialization:** actual installer imports the complete vendor closure from a non-editable install with no source-checkout dependency.
- [ ] **Identity:** identical inputs reuse unchanged completion bytes; different commits or assembly/dependency/profile inputs cannot overwrite a completed slot.
- [ ] **Selection:** installed binstubs and native resolvers execute the exact completed selected generation, including hash-qualified dev sources.
- [ ] **Failure:** incomplete/failed candidates remain unselected and preserve the previous healthy runtime and redirects.
- [ ] **Policy:** real hook and first-use paths do not publish runtime copies or redirects in managed mode; their non-deployment behavior still runs.
- [ ] **Skew:** newer-plugin/older-service and older-plugin/newer-service interactions work within declared capabilities or return precise readiness outcomes.
- [ ] **Source modes:** main tracking, stable/exact pins and dev tracking converge through one source-assembly path without moving midway through a run.
- [ ] **Maintenance:** the existing owner runs bounded, observable source health/sync/install cycles at the selected cadence; unchanged input is reused and source/candidate/overlap failures preserve healthy runtimes and edits.
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
