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
Curation is itself coverage-evidenced, not hand-picked: the same baseline
that drives diff-scoped selection also names, per test, which lines/branches
it covers and how long it costs to run, which makes choosing the fallback
set an explicit **optimization**, not a guess — see coverage-efficient
fallback curation below.

### coverage-efficient fallback curation

Given the baseline's own per-test coverage and cost data, the smoke
fallback's membership is chosen to maximize the source coverage it carries
per unit of total runtime it costs — the baseline already has everything
needed to compute this (which lines/branches each test covers, how long
each test takes) without any additional instrumentation. This is a
**budget-bounded** choice, not a fixed-size one: the set is built by
repeatedly adding whichever remaining test is cheapest per unit of
*still-uncovered* baseline coverage, stopping as soon as either the
coverage universe is fully covered **or** no remaining candidate both adds
new coverage and still fits the leftover budget — whichever comes first.
The budget is never treated as a quota to be spent in full: a test that
adds no new coverage is never added merely because budget remains, and a
test that would exceed the remaining budget is never force-added merely to
guarantee a non-empty result. This is a classic weighted-set-cover-style
greedy selection, not an exhaustive optimum, since the general problem is
NP-hard and an approximate, auditable, periodically-recomputed set is the
right shape for a safety net that must stay cheap to recompute as the
baseline itself evolves. A repo may re-run the curation whenever its
baseline changes meaningfully, rather than hand-maintaining the fallback
set as a static list.

### baseline correlation

The coverage baseline generated at a trunk-gate event is only useful to
in-flight and future changes if it can be reliably **located and correlated**
to the exact point in the contribution branch's history it was earned
against — whether that means the baseline itself lands *in* the contribution
branch, or is durably published elsewhere (e.g. alongside whatever
trunk-side artifact the gate already produces) with a precise pointer back
to its originating commit. A repo adopting this capability closes that loop
explicitly, rather than leaving each promotion's evidence stranded or
ambiguously attributed.

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

### baseline reachable from any fork point

A newly-earned baseline is durably associated with the exact contribution-
branch commit it was measured against. Because baselines are earned
periodically rather than on every commit, a subsequent change rarely forks
from that exact commit — it resolves instead to the **newest baseline whose
measured commit is an ancestor of its own fork point**, whether the artifact
lives on that branch directly or is published and pointer-linked from
elsewhere, with every commit between that baseline and the fork point
counted as coverage debt the selection has to carry (see coverage debt
accounting). A fork point with no ancestor baseline at all is the absent
case the smoke fallback already covers.

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

### the fallback set is recomputed, not hand-maintained

The smoke tier's membership is a derived artifact of the current baseline
(see coverage-efficient fallback curation), not a list a maintainer edits by
hand as the suite grows. It is recomputed whenever the baseline is, so it
stays an honest, currently-cheapest-per-coverage set rather than drifting
stale the way a hand-picked list silently would.

### full/heavy validation remains independent

A trunk's own periodic or post-merge full-suite validation continues to run
on its own cadence regardless of what any individual change's targeted CI
selected — targeting narrows fast feedback; it never substitutes for the
deeper, full-portfolio pass that earns the next baseline.

### attribution stays true to the branch under diff

A trunk-gate event may apply its own downstream transforms when producing
its published artifacts (materializing vendored dependencies, expanding a
DRY pointer into a full copy, or similar) — but the coverage measurement
this capability relies on is taken against the **same source form ordinary
changes are diffed against**, never a transformed/materialized copy. Where a
repo's trunk-gate output and its contribution branch's own source layout
diverge, the baseline is earned pre-transform, or the divergence is
reconciled before attribution is trusted — otherwise line-level correlation
between a change's diff and the baseline silently corrupts.

### attribution survives the commits between baseline and fork point

Resolving to the nearest ancestor baseline (see baseline reachable from any
fork point) is necessary but not sufficient: any intervening commit that
inserts or deletes lines shifts where a previously-covered line now sits,
independent of whether coverage debt has crossed its threshold. Selection
either **remaps** the baseline's attribution through the intervening commits
to the fork point's own coordinates, or **invalidates** attribution for the
specific files those commits touched (falling those files through to the
smoke fallback) — ancestry alone, without one of these, is not sufficient to
trust a baseline's line numbers against a later fork point.

### coverage debt's threshold is a tunable dial

The bound past which coverage debt forces the smoke fallback (whether framed
as elapsed time, commit count, or diff volume since the baseline) is an
adjustable setting a repo tunes for its own churn rate, not a hardcoded
constant — a repo under heavy concurrent-PR churn can tighten it; a quieter
one can loosen it, without changing this capability's own shape.

### the correlation loop never silently breaks

If a trunk-gate event cannot durably record and correlate its freshly-earned
baseline, that failure is visible (the same way a stuck changefile-cleanup
PR or a failed promotion already surfaces) rather than leaving subsequent
changes to silently regress to the smoke fallback without anyone noticing
why.

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
  where the baseline artifact lives; and exactly how correlation is wired
  are implementation choices for the realizing effort, not this vision.
- **Not mandatory for every repo, and not the same mechanism everywhere.**
  A repo without a dev→main-style trunk gate earns its baseline from
  whatever full-validation event it already has (a scheduled run, a
  different trunk gate); the underlying concepts here (baseline, diff-scoped
  selection, smoke fallback, correlation, debt accounting) are meant to be
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
  the natural place to earn the coverage baseline and durably correlate it
  back to the `dev` commit it was measured against
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
  to stay portable to a repo (a private downstream repo was named
  explicitly) that has no dev→main promotion of its own.
- **2026-09-28 (same day, follow-up)** — The operator observed that this
  repo's own promotion already writes a commit to `main`, so it may be more
  natural to check the baseline in *there* rather than back into `dev` — but
  `main`'s tree is a materialized/vendor-expanded copy of `dev`'s DRY
  pointer-sourced tree, so a baseline earned against `main`'s post-transform
  layout would misattribute line numbers when correlated against a diff
  computed on `dev`'s own source form. Generalized this into two should-be
  refinements rather than picking `dev` vs. `main` (a mechanism choice the
  vision deliberately leaves open): baseline *correlation* need not mean the
  artifact lives on the contribution branch, only that it is reliably
  locatable and tied to the exact commit it was earned against; and
  attribution must be measured against the same source form a diff is
  computed against, never a downstream-transformed copy, generalizing past
  vendoring to any repo-specific trunk-gate transform. Also folded in the
  operator's point that the coverage-debt threshold should be a tunable
  dial, not a fixed constant.
- **2026-10-01** — The operator, while sponsoring a downstream consumer
  repository's own low-risk spike proving the diff-scoped-selection
  primitive works with off-the-shelf `coverage.py` dynamic contexts, asked
  that the smoke fallback's own membership ideally be "the set of tests
  which together provide the best coverage set for the smallest amount of
  total runtime" — i.e. an explicit optimization over the same baseline
  data already in hand, rather than a hand-curated list. Folded in as
  **coverage-efficient fallback curation** (a greedy, budget-bounded
  weighted-set-cover-style selection) and **the fallback set is recomputed,
  not hand-maintained** — generalizing Phase 3's existing "curate and
  validate the fallback set itself" task into a concrete selection
  criterion rather than leaving curation unspecified.
