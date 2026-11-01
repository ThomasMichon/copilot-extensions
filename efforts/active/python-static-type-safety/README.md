# Python Static Type Safety

- **Slug:** `python-static-type-safety`
- **Repo:** copilot-extensions
- **Branch(es):** Independent, reviewed PRs targeting `dev`; one active slice per worktree.
- **Created:** 2026-10-31
- **Status:** Active
- **Vision:** Verification improvement below runtime-architecture altitude; preserves `visions/test-portfolio/README.md`'s cheapest faithful tier, bounded feedback and containment.
- **Umbrella issue:** #9807
- **Sub-issues:** #9831 (P1 checker foundation, complete); #9853 (P2 required source CI). Create/reuse later scoped issues as selected. #9177 remains separately owned.

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

## Scope Approvals

- The operator authorized planning, normal reviewed implementation PRs, phased non-strict admission, eventual strict compliance and simple local pre-validation.
- The confirmed terminal policy is every first-party file checked strictly, with typed dynamic boundaries and narrow reviewed third-party exceptions. It is not a literal ban on all `Any` expressions.
- No blanket ignores, unchecked first-party substitutes or permanent pending/basic cohorts count as completion.
- Normal PR/CI/review gates remain. No ruleset bypass, release-controller activation, dependency-policy relaxation, service restart or installed rollout is authorized by this effort.

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
- [ ] Add required PR and candidate-release static checks using the same front door and project policies; preserve existing unit selection and Main control.
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
- [ ] Required source and release gates actually reject a type regression on activated code; final strict gate rejects a newly unclassified Python file.
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
