# Python Static Type Safety

- **Slug:** `python-static-type-safety`
- **Repo:** copilot-extensions
- **Branch(es):** Independent, reviewed PRs targeting `dev`; one active slice per worktree.
- **Created:** 2026-10-31
- **Status:** Active
- **Vision:** Verification improvement below runtime-architecture altitude; preserves `visions/test-portfolio/README.md`'s cheapest faithful tier, bounded feedback and containment.
- **Umbrella issue:** #9807
- **Coverage:** [Portfolio tracker and merged lockdown records](coverage.md)
- **Current slice:** Continue Payload Invocation's complete original test/program corpus, source/frozen strict qualification, native/minimum-runtime gates and reviewed admission under #10371. The bounded declared-consumer roster and native fixture corrections merged in #11820 after the reviewed independent-runtime direction in #11796; neither activates a pending project. Promotion-owned copy implementation and complete acceptance merged in #11265; preparation and copied-source correspondence are not admission. Installer Engine (#10931) and Efforts (#10955) retain their separate owners and unmet gates. The original Windows membership report (#11468) is resolved by verified passing original cases. Preserve existing deadlines, source custody, resource limits and individual holds. Agent Roles is strictly admitted by #11485; Payload Invocation preparation merged in #11165. Versioned Runtime (#10331), full-composed-consumer qualification (#10362) and the all-Python campaign remain open. No Main release or installed rollout is inferred from a Dev merge.
- **Checker-only follow-up:** #10638 is delivered by merged #10866; #10938 delivers emitted-program visibility/binding completeness. Both are prerequisites, not Harness, Versioned Runtime or Payload Invocation admission. Preserve full-composed-consumer qualification under #10362 independently while progressing the other assigned slices.
- **Sub-issues:** #9831 (P1 checker foundation, complete); #9853 (P2 required source CI, complete); #9862 (P3 exact-candidate qualification, complete); #9916 (P4a canonical Installation Context source view, complete); #9924 (L1 Config Migrate strict admission, complete); #9930 (L1 Dropin Registry strict admission, complete); #9933 (L1 Endpoint Rendezvous strict admission, complete); #9941 (L1 initial leaf-library test/helper completion, complete); #9951 (L1 Lazy CLI Dispatch strict admission, complete); #9964 (L1 Bootstrap Killswitch strict admission, complete); #9967 (L1 Peer Launch strict admission, complete); #9971 (P2a Installation Context native typing preparation, complete); #9975 (P2b validation-decorator signature preservation, complete); #9980 (P2c production result and flow contracts, complete); #9983 (P2d native platform API contracts, complete); #9986 (P2e cleanup and worker contracts, complete); #9993 (P2f shared receipt-fixture typing, complete); #9996 (P2g slot-release test contracts); #10400 (shared real-import-root/additional-version checker prerequisite, merged #10553); #10331, #10362 and #10371 (independent consumer admissions, open). Create/reuse later scoped issues as selected. #9177 remains separately owned.

## Guiding Intent

Make Python's type contracts executable and continuously enforced. Every
first-party Python source, test, tool, bootstrap helper and generated/vendored
copy must ultimately be accounted for and validated with MyPy strict mode.
Developers and agents get the same reproducible check locally as CI, before
publication. Intermediate non-strict admission is a migration state, not the
definition of success.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| lead | Own the plan, integration, PR completion and full campaign gate | The current scoped issue owner and its externally controlled source worktree |
| slice-owner | Execute one explicitly assigned, non-overlapping PR slice | A separately claimed issue/worktree named by the lead before dispatch |
| reviewer | Review each proposal and implementation through the repository policy | The target PR's normal automated and applicable Maintainer review |

## Coordination

