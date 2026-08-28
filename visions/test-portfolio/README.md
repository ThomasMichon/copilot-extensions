# Test portfolio effectiveness — Vision

- **Subject:** the repository-wide portfolio of automated tests for plugins and shared libraries
- **Scope:** leaf (concrete cross-cutting capability)
- **Status:** Active
- **Last revised:** 2026-08-28
- **Reality docs:** [`TESTING.md`](../../TESTING.md)

## Purpose & Intent

The test portfolio should provide the strongest practical assurance that the
plugin suite honors its behavioral contracts while remaining fast, legible,
safe to run, and proportionate to the defects it can detect. Test count is not
the objective. The objective is a bounded set of evidence-bearing tests in
which every retained family contributes unique confidence at the cheapest tier
that faithfully exercises the behavior.

Growth and reduction are both evidence-driven. A new test should name the
contract or regression it protects; an existing test should be consolidated,
moved, rewritten, or removed when another test provides the same assurance more
clearly or safely.

## Concepts & Components

### behavioral contract map

A durable map connects test families to the externally meaningful behaviors,
design invariants, compatibility promises, failure boundaries, and regressions
they protect. The map makes omissions and redundant coverage visible without
using raw test count or line coverage as a proxy for assurance.

### evidence-bearing test family

The portfolio is reasoned about in coherent families: cases that exercise the
same contract through the same fixture and assertion shape. Individual
parameter values remain visible, but portfolio decisions happen at the family
level so thousands of near-identical cases do not masquerade as thousands of
independent guarantees.

### validation tiers

Checks are placed at the least expensive tier that preserves fidelity:
structural guards, hermetic units, contained components, disposable clean-room
validation, and explicitly targeted end-to-end checks. Higher-fidelity tiers
complement lower ones; they do not silently join the default developer loop.

### containment boundary

Every runner owns the resources its tests create. Child processes, temporary
state, network access, services, and host mutations are either bounded and
reaped by the runner or moved to a disposable or explicitly opt-in venue.

### portfolio budget

Each default suite has visible time, process, memory, reliability, and
maintenance budgets. A contribution that expands a budget explains the unique
assurance gained, while equivalent or stronger coverage may replace weaker
tests instead of accumulating beside them.

## Features

### contract-complete portfolio

Every critical behavior and invariant has deliberate positive, negative, and
boundary coverage appropriate to its risk. Gaps are visible as missing contract
coverage rather than hidden behind a large aggregate test count.

### effectiveness evidence

Critical test families carry evidence that they can detect relevant defects,
using regression provenance, focused mutation analysis, fault injection, or
another observable falsification method.

### tiered execution

Contributors can run fast guards, hermetic units, contained components,
clean-room scenarios, and end-to-end checks as distinct, composable tiers with
clear defaults and promotion rules.

### bounded feedback loops

Per-plugin and changed-scope commands provide useful feedback within recorded
budgets. Expensive or hazardous validation remains available without making
routine development slow or unsafe.

### portfolio observability

The repository can report what each suite protects, how long it takes, which
effects it may cause, how reliable it is, and why each family remains in the
portfolio.

## Behaviors

### host-safe by default

A default test run does not leave descendant processes, services, temporary
state, network sessions, or host mutations behind after success, failure,
interruption, or timeout. Tests that cannot satisfy that guarantee run in a
disposable or explicitly opt-in venue.

### cheapest faithful tier

A behavior is tested at the lowest-cost tier that can genuinely falsify it.
Duplicating the same assertion at more expensive tiers requires a distinct
failure mode or compatibility guarantee.

### unique value or consolidation

Each retained test family protects a distinct contract, regression, platform
boundary, or defect class. Families whose assurance is subsumed by stronger
coverage are consolidated or removed rather than preserved for count.

### reductions preserve assurance

Portfolio reduction never trades away a critical contract merely to improve
runtime or test count. Removal is accompanied by evidence that equivalent or
stronger detection remains.

### growth is budgeted

New tests declare their tier, effects, contract, and expected cost. Additions
that exceed a suite budget either replace weaker coverage or explicitly revise
the budget with justification.

### failures stay attributable

A failed test identifies the contract and tier that failed. Broad,
implementation-coupled assertions do not obscure which behavior regressed or
force unrelated contributors to debug an entire subsystem.

## Non-Goals / Boundaries

- **No target test count.** A smaller portfolio is desirable only when it
  preserves or improves assurance.
- **No line-coverage maximization.** Coverage is supporting evidence, not the
  definition of value.
- **No ban on subprocess or end-to-end tests.** Side-effecting tests remain when
  they detect unique failures, but they run under containment or in disposable
  venues.
- **No replacement for clean-room validation.** The clean room continues to
  validate fresh-machine install, bootstrap, provision, and behavior claims;
  this vision governs how all tiers compose into one portfolio.
- **No automatic deletion.** Evidence organizes review; maintainers decide
  dispositions against the contract map.

## See Also

- Related vision: [`clean-room-validation`](../clean-room-validation/README.md)
- Testing guide: [`TESTING.md`](../../TESTING.md)
- Current runner: [`tools/run-plugin-tests.py`](../../tools/run-plugin-tests.py)
- Realization effort:
  [`test-portfolio-rationalization`](../../efforts/active/test-portfolio-rationalization/README.md)
