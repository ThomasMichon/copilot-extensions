# Seasoned Dependencies and Transactional Installs

- **Slug:** `seasoned-dependencies-and-transactional-installs`
- **Repo:** copilot-extensions
- **Branch(es):** One managed coordinator branch; sequential proposal and implementation PRs
- **Created:** 2026-10-13
- **Status:** Active
- **Vision:** [plugin-services](../../../visions/plugin-services/README.md): seasoned external dependencies and immutable, failure-safe runtime construction
- **Umbrella issue:** #6476
- **Related issues:** #1181 (incomplete-slot recovery); #5472 (same-version immutability)

## Guiding Intent

External dependencies must have been released for at least seven days before
being declared or selected for installation. Installers respect the adopter's
configured feed rather than bypassing it. A failed dependency installation is a
failed candidate build, never a partially successful runtime publication.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Coordinator | Proposal, shared policy, integration, PRs and rollout | Managed contribution branch |
| Declaration evidence | Manifest and configured-feed eligibility audit | Bounded read-only evidence task |
| Windows evidence | Windows installer failure and publication seams | Bounded read-only evidence task |
| POSIX evidence | POSIX installer failure and publication seams | Bounded read-only evidence task |

## Coordination

- **Topology:** One coordinator-owned implementation branch and sequential PRs.
- **Host (owns PRs):** Coordinator.
- **Delegates:** Evidence only; implementation ownership is assigned explicitly.
- **Handoff:** Compact cited evidence returns to the coordinator; delegates do not
  mutate Git, installed payloads, running services or adopter configuration.
- Existing owners of #1181 and #5472 retain their distinct objectives. This
  effort closes only the overlapping requirements its own implementation proves.

## Context

