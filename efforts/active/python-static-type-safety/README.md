# Python Static Type Safety

- **Slug:** `python-static-type-safety`
- **Repo:** copilot-extensions
- **Branch(es):** Independent, reviewed PRs targeting `dev`; one active slice per worktree.
- **Created:** 2026-10-31
- **Status:** Active
- **Vision:** Verification improvement below runtime-architecture altitude; preserves `visions/test-portfolio/README.md`'s cheapest faithful tier, bounded feedback and containment.
- **Umbrella issue:** #9807
- **Sub-issues:** #9831 (P1 checker foundation, complete); #9853 (P2 required source CI, complete); #9862 (P3 exact-candidate qualification, complete); #9916 (P4a canonical Installation Context source view, complete); #9924 (L1 Config Migrate strict admission, complete); #9930 (L1 Dropin Registry strict admission). Create/reuse later scoped issues as selected. #9177 remains separately owned.

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

- **Topology:** Independent per-slice PRs, never a shared staging branch.
- **Host (owns PRs):** `lead` owns its PRs and verifies completion of delegated PRs.
- **Delegates:** None allocated yet. A future assignment names the issue, exact files, validation commands and stop condition; it does not claim an entire wave.
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
