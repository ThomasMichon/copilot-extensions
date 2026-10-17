# Dispatch PR selection

Parent: [Coverage-Guided CI](README.md), Phase 5.

## Request

> We need to use the coverage-based CI selection, and choose a bounded set
> of cross-cutting tests for "smoke"

Coordination: [#4453](https://github.com/ThomasMichon/copilot-extensions/issues/4453);
the concrete timeout is also recorded on
[#6800](https://github.com/ThomasMichon/copilot-extensions/pull/6800).

The current required `agent-dispatch` job executes the full portfolio rather
than smoke: total job wall time, even when every test passes, can exceed its
twelve-minute outer deadline.
Keep that deadline unchanged. Full promotion/manual validation remains
independent and exhaustive.
The current job deadline and full/manual lanes are configured in
[`ci.yml`](../../../.github/workflows/ci.yml); the independent promotion
portfolio is configured in
[`validate-and-promote.yml`](../../../.github/workflows/validate-and-promote.yml).

## Proposed execution contract

The implementation details below are **agent-recommended**, realizing the
existing coverage-guided CI and test-portfolio visions, not new operator intent.

- Reuse the existing [main-history pointer and Release baseline](README.md#phase-1--baseline-generation--correlation-at-the-promotion-gate),
  [ancestor correlation and attribution invalidation](README.md#phase-2--nearest-ancestor-resolution--attribution-remapinvalidate),
  and [debt assessment and `decide()` selector](README.md#phase-3--diff-scoped-selection--coverage-debt--smoke-fallback).
  Their implementations live in
  [`correlation.py`](../../../tools/coverage_guided_selection/correlation.py),
  [`ancestor_resolution.py`](../../../tools/coverage_guided_selection/ancestor_resolution.py),
  [`debt.py`](../../../tools/coverage_guided_selection/debt.py) and
  [`decide.py`](../../../tools/coverage_guided_selection/decide.py).
  Do not create a parallel name-filter selection policy.
- Always execute structural guards (`@pytest.mark.guard`, selected by the
  existing runner's `--guards` option) plus a bounded cross-cutting smoke set
  derived from the baseline's measured coverage and test costs through the
  existing [weighted-set-cover curation](../../../tools/coverage_guided_selection/fallback.py).
  Union that floor with attributed
  diff-selected tests; never discard a known implicated test to fit a budget.
- Make smoke/debt budgets explicit and report the baseline generation,
  fallback reason, selected node IDs, predicted cost and coverage breadth.
  Measure actual complete invocation time, including preparation and cleanup.
- Execute exact pytest node IDs through the existing
  [contained runner](../../../tools/run-plugin-tests.py), which enforces
  [isolated state roots and bounded child-process execution](../../../tools/plugin_test_containment.py).
  Its invocation and isolation contract are documented in
  [`TESTING.md`](../../../TESTING.md).
  Validate paths and selected-node existence; stale, malformed, absent or
  empty evidence must never silently mean a successful zero-test run.
  Preserve the selector's visible broader fallback where no usable baseline
  exists; do not replace it with a guard-only green check.
- Preserve changed/new test execution and conservatively handle changes
  outside the baseline's attributed source scope. Coverage is runtime
  evidence, not evidence that a text-scanning guard or changed dependency
  is irrelevant.
- Pilot only `agent-dispatch` PRs. Keep push, manual, promotion and other
  plugin lanes on their existing portfolio unless separately reviewed.
  Leave Fleet Manager collector/admission work in
  [#6783](https://github.com/ThomasMichon/copilot-extensions/pull/6783) with
  its existing owner. The unrelated shadow-only rollout remains in
  [Phase 4](README.md#phase-4--rollout-replace-agent-worktrees-collect-only-tier).

## Plan and validation

- [x] Review and merge this proposal before activating required execution.
      Merged in [#7093](https://github.com/ThomasMichon/copilot-extensions/pull/7093).
- [x] Measure real baseline curation and choose a bounded smoke budget with
      cross-cutting breadth evidence, not only a synthetic timing estimate.
      The 120-second floor covers 99.36% of 40,105 attributed lines across
      all 234 baseline source files; real execution evidence is below.
- [x] Add strict contained node execution and regression coverage for invalid,
      missing, renamed and empty node lists; ordinary full runs stay unchanged.
- [ ] Wire coverage selection plus the additive smoke floor into the PR lane;
      test fresh, missing, stale, debt-saturated, unattributed and mixed diffs.
- [x] Demonstrate a runtime-covered regression is selected and fails, while
      the corresponding clean selected invocation passes.
      The opt-in real-coverage fixture proves clean/broken/repaired execution
      and that the detecting test exceeds the smoke budget but is retained.
- [ ] Measure an actual hosted required invocation inside the unchanged
      twelve-minute deadline, including setup and cleanup, before claiming
      the timeout repaired.
- [ ] Update `TESTING.md` and the canonical effort with actual landed behavior
      and evidence. Leave unrelated parent completion gates open.

## Journal

- 2026-10-16: proposed a bounded pilot using the existing mechanism.
  `agent-worktrees` remains shadow-only; nine published plugin pointers
  already include `agent-dispatch`. No required execution policy changed.
- 2026-10-16: proposal #7093 merged after a clean current-head review and
  successful complete required CI. Implementation is next; the execution
  and hosted deadline proofs remain open. A real dispatch baseline curated
  2,159 tests at a 120-second predicted budget, covering 99.36% of its
  attributed lines across all 234 source files. The existing greedy
  implementation itself took several minutes, so execution also needs
  semantics-preserving curation acceleration, not a larger CI deadline.
- 2026-10-16: implemented exact-node manifests, additive guards/changed test
  files, fork-coordinate selection and the PR-only dispatch lane locally.
  The existing curator now takes 0.74 seconds on the same real baseline and
  returns the identical count, cost and breadth. One hundred randomized
  baselines also match the original greedy ordering and accounting.
  A real network-disconnected devcontainer invocation completed preparation,
  execution and cleanup in 102.31 seconds: 2,285 passed, 7 skipped,
  3,591 deselected; pytest itself took 77.52 seconds. This is local evidence,
  not the still-required hosted twelve-minute proof. Focused runner,
  curation and container-transport contracts passed (481 passed, 16 skipped);
  the separate opted-in runtime-covered regression proof passed all 15
  contracts, including the intentionally failing selected subprocess.
