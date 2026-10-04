"""Coverage-guided CI test selection -- Phase 0 design-spike prototype.

Realizes a vertical slice of `visions/coverage-guided-ci` and
`efforts/active/coverage-guided-ci`'s Phase 0: prove the mechanism (coverage
collection -> baseline -> diff-scoped selection -> coverage-efficient
fallback curation) works end-to-end against one small plugin before wiring
anything into the real promotion gate (Phase 1) or the `agent-worktrees`
rollout (Phase 4).

Four pieces, each independently testable:

- `baseline`: collects a portable JSON baseline (test -> covered lines,
  test -> wall-clock cost) from a real pytest + `coverage.py` dynamic-context
  run, via an ephemeral `uv run --with coverage --with pytest-cov` subprocess
  so this package itself carries no new ambient dependency.
- `selection`: pure-stdlib diff-scoped selection -- given a baseline and a set
  of changed (file, line) pairs, returns the covering tests, or names which
  changed lines forced a fallback (no baseline entry, or a baseline entry
  with no attributing test for that specific line). Named `selection.py`,
  not `select.py`: the latter shadows Python's own stdlib `select` module
  whenever this package's directory lands on `sys.path` (e.g. running
  `baseline.py` as a script inserts its own directory first) -- harmless on
  an interpreter build where `select` is a frozen builtin, but a hard
  `AttributeError` inside `subprocess`'s own `import selectors` on a build
  where it isn't (confirmed: GitHub Actions' `actions/setup-python` CPython
  3.12.14 build hits this; a locally-built CPython may not).
- `fallback`: pure-stdlib coverage-efficient fallback curation -- a greedy,
  budget-bounded weighted-set-cover selection over the baseline's own
  per-test coverage and cost data (see the vision's "coverage-efficient
  fallback curation" Concept).
- `correlation`: where a baseline generation lives on `main` and how it is
  correlated back to the `dev` commit it was measured against (this
  effort's own 2026-10-01 storage/correlation decision) -- not yet wired
  into a real promotion run.
- `ancestor_resolution`: Phase 2 -- given an arbitrary fork-point commit,
  walks `main`'s own history of a plugin's checked-in baseline for the
  newest generation whose `measured_commit` is an ancestor of that fork
  point, then carries its line-level attribution forward through every
  intervening commit's own diff. A file invalidates entirely (dropped,
  regardless of whether any *covered* line sits inside the changed hunk)
  the instant its diff contains even one hunk that actually replaces
  existing content. Otherwise (pure insertions/deletions only), each
  covered line is remapped individually and asymmetrically: a line
  preceded only by deletions translates safely through the cumulative
  offset, but a line preceded by *any* insertion is dropped rather than
  remapped -- inserted code can introduce new control flow (an early
  `return`, a new guard) that skips a line a test used to reach, and a
  line-coordinate shift alone can't prove that didn't happen.
- `debt`: Phase 3 -- measures how stale a resolved baseline is relative to
  the commit a selection is being made for, along two independently
  tunable dimensions (commit-volume since `measured_commit`, and wall-clock
  age), either of which crossing its own threshold trips the smoke/fallback
  tier for the whole selection -- distinct from (and in addition to)
  `selection.select_tests`'s own per-file/per-line fallback triggers.
"""
