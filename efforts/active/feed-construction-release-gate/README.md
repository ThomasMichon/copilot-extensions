# Feed Construction Release Gate

- **Slug:** `feed-construction-release-gate`
- **Repo:** copilot-extensions
- **Branch(es):** independent managed worktree; proposal and implementation target `dev`
- **Created:** 2026-10-20
- **Status:** Active
- **Vision:** extends [Source-Owned Release Pipeline](../../../visions/release-pipeline/README.md#configured-feed-construction-and-first-launch); realizes governed runtime assembly and independent installer readiness
- **Umbrella issue:** #7529
- **Related owners:** #3336, #6237, #4876, #6476, #7420, #7446, #7514

## Guiding Intent

Do not publish a release whose declared Python environments cannot be constructed
and launched under the selected package-feed restrictions. Source CI and
promotion must exercise the real configured feed, complete environment inventory
and installed entry points rather than infer availability from public release
timestamps, a wheel build or a warm developer environment.

This deliberately amends the previous exclusion of all installation execution
from promotion. The exception is bounded construction and first launch, not the
general integration portfolio. Existing eligible-unit selection and its coverage
packet remain unchanged. The existing end-to-end release latency objective also
remains; parallelism, authoritative inventory and justified equivalent-input
reuse must not become silent omissions.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Gate driver | Own proposal, generic qualification mechanism, evidence and completion | #7529 and its managed source worktree |
| Inventory evidence worker | Read-only census of canonical environment owners and construction/health declarations | Gate driver; no edit or publication authority |
| Release/control owners | Accept the existing CI and trusted promotion integration seams | #3336 and #6237 |
| Existing repair owners | Retain Manager construction/update and mirror-age admission implementations | #7420, #7446 and #7514 |
| Repository owner and automated reviewer | Review the policy exception, implementation and activation evidence | Normal repository PR flow |

## Coordination

- **Topology:** proposal first, then bounded implementation PRs against `dev`.
- **Host (owns PRs):** Gate driver.
- **Delegates:** inventory evidence only; no shared-worktree writers.
- **Scope:** reuse canonical installer, dependency-policy, runtime-health and
  artifact mechanisms. No parallel installer or promotion writer.
- **Shared files:** coordinate CI, trusted-controller and publication edits with
  #3336/#6237 before changing them. Existing runtime and seasoning repairs are
  dependencies, not competing implementations.
- **Handoff:** retain this effort's full completion gate, exact current source,
  owned PRs and outstanding platform/feed/control obligations.

## Context

- The declaration audit checks canonical public release metadata. It does not
  prove availability or resolver behavior through a configured consumer feed.
- First-party artifact production does not construct every consumer environment;
  standalone runtimes and selected optional/profile environments require explicit
  coverage rather than inference from a top-level plugin wheel roster.
- The existing release vision expressly excluded installer and generated-consumer
  integration prerequisites. This proposal changes that intent explicitly before
  implementing the narrow exception.
- Actual feed reachability from a hosted CI worker is unqualified at proposal
  time. Success from another environment is not that proof.
- Feed coordinates and authentication remain external adopter configuration.
  The operator authorized masked Actions configuration; no real feed, private
  deployment identity or credential belongs in this public effort or source.

## Request

The operator's immediate request (verbatim):

> Right. We need this pronto to prevent an outage. We need to ensure that every venv is constructable under the feed resrictions, during CI and promotion, so that we don't release something that can't launch

The operator confirmed the effort slug and authorized externally configured,
masked Actions feed selection. Provider-specific coordinates are intentionally
not reproduced here. The inventory, execution and evidence design below is
**agent-recommended**; it must clear review before implementation.

The operator subsequently specified the native targets (verbatim):

> Windows x64/AMR64, Linux x64/ARM64, macOS x64/ARM64, whatever a Raspberry Pi 5 has

The initial Python answer was `3.9+`. After the existing runtime/bootstrap
minimums were explained, the operator superseded it with (verbatim):

> Oh 3.11+ is fine

Interpretation: normalize the `AMR64` typo to ARM64; qualify x64 and ARM64 on
Windows, Linux and macOS, plus actual Raspberry Pi 5 hardware running 64-bit
Linux/ARM64. The qualification policy starts at Python 3.11; this does not
silently change existing packages' declared compatibility minimums.
_(Agent-recommended execution detail:)_ cover the latest stable patch of each
released CPython minor from 3.11 onward using the standard GIL-enabled ABI.
Prerelease and free-threaded interpreters are not substituted for those rows.
Unavailable capacity or dependency wheels remain failed qualification, not
unsupported targets or successful skips.

## Plan

### Phase 1 - Review the policy and complete inventory

- [x] Review and merge this release-vision exception and canonical effort.
- [x] Derive and validate every tracked Python source owner's classification
  against the canonical plugin/product rosters and explicit nested-project
  declarations, retaining libraries as inputs rather than separate venvs.
- [ ] Reconcile the complete environment census against existing authoritative
  declarations: runtime plugins, standalone applications, bootstrap/build
  environments and selected optional/server/profile closures.
- [ ] Record canonical construction and installed-health entry points and the
  supported platform/profile matrix, required extras and actual interpreter
  architecture/ABI; reject unclassified owners rather than maintain a permissive
  hand-written skip list. Classify development-only fixtures explicitly rather
  than confuse them with shipped runtime owners.
- [ ] Obtain accepted non-overlapping CI/trusted-controller integration seams
  from the existing release/control owners.

### Phase 2 - Implement bounded, feed-preserving qualification

- [ ] Reuse canonical dependency admission and construction in disposable roots
  with fresh environments and no success supplied by a prepopulated cache.
- [ ] Preserve configured index order, authentication/TLS, age policy and selected
  profile constraints, including bootstrap and isolated-build dependencies.
- [ ] Prove installed package origin and supported entry-point first launch in
  fresh processes, without importing the source checkout or starting host-owned
  services. Identify the actual launch boundary each result proves.
- [ ] Emit complete, source/candidate-bound inventory results; missing, duplicate,
  failed, skipped, timed-out or unavailable-feed results refuse qualification.
- [ ] Keep the provider-neutral runner bootstrap independent of unrelated broken
  product graphs; missing required feed configuration is an explicit failure.
- [ ] Reuse the existing trusted CI admission boundary. Candidate construction
  receives no publication/signing authority, and an untrusted PR never gains
  privileged feed credentials through a trusted-trigger workaround.

### Phase 3 - Earn actual CI and candidate evidence

- [ ] Prove configured-feed access from an actual hosted Actions worker.
- [ ] Exercise every admitted native platform/profile row using real
  construction and installed launch, not cross-platform resolution alone.
- [ ] Bind proof to the immutable source and composed candidate plus inventory
  and package-policy inputs. Reuse only independently verified equivalent inputs,
  never a stale or another-platform result.
- [ ] Preserve the separate eligible-unit proof and whole-release latency
  objective; do not restore unrelated integration suites.

### Phase 4 - Activate and complete release protection

- [ ] Require complete qualification in contribution CI and before candidate
  publication/promotion through the existing trusted control owner.
- [ ] Qualify the exact active Main controller's compatibility and deliver any
  control change through its reviewed activation path.
- [ ] Demonstrate a negative construction/launch result actually prevents
  publication/merge, then a fully qualified candidate can complete normally.
- [ ] Update actual pipeline/install documentation, record the merged source and
  activation evidence, resolve every plan/validation item and mark this effort
  Done. Source merge alone is not activation or runtime rollout.

## Validation Plan

- [ ] Unit regressions reject unknown/missing/duplicate inventory rows, altered
  source/policy bindings and incomplete or forged qualification results.
- [ ] Deterministic policy fixtures cover exact age boundaries and a seasoned
  canonical artifact with a recently rewritten mirror timestamp, without
  weakening the existing admission owner's rules.
- [ ] Real configured-feed construction covers transitive, build and selected
  optional dependencies, with no public-index fallback.
- [ ] A deliberately unavailable dependency, failed installed import/entry point,
  unavailable feed and exhausted deadline each produce a visible failed gate.
- [ ] Installed-origin and fresh-process launch controls cannot pass from a
  source-tree import, an old environment, another interpreter architecture or a
  previously running service.
- [ ] Untrusted candidate execution cannot acquire signer/publisher credentials;
  missing authorization or feed configuration is visible, not a successful skip.
- [ ] Actual hosted-worker evidence covers the complete declared native matrix;
  unavailable capacity remains a blocker, not a skip or implied qualification.
- [ ] Real CI and promotion negative controls prove the gate is required, not
  merely an informational successful job.
- [ ] Record actual execution/queue duration and preserve the release budget.
- [ ] Existing installer/rollback, publication integrity, required review and
  eligible-unit proof contracts remain intact.

## Review and activation status

The release and test-portfolio vision exception and canonical effort were
reviewed and merged in #7655. Implementation is permitted under that plan;
complete inventory and the accepted control integration seams remain open.
No construction runner, CI controller, feed configuration or enforcement
cutover is activated by the reviewed documentation.

| Boundary | Current evidence |
|----------|------------------|
| Checked-in policy | #7655 merged the construction/first-launch exception and canonical effort; implementation is permitted within that reviewed scope. |
| Inventory implementation | #8304 merged the source-owner diagnostic and engine declaration. Every tracked Python project is classified against canonical rosters; complete construction environments and the native/profile matrix are still unqualified. |
| Checked-in selected-extra constructor | #8393 merged at `e618cb2a44c1129139ffe5b2ae869f5c3c273540`: declared primary-package extras participate in the canonical transaction, admission identity and installed-origin evidence. Local constructor contracts passed with an explicit contention allowance (55 passed; one existing symlink-privilege skip), and the rebased base/profile integration controls passed again. This is not default-budget performance proof or an active gate. |
| Released selected-extra constructor | The latest observed Main release `bc361f7fa05201e3869eb9021d26d30c85805fc3` does not contain the profile-aware canonical engine. No deployment or running-state qualification is claimed. |
| Checked-in result admission | #8416 merged at `28906a984304278307a90bf2774f6b34e0b113b0`: pure expected-plan/result binding and exact successful coverage checks. Complete authoritative matrix derivation, producer authentication, artifact verification and execution are not implemented by that library. |
| Checked-in gate | No complete environment inventory, construction qualifier or required CI/promotion admission gate is active for this effort. |
| Released gate | None: the existing release pipeline does not yet enforce this effort's configured-feed construction and first-launch contract. |
| Installed evidence | Earlier consumer-specific Manager capture/origin results remain bounded observations, not full environment/profile coverage. The launch-maintenance effort separately records current selected-slot observations. |
| Disposable construction evidence | One cold `agent-mcp` base environment passed canonical construction, installed-origin health and isolated CLI `--help` with Windows AMD64 Python 3.13.15 and the existing configured feed. It was never selected or deployed as a host runtime. |
| Hosted/native qualification | Configured-feed execution, complete declared native matrix and negative publication controls remain unproved. Missing feed or capacity must fail qualification, not be called an unsupported row or successful skip. |
| Deployment | No feed configuration, trusted-controller activation or host rollout is performed or implied by these status records. |

## Journal

### 2026-10-20 - Urgent release-protection proposal

- Claimed #7529 after checking adjacent artifact, seasoning, runtime and
  promotion ownership.
- The operator confirmed the slug and masked Actions feed configuration.
- Requested coordination with #3336/#6237 before shared workflow edits.
- Started a read-only inventory census; implementation waits for reviewed intent.

### 2026-10-21 - Initial census and installed-launch boundary

- The initial read-only census identifies thirteen runtime plugins, standalone
  Manager and index-engine environments, and selected index server, engine and
  optional MCP closures. Shared libraries are construction inputs, not
  automatically separate environments.
- Bootstrap and isolated-build requirements belong in the qualification
  closure. The complete native platform/interpreter architecture/ABI and
  profile/extras matrix is still unclassified; this is not a completed
  inventory or permission to omit a row.
- A real installed launch exposed a separate headless Picker readiness defect
  tracked in #7638. A version probe or a successful environment construction is
  therefore not interchangeable with installed consumer first-launch evidence.
- Existing installation repairs remain separately owned. No construction runner,
  workflow enforcement or feed secret has been activated by this proposal.
- Reconciled the test-portfolio vision's blanket integration exclusions with
  the same bounded construction/launch exception; unit eligibility, coverage
  and the general periodic integration portfolio remain unchanged.

### 2026-10-21 - Reviewed intent and current inventory reconciliation

- #7655 merged after current-head automated approval and required checks. Its
  review also reconciled the standing test-portfolio vision with the same
  bounded exception; the general integration portfolio remains separate.
- The current authoritative standalone roster contains five products:
  Worktree Manager, index service, fleet-manager, Dispatch service and Chat
  service. The earlier census covered only Manager and index-related
  environments and is not complete release coverage.
- Source-only evidence distinguishes thirteen runtime plugin environments,
  the five standalone products, selected index service profiles and the
  independently constructed torch engine. Optional extras are not automatically
  independent environments; development-only test extras are not implicitly
  release profiles.
- Complete supported native OS, interpreter architecture/ABI, profile and
  bootstrap/build closure declarations remain unqualified. Missing declarations
  must become explicit inventory failures, not unsupported-by-assumption rows
  or successful skips.
- The separate Picker readiness repair #7652 reached published Manager
  0.6.55-dev1. Retained installed real-data captures succeeded on native Windows
  ARM64 and x64 environments; this does not qualify every platform or profile.
  A native WSL consumer exposed further engine/current-roster prerequisites,
  confirming that version, import and preference-file evidence alone cannot
  stand in for running consumer readiness.
- Existing #6237 compact-unit/control work remains independently owned.
  Coordination has been updated, but an accepted qualification-result
  integration seam and hosted feed execution evidence are still outstanding.

### 2026-10-21 - Architecture correction and launch-maintenance interruption

- Correction to the preceding installed-capture entry: the ARM64 Windows host
  ran an AMD64 interpreter and launcher. PE machine headers and
  `sysconfig.get_platform()` identify the actual Python ABI; the host-level
  `platform.machine()` value was not native ARM64 interpreter proof. Native
  Windows ARM64 Python remains unqualified.
- The operator prioritized a separate blocking-launch problem. #7694 and
  `efforts/active/coordinated-launch-maintenance/README.md` own that bounded
  repair. This effort remains Active with its complete construction, native
  matrix, trusted integration and activation gates unchanged. No feed gate,
  hosted secret or promotion requirement was installed during the interruption.

### 2026-10-23 - Resume the original gate after scheduling source delivery

- The interrupting scheduling change #7788 and its completion journal #8207
  merged. Promotion #8268 subsequently released that source. Its canonical
  launch-maintenance effort distinguishes released launchers, observed
  installed files and still-unproved installed execution-driver behavior;
  none closes this effort's construction or first-launch requirements.
- Resume the reviewed Phase 1 inventory reconciliation against current source.
  The earlier thirteen-plugin/five-product census is evidence to reconcile,
  not an immutable roster or completed matrix. Reuse authoritative owners and
  constructors, classify actual environment/profile identities and reject
  missing declarations before implementing qualification.
- Retain #3336/#6237 ownership of shared CI and trusted promotion. A bounded
  inventory/result-contract slice can proceed without installing a feed
  credential or changing their controller. Accepted integration seams,
  actual hosted feed access, native construction and required negative
  controls remain explicit completion gates.
- Existing deployment holds and independently owned Windows broker
  #6859/#7833 qualification are unchanged. No updater, service, registration
  or dependency-policy exception was introduced to resume this work.

### 2026-10-23 - Executable source-owner census; construction coverage still open

- Added a finite, read-only source inventory using the canonical marketplace,
  standalone product roster and actual Git-tracked Python projects. The current
  census contains thirteen runtime plugin owners and **six** standalone products,
  including the newly declared PR service. The earlier five-product record is
  historical, not a complete current roster.
- The diagnostic keeps twenty-seven canonical library inputs and seven
  identified vendored copies separate from product/runtime owners. An
  independently constructed index engine remains an explicit unclassified
  nested project and produces a nonzero result; it cannot disappear because it
  is absent from the marketplace and standalone product rosters. Test fixtures
  and future nested projects receive the same visible classification boundary.
- Optional dependency group names and console scripts are inventory data, not
  an inferred environment matrix or installed first-launch evidence. Reuse the
  existing shared installer-engine and versioned-runtime primitives through
  each owner's canonical constructor when execution is
  implemented. Worktrees' `runtime_install` and `runtime_install_modes` own its
  specific lifecycle; they are not a generic installer for other components.
  No new installer was added. Complete environment identities, production
  profile selections,
  constructor/launch bindings and native matrix remain open Phase 1 work.
- Local refusal/data contracts and a real fresh Git-fixture CLI control passed.
  The real source invocation independently reported the expected unclassified
  engine with `runtime_qualification: not-performed`. This is source discovery,
  not a feed construction run, clean-room runtime installation or hosted/native
  runtime qualification. No workflow, admitted unit packet, credential,
  controller, dependency policy or installed state was changed.

### 2026-10-23 - Declare the engine owner without inventing an environment matrix

- Added an inert plugin-owned `additionalPythonProjects` declaration for the
  index engine's existing `server/pyproject.toml`. The inventory consumes that
  declaration rather than hardcoding the engine into a second product roster.
  Missing, duplicate, escaping or multiply owned projects refuse classification.
  This metadata does not invoke the existing explicit engine installer or alter
  host/client role selection.
- The actual source CLI now classifies all tracked Python projects: thirteen
  plugin runtime owners, six standalone product owners and one explicitly
  declared engine project, with twenty-seven canonical and seven vendored
  library inputs. This closes the bounded source-owner census only, not the
  actual environment/profile and native matrix reconciliation.
- All twenty-two local contracts passed, including the actual fresh Git-fixture
  CLI refusal. A separate real-source invocation returned a complete
  source-owner inventory while still reporting
  `runtime_qualification: not-performed`. The required plugin changefile covers
  its inert declaration; no version number was changed manually. Construction,
  installed launch, hosted feed/native execution and trusted CI/promotion
  activation remain open.

- Review identified a real source-root binding error: an explicit target root
  still inherited the executing checkout's standalone roster. The diagnostic
  now reads the requested root's literal roster without importing or executing
  candidate code, and refuses missing, computed or ambiguous declarations.
  All twenty-four local contracts and the explicit-root source invocation passed
  after this correction; install-contract validation also passed. Root binding
  is source classification, not source/candidate-bound runtime evidence.

- A second review found order-dependent duplicate ownership between a parent's
  additional project and a later marketplace plugin. All three owner kinds now
  use the same duplicate-project claim check; regressions cover both marketplace
  orders and plugin/standalone overlap. This tightens classification only and
  does not add execution ownership or another installer.

- The roster reader now requires the canonical immutable tuple shape. A mutable
  list followed by `append` could otherwise add a consumer invisible to a
  literal-only read. The exact mutation regression refuses that source without
  executing the helper. This is not a general Python evaluator or a new runtime
  policy; nonliteral or mutable roster declarations need explicit reconciliation.

- Resolved source ownership is scoped to the declaring plugin/product, not just
  the repository. A link to another project's metadata cannot assign that
  project to a foreign plugin or claim a second owner through an alias.
  Resolver-boundary controls and a native file-link control cover this case;
  unavailable native link privileges remain an explicit skip, not native proof.

- The containment slice passed twenty-nine Windows controls with one explicit
  native file-link privilege skip. An existing Linux guest then passed all
  thirty controls, including the real file-link refusal and fresh Git-fixture
  CLI. No guest provisioning, package installation, launcher transfer or
  runtime deployment was performed. This qualifies the diagnostic's filesystem
  boundary, not hosted feed access or the release's native runtime matrix.

### 2026-10-23 - Source inventory merged; first cold constructor diagnostic passes

- #8304 merged into `dev` as
  `d9bc8ad547417b7b1864515071e65bead743b794` after current-head automated
  approval and successful required checks. This lands executable source-owner
  discovery, not a construction qualifier or an active CI/promotion gate.
  The complete environment/profile, bootstrap and native matrix remain open.
- At that exact source, a fresh private `agent-mcp` base candidate was built
  through the existing shared `installer_engine.numbered_candidate` path.
  It used an empty task-local cache with caching disabled, the existing
  configured default-index override and the project's seven-day age policy.
  Configuration observation found one network source, no configured public
  registry and no ambient index overrides. Feed coordinates and credentials
  are not recorded here; no fallback, age or TLS exception was introduced.
- Canonical installed-origin health passed before completion, followed by
  a fresh isolated `python -I -B -m agent_mcp --help` under the existing owned
  child-process helper while candidate retention remained held. A separate
  metadata probe verified distribution `0.2.0.dev138`, Python `3.13.15`,
  `win-amd64`, and interpreter/package/distribution origins inside that
  disposable candidate. This is one base environment and a bounded CLI launch,
  not native ARM64 Python, all optional capabilities, bootstrap-from-zero,
  hosted-worker access or complete release qualification.
- The first diagnostic adapter requested process-retention bootstrap assets
  that this owner does not ship and failed before installed health. It is not
  counted as a pass or a feed failure. The corrected diagnostic used the
  owner's normal attached constructor/child lifetime; no installer code,
  policy or deadline was weakened to obtain the subsequent result.
- No host activation path was called. The diagnostic root had no active
  selector, and existing host runtime selectors were unchanged across the
  successful run. No updater, service, registration or launcher transfer was
  started. Continue by declaring actual environment/profile/constructor and
  installed-launch bindings, then implement complete source/candidate-bound
  results and the accepted trusted-control integration.

### 2026-10-23 - Bind selected extras inside the existing constructor

- The existing candidate engine now accepts an immutable selection of declared
  primary-package extras. Selection is normalized and bound to non-base
  admission identity; base source fingerprints remain compatible. The same
  retained UV transaction installs the selected closure before installed health
  and completion. Reuse checks the recorded profile rather than treating a base
  or another-extra runtime as equivalent.
- This does not introduce an installer or change any owner's profile lifecycle.
  Containers retains its existing base/provider-exec coordinator. Native Index
  keeps a base CLI, a required host `[store,server]` sibling and its separately
  managed embedding engine. Engine and sibling composition require their actual
  owner bindings; selecting an extra on the primary package does not construct
  them automatically.
- The bounded metadata reconciliation also identifies production groups on
  Dispatch (`mcp`, `replay`), Pull Requests (`fleet`) and Vault (`cache`, `kek`),
  alongside the standalone products' declared groups. Presence of a group is
  not a declaration that it is selected or owns an independent venv. Missing
  environment/profile selection and native architecture/ABI declarations remain
  explicit inventory gaps, never permission to skip a release row.
- No host selector, runtime, service, feed configuration or controller is
  activated. Full construction results, hosted execution, accepted integration
  seams and negative enforcement proof remain open.
- Native validation is blocked, not successful. Windows runs exercised the new
  selection/refusal controls but the full constructor file encountered native
  health/marker-access deadlines and copied-interpreter write/cleanup access
  errors. A second interpreter family did not produce a clean full result.
  The cause has not been established; no security or production deadline was
  weakened, and unsuccessful fixture-mutation experiments were discarded.
- A minimal POSIX source fixture passed forty-four controls, but missing fixture
  graph inputs caused ten setup errors and one failure. Complete-graph
  preparation then exhausted its deadline. Neither run qualifies the native
  constructor file, and neither can substitute for hosted feed evidence.
  Preserve the implementation locally until complete validation and review pass;
  do not publish, deploy or waive the outstanding failures.

### 2026-10-23 - Narrow native access and lifetime diagnosis

- Subsequent read and write-open probes changed no retained candidate bytes.
  The interpreter, completion marker, lease file and origin receipt all opened
  successfully. No matching Python or UV process was observed. A separate
  framework-owned temporary-directory control also completed creation,
  replacement, readback, deletion and cleanup. These observations do not
  establish why earlier access failed or exclude a transient holder.
- With an existing native Windows ARM64 Python 3.13.15 interpreter, the exact
  previously failing executable-corruption control passed under its unchanged
  thirty-second item watchdog. The base-construction control and both real-UV
  selected-extra controls also passed. These are focused source controls, not a
  passing full native file or a production profile/feed qualification.
- The construction/preparation/reused-health control still exhausted the
  unchanged item watchdog in a separate focused run. Its stack was waiting for
  the retained health guardian during completed-runtime reuse. A diagnostic
  phase-timed run subsequently completed that case in about eighteen seconds;
  an unchanged-watchdog traced run passed both preparation outcomes. Individual
  observed child phases settled, with no single persistent hang identified.
  Timing variability and the earlier access failure remain unexplained.
- A lower-overhead, source-bound trace completed the same successful outcome in
  about twenty-three seconds under the unchanged watchdog. Five owned child
  calls consumed about fourteen seconds; initial retained-input preparation
  consumed about seven and a half seconds. Within those inclusive measurements,
  the two policy probes consumed about one second and twenty-nine retained file
  copies about half a second. UV reported three local packages resolved in
  59 milliseconds, prepared in 1.44 seconds and installed in 1.29 seconds, with
  the synthetic fixture's index and cache disabled. This local failure does not
  establish a configured-feed wait. The remaining startup, metadata, ownership
  and scheduling costs require attribution rather than a speculative operating-
  system or security diagnosis. A broader profiling attempt also timed out and
  is not counted as passing evidence.
- No source behavior, test deadline, operating-system policy, feed setting,
  controller or installed runtime was changed by these probes. Keep the native
  validation hold and the parent effort Active; isolated successful retries
  cannot erase an observed failure or authorize publication.

### 2026-10-23 - Explicit local contention allowance

- The operator authorized a bounded local trial using the existing runner's
  timeout overrides, not a persistent runner policy or production change.
  The full constructor file ran with sixty-second default item, six-hundred-
  second group and eighteen-hundred-second plugin watchdogs. Existing explicit
  per-test markers remained unchanged; CI and production deadlines did not move.
- The run failed after about 213 seconds. The real Worktrees dependency-graph
  freezing control reached its own existing 180-second marker while rereading
  source content for the post-copy fingerprint. It did not exhaust the new
  sixty-second default or reach the previously failing installed-health case.
  This attempt therefore does not qualify the full constructor file or prove
  that a doubled default resolves the local failures.
- This failure is in source snapshot verification, before UV construction or
  installed launch. It supplies a more specific local I/O boundary to
  investigate, not proof of contention, a defective constructor or a feed
  outage. No additional timeout increase, test-only machine allocation,
  security exception or publication is implied.

### 2026-10-23 - Complete the local constructor contracts under bounded load allowance

- The operator subsequently authorized the necessary local validation and
  continuation through the upstream PR flow. A process-local pytest adapter
  doubled both default and explicit item watchdogs: sixty-second default,
  240-second marked construction controls and 360-second real-graph freezing
  control. Group/plugin ceilings were 1,200/1,800 seconds. No tracked runner,
  test assertion, production child deadline, CI policy or cleanup behavior
  changed, and no test-only machine was allocated.
- The complete constructor file finished in about 694 seconds: 55 passed and
  one existing skip. A focused readback identified that skip as unavailable
  Windows symlink-creation privilege, not an omitted construction/profile
  control. The native interpreter was Windows ARM64 Python 3.13.15.
  Executable-corruption, preparation/reuse, selected-profile success/refusal,
  immutable origins and owned-child cleanup controls passed in that full run.
- This clears the local code-slice validation hold for PR review; it does not
  establish the cause of prior variability or default-budget performance.
  Earlier unsuccessful runs remain recorded above rather than relabeled.
  The portable source/contract and generated-consumer checks also passed.
- The existing required PR workflow checks source guards and its positively
  admitted unit packet. It does not execute this full native constructor file
  or qualify the configured feed. Keep complete environment/native inventory,
  hosted-feed evidence, trusted-controller integration and negative enforcement
  proof open under the parent effort. No release protection or host rollout is
  activated by this constructor slice.

### 2026-10-23 - Land the selected-extra constructor prerequisite

- #8393 merged as `e618cb2a44c1129139ffe5b2ae869f5c3c273540` after an actual
  current-head Copilot approval, complete review/thread reads with no open
  findings, and the required source CI and workflow-lockdown checks. The parent
  issue remains open and has no closing reference from that partial slice.
- The managed publication rebase included newer dependency-policy and process
  helpers. Three real base/selected-profile construction controls passed again
  against the exact reviewed head in about 67 seconds under the bounded local
  allowance. This is integration evidence for those changed dependencies, not
  a claim that the complete earlier native file ran again after the rebase.
- The active source worktree was pulled forward through the managed flow.
  The observed Main release still has the earlier canonical engine; source
  merge is not release, deployment or active configured-feed enforcement.
- Continue with the actual environment/profile/constructor/installed-launch
  bindings and complete fail-closed coverage/result contract. The accepted
  trusted-controller consumer seam remains an explicit prerequisite to CI and
  promotion wiring; existing control ownership is unchanged.

### 2026-10-23 - Separate readiness from pure qualification-result admission

- The continued MCP trace confirms its existing readiness declaration names
  platform installers and installed configuration validation, not an exact
  interpreter architecture/ABI matrix or source/candidate-bound construction
  evidence. A successful empty bridge configuration is valid module readiness,
  but cannot establish release construction coverage.
- Added a pure, versioned expected-plan/result admission library. Independently
  supplied expected inputs bind source, composed candidate, package policy,
  every declared environment/profile/extra selection, native interpreter and
  canonical construction/launch specification digests. Exact row coverage,
  actual native identity and complete successful check evidence are required;
  failed, skipped, unavailable and timed-out results cannot qualify.
- This is structural admission, not producer authentication or proof that a
  self-reported digest is genuine. Complete authoritative environment/platform
  declarations, authenticated evidence acquisition and referenced-artifact
  verification remain prerequisites to a trusted consumer. No report-supplied
  expected plan, inferred support matrix or readiness-only shortcut is added.
- The implementation closes a bounded part of the reviewed result-contract
  intent without changing existing installers, CI, promotion, credentials or
  installed runtimes. The accepted #6237/#3336 consumer seam and all actual
  hosted/native/negative-enforcement gates remain open.
- Thirteen focused in-process controls passed with source lint. They cover
  every plan/native binding dimension, exact row/check coverage, malformed and
  unsuccessful outcomes, evidence references and order-independent profiles.
  No additional native installation or clean-room run is applicable to this
  pure data-contract slice. Actual feed/native execution still belongs to the
  unimplemented producer and complete-plan integration, not this test result.

### 2026-10-23 - Land structural result admission; retain the matrix decision

- #8416 merged at `28906a984304278307a90bf2774f6b34e0b113b0` after an
  actual current-head automated approval, complete empty feedback/thread reads
  and successful required source CI, workflow-lockdown and attribution checks.
  The parent issue remains open. The source worktree was pulled forward through
  the managed flow; no runtime or controller was updated.
- The next prerequisite is an authoritative native support/qualification matrix.
  Existing source owner and OS installer declarations do not decide which
  interpreter architectures, Python patch versions and ABIs releases promise.
  A `requires-python` lower bound is not an exact native matrix, and absence of
  a runner or compatible wheel is not permission to classify a target as
  unsupported. Resolve this support-policy decision before declaring complete
  coverage or sizing the required native gate.
- The #6237/#3336 result-consumer seam also remains unaccepted. No shared CI,
  trusted-controller, credential or publication-writer edits are authorized by
  structural result admission alone. The full effort remains Active.

### 2026-10-23 - Resolve native qualification scope and bind target coverage

- The operator resolved the architecture/Python decision recorded in Request:
  x64 and ARM64 Windows/Linux/macOS, actual Raspberry Pi 5 and Python 3.11+.
  The Raspberry Pi 5 uses a 64-bit Arm Cortex-A76; its required native target is
  Linux ARM64 with Pi 5 hardware, not an interchangeable generic ARM64 worker.
- Added a canonical native-target policy and require complete per-environment/
  profile/extra coverage against that independently supplied policy. Current
  stable CPython minors are 3.11 through 3.15 according to the official Python
  status page checked on this date. The proposed execution detail selects the
  latest stable patch of each minor with its standard GIL-enabled ABI; the
  actual patch resolver and native execution remain unimplemented.
- The pure plan/result contract advances to version two, binding the native
  policy digest and explicit observed hardware class. Old evidence cannot
  acquire those fields by inference. Reduced plans, duplicate target/minor
  rows, generic ARM64 evidence for Pi 5 and other interpreter/ABI substitutions
  refuse qualification. Existing package Python minimums remain unchanged.
- Twenty-two focused in-process controls passed, including the complete
  synthetic 35-target matrix for one declared environment and all prior result
  contracts. This is not native execution or a complete environment census.
  Source lint also passed. No feed, machine, runner, controller or installed
  runtime was configured or changed.
- Continue with canonical environment/profile/constructor/launch bindings,
  independent latest-patch resolution and authenticated evidence production.
  Accepted control integration, actual hosted/native capacity and negative
  publication/merge enforcement remain open; unavailable rows cannot be skipped.
