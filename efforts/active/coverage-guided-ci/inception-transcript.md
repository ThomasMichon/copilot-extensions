# Inception transcript — `coverage-guided-ci`

Full operator↔agent exchange that produced the
[`coverage-guided-ci`](../../../visions/coverage-guided-ci/README.md) vision
and this effort. Captured verbatim (operator turns) / paraphrased faithfully
(agent turns, where reproducing full tool output would dominate this file),
**except one deliberate redaction**: a private downstream repository name the
operator used in Round 3 is replaced with a public-safe placeholder
(`[a private downstream repo]`) — that placeholder is not the operator's
original wording, only a stand-in for it. See the vision's own Provenance
section for the condensed should-be summary. This file is historical record,
not the should-be contract — the vision and this effort's own Plan are that;
nothing here should be re-derived as a delta on its own.

## Round 1 — general question, not yet about this effort

**Operator:** "Does Python have the concept of Code Coverage collection,
especially for tests?"

**Agent:** explained `coverage.py` / `pytest-cov`, line vs. branch coverage,
`.coveragerc`/`pyproject.toml` config, threshold enforcement
(`--fail-under`), multi-process combining (`coverage combine`), and IDE/CI
integrations (Codecov, Coveralls). Purely informational — no design proposed
yet.

## Round 2 — the idea is floated

**Operator:** "I'll keep that in mind. It would be awesome to somehow write a
CI flow which prioritizes tests based on their unique Code Coverage signals,
so we could squeeze appropriate, targeted subsets into CI flows."

**Agent:** named real prior art — `pytest-testmon` (records which lines each
test touched via `coverage.py`, re-executes only tests whose covered lines
changed), `coverage.py` dynamic contexts (`--dynamic-context=test_function`,
a queryable test→line map), `diff-cover` (the inverse: which changed lines
lack any covering test), and historical Google/Facebook-style test-impact
analysis (TIA). Mapped it onto this repo's own concrete gap — at that point
in the session, the agent had just traced `agent-worktrees`' own
cluster-free-dispatch regression (#4353/#4378/#4379) to `ci.yml`'s
`worktrees-smoke` job, which only `--collect-only`s that plugin's suite on
an ordinary PR (real execution deferred entirely to post-merge full-suite
validation) because the suite is too slow for the per-PR loop — and proposed
a `tools/run-plugin-tests.py --impacted` mode built on dynamic contexts,
offering to prototype it.

## Round 3 — the should-be shape, and "build a vision"

**Operator:** "It would make sense to run the coverage collection on
dev-to-main promotions, and then leverage the generated asset during CI
builds past that baseline. We can can then keep the 'smoke test set' as a
backup set in the case of over-saturation of accumulated coverage debt (lots
of PRs going in before a new baseline can establish). We'll need a way to
feed back the result of the dev-to-main promo into dev, so we have complete
coverage info when needed. It's worth building out a vision for this, as
part of copilot-extensions' self-maintaining system. It's *also* a great
idea to consider adopting the same pattern for [a private downstream repo]
with *our* CI flow, though we don't use a dev-to-main promotion."

This is the operator's own words for the should-be shape, essentially
unedited into the vision:
- coverage collection anchored to the dev→main promotion's own full-suite
  validation run (not a separate, ad hoc collection pass);
- the resulting baseline is a durable **asset**, reused by later CI runs
  rather than recomputed;
- the existing smoke-test tier is repurposed as an explicit **fallback**,
  triggered by **coverage-debt saturation** (the operator's own term) — many
  PRs landing before a fresh baseline can be cut;
- an explicit **feedback loop**, so the baseline reaches `dev` and is
  available "when needed" (i.e., to the very next PR, not just retroactively
  on the trunk side);
- this belongs as a **vision** — standing intent for copilot-extensions'
  "self-maintaining system" — not a one-off tool;
- **a second, private downstream repo** is named as a later candidate,
  explicitly flagged as architecturally different (no dev-to-main promotion
  of its own), so whatever gets built must not assume that pipeline shape.

The agent authored `visions/coverage-guided-ci/README.md` from this
directly (PR #4440, merged), cross-linked from `test-portfolio` and
`ci-failure-remediation`, with `dev-branch-release-pipeline` named as the
current realization vehicle for baseline generation — without committing
that effort to a new phase.

## Round 4 — the dev-vs-main / vendoring refinement

**Operator:** "It occurred to me that if we're careful, we could durably
check in the coverage data *to main* at the end of the promo-to-main build,
as we'll already be making a checkin to main at the end of the flow, and
marking the baseline info. This would mean that `dev` doesn't have that data
in the branch, so our test flows would need to grab it from the version of
main associated with the locally-checked-out-dev baseline. Doable, but
something which needs targeted tooling. We'd also have to account for
vendoring, so if we took care to run most validation tests on
pre-vendored code (assuming our vendor-pointer system works at runtime in
dev as we expect), then the source tracking would be accurate to `dev`. The
diff of commits on top of a dev baseline would directly correlate to the
tracked coverage lines, making it 'easy' to determine which tests are
impacted by a change on top of dev. And, of course, if churn gets too high,
rather than risking running 'the full suite' on every PR, we have a
'short-circuit', which we can tune."

This is the operator's own architectural insight, folded into the vision
(PR #4444, merged, across 4 automated review rounds) as should-be — not
picking `dev` vs. `main` as a mechanism, but generalizing what both options
actually require to be correct:
- **baseline correlation, not branch-resident propagation** — the artifact
  need only be reliably locatable and tied to the exact commit it was
  measured against, wherever it actually lives;
- **attribution stays true to the branch under diff** — a trunk-gate's own
  downstream transform (this repo's own vendor-pointer materialization from
  `dev`'s DRY form to `main`'s expanded form is the concrete example the
  operator gave) must never be what coverage is measured against; only the
  same source form a diff is computed on;
- **the coverage-debt threshold is a tunable dial** ("the short-circuit,
  which we can tune") — an explicit, adjustable bound, not a hardcoded
  constant.

Automated PR review on #4444 then caught two real logic gaps the operator's
own "diff of commits on top of a dev baseline would directly correlate"
framing implied but the first draft's wording didn't yet state precisely:
baselines are earned *periodically* (per the operator's own coverage-debt
framing), so an ordinary fork rarely lands on the exact baselined commit —
selection must resolve the **newest ancestor baseline** and carry
intervening commits as debt, never require an exact match; and ancestry
alone doesn't make old line numbers trustworthy — intervening commits that
insert/delete lines shift coordinates independent of whether the debt
threshold has tripped, so selection must **remap or invalidate** attribution
across those commits, not just resolve which baseline generation to use.
Both were folded into the vision as Behaviors; both apply directly to
whatever concrete correlation mechanism (`dev`-resident or `main`-published)
this effort ultimately builds.

## Round 5 — carve the effort

**Operator:** "I guess since you have all the context, write an effort to
drive this vision, and include my suggestions and thoughts in the
'inception' sidecar ref. Then, provide me a small prompt I can pass to a new
worktree to get started."

Produced this effort (`efforts/active/coverage-guided-ci/README.md`) and
this transcript file. See the effort README's own Request section for the
condensed capture, and its Plan for how the above should-be shape breaks
into phased, reviewable work.
