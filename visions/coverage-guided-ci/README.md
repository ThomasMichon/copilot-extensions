# Coverage-Guided CI Test Selection — Vision

- **Subject:** using durable code-coverage evidence, established at trunk
  validation, to select and prioritize which tests a given change's CI
  actually needs to run
- **Scope:** leaf (concrete cross-cutting capability)
- **Status:** Active
- **Last revised:** 2026-09-28
- **Reality docs:** none yet (see Provenance)

## Purpose & Intent

CI feedback should cost roughly what a change actually risks, not what the
full portfolio could possibly catch. As the portfolio grows, a full run
against every change becomes either too slow to keep in the ordinary
contributor loop (forcing the kind of collect-only/deferred-validation
special-casing this repo already carries for its heaviest plugin) or too
expensive to run as often as changes land. Coverage — which tests actually
execute which lines — is the evidence that lets a change's own CI run only
the tests genuinely implicated by what it touches, while still preserving a
trustworthy net for everything else.

Success looks like: a change's required CI cost scales with how much of the
portfolio its own diff plausibly implicates, not with the portfolio's total
size; the targeting evidence is itself a durable, versioned, trunk-derived
asset rather than a guess recomputed from scratch each time; and the system
never quietly under-tests when that evidence is missing, stale, or no longer
trustworthy — it falls back to a known-safe broader set instead. This is a
**targeting layer**, complementary to — not a replacement for — the tiered,
budgeted portfolio `test-portfolio` already governs, and the periodic full/
heavy validation a trunk pipeline (e.g. `ci-failure-remediation`'s subject)
already runs independent of any per-change targeting.

## Concepts & Components

### coverage baseline

A durable, versioned artifact recording which tests cover which source
lines/branches, established authoritatively wherever a repo's own trunk gate
already runs its full, trusted portfolio (a dev→main promotion's post-merge
full-suite validation, a scheduled full run, or an equivalent trunk-gate
event). The baseline is the one place selection evidence is *earned*, never
per-PR guesswork.

### diff-scoped selection

For a given change, the files/lines it touches are cross-referenced against
the current coverage baseline to derive the minimal test subset that
genuinely exercises the change — the CI-time counterpart of the baseline's
offline evidence.

### smoke fallback tier

A small, curated, always-safe test set that selection falls back to whenever
the coverage baseline cannot be trusted for a given change: absent, stale
past a bounded age, covering a file the baseline has no entry for, or the
accumulated volume of change since the baseline was last cut has grown large
enough that a diff-scoped subset is no longer a confident proxy for full
risk (**coverage debt saturation** — many PRs landing between one baseline
and the next). The fallback is a safety net, not a second-class citizen: it
is itself a deliberately curated, evidence-backed set, not an afterthought.

### baseline propagation

The coverage baseline generated at a trunk-gate event is only useful to
in-flight and future changes if it reaches the branch they are validated
against. A repo adopting this capability closes that loop explicitly —
feeding the freshly-earned baseline back into its ordinary contribution
branch — rather than leaving each promotion's evidence stranded on the trunk
side of the gate.

### coverage debt accounting

The gap between "when the baseline was last earned" and "how much has
changed since" is a first-class, observable quantity, not an implicit
assumption. It is what actually decides when diff-scoped selection remains
trustworthy versus when the smoke fallback should take over.

## Features

### baseline generation at the trunk gate

Wherever a repo's own trunk-validation event already runs its full, trusted
test portfolio, that run also durably records test-to-coverage attribution
as a new baseline generation — evidence earned once, reused broadly, never
recomputed per-PR from nothing.

### diff-scoped test selection

An ordinary change's CI derives its own targeted test subset from the
current baseline and its own diff, rather than choosing between "run
everything" and "run nothing but a fixed smoke tier" as the only two levers.

### graceful degradation, never silent under-testing

Selection degrades to the smoke fallback tier whenever the baseline is
missing, stale, or the accumulated coverage debt has crossed a bounded
threshold — visibly and deliberately, never by quietly narrowing coverage
past what the evidence can actually support.

### baseline feedback into the contribution branch

A newly-earned baseline is propagated back into whichever branch ordinary
changes are validated against, so the very next change already has fresh,
authoritative targeting evidence rather than working from a stale or absent
one.

### observable coverage debt

It is always evident, for any point in a repo's history, how fresh the
active baseline is and how much has changed since — the same signal that
governs the fallback decision is inspectable by a human or an agent
reasoning about why a given change's CI ran what it ran.

## Behaviors

### selection is additive to the portfolio's own tiers

Coverage-guided selection decides *which* tests from the existing, already
tiered and budgeted portfolio run for a given change — it does not introduce
a competing notion of which tests are worth keeping. `test-portfolio`'s own
contract map, tiering, and budgets remain the source of truth for what
exists; this capability only narrows *when* each already-justified test
runs.

### never quieter than the evidence supports

A change touching a file, module, or line the baseline cannot confidently
attribute falls back to the smoke tier (or broader) rather than being
silently excluded from every test. Absence of evidence is treated as
insufficient evidence, not as a green light.

### auditable per run

For any CI run that used coverage-guided selection, it is discoverable which
baseline generation it selected against and why (fresh diff-scoped subset,
or fallback and which trigger caused it).

### full/heavy validation remains independent

A trunk's own periodic or post-merge full-suite validation continues to run
on its own cadence regardless of what any individual change's targeted CI
selected — targeting narrows fast feedback; it never substitutes for the
deeper, full-portfolio pass that earns the next baseline.

### the propagation loop never silently breaks

If a trunk-gate event cannot feed its freshly-earned baseline back into the
contribution branch, that failure is visible (the same way a stuck
changefile-cleanup PR or a failed promotion already surfaces) rather than
leaving subsequent changes to silently regress to the smoke fallback without
anyone noticing why.

## Non-Goals / Boundaries

- **Not a coverage-percentage quality gate.** Coverage here is targeting
  evidence for *what to run*, never a proxy for a test's worth — that
  question remains `test-portfolio`'s, which explicitly disclaims
  line-coverage maximization as a goal.
- **Not a replacement for full/heavy validation.** Diff-scoped selection
  narrows a change's own fast feedback; the trunk's full-portfolio pass (and
  whatever remediates it when red) keeps running independent of any single
  change's own targeting.
- **Not a specific tool, format, or mechanism.** Whether a repo builds this
  on `coverage.py` dynamic contexts, `pytest-testmon`, or another approach;
  where the baseline artifact lives; and exactly how propagation is wired
  are implementation choices for the realizing effort, not this vision.
- **Not mandatory for every repo, and not the same mechanism everywhere.**
  A repo without a dev→main-style trunk gate earns its baseline from
  whatever full-validation event it already has (a scheduled run, a
  different trunk gate); the underlying concepts here (baseline, diff-scoped
  selection, smoke fallback, propagation, debt accounting) are meant to be
  portable across that variation, not bound to one repo's own pipeline
  shape.

## See Also

- Parent vision: none (top-level cross-cutting capability)
- Child visions: none (leaf)
- Sibling vision: [`test-portfolio`](../test-portfolio/README.md) — owns the
  portfolio's own contract map, tiering, and budgets that this capability
  selects *within*, never replaces
- Related vision: [`ci-failure-remediation`](../ci-failure-remediation/README.md)
  — the standing response when a trunk-gate run goes red, independent of
  whether that run was full or coverage-guided-selected
- Realization vehicle (pre-vision): the
  [`dev-branch-release-pipeline`](../../efforts/active/dev-branch-release-pipeline/README.md)
  effort's promotion gate is this repo's current trunk-validation event and
  the natural place to earn the coverage baseline and feed it back into
  `dev`
- Testing guide: [`TESTING.md`](../../TESTING.md)
- Current per-plugin runner:
  [`tools/run-plugin-tests.py`](../../tools/run-plugin-tests.py)

## Provenance

- **2026-09-28** — Authored from an operator conversation that began as a
  general question about Python code-coverage tooling, then converged on the
  idea live while this same session was tracing why `agent-worktrees`' own
  cluster-free-dispatch regression (#4353/#4378/#4379) sat undetected
  through PR review: that plugin's real test suite is deferred entirely to
  post-merge full-suite validation because it is too slow for the ordinary
  PR loop. The operator connected coverage collection specifically to the
  dev→main promotion's own full-validation run, proposed feeding the
  resulting baseline back into `dev`, and named the smoke-tier fallback for
  when accumulated change volume outpaces a fresh baseline — the whole
  should-be shape above is mined directly from that conversation, generalized
  to stay portable to a repo (aperture-labs was named explicitly) that has no
  dev→main promotion of its own.
