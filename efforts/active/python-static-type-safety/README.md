# Python Static Type Safety

- **Slug:** `python-static-type-safety`
- **Repo:** copilot-extensions
- **Branch(es):** Independent, reviewed PRs targeting `dev`; one active slice per worktree.
- **Created:** 2026-10-31
- **Status:** Active
- **Vision:** Verification improvement below runtime-architecture altitude; preserves `visions/test-portfolio/README.md`'s cheapest faithful tier, bounded feedback and containment.
- **Umbrella issue:** #9807
- **Current slice:** #10021 canonical Configuration Layers source/test contracts and strict admission. #10018 canonical Installation Context admission and its final #10013/#10014/#10015 test cohorts are complete.
- **Sub-issues:** #9831 (P1 checker foundation, complete); #9853 (P2 required source CI, complete); #9862 (P3 exact-candidate qualification, complete); #9916 (P4a canonical Installation Context source view, complete); #9924 (L1 Config Migrate strict admission, complete); #9930 (L1 Dropin Registry strict admission, complete); #9933 (L1 Endpoint Rendezvous strict admission, complete); #9941 (L1 initial leaf-library test/helper completion, complete); #9951 (L1 Lazy CLI Dispatch strict admission, complete); #9964 (L1 Bootstrap Killswitch strict admission, complete); #9967 (L1 Peer Launch strict admission, complete); #9971 (P2a Installation Context native typing preparation, complete); #9975 (P2b validation-decorator signature preservation, complete); #9980 (P2c production result and flow contracts, complete); #9983 (P2d native platform API contracts, complete); #9986 (P2e cleanup and worker contracts, complete); #9993 (P2f shared receipt-fixture typing, complete); #9996 (P2g slot-release test contracts). Create/reuse later scoped issues as selected. #9177 remains separately owned.

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
- **Delegates:** No active delegated implementation. The lead owns #10021's four canonical Configuration Layers source/test modules, the effort, shared checker controls and full completion judgment. The prior POSIX and guest-environment delegates completed their isolated cohorts.
- **Handoff:** A merged slice returns to the canonical README, updates its Plan/Validation/Journal, then selects the next unresolved item. A continuation retains #9807, this README, the current slice, live PRs and exactly one next action.
- Existing contributor PRs are reviewed/coordinated, not superseded. Shared-library changes precede dependent activations; unrelated leaf activations may proceed independently.

## Context

The initial investigation found no MyPy CI gate. `AGENTS.md` and
`CONTRIBUTING.md` encourage type hints; Ruff `F,E9` is not a type checker.
The [baseline](baseline.md) records a fixed source snapshot, syntactic annotation
coverage, progressive checker diagnostics and important limitations.

The implementation and PR boundaries are in [rollout](rollout.md). Existing
owners remain authoritative for dependency seasoning, test-environment
admission, canonical source/materialization and Main's governed release
controller. This effort adds a static-validation contract; it does not replace
those systems or authorize installed-runtime rollout.

## Request

> Make an effort to enforce 100% type-safety on all copilot-extensions python code, enforced by MyPy. You will need to plan our a way to get there, breaking the work into reasonably-sized PRs, to provide CI-time enforcement and activating projects part by part until we achieve compliance. You can use initial non-strict enforcement initially to get pieces in, and enforce strictness as codebases come closer to compliance and deltas are minimized. You'll want to provide simple local commands that agents run can to pre-validate, so they are not caught off guard by CI.

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

## Scope Approvals

- The operator authorized planning, normal reviewed implementation PRs, phased non-strict admission, eventual strict compliance and simple local pre-validation.
- The confirmed terminal policy is every first-party file checked strictly, with typed dynamic boundaries and narrow reviewed third-party exceptions. It is not a literal ban on all `Any` expressions.
- No blanket ignores, unchecked first-party substitutes or permanent pending/basic cohorts count as completion.
- Normal PR/CI/review gates remain. Main-controller adoption is limited to the
  explicitly approved two-step workflow change and job deadline above; no ruleset/CI bypass,
  pause/resume, dependency-policy relaxation, service restart or installed
  rollout is authorized.

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
- [x] Activate the already-clean small libraries in strict mode and the checker infrastructure's own code/tests; verify actual negative MyPy controls.
- [x] Add required source-PR static checks using the same front door and project policies; preserve existing unit selection.
- [x] Land candidate-local static-checking source/controller contracts with frozen-input identity, failure propagation and helper decision qualification.
- [x] Adopt exact-candidate release static checks in Main through the separately approved workflow-only change; preserve Main control.
- [x] Prove live candidate enforcement and regression rejection under the adopted Main controller.
- [x] Document and validate local `--prepare`, per-project, `--enforced`, `--changed`, `--all` and inventory commands. Default checking never implicitly installs tooling.

### Phase 2 - Source and platform qualification

- [x] Land the bounded canonical Installation Context composition/source-map adapter (#9916); keep copies independently pending.
- [ ] Make executed source fragments statically visible with source-mapped composition or genuine typed modules; qualify every source line and byte-identical copy without suppressing errors.
- [ ] Resolve tooling module identities, independent package environments, dependency/stub versions and declared interpreter/platform targets.
- [ ] Qualify generated templates and release-materialized copies against the actually checked canonical source, including negative drift controls.

### Phase 3 - Shared-library ratchet

- [x] Strictly admit all Config Migrate source/test files with a declared YAML/stub environment (#9924).
- [x] Strictly admit all canonical Dropin Registry source/test files; keep copies independently pending (#9930).
- [x] Strictly admit all canonical Endpoint Rendezvous source/test files, preserving native ownership fixtures (#9933).
- [x] Strictly admit canonical Installation Context source, worker and every test/helper after the final fixture cohorts (#10018).
- [ ] Strictly admit all four canonical Configuration Layers source/test modules (#10021).
- [ ] Admit near-clean leaf libraries through small annotation/narrowing PRs; activate directly in strict mode when possible.
- [ ] Admit remaining shared libraries in basic mode, then close their stricter deltas in separate PRs until every shared library is strict.
- [ ] Replace loose generics and unchecked boundary results with honest callable, protocol and validated data contracts; rerun affected consumer checks/tests.

### Phase 4 - Runtime and service ratchet

- [ ] Activate each runtime plugin and standalone service independently, using basic mode only where needed and recording its remaining strict delta.
- [ ] Tighten each activated runtime/service to strict through component-sized PRs; cover source, optional extras and platform-specific implementations.
- [ ] Converge the larger Worktrees, Worktree Manager and Fleet Manager surfaces through subsystem-sized PRs, preserving lazy-loading and native behavior.

### Phase 5 - Tests, tools and bootstrap

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
- [x] Actual MyPy subprocess controls prove an incompatible argument/return fails basic mode and missing annotations/bare generics fail strict mode.
- [ ] Local and CI use identical pinned checker, dependencies, project config and interpreter/platform targets; unknown source/setup errors fail visibly.
- [x] Changed-scope checks include working-tree edits and affected activated consumers; changes to checker/config/workflows cannot skip enforcement.
- [ ] Composition and generated-copy controls reject omitted/reordered/changed fragments or stale source correspondence; no empty result can certify coverage.
- [ ] Each typing change preserves behavior through the smallest existing focused tests plus directly relevant integration/native checks. Skips are explicitly justified in that PR.
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