- **Topology:** Independent per-slice PRs, never a concurrently edited shared staging branch. The lead may integrate non-overlapping test cohorts into one bounded reviewed slice after their isolated validation.
- **Host (owns PRs):** `lead` owns its PRs and verifies completion of delegated PRs.
- **Delegates:** The shared checker foundation (#10400 / #10553) has merged; Versioned Runtime (#10331), the Copilot Extensions Harness payload helpers/tests (#10362) and canonical Payload Invocation (#10371) remain distinct pending admissions in their assigned worktrees. Retained effort-helper, SSH, PR and Index service admissions have merged. Heavy unit/native qualification is staggered for actual test capacity, not a shared PR merge queue. Each owner receives an explicit bounded qualification and publication assignment; the lead retains integration, shared coverage/global documentation, the effort and full completion judgment. Completed children are finalized only after their merged evidence returns to this tracker.
- **Handoff:** A merged slice returns to the canonical README, updates its Plan/Validation/Journal, then selects the next unresolved item. A continuation retains #9807, this README, the current slice, live PRs and exactly one next action.
- Existing contributor PRs are reviewed/coordinated, not superseded. Shared-library changes precede dependent activations; unrelated leaf activations may proceed independently.

## Context

The initial investigation found no MyPy CI gate. `AGENTS.md` and
`CONTRIBUTING.md` encourage type hints; Ruff `F,E9` is not a type checker.
The [baseline](baseline.md) records a fixed source snapshot, syntactic annotation
coverage, progressive checker diagnostics and important limitations.
The [coverage tracker](coverage.md) records current merged enforcement by shared
library, plugin, service/manager and tool/support project. Classified paths and
prepared implementations are kept distinct from merged strict lockdown.

The implementation and PR boundaries are in [rollout](rollout.md). Existing
owners remain authoritative for dependency seasoning, test-environment
admission, canonical source/materialization and Main's governed release
controller. This effort adds a static-validation contract; it does not replace
those systems or authorize installed-runtime rollout.

## Request

> Make an effort to enforce 100% type-safety on all copilot-extensions python code, enforced by MyPy. You will need to plan our a way to get there, breaking the work into reasonably-sized PRs, to provide CI-time enforcement and activating projects part by part until we achieve compliance. You can use initial non-strict enforcement initially to get pieces in, and enforce strictness as codebases come closer to compliance and deltas are minimized. You'll want to provide simple local commands that agents run can to pre-validate, so they are not caught off guard by CI.

Additional operator request:

> How are we doing in overall coverage? We should ensure in our effort that we have a coverage tracker that represents plugins, services, and tools, and records as we get each section locked down.

> Yeah let's track both metrics

Confirmed operator responses:

- `effort_slug=python-static-type-safety, terminal_policy=strict-with-typed-boundaries`
- `execution_scope=plan-and-execute`
- `authorize_main_adoption=true`: after P3's source PR merges, add only the two
  read-only candidate MyPy steps to Main's
  `.github/workflows/validate-and-promote.yml` through its normal reviewed
  workflow-only bootstrap flow. No pause/resume, runtime rollout or CI bypass.
- `authorize_candidate_job_timeout=true`: the same reviewed adoption may raise
  only the read-only `source-units` job deadline from five to 20 minutes to
  accommodate setup, both six-minute MyPy steps, the unchanged 300-second
  unit packet and evidence upload.
- `review_block=accept-bounded-merge`: for #9932 only, accept the completed
  implementation review plus the verified one-line cohort-index correction
  after repeated reviewer-guidance tool failures. Required CI, resolved
  feedback and normal Maintainer/provider gates remain. This is not a
  standing exception for subsequent PRs.
- `dependency_policy=approve_scoped_exception`: permit only the reviewed
  `opentelemetry-semantic-conventions==0.66b1` declaration in the OTLP checker
  lock while the same lock pins its requiring stable SDK to `1.45.1`.
  Seven-day artifact age, non-yanked/known-timestamp checks and mandatory
  installation hashes remain. This is not a general prerelease allowance.
- `versioned_runtime_contract=dual-version-checking`: preserve existing runtime
  behavior and minimums while extending the existing checker to persistently
  qualify Python 3.10 and 3.11. No runtime TOML backport or interpreter-floor
  increase is authorized by this selection.

## Scope Approvals

- The operator authorized planning, normal reviewed implementation PRs, phased non-strict admission, eventual strict compliance and simple local pre-validation.
- The operator requested an effort-owned coverage tracker representing plugins,
  services and tools, recording each section's lockdown. Shared libraries and
  file-weighted accounting remain part of the original all-Python objective.
- The operator explicitly confirmed tracking both merged strict-project
  coverage and physical Python-file coverage.
- The confirmed terminal policy is every first-party file checked strictly, with typed dynamic boundaries and narrow reviewed third-party exceptions. It is not a literal ban on all `Any` expressions.
- No blanket ignores, unchecked first-party substitutes or permanent pending/basic cohorts count as completion.
- Plugin self-provisioning is being retired, with service implementations
  ultimately distributed outside Copilot plugin payloads. Qualification must
  validate explicitly declared Invocation consumers without requiring every
  Python application or thin connector to adopt that legacy model. This
  direction does not authorize service removal, runtime rollout or weakened
  validation of declarations that remain.
- Normal PR/CI/review gates remain. Main-controller adoption is limited to the
  explicitly approved two-step workflow change and job deadline above; no ruleset/CI bypass,
  pause/resume, service restart or installed rollout is authorized. The only
  dependency-policy exception is the exact reviewed prerelease declaration
  approved above; no age or integrity requirement is relaxed.
- For #10045's platform verification, the operator explicitly authorized starting
  the existing local Docker Desktop installation and preparing isolated test
  tooling. This is bounded test capacity, not installed plugin rollout, product
  Docker lifecycle acceptance or a new CI lane.
- For #10331, the operator selected qualification-only dual-version checking.
  The existing 3.11 TOML path must remain statically qualified without changing
  actual 3.10 behavior. A type-only guard without persistent 3.11 enforcement
  is insufficient. Directly dependent canonical generated copies retain their
  normal ownership and release checks; their consumers are not thereby admitted.

## Plan

The decomposition and proposed mechanics below are **agent-recommended**
realization of the operator's requested outcome. Each checkbox represents a
bounded PR or repeated bounded PR family, not permission for one repository-wide
rewrite.

### Phase 0 - Reviewed campaign

- [x] Land this plan and the baseline through the normal review gate; verify the literal Request and confirmed policy before execution.
- [x] Publish the agreed decomposition on #9807 and create/reuse scoped implementation issues as slices are selected.

### Phase 1 - Checker foundation and first admission

- [x] Deliver the typed local front door, reproducible checker preparation, project manifest and complete tracked-Python coverage ledger.
- [ ] Maintain the [portfolio coverage tracker](coverage.md), reconciling every
  library/plugin/service/tool project and recording merged lockdowns after
  each admission rather than counting prepared work as complete.
- [x] Activate the already-clean small libraries in strict mode and the checker infrastructure's own code/tests; verify actual negative MyPy controls.
- [x] Add required source-PR static checks using the same front door and project policies; preserve existing unit selection.
- [x] Land candidate-local static-checking source/controller contracts with frozen-input identity, failure propagation and helper decision qualification.
- [x] Adopt exact-candidate release static checks in Main through the separately approved workflow-only change; preserve Main control.
- [x] Prove live candidate enforcement and regression rejection under the adopted Main controller.
- [x] Document and validate local `--prepare`, per-project, `--enforced`, `--changed`, `--all` and inventory commands. Default checking never implicitly installs tooling.

### Phase 2 - Source and platform qualification

- [x] Land the bounded canonical Installation Context composition/source-map adapter (#9916); keep copies independently pending.
- [x] Expose the faithful composed Installation Context dependency to normal consumer checks without stubs or target reassignment (#10048).
- [ ] Make executed source fragments statically visible with source-mapped composition or genuine typed modules; qualify every source line and byte-identical copy without suppressing errors.
- [ ] Resolve tooling module identities, independent package environments, dependency/stub versions and declared interpreter/platform targets.
- [x] Persist additional interpreter qualification versions through the existing local/source/candidate checker, preserving the minimum target and default behavior (#10400 / merged #10553); Versioned Runtime's own qualification remains #10331.
- [x] Deliver ordinary real-import-root resolution with source-frozen custody and native `MYPYPATH` separator guards (#10400 / merged #10553), without admitting its pending consumers.
- [x] Deliver the faithful checker-only guard-module alias/source-view prerequisite (#10638 / merged #10866); preserve separate consumer, emitted/copy and native qualification.
- [x] Deliver bounded emitted-program checker visibility and binding completeness (#10938), without counting pending Harness targets as strict admission.
- [x] Deliver Payload Invocation's source/test split, source-bound catalog witnesses and authorized generated-output typing preparation with real ordinary Linux CI (#10371 / merged #11165); keep its pending project and separate consumer/native admission gates open.
- [ ] Qualify generated templates and release-materialized copies against the actually checked canonical source, including negative drift controls.
- [x] Make Dev-to-Main composition own the discussed Installer Engine and
  Invocation/helper/toolkit copies, rather than requiring contributor
  synchronization. Prove complete declared output materialization from
  stale or absent copies, unchanged Dev inputs, canonical-first Dev checks
  and exact composed-candidate correspondence before freezing and testing
  (merged #11265; complete exact-source copy acceptance in run 42682177708).

### Phase 3 - Shared-library ratchet

- [x] Strictly admit all Config Migrate source/test files with a declared YAML/stub environment (#9924).
- [x] Strictly admit all canonical Dropin Registry source/test files; keep copies independently pending (#9930).
- [x] Strictly admit all canonical Endpoint Rendezvous source/test files, preserving native ownership fixtures (#9933).
- [x] Strictly admit canonical Installation Context source, worker and every test/helper after the final fixture cohorts (#10018).
- [x] Strictly admit all four canonical Configuration Layers source/test modules (#10021).
- [x] Strictly admit all five canonical Core Delegation source/test modules (#10025).
- [x] Strictly admit all six canonical Fleet Contracts source/test modules (#10026).
- [x] Strictly admit canonical Repository Policy source/test modules, including the executable guest reader (#10029).
- [x] Strictly admit all canonical Fleet Index and Vault adapter source/test modules (#10036).
- [x] Strictly admit all canonical Work Coalescing Singleton source/test modules (#10039).
- [x] Strictly admit all eight canonical Update Telemetry source modules; owning consumer tests remain independently pending (#10043).
- [x] Strictly admit all canonical Installer Readiness source/test modules (#10044).
- [x] Strictly admit all canonical Docker Resource Producer source/test modules (#10045).
- [x] Strictly admit all canonical Single Instance Lease source/test modules (#10084).
- [x] Strictly admit all canonical Process Telemetry source/test modules with its declared optional-SDK checker environment (#10083).
- [x] Strictly admit all canonical Machine Transport source/test modules with its declared YAML checker environment (#10107).
- [x] Strictly admit all canonical Plugin Activation source/test modules with its declared YAML checker environment (#10108).
- [x] Strictly admit all canonical Plugin Resolve source/test/installer-fixture modules (#10141).
- [x] Strictly admit all canonical Venue Copilot source/test modules (#10166).
- [x] Strictly admit all 22 canonical Credential Relay source/test files with Python 3.10 native/copy qualification (#10222 / merged #10310); keep consumer projects independently pending.
- [ ] Strictly admit Versioned Runtime's complete source/test/helper scope with unchanged runtime behavior, persistent 3.10/3.11 qualification and canonical generated-copy correspondence (#10331).
- [ ] Strictly admit canonical Payload Invocation's complete Python source/test scope and executed/emitted helpers, preserving consumer-copy ownership and actual helper minimums (#10371).
- [ ] Strictly admit Installer Engine's complete source/helper scope with honest loader, process, configuration and platform contracts; preserve its independent bootstrap/runtime minimums and native/cold-flow acceptance (#10931).
- [ ] Admit near-clean leaf libraries through small annotation/narrowing PRs; activate directly in strict mode when possible.
- [ ] Admit remaining shared libraries in basic mode, then close their stricter deltas in separate PRs until every shared library is strict.
- [ ] Replace loose generics and unchecked boundary results with honest callable, protocol and validated data contracts; rerun affected consumer checks/tests.

### Phase 4 - Runtime and service ratchet

- [x] Strictly admit the complete unchanged Dispatch service Python source/test project (#10184 / merged #10189).
- [x] Strictly admit all 27 Index service source/test/helper files on its existing Python 3.10 minimum, preserving independent core-plugin ownership (#10206 / merged #10327).
- [x] Strictly admit all nine PR service source/test files on its existing Python 3.10 minimum, preserving original assertions and installed-runtime behavior (#10204 / merged #10332).
- [x] Strictly admit all 26 SSH service source/test files on its existing Python 3.10 minimum, preserving native ownership and transport behavior (#10211 / merged #10348).
- [ ] Activate each runtime plugin and standalone service independently, using basic mode only where needed and recording its remaining strict delta.
- [ ] Tighten each activated runtime/service to strict through component-sized PRs; cover source, optional extras and platform-specific implementations.
- [ ] Converge the larger Worktrees, Worktree Manager and Fleet Manager surfaces through subsystem-sized PRs, preserving lazy-loading and native behavior.

### Phase 5 - Tests, tools and bootstrap

- [x] Strictly admit AI Attribution's complete five-file Python helper/test portfolio (#10171), preserving shell emission and signature/disclosure behavior.
- [x] Strictly admit Context Handoff's complete six-file Python helper/test portfolio (#10179 / merged #10192), preserving native continuity and sync controls.
- [x] Strictly admit all three devcontainer helper/test files on their existing Python 3.11 minimum (#10183 / merged #10193).
- [x] Strictly admit all nine Harness Knowledge binding/overlay helper and test files, preserving Python 3.10 copied-helper compatibility (#10185 / merged #10201).
- [x] Strictly admit all five retained effort-helper source/test files on their existing Python 3.11 minimum, preserving native measurement and refusal controls (#10215 / merged #10360).
- [x] Strictly admit all three Agent Roles Python contract-test files on both existing 3.10 analysis targets, preserving every assertion and the non-authoritative JSON fixture boundary (#11473 / merged #11485).
- [ ] Strictly admit the Copilot Extensions Harness plugin's complete Python helper/test scope, preserving its actual helper minimums and payload ownership (#10362).
- [ ] Strictly admit the Efforts plugin's complete source/test and executed-program scope, retaining actual producer, namespace and selected-provider boundaries (#10955).
- [ ] Admit repository tools and CI helpers with unambiguous module identities and strict local/CI behavior.
- [ ] Admit tests in bounded package/fixture-family PRs, typing fixtures, doubles and monkeypatch interfaces without weakening assertions.
- [ ] Admit installer/bootstrap, hooks, payload-only Python, `.devcontainer`, effort-hosted executable helpers and every remaining inventory path.
- [ ] Close typed fixture/shared-helper cascades before consumer batches; do not exclude tests or bootstrap code to inflate compliance.

### Phase 6 - Complete compliance

- [ ] Prove zero unclassified/unqualified Python files and zero pending/basic projects, with every declared target passing strict checks.
- [ ] Verify required PR enforcement and strict qualification of the exact release candidate in the live provider; a merged workflow proposal alone is not proof.
- [ ] Remove temporary migration exceptions/ratchets, retain only reviewed third-party boundary exceptions, and update the actual contribution standard.
- [ ] Run final local/CI parity and negative coverage/type-regression controls, close #9807 only after these gates pass, set the effort Done and archive it.

## Validation Plan

- [ ] Unit controls cover manifest validity, complete file classification, additions/renames, safe selection, monotonic activation and dependency impact.
- [ ] Reconcile tracker rows and file totals with the authoritative manifest
  and inventory; local proposals and canonical-library locks must not imply
  complete consumer/copy enforcement. The 2026-11-08 UTC merged snapshot reconciles
  all 73 declared projects and 8,681 physical files at `b2b52122`, including the
  zero-physical-file pending vendored-library project. Remeasure against the
  current merged tree rather than treating a historical denominator as fixed.
  This recurring and final portfolio gate remains open.
- [x] Actual MyPy subprocess controls prove an incompatible argument/return fails basic mode and missing annotations/bare generics fail strict mode.
- [ ] Local and CI use identical pinned checker, dependencies, project config and interpreter/platform targets; unknown source/setup errors fail visibly.
- [x] Changed-scope checks include working-tree edits and affected activated consumers; changes to checker/config/workflows cannot skip enforcement.
- [ ] Composition and generated-copy controls reject omitted/reordered/changed fragments or stale source correspondence; no empty result can certify coverage.
- [x] Promotion-owned copy generation covers every declared Installer
  support and Invocation/helper/toolkit destination from canonical source,
  including stale and absent outputs, while refusing unsafe destinations.
  Dev checks canonical source without contributor snapshot synchronization;
  composed-candidate checks still require exact output correspondence.
  Prove unchanged source inputs and freeze/import of the resulting tree
  (#11265; all five exact-source acceptance jobs pass, including actual
  candidate canonical-drift rejection and restoration).
- [ ] **full-composed-consumer-qualification** (#9807 / #10362): preserve this
  unmet consumer gate independently of the merged checker-only #10866 and #10938 prerequisites.
  Prove the actual composed consumer, immutable 64-root boundary, copied/emitted
  helpers, native behavior and full frozen-candidate/promotion qualification.
  A passing development packet or focused source/frozen controls is not this
  proof; failed full-candidate attempts remain failures, not a flake or CI waiver.
- [ ] Each typing change preserves behavior through the smallest existing focused tests plus directly relevant integration/native checks. Skips are explicitly justified in that PR.
- [x] Credential Relay preserves its production behavior, assertions and deadlines across both strict targets, Python 3.10 native/copy tests and fresh exact-input source CI (#10310).
- [x] Index service preserves its original function/assertion contracts across both strict targets, Python 3.10 native/copy tests, composed-source qualification and fresh exact-input source CI; the eight original skips receive no additional credit (#10327).
- [x] PR service preserves all 81 original functions and 114 assertion expressions across both strict targets, actual Python 3.10 installed/native tests and fresh exact-input source CI (#10332).
- [x] SSH service preserves all 254 original function contracts, 299 assertion expressions and four embedded literals across both strict targets, actual Python 3.10 Linux/Windows qualification and fresh exact-input source CI (#10348).
- [x] Retained effort helpers preserve all 39 original function contracts and 31 assertion expressions across both strict targets, actual Python 3.11 Linux/Windows controls, composed-source qualification and fresh exact-input source CI (#10360).
- [x] Agent Roles preserves all 46 functions, 191 assertions and parameterizations; both strict targets, all 61 original tests and normal-checker regression rejection pass, followed by exact-head approval and required source CI (#11485). This does not qualify live role/provider behavior.
- [x] Payload Invocation preparation preserves its canonical and authorized copied-source contracts, rejects real catalog/type/data drift and passes complete ordinary Linux unit execution (#11165). The verified synthetic merge contains every current-head admitted test/source binding; this is not canonical-library, consumer, native or global strict admission.
- [x] Shared checker controls preserve default qualification and ordinary import-root/source-frozen custody semantics, including native separator rejection (#10400 / #10553).
- [x] Persistent dual-version checker controls reject a type error confined to the 3.11 TOML branch on both analysis platforms through local/source/frozen-candidate entry points (#10400 / #10553); four exact-head cases complete 28 actual MyPy analyses. Preserving Versioned Runtime's actual 3.10 behavior and native/copy qualification remains separately open under #10331.
- [x] Fresh checker preparation and a credential-free local subprocess check exercise the real developer command without executing product code or collecting tests.
- [x] Required source gate actually rejects a type regression on activated code.
- [x] Required exact-candidate release gate rejects a type regression on activated code.
- [ ] Final strict gate rejects a newly unclassified Python file.
- [ ] Final full inventory, all strict projects/targets, documented boundary exceptions and live PR/release qualification are green together.

## Proposal

Use a monotonic `pending -> basic -> strict` project manifest and a complete
file ledger. Basic mode includes `--check-untyped-defs`; strict mode adds
MyPy's strict checks. No diagnostic allowlist masquerades as passing checking.
During migration, `--enforced` gates activated projects and reports pending
coverage explicitly; `--all` probes the entire portfolio and cannot claim global
success while work is pending. Final compliance additionally requires every
project strict and every physical Python path qualified.

See [rollout](rollout.md) for the CLI contract, per-PR boundaries, setup and
composition prerequisites, and review/validation rules.

## Journal

- [2026-11-09 - Bounded promotion qualification and return](journal/2026/11.09%20Bounded%20promotion%20qualification%20and%20return.md)
- [2026-11-07 - Promotion-owned copies and source qualification](journal/2026/11.07%20Promotion-owned%20copies%20and%20source%20qualification.md)
- [2026-11-06 - Consumer and installer qualification](journal/2026/11.06%20Consumer%20and%20installer%20qualification.md)

### 2026-10-31 - Kickoff and baseline

- #9807 supplied the seed. The operator confirmed the effort slug, terminal policy and planning-plus-execution scope.
- Compatible target-owned efforts adoption was verified; this repository's flat effort layout and normal `dev` PR gate apply.
- A read-only census and progressive checker baseline were captured before source implementation. The composition probe changed no runtime code.
- The plan is proposed for review. No CI enforcement or project activation has been delivered yet.

### 2026-10-31 - Reviewed plan and first implementation slice

- #9829 merged after a current-head Copilot approval with no findings and green required CI. The source worktree was pulled forward onto the reviewed plan.
- The reviewed decomposition and baseline were linked on #9807; #9831 owns P1's local checker, full inventory and initial strict source qualification.
- The active slice transitions from `Phase 0 - Reviewed campaign` to `Phase 1 - Checker foundation and first admission`. P2/P3 retain actual PR/release enforcement; no implemented or live gate is claimed by plan completion.

### 2026-11-01 - Checker foundation merged; source enforcement selected

- #9838 merged into `dev` after exact-head Copilot approval, resolved findings
  and green required CI. P1's local checker, complete physical inventory and
  four strict projects are delivered; 63 projects remain pending.
- The checker and cache-helper controls pass 51 tests. Actual MyPy negative
  controls include basic/strict regressions and a non-ASCII Windows target.
  Strict checking passes all admitted projects on Linux and Win32 analysis
  targets. Fresh hash-locked preparation and the real local subprocess path
  were exercised without importing product code or collecting its tests.
- Review fixes preserve Python 3.10 bootstrap, immutable requirements,
  finite installation, selector-independent inventory, safe response files,
  repair-marker invalidation and readiness under the environment lease.
- P2 is the next active slice: required source-PR checking through the existing
  aggregate, local changed-scope parity and conservative consumer rechecking.
  P3 retains candidate-release qualification. Neither CI enforcement nor full
  type safety is claimed by this foundation merge; no installed rollout occurred.

### 2026-11-01 - Required source gate qualified on the draft shipping PR

- #9854's clean hosted candidate passed all source checks, the unchanged unit
  packet and the required aggregate in
  [run 41284762059](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41284762059).
  Fresh pinned checker preparation and every admitted platform target ran.
- A temporary, unconsumed module in an admitted source directory supplied an
  incompatible assignment. MyPy rejected precisely that assignment, units
  passed and the required aggregate failed in
  [run 41285005852](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41285005852).
  The probe is removed before shipping readiness; this is negative validation,
  not a retained exception or failing source.
- The changed-scope command conservatively checks every admitted consumer.
  Real Git controls cover staged/unstaged edits, additions/deletions, pinned
  comparison and invalid references. Source-hash admission was refreshed only
  for the changed CI guard and its existing packet fixture: node selection,
  production coverage targets and budgets are unchanged.
- Local validation passed 53 checker/cache controls, 236 CI/packet-fixture
  controls, strict admitted checks and the full 3,184-node unit packet with
  coverage. The source-CI PR still requires a clean final head, review and
  merge; candidate-release qualification and the full campaign remain open.

### 2026-11-01 - Required source enforcement merged

- #9854 merged into `dev` after the accepted exact-head owner review, resolved
  feedback and fresh required CI
  [run 41286252336](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41286252336).
  The source worktree is pulled forward; P2's source gate and conservative
  local parity are complete. The temporary negative probe is absent.
- The signing-independent fixture control passes and remains strict. The final
  PR description records documentation/CLI impact and source-versus-release
  scope. Unit nodes, coverage targets, budgets and exact-input reuse remain
  unchanged; no failed check was bypassed.
- P3 is next: qualify the admitted type contracts against the immutable
  candidate actually released, preserving source correspondence, release
  helper admission and independently owned Main control. The combined
  source/release Plan and Validation obligations are split above so source
  completion cannot masquerade as release qualification.
- Four projects are strict and 63 are pending. The effort and #9807 remain
  active; no all-Python compliance, Main activation or installed rollout is
  claimed.

### 2026-11-01 - Exact-candidate source contract selected

- #9862 owns P3. The source controller proposes candidate-local preparation and
  enforcement immediately after importing the frozen candidate, before unit
  evidence and signing/promotion admission. It reuses the same front door and
  existing read-only job rather than a parallel release path.
- Data-only controls reject omitted/reordered/skipped checks and a replaced
  frozen importer; legacy Main compatibility stays independent. The compatibility
  command reports prepared candidate typing separately from active Main typing.
- Actual Main-controller adoption and live candidate type-regression refusal
  remain open requirements. This source proposal does not activate, resume,
  publish or install anything, and cannot complete the campaign by itself.
- The first real Windows freeze exposed CRLF output from version/promotion
  writers, rejected by the repository's LF policy. Existing production-writer
  controls reproduced the JSON/Python violations. The coupled fix explicitly
  emits LF for LF-targeted outputs without weakening Git or changing TOML
  behavior; the real freeze must be retried before source publication.
- The related promotion suite exposed an outdated inline-body assertion after
  the renderer moved to selectors. Its version-drift regression now verifies
  the actual selector version metadata. No production guidance policy changed.
- The operator separately approved the bounded Main workflow-only adoption
  after source review and merge. That authority does not activate it yet;
  implementation, review and actual candidate-gate qualification remain open.
- The retry exposed the same LF violation in materialized installer metadata.
  Its owned writer now emits and compares canonical UTF-8 bytes rather than
  hiding CRLF through universal-newline reads. Both canonical and already
  materialized consumer controls verify output and matching-CRLF repair.
- A newer promoted source required a normal rebase before candidate preparation.
  The replay preserves upstream helper admissions and removes one duplicate
  manifest entry. Installer reference rewriting now emits the declared shell
  newline policy (LF for Bash, CRLF for PowerShell);
  the real consumer test failed before the repair and passes afterwards.
  Current inventory is 7,313 physical Python files, four strict projects and
  64 pending projects. The campaign remains active.
- The current packet exposed a Windows native ownership probe in a lifecycle
  outcome unit control. That test now isolates the unrelated native probe;
  production ownership checking and the no-native-effects unit contract are
  unchanged.
- The next pure-unit control required isolating native artifact opening at its
  fixture boundary while preserving actual byte reads and production fences.
  The real freeze then reached commit creation and exposed Windows' command-line
  length limit. Promotion commit and tag messages now use UTF-8 stdin, verified
  by a real oversized non-ASCII message round trip. No message truncation,
  alternative candidate, signing change or publication occurred.
- Hosted P3 qualification exposed Linux's eager `ctypes` binding on collection.
  Ownership mocking now belongs only to the promotion unit adapter and imports
  the native owner only on Windows. The original library test is unchanged;
  no shared-library payload changes or unnecessary consumer bumps are included.
- P3 review identified a job deadline shorter than its added steps, effective
  shell overrides that could mask failure, and text-stdin newline translation.
  The operator approved the bounded 20-minute job deadline in addition to the
  two steps. Contract controls reject failure-masking step/job/workflow shells;
  promotion messages use binary UTF-8 input with a raw commit-object assertion.
  The unchanged unit budget remains independent. Focused controls pass 109 tests.
- Current Dev now includes the independently landed lifecycle and guardian
  fixture repairs. The rebase preserves those stronger scoped controls and
  removes this PR's redundant isolation fixtures; only the P3 source contract
  and coupled materialization/message fixes remain in the proposal.
- Review also exposed missing release-helper discovery for candidate-local
  checker commands. Discovery now includes that checker and its namespace-
  relative import closure, with three newly admitted implementation files,
  four decision targets and two actor-free controls. The real compatibility
  command refused the missing admission before enrollment. Focused helper,
  shell and checker controls pass 136 tests; exact packet coverage remains
  required before merge.

### 2026-11-02 - Candidate source qualification merged; Main adoption selected

- #9881 merged as `2e64d1874a277040dc85a832e545820ad5068d7c`
  after exact-head Copilot approval, resolved feedback and green required CI.
  [Run 41368464711](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41368464711)
  passed all 3,508 controls with actual helper decision coverage in 111.924
  seconds; the final same-head required run 41369625276 also passed.
- The final source froze successfully. Its imported candidate passed exact
  source/Main/tree identity validation, fresh pinned preparation and all
  admitted Linux/Win32 strict checks. Local Windows packet runs that exceeded
  the unchanged 300-second budget were not accepted as passing receipts;
  hosted exact-head qualification passed without widening that budget.
- The source worktree is pulled forward. #9807 and #9862 remain open.
  Source qualification does not activate the preserved Main controller or
  complete the portfolio: four projects are strict and 64 remain pending.
- Next is the explicitly approved, separately reviewed Main workflow-only
  adoption: the two read-only MyPy steps and the 20-minute combined job
  deadline, with the unit packet's own 300-second bound unchanged. Live
  candidate rejection remains required; no installed rollout is authorized.

### 2026-11-02 - Reviewed Main workflow-only adoption merged

- #9914 merged as `8492f3540566c1474aba486fb5415b913c05e2d3`
  after exact-head Copilot approval, the normal Main source gate and identifier
  scans. Managed completion reported no review-gate bypass.
- The entire diff was one workflow file: the two six-minute candidate MyPy
  steps and the approved 20-minute combined job deadline. Full-document
  comparison proved every other Main controller setting unchanged. The unit
  packet remains bounded at 300 seconds.
- Configuration adoption is complete; actual provider execution and candidate
  type-regression rejection remain open. No pause/resume, installed rollout
  or global repository configuration change occurred.

### 2026-11-02 - Live candidate enforcement observed; canonical source view selected

- [Run 41370906756](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41370906756)
  executed the adopted Main controller, passed pinned candidate preparation,
  candidate MyPy enforcement and source units, then completed the normal
  non-admin generated promotion #9918. The negative candidate-regression proof
  remains open under #9862; success is not a substitute for rejection evidence.
- #9916 owns the bounded P4a adapter for canonical Installation Context.
  Composition and copy inheritance are split: the existing vendoring owner
  retains copy membership, and copies remain independently pending until
  correspondence is explicitly qualified.
- The checker-only view follows the actual literal loader order and maps
  diagnostics back to physical source lines. Focused controls cover actual
  basic/strict MyPy failures and a clean shared namespace, omitted/reordered/
  modified inputs, changed loader behavior and suppression/code-loss hazards.
  The original runtime loader and fragments are unchanged.
- Initial local validation passes 35 focused controls, strict checker typing,
  all eight admitted platform targets and Ruff F/E9. A real complete-project
  strict probe now reaches 496 errors across 29 files (including tests), rather
  than independent-fragment namespace cascades. It remains a failing pending
  probe, not library admission. Four projects are strict and 64 remain pending.
- A same-source Linux strict comparison of the 21 canonical implementation
  files reports 3,227 errors / 3,125 undefined-name diagnostics when checked
  independently, versus 85 errors / zero undefined-name diagnostics across
  the two composed/worker targets. These are actual MyPy subprocess results;
  no runtime or annotation changes were needed to remove the false namespace
  cascades. Both probes still fail, as required by their remaining typing debt.
- The new adapter is also byte-admitted as a release-helper decision target.
  One actor-free candidate-unit control checks real canonical composition,
  diagnostic mapping and stale bytes/membership rejection. The whole-packet
  deadline remains 300 seconds; full packet qualification is still required.

### 2026-11-02 - Frozen-candidate rejection proven; source-view qualification green

- The normal non-publishing candidate owner froze a synthetic source commit
  containing one incompatible assignment in the already-strict Worktree
  Identity library. Its existing unit-source digest was coherently updated;
  no stale-manifest or integrity failure substitutes for the typing control.
- Normal frozen import and materialized source/Main/candidate-tree validation
  passed. The candidate-local checker first refused missing preparation,
  explicit fresh `--prepare` passed, then `--enforced` returned 1 for exactly
  the deliberate `[assignment]` regression. The other admitted projects passed
  both analysis targets. Materialized identities still validated afterward.
- Together with the adopted Main run 41370906756 and the non-skippable
  failure-propagation contract controls, this closes P3's candidate gate.
  The negative control was local and unpublished: no deliberately broken Dev
  commit or failing release dispatch was submitted, because the required
  source type gate must not be bypassed to manufacture that provider run.
- P4a's hosted exact-head [run 41374055516](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41374055516)
  executed all 3,554 units with coverage once in 66.505 seconds and passed
  required guards. The local Windows run passed every assertion in 306.24
  seconds but exceeded the unchanged 300-second packet deadline; that local
  attempt was correctly refused and is not a qualifying receipt.
- The active stretch is now Phase 2's canonical source views, copy
  correspondence and environment/module qualification. Source views do not
  admit the remaining library/test debt: the full campaign stays Active at
  four strict / 64 pending projects.
- P4a review found two source-correspondence gaps: byte tokenization missed a
  directive after a CR-only newline, and AST parsing alone accepted a misplaced
  future import that actual runtime compilation rejects. Original sources now
  pass non-executing compilation, and directive scanning normalizes universal
  newlines before tokenization. Both cases have focused and required actor-free
  regression controls; physical source maps also cover LF, CRLF and CR input.
  Final focused validation passes 40 checker controls and 48 release/helper
  controls, plus all eight admitted MyPy analysis targets and Ruff F/E9.

### 2026-11-02 - Canonical source view merged; first complete leaf admission selected

- #9922 merged as `12758f925bf89745e01a8989a274da63b888aa08`
  after exact-head Copilot approval, resolved feedback and green required
  [run 41376941592](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41376941592).
  P3's candidate qualification and P4a's bounded canonical source view are
  complete. The source worktree is pulled forward; copy correspondence,
  other fragment families and the full strict portfolio remain open.
- #9924 is the next P5/L1 slice: all six Config Migrate source/test files,
  a separately hash-locked YAML/stub environment through the existing
  preparer, and minimal annotations/narrowing without runtime changes.
  The pre-change probe reports two basic missing-stub diagnostics and
  34 strict diagnostics across three files. Direct strict admission is
  preferred if the actual delta stays small.
- The complete project now passes strict checks on both declared Linux/Win32
  analysis targets after typed migrator generics, version-marker narrowing and
  fixture annotations. The separately generated flat lock preserves all 12
  base checker entries and adds only PyYAML 6.0.3 and
  types-PyYAML 6.0.12.20250915; every artifact predates the seven-day cutoff.
  The existing governed compiler/install owner enforces seasoning. Its export
  was normalized to the checker's existing SHA-256-only contract.
- Fresh project preparation, all ten admitted MyPy analysis targets, 69 focused
  library/checker controls and Ruff F/E9 pass locally. Only the three changed
  library/test byte digests are refreshed in unit admission; node selection,
  production decision targets and packet budgets are unchanged. This is a
  proposed fifth strict project, not a merged or complete campaign.
- The original migration controls also pass through their release adapters
  with the manifest controls (91 tests). Exact-source/helper admission,
  install contract and both physical/pointer consumer inventories pass;
  the changefile names the library and all five consumers. Full actor-free
  packet coverage remains the required hosted gate. Native Windows
  preparation is exercised; hosted Linux preparation remains required.
  A machine-provisioning clean room is not applicable to annotation-only
  runtime changes and isolated checker-dependency preparation.

### 2026-11-02 - Config Migrate merged; Dropin Registry selected

- #9929 merged as `9301cb5401284e5a08adaf7d9d208b6e227ef3e7`
  after exact-head Copilot approval with no findings and all required checks.
  Hosted [run 41378233039](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41378233039)
  passed seasoned preparation, both target policies and all 3,575 unit controls
  with coverage once in 59.450 seconds. The source worktree is pulled forward.
- Five projects are now strict and 63 remain pending. The next bounded L1 slice
  is #9930: all six Dropin Registry source/test files. Its pre-change strict
  probe reports 21 errors across two files: a generic local plus fixture
  annotations and a stat-result double. The canonical implementation needs
  no runtime logic changes; physical copies retain independent pending
  typing ownership even when synchronized through the existing vendoring tool.
- The candidate passes strict checking for all six canonical files on both
  declared targets. The Win32 target exposed an unavailable `os.mkfifo` call
  hidden by the old untyped fixture; its existing skip now executes inside the
  test so the checker can follow that platform guard. A spec-bound stat-result
  test double preserves the nonzero reparse-tag control without an unchecked
  cast or widening the library's input contract.
- All 12 admitted MyPy analysis targets, Ruff F/E9, 37 original/release-adapted
  controls and install/vendoring/admission checks pass. Native FIFO creation
  is the sole Windows skip; the existing actor-free FIFO refusal adapter
  still runs. Fresh isolated processes load each of the three physical copies
  and exercise real atomic writes and native directory scans successfully.
- Only the canonical scan annotation is synchronized into physical copies.
  Their independently authored tests are preserved and their typing ownership
  remains pending. The changefile names the library and all ten physical/
  pointer consumers. Only two unit-source digests change; nodes, decision
  targets and packet budget are unchanged. Required hosted packet qualification
  and review remain before merging this proposed sixth strict project.

### 2026-11-02 - Dropin Registry merged; Endpoint Rendezvous selected

- #9932 merged as `f6a6d3bf6b8fabff5e868c985e5b3b12afc69b67`
  with all required checks green in
  [run 41379486808](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41379486808).
  Its completed implementation review found only a stale cohort count; the
  sole follow-up corrected that index line and resolved its thread.
  Two later reviews deferred assessment because a required guidance skill
  was unavailable. The operator explicitly accepted that reviewed implementation
  plus the verified one-line correction for normal Maintainer completion.
  The bounded reason is recorded on the PR; no CI gate was bypassed.
- The source worktree is pulled forward after exact stable patch equivalence
  proved the merged squash contains all published work. Six projects are
  strict and 62 remain pending. #9807 and the full campaign stay open.
- #9933 selects all five Endpoint Rendezvous source/test files. Its initial
  Linux strict probe reported 65 errors across three files. Both declared
  targets now pass after JSON dictionary generics, fixture annotations,
  explicit optional-result/pipe narrowing, platform-safe native constants
  and filesystem callbacks matching `Path.unlink`'s actual signature.
- All 131 existing behavior/native tests pass in 9.74 seconds, including
  four-source ownership/concurrency controls for the canonical implementation
  and independent in-package variants. Ruff F/E9 passes. Runtime validation,
  locking, publication and process cleanup behavior are unchanged.
  The dynamic JSON value boundary remains explicit; existing #5903/#5904
  tracking is not closed or superseded by this typing slice.
- No release-unit source digest, node, decision target or budget changed:
  this library is not part of that packet's admitted source set. Required
  hosted typing/packet CI and normal review remain before this proposed
  seventh strict project can merge. In-package variants retain independent
  pending typing ownership.

### 2026-11-02 - Hosted-review instruction delivery repaired

- #9940's review deferred assessment because the checked-in catch-all
  unconditionally required a CLI-only skill/resolver workflow. The referenced
  review documents were intact; the generated instruction body was stale
  relative to its current owning template.
- The operator selected repair-first, not a further review exception.
  #9945 merged as `ff59152d405338d607a5f174b3885096cc3bf74d`,
  closing #9944 after exact-head Copilot approval with no findings and all
  required checks in
  [run 41421184069](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41421184069).
  The isolated repair worktree is finalized.
- The scoped generator refreshed only the catch-all locator, complete reviewed
  fallback and corresponding lock entry. The other seven lock entries are
  unchanged. All 151 delivery controls, offline scan, actual repository
  fallback selection without enabled-source lookup and no-op regeneration
  pass. No review/CI gate or runtime behavior changed; no rollout was requested.
- The typing worktree is pulled forward. Next: publish the same Endpoint
  Rendezvous candidate on the repaired base, obtain a genuine fresh review,
  merge only after required checks, then continue #9941's four remaining
  leaf-library helper/test files. #9807 remains active.

### 2026-11-02 - Endpoint Rendezvous merged; leaf-library tests selected

- #9940 merged as `fc9fd32bfe9ce823f37d69b894e8bc90118f96c8`
  after exact-head Copilot approval with no findings, no unresolved threads
  and all required checks in
  [run 41422127217](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41422127217).
  Normal review was restored; no bounded exception was used for this slice.
- The source worktree is pulled forward after a backup and exact stable
  patch equivalence proved all local commits were present in the merged
  squash. Seven projects are strict and 61 remain pending.
- #9941 selects the four remaining test/helper files of Remote Login Shell,
  Session Liveness Probe and Worktree Identity. Their existing source and
  `-remaining` project names remain stable. The measured strict deltas were
  six, ten and eight diagnostics respectively.
- The candidate annotates test parameters/returns and wire-reader callables,
  retains invalid-value fixtures through `object` boundaries, and narrows
  optional probe reasons before existing substring assertions. Production
  code, test parameter matrices and runtime behavior are unchanged.
  The full campaign and later leaf-library admissions remain open.
- All 20 admitted analysis targets, 134 focused library/checker tests and
  Ruff F/E9 pass. Only the two changed test-source digests in the release
  unit manifest are refreshed; its selected nodes, decision targets and
  packet budget remain unchanged. The Session Liveness Probe tests are
  outside that packet's admitted source set.
- This proposes ten strict projects and 58 pending projects. Hosted
  source/packet CI and normal review remain required before admission lands.
  The changefile covers all three libraries and their eight unique consumers.
  No installer/bootstrap or runtime source changed, so a provisioning clean
  room and external runtime rollout are not applicable.

### 2026-11-02 - Initial leaf tests merged; Lazy CLI Dispatch selected

- #9949 merged as `7f64116586b9343ab15aec8203f703a18581b00b`
  after exact-head Copilot approval with no findings, no unresolved threads
  and all required checks in
  [run 41423907266](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41423907266).
  The source worktree is pulled forward. Ten projects are strict and 58
  remain pending; the full effort stays active.
- #9951 selects all three canonical Lazy CLI Dispatch source/test/helper
  files. Its initial strict probe reported 30 diagnostics across two files.
  The candidate annotates tests and preserves each reflective helper's
  callable signature through `ParamSpec` and a return type variable.
  Narrow casts record the existing requirement that a dynamically selected
  override honors the callable it replaces; no runtime validation is removed.
- Both declared targets and all 22 admitted targets pass. All 14 original
  behavior tests and their release adapter pass; existing override-selection
  tests additionally invoke the selected callable. Real pinned MyPy client
  controls accept valid calls and reject two incorrect arguments and two
  incorrect return assignments, rather than silently erasing signatures.
- Only the two changed release-unit source digests are refreshed; selected
  nodes, decision targets and packet budget are unchanged. The changefile
  covers the library and its sole consumer, Agent Worktrees, whose editable
  reference already resolves the canonical source. No physical-copy rewrite
  or pending-caller qualification is claimed.
- This proposes 11 strict projects and 57 pending projects. Hosted
  source/packet CI and normal review remain required. No installation,
  bootstrap, dispatch selection or lifecycle behavior changes, so a separate
  provisioning clean room and installed-runtime rollout are not applicable.
- #9953's first review recommended retaining the locally exercised MyPy
  clients as automated regressions. Four checked-in checker cases now cover
  valid and invalid clients for both helpers; exact callable assertions in the
  library tests also run in the required static cohort, so signature erasure
  cannot quietly pass CI. The tests reuse the existing checker environment
  and dependency pins. All 58 library/checker tests and both library targets
  pass; the updated test-source digest is refreshed without changing packet
  selection or budgets.

### 2026-11-02 - Remaining hosted-review selectors repaired

- #9953's post-fix reviews confirmed the callable regression finding was
  resolved but deferred guidance acquisition again. The earlier catch-all
  repair was incomplete: five other checked-in selectors still required the
  obsolete CLI-only resolver/authentication workflow. No review document
  reference was missing or deleted.
- #9959 merged as `b5cf658c3b6871e471160b98594f85c020ea787a`,
  addressing #9957 after exact-head Copilot approval with no findings and all
  required checks in
  [run 41429360484](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41429360484).
  Its isolated worktree is finalized. All 171 focused delivery controls,
  eight actual offline fallback selections, offline scan and deterministic
  regeneration pass.
- The complete existing eight-source roster now uses current locators and
  reviewed offline fallbacks. Enabled plugins, review gates and runtime code
  are unchanged; no installed rollout occurred. The typing worktree is pulled
  forward for a genuine fresh assessment of #9953. Ten projects remain strict
  and 58 pending until that proposed admission actually merges.

### 2026-11-02 - Direct guidance-file reads clarified

- The next #9953 review still deferred on the skill/resolver despite the
  refreshed roster. The current renderer listed file paths but did not
  explicitly distinguish ordinary repository file reads from source lookup.
  #9957 retained that remaining delivery gap rather than treating a deferred
  assessment as a passing review.
- #9963 merged as `adafc0cee68b4a6b03fc9cb6a9592005ce0b35e3`
  after exact-head Copilot approval with no findings and green required
  [run 41432156252](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41432156252).
  Its isolated worktree is finalized. The renderer and all eight committed
  locators now explicitly allow file-tool reads without a skill, Python or
  resolver, reserving source lookup for an unclear path.
- All 279 focused projection/delivery controls pass, including the unchanged
  legacy/current authority non-override controls. An initial wording that
  imposed selection precedence was rejected by those controls and is not part
  of the merged change. Actual offline selection, deterministic generation,
  offline scan, Ruff and install-contract checks pass.
- Source-specific delivery/full-read rules, reviewed fallback bodies,
  production budgets, enablement and review gates are unchanged. No installed
  rollout occurred. The typing worktree is pulled forward for #9953's fresh
  assessment; the full strict-typing campaign remains active.

### 2026-11-02 - Lazy CLI Dispatch merged; Bootstrap Killswitch selected

- #9953 merged as `a67a668ebe832c5f31d48f6b747e4dd85fb450e4`
  after a genuine exact-head Copilot approval with no findings, its regression
  thread resolved and all required checks in
  [run 41432933210](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41432933210).
  No review exception was used. The repaired file-read guidance restored
  the normal review flow.
- The source worktree is pulled forward after a backup and exact stable
  patch equivalence proved all four local commits were present in the merged
  squash. Eleven projects are strict and 57 remain pending.
- #9964 selects all three canonical Bootstrap Killswitch Python files.
  Its initial Linux strict probe reports 28 diagnostics across the source
  helper and two shell-contract test files. The slice preserves default-cell
  scope, fail-open reads, atomic publication, parser dispatch and shell
  behavior; physical consumer copies retain independent pending typing
  ownership. No switch activation or installed-runtime rollout is requested.
- The candidate passes strict checking for all three files on both declared
  targets, and all 24 admitted analysis targets pass. State records use
  `dict[str, object]`; the parser-installed handler retains its existing
  callable dispatch through a narrow cast. Fixture annotations and optional
  executable assertions preserve the existing shell availability skips.
- All ten shell controls pass under PowerShell 7 and again under Windows
  PowerShell 5.1. The 27 CLI/hook consumer controls pass, and the separately
  enabled cached-manifest hook integration passes real subprocess/IPC
  stop/restore and fallback behavior. All state is redirected into test-owned
  temporary paths. The native Bash lane is not run on this Windows host;
  its only changes are fixture/return annotations, not the shell guard.
- Existing source synchronization updates exactly three Python copies.
  Their own typing ownership remains pending; no unit-source digest, node,
  decision target or packet budget changes. Ruff, install contract and the
  complete 13-adopter synchronization check pass. This proposes 12 strict
  projects and 56 pending projects; hosted source/packet CI and normal review
  remain required. Annotation-only runtime changes do not require a separate
  provisioning clean room or installed-runtime rollout.

### 2026-11-02 - Bootstrap Killswitch merged; Peer Launch selected

- #9966 merged as `92df5341f95380434d46574a684fce2236df85e7`
  after exact-head Copilot approval with no findings, zero open review
  comments and all required checks in
  [run 41434649941](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41434649941).
  The source worktree is pulled forward. Twelve projects are strict and 56
  remain pending; the full effort remains active.
- #9967 selects all three canonical Peer Launch source/test files.
  The initial Linux strict probe reports 65 diagnostics, mostly fixture
  annotations and optional importlib-spec narrowing, plus platform-specific
  subprocess keyword typing and two dynamically loaded record boundaries.
  Preserve same-cell governance, validation ordering, headless flags,
  environment isolation and dependency-free bootstrap. Physical copies and
  pending consumer projects retain independent qualification obligations.
- The candidate passes both declared targets and all 26 admitted analysis
  targets. Its optional subprocess `creationflags` dictionary is explicitly
  typed; narrow casts retain the existing dynamically loaded validated-record
  boundaries. Fixture annotations and importlib-spec assertions preserve all
  existing controls and loader behavior.
- All 37 canonical, release-adapted environment and Worktrees consumer
  controls pass. A real native subprocess invoked with the canonical keyword
  helper also passes. Existing source synchronization updates exactly 12
  packaged copies. Installation-context primitive synchronization remains
  independently unchanged and green.
- Only the three changed release-unit source digests are refreshed; selected
  nodes, decision targets and packet budget are unchanged. The existing MCP
  source-only footprint refresher updates its `src` producer hash without
  changing semantic revision 36 or any teardown contract. Ruff, headless,
  source-admission, install-contract and vendoring checks pass.
- This proposes 13 strict projects and 55 pending projects. Hosted
  source/packet CI and normal review remain required. No installation,
  provisioning, launch protocol or lifecycle semantics change, so a separate
  provisioning clean room and live external runtime rollout are not
  applicable. Actual peer installation or activation is not requested.

### 2026-11-02 - Peer Launch merged; larger canonical qualification resumed

- #9969 merged as `cbe46f258dcf585f3c7c277089fc61a12601377a`
  after exact-head Copilot approval with no findings, zero open review
  comments and all required checks in
  [run 41436563919](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41436563919).
  The source worktree is pulled forward. Thirteen projects are strict and 55
  remain pending; the full effort stays active.
- Return to Phase 2's larger Installation Context qualification gate using
  its already-qualified composed source view. Measure the complete current
  project, including tests, before selecting a bounded basic/strict ratchet.
  Canonical admission will not silently qualify physical copies or close
  the independently pending composition/correspondence obligations.
- The complete 51-file project maps to 32 checker targets. Its Linux basic
  probe reports 249 diagnostics across 35 mapped physical files; strict
  reports 496 across 43. MyPy groups the composed source as one module, so
  its own file-summary counts differ from physical source-map counts.
- #9971 selects a preparatory base/files-fragment slice, not admission.
  The always-raising failure helper has an honest `NoReturn` contract;
  native field/FFI result types and distinct descriptor/path helper names
  remove false flow/signature conflicts. The final digest loop avoids
  shadowing its path-encoding callable. Runtime validation, traversal,
  failure, hashing, publication and cleanup behavior are preserved.
- Remaining whole-project diagnostics are 219 basic / 458 strict on Linux
  and 205 basic / 445 strict on Win32. Both edited fragments have no mapped
  diagnostics on either declared target, but every whole-project probe still
  exits nonzero. No filtered result, ignored diagnostic, new exclusion or
  unchecked first-party substitute is treated as qualification. Counts remain
  13 strict / 55 pending projects.
- Nine focused native snapshot/reparse/hash controls pass; the two POSIX
  replacement-primitive cases retain their explicit Windows skip. All 26
  admitted targets, Ruff, source-unit admission, install contract and the
  complete canonical-copy check pass. The source owner synchronizes exactly
  54 copied fragment files. Only two unit-source hashes and MCP's source-only
  producer hashes change; revision 36, nodes, decision targets and packet
  budget are unchanged.
- Hosted unit/source CI and normal review remain required. These are typing
  preparations, not provisioning or lifecycle changes; no separate fresh-box
  clean room or installed rollout is requested. Later source/test preparations,
  signature preservation for the validation decorator, genuine whole-project
  admission and physical-copy qualification remain open.

### 2026-11-02 - Native preparation merged; decorator contracts selected

- #9973 merged as `de0495f9eaa1e3d1b888c5c2ea24ce543cd117d4`
  after exact-head Copilot approval with no findings, zero open review
  comments and all required checks in
  [run 41439756652](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41439756652).
  The source worktree is pulled forward. This is measured preparation, not
  Installation Context admission; counts remain 13 strict / 55 pending.
- #9975 selects signature preservation for `_validation_scope`. Replace its
  parameter/return erasure with `ParamSpec` and a return type variable while
  preserving wrapper metadata, nested cache scope, thread isolation,
  exceptions and token restoration.
- Retain actual-source positive/negative MyPy controls and exact callable
  assertions for eventual whole-project enforcement. More precise signatures
  may expose additional genuine diagnostics; do not hide them or qualify an
  otherwise-failing pending library. Source/test debt, source-copy
  correspondence and all later project admissions remain required.
- The decorator now preserves its complete callable signature with `ParamSpec`
  and a return type variable. Two real-MyPy clients import the complete
  composed canonical implementation: valid calls pass; incorrect positional
  types, keyword types, positional use of a keyword-only parameter and return
  assignment all fail. This checks the client contract only, not pending
  library admission. An exact callable assertion remains in the library tests
  for eventual whole-project enforcement.
- Runtime metadata, signature, result and exception-restoration controls pass
  alongside nested-cache/thread-isolation checks. The dynamically loaded test
  module has an explicit `ModuleType` return contract. Whole-project probes
  still fail: Linux 219 basic / 440 strict, Win32 205 basic / 427 strict.
  The base fragment and exact callable assertion are clean on both targets.
  Counts remain 13 strict / 55 pending; no diagnostic is ignored or filtered
  for qualification.
- The existing owner synchronizes exactly 27 physical copies of the base
  fragment. MCP's source-only producer hashes retain revision 36 and unchanged
  semantics. Only one admitted unit-source digest changes; nodes, targets and
  packet budget remain unchanged. No provisioning, activation or lifecycle
  behavior changes, so fresh-box installation and external runtime rollout
  are not applicable to this typing-only preparation.

### 2026-11-02 - Decorator contract merged; production result contracts selected

- #9977 merged as `71f84b162317a3083ef9dccfde0663201c8eb5ad`
  after exact-head Copilot approval with no findings, zero open review
  comments and all required checks in
  [run 41442815799](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41442815799).
  The source worktree is pulled forward; #9975 is complete.
- #9980 selects the remaining bounded production result-shape and flow
  annotations. Preserve source resolution, PowerShell argv construction,
  validated result boundaries and optional-value semantics. Native adapters,
  source/test debt, whole-project admission and physical-copy qualification
  remain separate gates. The portfolio remains 13 strict / 55 pending.
- Seven canonical fragments now use honest local JSON/result shapes,
  object-to-integer narrowing, optional evidence and variable-length argument
  tuples. Distinct directory-string/path locals avoid false cross-branch
  inference. Plugin maintenance explicitly narrows the root already resolved
  by its required validation branch before constructing its marker path.
  Selection, validation ordering, outputs and cleanup algorithms are unchanged.
- Whole-project probes still fail: Linux 197 basic / 415 strict, Win32
  183 basic / 402 strict. The slice removes 22 basic and 25 strict diagnostics
  per target without admission, new exclusions, ignores or unchecked stubs.
  Remaining production diagnostics belong to native platform adapters, the
  dynamic worker and runtime cleanup boundaries; test debt is still open.
- Existing source/governance controls pass 127 tests with five explicit native
  or platform skips. All 114 PowerShell argv/descriptor adapters pass.
  Synchronization updates exactly 189 physical fragment copies; seven
  admitted-source hashes and MCP source-only producer hashes are refreshed.
  Unit nodes, coverage targets, 300-second budget and footprint revision 36
  stay unchanged.
- Initial hosted units pass, but the module-size guard rejects the added
  standalone declaration at 1,001 lines. Move that same annotation onto its
  first assignment, retaining the 1,000-line bound without widening the
  baseline, deleting unrelated content or changing the contract.

### 2026-11-02 - Result contracts merged; native API contracts selected

- #9981 merged as `9ec39a4c5634ddefea338cce1bb23153e9b8706b`
  after fresh exact-head approval with no findings, zero open review comments
  and all required checks in
  [run 41446180511](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41446180511).
  The source worktree is pulled forward after proving exact combined-patch
  equivalence with the merged squash. #9980 is complete.
- #9983 selects the native API typing boundary for existing platform-gated
  liveness, lock exclusion, account-home lookup and guest identity. Preserve
  native flags, runtime platform selection and validated outputs. Runtime
  cleanup protocols, worker/test typing and whole-project/copy admission
  remain open. Counts remain 13 strict / 55 pending.
- Four canonical fragments now declare narrow native callable/constant
  boundaries while retaining the existing runtime platform guards and
  attribute lookup failures. Opaque registry handles/results remain `object`
  until the existing value validators narrow them; account-home records use
  a read-only structural contract. No lock flags, native lifetime checks,
  guest identity or ownership validation changes.
- Full-project probes still fail: Linux 185 basic / 403 strict, Win32
  175 basic / 394 strict. Only runtime cleanup and the dynamic worker retain
  production diagnostics; the rest are test debt. This preparation introduces
  no ignores, unchecked first-party stubs or admission claim.
- All 74 selected native Windows lock/handoff/liveness and guest identity
  controls pass. POSIX registration-permission validation retains its explicit
  native-platform skip; native POSIX execution is not claimed on this worker.
  Four additional backend-contract controls cover UID/home flow, missing
  account refusal, registry query ordering and malformed identity refusal.
  The existing owner synchronizes exactly 108 physical copies and refreshes
  four unit-source digests plus MCP source-only producer hashes. Unit nodes,
  coverage targets, packet budget and footprint revision remain unchanged.

### 2026-11-02 - Native contracts merged; final production boundaries selected

- #9985 merged as `cda39e283302b5f2127f0d36d01a2d8a1aee2e08`
  after exact-head approval with no findings, zero open review comments and
  all required checks in
  [run 41448423992](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41448423992).
  The source worktree is pulled forward; #9983 is complete.
- #9986 selects the five remaining production diagnostics: the failed-candidate
  cleanup callable, optional reservation evidence and the dynamically loaded
  worker callback/request boundary. Preserve ownership and native lifetime
  custody; missing evidence must refuse before mutation. Keep actual
  callable-contract and focused runtime controls. Tests and independent
  copy qualification remain open; the full effort stays active.

- The cleanup callable now has its actual structural boolean/validator contract;
  lifecycle evidence has a fixed tuple shape and a missing target snapshot
  refuses before receipt mutation. The PowerShell worker names its request and
  caller-lifetime fields and casts only the known dynamically loaded callables.
  SDK helper lookup remains lazy; callback, environment and exit-code behavior
  is unchanged.
- Whole-project probes still fail honestly on tests: Linux 184 basic / 398
  strict and Windows 174 basic / 389 strict, with zero production diagnostics
  on either target. No suppression, filtered qualification, first-party stub,
  project activation or strict-compliance claim is introduced.
- Validation: 11 focused actual-MyPy and reservation controls, 53 complete
  checker/copy tests and 127 real slot/PowerShell/native-caller tests pass.
  The native test environment initially lacked the declared `psutil` test
  dependency; installing it into the disposable checker environment resolved
  collection and the complete selected suite then passed.
- All 26 enforced MyPy targets, changed-file Ruff, install contracts,
  documentation consistency, copy synchronization and vendoring checks pass.
  The owner updates exactly 40 physical copies. Only two unit-source digests
  and MCP producer hashes change; 3,665 unit nodes, coverage targets, the
  300-second packet budget and footprint revision 36 remain unchanged.
- These are below-altitude contract annotations and an early refusal preserving
  the existing release invariant. Existing operational documentation and wire
  semantics remain accurate. Fresh provisioning and remote development venues
  are not changed; local native consumer tests exercise the actual boundary.
  The campaign remains 13 strict projects and 55 pending, including canonical
  test and independent-copy qualification.

### 2026-11-02 - Production boundaries merged; shared test fixtures selected

- #9991 merged as `55af5adfeda80e411a3fe3e11674e3b11612e2a8`
  after exact-head approval with no findings, zero open review comments and
  all required checks in
  [run 41453768629](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41453768629).
  The source worktree is pulled forward; #9986 is complete.
- #9993 selects the shared receipt-layout and runner/helper typing boundary.
  Close fixture-type cascades before consumer batches, retain every assertion
  and the module-size ceiling, and rerun full declared-platform probes plus
  focused real consumers. No blanket ignore, replacement stub or filtered
  admission qualifies the library. All outstanding Plan/Validation items and
  the 13-strict / 55-pending portfolio remain active.

- Shared layouts now name their path and string fields; snapshot-stamp and
  slot-provision helpers retain precise result contracts across the JSON and
  dynamic SDK boundaries. The bounded host fixture names its iterator result,
  receipt-name parameters retain their literal alternatives, and the digest
  helper no longer reuses one local for text and bytes. Runtime values and
  assertions remain unchanged.
- Full declared-platform probes retain zero production diagnostics and reduce
  the remaining test errors to Linux 158 basic / 368 strict and Windows
  148 basic / 359 strict (26 basic and 30 strict errors removed per target).
  These remain failing exploratory probes, not library admission.
- Validation: 96 complete checker and focused receipt/slot-release consumer
  tests pass, including actual MyPy positive/negative fixture-client controls,
  real Python/PowerShell snapshot publication, completion and linked/stale
  receipt cases. All 26 already-enforced MyPy targets, changed-file Ruff,
  documentation consistency, install contracts, source-copy synchronization,
  vendoring and diff whitespace checks pass.
- Documentation impact: this is below-altitude test-contract preparation;
  existing developer commands and operational documentation remain accurate.
  The shared-library ownership map requires an `agent-machines` patch
  changefile even for this test-only slice. Fresh-box and remote-venue tests
  are skipped because production/provisioning behavior
  is unchanged; local real consumer tests cover the affected helpers.
  The governed unit packet and its budget remain unchanged.
- The final consumer audit also narrows the slot-release receipt-drift
  parameter to its two declared keys; its four runtime cases pass and the
  complete platform probes report no new literal-key diagnostics.

### 2026-11-02 - Shared fixtures merged; slot-release contracts selected

- #9995 merged as `c7f8fba2e041e1fe7a036e85a05616100d2ab229`
  after exact-head approval with no findings, zero open review comments and
  all required checks in
  [run 41459443498](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41459443498).
  The source worktree is pulled forward; #9993 is complete.
- #9996 selects the remaining slot-release helper and monkeypatch contracts.
  Preserve the existing receipt-only reservation lifecycle, refusal/race
  assertions and real runner boundaries. Run full platform probes and all
  slot-release cases. The campaign remains active; library admission and
  independent copies are still open.

- Slot-release layouts, arguments, runners, test parameters and monkeypatch
  callbacks now retain concrete contracts. Invalid-input mutation cases still
  accept their deliberate string/integer alternatives; dynamic SDK results
  are cast only at the forwarding boundary. No lifecycle assertion changes.
- Full probes report zero slot-release and production diagnostics on both
  declared targets. Remaining project test debt is Linux 157 basic / 352
  strict and Windows 147 basic / 343 strict: 16 strict diagnostics removed
  per target. The parent project remains pending.
- Validation: all 77 checker and slot-release tests pass, including actual
  MyPy positive/negative helper clients and real Python/PowerShell release,
  refusal, replay and race cases. Changed-file Ruff, all 26 enforced MyPy
  targets, documentation consistency, install contracts, source-copy sync,
  vendoring and whitespace checks pass.
- Documentation impact: below-altitude test-only typing; developer commands,
  runtime behavior and operating documentation remain accurate. Fresh-box
  and external-venue tests are not applicable. The ownership-map-required
  `agent-machines` patch changefile is included; the governed unit packet,
  source-copy bytes, CI budgets and 13-strict / 55-pending admission counts
  remain unchanged.

### 2026-11-02 - Slot-release contracts merged; independent test cohorts selected

- #9997 merged as `ad6c8bde24477d481eb46d18fa7d73e87210cfef`
  after exact-head approval with no findings, zero open review comments and
  all required checks in
  [run 41462080479](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41462080479).
  The source worktree is pulled forward; #9996 is complete.
- The lead selected #10001 for the remaining snapshot-provenance test
  parameters, native callbacks and result narrowing. #9998 and #9999 own
  disjoint lock-recovery and digest-cache hardening test cohorts in isolated
  worktrees. Each delegate first returns a bounded diagnosis/plan; shared
  helpers, checker controls and this canonical effort remain lead-owned.
  No worker may claim full library admission or close #9807.
- All unresolved Plan and Validation Plan items remain active. The last
  measured project debt is Linux 157 basic / 352 strict and Windows
  147 basic / 343 strict, with zero production and slot-release diagnostics.

### 2026-11-02 - Three test cohorts integrated; next independent cohorts selected

- #9998 and #9999 supplied isolated lock-recovery and digest-cache test
  contracts; the lead integrated them with #10001's snapshot-provenance
  fixture, native callback and result contracts. The bounded slice changes
  no production implementation, assertions, skips, timeouts or project level.
- Whole-project probes report zero diagnostics in all three changed modules
  and production on both targets. Remaining test debt is Linux 127 basic /
  250 strict and Windows 114 basic / 238 strict. All 26 already-enforced
  platform checks, changed-file Ruff, install contracts, documentation
  consistency, source-copy synchronization and vendoring checks pass.
- The delegates' complete modules pass independently (42 lock-recovery
  cases; 34 digest-cache cases with three existing POSIX-only skips).
  Combined validation of all three complete modules and checker regressions
  passes 393 tests, with 54 existing platform/opt-in skips, in 1,194.56 seconds.
  Windows-native Python/PowerShell producer, completion, policy and race
  contracts ran; POSIX-only and opt-in exhaustive adapters retain their
  existing skips. Native producer tests require a short temporary basename
  on Windows; a long invocation path exposed the native path limit, while
  the complete run passes using `--basetemp .test-venvs/s`. This broader
  manual check adds no tests or duration to the governed unit packet.
- #10003 and #10004 select installation-mode governance and runtime-profile
  policy test contracts in separate worktrees. Both diagnosis/plan milestones
  passed; isolated implementation and module validation are authorized.
  Publication and shared helpers remain lead-owned.
- Documentation impact: below-altitude test-contract preparation; developer
  commands and operational behavior remain accurate. Fresh-box and remote
  venue tests do not apply to unchanged production/provisioning code.
  The governed unit packet, CI budgets and 13-strict / 55-pending admission
  counts remain unchanged. The campaign and #9807 remain active.

### 2026-11-02 - Snapshot, lock and digest contracts merged

- #10005 merged as `0bc0717d04f6fed35e04154d8255488dba7c14d3`
  after exact-head approval with no findings, zero open review comments and
  all required checks in
  [run 41469685645](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41469685645).
  The source worktree is pulled forward; #9998, #9999 and #10001 are complete.
- Re-reading this Plan and Validation Plan leaves library admission, remaining
  test/helper families, independent copies and the portfolio ratchet open.
  The next slice is #10003/#10004's governance/profile contracts and the
  directly dependent fixture clients exposed by their precise helper types.
  Preserve those concrete contracts rather than widening them to conceal
  caller debt. #9807 remains open.

### 2026-11-02 - Governance/profile contracts and shared clients integrated

- #10003 and #10004 supplied concrete governance/profile fixtures, keyword
  arguments, native seams and checked module interfaces. The shared loader
  contract includes its existing JSON/deployment clients; a protocol limited
  to the original module would incorrectly hide valid APIs.
- Dependent diagnostic, deployment and legacy-snapshot fixtures preserve the
  concrete layout/environment/keyword types, and the JSON-reader storage
  double retains the real raising callback signature. One profile cast is
  removed because integration now proves its field is already a `Path`.
- Full project probes report zero diagnostics in all six changed modules
  and production on both targets. Remaining test debt is Linux 60 basic /
  139 strict and Windows 47 basic / 127 strict. The library remains pending.
  Combined validation of all changed modules and the directly dependent
  deployment adapters passes 516 tests, with 127 existing platform skips,
  in 403.04 seconds. Windows-native Python/PowerShell contracts ran; existing
  Bash/POSIX/WSL and invalid-Windows-path cases retain their skip boundaries.
- All 26 already-enforced platform checks, changed-file Ruff, install
  contracts, documentation consistency, source-copy sync, vendoring and
  whitespace checks pass. Documentation impact: below-altitude test-only
  contracts; developer commands and operational behavior remain accurate.
  Fresh-box and external provisioning checks are not applicable to unchanged
  production code. The governed unit packet and CI budgets remain unchanged.
- #10006 and #10007 select the next two native-bootstrap and source-selection
  families in independent worktrees. Their current authorization is bounded
  diagnosis/planning only. No project admission, production change or
  installed-runtime rollout is included.

### 2026-11-02 - Governance/profile contracts merged; native/source cohorts selected

- #10009 merged as `398a17386239d315dd5bc1fcb53df6e59a2627ed`
  after exact-head approval with no findings, zero open review comments and
  all required checks in
  [run 41473701489](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41473701489).
  The source worktree is pulled forward; #10003 and #10004 are complete.
- Re-reading this Plan and Validation Plan leaves library admission,
  remaining helper/test families, independent copies and portfolio
  qualification open. #10006 and #10007 passed their bounded diagnosis
  milestones; implementation and full module/platform validation are
  authorized only in their isolated, updated source worktrees.
- The lead retains remaining small fixture/adapter families. Last measured
  library debt is Linux 60 basic / 139 strict and Windows 47 basic /
  127 strict, with zero production diagnostics. The library remains pending
  and the full campaign stays active under #9807.
- #10010 selects the lead-owned `test_installation_context.py`,
  `test_vendoring.py`, `test_json_query_bytes.py`,
  `test_directory_evidence_cost.py` and `powershell_test_host.py` contracts.
  Keep unknown-payload discovery fixtures outside a real repository during
  runtime validation; an in-checkout temporary directory legitimately
  inherits the enclosing marketplace and is not an isolated unknown payload.

### 2026-11-02 - Native/source and lightweight helper contracts integrated

- #10006 and #10007 supply native host, lock-handoff, source-provenance and
  selection test contracts. #10010 supplies concrete source vectors,
  vendoring-tool members, heterogeneous JSON fixtures, optional executable
  narrowing and a captured non-null PowerShell response stream.
- Whole-project probes report zero diagnostics in all nine changed modules
  and production on both declared targets. Remaining test debt is Linux
  30 basic / 48 strict and Windows 33 basic / 51 strict, in eight untouched
  modules. The library remains pending; 13 strict / 55 pending project
  admission counts and governed CI budgets are unchanged.
- Combined validation of all changed test modules and checker regressions
  passes 187 tests, with 174 existing platform/tool skips, in 431.71 seconds.
  Windows-native PowerShell/Python paths ran. Bash/AWK-dependent cases retain
  their existing unavailable-tool skips; source-discovery fixtures ran outside
  a real marketplace. All original assertions remain; new loader, identity and
  executable assertions only narrow or verify existing fixture boundaries.
- All 26 already-enforced platform checks, changed-file Ruff, installation
  contracts, documentation consistency, source-copy sync, vendoring and
  whitespace checks pass. Documentation impact: below-altitude test-only
  preparation; existing developer commands and production guarantees remain
  accurate. Fresh-box provisioning and external-venue validation do not apply
  to unchanged runtime code. #9807 and the full campaign remain active.
- Review of #10012 caught a nullable `ref` in a descriptor fixture; the
  descriptor contract now includes `None` while normalized fields remain
  strings. Both full-project strict counts are unchanged. The fixture
  consistency test passes, and a real MyPy client control accepts `str | None`
  but rejects using that descriptor value as a definite string.

### 2026-11-02 - Native/source and helper contracts merged; final test families selected

- #10012 merged as `aa271cd0a6ca39857d608275d078ee8e807d7723`
  after exact-head approval, resolution of the nullable-descriptor finding
  and green required checks in
  [run 41480620942](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41480620942).
  #10006, #10007 and #10010 are complete.
- The normal sync safely aborted a squash-history conflict. All 13 owned
  paths were proved byte-identical upstream, and the aggregate stable patch
  ID matched the accepted merge, before dropping only duplicate local history
  and completing the managed sync. Unrelated upstream changes were preserved.
- Re-reading this Plan and Validation Plan leaves library admission,
  generated/vendored copies and portfolio qualification open. #10013 and
  #10014 passed diagnosis/planning and own the final independent POSIX and
  guest-environment families. #10015 selects the lead-owned remaining native
  adapters. The latest strict baseline is 48 Linux / 51 Windows diagnostics
  across those eight test modules; no production errors. #9807 remains open.

### 2026-11-02 - Canonical Installation Context strict admission prepared

- #10013 and #10014 supply typed POSIX fixtures/callbacks and guest-environment
  boundaries. #10015 supplies native adapter/class fixtures, typed timeout-note
  diagnostics, the real tooling import identity and the dependent optional
  PowerShell caller. The two profile-home test children now explicitly retain
  headless Windows launch flags. No production implementation changes.
- The complete composed library passes strict on Linux and Windows:
  51 physical source/test files represented by 32 checker targets each.
  #10018 changes only its admission level from pending to strict; includes,
  excludes, platform/interpreter targets and environment pins are unchanged.
  The current inventory is 7,446 physical files, 14 strict / 54 pending
  projects. Independent copies and the full campaign remain unqualified.
- Full affected modules and checker regressions pass 357 tests, with 112
  existing platform/tool skips and eight passing subtests, in 504.18 seconds.
  The lead ran the complete external-isolation suite, not just the delegates'
  filesystem-free subsets. An assertion/decorator audit preserves 448 original
  assertion contracts and every test decorator; timeout-note assertions access
  the same exception attribute through a typed alias.
- After admission, all 28 enforced platform checks and all 52 checker controls
  pass. The documented `python tools/typecheck.py lib-installation-context`
  command passes both targets in 11.56 seconds on a prepared local environment.
  This is a local prepared measurement, not a cold CI budget claim. Existing
  six-minute MyPy steps and governed unit selection/budgets are unchanged.
- Changed-file Ruff, source-copy sync, vendoring, documentation consistency,
  installation contracts and whitespace checks pass. Documentation impact:
  `docs/python-typechecking.md` now documents the admitted canonical scope,
  local command and independently pending copies. Fresh-box provisioning and
  external runtime venues do not apply to unchanged production behavior.
  Source CI must still validate this exact candidate before merge; #9807
  remains active.

### 2026-11-02 - Canonical Installation Context strict admission merged

- #10019 merged as `c9335651029f80e4f22520291e92929044965cbf`
  after exact-head approval with no findings, zero open review threads and
  green required CI in
  [run 41484654768](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41484654768).
  Its real pinned-environment preparation and admitted type-contract steps
  passed in source CI. The managed source sync completed.
- #10013, #10014, #10015 and #10018 are complete. The canonical library's
  source, worker and every test/helper are now strict; its independent copies
  and the larger campaign remain pending. The completed admission advances
  the shared-library Plan, not the full objective. #9807 remains open.
- Re-reading the remaining Plan and Validation Plan selects #10021's four-file
  Configuration Layers scope. Its initial complete-project strict probes
  report 116 Linux / 114 Windows diagnostics. Source and tests were read
  before implementation; preserve JSON validation, configuration precedence,
  source ownership, optional absence and the real cross-language controls.

### 2026-11-02 - Canonical Configuration Layers strict admission prepared

- #10021 adds source/layer/document/composition records, an explicit recursive
  JSON merge contract, qualified resolver callables and nullable reader results.
  Raw inputs still pass the original validation; arbitrary configuration values
  and serialized owner reports retain documented dynamic JSON boundaries.
  Tests use the canonical SDK for static analysis while preserving the existing
  runtime import and all native/cross-language assertions.
- All four canonical modules pass strict on Linux and Windows; the manifest
  admits them without changing includes, excludes, pins or declared targets.
  The expanded cohort passes all 30 platform checks: 15 strict / 53 pending
  projects across 7,446 physical files. Independent payload copies remain
  pending. The local prepared command
  `python tools/typecheck.py lib-configuration-layers` passes in 13.15 seconds;
  this is not a cold CI budget measurement.
- The contained parity and affected Index/model-routing consumer suites pass
  109 tests and three subtests. The 52 checker controls also pass. An earlier
  uncontained diagnostic invocation inherited the host's ready Worktrees owner
  and failed seven synthetic-repository cases; the standard isolated environment
  removes that accidental host dependency and all seven pass unchanged.
- Four standalone Python copies are synchronized. Ruff, copy correspondence,
  vendoring, documentation consistency, install contracts and whitespace checks
  pass. Documentation impact: `docs/python-typechecking.md` records the scope,
  dynamic boundaries, local command and independently pending copies.
  This is below runtime-architecture altitude; validation, source ownership,
  precedence, native subprocess options and optional absence are unchanged.
  Fresh provisioning and external-venue runtime tests do not apply to this
  type-contract-only slice. Exact-candidate source CI and review remain required
  before merge; #9807 remains active.
- The first source-CI unit job correctly rejected stale admission hashes for
  the two edited canonical source files. After reviewing their pure unit/import
  closure, only those two hashes were refreshed; the 3,665 nodes, 458 source
  entries, 209 coverage targets and 300-second budget were preserved exactly.
  The complete existing packet then passed locally in 234.78 seconds under
  owned process containment, with coverage recorded and cleanup/audit complete.
  Its 16 non-admitted collected cases remain deselected by the unchanged exact
  whitelist. Updated-head review and source CI remain required.

### 2026-11-02 - Canonical Configuration Layers strict admission merged

- #10023 merged as `366b590d225378dfeb0876b6d2810970bb181e63`
  after updated-head approval with no findings, zero open review threads and
  green required CI in
  [run 41491412895](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41491412895).
  #10021 is complete. Its canonical four-file scope is strictly enforced;
  independent copies and the remaining 53 projects are not admitted by this PR.
- The source sync safely aborted a squash-history conflict. All 13 owned paths
  matched the accepted merge byte-for-byte, the aggregate stable patch ID
  matched, and the merge was an upstream ancestor. Only proven duplicate local
  history was dropped after retaining a backup; the managed sync then passed.
- Re-reading this Plan and Validation Plan leaves the campaign active.
  #10025 owns the next five-file canonical Core Delegation slice: its initial
  strict probes report 26 Linux / 28 Windows diagnostics. One independent
  worker is diagnosing the six-file Fleet Contracts scope in isolation before
  implementation. #9807 remains open; no installed runtime was rolled out.

### 2026-11-02 - Canonical Core Delegation strict admission prepared

- #10025 types protocol-agnostic JSON dictionaries, socket addresses and the
  existing pointer-correct Windows function boundary. The native DLL is still
  called directly with the same configured signatures; the protocol does not
  add a runtime wrapper. Tests type their fixtures and narrow TCP addresses
  with additional assertions, preserving every existing transport/fall-through
  assertion and the Windows Unix-socket skip.
- All five canonical source/test files pass strict on both declared targets.
  The expanded cohort passes all 32 platform checks: 16 strict / 52 pending
  projects across the current 7,489-file physical inventory. The documented
  local command `python tools/typecheck.py lib-core-delegation` passes in
  10.29 seconds with the prepared checker; no cold CI timing is claimed.
- The contained real loopback/rendezvous suite passes nine tests, with its one
  existing Unix-socket-on-Windows skip, in 2.26 seconds. Owned process cleanup
  and the host-environment audit pass. Linux remains a static analysis target,
  not a claimed native test result. No installer/provisioning or independently
  available external service is changed, so fresh-box and live external-venue
  tests are not applicable to this type-contract-only slice.
- Both release consumer maps and source-import discovery identify no current
  plugin copy or canonical-reference consumer; the changefile addresses the
  canonical library. No edited Core Delegation input is enrolled in the
  promotion unit manifest, so its existing nodes/hashes/targets are unchanged.
  Ruff, shared vendoring, documentation consistency, install contracts and
  whitespace checks pass. Documentation impact:
  `docs/python-typechecking.md` records strict scope, JSON/native boundaries,
  the local command and the native test distinction. Review and exact-candidate
  source CI remain required; #9807 and the full effort stay open.

### 2026-11-02 - Core Delegation merged; Fleet Contracts implementation selected

- #10027 merged as `5748427341387d616c1be4803f6e0e5cdecc1154`
  after exact-head approval with no findings, zero open review threads and green
  required CI in
  [run 41495532516](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41495532516).
  #10025 is complete; all five canonical files are now strictly enforced.
  The managed source sync passed. Re-reading this Plan and Validation Plan
  leaves 52 pending projects and the larger completion gate open.
- #10026's independent Fleet Contracts diagnosis confirms six canonical files
  and 180 strict diagnostics on each target: 12 source and 168 test diagnostics.
  One bounded source/test implementation is selected. Its worker owns only
  those canonical modules and the library/SSH release changefile; the lead
  retains shared admission, source-hash, documentation and final integration
  checks. The existing Fleet unit closure and execution budget must be preserved.
- The lead continues the shared-library ratchet while that isolated
  implementation proceeds. No installed runtime rollout is part of either
  slice, and #9807 remains open.

### 2026-11-02 - Fleet Contracts strict integration

- Integrated the isolated source/test annotations and library/SSH changefile.
  All six canonical files pass strict MyPy on Linux and Windows; the expanded
  enforced gate passes for 17 strict projects, with 51 projects still pending.
  The worker reports 201 canonical tests, 201 promotion-adapter tests and 219
  SSH consumer tests passing. The lead independently verified strict admission;
  whole-packet validation and review remain required.
- Refreshed exactly four existing Fleet source hashes in the promotion
  manifest. A structural comparison proves all other packet data unchanged:
  no node, source, coverage-target or budget expansion. This is canonical
  library admission, not activation of the independently pending SSH runtime.
- The lead's contained whole-packet run passes all 3,665 selected nodes in
  241.047 seconds, with the existing 16 deselections and no failures or skips.
  Coverage, exact-source admission, effect audit and owned cleanup pass under
  the original 300-second budget. Module-size, changefile, install-contract,
  vendoring, documentation-consistency, Ruff `F,E9` and diff guards pass.
  Fresh-machine and live-venue lanes are not applicable to this annotation-only
  library admission: no installer, provisioning or transport behavior changes.
  Required remote CI and review remain pending before merge.

### 2026-11-02 - Fleet Contracts merged; Repository Policy selected

- #10033 merged as `f38815cff84771b5676a91738b8e3c3a2b45a96e`
  after exact-head approval, zero open review threads and green required CI in
  [run 41500645633](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41500645633).
  #10026 is complete. Native finalization independently verified the worker's
  full content on `dev`, removed its worktree and settled the parent claim.
- Re-reading this Plan and Validation Plan leaves 51 pending projects and the
  overall completion gate open. #10029 owns Repository Policy's next bounded
  library admission: type the canonical evaluator and tests, extract its
  embedded guest-reader program into a genuinely checked module, and preserve
  fresh `-I -S` receiver behavior and required license metadata.

### 2026-11-02 - Repository Policy strict qualification

- Extracted the embedded guest program into `_guest_reader.py`, checked as
  real source rather than an unchecked string. The builder reads it only when
  constructing a probe. All six canonical files pass strict MyPy on Linux and
  Windows with the existing declared YAML/stub environment; the expanded
  enforced gate passes for 18 strict projects, with 50 still pending.
- All 73 canonical tests pass in an isolated managed environment, including
  fresh `-I -S` receiver acceptance/refusal and explicit-context refusal.
  The first attempt's six metadata failures were resolved by preparing the
  real distribution in that environment, not by weakening the license guard.
  A normal wheel build includes the exact checked reader and license.
  Installed-wheel controls pass allowed, denied, duplicate-policy and
  explicit-context receiver cases without repository writes.
- Targeted consumer checks pass: CodeSpaces 27, Containers 6, Dispatch 24,
  Bridge 101 and Worktrees 55. The Worktrees transaction-order fixture now
  isolates the unrelated profile reconciler and explicitly proves its call
  remains after the registry write and inside the transaction. This resolves
  the observed nested-guard fixture failure without changing production locks.
- Both consumer maps identify the same five editable consumers and no physical
  copies. The changefile includes the library and all five consumers. No
  Repository Policy file belongs to the existing promotion-unit source closure,
  so that packet's manifest is unchanged; required CI still runs it.
  No live remote venue is needed for this read-only source extraction; fresh
  subprocess and installed-wheel composition are exercised locally.

### 2026-11-02 - Repository Policy merged; Fleet adapters selected

- #10035 merged as `f72ce614fe2004e1f811c70a7577c6b3ae3a1429`
  after exact-head approval, zero open review threads and green required CI in
  [run 41504133602](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41504133602).
  #10029 is complete. Re-reading this Plan and Validation Plan leaves 50
  pending projects and the larger completion gate open.
- #10036 owns two small canonical adapter scopes, four files apiece. Initial
  Linux strict probes report 53 Index and 57 Vault diagnostics, concentrated
  in test fixtures, optional response members and validated operation-specific
  parameter unions. Their first annotated complete-scope probes pass on both
  targets, but behavioral validation, admission and review remain required.

### 2026-11-02 - Fleet adapter strict qualification

- Both complete four-file adapter scopes pass strict MyPy on Linux and
  Windows. Backend JSON remains an explicit serialization boundary, and narrow
  operation-specific parameter casts preserve the validated request contract
  without adding or removing runtime checks. Positive request factories now
  use typed constructors; existing assertions remain, with explicit non-null
  response assertions added for narrowing.
- The contained canonical suites pass all 24 tests without skips, including
  the two real `agent_index.server.build_app()` HTTP round trips over an
  OS-assigned port. Both consumer maps report no shipped consumers or physical
  copies, consistent with these prototype libraries' documented status.
  Neither adapter belongs to the existing promotion-unit source closure, so
  its manifest and budget remain unchanged. No installer or first-provision
  behavior changes, and no credential store or live remote service is touched.
- The expanded enforced gate passes all 20 strict projects on both targets;
  48 projects remain pending. A production AST comparison, ignoring only
  no-op typing casts, confirms unchanged function bodies; every existing test
  assertion, name and decorator remains. Module-size, changefile,
  install-contract, vendoring, documentation-consistency, Ruff `F,E9` and diff
  guards pass. Required remote CI and review remain before merge.

### 2026-11-02 - Fleet adapters merged; Work Coalescing Singleton selected

- #10038 merged as `15b910907169da99887dac09aa35408e7bdcf40a`
  after exact-head approval, zero open review threads and green required CI in
  [run 41505959750](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41505959750).
  #10036 is complete; managed source sync passed. Re-reading this Plan and
  Validation Plan leaves 48 pending projects and the larger completion gate open.
- #10039 owns the next eight-file canonical Work Coalescing Singleton scope.
  Its initial Linux strict probe reports 137 diagnostics in five files.
  Diagnose real wire/callback, queue/completion and native transport boundaries
  before implementation; preserve all deadline, coalescing and cleanup behavior.
  No service deployment or runtime rollout is part of this slice.

### 2026-11-02 - Work Coalescing Singleton strict qualification

- The complete eight-file canonical source/test project passes strict MyPy
  for Linux and Windows, reducing the admitted inventory to 47 pending and
  increasing the strict cohort to 21 projects. The manifest selects the
  existing base checker environment without exceptions or exclusions.
- Protocol-agnostic payloads and compute results retain explicit dynamic JSON
  boundaries. Rendezvous uses a typed record; TCP server/handler ownership and
  socket overrides honor the stdlib contracts. Narrowing the in-flight value
  directly preserves the existing owner/joiner branch and completion behavior.
  All prior test names and assertions are preserved; typed socket doubles and
  scoped monkeypatches replace untyped callback/method-assignment seams.
- The contained canonical suite passes all 43 tests, including actual TCP
  round trips, shared execution and absolute completion deadlines. Relevant
  editable consumers pass Worktrees 180, Pull Requests 42 and Worktree Manager
  159 tests. The Pull Requests cleanup fixture initially hit Windows' executable
  path-length limit; an extended-length temporary root did not survive its
  path normalization. Re-running the identical tests with a shorter owned
  pytest base path passes, retaining the native fixture runtimes, process
  ownership and cleanup checks. No test or product code was changed to evade
  that failure. The deliberately injected early serve-thread failure retains
  its existing warning in the canonical suite.
- Both consumer maps identify the same three editable consumers and no
  physical copies. The promotion-unit source closure contains no library
  input, so its hashes, nodes and budget remain unchanged. The changefile
  names the library and all three consumers. The full admitted cohort,
  install-contract, vendoring, documentation and Ruff checks pass.
- Validation tiers: existing unit tests and actual native TCP/consumer
  integration were exercised. A fresh-machine clean-room scenario and a live
  external deployment are not applicable to this annotation-only admission:
  no installation, discovery, wire format, service activation or cutover
  behavior changes. Required remote CI and review remain before merge; the
  larger campaign's Plan and Validation Plan remain open.

### 2026-11-02 - Work Coalescing Singleton merged; independent leaves selected

- #10042 merged as `ed7f33a400d1b67581687b34c62891cc3960b5e6`
  after exact-head approval, zero open review threads and green required CI in
  [run 41510792927](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41510792927).
  #10039 is complete; managed source sync passed. Re-reading this Plan and
  Validation Plan leaves 47 pending projects and the full campaign gate open.
- The lead selected #10043's complete eight-file Update Telemetry scope.
  Separate source-only worktrees implement #10044's ten-file Installer Readiness
  and #10045's eleven-file Docker Resource Producer scopes. Their edit ownership
  is library-local; registry activation, consumer qualification, global docs,
  changefiles, publication and completion stay with the lead. They must preserve
  existing assertions and authority, schema, resource identity and cleanup
  contracts, with no installed-runtime or live-container mutation.

### 2026-11-02 - Shared dependency delivery identified during leaf implementation

- The isolated #10044 implementation closes Installer Readiness's annotation
  delta and passes 47 contained tests, but its normal standalone checker still
  reports four `installation_context` import-not-found errors on each target.
  The canonical public imports are correct. A diagnostic probe including the
  real loader and nineteen fragments renders the existing faithful source view
  and passes; that alternative is diagnostic evidence, not admission.
- #10048 owns the shared checker prerequisite: deliver that composed first-party
  dependency to consumers while retaining physical ownership, source verification,
  accurate target counts and negative type controls. The library remains pending;
  no stub, private-fragment import, wrapper or suppression substitutes for it.
- The isolated #10045 implementation passes its complete eleven-file strict
  probe on both targets and 107 contained tests on Windows, with 53 POSIX-only
  skips. Parent integration, consumer qualification and Linux runtime coverage
  remain before admission; the worker's local commit is not a merged PR.

### 2026-11-02 - Update Telemetry strict qualification

- The complete eight-source-file canonical project passes strict MyPy on Linux
  and Windows. Its normal local command and the full enforced cohort pass with
  22 strict and 46 pending projects; physical inventory remains 7,515 files.
  The contract tests remain owned by the independently pending Worktrees project.
- JSON event, status and error documents retain explicit dynamic boundaries.
  The human sink has a minimal write/flush protocol; active-context clocks and
  enabled-run event paths use narrow lifecycle invariants. Context tokens,
  recursive redaction, optional publication results and native platform branches
  are statically visible without changing persisted schemas, fail-open behavior,
  lock ownership, deadline handling or command execution.
- The complete Worktrees telemetry, wiring and launch-progress suites passed
  292 tests with four native-platform skips. After the final human-output edit,
  the affected human/shell/progress selection passed 82 tests with the same four
  skips and 210 intentional deselections. Final standalone/bootstrap and native
  capability controls passed 151 tests with two POSIX-only skips; Manager update
  and launch-output consumers passed 88 tests with sixteen POSIX-only skips.
  These retain actual native children, pipes, exit codes, credential redaction
  and fresh standalone imports. The Windows host does not claim POSIX runtime
  qualification: skipped controls cover POSIX signal exit, Linux pidfd custody,
  native Bash/preflight and POSIX installer rendering.
- The two editable library consumers are Worktrees and Worktree Manager.
  Payload generation additionally copies the human, redactor and capability
  sources to fourteen helper packages across thirteen plugins. All 42 generated
  files are raw-byte-identical to their canonical sources; only the 28 human and
  capability copies changed. Those copies remain independently pending.
  The fifteen-entry changefile includes the library, Manager and all thirteen
  lifecycle plugins. No first-party substitute or missing-import ignore is used.
- MCP's generated script change required its existing producer-digest refresh.
  The owning refresh tool changed only that scripts digest: source digest,
  footprint contract and revision 36 are unchanged. Install-contract, vendoring,
  generator correspondence, documentation, Ruff and diff checks pass. No changed
  file belongs to the source-bound promotion-unit packet, so its closure, hashes,
  selection and budget remain unchanged.
- Validation tiers: existing units, native consumer integration and fresh
  dependency-free bootstrap imports were exercised. No machine provisioning,
  installed update, live external service or container mutation is part of this
  static admission. Required remote CI/review remain before merge; #10044 and
  #10045 remain integration-pending and #10048 remains the checker prerequisite.

### 2026-11-02 - Update Telemetry merged; faithful dependency delivery selected

- #10053 merged as `b924eae22610b5f0f7f638c802f4fddd30d96667`
  after exact-head approval, zero open review threads and green required CI in
  [run 41515936607](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41515936607).
  #10043 is complete; managed source sync passed. Re-reading this Plan and
  Validation Plan leaves 46 pending projects and the full campaign gate open.
- The lead's separate source-only worktree implements #10048. Import-only
  views retain the consumer's original target list and physical counts while
  using the same verified canonical composition. The preserved #10044 worker
  implementation now passes a real ten-file strict probe on both targets
  through the changed checker; it remains pending until normal checker
  publication and library admission complete.
- Focused actual-MyPy and actor-free checker controls pass all 60 tests.
  Positive/negative imports preserve argument and return typing; source drift
  refuses qualification and releases the view, target files and environment
  lease. The source-bound packet admits only the two changed input hashes and
  splits one existing custody node into its original and import-only variants;
  its complete qualification remains before publication.

### 2026-11-02 - Faithful checker dependency delivery merged

- #10068 merged as `209d6a04c51b0d91941c59bbf64aeed2d3a4f4f8`
  after exact-head approval, zero open review threads and green required CI in
  [run 41520633399](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41520633399).
  #10048 is complete; managed source sync passed. Re-reading this Plan and
  Validation Plan leaves 46 projects pending and the overall gate open.
- The final post-sync 22-project enforced cohort passed on both targets, with
  7,544 physical files classified. The source-bound packet passed all 3,666
  admitted nodes, retained sixteen existing deselections and completed in
  241.703 seconds within the unchanged 300-second budget. The import context
  now reaches actual consumers without reassigning dependency targets.
- The lead selected #10045 for the next independent admission and integrated
  its preserved worker commit. Both consumer maps report no shipped consumers
  or physical copies, and the library is outside the source-bound packet.
  Complete current open-PR file inspection, including pagination for four large
  Dev PRs, found no overlapping library edits. The existing required fixture
  lane will provide Linux/minimum-Python coverage; no live Docker mutation is
  authorized. #10044 follows now that its checker prerequisite has landed.

### 2026-11-02 - Docker Resource Producer strict qualification

- The integrated eleven-file canonical source/test project passes the normal
  strict checker command on Linux and Windows analysis targets. The full
  proposed cohort passes all 23 strict projects; 45 remain pending, with 7,545
  physical Python files classified. The manifest adds no exclusion or exception.
- Identity fields are narrowed before construction while retaining the
  constructor's existing refusal contract. A structural binary-read protocol
  covers HTTP framing, and typed synthetic resource/context/invocation records
  remain distinct from malformed response vectors. Native transport admission,
  creator evidence, generation checks, custody and cleanup authority remain.
- Parent verification preserves the exact ASTs of all 71 test names and 153
  assertions. The integrated contained Windows suite passes 107 tests with 53
  existing POSIX-only skips in 36.97 seconds. Required PR guards already select
  this fake-Docker suite on minimum Python 3.10; their Linux result remains a
  merge gate, not a claim inferred from the Windows run.
- Both consumer maps report no shipped consumers or copies. No source-bound
  packet input changes, so packet hashes, nodes and budget remain unchanged.
  The single library changefile, README command and shared typing guide cover
  the admission. Ruff, install-contract, vendoring, documentation and diff checks
  pass; commit-based publication guards and remote review/CI remain.
- Validation tiers: existing canonical fixtures exercise actual synthetic
  subprocesses and transport behavior under containment. The required Linux lane
  covers native POSIX/minimum-runtime fixtures. Live Docker lifecycle acceptance,
  host provisioning, service rollout and container mutation are not part of this
  static admission, and none was performed. The worker worktree remains held
  until the library admission is merged.

### 2026-11-02 - Docker review correction and platform evidence reconciliation

- #10076's first review found that the changefile's bare library identifier
  would be ignored by promotion. It now uses `libs/docker-resource-producer`;
  the real accumulator computes `0.1.0-dev1` to `0.1.1-dev1`. No version file
  was manually changed.
- Actual CI job inspection contradicts the preceding assumed Linux fixture
  lane: the current guards and required source-bound unit packet passed, but
  neither runs this independent library suite. The old `TESTING.md` paragraph
  was stale and is corrected. Green unit CI is not substituted for Linux
  fixture evidence, and no new required integration gate is introduced.
- Existing local Linux test capacity was unavailable until the operator
  explicitly approved Docker Desktop startup and isolated tooling. The
  hash-locked Dev Containers CLI passed the existing npm seasoning audit.
  The convenience wrapper is POSIX-only and its runner does not register this
  library; platform verification instead uses its checked-in hardened profile,
  a source snapshot, explicit contained execution, network disconnection and
  exact resource cleanup. Execution results remain pending.
- Next independent canonical source-only workers are reserved by #10083
  (Process Telemetry) and #10084 (Single Instance Lease). The lead retains
  admission metadata, consumer qualification, publication and completion.

### 2026-11-02 - Docker Linux/minimum-runtime fixtures verified

- The complete synthetic Docker suite passes all 160 cases, with zero skips,
  on actual Linux/Python 3.10.21 in 12.94 seconds, within the unchanged
  300-second contained-run budget. This exercises all 53 POSIX cases skipped
  on Windows. The source snapshot is `401bd13af5f991802b51bbf42e00aebee1ab8050`;
  subsequent documentation changes do not alter its executable inputs.
- The test used the checked-in isolation profile's immutable image and hardening
  arguments: non-root execution, a read-only root, dropped capabilities, no
  privilege escalation, bounded temporary storage and no host or Docker-socket
  bind. Dependency preparation preceded verified network disconnection; actual
  tests used the existing contained runner and daemon-test lifecycle.
- The convenience CLI's setup timed out, so explicit Docker phases applied the
  same profile. A container-side dependency CDN handshake failed; the host's
  canonical seasoned UV adapter instead prepared the same hash-locked Linux
  dependency target for transfer. Neither TLS verification nor dependency
  seasoning was relaxed. Source, requirement and transferred-artifact hashes
  and exact container/volume cleanup receipts were retained.
- This supplies the missing independent Linux evidence, not a new CI lane,
  live product Docker acceptance or installed runtime rollout. Required CI
  and a subsequent passing review still gate #10076's corrected head.

### 2026-11-02 - Docker admission merged; Readiness selected

- #10076 merged into Dev as `1176251cc9b0a05789f200bbe9f673644d9b7895`
  after exact-head approval, zero open review threads and green required CI in
  [run 41534795545](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41534795545).
  #10045 is complete. There are now 23 merged strict projects and 45 pending;
  this Plan and Validation Plan remain active, not complete.
- The lead preserved a backup before resolving a pull-forward conflict caused
  by replaying the already squash-merged incremental commits. All thirteen
  published paths exactly matched the provider-confirmed merge, with no
  unpublished survivor; an empty-range rebase advanced only proved-merged
  history, then managed source synchronization passed. No stash, discarded
  change, runtime rollout or CI bypass was used.
- Readiness's preserved artifact has been reviewed and independently retains
  all 31 test names, 94 assertion ASTs and six parametrizations. Admission,
  the Agent Machines consumer regression, publication and merge remain next.
- Single Instance Lease's complete fourteen-file worker artifact passes strict
  checking on both targets and its Windows contained suite (52 pass, twenty
  platform skips). Parent review is complete; Linux qualification, the physical
  copy, ten pointer consumers and four source-packet input updates remain
  integration work. Process Telemetry remains pending until its declared OTLP
  extra has an explicit seasoned, hash-locked checker environment; no import
  suppression or first-party substitute is accepted.

### 2026-11-02 - Installer Readiness strict qualification

- The lead integrated the reviewed Readiness artifact and activated all ten
  canonical source/test files in strict mode. The proposed 24-project enforced
  cohort passes both targets, with no exclusions or missing-import exceptions;
  44 projects remain pending. The documented per-project command also passes
  after managed source synchronization. The refreshed inventory classifies
  7,574 physical Python files.
- The standalone suite passes all 47 tests, with zero skips, on actual
  Linux/Python 3.10.21 in 2.61 seconds using the checked-in hardened profile,
  a source snapshot, transferred hash-locked dependencies, verified network
  disconnection and the existing 300-second contained runner. The successful
  test container and volume were removed with exact cleanup receipts.
- The four focused Agent Machines consumer files pass 114 tests with seventeen
  existing native-POSIX skips on Windows in 283.18 seconds. The managed runner
  reports 310.05 seconds including environment preparation and cleanup.
  Canonical admission does not qualify Agent Machines's entire pending scope.
- Runtime edits are local variable renames; fixture shapes and annotations
  preserve all 31 test names, 94 assertion ASTs and six parametrizations.
  There are no physical copies or source-bound packet inputs to refresh.
  Ruff, install-contract and documentation checks pass. Current open-PR
  inspection, including pagination for four large PRs, finds no overlap.
- Validation tiers: existing library fixtures and focused consumer integration
  exercise receipt classification, dependency planning and repair boundaries.
  Fresh-machine installation and live managed-runtime rollout are not changed
  and were not performed. Linux execution uses isolated local test capacity;
  no external development venue or new CI lane is required for this library.
  Review, required CI and merge remain open gates for #10044.

### 2026-11-02 - Readiness consumer release propagation

- #10096's guard and review identified the missing literal `agent-machines`
  patch entry. Its editable library reference is a release consumer even though
  it is not a physical copy or a newly admitted checker scope. The changefile
  now names both `libs/installer-readiness` and `agent-machines`, preserving
  runtime rebuild propagation without changing the consumer's admission level.

### 2026-11-02 - Readiness admission merged; Lease integration selected

- #10096 merged as `3b4e9ca876464ec91aa9ed4d4e613ddfb6901715`
  after exact-head approval, the resolved consumer-bump finding and green
  required CI in
  [run 41539947371](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41539947371).
  There are now 24 merged strict projects and 44 pending. This Plan and
  Validation Plan remain active; the full strict-compliance gate is not closed.
- A managed pull-forward replay conflict auto-aborted. A preserved backup and
  exact equality of all eight published blobs proved there was no unpublished
  survivor before advancing only merged history; managed synchronization then
  passed. Unrelated incoming Dev changes were preserved.
- The lead selected #10084. Independent verification retains all 59 test names,
  113 assertion ASTs and seven parametrizations in its six edited test files.
  Actual Linux/Python 3.10.21 contained execution passes 60 tests with twelve
  native-Windows skips in 1.98 seconds; the successful container and volume were
  removed. Copy/consumer qualification, admission and the unchanged source-bound
  unit packet remain before publication.
- #10083's isolated child now declares a separate universal, seasoned,
  SHA256-locked OTLP checker environment matching its existing optional extra
  and base checker pins. Preparation passed; the same source-only worker has
  resumed. This does not activate that pending scope or change runtime deps.

### 2026-11-02 - Single Instance Lease strict qualification

- The lead integrated the reviewed fourteen-file artifact and activated the
  canonical source/test project in strict mode on both targets. All 25 proposed
  strict projects pass; 43 remain pending, with 7,574 physical Python paths
  classified. There are no new exclusions, import suppressions or runtime deps.
- Runtime annotations cover handles, liveness probes, ownership tables and
  collaborators. Independent verification retains all 59 test names, 113
  assertion ASTs and seven parametrizations in the six edited test files.
  Actual Linux/Python 3.10.21 execution passes 60 tests with twelve native-Windows
  skips in 1.98 seconds; the isolated successful container and volume were removed.
- All seven shipped physical-copy source files exactly matched canonical's
  pre-change content before the existing materializer refreshed only the
  Customizing Copilot copy. The same-version override was limited to that
  verified canonical update, without manually changing any version.
  Canonical/copy correspondence passes. A contained Windows run verifies that
  the package actually imports from the shipped copy and passes the canonical
  suite's 52 tests with twenty existing native-POSIX skips in 13.55 seconds.
- The changefile names the canonical library and all eleven release consumers,
  including the physical copy and ten editable references. Their complete
  checker scopes remain independently pending. The typing guide documents the
  canonical admission and concrete boundary shapes.
- Only four changed Lease source-input hashes are refreshed in the source-bound
  packet; all 3,675 nodes, 210 coverage targets and the 300-second budget remain
  unchanged. Complete qualification passes 3,675 tests with sixteen existing
  deselections in 255.093 seconds, including the single coverage collection.
  Ruff, install-contract, vendored-copy, documentation and diff checks pass.
- Validation tiers retain real kernel-lock/platform fixtures and actor-free
  consumer decisions. Native Linux and Windows contracts are exercised through
  the isolated library/copy suites; no installer, fresh-machine provisioning,
  live service takeover or installed runtime rollout changes. No external
  development venue or additional CI lane is required. Review, required CI
  and merge remain open gates for #10084.

### 2026-11-02 - Lease admission merged; Process Telemetry selected

- #10102 merged as `3350afd6c7ca5d75f87812c3eadfc349f2e617ed`
  after exact-head approval, zero review findings and green required CI in
  [run 41542391423](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41542391423).
  Managed source synchronization passed. There are now 25 merged strict
  projects and 43 pending; this Plan and Validation Plan remain active.
- The lead selected #10083 and reviewed all sixteen edited source/test files.
  Independent AST comparison preserves its 31 test names, 47 assertions and
  thirteen parametrizations. The artifact passes its seventeen-file strict
  scope on both targets in the declared OTLP checker environment.
- Its actual Linux/Python 3.10.21 contained run passes all 73 tests with zero
  skips in 0.20 seconds, explicitly selecting both ordinary and native files.
  An initial launcher-only collection failure was analyzed in the retained
  container: this library has no path-injecting conftest, so its import roots
  and separately named native test file must be supplied explicitly. No source
  change or fresh container was substituted for that diagnosis.
- Optional-SDK native verification, consumer/copy/packet qualification,
  admission and normal publication remain before completing #10083.

### 2026-11-02 - Process Telemetry qualification and scoped declaration policy

- The seventeen-file canonical source/test scope passes strict checking on both
  targets in its declared optional-SDK environment. All 26 proposed strict
  projects pass; 42 remain pending, with 7,576 physical files classified.
  There are no physical copies or source-bound packet inputs/direct nodes to
  refresh. Release consumers are Bridge, Dispatch and Worktree Manager; their
  complete checker scopes remain independently pending.
- The retained Linux/Python 3.10.21 container passed all 73 ordinary/native tests,
  then both real-SDK and absent-SDK boundary checks. Actual SDK constructors,
  exporters, protobuf round trips and three-signal acknowledgements execute;
  only HTTP posting is synthetic, and no new thread is admitted. After the
  original invocation failure was diagnosed and all corrected checks passed,
  that exact container and volume were removed with verified receipts.
- Focused contained Windows consumer runs pass Bridge's 97 tests, Dispatch's
  29 tests and Manager's 70 tests. The initial Bridge and Manager skips reflect
  their deliberately absent optional SDK, not platform qualification.
  A seven-package SDK-only projection of the existing hash lock was installed
  under test-environment admission through the canonical seasoned installer,
  preserving each consumer's pytest 9.1.1. The formerly skipped SDK paths then
  pass all 56 Bridge cases and all 71 Manager observation cases, with zero skips.
- The declaration audit initially refused the SDK's required
  `opentelemetry-semantic-conventions==0.66b1` pin solely because it is a
  prerelease. Both public artifacts were uploaded on October 6 and are
  non-yanked and seasoned; the stable SDK's version-specific metadata requires
  that exact pin. The operator approved the bounded declaration exception
  recorded above rather than weakening the seven-day or installation policies.
- The audit now recognizes only that exact file/package/version and
  unconditional same-file SDK pin. Other files, versions, ranges and parent
  changes remain rejected. Unit controls cover revocation and young, yanked,
  missing-timestamp and youngest-artifact refusals. The complete existing
  dependency-audit suite passes 154 tests under containment; the live Python
  audit passes all 460 declarations. Installer hash enforcement and resolver
  seasoning are unchanged.
- Documentation records the checker environment, concrete typed boundaries,
  scope limits and exact declaration exception. Ruff, install-contract,
  documentation consistency and diff checks pass. Review, required CI and merge
  remain before completing #10083.
- The next independent source-only slices are claimed by #10107 (Machine
  Transport) and #10108 (Plugin Activation). Both retain their pending level and
  use the existing seasoned YAML checker lock after their initial missing-stub
  prerequisite was diagnosed. The lead retains shared metadata and publication.

### 2026-11-02 - Process packet admission reconciled to incoming Dev

- #10115 received exact-head approval with no findings. Its source-unit job
  correctly refused the changed `tools/dependency_age.py` bytes: incoming Dev
  had added that tool to the admitted packet after the initial impact snapshot.
  The completed job log names the stale source input directly; this is neither
  a flaky test nor permission to bypass the gate.
- Only that one source digest is refreshed. The current 3,773 nodes, 513 source
  inputs, 211 coverage targets, nineteen existing deselections and 300-second
  budget remain unchanged. Complete local qualification passes all 3,773 tests
  in 282.171 seconds, including the single coverage collection.
- The PR's impact statement is corrected to describe the new tracked policy
  input. The updated head requires fresh review and required CI; earlier approval
  and the initial snapshot's lack of a policy input do not qualify it.
- The next Machine Transport artifact is independently verified to preserve
  all 214 test names, 324 assertions and 134 parametrizations. All 763 tests pass
  with zero skips on actual Linux/Python 3.10.21 in 2.41 seconds, matching its
  contained Windows result. Plugin Activation independently preserves its 105
  test names, 333 assertions and 43 parametrizations; integration remains pending.

### 2026-11-02 - Process admission merged; Machine Transport selected

- #10115 merged as `20938a4d5ed5d58f089c26b4a2d4c4101f4c4b82`
  after exact-head approval, no open review findings and green required CI in
  [run 41549310660](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41549310660)
  and the subsequent same-head
  [run 41549418312](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41549418312).
  There are now 26 merged strict projects and 42 pending. This Plan and
  Validation Plan remain active; no installed runtime rollout is claimed.
- Managed pull-forward auto-aborted a replay conflict. A retained backup,
  equality of all 24 ordinary published blobs, exact inclusion of the policy
  paragraph and equality of the sole owned packet digest proved that all own
  work was merged. Incoming shared-document and packet changes were preserved;
  managed synchronization then passed. The incoming packet has 3,779 nodes,
  514 source inputs and the same 300-second budget.
- Machine Transport is the next admission: all nineteen canonical files pass
  strict probes on both targets, and all 763 fixtures pass on Windows and actual
  Linux/Python 3.10.21 without skips. Its existing YAML checker lock is explicit;
  five release consumers and three changed packet inputs require qualification.
  There are no physical copies.
- Plugin Activation's reviewed artifact passes 207 Windows tests and 203 actual
  Linux/Python 3.10.21 tests with three existing native-Windows skips, in 4.42
  seconds on Linux. Its conditional surrogate-filename vector is Windows-only.
  The sole physical copy, nine release consumers and changed source/test packet
  inputs remain lead-owned integration work.

### 2026-11-02 - Machine Transport admission and consumer qualification

- The lead integrated the reviewed artifact and its explicit existing YAML
  checker environment, then activated all nineteen canonical files strictly on
  both targets. All 27 proposed strict projects pass; 41 remain pending, with
  7,614 physical Python files classified. The original 214 test names, 324
  assertion ASTs and 134 parametrizations remain exact.
- Only the three changed canonical runtime input hashes are refreshed.
  Complete qualification passes all 3,779 current source-bound unit nodes with
  nineteen existing deselections in 250.203 seconds, including the single
  coverage collection. Nodes, coverage targets and the 300-second budget are
  unchanged. There are no physical copies; the changefile names the library
  and all five release consumers.
- Focused contained consumer regressions pass Bridge's 83 tests, Machines's
  169 tests, SSH's 15 tests, Worktrees's 404 tests and Manager's 107 tests.
  Machines retains one actual-container-root skip on Windows; an explicit
  isolated Linux check remains before publication.
- Manager's headless grouping fixture now composes the actual model mixin and
  initializes its non-live reader state instead of omitting the failure-marker
  contract. Worktrees's reciprocal-claim test retains the precise execution-space
  refusal through the real structured diagnostic callback while asserting the
  sanitized public notice. Both repairs preserve runtime behavior and the
  meaningful routing/ownership assertions rather than skipping failed cases.
  Neither fixture file is a source-bound packet input.
- Ruff, install-contract, documentation and diff checks pass. Native library
  fixtures already pass all 763 cases without skips on both Windows and actual
  Linux/Python 3.10.21. No remote host, headed interface, live resource takeover,
  installed rollout or new CI lane is introduced. Review, required CI and merge
  remain open gates for #10107.

### 2026-11-02 - Actual-container Machine consumer case verified

- The exact Machines consumer case skipped on Windows now passes in the
  isolated Linux/Python 3.10.21 environment. Combined execution passes all
  764 library/consumer cases with zero skips in 1.60 seconds.
- Its first collection attempt omitted a declared first-party source dependency.
  The failed container and volume were retained, and the launcher derived the
  twelve-project local import closure from the actual source declarations.
  Corrected execution ran in that same container; only after diagnosis and
  successful verification were the exact container and volume removed.
  No dependency declaration, runtime code or assertion was changed for this check.

### 2026-11-02 - Machine admission merged; Plugin Activation selected

- #10128 merged as `5fd7172247e789cb14c18867310b3ac90d755560`
  after exact-head approval, no open review findings and green required CI in
  [run 41556572650](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41556572650).
  Managed source synchronization passed. There are now 27 merged strict
  projects and 41 pending; this Plan and Validation Plan remain active.
- The final qualification retains the complete canonical Windows/Linux fixtures,
  all five focused consumer contracts, the actual-container consumer case and
  the unchanged-budget 3,779-node unit packet. Consumer fixture drift is repaired
  without runtime behavior changes or weaker routing/ownership assertions.
- The lead selected #10108. Its reviewed twenty-one-file artifact and Windows/
  Linux fixtures are already qualified, and all fourteen shipped source files
  exactly match the artifact's pre-change canonical baseline. Synchronizing that
  copy, admitting the scope, propagating its nine consumers and qualifying the
  changed source/test packet inputs remain before normal reviewed publication.

### 2026-11-02 - Plugin Activation admission and consumer qualification

- All twenty-one canonical source/test files pass strict checking on both
  declared targets. The proposed cohort has 28 strict projects and 40 pending;
  no independently pending consumer or packaged copy is declared strict.
  All fourteen packaged source files correspond to the canonical source after
  the scoped synchronization. The changefile names the library and all nine
  release consumers, without hand-editing versions.
- The complete library fixtures preserve the original 105 test names,
  333 assertion ASTs and 43 parametrizations. Windows passes 207 tests without
  skips; actual Linux/Python 3.10.21 passes 203 with three native-Windows skips.
  The surrogate-filename vector is conditionally collected only on Windows.
- Focused contained consumer checks pass Bridge's 154 tests, Codespaces's 89,
  Containers's 42, Dispatch's 121, Machines's 116, Worktrees's 117,
  Customizing Copilot's 58 and Manager's 110. Platform-specific skips remain
  explicit. Logger's four activation-install closure controls pass; its
  retained source-reselection controls receive separate native qualification.
- Bridge's ordering fixtures use a real isolated database and isolate the
  separate hosting-admission contract; its real renderer checks the managed
  instruction-copy namespace. Dispatch's unavailable-layer fixture checks
  the discovery mode that retains both healthy and diagnostically unavailable
  paths. These repairs retain the real consumer behavior and assertions.
- The native Logger/Vault fixtures now populate the immutable bootstrap through
  the assembled payload's retained-helper owner before marking slots complete.
  POSIX nested venv creation resolves the real interpreter, preventing Python
  3.10's outer-venv launcher alias from losing its standard library. All thirteen
  native cases pass on Linux in 215.62 seconds, without skips. The failed
  container was retained for in-place diagnosis and corrected execution; its
  exact container and volume were removed only after successful validation.
  All thirteen Windows cases also pass across six bounded selectors, with
  120-second native-case limits and the existing 300-second group limits.
  The broader native installer file is not claimed as a passing suite:
  its aggregate run exceeded the group limit, while the selected activation
  closure and complete source-reselection contracts are independently green.
- The incoming packet-runner change is preserved. Complete current qualification
  passes all 3,783 source-bound unit nodes with nineteen existing deselections
  in 287.062 seconds, including the single coverage collection. This admission
  refreshes only nine changed library source/test input hashes; coverage targets
  and the 300-second budget are unchanged. The consumer fixture files are not
  packet inputs.
- Ruff, install-contract, documentation, module-size, changefile and copy
  correspondence checks pass. Review, required CI and merge remain before
  completion of #10108. The independent canonical Plugin Resolve source/test
  worker is reserved by #10141; its integration and complete qualification
  remain separately owned. This effort and #9807 stay active.

### 2026-11-02 - Portfolio coverage made explicit

- The operator requested a tracker covering plugins, services and tools and
  recording each section as it becomes locked down. The linked tracker has
  one row per declared checker project, grouped into shared libraries,
  plugins, services/managers and tools/support.
- The merged snapshot has 27 of 68 strict projects, but only 206 of 7,628
  physical Python files in those scopes. All paths are classified; the
  39.7% project count is not a 39.7% code-volume claim. Plugins and services
  remain independently pending despite their tested shared-library consumers.
- Plugin Activation and Plugin Resolve are explicitly prepared work, not
  merged locks. The tracker keeps qualification/issue records and a
  post-merge update procedure. The effort index now links this canonical
  scoreboard instead of carrying a stale second count.

### 2026-11-02 - Plugin Activation merged; both coverage metrics refreshed

- #10150 merged as `fc2cf24697f3055f1187c52b5793aac53e05cba0`
  after approval on exact head
  `b0e7dc593994f372828ef322a9c676acfef289b4`, no open review threads and green
  required CI in
  [run 41567659422](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41567659422)
  and the
  [lockdown run 41567659504](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41567659504).
  #10108 is closed; #9807 remains open. Managed source synchronization passed.
- The current merged snapshot has **28 / 68 strict projects (41.2%)** and
  **227 / 7,635 physical Python files (3.0%)** in those scopes. Forty projects
  remain pending, with no basic scopes. The tracker records Plugin Activation
  as locked and accounts for newly added, independently pending Container
  files rather than freezing the old denominator.
- Both metrics now have one canonical portfolio tracker with plugin,
  service/manager, tool/support and shared-library rows. Project percentages
  remain distinct from file-weighted coverage, and classification remains
  distinct from enforcement. This Plan and Validation Plan remain active.
- #10141 is the next slice. Its twenty-three-file source artifact passes
  strict probes on both targets and 400 contained Windows tests with four
  explicit POSIX skips. Lead review, actual Linux execution, packaged-copy
  correspondence, consumers and full admission remain before publication.
  The completed Plugin Activation implementation child is natively finalized;
  equality of all twelve owned artifact blobs and realization of its YAML
  prerequisite established that no unpublished work was discarded.

### 2026-11-02 - Plugin Resolve source and consumer qualification prepared

- The lead reviewed all twenty changed artifact files and integrated the
  twenty-three-file canonical project. An independent cast-erased AST
  comparison preserved all 169 test functions, 515 assertions and 74
  parametrization decorators. Both declared strict targets pass, as does the
  complete twenty-nine-project enforced cohort. JSON documents retain explicit
  dynamic values; native lock protocols describe the actual imported APIs.
- Fresh, hardened, network-disconnected Linux execution on Python 3.10.21
  passes 393 canonical tests with eleven platform skips in 26.09 seconds.
  The original contained Windows artifact run passed 400 tests with four POSIX
  skips. The fourteen-file physical copy matched the pre-change canonical
  baseline before owner-scoped materialization; its 33 tests and the 62
  copy-tooling controls pass. Copies remain independently pending scopes.
- Consumer qualification covers Bridge preparation/hosting, Codespaces
  preparation/staging/hosting, Containers preparation/hosting, SSH plugin
  launch, Dispatch registration, Worktrees plugin/related/acquisition contracts,
  Worktree Manager provider/update contracts, Machines preinstallation and
  Logger's library-closure transaction. Codespaces passes all 77 selected
  tests through its normal runner; Containers passes 37, SSH 120 with three
  platform skips, Dispatch 74, Worktrees 250 with five skips, Manager 64,
  and Logger eight Windows cases with eight POSIX skips.
- Four consumer fixture files required contract corrections, not production
  bypasses: SSH supplies an actual synthetic receiving-policy result and
  verifies the policy wrapper before inspecting its nested productive command;
  Codespaces explicitly identifies its synthetic host and checks both current
  preparation flags; both Machines spies retain every argument/order/refusal
  check while accounting for the existing `-B` interpreter flag. Machines
  passes five Windows cases with six skips and seven Linux cases with four
  skips. Both Linux containers and their volumes were removed after success.
- The native identity-memo test requires stable ancestor directory versions.
  Its original assertions pass unchanged in the isolated repository test-run
  area: all 207 Activation consumer tests pass. Source-hosting tests remain
  outside that denied repository ancestry. Codespaces' normal runner preserves
  installed license metadata ahead of source-tree egg-info without substituting
  installed implementations for canonical source.
- The changefile names the canonical library and all ten release consumers.
  Only four existing packet input hashes are refreshed; no unit nodes,
  coverage targets or deadline are added. Ruff, install-contract, docs and
  changefile checks pass. Current-head packet execution, required CI, review
  and merge remain before this row can be locked.
- Native isolated Linux execution supplies first-touch validation. No live
  SSH/CodeSpace service rollout or authenticated external receiver is selected
  or available for this source-only admission; no such execution is claimed.
  Embedded Python transport/source-map qualification remains an open
  cross-cutting gate under #9807. Both coverage metrics retain the merged
  twenty-eight-project snapshot until #10141's admission merges.

### 2026-11-02 - Plugin Resolve current-source packet qualified

- Managed synchronization preserved every integrated canonical artifact file.
  On source `3c8cfd5fa8dc5212653912617a0624cf64c72418`, the complete current
  packet passes all 3,811 source-bound unit nodes in 257.922 seconds, with
  nineteen existing deselections and one coverage collection. All 518 input
  hashes match; the 213 coverage targets and 300-second deadline are unchanged.
- The refreshed twenty-nine-project enforced cohort passes both declared
  targets over the current 7,644-file classified inventory. Incoming pending
  files are accounted for rather than omitted. Review, required CI and merge
  remain separate completion gates, and the coverage tracker retains the
  last merged snapshot rather than crediting this local admission.

### 2026-11-02 - Plugin Resolve merged; both metrics refreshed

- #10165 merged as `c0efcffd3e1e66f73ca6b8a82473d0b3071d98ed`
  after Copilot approval on exact head
  `fa3af94c81e96763c62b8e44dcd286cf35f9ee11`, no open review threads and
  green required
  [CI run 41575317292](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41575317292)
  and
  [lockdown run 41575316972](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41575316972).
  #10141 is closed; #9807 remains open. Managed campaign synchronization and
  completed-child finalization passed; no unpublished artifact was discarded.
- The current merged tracker has **29 / 68 strict projects (42.6%)** and
  **250 / 7,644 physical Python files (3.3%)** in those scopes. Thirty-nine
  projects remain pending, with no basic scopes. Newly added, independently
  pending Vault and Fleet Manager files are reflected in their rows and area
  totals. Complete consumer admission and embedded/source-map qualification
  remain open rather than being credited by this library lock.
- #10166 owns the next independent canonical Venue Copilot source/test slice.
  Its source-only implementation is assigned in a managed child; the lead
  retains all integration and publication gates. Payload Invocation remains
  pending while contributor PR #10002 owns changes to its generator, tests and
  launch-policy contract; this campaign does not supersede that work.
- This Plan and Validation Plan remain active. A completed library PR is not
  completion of the all-Python objective or permission to finalize the
  campaign workspace.

### 2026-11-02 - Venue Copilot source and consumer qualification prepared

- #10166's complete canonical source/test artifact is integrated. Both declared
  MyPy targets check all 12 physical Python files strictly with zero diagnostics;
  the original Linux and Windows diagnostic counts were 189 each. All 30
  provisionally activated projects pass both targets against the 7,644-file
  inventory. The merged coverage tracker remains at 29 projects and 250 files
  until this admission clears review, required CI and merge.
- Explicit JSON/plan `Any` boundaries, callback and adapter protocols, prepared
  delivery overloads and identity casts retain runtime behavior. Parent review
  and independent AST checks preserve 73 production functions, 158 test/helper
  functions, all 252 assertions and 11 parametrizations. No cleanup, ownership,
  reservation, delivery-unknown or retry guard is removed.
- The native Windows contained suite passes 119 tests, with three existing
  POSIX/fcntl skips. Fresh native Python 3.10.21 Linux qualification in the
  hardened, read-only, network-disconnected container passes all 122 tests with
  no skips. Its exact owned container and temporary volume are removed and
  cleanup is verified. This is library/runtime qualification, not installed
  service rollout or live remote venue operation.
- The normal isolated consumer runner passes Codespaces' detached/venue/plugin
  preparation cohort (208 tests, nine platform skips), Containers' matching
  cohort (116 tests, no skips) and SSH's detached/plugin cohort (120 tests,
  three platform skips). Consumer projects remain independently pending.
  There are no physical Venue Copilot vendored copies in the current source
  inventory. The changefile names the library and all three actual release
  consumers; no version is hand-edited.
- Ruff `F,E9`, install-contract and documentation consistency gates pass.
  The original 3,811-node packet retains every one of its 518 input hashes,
  213 coverage targets and 300-second deadline. Exact-source execution passes
  all 3,811 nodes in 233.047 seconds, with no observed effects and one coverage
  recording. Hosted review, required CI and merge remain publication gates.
- This annotation-only slice changes neither provisioning nor independently
  deployed CLI/transport semantics. Clean-room installation and live external
  venue tests are not applicable; native contained library and consumer tests
  exercise the unchanged real consumer contracts without provisioning a service.
  The full Plan and Validation Plan remain active.
- Publication synchronization retains every one of the ten implementation
  artifact blobs. Incoming Dev changes update six unrelated packet input hashes
  without changing nodes, coverage targets or the 300-second deadline. The
  current 3,811-node packet passes again in 257.079 seconds; independent packet
  verification confirms source/manifest/coverage bytes and zero observed effects.
  All 30 activated projects pass both targets against the updated 7,645-file
  inventory. All three consumer cohorts pass again with the same counts after
  dependency synchronization. The additional file is independently pending;
  the tracker's previous merged snapshot is not retroactively changed.

### 2026-11-02 - Venue Copilot merged; both metrics refreshed

- #10170 merged as `d7b1490dce01f3163a3146a057a13f149206a0eb`
  after Copilot approval on exact head
  `f42bd167d9dfa05b4eb51157d2d8d1b1625a2133`, zero open review threads and
  green required
  [CI run 41580528367](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41580528367)
  and
  [lockdown run 41580528248](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41580528248).
  #10166 is closed; #9807 remains open. Managed campaign synchronization and
  completed-child synchronization/finalization passed; no useful artifact was
  discarded and no runtime rollout occurred.
- The current merged inventory/tracker records **30 / 68 strict projects
  (44.1%)** and **262 / 7,665 physical Python files (3.4%)**. Thirty-eight
  projects remain pending, with no basic scopes. New independently pending
  Procutil, Bridge, Worktrees, Efforts and Manager files are included in their
  rows and area totals. Passing consumer tests does not admit their projects.
- #10171 owns the next complete AI Attribution Python helper/test slice:
  five physical files and 141 initial Linux strict diagnostics. No matching
  open typing issue or PR exists. Disclosure/signature precedence, shell
  invocation and every existing policy assertion must remain unchanged;
  fragment/embedded-Python qualification remains an independent campaign gate.
- Versioned Runtime's 510-diagnostic, 22-file baseline is retained for later
  component-sized preparation PRs, not a single rewrite. Payload Invocation
  remains pending behind contributor PR #10002 rather than being superseded.
  This Plan and Validation Plan remain active; completing one library is not
  completion of the all-Python campaign.

### 2026-11-02 - AI Attribution helper and native qualification prepared

- #10171's complete five-file Python helper/test artifact is integrated. The
  normal local checker passes all five files strictly on both declared targets,
  and all 31 provisionally activated projects pass both targets against the
  7,665-file inventory. The initial Linux strict delta was 141 diagnostics.
  The merged-only tracker remains at 30 projects and 262 files until review,
  required CI and merge admit this scope.
- Validated profiles, rules, input probes and resolved signatures have structured
  types. The heterogeneous slot-selection intermediate remains an explicit
  dynamic boundary; identity casts and equivalent literal-key bindings preserve
  behavior. Parent review and independent AST comparison retain all 169 function
  names, 380 assertions and 30 parametrizations. Disclosure/signature precedence,
  malformed-input refusal, output bytes, native flags and policy remain unchanged.
- Native Windows contained validation passes 187 tests with 13 skips in its
  default run, then five explicit-tier tests with two skips. Combined coverage is
  192 passing cases and eight platform/tool skips, not a single 192-test run.
  Tier selection enables the original native CLI, cache and profile-budget
  controls without admitting effects into the release packet. Both runs retain
  the 300-second contained wall limit.
- Fresh native Python 3.10.21 Linux validation in the hardened, read-only,
  network-disconnected container enables the original explicit-tier controls:
  193 tests pass, with six PowerShell-only skips. The Python 3.9.25 helper smoke
  imports both helpers, validates/resolves/decorates a profile and verifies
  invalid-session refusal without creating a state directory. This preserves
  the documented minimum helper runtime. Exact owned container and volume
  cleanup is verified.
- Ruff `F,E9`, install-contract, documentation consistency and changefile
  coverage pass. The changefile names the payload-only plugin; no version or
  dependency declaration is changed. Missing worker-side `pytest-timeout`
  preparation used the canonical seasoned adapter rather than weakening its
  contained test deadline.
- The current original packet retains 3,811 nodes, 520 input hashes, 213 coverage
  targets and its 300-second deadline. This slice changes no packet-bound
  input or eligibility; exact-source execution passes all 3,811 nodes in
  280.063 seconds with no observed effects and one coverage recording.
  Independent packet verification confirms outcomes and source/manifest/coverage
  byte bindings. Scoped module-size conformity also passes.
  Embedded shell Python remains an independently open qualification gate.
  Native helper/CLI/hook tests are the simple integration lane; clean-room
  installation and live external venue operation are not applicable to this
  annotation-only change, which installs or deploys no service.

### 2026-11-02 - AI Attribution merged; both metrics refreshed

- #10176 merged as `02fedf0688003fcab6a5bc4ae24bd85ada936880`
  after Copilot approval on exact head
  `45b1e116eb0d6208ee3e92f73fbf94efa81df956`, zero open review threads and
  green required
  [CI run 41585084858](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41585084858)
  and
  [lockdown run 41585085294](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41585085294).
  #10171 is closed; #9807 remains open. Managed campaign synchronization and
  completed-child synchronization/finalization passed; no useful artifact was
  discarded and no runtime rollout occurred.
- The current merged tracker records **31 / 68 strict projects (45.6%)** and
  **267 / 7,666 physical Python files (3.5%)**. Thirty-seven projects remain
  pending with none basic. AI Attribution is the first plugin's complete
  physical Python scope admitted; embedded/generated-source qualification
  remains independently open. The newly added, pending Worktrees file is
  reflected in its row and area total rather than omitted.
- Select the next independent, bounded Python scope through public ownership
  checks and the existing typed-boundary qualification flow. Versioned Runtime
  needs component-sized preparation rather than one rewrite; Payload Invocation
  remains behind contributor PR #10002. This Plan and Validation Plan remain
  active, and completing one plugin does not complete the all-Python campaign.

### 2026-11-02 - Context Handoff Python helpers selected

- #10179 reserves the complete six-file Python project. Open-PR file inspection
  found no overlapping Context Handoff source or checker-manifest change.
  The strict Linux baseline has 37 diagnostics across five files.
- This is below-altitude helper/fixture typing, not a change to continuity,
  native selection, handoff storage, process custody or launch behavior.
  Existing Node sources, test nodes, assertions and runtime contracts stay
  intact; dynamic JSON and imported-module boundaries remain honestly typed.
- Admission is prepared in an independent managed implementation worktree.
  Both declared checker targets, native Python and Node contracts, a
  disconnected Linux helper run and the original source-unit packet must pass
  before reviewed publication. The tracker remains at the last merged
  **31 / 68 projects** and **267 / 7,666 physical files** until this admission
  actually merges.

### 2026-11-02 - Independent project fleet authorized

- The operator requested: "Worth fanning out into a sub-agent fleet to drive
  changes across projects; submit PRs in parallel using worktrees per sub-agent".
  Independent implementation worktrees now own Dispatch service (#10184),
  Harness Knowledge (#10185) and devcontainer helpers (#10183), alongside
  Context Handoff (#10179).
- Each worker owns one complete declared project and its reviewed Dev PR.
  The coordinator owns this canonical effort and both coverage metrics.
  Implementation and public reviews may run concurrently; local heavy packet
  execution and shared-clone allocation/synchronization are staggered to
  preserve existing deadlines and avoid actual Git-ref/codename contention.
  This is resource scheduling, not a public merge queue or a new review gate.
- Diagnostic count is a decomposition signal, not a qualification exemption
  or an arbitrary blocker. Devcontainer's 165/166 and Harness Knowledge's
  235/241 Linux/Windows diagnostics are being addressed through their diagnosed
  coherent source/fixture components. No incomplete scope is credited as locked.
- Context Handoff's native Python suite passes 48 tests with one fixture
  path-length skip. Its original Node suite passes 285 tests with 11 native/
  tool skips. An inherited Git line-ending conversion policy initially rejected
  fixture staging and left the checkout dirty; explicit fixture-local
  `core.autocrlf=false` and checked setup exits correct that portability issue.
  Sync behavior and all original refusal/exit/JSON assertions remain unchanged.

### 2026-11-03 - Context Handoff exact source qualified

- Prepared source `25d3ee1fd8c3dc7e27ff5a22a741287d33280033` admits the
  complete six-file Python project. The normal front door passes all 32 locally
  admitted projects on both declared analysis targets. Independent comparison
  preserves all 74 Python functions, 165 assertions, 43 test definitions and
  decorator ASTs; no Python control is removed or weakened.
- Native Windows Python passes 48 of the original 49 cases, with the oversized
  catalog fixture skipped for Windows path limits. Disconnected Python 3.10.21
  Linux passes 47 cases with two PowerShell-only skips. Together these exercise
  every original case, not a single-platform 49-pass run. Native Windows Node
  passes 285 of 296 cases; its 11 POSIX mode/signal/symlink/FIFO skips are not
  claimed as qualification of the unchanged Node production portfolio.
- The Linux container was retained after its prepared dependency archive lacked
  `pytest-timeout`. The existing prepared, portable 2.5.0 plugin satisfies the
  runner's `>=2.3,<3` requirement. Transfer through the same container's exec
  boundary and explicit plugin loading preserved the 30-second test and
  300-second contained deadlines. In-place diagnosis, rerun and exact owned
  container/volume cleanup were verified; no network reconnection, new
  container retry or dependency-policy relaxation was used.
- The original packet retains 3,811 admitted nodes, 520 bound inputs, 213
  coverage targets and its 300-second ceiling. Exact-source execution passes
  every admitted unit in 256.344 seconds, with no observed effects and one
  coverage recording. Independent packet verification confirms outcome,
  source, manifest and coverage bindings; this change enrolls no new tests or
  effects into that packet.
- Ruff `F,E9`, install-contract, documentation consistency, generated plugin-only
  patch changefile, whitespace and scoped source-size checks pass. The two
  changed production modules remain within their existing 1,000-line cap.
  Clean-room provisioning and live external service/venue operation are not
  applicable to this annotation-only payload change. The fresh isolated Linux
  helper run proves the supported Python 3.10 path without installation or
  rollout. Embedded/generated Python remains an independently open gate.

### 2026-11-03 - Context Handoff and Dispatch service merged

- #10192 merged as `8c65cf0a7f33eef721c536157a73cce3053f4c67` on the
  unchanged reviewed head `df239e359fde667985f6ff1ef4fce2a11a603b63`.
  Copilot's owner-PR Comment verdict identified only the required Documentation
  impact statement. The description now links the affected documents; the
  exact thread is resolved, with no remaining blocking findings. Required
  [CI run 41592786412](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41592786412),
  the metadata-refresh
  [CI run 41593796949](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41593796949)
  and their lockdown checks are green. The ordinary role-scoped managed merge
  completed without ignoring CI or a change request. #10179 is closed.
- #10189 merged as `a821288054999248812454320a578228ff14b19d` after exact-head
  Copilot approval, green required
  [CI run 41590811517](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41590811517)
  and zero review threads. Its complete six-file source is unchanged; 16 native
  cases and the original 3,811-unit packet passed. #10184 is closed and its
  implementation child is finalized with the parent claim at rest.
- Canonical-source synchronization encountered an already-carried journal
  replay. Complete coverage-file equality and complete journal-prefix equality
  against the pinned upstream target proved that the sole local commit was
  redundant before its replay was skipped. No useful work was discarded.
- Both metrics are refreshed: **33 / 68 strict projects (48.5%)** and
  **279 / 7,670 physical Python files (3.6%)**. Thirty-five projects remain
  pending; none are basic. Two new Dispatch-plugin files and two repository-tool
  files are counted in their pending owners rather than omitted or credited.
- Devcontainer and Harness Knowledge continue in their independent worktrees.
  Devcontainer qualification exposed the existing Python 3.11 wrapper minimum;
  its declared checker target and native qualification must match that contract,
  not introduce unrequested Python 3.10 runtime behavior. Harness Knowledge
  retains its Python 3.10 copied-payload and native qualification gates.
  #9807, this Plan and this Validation Plan remain active; no runtime rollout
  or whole-campaign completion is claimed.

### 2026-11-03 - Devcontainer helpers merged; both metrics refreshed

- #10193 merged as `40a441ae8553f3a84bd70f4bbb5abe7832fa6ed5` after
  Copilot approval on exact head
  `0d622564a9a3e8b0bda8704afa6f0ca93ba40e02`, green required CI and zero
  review threads. #10183 is closed; #9807 remains open.
- The complete three-file project preserves its 117 original functions,
  291 decision nodes, 42 tests, 156 assertion calls, decorators and native
  scripts. The existing wrapper minimum is Python 3.11: the unsupported
  Python 3.10 run exposed `hashlib.file_digest` availability rather than a
  requested compatibility change. Only this project's declared checker version
  is aligned to 3.11. Both analysis targets and all 42 Linux native contracts
  pass; Windows retains its unchanged directory-fsync skip.
- Publication incorporated two already-reviewed upstream source/node additions.
  The old 3,811-node proof was not reused against those changed inputs:
  fresh exact-head CI passes all 3,813 nodes in 76.181 seconds, with independent
  verification of all 520 inputs, 213 coverage targets and zero effects.
  The 300-second budget remains unchanged. Failed native test resources were
  retained for diagnosis, then exactly cleaned up; no runtime deployment occurred.
- Both metrics now record **34 / 68 strict projects (50.0%)** and
  **282 / 7,670 physical Python files (3.7%)**. Thirty-four projects remain
  pending and none are basic. All rows remain tied to actual merged policy.
  Harness Knowledge qualification and the next independent service scope
  continue; this Plan and Validation Plan remain active.

### 2026-11-03 - Harness Knowledge merged; independent service fleet continues

- #10201 merged as `dc9501dd8350aa08cdba8b9b785a906787bcb8d0` after
  Copilot approval on exact head
  `750962b829d0013b7b5bf1913bd555427071c9ec`, green required CI run
  `41599033543` and zero review threads. #10185 is closed; #9807 remains open.
- All nine canonical files pass both declared strict analysis targets.
  Preservation retains 175 functions, 96 tests, 238 assertions and 24
  decorators. All 151 native Linux tests and every copied standard-library
  helper's capability/help contracts pass on Python 3.10.22 without touching
  actual bindings or operator state. Owned Docker resources and the PR watch
  are released; no installed rollout is claimed.
- After a backup and managed pull-forward, the previously published journal
  replay is redundant upstream; the useful Devcontainer journal remains.
  No useful local work is discarded. The inventory now records **35 / 68
  strict projects (51.5%)** and **291 / 7,672 physical Python files (3.8%)**.
  Thirty-three projects remain pending; none are basic. Two new Dispatch-plugin
  files are classified under their pending owner, not omitted or credited.
- Independent service slices #10204, #10206 and #10211 own separate worktrees.
  Their preparation remains pending coverage until reviewed actual merge.
  SSH-service scope does not overlap the open SSH-plugin PRs; Chat-service
  overlap with #8726 is preserved for its existing owner. Heavy validation
  is staggered while independent implementation proceeds.
- This Plan and Validation Plan remain active. The final all-project,
  embedded/copy, negative-control and source/candidate parity gates are still
  unresolved; a merged helper slice is not whole-campaign completion.

### 2026-11-03 - Credential Relay strict admission; both metrics reconciled

- #10310 merged into `dev` as `5ee8e294cbf57795fa54bc32fb45f36bb215ea57`
  after Copilot approval on exact head
  `0f8cd9a9b0d5e1749b439555c8fdd319fd1ef3a1`, zero review threads and green
  required [CI run 41735669002](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41735669002).
  The 22-file canonical library/test scope is strict on both targets; consumer
  project scopes remain independently pending. #10222 is closed; #9807 remains
  open.
- Production behavior, all 295 original assertions and test deadlines are
  preserved. Windows Python 3.10.22 passes all 196 native cases. The initial
  canonical/four-copy Linux qualification passed 980 cases; the three affected
  cases across those five variants passed again after the test-only compatibility
  correction. Python 3.10's separate `asyncio.TimeoutError` is accepted alongside
  `OSError`, preserving the same bounded failed-connection expectation that
  already passed through the Python 3.11+ built-in exception alias.
- The pre-publication composed candidate passed 3,857 units in 200.748 seconds
  with zero effects. Its composed-tree fingerprint is separate from the raw
  source Git tree; materialized provenance, caller-derived admission and the
  complete packet were independently verified against the actual source.
  Changed rebased inputs received a fresh
  [CI run 41734664779](https://github.com/ThomasMichon/copilot-extensions/actions/runs/41734664779):
  3,958 units, all 553 bound inputs and 234 coverage targets, zero effects,
  88.613 seconds under the unchanged 300-second budget. The final required run
  reused that exact-input proof, not the historical 3,857-unit packet.
- Unit, developer-consumer and clean-room minimum-runtime checks are exercised.
  Live external-venue validation was not run because no remote lane was
  currently admitted. No installed rollout or whole-campaign completion is
  claimed. Owned failed setup fixtures were diagnosed before exact cleanup;
  all four unit containers and their volume are independently confirmed absent.
- After a backup and managed pull-forward, useful prior Devcontainer and Harness
  Knowledge journals remain intact. Every tracker row is reconciled against the
  authoritative inventory: **36 / 68 strict projects (52.9%)** and
  **315 / 7,780 physical Python files (4.0%)**. Thirty-two projects remain
  pending; none are basic. Credential Relay accounts for 22 newly locked files;
  two added files under the already strict Bootstrap Killswitch scope are counted
  separately. Other additions stay with their pending owners.
- PR, Index, SSH and retained effort-helper qualification continue independently.
  #9807, this Plan and this Validation Plan remain Active until every remaining
  project and cross-cutting gate is resolved.

### 2026-11-03 - Complete Index service qualified for reviewed publication

- #10206 prepares strict admission of all 27 physical `service-index` source,
  test and helper files on both declared analysis targets. Its Python 3.10
  contract is unchanged. Honest fixture records, callable/process protocols
  and typed dynamic boundaries replace loose generics without excluding files
  or weakening the original 411 function/decorator contracts and 516 assertions.
  Twelve additional non-null/type guards retain the original value assertions.
- Actual minimum-runtime qualification exposed coupled core compatibility
  defects, not a reason to raise the service minimum. Task status retains native
  `enum.StrEnum` on Python 3.11+ and a private string/Enum base on 3.10; all eight
  explicit values, formatting, lookup, JSON and SQLite wire semantics remain.
  Five core UTC imports use the identical `timezone.utc` singleton. All 124
  original functions in the six affected core modules remain unchanged.
  These repairs do not strictly admit the independently pending Index plugin.
- The malformed RECORD fixture keeps its original NUL filename and assertions.
  An explicit CSV escape character avoids Python 3.10's NUL escape sentinel;
  the release verifier rejects forbidden NUL bytes before version-dependent
  CSV parsing. The complete 171-case release family passes on Python 3.10
  and 3.12. The focused status/UTC parity packet passes 20 cases on each.
- A fresh non-editable Linux Python 3.10.22 runtime is built from six actual
  composed first-party wheels and a genuine 44-distribution closed profile.
  Retained wheel custody, metadata/RECORD/ABI/closure inspection and installed
  health preserve the normal seven-day, non-yanked and SHA-256 admission.
  All 563 current native cases complete: **555 pass and eight original skips**.
  This includes all 506 service cases and 57 core enum/polling cases.
- The original deployment timeout was diagnosed rather than waived. The
  four-CPU quota exposed sixteen CPUs to Arrow; aligning the private test
  container's affinity to four reduces import threads from 18 to six and peak
  test PIDs from 84 to 49. The focused corrected case passes in 26.15 seconds;
  the complete native collection passes in 100.03 seconds. CPU, memory, PID,
  network, read-only/non-root and original 30/300-second limits are unchanged.
  Startup/CLI composition bodies remain runtime-equivalent to the baseline.
- The eight skips retain their original scope: six Windows-only cases, the
  separate POSIX abrupt-worker control and the opt-in real-UV staging fixture.
  Existing closed SDK artifacts are not that fixture's approved descriptor,
  source-commit, lock and UV-config manifest; its unset fixture receives no
  first-touch credit. Actual copied/distribution/CLI cases remain separately
  exercised. No live backend, operator configuration or installed rollout is used.
- Pre-publication source `60a6e1ed00c3efd54d066a69a893a24a712f23ae`
  and its actual composed candidate pass **3,958 units in 275.343 seconds**:
  553 Dev inputs, 1,889 candidate inputs, 718 coverage targets, all 234 required
  positive origins and zero effects. The composed Git tree is distinct from
  raw source HEAD. Independently derived manifests and canonical verification
  bind the real source/candidate roots. Journal integration changes publication
  identity, so required CI must produce current-head proof before merge;
  historical packet success is not claimed for a new head.
- Ruff `F,E9`, scoped size, install-contract, documentation, checker-lock and
  both required patch changefiles pass. Failed private setup/test instances
  were diagnosed before exact container/volume cleanup. Native evidence may
  be reused only for identical qualified production, test and SDK inputs.
  The merged tracker remains **36/68 projects and 315/7,780 physical files**,
  with 32 pending and zero basic; this prepared service is not yet a merged
  admission. #9807, this Plan and this Validation Plan remain Active.

### 2026-11-03 - Index service merged; both portfolio metrics reconciled

- #10327 merged as `dcb8603d01a6b008b1f2d83a3ee0bd9824bbdead` after
  Copilot approval on exact head
  `d2d44708f0d4a6a8453b23153699508db8448a3d`, green required CI run
  `41753974777` and zero review threads. #10206 is closed; #9807 remains open.
  The actual merged tree matches the fresh provider qualification tree
  `8d1a1ecc04095fba31a34ec351216b404782d0f2`.
- Fresh exact-input source CI passes all **3,958 units in 90.907 seconds**,
  with all 553 inputs, 234 positive coverage targets and zero effects.
  This is new provider evidence, not the pre-publication 275.343-second
  composed packet relabeled for a new head. The unchanged owned service/core
  blobs retain their independently verified native and composed-source
  qualification. Newly refreshed upstream runtime-archive members match their
  canonical regular sources and pass targeted actual Python 3.10 probes.
- All 27 service files remain strict on both targets. Linux Python 3.10.22
  passes 555 native service/core cases with eight original skips; the
  Windows 3.10/3.12 parity and copy controls retain their recorded scope.
  All 411 original service function/decorator contracts, 516 assertions and
  124 affected core functions remain preserved. The separate Index plugin
  is not admitted by this service change. Exact failed/successful test
  resources are absent; there is no installed-runtime rollout.
- The complete Plan and Validation Plan were reread, and all 68 tracker rows
  reconciled to the authoritative inventory: **37 / 68 strict projects
  (54.4%)** and **342 / 7,806 physical Python files (4.4%)**. Thirty-one
  projects remain pending and none are basic. The new admission contributes
  27 locked files. The 26 additional files inventoried in pending Dispatch,
  Index, Worktrees and Worktree Manager scopes receive no strict credit.
- All three earlier merged-admission journal sections and the complete prior
  coverage document are preserved in the reviewed change. PR service, SSH
  service and effort-helper qualification continue independently; this effort,
  its Plan and its Validation Plan remain Active until every remaining project
  and cross-cutting gate is resolved.

### 2026-11-03 - PR service merged; dual-version qualification selected

- #10332 merged as `628738bf021f2bcbadd8e1e06bd940a398843997` after
  actual Copilot approval on exact head
  `18b000b114a1c6cda37817f27a6a67e16b1076a7`, green required CI run
  `41776492365` and zero open review threads. Both required commit trailers
  are verified on the actual merge. #10204 is closed; #9807 remains open.
- Fresh provider source evidence passes **3,964 units in 95.456 seconds**,
  all 571 admitted inputs, 236 positive coverage targets and zero effects.
  This is not either older 3,940-node source evidence or the pre-publication
  157.120-second composed packet relabeled for a new head. The actual merged
  input blobs and modes match all 571 admitted sources; installed native
  service-module hashes also match the merge. Intervening Dispatch/documentation
  changes alter the whole tree outside those qualified inputs.
- Actual Linux Python 3.10.22 passes all **49 native cases without skips**
  in 22.573 seconds against eight fresh first-party wheels and a genuine
  35-distribution noneditable closure. Both nine-file strict targets pass.
  All 81 original functions, 28 original test definitions and 114 original
  assertion expressions remain; original fault deadlines are unchanged.
  Exact test resources and canonical candidate scratch are retired. The
  separately copied public interpreter bundle has its own verified custody,
  so the producer archive no longer gates child finalization.
- Current upstream admission data contained one stale expected fingerprint
  for `worktree-manager/scripts/versioned_runtime_source.py`. Its actual
  committed bytes match the canonical family, whose synchronization check
  passes. The reviewed one-field data correction changes no source membership,
  positive node, coverage target, controller, eligibility policy or 300-second
  budget. Canonical manifest/source verification and fresh CI pass.
- The complete Plan and Validation Plan were reread. Every tracker row matches
  the refreshed inventory: **38 / 68 strict projects (55.9%)** and
  **351 / 7,816 physical Python files (4.5%)**. Thirty projects remain pending
  and none are basic. The new admission adds nine locked service files;
  ten new Dispatch/repository-tool files remain pending under their owners.
  The preceding Index admission journal is now upstream.
- The operator selected **persistent 3.10/3.11 qualification** for #10331,
  preserving Versioned Runtime's actual TOML availability/error behavior and
  minimums rather than adding a runtime backport. The proposed source-owned
  checker extension must cover the existing local, source-PR and exact-candidate
  entry points, cache identity, real negative controls and canonical generated
  copies. No library or consumer admission is claimed before review/merge.
- SSH service, Versioned Runtime/checker and retained effort-helper work
  continue independently. This effort, its Plan and its Validation Plan remain
  Active until every remaining project and cross-cutting gate is resolved.

### 2026-11-03 - SSH service merged; retained helpers qualified locally

- #10348 merged as `1a267c90284abce573370da582e3e70b215a76d5` after
  actual Copilot approval on exact head
  `bd12b7373c140d8ddd35bf07d44c409674b2d0ea`, green required CI run
  `41781687518` and zero review threads. The actual merged commit retains
  both required trailers. #10211 is closed; #9807 remains open.
- Fresh provider packet `816bd981cd29b582cbe30c26146d9b0f8aa0d7b5`
  passes **3,964 units in 61.198 seconds**, all 571 admitted input
  fingerprints, 236 positive coverage targets and zero effects. Canonical
  packet verification uses the actual provider Git manifest; all 571 actual
  provider and merged Git input blobs independently match. The actual merge
  differs from that packet candidate only in 13 effort Markdown documents;
  it is not relabeled as the packet's original commit or whole tree.
- Both 26-file strict targets pass. Actual Linux Python 3.10.22 passes
  **165 cases**, retaining seven original Windows-only skips. Actual Windows
  Python 3.10.22 passes **10 cases**, including every required Windows-only
  case, retaining one original POSIX-only skip. The target-specific SDK
  closures contain 45 and 46 distributions respectively. Original 254
  function contracts, 299 assertion expressions and four embedded Python
  literals remain; the two empty-query compatibility guards and copied-build
  permission fixture are explicit, tested deltas.
- The complete Plan and Validation Plan were reread. Every project row matches
  the refreshed merged inventory: **39 / 68 strict projects (57.4%)** and
  **377 / 7,816 physical Python files (4.8%)**. Twenty-nine projects remain
  pending; none are basic. This admission adds 26 locked service files without
  crediting any separately owned plugin or generated consumer.
- Retained effort helpers (#10215) are locally prepared, not merged:
  five physical files and five emitted targets pass both strict analysis
  targets; actual Python 3.11 Linux and Windows each pass all 15 native
  controls. All 39 original function contracts and 31 assertion expressions
  remain. Refreshed composed-source qualification passes the current
  3,972-node packet with caller-derived admission and isolated test resources;
  eight independently added Dev controls are included without changing selection.
  These five files remain pending until their own review and merge.
- Versioned Runtime/checker's persistent 3.10/3.11 implementation (#10331)
  remains prepared and independently uncredited while its metadata and native,
  copied-source and composed qualification gates are resolved. This effort,
  every unfinished Plan/Validation item and #9807 remain Active.

### 2026-11-03 - Retained effort helpers merged; payload helpers selected

- #10360 merged as `78d562784d698cba676f95d50169a994f33b932d` after
  actual Copilot approval on exact head
  `efbfd3fec5d67f0fdfb68d714e1216fedc01ace2`, green required CI run
  `41785715387` and zero review threads. The actual merged commit retains
  both required trailers. The source-attribution and normal publication
  checks pass; no CI gate is bypassed.
- Fresh provider packet `0a86b543a1c0eafb2f47b08f44bd324232ed0e56`
  passes **3,972 units in 63.509 seconds**, 571 admitted input fingerprints,
  236 positive coverage targets and zero effects. Canonical verification
  uses the actual provider Git manifest and merge-candidate parent identity.
  All 571 actual merged input blobs also match, and all five actual merged
  helper/test files equal the independently native-qualified source bytes.
- Both five-file strict analysis targets pass on the existing Python 3.11
  minimum. Actual Linux and Windows Python 3.11 each pass all **15 native
  controls**, including disposable-runtime payload ownership and optimized
  interpreter refusal checks. All 39 original function/default/decorator
  contracts and 31 assertion expressions remain. The unfiltered `RECORD`
  read repairs a coupled refusal gap rather than weakening a negative test.
- Refreshed composed-source qualification passes the current **3,972-node**
  packet against 571 development inputs, 1,907 materialized candidate inputs
  and 720 canonical/copied targets, with caller-derived admission and zero
  effects. Exact disposable containers and volumes are retired. This is
  current source qualification, not a rerun or relabeling of the historical
  two-consumer installation experiment.
- The complete Plan and Validation Plan were reread. Every tracker row matches
  the refreshed merged inventory: **40 / 68 strict projects (58.8%)** and
  **382 / 7,818 physical Python files (4.9%)**. Twenty-eight projects remain
  pending and none are basic. Five helper files are newly locked; two newly
  inventoried Codespaces-plugin files remain pending under that owner's row.
- #10362 selects the independent Copilot Extensions Harness payload-helper
  project, initially 17 files, for bounded source preparation. It excludes
  the shared checker and generated runtime family already owned by #10331.
  Both slices remain uncredited until their own review, applicable native/
  emitted-copy qualification and merge. This effort and #9807 remain Active.

### 2026-11-03 - Canonical Payload Invocation preparation selected

- #10371 selects the independent canonical Payload Invocation library,
  initially 31 physical Python files, including its executed/emitted Python.
  Preparation owns the library, its tests and its checker row, not writes
  across consumer trees or the shared checker already owned by #10331.
- The Copilot Extensions Harness helper slice has typed three scripts and
  preserved its original callable/assertion contracts in light tests, but its
  complete project remains pending while test typing, real import resolution
  and embedded/minimum qualification continue. An unresolved first-party
  import is not permission to insert an unchecked substitute.
- These are preparation scopes, not admissions. Source-data correspondence,
  actual interpreter/native and generated-copy qualification, review and
  required CI remain explicit gates. All global Plan/Validation and final
  completion conditions remain Active.

### 2026-11-05 - Shared checker prerequisite merged; portfolio reconciled

- #10553 merged into Dev as `abc0e42784388ddac6d1c5c98103ba699ed2c91e`
  at 05:18:41 UTC after Copilot approval `5780121302` on exact head
  `68ca30dc1aec6ce7e8f43e9352a62b4b370f5d35`, green required checks,
  source attribution, workflow lockdown and resolved threads. Normal
  role-scoped completion used no CI bypass. #10400 closed only after merge;
  #9807, #10331, #10362 and #10371 remain open.
- The delivered prerequisite provides real ordinary import roots, additional
  qualification-version APIs with unchanged defaults, source-frozen custody
  and native `MYPYPATH` separator guards. The small ignored-frame audit-event
  allocation fast path preserves those guards; its microbenchmark is not
  evidence of the cause of earlier suite deadline failures.
- Normal uninstrumented local source and frozen-candidate packets on `68ca30d`
  each pass **4,129 nodes / 12,387 phase reports**, zero effects and
  **247 measured positive files / 247 required origins**, in **255.859**
  and **253.140 seconds**, respectively, under unchanged 300-second limits.
  Source has 588 inputs and 247 targets; candidate has 1,924 inputs and
  731 declared targets, not 731 measured bodies. The candidate
  `f50c334bb08bc389e7d0a621bcaef5940c44f900` was prepared at
  04:35:43 UTC against Main `690b45b7d032502b02710a33782ffdfaa691a61e`.
- Provider CI runs `41971486501` and `41971500229` instead qualify actual
  input identity `7153ff00171ec37563e4ce13e745329fe911ebd1`:
  **4,136 nodes / 12,408 phase reports**, zero effects, in **87.173**
  and **50.304 seconds**. Independent canonical packet verification and
  actual merged-Git comparison reconcile all **590 source inputs and
  248 targets** with `abc0e42`. This is input correspondence, not a
  relabeling of either packet's original commit or whole tree. Postmerge
  run `41973210769` succeeds on `abc0e42`, but its unit job is skipped by
  ordinary push policy and supplies no new postmerge packet.
- Qualification artifacts are retained with their original index, byte lengths
  and SHA256 values verified. Earlier failed diagnostics and 900/600/300-second
  allocation breaches remain historical failures, not positive qualification.
- The current reviewed Dev snapshot `ff44a10389f0b735a8ead7ad41e34e3ceb95047d`
  and actual inventory reconcile every row: **40 / 68 strict projects
  (58.8%)**, **384 / 7,981 physical Python files (4.8%)**, **28 pending,
  zero basic and zero unclassified paths**. Two additional files belong to
  existing strict Credential Relay and Dispatch-service scopes; no new
  consumer project is admitted.
- This documentation-only reconciliation passes the actual inventory CLI,
  all-row/count correspondence, documentation consistency and effort-structure
  checks. It does not rerun or replace the foundation's unit/MyPy packets;
  clean-room and live-venue tiers are inapplicable because no runtime,
  provisioning or installation behavior changes.
- Fresh acceptance on exact `68ca30dc1aec6ce7e8f43e9352a62b4b370f5d35`
  passes all **four unchanged version-matrix cases / 28 actual MyPy analyses**
  through the real source and frozen-candidate front doors on `linux` and
  `win32` analysis targets, with zero failures, errors or skips. The controls
  preserve minimum-only defaults, accept both clean versions, reject a bad
  3.10 assignment while 3.11 remains clean, and reject a 3.11-only TOML
  assignment while 3.10 remains clean. Raw JUnit and exact source/SDK witnesses
  are retained and independently verified; inputs remain unchanged.
- Those tests retain their existing interpreter-locator and prepared-marker
  adapters while exercising real Git, frozen importing and MyPy. They are
  not normal frozen-SDK first-provision, native minimum-interpreter runtime,
  library/copy/emitted admission or a new full packet. Earlier predecessor
  controls and failed allocation ledgers remain separate historical evidence.
- Next: integrate canonical consumer aliases, emitted/source correspondence
  and remaining native/interpreter gates for #10331, #10362 and #10371.
  Copies and fragment/runtime/POSIX qualification remain independent. This
  effort stays Active; source merge is neither release nor installed rollout.

### 2026-11-06 - Checker-only sequencing clarified; consumer proof remains open

- #10638 remains a pending checker-only prerequisite, not a merged change or
  consumer admission. The approved sequencing removes the extra local full
  composed/frozen proof from that PR's opening prerequisites while retaining
  **full-composed-consumer-qualification** under #9807 / #10362 as unmet.
  Normal required development-unit checks, source profiles, release-controller
  behavior, budgets and required CI are unchanged.
- The earlier normal development packet passes **4,148 nodes / 12,444 phase
  reports**, zero effects and a **248-target basis** in **253.234 seconds**.
  Focused source/frozen evidence separately passes six positive controls,
  four-module checks, eight argument/return negative controls and 17 custody
  guards. Neither focused evidence nor the development packet is a successful
  full frozen-candidate packet.
- The separately prepared full frozen candidate did not pass its original
  normal deadline or the subsequent ordinary short-path 300-second attempt.
  These failures and their diagnostics remain retained for #10638's eventual
  evidence and PR limitations; they are not positive packet credit, an accepted
  known flake, or permission to waive CI.
- Upstream manifest movement requires fresh development qualification after
  the checker owner preserves both upstream changes and its intended additions.
  The earlier 4,148-node result is historical, not proof of the current
  rebased implementation. No prospective packet count is recorded as executed.
- The actual inventory on reviewed Dev
  `5155bd04dafac9f25a72786f92a85d1fbcc63f21` reconciles all **68 projects**:
  **40 strict (58.8%)**, **28 pending**, **zero basic or unclassified paths**;
  **400 / 8,346 physical Python files (4.8%)** belong to existing strict scopes.
  Compared with the prior 384/7,981 snapshot, 365 physical files were added,
  including 16 within already-strict upstream owners. No new project admission
  or credit from prepared checker/consumer work is inferred.
- Actual consumer, immutable-root, copy/emitted, native and candidate-promotion
  gates remain open, including #10331, #10362 and #10371. This effort and #9807
  remain Active. This documentation slice changes no source policy, runtime,
  installed payload or required CI and does not complete the campaign.

### 2026-11-06 - Checker-only alias delivered; actual consumer integration next

- #10866 merged into Dev as `07e4932be0d4c1f6d348be95b39dbd61deccc1df`
  after Copilot approval `5795502451` on published head
  `7e95af2542e529148ab7ab91b41cf1f16b68d940`, green required checks and
  no open findings. #10638's checker-only prerequisite is delivered; no Harness,
  Versioned Runtime or Payload Invocation project admission follows from it.
- Hosted `--enforced` checking passes the checker on both `linux` and `win32`
  Python 3.10 targets, with four physical files/four targets, and all admitted
  scopes. Fresh producer run `42156951476` qualifies **4,174 units**; later
  run `42157114998` reuses that verified same-head packet with fresh guards and
  type checking, rather than providing a second fresh unit execution.
- The retained original CI archive passes its provider digest and size binding.
  Independent, data-only canonical verification against actual CI-bound Git
  source `d5a9ae56e658fa0bb8af340948ff2d0ad355cb4e` verifies all **597 input
  hashes**, **12,522 raw phase reports** (4,174 each setup/call/teardown),
  **zero effects**, and **251 positive measured files/declared targets**.
  Packet time is **94.682 seconds** under the unchanged 300-second limit.
- That CI-bound source is distinct from both the published head and final Dev
  merge. Two upstream inputs differ at the final merge:
  `tools/promotion_unit_tests/test_related_acquisition.py` and
  `plugins/agent-worktrees/src/agent_worktrees/config_acquisition.py`.
  The original failed whole-merge equivalence check is retained, not relabeled
  as a pass; no complete merged-tree or frozen-candidate packet is claimed.
- The nine immutable shipping records and selected earlier source/frozen
  positive, negative and input-provenance records are retained with verified
  byte lengths and hashes. The earlier six positives, four-module controls,
  eight argument/return negatives and 17 custody guards qualify only their
  historical bytes. The Windows full frozen-candidate timeouts remain failures,
  not a known flake, CI waiver or consumer qualification.
- The read-only inventory on reviewed Dev
  `064444b6e6b41c4ea645033bcec061b191686418` reconciles **69 declared projects**:
  **40 strict (58.0%)**, **29 pending**, **zero basic or unclassified paths**,
  and **400 / 8,355 physical Python files (4.8%)** in strict scopes.
  Sixty-eight projects own physical paths; newly declared
  `service-vendored-libraries` remains pending with zero tracked physical files.
  Nine additional physical files belong to pending owners; no strict admission
  is added and the earlier 40/68, 400/8,346 snapshot remains historical.
- Next is actual Harness consumer integration (#10362), preserving the
  immutable 64-root, copied/emitted, native and candidate-promotion gates.
  Versioned Runtime (#10331), Payload Invocation (#10371), the full composed
  consumer objective and every original unfinished portfolio gate remain open.
  This effort and #9807 stay Active; source delivery is not release or rollout.

### 2026-11-06 - Emitted-program checker prerequisite reconciled after #10938

- Provider-confirmed #10938 merged as
  `baddb10f709649756a2ff1f1cfeb961057be54ef` at 09:23:49 UTC, from published
  head `c3fac7dc5b4cf63038b046c618cd70b05ea8030d`. The current Copilot
  COMMENT review (5798063039), all six resolved threads and normal managed
  role-scoped merge were reported by the delivery owner; no CI bypass was used.
  The canonical Plan and Validation Plan were reread after merge: this delivers
  bounded checker visibility/binding completeness, not strict consumer admission.
- Four positive analysis cells took 87.545 seconds: the strict checker checks
  four physical files/four targets; the independently pending Harness checks
  17 physical files plus 18 supplemental programs (35 targets), each for
  Python 3.10/Linux and 3.10/win32. The pending command exits 1 and is not an
  admission pass. The 18 actual emitted programs retain identical bytes, paths
  and source ranges against the earlier actual baseline; its 21 inputs remain
  unchanged. Lexical binding is limited to unique same-suite/ancestor-prior
  assignments and source-origin construction guarantees; deeper nested children
  are refused and non-Name binders remain guarded.
- Local evidence reported 46 focused plus five pure controls passing (51 total),
  six impacted components passing, and an actual MyPy negative in 14.8 seconds.
  These are not hosted CI or native evidence. The normal 45-negative cleanup
  attempt still timed out and is unqualified; the earlier 44 quarantine objects
  remain protected, with no replay waiver or removal. Actual native/POSIX,
  full frozen-candidate, emitted-copy and full consumer qualification are not
  claimed by these local packets.
- Independently downloaded the actual hosted artifact **13490816434** from
  run **42192875462**, attempt 1, job **127808910057**. Its original 1,516,012-byte
  archive matches provider SHA-256
  `43690edf32243b3af01b8415ba1866d42ac26f6a6cd63a2d6b66ade0a5fdf833`;
  the provider reports it unexpired and bound to the published head. Only the
  three JSON data members were read; no archive code, SDK, tests or emitted
  programs were executed. The repository's data-only packet verifier passes
  against the manifest read from actual CI-bound Git source
  `364b451ff5ac36299d537837b75c3deb0aa7e88a`: **4,226 nodes**, each with setup,
  call and teardown (12,678 phase reports), no effects, **251/251** declared
  positive-coverage files and **598** independently hashed source inputs.
  Recorded execution is 71.758823401 seconds within the unchanged 300-second
  packet bound. Packet digest:
  `f06ccc7ec73469f436c88abb8c7a9c4a6134cf1db2e58fa782999db552689f8a`.
- Comparing the actual final merged Git blobs with those CI-bound inputs finds
  three differences: `libs/peer-launch/peer_launch.py`,
  `plugins/agent-bridge/src/agent_bridge/_peer_launch.py` and
  `plugins/agent-bridge/src/agent_bridge/session_lifecycle_cli.py`.
  The retained proof records both hashes for each delta. This proves the
  CI-bound packet, **not whole-final-merge equivalence**. Original archive,
  protocol, Git blob data, capacity/cleanup receipts and retention index remain
  locally retained; earlier protected evidence is neither reused nor removed.
- Fresh normal owner-managed Dev sync preserves upstream work and reconciles
  **every one of 71 manifest rows** at
  `91ce5da3e2971c4f3a7520b2627b34fac1f8a8da`: **40/71 strict projects
  (56.3%)**, **400/8,424 physical Python files (4.7%)**, **31 pending**, zero
  basic and zero unclassified. Seventy projects own physical paths, with the
  zero-file vendored-library project still pending. The 40/69, 400/8,355,
  8,363-file and 8,411-file observations are different historical snapshots,
  not competing admission claims. No new strict project or file is credited.
- Independent pending all-Python lanes remain visible: Installer Engine
  (#10931, 28 physical files) reported a local host-options cohort with
  448 strict/124 basic diagnostics on the first 3.10/Linux cell and 14 passes,
  zero skips (index
  `9b51ad8708f19cfd1f98acf280204d451b19e8b15e0dd758e1d5fdbb3c174359`).
  Efforts (#10955, 36 physical files) reported 105 IO diagnostics, all in tests,
  none in production, on the first 3.10/Linux cell, plus 19 passes and one
  Tier-3 skip (index
  `6188a95b5f81ae8521114e6187d47f6ba683d3c5b6b5f8bc19f9ab4b14fd61c3`).
  Actual missing-provider refusal needs `--allow-explicit-tiers` and was not
  exercised. Both win32 cells short-circuited without running; further source
  changes remain unqualified. These are local preparation, not merged
  admissions or additions to strict coverage.
- Next remains #10362's complete composed consumer: immutable 64-root boundary,
  actual aliases, native/emitted-copy and full frozen-candidate/promotion proof.
  #10331, #10371 and all independent pending projects retain their own gates.
  The root objective is every first-party Python path strictly qualified,
  zero pending/basic/unqualified scopes, final local/CI/release parity and
  explicit resolution of every Plan/Validation item before Done. #9807 and
  this effort remain **Active**; no archive, finalization, release or rollout
  is authorized by this reconciliation.
