"""Coverage-guided CI test selection -- Phase 0 design-spike prototype.

Realizes a vertical slice of `visions/coverage-guided-ci` and
`efforts/active/coverage-guided-ci`'s Phase 0: prove the mechanism (coverage
collection -> baseline -> diff-scoped selection -> coverage-efficient
fallback curation) works end-to-end against one small plugin before wiring
anything into the real promotion gate (Phase 1) or the `agent-worktrees`
rollout (Phase 4).

Three pieces, each independently testable:

- `baseline`: collects a portable JSON baseline (test -> covered lines,
  test -> wall-clock cost) from a real pytest + `coverage.py` dynamic-context
  run, via an ephemeral `uv run --with coverage --with pytest-cov` subprocess
  so this package itself carries no new ambient dependency.
- `select`: pure-stdlib diff-scoped selection -- given a baseline and a set
  of changed (file, line) pairs, returns the covering tests, or names which
  changed lines forced a fallback (no baseline entry, or a baseline entry
  with no attributing test for that specific line).
- `fallback`: pure-stdlib coverage-efficient fallback curation -- a greedy,
  budget-bounded weighted-set-cover selection over the baseline's own
  per-test coverage and cost data (see the vision's "coverage-efficient
  fallback curation" Concept).
"""
