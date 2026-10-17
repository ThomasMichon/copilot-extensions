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

- [ ] Review and merge this proposal before activating required execution.
- [ ] Measure real baseline curation and choose a bounded smoke budget with
      cross-cutting breadth evidence, not only a synthetic timing estimate.
- [ ] Add strict contained node execution and regression coverage for invalid,
      missing, renamed and empty node lists; ordinary full runs stay unchanged.
- [ ] Wire coverage selection plus the additive smoke floor into the PR lane;
      test fresh, missing, stale, debt-saturated, unattributed and mixed diffs.
- [ ] Demonstrate a runtime-covered regression is selected and fails, while
      the corresponding clean selected invocation passes.
- [ ] Measure an actual hosted required invocation inside the unchanged
      twelve-minute deadline, including setup and cleanup, before claiming
      the timeout repaired.
- [ ] Update `TESTING.md` and the canonical effort with actual landed behavior
      and evidence. Leave unrelated parent completion gates open.

## Journal

- 2026-10-16: proposed a bounded pilot using the existing mechanism.
  `agent-worktrees` remains shadow-only; nine published plugin pointers
  already include `agent-dispatch`. No required execution policy changed.
