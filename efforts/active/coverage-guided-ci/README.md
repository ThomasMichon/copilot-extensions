# Coverage-Guided CI Test Selection

- **Slug:** `coverage-guided-ci`
- **Repo:** copilot-extensions
- **Branch(es):** reviewed plan PR, then serial per-phase PRs off `dev`
- **Created:** 2026-09-28
- **Status:** Draft
- **Vision:** realizes [`visions/coverage-guided-ci`](../../../visions/coverage-guided-ci/README.md)
  in full — this effort exists to close that vision's whole vision-vs-reality
  delta, phase by phase; complements
  [`visions/test-portfolio`](../../../visions/test-portfolio/README.md) and
  [`visions/ci-failure-remediation`](../../../visions/ci-failure-remediation/README.md)
- **Umbrella issue:** #4453
- **Sub-issues:** pending Phase 1 decomposition

## Guiding Intent

Give ordinary CI a third option between "collect-only" and "the full,
multi-minute suite": run only the tests a change's own diff plausibly
implicates, evidenced by a durable coverage baseline earned once at the
repo's own trunk-validation gate (the dev→main promotion's post-merge
full-suite run) rather than guessed at per-PR. Fall back to a curated smoke
tier whenever that evidence is missing, stale, or coverage debt has
saturated it. Never let the targeting silently under-test.

`agent-worktrees` is the concrete, motivating case: its real suite runs
`--collect-only` on an ordinary PR and only executes for real post-merge,
which is exactly how a real regression (#4353/#4378/#4379, fixed in #4429)
sat undetected through review. This effort's first realized slice should
replace that plugin's collect-only tier with genuine diff-scoped selection.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Primary maintainer | Owns the plan, the umbrella issue, and phase sequencing decisions | isolated local worktrees |
| Review agents | Review the plan PR and each phase's implementation for silent under-testing or attribution drift | repository PR review |

## Coordination

- **Topology:** one reviewed plan PR (this README + inception transcript),
  followed by serial per-phase PRs off `dev`.
- **Host (owns PRs):** Primary maintainer.
- **Delegates:** none yet; phases are sized for one participant at a time
  until Phase 1 scopes out real parallelizable slices.
- **Handoff:** each phase's PR merges to `dev` before the next phase begins;
  no phase depends on an unmerged predecessor's uncommitted state.

## Context

Full inception exchange (the operator's own coverage-tooling question, the
CI-prioritization idea, the promotion-anchored should-be shape, the
dev-vs-main/vendoring refinement, and the review-driven corrections that
followed): [`inception-transcript.md`](inception-transcript.md).

Directly caused by, and validated against, the live investigation that fixed
`agent-worktrees`' `_CLUSTER_FREE_MODULES` drift (#4353/#4378/#4379, PR
#4429) — see that PR and `visions/coverage-guided-ci/README.md`'s own
Provenance section for the concrete symptom this effort exists to prevent
recurring: a plugin whose real suite is deferred to post-merge validation
gets zero real per-PR execution today.

Related, pre-existing mechanisms this effort builds on rather than
duplicates:
- `.github/workflows/validate-and-promote.yml`'s `full`/`worktree-manager`
  jobs already run the full, trusted suite at the promotion gate — the
  natural place to add coverage instrumentation (Phase 1).
- `tools/run-plugin-tests.py` already has `--guards`/`--collect-only`/`-k`
  selection tiers; this effort adds a new tier alongside them rather than
  replacing the mechanism.
- `promote_release.py`'s vendor-pointer materialization (DRY on `dev`,
  expanded on `main`) is the concrete transform the vision's
  attribution-correctness Behavior generalizes past.

## Request

The operator's own words, condensed (see `inception-transcript.md` for the
full, verbatim exchange):

> Write a CI flow that prioritizes tests using their own code-coverage
> signals so targeted subsets, not the full suite, run per change. Anchor
> coverage collection to the dev-to-main promotion's own full-suite
> validation, treat the resulting baseline as a durable, reused asset, and
> keep the existing smoke-test tier as an explicit fallback for coverage-debt
> saturation (too many PRs landing before a fresh baseline). Build a
> feedback loop so the baseline reaches `dev`. This is a standing capability
> ("part of copilot-extensions' self-maintaining system"), not a one-off
> tool — author a vision for it. ~~Separately~~, consider the same pattern
> for another repo later, one with no dev-to-main promotion of its own.
>
> Follow-up: rather than feeding the baseline back into `dev`, it may be more
> natural to check it into `main` at the same commit the promotion already
> writes there — but `main`'s tree is vendor-materialized/expanded from
> `dev`'s DRY form, so attribution must be measured pre-transform (against
> `dev`'s own source layout) for line-level correlation against a PR's diff
> to stay valid. If churn between baselines gets too high, short-circuit to
> the smoke set instead of risking a full-suite run on every PR — and that
> short-circuit threshold should be tunable.
>
> Now: write an effort to drive the vision, capture the above in an
> inception sidecar, and hand back a short prompt to kick off a fresh
> worktree.