A recent dependency-minimum update (#6362) requires a package version absent
from a configured feed. Public release availability alone does not establish
feed eligibility: a proxy's artifact upload timestamp may be later than the
public release timestamp, and a real seven-day resolver cutoff can reject an
otherwise available version.

Existing implementation owners are
[`libs/installer-engine`](../../../libs/installer-engine/),
[`libs/versioned-runtime`](../../../libs/versioned-runtime/) and
[`libs/installation-context`](../../../libs/installation-context/).
The [install contract](../../../docs/install-contract.md) already requires
completion-gated selection, immutable completed slots and provenance-aware
cleanup. Their existence does not prove every shell installer has enrolled its
construction lifetime.

## Request

Operator input, in sequence:

> We need to ensure that if any dependencies fail to install, we don't install
> the slot; don't leave it in a bad state. Help make the fixes to package versions
> so everything can install cleanly.

> We need to adjust our package version dependencies so be compatible with the
> [configured package feed].

The bracketed substitution removes private deployment context without changing
the feed-compatibility requirement.

> Essentially, copilot-extensions needs a rule that any declared dependency
> needs to have been released for at least a week.

The operator selected **declarations-and-installation** when asked whether the
seven-day rule also applies to versions selected by runtime installers, rather
than only to declared minima. The operator confirmed this effort's slug.

## Plan

### Phase 1 - Reviewed policy and scope

- [x] Land this proposal and the corresponding standing vision intent through
  the repository's review gate before implementation.
- [x] Establish a seven-day rule for external build, runtime, development and
  selected optional dependencies; retain an explicit exemption for attributable
  repository-owned source dependencies, not their external dependency closure.
- [x] Identify all package-manager and installer entry points that need the
  policy, including the repository's npm tooling. Unknown release metadata must
  not count as eligible.

### Phase 2 - Declarations and resolved installation

- [x] Adjust incompatible version floors to allow week-old artifacts from the
  configured feed, preserving Python/platform support and existing upper bounds.
- [x] Add declaration-age enforcement and dependency-update cooldowns without
  hardcoding any adopter's feed or identity. Weekly scheduling alone is not a
  release-age gate.
- [x] Apply a bounded, fail-closed seven-day cutoff to actual dependency
  resolution, including transitive and isolated build dependencies. Honor a
  stricter cutoff; refuse weaker or unsupported bypass paths.
- [x] Cover UV, any retained pip paths and npm installation consistently.
  Unsupported age enforcement must fail explicitly rather than silently fall
  back to an unrestricted resolver.

### Phase 3 - Failed construction never publishes

- [x] Wire failure cleanup through canonical shared primitives and synchronize
  every affected independently shipped installer on Windows and POSIX.
- [x] Fail the build if any selected dependency fails, including a selected
  extra; do not convert that failure into success by retrying a reduced
  capability in the same partially populated environment.
- [x] Publish completion, active selection, manifests and service cutover only
  after the selected dependency closure and health gates succeed.
- [x] Remove only the failed build's owned, incomplete candidate. Preserve
  completed same-version slots, the prior healthy runtime, unrelated builders,
  selector/retention ownership and installation-cell receipts. Never rename a
  built venv as a shortcut that leaves embedded paths invalid.
- [x] Surface cleanup errors and keep any unreclaimed candidate nonselectable;
  process interruption must not turn an incomplete directory into a runtime.
- [x] Refresh explicit editable development installs through distinct, claimed
  generations, preserving the previously working development environment on
  dependency failure.

### Phase 4 - Release, rollout and conclusion

- [x] Update authoritative operating documentation and add changefiles for
  every affected plugin.
- [ ] Land implementation through real review and required CI, then verify the
  promoted release's exact contents rather than relying on commit ancestry.
- [ ] Run the authorized unified updater and prove clean dependency resolution,
  completed-slot selection and healthy retained/cut-over services.
- [ ] Resolve every Plan and Validation Plan item, journal the outcome, archive
  this effort, release its completed binding and finalize the managed worktree.

## Validation Plan

- [x] Unit-test the exact 604800-second boundary, future/unknown timestamps,
  yanked or incompatible artifacts, local-source exemptions, stricter cutoffs
  and forbidden package-manager bypasses.
- [x] Exercise the real resolver against a controlled index with old, new and
  missing-timestamp artifacts, including a transitive and isolated build
  dependency. Exercise npm's actual admission path with controlled metadata.
- [ ] Run native contained Windows and POSIX installer failures after partial
  dependency writes; prove nonzero exit, no completed/reusable failed slot,
  unchanged prior runtime/selectors and no service cutover.
- [x] Prove same-version retry succeeds from a clean candidate and that failure,
  contention, interruption and cleanup denial cannot alter a completed or
  differently owned slot.
- [ ] Exercise fresh installation without prior runtime state, plus update with
  a live retained healthy runtime. Record clean-room evidence or an explicit
  unavailable-lane reason; do not substitute mocked helpers for real lifecycle
  boundaries.
- [ ] Run targeted tests, touched-code lint, install-contract/fan-out guards and
  required CI. Keep the required lane focused and put exhaustive matrices in
  the existing full portfolio.
- [ ] Audit declared minima and prove actual installation through the configured
  feed with the seven-day cutoff, without modifying or bypassing that feed.
- [ ] Verify the deployed candidate's completion and service health, and persist
  rollout evidence before declaring the objective complete.

## Proposal

Extend the plugin-services vision with a dependency-seasoning floor and make
its existing immutable-runtime guarantee explicit for failed construction.
Implement through the existing shared installer, resolver and ownership seams;
do not introduce a new service, installer control plane or private feed policy.
This is a proposal, not a claim that those mechanisms are already deployed.

## Journal

### 2026-10-13 - Development-install decision

- The operator selected immutable development generations rather than refreshing
  the single claimed `versions/dev` environment in place. Each refresh retains
  editable source iteration but constructs its dependency environment beside
  the working generation; dependency failure leaves that generation selected.
- Extended the standing plugin-services intent before implementing this branch.
  Existing legacy `versions/dev` environments remain readable, not rewritten.
  The original numbered-release rollback target remains claim-owned across
  repeated refreshes.

### 2026-10-13 - Proposal

- Captured the operator's dependency, seven-day installation and failed-slot
  requirements; the configured-feed substitution is the only redaction.
- Claimed #6476 and completed bounded declaration, feed-timestamp, Windows and
  POSIX evidence passes. A real cutoff probe showed why public package release
  dates alone cannot establish configured-feed installation eligibility.
- Implementation has not started; this proposal must clear review first.

### 2026-10-13 - Reviewed intent landed

- #6479 merged after an approving Copilot review and both required checks.
- Synced the coordinator branch onto the merged proposal before implementation.
- The remaining objective is declaration and resolver enforcement, transactional
  construction, promotion and verified rollout; the proposal is not completion.

### 2026-10-13 - Policy and construction implementation

- Restored the incompatible FastAPI minimum, added seven-day native UV defaults
  to the canonical projects and update cooldowns, and implemented declaration
  admission, including UV resolution overrides and build metadata.
- The live Python declaration lane passed. The complete public npm declaration
  query is unavailable on this host because its TLS handshake fails under the
  default, OS-trust and TLS-1.2 paths. The required public CI lane will exercise
  the full audit; this is not an eligibility exception or a registry fallback.
- Real controlled UV resolution selected the older transitive dependency and
  isolated backend, refused missing timestamps and transitive URL escape, and
  proved the exact boundary behavior. UV's millisecond, exclusive cutoff is
  intentionally conservative; no epsilon admits younger bytes.
- Real npm admission checked the thirteen locked configured-registry artifacts
  without changing the feed or lock; controlled native installs refused young
  and unknown releases before download and installed the seasoned closure.
- Canonical owned-candidate cleanup and the Index cell lifetime are implemented.
  Legacy installer enrollment, complete integration, release and rollout remain
  open; isolated API tests do not establish adoption by those remaining callers.

### 2026-10-13 - Integrated construction and pre-submit review

- The focused installer portfolio passed across all thirteen runtimes:
  668 tests passed and 104 platform/optional-lane tests skipped. Native
  Windows failures preserve prior selection and reclaim owned incomplete
  candidates; the native POSIX lane remains required CI evidence.
- Nine root policy, materialization and release-artifact test modules passed:
  582 tests passed and two skipped. Independent engine and runtime fan-out
  checks passed, including every new standalone companion resource.
- The snapshot test now separates publication and dependency-content identity
  into bounded real PowerShell invocations without weakening its timeout or
  immutability, ordering and self-contained-payload assertions.
- Added a release changefile for all eighteen affected plugin and standalone
  owners. No version field was hand-edited.
- Independent pre-submit review found four release-validator provisioning
  commands retaining the forbidden dependency-resolution bypass. Removed that
  argument while retaining hash pins, binary-only resolution and explicit
  validator feed selection; the contract test now submits each actual
  workflow argument sequence to the canonical admission policy.
- Release, complete public-feed/OS CI evidence and authorized live rollout
  remain open. Local public npm TLS and native Linux/macOS unavailability are
  not policy exceptions and do not license declaring the effort Done.

### 2026-10-13 - Concurrent-base reconciliation

- Reconciled the implementation with the current development branch rather
  than dropping its new install-transaction locks, Codespaces module-launch
  health gate, telemetry library or SSH machine-transport dependency. Retained
  upstream workflow retirement and unrelated module-ceiling tightenings.
- Candidate cleanup settles before the outer Windows install lock is released.
  SSH submits the complete local source graph as one seasoned batch and still
  resolves the plugin closure afterward; neither stage can use the former
  dependency-resolution bypass or unrestricted pip fallback.
- Added native seasoning defaults to the newly introduced telemetry project.
  The current live Python audit covers 260 declarations with zero violations;
  the declaration and standalone-runner contracts passed 158 tests, with one
  unavailable-lane skip. Inventory assertions derive coverage from the actual
  canonical portfolio rather than freezing its package count.
- Completion-contract validation exposed a real Worktrees activation bug:
  the existing caller requires Boolean success, but the canonical completion
  adapter is deliberately output-free. The Worktrees wrapper now returns true
  only after held completion succeeds, without changing the shared adapter's
  output contract.
- Updated the completion fixtures to drive actual held construction and
  companion resources, including malformed/read-failed protocol refusals.
  Independent native integration passed all 50 completion cases and the
  remaining 218 Worktrees installer cases, with 13 platform/optional skips,
  in separate focused invocations under unchanged budgets. Both Windows
  PowerShell hosts passed; Git Bash is compatibility evidence, not native
  Linux or macOS evidence.
- Rebased SSH installer tests passed six cases with eight platform skips.
  The development-generation failure/retry check passed its native Windows
  case; the POSIX case remains required CI evidence. All fourteen changed
  PowerShell and Bash scripts parsed successfully.

### 2026-10-14 - Public review and native integration

- Opened #6598; its first native Linux/Windows lanes exposed additional
  installer fixture gaps. The first public guards lane passed, including
  declaration admission, governed artifact builds and the Python 3.10 floor.
  That initial audit did not yet include workflow-helper npm declarations;
  the expanded all-ecosystem audit remains a required subsequent-head check.
- Addressed all five concrete review observations in source: inline index
  values cannot exempt a following direct URL; SHA-1-only SRI cannot skip
  strong artifact verification; root workflow resolutions use the canonical
  adapter; policy triggers include both omitted implementations; and the
  declaration inventory includes `.github/actions`.
- Extended the existing npm installer for explicit locked project directories
  and selected module health imports. Real controlled CLI tests cover strong
  and SHA-1 registry metadata, complete locked closures, and young/unknown
  refusal before download. All 29 npm policy tests passed.
- Actual consumer validation found three coupled resolver defects not covered
  by metadata-only mocks: `--quiet` suppresses UV's settings projection;
  `--force-reinstall` conflicts with the appended reinstall alias; and Windows
  discovery can return multiple UV applications. The settings probe now
  ignores only display suppression while installation remains quiet, aliases
  normalize to one mandatory reinstall, and discovery selects one application.
  All 55 policy tests passed, including real quiet/offline resolution and
  native multiple-application discovery.
- Rebuild validation also exposed the missing Windows junction fallback on
  Python versions without `Path.is_junction`. Cleanup now uses the native
  mount-point tag without traversing the target; 18 admission tests preserve
  the leased directory and outside sentinel.
- Workflow trust remains unchanged: privileged helper loading uses the
  workflow definition's exact trusted commit. Whole-tree promotion advances
  the new helper and its workflow calls together; no untrusted checkout or
  seasoning bypass was introduced to bootstrap the change.
- Review revalidation, complete required native CI, promotion and authorized
  rollout are still open. Passing local or individual public lanes does not
  constitute release or completion.
- Independent combined validation closed the remaining snapshot and launcher
  gaps: Vault passed 14 cases with nine platform skips; Logger passed 25 with
  four platform skips. Vault's shared stamped fixture separates source
  disappearance, actual first-use provisioning and disabled-self-provision
  compatibility launch without raising any per-item deadline.
- Touched Python lint, the thirteen-plugin install contract and canonical
  runtime/resolver fan-out checks passed. The contribution base advanced while
  these checks ran; reconcile the actual conflict before publishing this
  review-fix round, then obtain fresh required CI and review.
- Reconciled the Bridge immutable-update change without discarding its
  fingerprint, reuse or prune-before-publication guarantees. Held construction
  now begins under the install lock before any build or service drain. The
  actual admission fixtures passed 40 cases with 38 platform skips; completion,
  failure ownership and host contracts passed 52 with four platform skips.
- A subsequent real review identified early install-lock release on POSIX
  signals. The canonical Bash adapter now routes `INT`/`TERM` through exit
  settlement before chaining outer cleanup, covering every installer rather
  than patching only the reported caller. Added real native-POSIX signal/lease
  ordering tests; those require the public Linux lane and are not claimed as
  locally executed Windows evidence.
- The complete public all-ecosystem declaration gate passed with 263
  declarations and zero violations; the governed npm admission also passed.
  The same CI run correctly blocked on remaining Bridge, Index and Codespaces
  installer fixture failures rather than treating individual green lanes as
  overall readiness.
- Replaced the Bash-4-only construction transport with Bash-3.2-compatible
  private channels while retaining descriptor ownership, EOF cleanup, signal
  settlement and prior exit handlers. Independent canonical checks passed
  22 cases with eight unavailable-platform skips. Added a path-scoped, required
  native macOS lane that explicitly uses stock `/bin/bash`; its runtime result
  remains pending the next published head.
- Corrected first engine migration selection in the daemon, both installers
  and their generated launchers. Resolver staging and candidate completion no
  longer imply activation: legacy selection remains until a selector is
  published. All 29 engine-daemon tests passed, including a real failed
  selector replacement and successful current/last-good publication.
- Physical offline-provider fixtures exposed a real retry-rescrub regression:
  build residue recreated by a failed admitted attempt survived into its retry.
  Shared pre-attempt callbacks now let the Bridge scrub every bounded attempt
  without bypassing seasoning or duplicating retry logic. Independent Bridge
  checks passed 12 cases with 14 platform skips; Index hook/provisioning checks
  passed 37 with 29 skips; Codespaces adoption passed 31 with 26 skips.
  Native Linux/macOS integration and fresh review remain open.
- The new stock macOS lane executed Bash 3.2.57 and passed the interruption
  and ownership checks, but exposed a real success-path bug: a process-
  substitution PID was not waitable in its owning shell. Replaced that
  transport with an explicitly backgrounded, waitable child and private FIFO
  channels. Independent canonical checks passed 23 cases with eight platform
  skips; the corrected native macOS result is still pending.
- Closed the local-artifact admission gap found by subsequent review. Directory
  sources require attributable canonical/payload roots, matching project
  identity and recursively contained local mappings; wheels, archives,
  escaping paths and hidden local artifact references are refused. The Python
  API defaults to no trusted roots. A proper bundled TOML parser preserves
  fresh Python 3.10 without installing bootstrap dependencies. Independent
  policy checks passed all 105 cases.
- The stricter boundary correctly rejected a Vault snapshot fixture that had
  copied development-only relative library paths. The fixture now runs the
  existing release materializer before stamping rather than broadening trust.
  Independent snapshot provisioning passed six cases with three platform
  skips; fresh Worktrees bootstrap passed 21 with four skips.
- Removed an extra fixture-only PowerShell launch from each physical UV retry,
  retaining four attempts, exact mocked delays and the original 20-second
  child limit. Independent retry checks passed all six native cases. The late
  Bridge owner-launch fixture now ships both canonical companions and passed
  15 cases with one platform skip. Corrected Linux-only fixture assertions
  retain stable coordination locks and require canonical selector newline
  bytes. No failing required lane has been bypassed.
- Fresh-environment review identified two direct Python API callers that had
  not supplied their repository-owned source root. Both plugin and standalone
  test preparation now attest the tooling's fixed repository root explicitly,
  retaining the policy's identity and recursive-containment checks. Added
  real-policy wiring regressions for editable and script-only plugins and all
  four standalone components; environment-lifetime fixtures now provide valid
  source metadata rather than mocking past admission.
- The combined runner/admission contracts passed 115 cases with two platform
  skips. A real forced-fresh Bridge test environment resolved and built its
  complete closure through the configured feed, and a fresh contained Fleet
  environment prepared successfully and passed 458 tests with one skip.
  This is fresh test-preparation evidence, not runtime rollout or service
  deployment. Current-head review, native required CI and release remain open.
- The current implementation review returned a clean owner-authored Comment
  with zero open findings. Stock macOS Bash 3.2 then passed all 31 canonical
  construction, interruption and ownership cases. Required CI still correctly
  blocked on six fixture groups whose temporary local sources lacked real
  publisher attribution or retained escaping development-only paths.
- Repaired those fixture groups as a coherent test-only batch: they now stage
  matching publisher/project/roster metadata, canonical helper locations and
  recursively release-materialized library graphs. No policy mocks or
  package-argument-derived trust roots were introduced. Test-author guidance
  now records that requirement.
- Independent native integrations passed Worktrees bootstrap 22 cases with
  four platform skips, Index source/provisioning 17 with 19 skips, Bridge
  source-graph admission one with 11 skips, Dispatch installer contracts 39
  with 17 skips, Logger snapshots 17 with one skip, and Codespaces adoption 31
  with 26 skips. The last module passed alone in 152.74 seconds under its
  unchanged 300-second wall limit after a contended combined run exhausted
  that limit; no deadline or assertion was relaxed. Fresh-head Linux CI is
  still required for the skipped native shell cases.
- A bounded portability check caught an introduced ambiguity between named
  requirements and owned directory/file-URL paths containing `@`. Admission now
  recognizes real paths before splitting `name@reference`, without changing
  containment, identity or artifact refusal. All 118 policy cases passed
  independently, including 13 new positive/negative path regressions.
- The contribution base advanced during the native CI queue. Reconcile its
  actual content before the next publish; a conflicted PR does not receive a
  new CI run and cannot stand in for verified release readiness.
- Preserved the complete implementation on a backup, collapsed the owned
  private branch and rebased onto the advanced contribution base. Kept its
  stable install-lock recovery, shared daemon changes and trusted promoter
  adapter rather than discarding concurrent work.
- Budget's new `agent-procutil` dependency now joins the same seasoned source
  batch. Retained before/after artifact cleanup while refusing unrestricted
  pip fallback, included canonical dependency metadata in admission identity,
  and made development snapshots own their local library paths. Failure and
  clean retry are separate real native contracts under the unchanged item
  limits. The focused integration passed 11 cases with 12 platform skips;
  delivery and trusted publication/policy contracts passed 204 tests.
- Published the reconciled owned head through the supported exact-lease flow,
  then preserved a one-to-one replay against the newer contribution base.
  Its only conflict retained both lifecycle admission and construction cleanup;
  the focused native Worktrees integration passed 63 cases with four skips.
- Subsequent real review identified two additional races. Fleet artifact
  staging now retains and executes all RECORD-admitted companion bytes, rather
  than reopening them after admission. Failed cell construction now carries
  both receipt generations and payload identity into lock-protected
  reprovisioning, so an intervening publisher invalidates the older builder.
  The actual construction race suite passed 24 cases with nine platform skips,
  paired provisioning passed nine checks, and installed-wheel Fleet validation
  passed 591 cases with six skips. Selected verification dependencies were
  restored only through the configured feed and canonical seven-day cutoff.
- Required CI on the reconciled head still failed. New upstream diagnostic
  workflow steps require canonical seasoning, and two native fixture groups
  still asserted the retired completion/cleanup and raw snapshot contracts.
  Correct those without weakening admission. The native Worktrees first-use
  timeout remains under investigation; expose its real subprocess output
  under the unchanged item deadline. No implementation merge, release or
  runtime rollout is complete.
- The corrected native run passed every test step, including Worktrees
  first-use, stock macOS construction and the Linux snapshot contracts.
  The provider nevertheless marked the Windows runner job cancelled after
  all its steps succeeded; the required aggregate correctly refused that
  result. A fresh required run remains necessary, not a bypass.
- Fresh review resolved the companion-byte and paired-generation races, but
  identified a separate npm lockfile admission/install race. All npm metadata
  and installation now consume a private snapshot of retained package, lock
  and local configuration bytes. Only its health-checked dependency tree is
  published, with rollback if publication fails. Concurrent input changes
  refuse publication and preserve the previous tree.
- Real controlled-registry validation passed all 35 npm cases, including
  replacements immediately before native `npm ci`, installation failure
  after writes, failed selected-module health and failed publication.
  Source/workflow contracts passed 82 cases. The fixture disables unrelated
  npm update-notifier requests rather than raising its 45-second child limit.
  This fresh isolated npm lane is clean-room/local integration evidence;
  native provider CI, fresh review and deployed rollout remain open.
- A second native provider run exposed the cancelled-job cause: the Windows
  runner lane's combined diagnostics, wrapper, payload and Fleet work exhausted
  its seven-minute job limit. Native diagnostics and wrappers now run in their
  own five-minute required lane, preserving every selection and child deadline.
  Runner/Fleet work retains its existing seven-minute limit. The aggregate must
  observe both jobs; no test, budget or required check is waived.
- The subsequent native first-use failure reached package installation but
  timed out before activation. The source fingerprint's growing shell-string
  concatenation made completion revalidation quadratic in materialized source
  size. An unchanged full-source fingerprint exceeded 90 seconds in a real
  Git-owned Bash probe; streaming the identical bytes completed in 78.14
  seconds on that host without changing the deadline or digest format.
  Exact legacy-byte regressions cover plugin, vendored and external source
  inputs. The native Windows source/first-use contracts passed 35 cases with
  four platform skips; Linux first-use remains owed by required CI.
- The streamed-fingerprint head passed every required provider job, including
  native Linux first-use, both separated Windows lanes and stock macOS
  construction. Fresh review nevertheless identified two further input-binding
  gaps: UV resolution still reopens mutable admitted manifests/configuration,
  and npm needed revalidation after its dependency-tree rename.
- npm now revalidates at the publication linearization point and restores the
  retained tree on input mismatch. Four actual controlled-registry publication
  cases passed, including manifest, lock and configuration replacements after
  the candidate rename. UV input binding and fresh-head validation remain open;
  the implementation has not merged or deployed.
- UV admission and execution now share a private input-retention lifetime.
  Actual installation consumes retained source metadata, recursive requirements
  and configuration with a captured environment, and verifies native resolver
  equivalence instead of weakening configured-feed behavior. The Python tooling
  consumers use this execution seam rather than reopening preflighted inputs.
- Static setuptools `src` editables retain dependency metadata while referencing
  the original live code. Actual configured-feed setuptools builds proved imports
  survive input-snapshot cleanup and observe source edits, including separate
  Windows source and environment volumes. The first absolute-path approach was
  rejected by setuptools; relative package-directory mapping passed. Compact
  common-root mirrors avoid Windows current-directory length failures.
  Unknown editable shapes fail before installation. Controlled-registry
  post-admission input mutations and native configuration-precedence coverage
  are implemented; final combined validation, shipped-helper synchronization,
  fresh review/CI, release and deployed rollout remain open.
- Combined verification passed 542 cases with two platform skips: the complete
  135-case UV policy suite, all consumer/artifact lifecycle contracts and all
  38 real npm admission/publication cases. Three later argument/certificate
  normalization regressions and the source/policy guards passed in a 300-case
  focused run. Touched-code lint, installer contracts and all 13 shipped helper
  copies passed. Native editable checks cover explicit, implicit, environment,
  project and directory selection without resolving a venv interpreter symlink
  to its base Python. Required fresh-head provider CI and genuine review remain
  blocking; no merge, release or deployed-runtime claim is made.
- Fresh required CI exposed the shared policy module's size limit and missing
  standalone build resources. Configuration-retention primitives now ship as
  an adjacent canonical companion, synchronized across all thirteen consumers.
  The standalone runner explicitly retains its attributable runtime build
  helper and three primitives without expanding dependency-source trust.
- Real Fleet and Index setuptools wheel builds consumed retained resources
  after the original helper and primitives were replaced. Exact wheel bytes
  and snapshot cleanup passed. A fresh configured-feed Fleet install preserved
  its complete development extra and passed 591 tests with six platform skips.
  The Pull Requests finalizer now releases its install lock even when
  construction cleanup raises; a still-live PowerShell host reacquired the
  actual exclusive file lock.
- Native Python 3.10 exposed variable-width fractional timestamps in UV's
  settings projection. Conservative microsecond normalization now preserves
  the seven-day boundary across supported interpreters. The complete parent
  policy, runner, artifact, fan-out and module-size contracts passed 532 tests
  with two platform skips.
- Installer fixtures now ship both canonical companions, preserve real source
  ownership and validate retained identity, private containment and Python
  bytes before normalizing logged paths. Retry/scrub fixtures run from their
  declared fixture context rather than discovering unrelated checkout metadata.
  Compound snapshot stamping and removed-checkout admission are distinct
  native cases under unchanged deadlines. Required fresh-head provider CI,
  review, release and runtime rollout remain unresolved.
- Corrected native fixture validation passed Index 15 cases with thirteen
  platform skips, Codespaces eight with four skips, Dispatch scrub eight and
  preinstall two, Bridge retries six with one skip, and Logger's durable
  provision and snapshot-only first-use cases. Ordering/retry fixtures retain
  canonical metadata and source graphs but use bounded fixture code bodies;
  real product artifact coverage remains separate. Touched-code lint and all
  thirteen installer/fan-out contracts passed without raising any deadline.
- Fresh provider CI correctly refused the new real-wheel regression because
  the runner-contract lane had provisioned only pytest and YAML, leaving its
  isolated child interpreter without setuptools. Both native runner lanes now
  declare the actual setuptools floor and wheel dependency through the same
  canonical seasoned resolver. The workflow contract asserts those explicit
  prerequisites; no missing-backend skip or build-isolation bypass was added.
- Subsequent genuine review confirmed the retained-resource and cleanup-lock
  fixes, then identified a migrated-engine status regression. Status now resolves
  its interpreter once and reports unavailable selection explicitly without
  hiding health/PID diagnostics or allowing launch to fall back to a legacy
  environment. All 33 daemon contracts passed, including real stale-selector
  and missing-resolver cases. Fresh-head review and complete required CI remain
  blocking.
- The next required CI run exposed stale Bridge, Index and Vault fixture
  projections and working directories. Corrected fixtures preserve the native
  resolver, full source graph and retained-byte checks. The native Index case
  passed; native POSIX confirmation remains required.
- The Windows diagnostic checkout expanded reconciliation from two to twelve
  cases upstream; together with six binding cases, the full roster is eighteen.
  Its wrapper collected almost ten thousand unrelated tests and exhausted the
  two-minute step after all eighteen passed. A strict repeated test-file selector
  now collects exactly their two source files without changing selection or
  containment. Runner and workflow contracts passed 86 cases with two platform
  skips; the local eight-case roster completed in 17.56 seconds.
- With explicit operator approval, only the two indivisible Vault snapshot
  provisioning cases allow sixty seconds per item. Actual source retention,
  installation, health and publication still run in the timed fixture.
  The shared Windows host remains CPU-saturated and its later native attempts
  timed out, so local evidence is not a passing final gate. A focused fresh
  Windows provider lane now carries that blocking proof, independently of the
  existing POSIX suite; suite and CI deadlines remain unchanged. No further
  deadline increase, failed-check bypass, merge or rollout occurred.
- New provider CI did not register because intervening development commits
  made the PR unmergeable. Preserved the published tip, collapsed only this
  coordinator's commits without changing their source tree, and reconciled
  the two conflicts once. Bootstrap fixtures retain the upstream bounded
  fake-source optimization and this change's admitted metadata graph.
  Existing upstream size ceilings remain unchanged except for this effort's
  already-established versioned-runtime reductions. Cell process-environment
  hygiene now lives beside the existing process helpers, preserving its public
  compatibility seam rather than widening a newly tightened core ceiling.
- Reconciled bootstrap metadata passed both focused cases; process-environment
  hygiene and native cell failure/retry/retention passed all four cases.
  Current PR-scoped size checks, touched-code lint, all thirteen installer
  contracts and canonical engine synchronization passed. Fresh provider review
  and native CI still own the remaining merge gate.
- The verified source-owned replay published successfully. Its fresh required
  CI passed the new native Windows Vault first-use lane, every POSIX plugin
  suite, both runner lanes and the macOS construction lane. Current-head
  Copilot review reported zero open findings. One new upstream bootstrap
  fixture still failed native Windows diagnostics: a copied base interpreter
  could not load its DLLs with PATH restricted.
- That fixture now constructs a real stdlib venv and verifies its exact
  off-PATH executable and prefix before the unchanged native hook, staging
  and lock-release assertions. The base-interpreter Windows regression passed
  in 17.20 seconds and touched-code lint passed. No deadline was raised;
  fresh-head provider CI and review remain required before merge or rollout.
- The next genuine review identified two validation defects: multiline
  PowerShell admission steps could mask native failure, and an exhaustive
  standalone identity fixture omitted canonical retained runtime resources.
  All three affected Windows steps now stop on admission failure; the Fleet
  step also stops on its prerequisite-test failure. Four real PowerShell
  failure-propagation cases and the real UV environment-identity case passed.
- Windows Fleet acceptance exhausted its two-minute full-suite bound halfway
  through the growing selected roster, rather than reporting an individual
  failure. With explicit operator approval, only that Windows suite now allows
  five minutes; Linux retains two minutes and the existing seven-minute job
  is unchanged. All 602 tests passed with six platform skips and eleven
  pre-existing full-distribution deselections in 158.75 seconds. No selection
  was reduced. All 72 workflow contracts passed in a stable interpreter.
  Fresh-head native CI and confirmation of the two review fixes remain open.
- A subsequent source-owned replay preserved fourteen implementation commits
  while integrating the new persistent Containers profile coordinator and
  upstream bounded bootstrap fixtures. Both selected profiles now invoke the
  canonical seasoned resolver; missing UV refuses before mutation, signed
  Windows copies remain unseeded, and no unrestricted pip fallback survives.
  A real subprocess refusal reaches the dependency-age boundary and leaves the
  previous active selector and completion marker unchanged. Native profile,
  dependency and transaction-lock contracts passed 40 tests with eight platform
  skips; the reconciled Worktrees bootstrap passed 22 with three platform skips.
- Strict test-file admission retains both upstream and original regression
  coverage, without duplicate preparation. The upstream production-retention
  exclusions on ordinary PR runs and ten-minute manual-dispatch Fleet budget are
  preserved, alongside the approved ordinary Windows five-minute suite.
  Both workflow event modes propagate native admission failure. All 218
  focused runner, workflow, finalizer and fan-out tests passed with two platform
  skips. The complete canonical policy pass was green; two new workflow-expression
  fixture failures were corrected and included in the passing focused run.
  All thirteen installer contracts, canonical fan-out, touched-code lint and
  unchanged source-size ceilings passed. Required current-head CI and a fresh
  genuine review remain unresolved; no merge, promotion or rollout has occurred.
- Current-head Copilot review identified two Medium findings: the newly
  integrated Containers coordinator retained failed candidates, and canonical
  engine-reference synchronization skipped Python policy companions. Failed
  construction now invokes guarded candidate discard while construction
  ownership remains held, before propagating the original failure. Creation,
  dependency, health and source-drift failures all remove the candidate, retain
  the prior completed selection, release the lease and permit a clean retry.
  Reused completed slots are never discarded. All 45 native profile/dependency/
  transaction contracts passed with eight platform skips, including actual
  subprocess policy refusal and candidate-directory absence.
- Synchronization now repairs missing or stale Python support in both engine
  modes while continuing to remove local shell engines for canonical-reference
  adopters. Repair, permission parity and idempotence contracts passed with the
  existing finalizer contracts: 24 tests. All thirteen installer contracts and
  eight canonical-engine adopters remain in sync. Both actual Containers
  profiles were also freshly built through the configured-feed seasoned
  resolver, imported successfully and published their exact completed profile
  selectors after the cleanup correction; neither build reused an old slot.
  Required fresh-head review and CI remain the next gate.
- Replayed all sixteen implementation commits over the subsequent installer
  publication-honesty changes. Retained guarded activation, immutable completed
  Bridge slots and fail-closed cross-platform SHA-256 discovery while keeping
  canonical construction ownership and failed-candidate cleanup. All completion
  wrappers now return Boolean success only after the output-free held adapter
  acknowledges completion; no raw candidate-directory deletion was restored.
- The upstream completion guard now recognizes held construction as a throwing
  writer, with regressions rejecting swallowed or conditionally skipped
  failures. Actual shipped PowerShell wrappers exercise successful completion,
  writer refusal and missing fingerprints. Streaming fingerprint tests retain
  working/failing hash-tool coverage rather than selecting implementations by
  their former buffering variable.
- Native Windows Git Bash fixture integration exposed its distinct transport:
  a Windows Python process cannot consume an MSYS FIFO as stdin, and its default
  output uses CRLF. Test-only anonymous-pipe relays and explicit line framing now
  model the real POSIX protocol, including early refusal and relay cleanup.
  All 50 Worktrees publication cases and 131 guard/synchronization/finalizer
  cases passed. Git Bash remains compatibility evidence, not native POSIX
  evidence; the required Linux and stock macOS lanes remain authoritative.
  Current-base Bridge admission and rollback passed 120 cases with ten native
  platform skips across two focused invocations. Its trap assertion now
  requires both marker cleanup and the install-lock release, retaining the
  canonical construction settlement that precedes that outer trap.
  Fresh-head review and provider CI remain pending. The effort is not merged,
  released or deployed.
- The reconciled head received a genuine clean current-head Copilot Comment,
  but required CI correctly blocked on two native failures. Removed an
  accidentally inserted early success return from Codespaces development
  construction: completion must still be followed by helper staging and
  generation activation. Its real native failure/retry test now explicitly
  requires the initial generated selector and passed on Windows; native POSIX
  revalidation remains required CI evidence.
- A cold Windows read-only helper exceeded its unchanged fifteen-second
  subprocess deadline. The canonical PowerShell bootstrap now compiles missing
  native types in one batch rather than repeatedly invoking the compiler.
  Four cold/partial-preload/reentry cases passed on both installed PowerShell
  hosts within that same bound. All eighteen real diagnostic/binding cases
  passed in 105.14 seconds, and seven native receipt/filesystem cases passed.
  Canonical copies are synchronized across all eleven payload adopters.
  No timeout, selection, filesystem guarantee or review requirement was relaxed.
  Fresh-head native CI is still required before implementation merge.
- Required CI on that head passed the corrected native Windows diagnostics,
  Codespaces POSIX development lifecycle, both runner lanes, Windows first-use
  and stock macOS construction. The remaining Windows archive-source failure
  matched the already tracked replacement-completion flake #6486 and passed
  its failed-job rerun. Index's split suites all passed but exceeded the
  eight-minute job boundary, producing a cancelled aggregate twice; neither
  result was waived.
- Replayed the complete implementation onto #6697's already-merged promotion
  changes, including its independently established twelve-minute Index job.
  Preserved verified candidate-artifact publication, exact snapshot dispatch
  and the fast promotion portfolio rather than substituting a manual exhaustive
  run. New native readiness preparation uses the canonical seasoned resolver,
  and the promotion aggregate now includes this effort's three additional
  required native lanes. Ordinary/manual Fleet budgets and native failure
  propagation retain their existing boundaries.
- All 379 focused policy, source-attribution, native failure-propagation,
  Fleet-scope, candidate-readiness/retry and exact promotion-dispatch contracts
  passed. Failure propagation covers PR, ordinary manual and promotion-snapshot
  dispatches. Runtime installer and canonical native-helper bytes are unchanged
  by this replay; previously exercised native proofs still apply. Fresh-head
  required CI and genuine review remain necessary before merge or rollout.
- A final two-commit base advance added explicit test-wheel refresh and shared
  daemon launch ownership. Replayed the implementation without changing the
  already-validated promotion workflow or settled native installer bytes.
  Retained explicit `--reinstall`, the leased environment directory and
  junction-safe cleanup; the upstream argv regression now observes the
  canonical policy boundary and verifies its fixed trusted root as well.
  All 86 runner/ownership cases passed with two platform skips, and all 58
  Index daemon/lifecycle cases passed against the new launch API. Fresh-head
  CI and review remain the merge gate; this replay is not a release.
- Fresh current-head Copilot review again reported zero open findings. Native
  Bridge CI exposed a newly merged owner-boundary fixture that staged only the
  former single-file runtime helper. The fixture now copies the complete three-
  file runtime closure, retaining the real independently owned interpreter,
  isolated environment, exact launch/resume arguments and workspace-only cleanup.
  The complete local owner-boundary module passed eight cases with three native
  POSIX skips; native Linux revalidation remains required CI evidence.
- Subsequent genuine review identified a destructive support-synchronization
  path: a destination symlink could redirect a policy-file copy or permission
  change to an unrelated target. Synchronization now applies the existing
  linked-path guard to the destination before reading or mutating it.
  Twelve native filesystem regressions cover both wrapper modes, both support
  files and different, matching or dangling targets; the link and target bytes,
  permissions and modification time remain unchanged on refusal. All 142
  synchronization/completion-guard cases passed, and all eight canonical
  adopters remain synchronized. Fresh review and required CI still precede
  merge; runtime installation behavior was not changed by this correction.
- Review then identified the same destination-link defect in the separate
  versioned-runtime fan-out entry point. Both check and write modes now
  preflight every destination and ancestor before any destination read or
  mutation. Forty-five real entry-point cases cover all seven assets,
  matching/different/dangling targets, linked ancestors, preservation of earlier
  stale files, and ordinary repair/idempotence. Both synchronization suites
  passed all 80 cases; runtime/resolver fan-out and thirteen installer contracts
  remain clean. The release materializer already guards its support destinations.
- Required Windows artifact CI exhausted its unchanged five-minute aggregate:
  composed contracts passed in 215 seconds before native readiness was
  interrupted. Moved the unchanged native selection into its own five-minute
  required job, sharing the existing detector's output and retaining both PR
  and promotion aggregation. No case, selector or per-item/job deadline was
  relaxed. All 37 focused native, real detector and promotion-aggregate checks
  passed in 102.23 seconds. Fresh-head review and required CI remain blocking.
- The subsequent head passed required CI and genuine Copilot review with zero
  open findings. Before merge, an operator-observed staged Containers update
  exposed a sibling-import assumption: safe-path or isolated Python does not
  add the script directory to module search. The profile coordinator now loads
  its exact adjacent runtime helper explicitly, and both shell launchers use
  isolated mode. Real copied-payload subprocesses cover safe-path and isolated
  startup from a hostile ambient directory, while the actual selected-dependency
  refusal also runs isolated and preserves prior selection. All 47 profile,
  dependency and lock cases passed with eight platform skips; installer
  contracts, touched Python lint and PowerShell parsing passed. Fresh-head
  publication gates remain required; no installed payload was patched.
