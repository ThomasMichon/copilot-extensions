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
- [x] Pick the concrete coverage-collection mechanism (`coverage.py` dynamic
      contexts is the leading candidate named during inception; confirm it
      scales to this repo's per-plugin `.test-venvs` isolation model).
      **Decided 2026-10-01** (Phase 0 pilot, PR #4807): `coverage.py`
      dynamic contexts, via an ephemeral `uv run --with coverage --with
      pytest-cov --with pytest-json-report` ephemeral ancillary venv (not
      `.test-venvs` directly — that cache has no coverage-context
      pass-through yet; wiring it into the cache is Phase 1 scope).
- [x] Decide the baseline artifact's storage location and correlation
      mechanism — `dev`-resident vs. `main`-published-and-pointer-linked
      (the vision deliberately left this open; this effort makes the call).
      Must satisfy the vision's attribution-correctness Behavior: measured
      against `dev`'s own pre-vendor-materialization source form.
      **Decided 2026-10-01:** the baseline is checked into `main`,
      piggybacking on the promotion pipeline's own existing
      commit-per-promotion + `promote-<timestamp>-<sha>` tag — this repo
      already has a trusted, audited correlation mechanism
      (`.github/release-pipeline-state.json`'s `last_promotion.dev_head`,
      recording the exact `dev` commit each `main` promotion was measured
      against) rather than needing to invent a GitHub-Release-asset path
      with no existing analog in this pipeline. Attribution correctness is
      unaffected either way: measurement happens against `dev`'s own source
      form regardless of which branch later stores the resulting JSON. See
      this entry's own Journal note for the fuller rationale and the
      rejected alternative (a GitHub Release asset tied to the same tag).
- [ ] Spike coverage collection inside `validate-and-promote.yml`'s existing
      `full`/`worktree-manager` jobs (no new job; instrument the existing
      one) and confirm the artifact it produces round-trips through the
      chosen storage/correlation mechanism.

### Phase 1 — Baseline generation + correlation at the promotion gate
- [x] Instrument the promotion gate's full-suite run to emit a durable,
      versioned baseline (test → covered lines/branches).
      **Wired 2026-10-02** for one pilot plugin (`agent-ssh`, PR TBD): the
      `full` matrix job's own `agent-ssh` leg now also runs
      `coverage_guided_selection.baseline`'s `--project-dir`-aware
      collection (needed to resolve `agent-ssh`'s own vendored
      dependencies, unlike the dependency-free `ai-attribution` Phase 0
      pilot) and uploads the resulting baseline as a build artifact.
      Expanding to the remaining 8 plugins in this matrix is the next
      increment, not yet done.
- [x] Publish baselines **atomically**: since `validate-and-promote.yml` runs
      per-plugin suites as separate matrix jobs (plus `worktree-manager`
      separately), never persist per-job coverage results directly as a
      selectable generation. Aggregate first, and create the generation only
      after every required validation result for the pinned commit has
      succeeded — the same all-required-jobs-green condition the `promote`
      job itself already gates on — so a job failure/cancellation can never
      leave a partial, silently-incomplete baseline live for selection.
      **Satisfied by construction:** the per-job artifact is never itself
      "the baseline" -- only `promote_release.py`'s own commit (which only
      ever runs once `gate`+`full`+`worktree-manager`+`guards-full-sweep`
      have all succeeded) actually checks a baseline into `main`'s tree.
- [x] Implement baseline correlation: durably tie the artifact to the exact
      `dev` commit it was measured against, resolvable later by commit
      ancestry. **Done** via the `measured_commit` field (Phase 0's own
      storage/correlation decision) plus ordinary git ancestry on `main`
      once checked in.
- [x] Implement the "correlation loop never silently breaks" Behavior:
      surface a visible failure (not a silent gap) if a promotion run
      cannot record/correlate its baseline. **Done:**
      `promote_release.py`'s own `measured_commit` consistency check fails
      the promotion outright (not silently) if a downloaded baseline
      artifact doesn't match the exact `dev` commit this run is promoting
      -- a stale/mis-targeted artifact can never be silently checked in.

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
      vision requires. Curate it per the vision's **coverage-efficient
      fallback curation** Concept: a greedy, budget-bounded weighted-set-cover
      selection over the baseline's own per-test coverage + runtime-cost
      data (repeatedly add whichever remaining test is cheapest per unit of
      still-uncovered baseline coverage, stopping as soon as either the
      coverage universe is fully covered or no remaining candidate both adds
      new coverage and still fits the leftover budget, tunable per repo) —
      not a hand-picked list, and never padded out to spend the whole budget
      once saturated — and recompute it whenever the baseline changes
      meaningfully. Validate that curated set carries real assurance
      (per `test-portfolio`'s own evidence-bearing-family bar), not just that
      it exists.
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

### 2026-10-02 — Phase 1 pilot: real promotion-gate wiring for `agent-ssh`
Operator asked to actually get a baseline committed to `main`, "so we can
incrementally work towards full coverage across all plugins" -- driving
Phase 1 for real, one plugin at a time, same pilot-first pattern as
Phase 0.

**Picked `agent-ssh`** (16 test files, smallest in `validate-and-promote.yml`'s
own `full` matrix) deliberately *not* because it's dependency-free like the
Phase 0 `ai-attribution` pilot, but because it *does* have real vendored
path dependencies (`agent-ssh-manager`, `agent-procutil`, `agent-zdd`,
`agent-dropin-registry` via its own `[tool.uv.sources]`) -- proving the
general case a bare ephemeral `uv run --with` venv (Phase 0's own approach)
cannot handle, not just the easy one.

**`tools/coverage_guided_selection/baseline.py`:** added an optional
`project_dir` parameter. When given, `collect_baseline` builds a real
ephemeral venv via `uv venv` + `uv pip install -e .[dev]` (mirroring
`tools/run-plugin-tests.py`'s own `_ensure_venv` cached-venv pattern, cwd'd
to the plugin's own root so its vendored `[tool.uv.sources]` resolve) before
adding the coverage-collection extras -- validated directly against
`agent-ssh`'s real suite (187 collected test cases across 16 files, 10
source files attributed) via a new, real integration test
(`test_collect_baseline_with_project_dir_resolves_real_plugin_dependencies`).

**`tools/promote_release.py`:** added `_write_coverage_baselines_into_scratch`
+ a `coverage_baselines_dir` parameter/`--coverage-baselines-dir` CLI flag.
Each `*.json` baseline is checked into the generated commit's own tree at
`COVERAGE_BASELINES_DIR` (`.github/coverage-baselines/`), but only after
verifying its own `measured_commit` matches this promotion's exact `dev_head`
-- a mismatch raises `PromotionError` outright (the "correlation loop never
silently breaks" Behavior), never a silent skip. `_tree_excluding_state`
(the existing no-op-promotion content comparison) now also excludes
`COVERAGE_BASELINES_DIR`, alongside the pre-existing pipeline-state
exclusion, so a freshly re-collected baseline with no real `dev` content
change never forces a vacuous promotion. 4 new tests (27 total, all
passing): a matching baseline checks in correctly, a mismatched
`measured_commit` is refused, omitting `coverage_baselines_dir` entirely
stays fully valid (today's real default, before any plugin is wired), and
a baseline-only "change" is correctly a no-op.

**`validate-and-promote.yml`:** the `full` matrix job's `agent-ssh` leg
(`if: matrix.plugin == 'agent-ssh'`) now also runs the above collection and
uploads the result as a build artifact (`continue-on-error: true` -- a
baseline is optional evidence, never a gate on the trusted
`run-plugin-tests.py` run alongside it). The `promote` job downloads any
`coverage-baseline-*` artifacts (also best-effort: no artifact at all,
e.g. before this plugin's own collection step ran or succeeded, is just the
"no baselines this run" case `_write_coverage_baselines_into_scratch`
already handles) and passes the directory to both
`promote_release.py` invocations (dry-run and `--push`).

**Verified directly**, not just by code reading: the exact CLI invocation
the new `full` matrix step runs
(`python tools/coverage_guided_selection/baseline.py plugins/agent-ssh/tests
--cov-source plugins/agent-ssh/src/agent_ssh --plugin agent-ssh
--project-dir plugins/agent-ssh --measured-commit <sha> --out <path>`)
produces a real baseline (187 tests, 10 covered files, correct
`measured_commit`) when run directly in this checkout.

**Not yet done** (left for the next increment): watching this actually run
and land for real through a live `validate-and-promote.yml` run (this PR's
own validation is direct/local only, since triggering the real promotion
pipeline isn't something a PR branch can do); then expanding the plugin
list in both the `full` job's `if:` condition and this Journal beyond
`agent-ssh`, one plugin (or a small batch) at a time, toward "full coverage
across all plugins" per the operator's own framing.

### 2026-10-01 — Phase 0 storage/correlation decision: check into `main`
Resolving this Phase 0 Plan item's own open question, per the operator's
proposal (either check the baseline into `main` as part of the promotion
push, tied to the `dev` commit it was measured against, or attach it as a
release artifact associated with that commit).

Investigated this repo's actual promotion mechanics
(`.github/workflows/validate-and-promote.yml`, `tools/promote_release.py`)
before deciding, rather than picking abstractly:
- Every `dev`→`main` promotion already creates a new `main` commit, pushes
  an annotated tag (`promote-<timestamp>-<main-sha-prefix>`) on it, **and**
  writes `.github/release-pipeline-state.json` on `main` recording
  `last_promotion.dev_head` -- the exact `dev` commit that promotion was
  measured/built against. This correlation mechanism already exists,
  already ships, and is already trusted by the rest of the pipeline (it's
  what `promote_release.py`'s own monotonic-promotion guard reads).
- There is **no existing GitHub Release object** anywhere in this
  pipeline -- only git tags. A release-artifact path would mean building
  an entirely new correlation mechanism (tag -> release -> asset, a new
  `gh release create` call, new token scope, a new fetch path for the
  selector) that duplicates what the commit + tag + state file already do,
  for no correctness benefit: attribution correctness is unaffected either
  way, since the coverage *measurement* happens against `dev`'s own source
  form in the `full`/`worktree-manager` jobs regardless of which branch
  later stores the resulting JSON.

**Decision:** check each baseline generation into `main`'s own tree (e.g.
`.github/coverage-baselines/<plugin>.json`, alongside the existing
`release-pipeline-state.json`), piggybacking on the commit + tag the
promotion pipeline already creates per run. Embed the measured `dev_head`
directly in the baseline JSON itself (redundant with, but independently
self-describing from, the pipeline state file's own `last_promotion.dev_head`)
so a baseline file is correlatable on its own without cross-referencing a
second file. This is "durably published elsewhere" per the vision's own
`baseline correlation` Concept (relative to `dev`, the contribution
branch), not "resident on the contribution branch" -- a vision-compliant
choice the vision itself deliberately left open for this effort to make.

Not yet done (left for the next increment, per Phase 0's own remaining
checklist item): actually wiring this into `validate-and-promote.yml`'s
real jobs and confirming the round-trip against a real promotion run.

### 2026-10-01 — Phase 0 kickoff: pilot-first sequencing + fallback-curation refinement
- Operator (via a downstream consumer repository's session that had just
  proven the diff-scoped-selection primitive with a low-risk `coverage.py`
  spike against one of *that* repo's small plugins) asked to drive this
  effort for real,
  incrementally, "one project or sub-component at a time, until we know it
  works effectively" — rather than Phase 1's current framing of
  instrumenting `validate-and-promote.yml`'s full-repo promotion gate in one
  shot. Adopting a **pilot-first** reading of Phase 0: prove the mechanism
  (coverage collection -> baseline -> diff-scoped selection -> curated
  fallback) end-to-end against one small, fast plugin's own suite first,
  before wiring anything into the real promotion gate or targeting
  `agent-worktrees` (Phase 4's actual target, but its 268-test-file suite is
  the wrong place to debug the mechanism itself). This doesn't change the
  Plan's phase content, only its rollout order within Phase 0 — see this
  entry's own sub-bullets for what was actually run.
- Operator also asked that the smoke/fallback tier's membership be chosen to
  maximize coverage per unit of runtime it costs, not hand-picked. Folded
  into the vision as **coverage-efficient fallback curation** (a greedy,
  budget-bounded weighted-set-cover selection) and into this effort's own
  Phase 3 fallback-curation bullet — see `visions/coverage-guided-ci`'s own
  Provenance entry for the full reasoning.
- Picked `ai-attribution` (2 test files, no vendored path deps) as the pilot
  plugin: small enough to iterate on the selection mechanism itself in
  seconds, not minutes, before applying it anywhere that matters.
- Confirmed the coverage mechanism: `coverage.py` dynamic contexts
  (`--cov-context=test`) via `pytest-cov`, run directly with `uv run` rather
  than through `tools/run-plugin-tests.py` for this design-spike stage —
  the runner's sub-suite/venv-reuse model (25-file chunks, non-editable
  cached venvs) has no native coverage-context pass-through yet, and
  retrofitting it is Phase 1's job (instrumenting the real gate), not
  Phase 0's (proving the mechanism works at all). See
  `tools/coverage_guided_selection/` (added this phase) for the resulting
  prototype: `baseline.py` (collect a `.coverage` baseline + emit a portable
  JSON of test -> covered-lines + per-test wall-clock cost), `select.py`
  (diff-scoped selection: changed lines -> covering tests, with an explicit
  no-attribution fallback trigger), and `fallback.py` (the greedy
  coverage-efficient curation described above).
- Validated end-to-end against `ai-attribution`'s real suite (104 tests, 1
  in-process-covered script, 131 attributed source lines): a real changed
  line selected exactly the 1 test exercising it; an uncovered line and an
  unknown file both correctly tripped the fallback trigger (not a silent
  empty selection); and the greedy fallback set reached **100% coverage of
  the 131-line universe using roughly a dozen of the 104 tests in well
  under a second**, versus **~60-70s for the full suite** (observed across
  several runs; exact figures vary run to run with real subprocess timing
  noise) — on the order of **several hundred times** cheaper at full
  measured coverage, a concrete instance of "best coverage set for the
  smallest amount of total runtime." These are illustrative, observed
  figures, recorded here for context -- the real-suite integration test in
  `tools/test_coverage_guided_selection.py`
  (`test_collect_baseline_round_trips_against_a_real_plugin_suite`)
  deliberately asserts only durable *bounds* (non-empty coverage/selection,
  and that the curated fallback costs strictly less than the full suite),
  not these exact numbers, since a real subprocess run's precise timings are
  expected to vary slightly run to run.
- **Documentation impact:** `visions/coverage-guided-ci/README.md` is
  updated in place (new "coverage-efficient fallback curation" Concept +
  matching Behavior + Provenance entry) because this pilot's fallback-
  curation criterion is new intended behavior, not an existing gap the
  vision already described -- per the Change Intake vision-extending
  reflex. This effort's own Plan (Phase 3's fallback-curation bullet) and
  this Journal are updated to match. No other repo-wide documentation
  (`AGENTS.md`, `docs/architecture.md`, `TESTING.md`) yet describes this
  prototype: it is Phase 0 scaffolding under `tools/`, not a documented
  end-user capability, so none of those need a change for this pilot --
  `TESTING.md` gains a mention once a later phase wires this into a real,
  user-facing CI tier.
- Phase 0's first checklist item (pick the coverage mechanism) is
  substantiated by this pilot; its other two items — the baseline's durable
  storage/correlation location, and spiking collection inside
  `validate-and-promote.yml`'s real jobs — are repo-CI-architecture
  decisions deliberately **not** made by this pilot and remain open for the
  next increment, since this pilot's own standalone ephemeral-venv approach
  (not `tools/run-plugin-tests.py`'s cached `.test-venvs`, which has no
  coverage-context pass-through yet) was itself a deliberate scope
  narrowing — see this entry's own sequencing note above.
- Next increments, in order: (1) get this pilot's own PR reviewed and
  merged; (2) decide Phase 0's remaining two items (storage/correlation
  location; instrumenting `validate-and-promote.yml` for real) against a
  second, still-small pilot or directly against `agent-worktrees`'
  `worktrees-smoke` job, per operator direction; (3) only then begin Phase 1
  for real.

### 2026-09-28 — Kickoff
- Effort created from a five-round operator/agent conversation (see
  `inception-transcript.md`): a general coverage-tooling question, the
  CI-prioritization idea, the `coverage-guided-ci` vision (PR #4440), a
  dev-vs-main/vendoring refinement folded into the vision (PR #4444, 4
  review rounds), and this effort to drive it.
- Filed umbrella issue #4453.
