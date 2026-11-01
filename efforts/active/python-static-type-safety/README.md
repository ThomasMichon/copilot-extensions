# Python Static Type Safety

- **Slug:** `python-static-type-safety`
- **Repo:** copilot-extensions
- **Branch(es):** Independent, reviewed PRs targeting `dev`; one active slice per worktree.
- **Created:** 2026-10-31
- **Status:** Active
- **Vision:** Verification improvement below runtime-architecture altitude; preserves `visions/test-portfolio/README.md`'s cheapest faithful tier, bounded feedback and containment.
- **Umbrella issue:** #9807
- **Sub-issues:** #9831 (P1 checker foundation, complete); #9853 (P2 required source CI, complete); #9862 (P3 exact-candidate qualification). Create/reuse later scoped issues as selected. #9177 remains separately owned.

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

## Scope Approvals

- The operator authorized planning, normal reviewed implementation PRs, phased non-strict admission, eventual strict compliance and simple local pre-validation.
- The confirmed terminal policy is every first-party file checked strictly, with typed dynamic boundaries and narrow reviewed third-party exceptions. It is not a literal ban on all `Any` expressions.
- No blanket ignores, unchecked first-party substitutes or permanent pending/basic cohorts count as completion.
- Normal PR/CI/review gates remain. Main-controller adoption is limited to the
  explicitly approved two-step workflow change above; no ruleset/CI bypass,
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
- [ ] Add exact-candidate release static checks through the existing controller contract; preserve Main control.
- [x] Document and validate local `--prepare`, per-project, `--enforced`, `--changed`, `--all` and inventory commands. Default checking never implicitly installs tooling.

### Phase 2 - Source and platform qualification

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
- [ ] Required exact-candidate release gate rejects a type regression on activated code.
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