_(agent-recommended)_ Everything below this point — the phase breakdown,
sequencing, and validation plan — is the agent's own structuring of how to
realize the above; the operator did not specify phases or an implementation
order.

## Plan

### Phase 0 — Design spike: coverage mechanism + storage format
- [ ] Pick the concrete coverage-collection mechanism (`coverage.py` dynamic
      contexts is the leading candidate named during inception; confirm it
      scales to this repo's per-plugin `.test-venvs` isolation model).
- [ ] Decide the baseline artifact's storage location and correlation
      mechanism — `dev`-resident vs. `main`-published-and-pointer-linked
      (the vision deliberately left this open; this effort makes the call).
      Must satisfy the vision's attribution-correctness Behavior: measured
      against `dev`'s own pre-vendor-materialization source form.
- [ ] Spike coverage collection inside `validate-and-promote.yml`'s existing
      `full`/`worktree-manager` jobs (no new job; instrument the existing
      one) and confirm the artifact it produces round-trips through the
      chosen storage/correlation mechanism.

### Phase 1 — Baseline generation + correlation at the promotion gate
- [ ] Instrument the promotion gate's full-suite run to emit a durable,
      versioned baseline (test → covered lines/branches).
- [ ] Publish baselines **atomically**: since `validate-and-promote.yml` runs
      per-plugin suites as separate matrix jobs (plus `worktree-manager`
      separately), never persist per-job coverage results directly as a
      selectable generation. Aggregate first, and create the generation only
      after every required validation result for the pinned commit has
      succeeded — the same all-required-jobs-green condition the `promote`
      job itself already gates on — so a job failure/cancellation can never
      leave a partial, silently-incomplete baseline live for selection.
- [ ] Implement baseline correlation: durably tie the artifact to the exact
      `dev` commit it was measured against, resolvable later by commit
      ancestry.
- [ ] Implement the "correlation loop never silently breaks" Behavior:
      surface a visible failure (not a silent gap) if a promotion run
      cannot record/correlate its baseline.

### Phase 2 — Nearest-ancestor resolution + attribution remap/invalidate
- [ ] Given an arbitrary fork-point commit, resolve the newest baseline
      whose measured commit is an ancestor, per the vision's Feature.
- [ ] Implement remap-or-invalidate: for files touched by commits between
      the resolved baseline and the fork point, either translate line-level
      attribution through those commits' own diffs, or mark the file's
      attribution invalid (forcing it through the smoke fallback for that
      file specifically).
- [ ] Unit-test this against a constructed history with real intervening
      line insertions/deletions, not just a same-content forward-move case.

### Phase 3 — Diff-scoped selection + coverage-debt / smoke fallback
- [ ] Build the diff-scoped selector: PR diff + resolved baseline (with
      Phase 2's remap/invalidate applied) → targeted test subset.
- [ ] Implement coverage-debt accounting (age and/or commit-volume since the
      resolved baseline) with a tunable threshold, per the vision's own
      Behavior.
- [ ] **Curate and validate the fallback set itself**, not just its trigger
      conditions: today `worktrees-smoke` only has `--collect-only` (no real
      execution) plus the always-run structural `--guards` step, neither of
      which is a genuine "run a safe, evidence-backed subset" fallback the
      vision requires. Define what the smoke tier actually *executes* when
      triggered, and validate that curated set carries real assurance (per
      `test-portfolio`'s own evidence-bearing-family bar), not just that it
      exists.
- [ ] Wire the smoke-fallback trigger: missing baseline, stale baseline,
      unresolvable/invalidated attribution for a touched file, a changed
      line or module with **no attribution even where its own file has
      other baseline entries** (the common new-code/previously-uncovered-code
      case — a partially-attributed file is not the same as a fully-covered
      one), or debt past threshold — any one trips the fallback for the
      affected scope, never a silently smaller subset.
- [ ] Make the selection auditable: which baseline generation was used, and
      why (fresh subset vs. fallback + trigger), discoverable per CI run.

### Phase 4 — Rollout: replace `agent-worktrees`' collect-only tier
- [ ] Wire `ci.yml`'s `worktrees-smoke` job to use diff-scoped selection
      (Phases 1-3's output) instead of `--collect-only`, keeping the
      existing `--guards` real-execution step alongside it.
- [ ] Validate against a **genuinely runtime-covered** regression, not
      #4353/#4378/#4379: that regression's own detecting test
      (`test_cluster_free_modules_matches_regenerated_scan`) reads each CLI
      module as text and parses its AST rather than executing the changed
      lines, so ordinary line/branch coverage can never create a
      test→changed-file attribution edge for it — the `--guards` step
      already catches that specific class independently of selection
      (real, but not proof selection works). Construct or pick a change
      whose regression is caught by a test that actually *executes* the
      changed lines, and confirm diff-scoped selection selects that test
      and fails, not silently passes an incomplete subset. Text/AST-scanning
      guard tests (this drift-check pair included) remain validated by the
      `--guards` tier, not by coverage-guided selection, until/unless a
      later phase adds non-runtime (static-analysis) dependency edges.
- [ ] Measure and record real wall-clock PR-CI impact for `agent-worktrees`
      changes (before/after).

### Phase 5 — Generalize beyond `agent-worktrees`
- [ ] Assess whether other plugins would benefit from diff-scoped selection
      over their current smoke/full split (no plugin besides
      `agent-worktrees` is currently collect-only-gated, so this phase is
      about incremental adoption where it adds real value, not a mandate).
- [ ] Revisit `dev-branch-release-pipeline`'s own effort README once this
      phase lands — it may be ready to formally adopt this effort's Phase 1
      output as one of its own promotion-gate responsibilities.

_(agent-recommended, out of this effort's scope, tracked for later)_
Adopting the same coverage-guided pattern for another repo's own CI flow is
explicitly a separate, later effort per the operator's own request — a repo
without this repo's own dev-to-main promotion needs its own
baseline-generation anchor point (a scheduled run, or a different trunk
gate), designed against this vision's portable concepts, not a copy of this
effort's
copilot-extensions-specific Phase 1.

## Validation Plan

- [ ] Phase 1: a promotion run genuinely produces a baseline correlated to
      its own commit; a deliberately-broken correlation step is visibly
      surfaced, not silently swallowed; and a constructed run where one
      required matrix job (a per-plugin `full -` job, or
      `worktree-manager (out-of-plugin, full)`) fails or is cancelled
      produces **no** selectable generation at all — proving the atomic-
      publication gate, not just asserting it exists.
- [ ] Phase 2: a constructed multi-commit history (baseline → several
      line-shifting commits → fork point) resolves to the correct nearest
      ancestor and correctly remaps/invalidates attribution — verified
      against a hand-computed expected result, not just "no exception."
- [ ] Phase 3: a change touching only files with valid, resolvable
      attribution selects a strict subset of the full suite; a change
      touching a file with no baseline entry at all, a change touching a
      **partially-attributed file** (some lines/modules covered, the
      touched ones not), or a change crossing the debt threshold each falls
      back to the smoke tier — all three paths verified by test, and all
      three are auditable after the fact. The fallback tier itself
      genuinely executes its curated set (not collect-only) and that set's
      own assurance is evidenced, not assumed.
- [ ] Phase 4: construct a change whose regression is caught by a
      genuinely runtime-executed test (not #4353/#4378/#4379's text/AST
      scanner — see that phase's own note on why it can't prove selection)
      and confirm diff-scoped selection selects that test and fails, rather
      than silently passing an incomplete subset; separately confirm real
      `worktrees-smoke` PR-CI wall-clock time versus the prior collect-only
      baseline and the full-suite baseline.
- [ ] No phase regresses `test-portfolio`'s own host-safety or budget
      guarantees — the selector is an additional CLI mode, not a rewrite of
      how any existing test tier runs.

## Proposal

_Pending review of this plan._

## Journal

### 2026-09-28 — Kickoff
- Effort created from a five-round operator/agent conversation (see
  `inception-transcript.md`): a general coverage-tooling question, the
  CI-prioritization idea, the `coverage-guided-ci` vision (PR #4440), a
  dev-vs-main/vendoring refinement folded into the vision (PR #4444, 4
  review rounds), and this effort to drive it.
- Filed umbrella issue #4453.
