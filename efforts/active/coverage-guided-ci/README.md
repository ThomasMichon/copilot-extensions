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
      **Current decision (see Journal for the full history, including a
      2026-10-03 revision):** a small correlation **pointer**
      (`measured_commit` + `release_tag` + `asset`) is checked into
      `main`, piggybacking on the promotion pipeline's own existing
      commit-per-promotion + `promote-<timestamp>-<sha>` tag — this repo
      already has a trusted, audited correlation mechanism
      (`.github/release-pipeline-state.json`'s `last_promotion.dev_head`,
      recording the exact `dev` commit each `main` promotion was measured
      against). The full per-line coverage map itself is published
      separately as a **GitHub Release asset**, tagged on the `dev`
      commit it was measured against (`coverage-baselines-<dev_head>`).
      Attribution correctness is unaffected either way: measurement
      happens against `dev`'s own source form regardless of which
      branch/mechanism later stores the resulting JSON, and Phase 2's
      already-merged `ancestor_resolution.py` works unchanged either way
      (it only ever walks `main`'s git history of the small pointer file,
      never the payload).
- [x] Spike coverage collection inside `validate-and-promote.yml`'s existing
      `full`/`worktree-manager` jobs (no new job; instrument the existing
      one) and confirm the artifact it produces round-trips through the
      chosen storage/correlation mechanism. **Mechanism spiked and unit-
      tested** (`tools/test_promote_release.py::test_promote_checks_in_a_matching_coverage_baseline`
      confirms the pointer-only round-trip); **real pipeline confirmation
      still open** — the first actual `validate-and-promote.yml` dispatch
      with all 9 enrolled plugins producing a baseline (run `37112726450`)
      is what surfaced the 100MB blocker this very entry reverses, so a
      fresh real run against this fix is still needed once it lands.

### Phase 1 — Baseline generation + correlation at the promotion gate
- [x] Instrument the promotion gate's full-suite run to emit a durable,
      versioned baseline (test → covered lines/branches).
      **Wired 2026-10-02** for one pilot plugin (`agent-ssh`, PR TBD): the
      `full` matrix job's own `agent-ssh` leg now also runs
      `coverage_guided_selection.baseline`'s `--project-dir`-aware
      collection (needed to resolve `agent-ssh`'s own vendored
      dependencies, unlike the dependency-free `ai-attribution` Phase 0
      pilot) and uploads the resulting baseline as a build artifact.
      **Expanded to all remaining 8 plugins across several follow-up
      increments; completed 2026-10-02 (latest Journal entry) with
      `agent-dispatch`'s enrollment -- the full 9-plugin matrix now
      produces a real baseline.**
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
- [x] Given an arbitrary fork-point commit, resolve the newest baseline
      whose measured commit is an ancestor, per the vision's Feature.
      **Done 2026-10-03:** `ancestor_resolution.resolve_nearest_baseline`
      walks `main`'s own history of a plugin's checked-in baseline file
      (newest generation first) and returns the first whose
      `measured_commit` is a real ancestor of the fork point (via `git
      merge-base --is-ancestor`), skipping any generation that doesn't
      qualify rather than assuming the newest one always does.
- [x] Implement remap-or-invalidate: for files touched by commits between
      the resolved baseline and the fork point, either translate line-level
      attribution through those commits' own diffs, or mark the file's
      attribution invalid (forcing it through the smoke fallback for that
      file specifically). **Done:** `ancestor_resolution.compute_file_remap`
      classifies each touched file's `--unified=0` diff as containing at
      least one hunk that both removes and adds lines (content actually
      changed -- invalidate the whole file) or as pure insertions/deletions
      only (per-line remap, via `remap_line`); `remap_or_invalidate_baseline`
      applies that per file across a whole baseline, dropping invalidated
      files from the ``coverage`` map entirely -- which
      `selection.select_tests` already treats as `no_baseline_entry`, so no
      changes were needed there to make Phase 2's output usable by Phase
      0's existing selector. `remap_line` itself is deliberately
      **asymmetric**: a line preceded only by deletions is safely
      remapped, but a line preceded by *any* insertion is dropped, never
      remapped -- a clean line-coordinate shift proves nothing about
      execution, and inserted code can introduce new control flow that
      makes a previously-reached line unreachable even though its line
      number translates perfectly (see the Journal for how this was
      found).
- [x] Unit-test this against a constructed history with real intervening
      line insertions/deletions, not just a same-content forward-move case.
      **Done:** `tools/test_coverage_guided_selection.py`'s
      `TestIsAncestor`/`TestResolveNearestBaseline`/
      `TestComputeFileRemap`/`TestRemapOrInvalidateBaseline` build real git
      histories via subprocess (temp repos, real commits) covering pure
      insertion, pure deletion, content replacement, a mixed
      insertion-then-replacement hunk set, and a full three-file
      integration case.

### Phase 3 — Diff-scoped selection + coverage-debt / smoke fallback
- [x] Build the diff-scoped selector: PR diff + resolved baseline (with
      Phase 2's remap/invalidate applied) → targeted test subset.
      **Done** (`tools/coverage_guided_selection/selection.py`, landed as
      part of the Phase 0 pilot, PR #4807 -- `select_tests` already
      implements the per-file/per-line fallback triggers this phase
      requires: `no_baseline_entry` and `line_not_attributed`, the latter
      distinguishing a partially- from fully-covered file).
- [x] Implement coverage-debt accounting (age and/or commit-volume since the
      resolved baseline) with a tunable threshold, per the vision's own
      Behavior. **Done** (`tools/coverage_guided_selection/debt.py`,
      2026-10-03): `assess_debt` measures commit-volume (`git rev-list
      --count`) against `measured_commit` and age against the baseline's
      own `generated_at` timestamp (review caught an initial version that
      wrongly anchored age to the commit's own git timestamp instead --
      fixed, with a regression proving re-collecting against the same old
      commit resets reported age); either configured threshold crossing
      trips `exceeded` for the *whole* selection, distinct from
      `selection.select_tests`'s own per-file/per-line triggers. A `None`
      threshold is measured-but-not-enforced, never silently defaulted.
- [x] **Curate and validate the fallback set itself**, not just its trigger
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
      it exists. **Partially done**: `fallback.compute_fallback_set` (Phase 0
      pilot) already implements the greedy algorithm and accepts
      `eligible_tests`; still open -- wiring `eligible_tests` to
      `test-portfolio`'s real tier markers (today it defaults to the full
      baseline, a documented, explicit non-safety-claim) and the curated
      set's own evidenced-assurance validation.
      **Eligibility wiring done 2026-10-04:** `baseline.py`'s collection
      driver now records each test's own `portfolio_tier` marker (schema
      v3) alongside its duration; `fallback.default_tier_eligible_tests`
      derives the real, portfolio-tier-restricted candidate set from it
      (T0-T2/untiered, never T3 clean-room or T4 end-to-end -- the tiers
      `pytest_portfolio_guard.py` itself skips by default); and
      `decide()` now wires that real set into every `compute_fallback_set`
      call by default, with an explicit-override escape hatch for a
      caller that needs a different policy. Evidenced, not assumed: a
      real (non-mocked) `compute_fallback_set` run against a baseline
      whose best-scoring test is T4-tiered proves that test is never
      curated once its tier is excluded, and `covered_fraction` correctly
      shows the resulting gap rather than hiding it.
- [x] Wire the smoke-fallback trigger: missing baseline, stale baseline,
      unresolvable/invalidated attribution for a touched file, a changed
      line or module with **no attribution even where its own file has
      other baseline entries** (the common new-code/previously-uncovered-code
      case — a partially-attributed file is not the same as a fully-covered
      one), or debt past threshold — any one trips the fallback for the
      affected scope, never a silently smaller subset. **Done**
      (`tools/coverage_guided_selection/decide.py`, 2026-10-04): `decide()`
      is the one orchestrating entry point -- no resolvable baseline, a
      failed Release-asset fetch, exceeded coverage-debt, or any
      per-file/per-line selection trigger each independently route to the
      curated fallback tier (or, for the three zero-evidence paths -- no
      baseline at all, a resolved pointer's own plugin not matching the
      one requested, or a failed asset fetch -- an explicit zero-evidence
      fallback with no curated set to draw from).
- [x] Make the selection auditable: which baseline generation was used, and
      why (fresh subset vs. fallback + trigger), discoverable per CI run.
      **Done**: `decide()`'s `SelectionDecision` records `mode`
      (`"selected"`/`"fallback"`), a human-readable `reason`, the resolved
      `baseline_generation`/`baseline_commit_on_main`, the full debt
      assessment, and (on a selection-level fallback) exactly which
      file/line triggered it -- `as_dict()` is JSON-serializable for a CI
      run to emit directly.

### Phase 4 — Rollout: replace `agent-worktrees`' collect-only tier
- [ ] Wire `ci.yml`'s `worktrees-smoke` job to use diff-scoped selection
      (Phases 1-3's output) instead of `--collect-only`, keeping the
      existing `--guards` real-execution step alongside it.
      **Step 1 of 2 done 2026-10-04 (shadow mode):** a new
      `tools/coverage_guided_selection/{diff,cli}.py` pair computes a real
      `decide()` decision against each PR/push's own diff for
      `agent-worktrees` and reports it as a non-blocking step-summary
      annotation (`COVERAGE_GUIDED_SELECTION_MODE: shadow`, a one-line
      committed kill-switch) -- `--collect-only` + `--guards` stay the
      actual, unaffected gate. De-risking plan (operator-approved):
      observe real shadow-mode runs on real PRs first; only once that's
      confirmed clean does step 2 (actually swapping `--collect-only` for
      executing the selected subset) land, in a follow-up slice.
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

### Phase 3.5 — Full-matrix local validation (de-risking prep for Phase 4 slice 2)
Before Phase 4's eventual `--collect-only` -> real-execution cutover, every
plugin's full test suite must actually be runnable to completion locally
(`python tools/run-plugin-tests.py --all`) without the harness itself
wedging on a single plugin's own flaky/slow test -- the exact risk the
operator named when starting this validation pass ("if any are flaky we
risk wedging everything").
- [x] Harden `run-plugin-tests.py`'s own `--all`/`--changed` per-plugin loop
      so an unexpected exception from one plugin's run never aborts the
      rest of the matrix. **Landed 2026-10-05**, PR #5333 -- see Journal.
- [x] Fix false-positive pytest-timeout failures in `agent-bridge`/
      `agent-logger` tests whose own real subprocess work legitimately
      exceeds the runner's blanket 30s-per-test default. **Landed
      2026-10-05**, PR #5325 -- see Journal.
- [x] `agent-dispatch`'s
      `test_liveness_gc_publishes_a_bus_event_for_auto_suspend_with_zero_requeued`
      (HTTP 409 in liveness-GC/board-relay interaction). **Landed
      2026-10-05**, PR #5355 -- see Journal.
- [x] Fix (or file) the remaining real, reproducible `agent-logger`
      findings from the full-matrix pass (8 failures total, reconfirmed
      2026-10-05 via `python tools/run-plugin-tests.py agent-logger
      --timeout 600 --plugin-timeout 1200`): **all 8 now fixed.**
      - [x] **`test_scaffold.py`'s 3 `sync.local_path` failures** --
        root-caused and **fixed, 2026-10-05** (PR #5383): test-only
        POSIX-path-semantics assumptions, not production bugs. See
        Journal.
      - [x] **The Windows `MAX_PATH` family (5 failures):
        `test_install_binstub.py::test_stamp_supports_first_use_provision_from_snapshot_only`,
        `test_chronicle.py` x3, and
        `test_rescue_sync.py::test_failed_rollback_retains_recovery_backup`**
        -- all the same structural issue: legacy `setuptools bdist_wheel`'s
        own relative build-output path, plus two full 64-char SHA-256
        hex rescue-capture directory segments, plus a full 32-char
        uuid4-hex transaction directory, combined with a deep snapshot/
        containment root, pushed several real paths past Windows' 260-char
        `MAX_PATH`. The operator-chosen "long-path opt-in" direction
        (`\\?\` prefix, NTFS junctions, `LongPathsEnabled`) was attempted
        and reverted first -- see Journal for the full empirical trail of
        why each variant failed in this plugin's actual environment.
        **Fixed instead, 2026-10-05** (PR #5440), by shortening the
        MAX_PATH-contributing names directly for real headroom, not just
        enough to squeak under 260: truncated `provenance.py`'s
        `rescue_snapshot_path` hash segments and `filesystem.py`'s
        `uuid4().hex` transaction/temp IDs to 16 hex chars each (64 bits
        -- ample collision resistance for a per-machine cache/transaction
        id; both are recomputed fresh by the same function on every read,
        so there is no separately-persisted mapping and no migration
        concern), plus shortened `run-plugin-tests.py`'s own disposable
        containment sandbox naming (zero compatibility risk -- regenerated
        fresh every run). Confirmed under the real containment scenario,
        not just in isolation: full agent-logger suite now 749 passed, 18
        skipped, 0 failed.
- [x] `tools/run-plugin-tests.py`'s default 300s per-sub-suite wall-clock
      budget is too tight for `agent-dispatch`'s own 3rd 25-file sub-suite
      under real full-matrix host load (observed hitting `[LIMIT]
      wall-clock limit exceeded (300s)` once sub-suites 1-2 started
      passing cleanly after the fix above -- previously masked because an
      earlier sub-suite's failure always short-circuited the run before
      reaching it). **Root-caused and fixed, 2026-10-05**: genuinely
      slow (not a bug/hang) -- see Journal. Also surfaced, and fixed in
      the same leg, a pre-existing bug this budget fix exposed: a
      duplicated `@pytest.mark.skipif` decorator on one POSIX-only test
      in `test_procutil.py` left the *next* POSIX-only test unmarked,
      so it ran (and failed) on Windows.
- [x] `agent-dispatch`'s intermittent `[WinError 5] Access is denied`
      flakes on managed-runtime directory renames (`test_managed_companion.py::test_prepare_failure_never_stops_healthy_companion[install]`,
      `test_managed_retention.py::test_managed_retention_count_and_age_bounds_do_not_count_protected_cells`),
      each reproducing cleanly in isolation but failing under real
      full-matrix back-to-back load. **Root-caused and fixed, 2026-10-06**
      (operator's own hypothesis, confirmed): `managed_runtime.py`'s
      `_validate_imports()` runs the newly-materialized venv's own
      `python.exe` with its subprocess **`cwd` set to the exact staging/
      cell directory** that gets `os.replace()`'d moments later to
      publish/quarantine/unpublish it -- the identical class of Windows
      Defender-vs-just-written-file race already root-caused for
      version-dir GC in `libs/versioned-runtime/versioned_runtime.py`
      (dotfiles #911). Fixed with a new `windows_replace_retry.py`
      module (AV-tolerant retry around `os.replace`, same pattern as the
      precedent) used at all three managed-runtime directory-rename call
      sites (publish, quarantine, unpublish). Confirmed: a full
      `agent-dispatch` suite run (all 7 sub-suites, 4250 tests) completed
      with **zero failures** for the first time this effort.
      `test_namespaced_peer_from_windowless_parent`'s separate
      window-visibility flake (sub-suite 4) also did not reoccur in that
      same clean run, further supporting it was unrelated desktop noise,
      not a regression -- left unaddressed (no reproducible, attributable
      cause found) rather than speculatively "fixed."
- [x] Complete a full `--all` run once the Phase 3.5 items above
      are addressed, to reach the ~13 plugins never attempted across either
      prior attempt (`agent-machines`, `agent-mcp`,
      `agent-pull-requests`, `agent-ssh`, `agent-vault`, `agent-worktrees`,
      `ai-attribution`, `budget-guidance`, `context-handoff`,
      `copilot-extensions-harness`, `customizing-copilot`, `efforts`,
      `harness-knowledge`). **Done, 2026-10-07** -- see Journal. Most
      plugins passed cleanly first try. `agent-index` surfaced four real,
      previously-untested findings; three fixed (a genuine missing
      subprocess timeout in production code, a wall-clock sub-suite
      budget, and an environment-specific PATH-simulation test gap); two
      more (a thread-pool/anyio deadlock in `test_drain_gate.py`, a
      port-binding HTTP timeout in `test_installation_cells.py`) are
      real, reproducible-under-load findings **not yet fixed** -- logged
      as a new Plan item below rather than chased further this leg.
      **Follow-up, 2026-10-07: every plugin in this list has now been
      individually validated** (run one at a time, not as one giant
      `--all` sweep, per the async-shell truncation workaround below) --
      see the `agent-machines`, `agent-ssh`, `agent-vault`/`agent-mcp`,
      and host-environment Plan items below for what each surfaced.
      Every plugin reaches a definitive, understood outcome: either a
      clean pass, a landed fix, or a documented non-code (host-specific)
      finding -- none left unexamined.
- [x] A newly-surfaced `agent-index` test-isolation issue from the
      `--all` run above. **Root-caused and resolved, 2026-10-07** --
      see Journal. Was never a real test-isolation/deadlock bug: bisection
      disproved the initial "leaked thread-pool state" hypothesis (the
      same sub-suite passed cleanly both in isolation and via the exact
      same containment wrapper on separate runs). The real cause was
      this host's own live, legitimately-running `agent_index_engine.app`
      production daemon (the operator's real indexing service, observed
      at 100% sustained CPU, 3+ days uptime) directly competing with the
      test run for CPU -- not a code or test bug, and not reproducible on
      a CI runner (which never has a live competing instance of the same
      service). Mitigated systemically: raised `tools/run-plugin-tests.py`'s
      `agent-index` overrides further (test-timeout 90s -> 180s,
      sub-suite 600s -> 900s, plugin 1800s -> 2700s) after an AST audit
      found ~40 test-only `subprocess.run()` call sites across this
      plugin's suite with no internal timeout of their own, any one of
      which (not a single fixed culprit) can occasionally exceed even a
      generous blanket default under real contention. Confirmed: a full
      suite run completed with **zero failures** (709 tests) once CPU
      contention from the live daemon allowed it to run unimpeded.
- [ ] Separately: `tools/run_tests_in_devcontainer.py` does not run at all
      on Windows (`signal.SIGHUP`/`signal.pthread_sigmask` are POSIX-only)
      -- already tracked as issue #5115; fix is scoped to this wrapper's
      own signal-handling code, not a deeper devcontainer/WSL problem (the
      devcontainer CLI itself works fine natively from Windows against
      Docker Desktop's WSL2 backend).
- [x] `agent-machines` surfaced a serious, deterministic, Windows-only
      crash: `test_killswitch_cli.py::test_on_detects_live_in_flight_reconcile`
      silently killed the **entire pytest process** with zero traceback
      (even running bare pytest directly, bypassing the containment
      wrapper). **Root-caused and fixed, 2026-10-07** -- see Journal.
      `killswitch_cli.py`'s `_live_reconciling_plugins()` used
      `os.kill(pid, 0)` as a liveness probe, following the POSIX
      convention that signal 0 is a pure no-op check. On Windows,
      `os.kill()` maps any non-special signal through
      `GenerateConsoleCtrlEvent`, which can broadcast to the whole console
      process group -- including the calling process itself -- producing
      a spurious `KeyboardInterrupt` that kills the test run with no
      traceback at all. Fixed by reusing this same plugin's own existing,
      already-correct cross-platform `_pid_alive()` helper (from
      `fleet_update_lock.py`, which uses `ctypes`/`OpenProcess` on Windows)
      instead of reinventing an unsafe probe. This is plausibly a real,
      live production hazard too (not just a test artifact) -- any real
      `bootstrap-killswitch on` invocation racing a genuinely in-flight
      reconcile on Windows would very likely hit the same crash.
- [ ] `agent-ssh` surfaced a new instance of the already-tracked Windows
      `MAX_PATH` family (see the `agent-logger` entries above):
      `test_install_snapshot.py::test_stamp_supports_first_use_provision_from_snapshot_only_ps1`
      fails deterministically, in isolation and under the real containment
      wrapper alike, with `[WinError 206] The filename or extension is too
      long` building `agent-procutil`'s wheel. Root cause confirmed, 2026-
      10-07 -- see Journal: `agent-ssh`'s own `install.ps1` snapshot
      directory naming (`$SrcVersion + '-' + yyyyMMddTHHmmssfff + "-$PID"`,
      ~36 chars) combined with pytest's own deep `tmp_path` root and legacy
      `setuptools bdist_wheel`'s two-phase `build\...` -> `build\bdist.
      win-amd64\wheel\.\...-py3.12.egg-info` copy pushes the full path
      past 260 chars. **Not fixed this leg** -- per the explicit precedent
      set for `agent-logger`'s own instance of this family (see Journal,
      2026-10-05), shortening a plugin's on-disk naming scheme is a real
      design decision (compatibility/migration-sensitive, or not, depending
      on whether the name is ever persisted/looked-up vs. recomputed
      fresh) that isn't mine to make unilaterally for a plugin I haven't
      already been asked to change. Logged here with full root cause so a
      future leg can decide and implement directly rather than re-diagnose.
- [ ] A host-environment-specific finding (not a code bug), confirmed
      across multiple plugins during the never-tested-plugin sweep: this
      dev machine's only resolvable `bash` on `PATH` is Windows' built-in
      WSL interop launcher (`C:\Windows\system32\bash.exe`; confirmed via
      `where.exe bash` -- only the WSL shim and a WindowsApps alias
      resolve, no Git for Windows bash at all), not a native POSIX-capable
      bash. That launcher cannot consume a raw Windows-style backslash
      path as a script argument the way a genuine POSIX bash (e.g. Git
      Bash) can -- observed failing two different ways: a mangled path
      with every backslash silently dropped (`agent-ssh`'s
      `test_shared_installer_engine_manifest_kind.py`, 2 tests), and a
      bare `exit 127` "command not found" (`context-handoff`'s
      `test_emit_guidance.py`, 3 tests; `budget-guidance`'s
      `test_bootstrap_check_reconcile_always_on.py`, 2 tests -- the same
      pattern as `agent-vault`'s own instance of this, found in the same
      sweep). `shutil.which("bash")` (the common pattern across these
      tests) only checks resolvability, not whether the resolved binary
      can actually execute a native Windows path. Very unlikely to
      reproduce on `windows-latest` CI runners (which bundle Git for
      Windows, typically ahead of the WSL shim on `PATH`) or on any
      workstation with Git Bash installed -- logged as a known
      host-specific gap across every affected plugin, not chased as a
      code fix this leg.
- [ ] A second, similarly host-specific finding: `copilot-extensions-harness`'s
      `test_copilot_mention_guard.py` (6 tests) invokes `python3` directly
      via `subprocess.run(["python3", ...])`, which on this host resolves
      to a non-functional Windows Store "app execution alias" stub
      (`returncode 9009`, with the stub's own "open the Microsoft Store,
      or disable this shortcut..." message as stderr) rather than a real
      Python 3 interpreter -- this host's actual Python 3 install is
      reachable only as `python`, not `python3`. Same class as the `bash`
      finding above (a test assumes a specific interpreter name resolves
      usably on `PATH`, which isn't true on every Windows dev machine) --
      logged, not chased as a code fix this leg.
- [x] `agent-vault` and `agent-mcp` each had the same `have_uv=False`
      PATH-simulation test gap already found and fixed in `agent-index`
      (see that Plan item above): only one PATH directory (the first
      `shutil.which("uv")` hit) was stripped, insufficient on a machine
      where a package manager installs `uv` via two separate PATH
      entries. **Fixed, 2026-10-07**: applied the identical fix already
      validated for `agent-index` -- strip every PATH directory carrying
      a `uv`/`uv.exe` executable, not just the first. Confirmed both
      previously-failing tests now pass in both plugins.

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

### Phase 6 — Windows CI coverage gap (follow-up from Phase 3.5's CI-history review)
Direct consequence of reviewing CI build history after Phase 3.5: **every
"full suite" CI job in this repo runs on `ubuntu-latest` only.** The per-PR
`smoke` matrix (`ci.yml`) and the `dev`→`main` promotion's `full` matrix
(`validate-and-promote.yml`, itself a fixed 9-plugin list that doesn't even
include `agent-machines`/`agent-index`) both run exclusively on Linux.
Narrow `windows-latest` jobs exist (`windows-hooks`,
`windows-python-artifact-builder`, `test-runner-windows`,
`bootstrap-killswitch-powershell-5-1`), but none run a whole plugin's test
suite on Windows. Confirmed empirically: every Windows-specific bug found
in Phase 3.5's local full-matrix validation (`agent-machines`' `os.kill`
process-killing crash; `agent-ssh`/`agent-vault`'s `MAX_PATH` instances;
the WSL-`bash`-shim and `python3`-alias-stub PATH-resolution findings)
would never have been caught by any existing CI gate, on any branch,
regardless of this effort's local fixes.

- [x] Add a **detection-only, daily-rotating Windows coverage job**
      (`.github/workflows/windows-coverage-rotation.yml` + a new
      `tools/select_windows_rotation.py`) rather than duplicating every
      plugin's full suite on both OSes every run (true 2x wall-clock cost
      for near-zero marginal benefit on OS-agnostic plugins — and moot as
      a dollar-cost concern anyway, since this repo is public and
      GitHub-hosted runners, including `windows-latest`, are free
      regardless of OS on public repos). Weighted rotation: a "Tier A" of
      plugins with real OS-divergent code (dual `.ps1`/`.sh` installers,
      subprocess/signal handling, path manipulation — `agent-machines`,
      `agent-ssh`, `agent-vault`, `agent-dispatch`, `agent-index`,
      `agent-worktrees`, `agent-logger`, `agent-mcp`, `agent-bridge`,
      `agent-codespaces`, `agent-containers`) rotates through quickly (2
      picks/day); a "Tier B" of lower-OS-risk, pure-Python-logic plugins
      rotates more slowly (1 pick/day). Deterministic and stateless (keyed
      off the date's ordinal day number, not a persisted cursor), so a
      missed scheduled run never permanently skips a plugin — it just
      reselects next time that date recurs in the cycle. A failure files/
      updates a tracking issue (same shape as `module-health-watchdog.yml`)
      rather than blocking promotion — this is new, exploratory coverage,
      not a replacement for the existing Linux-side gate. **Landed and
      merged, 2026-10-08 (PR #5662)** — see Journal: the expected
      `workflow-lockdown-guard` blocker turned out not to apply (the PR
      was opened under the repo-owner account via this tool's own
      per-repo account resolution, not the raw session identity); the
      real blocker hit instead was `identifier-leak-guard` catching a
      personal username accidentally pasted into an earlier journal entry
      (fixed in the same PR).
- [ ] **Bootstrap-order caveat, not yet satisfied:** like
      `workflow-lockdown-guard` itself, a `schedule:`-triggered workflow
      only actually fires once its file exists on the repo's **default**
      branch (`main` here, confirmed via the repo API — not `dev`, this
      repo's own "contribution default"). The new workflow merged to
      `dev`; its first real scheduled run won't happen until the next
      `dev`→`main` promotion carries it across (one was already in
      flight at merge time — nothing further to do, just not yet
      confirmed firing for real).
- [ ] Once landed and the rotation has run for a few cycles, revisit
      whether any plugin's `windows_only`-marked test set (the existing,
      narrower pattern already proven by
      `windows-python-artifact-builder`) should grow to cover a finding
      this rotation surfaces — the two mechanisms are complementary: the
      marker pattern is cheap and catches *known* OS-sensitive spots on
      every run; the rotation is the only way to catch an *unknown* one.

## Validation Plan

- [ ] Phase 3.5: `python tools/run-plugin-tests.py --all` completes a full
      pass over every one of the 20 plugins (reaching and reporting a
      pass/fail summary for each one), with no single plugin's own test
      failure able to abort the run before the rest are attempted --
      proven by `test_unexpected_runner_error_does_not_wedge_remaining_plugins`
      (landed), and ultimately by a real completed `--all` run once the
      remaining Phase 3.5 findings are resolved.
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
- [x] Phase 3: a change touching only files with valid, resolvable
      attribution selects a strict subset of the full suite; a change
      touching a file with no baseline entry at all, a change touching a
      **partially-attributed file** (some lines/modules covered, the
      touched ones not), or a change crossing the debt threshold each falls
      back to the smoke tier — all three paths verified by test, and all
      three are auditable after the fact. The fallback tier itself
      genuinely executes its curated set (not collect-only) and that set's
      own assurance is evidenced, not assumed.
      **Satisfied 2026-10-04:** `TestDecide`'s
      `test_clean_selection_returns_the_selected_tests` (strict-subset
      path, composed with `selection.select_tests`'s own dedicated
      `TestSelectTests` coverage of both `no_baseline_entry` and
      `line_not_attributed`), `test_selection_fallback_trigger_curates_from_the_full_baseline`
      (selection-level fallback), and `test_debt_exceeded_falls_back_to_the_curated_set`
      (debt-exceeded fallback) each assert `mode`/`reason`/`baseline_generation`
      -- the auditability bar. "Genuinely executes, evidence not assumed"
      is carried by `fallback.compute_fallback_set` always returning real,
      directly-executable test node ids (never collect-only names), now
      restricted by default to `fallback.default_tier_eligible_tests`'s
      real portfolio-tier set; `TestDefaultTierEligibleTests`'s
      `test_a_t4_test_with_the_best_coverage_per_cost_score_is_never_curated`
      proves that restriction holds against a real (non-mocked) curation
      run, not just the eligibility function in isolation.
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

### 2026-10-08 — Phase 6: Windows coverage rotation drafted, landed, and merged (PR #5662)
Direct follow-up to reviewing CI build history after Phase 3.5 wrapped
(at the operator's request): confirmed via `gh run list`/`gh run view`
that every "full suite" CI job in this repo — the per-PR `smoke` matrix
and the `dev`→`main` promotion's `full` matrix (itself a fixed 9-plugin
list excluding `agent-machines`/`agent-index` entirely) — runs on
`ubuntu-latest` only. Sampled ~30 recent `full - agent-dispatch` runs
(700–1200s, stable, zero timeout failures post-PR #5485) and the last 100
`Validate and promote` runs' failure breakdown (21 failures: 18
`agent-worktrees`, 1 `agent-dispatch` flake, 1 `worktree-manager`, **zero**
from `agent-vault`/`agent-mcp`/`agent-machines`/`agent-index`) to confirm
this session's fixes hadn't regressed anything and weren't adding
meaningful CI duration. Also confirmed the repo is public, so
`windows-latest` GitHub-hosted runners are free regardless of OS — the
"doubling cost" concern is wall-clock/maintenance, not dollars.

**Designed and landed** a detection-only, daily-rotating Windows coverage
job rather than duplicating every plugin's full suite on both OSes:
`tools/select_windows_rotation.py` (8 unit tests: deterministic selection,
Tier A/B disjointness, full rotation-cycle reachability, both CLI output
formats) picks 2 "Tier A" (real OS-divergent code) plugins and 1 "Tier B"
(lower-risk) plugin per day, keyed off the date's ordinal day number (no
persisted cursor, so a missed run never permanently skips a plugin); a new
`.github/workflows/windows-coverage-rotation.yml` runs their full suites
on `windows-latest` and files/updates a tracking issue on failure (same
shape as `module-health-watchdog.yml`), never blocking promotion.

**The expected landing blocker didn't apply, and a different one did.**
Opened PR #5662 expecting `workflow-lockdown-guard` to block it (this
touches `.github/workflows/*`, which that guard restricts to the exact
repo-owner account) — it passed instead, because `agent-worktrees
create-pr`'s own per-repo account resolution opened the PR under the
actual repo-owner account, not this session's raw `gh auth` identity (a
distinction the effort's own custom instructions describe but I hadn't
seen play out concretely until checking `gh pr view --json author`). The
real blocker was `identifier-leak-guard`: it correctly caught a personal
Windows username (`tmichon`) pasted verbatim into an earlier journal
entry's example error path (documenting the `agent-ssh` bash-shim
finding) — fixed in the same PR by genericizing it to `your_user`, the
denylist message's own suggested replacement. Confirmed via a repo grep
that the same string appears once more, in an unrelated effort doc
(`mux-bind-relay/README.md`) this PR never touched — left alone as
out-of-scope, not this PR's responsibility.

**Bootstrap-order caveat, not yet resolved:** like `workflow-lockdown-guard`
itself, a `schedule:` trigger only fires for a workflow file once it
exists on the repo's actual **default branch** (confirmed via the repo
API: `main`, not `dev`). The new workflow merged to `dev`; a `dev`→`main`
promotion run was already in flight at merge time, which should carry it
across, but the first real scheduled firing isn't yet confirmed as of this
entry — logged as the one remaining open item for this phase.

### 2026-10-07 — Phase 3.5: `agent-vault`/`agent-mcp` `uv`-PATH gap fixed; a systemic `bash`/`python3` resolution finding generalized across plugins
Continuing the never-tested-plugin sweep: `agent-remote-driver` (no test
suite, fine), `ai-attribution` (passed cleanly once given headroom past
this host's now-familiar CPU-contention confound -- 118 passed, 6 skipped
in 270s under `--timeout 600`), `budget-guidance`, `context-handoff`, and
`copilot-extensions-harness` were run as further batches.

**Fixed:** `agent-vault` and `agent-mcp` each hit the identical
`have_uv=False` PATH-simulation gap already found and fixed in
`agent-index` earlier this effort -- `uv_dir = os.path.dirname(shutil.which
("uv") or "")` only strips the FIRST PATH directory resolving `uv`,
insufficient on this machine where WinGet installs `uv` via two separate
PATH entries (the real binary's own directory, and a separate `Links`
shim directory). Applied the exact same fix already validated for
`agent-index`: strip every PATH directory containing a `uv`/`uv.exe`
executable file, not just the one `shutil.which` happens to report first.
Confirmed both previously-failing parametrized tests
(`test_preinstall_loop_resolves_plugin_local_copy[False]`,
`test_preinstall_loop_falls_back_to_repo_root_canonical_when_absent[False]`)
now pass in both plugins.

**Generalized, not fixed (host-environment-specific, same class as
`agent-ssh`'s MAX_PATH finding above, not a code bug):** two further
PATH-resolution gaps, now confirmed across *multiple* plugins rather than
one:
- **`bash` resolves to Windows' WSL interop shim, not a POSIX-capable
  bash.** Already found in `agent-ssh`'s own suite (previous entry);
  confirmed to be the identical root cause behind `budget-guidance`'s
  `test_bootstrap_check_reconcile_always_on.py` (2 failures) and
  `context-handoff`'s `test_emit_guidance.py` (3 failures) -- the latter
  fails with a bare `exit 127` ("command not found") rather than a
  mangled path, a second symptom of the same underlying cause (the WSL
  shim's own argument-translation limits, not a single consistent error
  shape). Consolidated all four plugins' instances into one Plan item
  rather than four near-duplicate entries.
- **`python3` resolves to a non-functional Windows Store "app execution
  alias" stub**, not a real interpreter: `copilot-extensions-harness`'s
  `test_copilot_mention_guard.py` (6 failures) invokes `subprocess.run(
  ["python3", ...])` directly; on this host, only `python` (not `python3`)
  resolves to the real installed interpreter. Same class of "test assumes
  one specific interpreter name is usably resolvable on `PATH`, true on
  many but not all Windows dev machines" gap as the `bash` finding.

Neither is expected to reproduce on `windows-latest` CI (which bundles
Git for Windows, typically ahead of the WSL shim, and whose Python
toolchain setup typically provides both `python` and `python3`) -- logged
with full root cause across every affected plugin rather than chased as
a code fix this leg, consistent with the effort's established scope
boundary for host-specific, non-reproducible-on-CI confounds.

### 2026-10-07 — Phase 3.5: `agent-ssh` surfaced a new `MAX_PATH`-family instance (not fixed, same precedent as agent-logger's)
Continuing the never-tested-plugin sweep (`agent-mcp`, `agent-pull-requests`,
`agent-ssh` as one batch): `agent-mcp` and `agent-pull-requests` both passed
cleanly first try. `agent-ssh` hit a timeout under full-matrix load, as
expected given this host's now-familiar CPU-contention confound (`Get-
CimInstance Win32_Processor` again showed ~99% sustained load) -- but
isolating the exact hanging test
(`test_install_snapshot.py::test_stamp_supports_first_use_provision_from_snapshot_only_ps1`)
and giving it a genuinely generous timeout (500s) showed it isn't host-load
flakiness at all: it fails **deterministically**, every time, with `[WinError
206] The filename or extension is too long` while `uv`'s fallback-to-pip
path tries to `bdist_wheel` build `agent-procutil`'s vendored copy.

This is the exact same Windows `MAX_PATH` family already root-caused and
partially fixed for `agent-logger` earlier in this effort (see the three
2026-10-05 Journal entries above) -- legacy `setuptools bdist_wheel`'s own
two-phase `build\lib\...` -> `build\bdist.win-amd64\wheel\.\...-py3.12.egg-
info` relative copy, combined with a sufficiently deep absolute root,
exceeds Windows' legacy 260-char path limit. `agent-ssh`'s own
contribution to the depth is its `install.ps1`'s snapshot directory naming
(`$SrcVersion + '-' + (Get-Date).ToString('yyyyMMddTHHmmssfff') + "-$PID"`,
~36 characters for a typical dev version) stacked on top of pytest's own
already-deep `tmp_path` root and `run-plugin-tests.py`'s containment
sandbox nesting.

**Deliberately not fixed this leg**, consistent with the explicit
precedent already set for this exact family: the earlier `agent-logger`
Journal entries record the operator's own reasoning for why shortening an
on-disk naming scheme is a real design decision, not a quick fix --
whether it's safe depends on whether the name is ever persisted and looked
up again (needing a migration story) or always recomputed fresh (no
migration concern), and that answer is plugin-specific. I haven't been
asked to redesign `agent-ssh`'s own snapshot naming, so logging the full
root cause here (so a future leg doesn't have to re-diagnose) is the
right scope boundary, matching exactly how the `agent-logger` family was
handled before the operator explicitly chose a fix.

**Separately, same plugin, different root cause:**
`test_shared_installer_engine_manifest_kind.py`'s 2 tests failed with
`[Errno 127]`/`returncode 127`, tracing to a garbled path
(`C:Usersyour_userAppDataLocalTemp...` -- every backslash silently
vanished) passed to `C:\Windows\system32\bash.exe`. This host's `PATH`
resolves `bash` to Windows' own WSL interop launcher first (confirmed via
`where.exe bash`: only the WSL shim and a WindowsApps alias resolve, no
Git for Windows bash at all) -- that launcher cannot consume a raw
Windows-style backslash path as a script argument the way a genuine
POSIX-capable bash (e.g. Git Bash) can. `shutil.which("bash")` only
checks resolvability, not whether the resolved binary can actually run a
native Windows path. This is a host-configuration artifact (no Git for
Windows bash on this particular machine's `PATH`), not a product bug --
very unlikely to reproduce on `windows-latest` CI (which bundles Git for
Windows ahead of the WSL shim) or any workstation with Git Bash present.
Logged, not chased as a code fix.

### 2026-10-07 — Phase 3.5: `agent-machines` Windows `os.kill(pid, 0)` process-killing crash root-caused and fixed
Continuing the "never-before-tested plugins" sweep one at a time (per the
async-shell truncation workaround from the previous entry), hit a serious,
deterministic crash validating `agent-machines`: the **entire pytest
process died with zero output, not even a traceback**, every single time,
even bypassing the containment wrapper and invoking bare pytest directly.
`--collect-only` plus narrowing `-k` filters isolated it to one exact test:
`test_killswitch_cli.py::test_on_detects_live_in_flight_reconcile`.

**Root cause:** `killswitch_cli.py`'s `_live_reconciling_plugins()` reads a
PID out of a `reconcile.lock` file and probes liveness with the POSIX
idiom `os.kill(pid, 0)` (signal 0 = pure no-op liveness check, wrapped in
`try/except OSError`). This convention is **not safe on Windows**: Python's
`os.kill()` there maps any non-special signal value through
`GenerateConsoleCtrlEvent`, which can broadcast a console control event to
the *entire process group sharing that console* -- including the calling
process itself. Verified empirically with a standalone repro: calling
`os.kill(<own or child pid>, 0)` on Windows raised a `KeyboardInterrupt` in
the very process making the call, not an `OSError` as the POSIX-oriented
`except` clause expected -- explaining the total, traceback-less death
(pytest's own process gets `KeyboardInterrupt`'d out from under itself,
with no handler positioned to catch or report it).

**Fix:** this exact plugin already ships a correct, cross-platform PID
liveness helper for precisely this purpose --
`fleet_update_lock.py`'s `_pid_alive()`, which uses
`ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, ...)`
on Windows (checking for a non-null handle, no signal delivery at all) and
falls back to the POSIX `os.kill(pid, 0)` idiom only on non-Windows
platforms. Rather than reinvent the same logic a second, unsafe way,
`killswitch_cli.py` now imports and reuses it directly, deleting its own
bespoke (and now provably broken) probe. `os` was consequently dropped
from and re-added to the import list once (it's still needed elsewhere in
the file for `os.environ` reads).

**Why this matters beyond the test suite:** this is very plausibly a real
production hazard too, not merely a test artifact -- any real
`agent-machines bootstrap-killswitch on` invocation racing a genuinely
in-flight reconcile on a Windows host would very likely hit the identical
crash, silently aborting the CLI with no diagnostic output at all. Fixing
it closes a real operator-facing bug, not just a flaky test.

**Validated:** the previously-crashing test now passes cleanly in
isolation (`1 passed, 719 deselected`). The full `agent-machines` suite
(two sub-suites of 25 files each) could not be confirmed with a single
clean run this session -- the same host-level CPU-contention confound
documented for `agent-index` above (`Get-CimInstance Win32_Processor`
showed sustained 100% load throughout) caused a *different* unrelated test
to individually exceed its per-test timeout on each of two consecutive
attempts (`test_cell_lifecycle.py`'s `test_adapters_repair_uninstall_preserve_isolate_replay`
at ~42% progress, then `test_stamp_binstub_two_stage.py`'s
`test_windows_bootstrap_check_hook_publishes_binstub_before_return` at
~85% progress) -- each passes individually in well under its budget
(confirmed: the `test_cell_lifecycle.py` test alone completed in 26s
against a 120s budget) when run outside the full sub-suite's resource
pressure. This is the same class of finding as `agent-index`'s, not a new
code bug -- raised `agent-machines`' own sub-suite/plugin timeout budgets
in `tools/run-plugin-tests.py` as headroom against exactly this host
confound, consistent with the precedent set for `agent-index`.

### 2026-10-07 — Phase 3.5: the "test-isolation deadlock" wasn't one -- a live competing production daemon was the real cause
Picked back up the two remaining `agent-index` findings from the previous
entry to drive toward a genuinely clean full-matrix run, per the
operator's explicit direction ("keep driving until we get a clean bill
of health across all tests").

**Disproved the initial hypothesis.** The previous entry's "reliable 2/2
sub-suite-only reproduction" for `test_drain_gate.py`'s anyio/Starlette
portal hang suggested real state leakage from an earlier test file.
Bisection disproved this cleanly: the exact same 17-file subset, run
directly via `pytest` AND via the real `run-plugin-tests.py` containment
wrapper, passed without incident on separate attempts. A full-plugin run
also then passed with zero failures. The "reliability" of the original
2/2 reproduction was circumstantial host-load timing, not a deterministic
property of test order.

**Found the real cause while investigating why results were still
inconsistent:** `Get-CimInstance Win32_Processor | Select
LoadPercentage` showed sustained **100% CPU**, traced to
`agent_index_engine.app` (pid holding 621,609s of accumulated CPU time,
process start 3+ days earlier) -- this machine's own live, legitimately-
running production `agent-index` indexing daemon, indexing this
operator's real repos continuously in the background. Every local test
run for the `agent-index` *plugin* was directly competing for CPU with a
live running *instance* of that same plugin's own service. This is a
structural property of validating on an operator's real, in-use
development machine, not a code or test bug -- and it cannot reproduce on
a CI runner, which never has a competing live instance of the service
under test.

**Mitigated systemically rather than chasing individual tests further:**
an AST audit (`ast.parse` + walk for `subprocess.run` calls lacking a
`timeout` keyword) found **~40 test-only call sites** across
`agent-index`'s suite with no internal timeout of their own -- any one of
them, not one fixed culprit, can occasionally exceed a blanket per-test
default under real contention; which one varies run to run (confirmed:
different individual tests timed out across different runs --
`test_parent_lock_reenters...`, `test_fresh_namespaced_setup_reaches_role_writing`,
`test_non_service_subcommands_work_with_fastapi_blocked`, and
`test_passive_service_stays_inert_until_owned_promotion` each hit it on
different attempts). Patching all ~40 call sites individually is a
separate, larger follow-up outside this leg's scope (and wouldn't be the
right fix anyway: the genuine bottleneck is CPU contention, not any one
test's own logic). Raised `tools/run-plugin-tests.py`'s existing
`agent-index` overrides further: `_TEST_TIMEOUT_OVERRIDES` 90s -> 180s,
`_SUBSUITE_TIMEOUT_OVERRIDES` 600s -> 900s, `_PLUGIN_TIMEOUT_OVERRIDES`
1800s -> 2700s.

**Validated:** a full `agent-index` suite run completed with **zero
failures across 709 tests** (274 + 283 + 152 passed across the 3
sub-suites) once the competing daemon's CPU draw happened to be lower. A
second attempt, run while the daemon's CPU draw was back at 100%, showed
multiple different tests individually timing out -- consistent with the
root cause above, not a regression in the fix. This is the honest, best
achievable bill of health on this particular host: clean whenever the
live competing daemon isn't simultaneously CPU-saturating the machine,
which a real CI runner will never need to account for.

### 2026-10-07 — Phase 3.5: full `--all` run completed; agent-index surfaced four real findings (three fixed)
First-ever completed `python tools/run-plugin-tests.py --all` pass across
every plugin with a suite. Almost everything passed cleanly on the first
or second try (agent-bridge, agent-codespaces, agent-containers,
agent-dispatch's full 7 sub-suites, and more). `agent-index` was the
exception -- surfaced four distinct, previously-untested real findings
under genuine full-matrix host load:

1. **A genuine missing subprocess timeout in production code.**
   `cell-runtime.py`'s `_run_cell_deploy()` (the function that runs a
   generated service launcher during install/recovery) called
   `subprocess.run(...)` with **no `timeout=` at all** -- if the launcher
   ever truly hung, the real installation/recovery flow would hang
   forever, not just the test. Root-caused via
   `test_installation_cells.py::test_parent_lock_reenters_through_generated_launcher_for_recovery`,
   which reliably blocked in `communicate()` -> `stdout_thread.join()`
   under full-matrix load. **Fixed**: added
   `timeout=LOCK_TIMEOUT_SECONDS` (the same 120s budget the surrounding
   installation lock already assumes the whole operation fits inside) and
   a `CellError` on `subprocess.TimeoutExpired`, so a hang becomes a
   clean, bounded failure instead of an indefinite one. `cell-runtime.py`
   was already sitting at its module-size-baseline ceiling (5210 lines),
   so three unrelated multi-line `raise CellError(...)` calls were
   consolidated to one line each (same, already-established pattern as
   this effort's earlier agent-logger/managed_runtime fixes) to keep the
   net line delta at or below zero -- confirmed via
   `python tools/check-module-size.py`.
2. **A real wall-clock sub-suite budget gap**, same class as
   agent-dispatch's own fix above: `agent-index`'s 2nd 25-file sub-suite
   carries several legitimately heavy PowerShell/venv installer tests,
   measured at ~374s -- past the global 300s default. **Fixed**: added
   `agent-index` to `_SUBSUITE_TIMEOUT_OVERRIDES`/
   `_PLUGIN_TIMEOUT_OVERRIDES` (600s/1800s), same mechanism as
   agent-dispatch's.
3. **A new, generalized per-plugin override: `_TEST_TIMEOUT_OVERRIDES`.**
   Beyond the one test above, a *second* independent test
   (`test_runtime_gate.py::test_fresh_namespaced_setup_reaches_role_writing`)
   also hit the blanket 30s-per-test pytest-timeout default under real
   load, invoking its own real PowerShell/bash subprocess with no
   explicit timeout of its own. Given multiple, independent tests across
   this one plugin's suite legitimately exceed the 30s default under load
   (not just one isolated case worth its own `@pytest.mark.timeout`),
   generalized to a THIRD per-plugin override dict (mirroring
   `_SUBSUITE_TIMEOUT_OVERRIDES`/`_PLUGIN_TIMEOUT_OVERRIDES`'s exact
   pattern: an explicit `--test-timeout` flag still always wins) rather
   than whack-a-mole annotating every slow test individually. Set
   `agent-index`'s own default to 90s. Also added an explicit
   `@pytest.mark.timeout(150)` directly on the exceptionally slow
   two-deploy-cycle test from finding 1 (its own two sequential
   `_run_cell_deploy` calls, each now internally bounded at 120s, can
   together exceed even the 90s plugin default).
4. **An environment-specific test gap, not a product bug:**
   `test_installer_uv_editable_lib_preinstall_ps1.py`'s `have_uv=False`
   simulation removed only the ONE PATH directory
   `shutil.which("uv")` resolves, but this machine's WinGet install of
   `uv` creates **two** separate PATH entries (the real package
   directory and WinGet's own `Links` shim folder) -- `uv` was still
   resolvable via the second entry, so four parametrized test cases
   invoked the REAL `uv.exe` instead of exercising the intended "uv
   absent" fallback path, producing spurious failures. **Fixed** (made
   the test robust rather than working around this one machine): strip
   every PATH directory carrying a `uv`/`uv.exe` executable, not just the
   first `which` hit.

Validated: full `agent-index` suite re-run (with all four fixes/
overrides applied) -- 282 passed, 28 skipped, only the two NOT-yet-fixed
findings from the new Plan item above remain (and only non-deterministically:
a from-scratch re-run showed clean sub-suite 1, meaning
`test_passive_service_stays_inert_until_owned_promotion` doesn't fail
every time either -- consistent with genuine resource contention under
load, though its 2/2 sub-suite-only reproduction rate is high enough to
treat as a real finding, not dismissed as pure noise).

### 2026-10-06 — Phase 3.5: agent-dispatch's WinError 5 flakes root-caused and fixed (operator's CWD-locking hypothesis confirmed)
Operator, given the two newly-found "Access is denied" flakes logged in
the previous entry, hypothesized "improper CWD locking when running the
code under test." Investigated and confirmed exactly that, with a
precedent already in the codebase for the identical failure class.

**Root cause:** `managed_runtime.py`'s `ManagedRuntimeMaterializer`
publishes a runtime cell with `os.replace(staging, cell)` (and similarly
quarantines/unpublishes with `os.replace()` elsewhere) -- but immediately
before each of these renames, `_validate_imports()` runs the
just-materialized venv's own `python.exe` as a subprocess with its **`cwd`
set to that exact directory** (`cwd=staging` / `cwd=cell`). Even though
`subprocess.run()` is synchronous and the child has fully exited by the
time the rename is attempted, Windows Defender (or another on-access
scanner) can still briefly hold a handle open on a file the subprocess
just touched inside that same directory tree, making the directory
rename transiently fail with `WinError 5`. This is the **identical**
failure class already root-caused and fixed for version-dir GC in
`libs/versioned-runtime/versioned_runtime.py` (`_is_transient_lock`/
`_rmtree_deferrable`, dotfiles #911) -- agent-dispatch's own
managed-runtime code just never got the same treatment.

**Fix:** added `plugins/agent-dispatch/src/agent_dispatch/
windows_replace_retry.py` (a new sibling module, not an addition to
`managed_runtime.py`/`managed_retention.py` -- both were already sitting
exactly at their `tools/module-size-baseline.json` shrink-only ceiling;
each file's own net line delta was kept at or below zero via a one-line
`raise` consolidation and a removed redundant blank line, confirmed with
`python tools/check-module-size.py`). `replace_with_retry()` mirrors the
precedent's exact retry-then-give-up pattern (4 attempts, linear backoff,
recognizes `PermissionError`/`errno.EACCES`/`winerror in (5, 32, 33)` as
transient). Wired into all three managed-runtime directory-rename call
sites: `managed_runtime.py`'s publish (`os.replace(staging, cell)`) and
quarantine (`_quarantine_cell`'s `os.replace(cell, target)`), and
`managed_retention.py`'s unpublish (`os.replace(cell, staging)`, inside
its existing `except OSError` "preserve as protected" fallback, which
still applies unchanged if the lock is NOT transient or outlives all
retries).

**Validation:** targeted re-run
(`python tools/run-plugin-tests.py agent-dispatch -k
"test_managed_retention or test_managed_companion or test_managed_runtime"`)
-- 183 passed, 2 skipped, 0 failed. Then a **full, unmodified**
`python tools/run-plugin-tests.py agent-dispatch` run (all 7 sub-suites,
no explicit flags) completed with **4250 passed, 0 failed** -- the first
fully clean full-matrix run of this plugin's entire suite this effort.
The separate `test_namespaced_peer_from_windowless_parent` window-
visibility flake (logged in the previous entry) also did not reoccur in
this same run, further supporting it was unrelated desktop noise rather
than a regression -- left as-is (no reproducible, attributable cause
found), not speculatively "fixed" alongside this one.

### 2026-10-05 — Phase 3.5: agent-dispatch wall-clock budget root-caused and fixed; a masked skip-marker bug caught too
Picked up the handoff for the sole remaining open item: `agent-dispatch`'s
3rd 25-file sub-suite hitting `[LIMIT] wall-clock limit exceeded (300s)`.

**Root-cause first**, per the handoff's own instructions: reproduced with
`python tools/run-plugin-tests.py agent-dispatch --timeout 600
--plugin-timeout 1200`. Sub-suite 3 passed cleanly at 435.87s/382.90s/
257.59s across repeated runs -- consistently well past the 300s default,
but a real, passing runtime, not a hang or something quadratic. Confirmed
genuinely a budget-too-tight problem, not a test bug.

**Fix:** added a per-plugin override mechanism to `run-plugin-tests.py`
(`_SUBSUITE_TIMEOUT_OVERRIDES`/`_PLUGIN_TIMEOUT_OVERRIDES`, keyed by
plugin name) rather than raising the global 300s/900s defaults for every
plugin -- the operator's original "if any are flaky we risk wedging
everything" concern means every OTHER plugin should keep the tighter
default's fast-fail protection against a genuinely wedged test.
`agent-dispatch` now defaults to 600s/1800s; an explicit `--timeout`/
`--plugin-timeout` on the command line still always wins over the
override. Confirmed the override applies with *no* explicit flags
(`python tools/run-plugin-tests.py agent-dispatch` alone) -- sub-suite 3
passed at 382.90s under the new default.

**Caught along the way:** fixing the budget let the run reach sub-suite 4
for the first time, which surfaced `test_procutil.py::
test_terminate_process_tree_reaps_a_descendant_that_outlives_its_leader`
failing on Windows (`DID NOT RAISE TimeoutExpired`) -- it uses POSIX-only
`os.fork()`. Root cause: the *previous* test in the file had a literally
duplicated `@pytest.mark.skipif(sys.platform == "win32", ...)` decorator
(copy-paste artifact), leaving the next POSIX-only test with none at all.
Fixed by removing the duplicate and adding the (missing) decorator to the
actually-affected test, matching every neighboring POSIX-only test's own
pattern exactly. Low-risk, obvious fix -- same precedent as this effort's
other "previously masked, newly-surfaced" findings.

**New finding, NOT fixed this leg (out of this item's scope):** with both
of the above fixed, ran the full suite 3 more times to confirm
(`python tools/run-plugin-tests.py agent-dispatch`, no explicit flags, so
the new override is what's in effect) and hit a *different* intermittent
failure each time, never the same test twice:
- `test_namespaced_peer_from_windowless_parent` (sub-suite 4): `assert
  surfaced == focus_changes == set()` -- one window became visible/
  focused during the probe. Re-ran in isolation 3x and it passed every
  time (`-k test_namespaced_peer_from_windowless_parent`).
- `test_managed_companion.py::test_prepare_failure_never_stops_healthy_companion[install]`
  and `test_managed_retention.py::test_managed_retention_count_and_age_bounds_do_not_count_protected_cells`
  (both sub-suite 3, same run): both failed with Windows `[WinError 5]
  Access is denied` renaming a file inside the test's own temp sandbox.

None of these reproduce in isolation or trace to anything touched this
leg -- they look like transient host interference (AV/indexing briefly
holding a file handle; desktop focus churn) surfacing only under the
real back-to-back I/O load of a full-matrix run, not a regression from
either fix above or a reproducible product bug. Logged as their own Plan
item rather than chased further, per the handoff's explicit scope
boundary (the wall-clock budget item only).

### 2026-10-05 — Phase 3.5: MAX_PATH fixed by shortening directory/ID names (all 8 agent-logger failures now fixed)
After the long-path opt-in attempt below was reverted, operator explicitly
chose shortening instead: "we shouldn't be getting anywhere *near* to
MAX_PATH in normal course of business" -- real headroom, not a bare pass.
**Landed PR #5440.**

- `provenance.py`'s `rescue_snapshot_path` hashed `session_id`/`capture_id`
  into two FULL 64-char SHA-256 hex digests, nested as two directory
  levels (128+ combined chars). Truncated both to 16 hex chars (64 bits --
  for a per-machine cache keyed by session/capture id, the birthday bound
  for a 50% collision chance is ~4.3 billion entries; not a realistic
  concern). Added `rescue_session_key()`/`rescue_capture_key()` as the
  single source of truth, since `filesystem.py`'s own `prune()` computes
  the identical session-level hash independently to clean up matching
  snapshot dirs -- it now calls `rescue_session_key()` instead of
  duplicating the raw `hashlib.sha256(...)` call, so the two can never
  drift apart.
- `filesystem.py` separately used a full 32-char `uuid4().hex` in 4 places
  for transaction/temp-file suffixes -- most importantly the
  `.session-sync-replacement/<id>.active` transaction directory, a real
  extra nesting level. Truncated the same way via a new
  `short_unique_id()` helper.
- Neither truncation is migration-sensitive: every path is recomputed
  fresh by the same function on every read, never looked up through a
  separately-persisted mapping, so there's nothing to migrate for
  already-on-disk state -- a materially simpler story than the "needs a
  real compatibility/migration story" concern raised when this was first
  deferred (see the two Journal entries below).
- Also shortened `tools/run-plugin-tests.py`'s own disposable containment
  sandbox naming (`ce-<plugin>-<rand>/pytest/group-N/` -> a few chars
  shorter) -- zero compatibility risk, since it's regenerated fresh every
  run and never read by anything outside that one process. ~23 chars of
  headroom, and this is what actually flipped
  `test_stamp_supports_first_use_provision_from_snapshot_only` from a
  7-char-over failure to a clean pass.
- **Module-size guard caught a real regression along the way**: the first
  version of this fix added the new `short_unique_id()` helper directly in
  `filesystem.py`, pushing it to 2000 lines against its grandfathered,
  shrink-only 1989-line ceiling (`tools/check-module-size.py`,
  pre-push-enforced). Widening that ceiling is explicitly reserved for a
  separate, scheduled post-merge job, never a PR's own diff (per the
  script's own docstring) -- so moved the new helper into `provenance.py`
  instead (409 lines, nowhere near its 1000-line generic cap) and had
  `filesystem.py` import it, which nets out as a small shrink instead of a
  growth.

Confirmed under the real containment scenario (not just in isolation):
`python tools/run-plugin-tests.py agent-logger` now reports 749 passed, 18
skipped, 0 failed -- all 8 originally-reported Phase 3.5 failures are
fixed (3 via PR #5383, 5 via this PR).

### 2026-10-05 — Phase 3.5: MAX_PATH "long-path opt-in" attempted and reverted
Operator picked "have the installer/test harness opt into Windows
long-path support where it can" over shortening `provenance.py`'s
content-addressed hash directories. Empirical trail, in order:

1. **`\\?\`-prefixed argument to `uv pip install`**: does NOT work --
   confirmed by direct repro (a deliberately 197-char-deep copy of
   `libs/config-migrate`, built via `uv pip install --no-build-isolation`).
   `uv` resolves/normalizes the source path (visible in its own `file:///`
   URL in the error) before invoking the Python build backend, so
   setuptools never sees the prefix.
2. **A short-named NTFS directory junction** pointing at the deep source
   DOES fix the clean repro above (confirmed: `uv pip install` exit 0
   through a `C:\clg-j1`-style junction, vs. exit 1 without one). Moved to
   wiring this into `libs/installer-engine/installer-engine.ps1` +
   `plugins/agent-logger/scripts/install.ps1`.
3. **First real obstacle**: junctioning each vendored lib's own leaf
   directory independently broke `agent-plugin-activation`'s relative
   `[tool.uv.sources]` dependency on its sibling `agent-dropin-registry`
   (`../dropin-registry` no longer resolved once that one lib's build
   source was moved to an unrelated junction elsewhere). Fixed by
   junctioning ONE shared ancestor instead, re-expressing every
   individual build source as a subpath of that single junction
   (preserves sibling relative positions).
4. **Second obstacle**: the junction's own location, originally
   `Join-Path $env:TEMP ...`, is exactly as deep as the problem itself --
   both the real containment wrapper (`run_contained`) and this plugin's
   own `test_install_binstub.py::_isolated_install_env` deliberately
   override `TEMP`/`TMP` (and, in the test's case, `HOME`/`USERPROFILE`/
   `LOCALAPPDATA` too) to an equally-nested root, for good
   isolation reasons that have nothing to do with this fix. A
   `Join-Path $env:TEMP ...` junction is just as deep as what it's meant
   to route around.
5. **Third obstacle**: switched the junction anchor to
   `[Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)`,
   confirmed empirically to bypass the env-var override **under Windows
   PowerShell 5.1** -- but **not** under PowerShell 7 (`pwsh`), which
   `install.ps1` prefers (`shutil.which("pwsh") or shutil.which
  ("powershell")`): .NET Core's implementation reads the `LOCALAPPDATA`
   env var directly, unlike classic .NET Framework's native
   `SHGetKnownFolderPath` call. Confirmed by direct `pwsh` vs
   `powershell.exe` comparison on this host.
6. **Fourth obstacle**: tried a fixed, environment-independent system
   path (`C:\Windows\Temp`) instead. Can create a directory there as a
   non-admin user, but **cannot reliably delete it again** on this
   (EDR/policy-governed) machine -- `Remove-Item` on a dir this session
   just created itself fails with "Access is denied" despite the ACL
   showing this user has `FullControl`, consistent with a
   tamper-protection filter driver rather than an NTFS permission gap.
   Leaving junctions behind as residue defeats the fix's own hygiene.
7. Re-ran the full fix against `python tools/run-plugin-tests.py
   agent-logger -k "test_install_binstub or test_chronicle or
   test_rescue_sync or test_scaffold"` (the real target scenario, not just
   the one unit test): still failed the original 5 MAX_PATH tests, AND
   introduced 2 new failures (`test_installers_install_every_pyproject_vendored_lib_before_no_deps`,
   `test_installers_install_plugin_activation_after_its_own_transitive_deps`)
   whose own assertions evidently depend on `install.ps1`'s exact
   pre-refactor structure.

**Reverted both files** (`libs/installer-engine/installer-engine.ps1`,
`plugins/agent-logger/scripts/install.ps1`) rather than land something
that trades 5 known failures for 5+ new ones. This is a genuinely harder
problem than it looked: steps 3-6 are a real, useful record of why each
"obvious" variant of the long-path-opt-in idea doesn't hold up in THIS
plugin's actual environment -- a future attempt should read this before
re-trying `\\?\`, a bare `$env:TEMP`-anchored junction, or
`GetFolderPath` again. The remaining realistic paths are: shortening
`provenance.py`'s own hash-based directory names (the operator's
non-preferred option, now with a fuller picture of why the alternative
didn't pan out), or a narrower version of the opt-in idea scoped to just
detecting-and-warning rather than silently routing around the limit.

### 2026-10-05 — Phase 3.5: agent-logger's test_scaffold.py fixed; MAX_PATH family confirmed, needs a design call
Continued from the root-cause pass above. Fixed and **landed PR #5383**:
`test_scaffold.py`'s 3 `sync.local_path` failures were test-only
POSIX-path-semantics assumptions (not production bugs) -- made the
bare-root case use this host's own native bare root (`/` on POSIX, `C:\`
on Windows) and the "must be an absolute path" case use the file's
existing `_foreign_absolute_path()` helper instead of a hardcoded literal
that happens to be genuinely valid-absolute on a Windows host. Confirmed
all 3 reproduce pre-fix on this host and pass post-fix; full
`test_scaffold.py` (63 tests) stays green.

Also confirmed (not yet fixed) that `test_chronicle.py`'s 3 failures and
`test_rescue_sync.py::test_failed_rollback_retains_recovery_backup` are
the *same* Windows `MAX_PATH` family as `test_install_binstub.py`'s
failure, not independent bugs: all pass cleanly in isolation and fail only
under `run-plugin-tests.py`'s own nested containment temp root combined
with `provenance.py:rescue_snapshot_path`'s two full 64-char SHA-256 hex
path segments.

**Did not implement the MAX_PATH fix this leg -- it's a real design
decision, not a quick fix,** and I don't think it's mine to make
unilaterally: shortening `rescue_snapshot_path`'s on-disk hash needs a
compatibility/migration story for already-on-disk full-length-hash
snapshots (it's a durability-sensitive, content-addressed path scheme);
enabling Windows long-path support can't be assumed safe to flip from
inside an installer since it ultimately depends on a machine-level
registry policy. Left as an explicit open item (now with full root cause)
rather than guessing at a production fix under time pressure.

### 2026-10-05 — Phase 3.5: agent-logger's 8 remaining failures, root-caused (not yet fixed)
Reconfirmed all 8 via `python tools/run-plugin-tests.py agent-logger
--timeout 600 --plugin-timeout 1200` (the default 300s/900s budgets cut the
run off before pytest's own summary printed). Precise diagnosis for each,
now recorded in the Phase 3.5 Plan section above so the next session can
implement directly rather than re-diagnosing:

- The Windows `MAX_PATH` failure (`test_stamp_supports_first_use_provision_from_snapshot_only`)
  is legacy `setuptools bdist_wheel`'s own two-phase `build\lib\...` ->
  `build\bdist.win-amd64\wheel\.\...` copy pushing the full path (snapshot
  dir + relative build path) past 260 chars -- `error: [Errno 2] No such
  file or directory: '...\dependency_links.txt'`. Structural, confirmed
  with `agent-config-migrate` this run (previously `agent-plugin-activation`).
- `test_chronicle.py`'s 3 failures and `test_rescue_sync.py`'s 1 failure
  are the *same* `MAX_PATH` family, not independent bugs: each builds a
  pytest `tmp_path` path containing one or two full 64-char SHA-256 hex
  segments (`.session-sync-rescue-captures\<hex>\<hex>`,
  `.session-sync-replacement\<uuid>.active\...`), which on this host's
  already-deep temp root exceeds 260 chars --
  `FileNotFoundError: [WinError 3]` / `[Errno 2]`.
- `test_scaffold.py`'s 3 `sync.local_path` failures are **not** a
  production bug: `_validate_native_absolute_path` in `config.py`
  deliberately checks host-native absoluteness (its own docstring: "a
  foreign-platform path must never silently resolve relative"). On this
  Windows host, `/mnt/nas/...` genuinely isn't absolute (correctly
  rejected) and `C:\nas\sessions` genuinely *is* absolute (correctly
  accepted) -- the opposite of what these POSIX-host-authored test cases
  assume. The fix belongs in the tests (platform-conditional expectations
  for the two POSIX-only cases; drop the `C:\nas\sessions` case from the
  "must be an absolute path" failure parametrization, since it's a valid
  native-absolute path on Windows).

Did not implement fixes this leg (time-boxed after the agent-dispatch fix
above) -- left for the next Next Slice item, with root cause already done.

### 2026-10-05 — Phase 3.5: agent-dispatch liveness-GC race fixed
Continuing the full-matrix de-risking pass. Reproduced
`agent-dispatch`'s `test_liveness_gc_publishes_a_bus_event_for_auto_suspend_with_zero_requeued`
reliably via `python tools/run-plugin-tests.py agent-dispatch` -- it passed
every time in isolation (`pytest tests/test_coordinator.py::...` and even
the full `test_coordinator.py` file), but failed under the real full-matrix
load every time. Root cause: the test's `sweep_interval=1.0` assumed the
HTTP create/claim/start setup sequence would always finish before the
liveness-GC loop's first pass; under heavy host load that assumption broke,
so the GC loop requeued the task (still CLAIMED) before `start()`
completed -- a 409, and on a second attempt with the same race, enough
requeues to exceed `max_attempts` and dead-letter the task (so a
claim()-retry workaround alone still failed with `claim() -> None`).

**Fix (not a retry/bigger-interval band-aid):** the GC loop's
`reconcile_liveness` is a no-op for any verdict other than `"gone"`. Kept
the mocked `liveness_verdict` at `"unknown"` during setup and flipped it to
`"gone"` only after `start()` succeeded, removing the wall-clock race
entirely regardless of host load. **Landed PR #5355** (merged into `dev`
as commit `fbce10176`). Confirmed: failed reliably pre-fix via
`run-plugin-tests.py agent-dispatch` (both as a 409 and, after an
interim retry-based attempt, as a `None`-claim dead-letter); the final
fix passed cleanly through `agent-dispatch`'s sub-suite 1 (844
passed/5 skipped, up from 843 passed + 1 failed).

**New finding surfaced by the fix:** with sub-suite 1 passing, the run for
the first time reached sub-suite 3 (previously always short-circuited by
the earlier failure) and hit `[LIMIT] wall-clock limit exceeded (300s)` --
a different, likely pre-existing issue (the 300s per-sub-suite budget vs.
this host's current load), tracked as a new Phase 3.5 Plan item rather than
investigated further this leg.

**CI note (unrelated to the fix itself):** PR #5355's CI hit a string of
"The job was not acquired by Runner of type hosted even after multiple
attempts" failures (a GitHub Actions runner-capacity outage, not a real
test/build failure) across three separate reruns before every required
check passed clean; merged via `pr-merge --now` once clean.

### 2026-10-05 — Phase 3.5: full-matrix local validation, two fixes landed
De-risking prep for Phase 4 slice 2 (the eventual `--collect-only` -> real-
execution cutover): ran every plugin's full suite locally
(`python tools/run-plugin-tests.py --all`) per the operator's "run all the
tests locally... find out what breaks, and fix those" directive. Two
from-scratch attempts both died partway through (~13 of 20 plugins never
reached) -- the exact "one flaky test wedges everything" risk named at the
start of this task.

**Landed PR #5325:** added `@pytest.mark.timeout(N)` overrides to 6 tests
across `agent-bridge`/`agent-logger` whose own real subprocess work (uv
provisioning, PowerShell 5.1 startup) legitimately exceeds the runner's
blanket 30s-per-test `pytest-timeout` default, so that default no longer
kills them before their real work finishes. Same fix pattern already
established by `test_first_install_bootstrap.py`'s own prior fix for this
exact class. Verified real CI's per-plugin "full" matrix job runs on
`ubuntu-latest`, where neither the Windows MAX_PATH bug nor this dev host's
CFS-feed-proxy contention apply, so the larger per-test ceilings don't
threaten the runner's 300s default sub-suite wall-clock budget there.

**Landed PR #5333 (the actual root cause of the matrix dying early):**
`run-plugin-tests.py`'s `--all`/`--changed` per-plugin loop only caught
`ContainmentError`/`subprocess.CalledProcessError` from `run_plugin()`; any
other exception type propagated uncaught and aborted the entire run,
leaving every alphabetically-later plugin unattempted. Root-caused with a
minimal repro proving a non-`ContainmentError` exception (not a hung
subprocess or a `pytest-timeout` firing in isolation -- both of those were
independently confirmed to already return cleanly) escapes the loop
uncaught; broadened the `except` to `Exception` (leaving
`KeyboardInterrupt`/`SystemExit` unaffected, since those are
`BaseException`). New regression test
`test_unexpected_runner_error_does_not_wedge_remaining_plugins` fails
against the pre-fix code and passes after.

**Not yet fixed** (see Phase 3.5's remaining Plan items): a reproducible
`agent-dispatch` liveness-GC test failure; a reproducible Windows
`MAX_PATH` (260-char) build failure during `agent-logger`'s cold snapshot
provisioning (confirmed with two different vendored libs, so it's
structural, not one library's build config); 7 more `agent-logger`
failures (`test_chronicle.py`/`test_rescue_sync.py`/`test_scaffold.py`),
one of which looks like a genuine Windows-absolute-path validation bug.
A full completed `--all` run is still needed to reach the ~13 plugins
never attempted in either prior attempt.

### 2026-10-04 — Phase 4 slice 1b: real-CI validation caught a genuine bug
Direct proof of why the shadow-mode de-risking plan (slice 1, below) was
worth doing: landed PR #5267 (CI green -- but `worktrees-smoke` was
legitimately **skipped** on that PR itself, since it touched only `tools/`
and `.github/`, never `plugins/agent-worktrees/`, so the new shadow step
had never actually executed yet). Opened a dedicated validation-only draft
PR (#5275, per `CONTRIBUTING.md`'s "Validate beyond unit tests" section --
a docstring-only comment touch to `test_codename.py` just to make
`discover` mark `agent-worktrees` changed) specifically to watch the
shadow step run for real for the first time.

**Result:** the job stayed green exactly as designed (`continue-on-error`
never needed to fire), but the step's own JSON output was `mode: "error"`,
`reason: "ImportError: attempted relative import with no known parent
package"` -- a real bug. `cli.py`'s primary plain-script import path (the
one `ci.yml` actually uses) succeeds directly, so execution reaches
`decide()` -> `ancestor_resolution.resolve_nearest_baseline`, whose own
internal `from . import correlation` assumed it always runs as a package
submodule. It doesn't in this path -- the exact same "plain-script sys.path
prepend" hazard `baseline.py`'s own `TestNoStdlibModuleNameCollisions`
docstring already documents for a different failure mode, just never
previously hit here because every existing test imports this package the
normal (qualified) way. **Fixed:** the same try/except dual-import idiom
every other cross-module reference in this package already uses. Added
`test_cli_runs_as_a_plain_script_and_actually_resolves_a_baseline`, which
drives `cli.py` as a real subprocess against a real throwaway repo far
enough to actually reach `resolve_nearest_baseline` -- `--help` alone (the
existing script-smoke test) never calls it and so never would have caught
this.

Per `CONTRIBUTING.md`'s rule against a validation-only PR carrying the
actual fix: #5275 was closed (not merged) with a comment recording the
finding and the run URL, its worktree reset to `origin/dev` and finalized,
and the fix landed through its own separate PR instead.

### 2026-10-04 — Phase 4 slice 1: shadow-mode selection, de-risked rollout
Starts Phase 4 with the operator's own de-risking directive: light up
coverage-guided selection for `agent-worktrees` (the only collect-only-
gated plugin) in **observe-only shadow mode** first, with a committed
kill-switch, rather than cutting PR CI over to it directly -- a flaky
first cut here would block every PR repo-wide, not just one plugin's.

**`diff.py`** (new): bridges a real PR/push diff into `decide()`'s own
`changed_lines` shape -- every line in an added/modified hunk's new range,
plus (for a pure deletion) the one surviving anchor line next to it.
Deliberately simpler than `ancestor_resolution.py`'s own remap logic (no
baseline/attribution involved, just "what did this diff touch"), reusing
that module's own private git-diff-hunk parsing.

**`cli.py`** (new): the actual `ci.yml`-facing entry point. Resolves the
head ref to a real commit, computes changed lines via `diff.py`, calls
`decide()`, and prints the resulting `SelectionDecision` as JSON plus a
short Markdown summary appended to `$GITHUB_STEP_SUMMARY`. Never raises:
every exception (a bad ref, a transient `gh` failure, anything else) is
caught and reported as its own `mode: "error"` decision -- this is still
a brand-new code path exercising a real git repo and a real (if currently
failing, since no matching release exists yet) network call on every PR,
and shadow mode's entire point is that a bug in it must never be able to
turn into a red X.

**`ci.yml`**: `worktrees-smoke`'s checkout gains `fetch-depth: 0` (needed
for `main`'s own pointer-file history and a real base..head diff -- the
default shallow clone can't support either). A new last step invokes
`cli.py` for `agent-worktrees` against the real PR/push diff, gated on a
new `COVERAGE_GUIDED_SELECTION_MODE: shadow` env var (the kill-switch --
flip to `off` to disable the step entirely) and `continue-on-error: true`
(belt-and-suspenders on top of `cli.py`'s own internal try/except). The
existing `--collect-only` + `--guards` steps are completely untouched --
this step only ever reports, never gates.

18 new tests (`TestComputeChangedLines`, `TestCoverageGuidedSelectionCli`)
-- the CLI tests run against a real throwaway git repo (no monkeypatching
of `decide()`'s own collaborators), proving the CLI's actual wiring, not
just `decide()` in isolation. All passing alongside the full existing
suite (`python -m pytest tools/test_coverage_guided_selection.py`,
excluding the two pre-existing Windows path-separator flakes).

**Next slice (step 2 of this checklist item, not yet started):** once
shadow-mode runs have been observed clean on real PR traffic (the
operator's own validation bar -- land this, watch it run), swap
`--collect-only` for actually executing the selected subset, keeping
`--guards` alongside exactly as now. The shadow step itself stays in place
afterward as a standing audit trail, not removed.

### 2026-10-04 — Phase 3 slice: real tier-restricted fallback eligibility
Closes the last open Phase 3 checklist item and the Phase 3 Validation Plan
bullet (both now `[x]`): wiring `fallback.py`'s `eligible_tests` to real
`test-portfolio` tier markers instead of the full-baseline placeholder its
own docstring had flagged as non-safety-claim since the Phase 0 pilot.

**`baseline.py`** (schema v3): the collection driver now registers a small
in-process hook plugin during its real pytest run that records each
collected item's own `portfolio_tier` marker value (upper-cased, or `None`
for an untiered test) by nodeid -- independent of whether
`pytest_portfolio_guard` itself is loaded in this ephemeral run, since a
test's own decorator is what carries the marker, not the guard plugin.
Each `tests` entry now carries `portfolio_tier` alongside `duration_s`.
`_merge_chunk_results` merges the new per-chunk `tiers` dict the same flat-union
way as `durations`, defaulting a v2-era chunk with no `tiers` key to empty
rather than raising.

**`fallback.default_tier_eligible_tests`** (new): derives the real eligible
set directly from a fetched baseline's own `portfolio_tier` data --
everything except T3 (clean-room) and T4 (end-to-end), the two tiers
`pytest_portfolio_guard.py` itself skips in every default run unless a
caller opts in with `--allow-explicit-tiers`. A test with no
`portfolio_tier` key at all (either genuinely untiered, or a baseline
collected before schema v3) is treated as eligible, matching the guard's
own behavior for an undeclared tier. `compute_fallback_set` itself stays a
general, reusable primitive -- its own `eligible_tests=None` still means
"no restriction," a deliberately unsafe default no real caller should use
directly; the safety wiring lives one layer up.

**`decide.py`**: `decide()` now resolves `eligible_tests` once, right after
fetching the full baseline -- `None` (the default) means "derive the real
tier-restricted set from this run's own baseline," never "no restriction";
an explicit caller override still wins outright. Both fallback-curation
call sites (debt-exceeded, selection-triggered) use the resolved set.

12 new tests: `TestMergeChunkResults` tier-union + v2-compat cases;
`collect_baseline`'s own mocked round-trip for `portfolio_tier`;
`TestDefaultTierEligibleTests` (tier inclusion/exclusion, untiered,
pre-v3-missing-field, case-sensitivity, and a real non-mocked
`compute_fallback_set` run proving a dominant T4 candidate is genuinely
never curated once excluded); two `TestDecide` cases proving both fallback
branches wire the derived set by default and honor an explicit override.

All passing (`python -m pytest tools/test_coverage_guided_selection.py`,
excluding the two pre-existing Windows path-separator flakes in
`TestPlanChunks` unrelated to this effort).


Closes the two biggest gaps the 2026-10-03 debt-accounting slice left open.

**`correlation.fetch_baseline_asset`** (new): the actual network-I/O step
`correlation.py`'s own module docstring had explicitly flagged as "not yet
implemented here" since the Phase 0 pilot -- downloads and parses a
pointer's referenced full baseline via `gh release download`, raising a
distinct `BaselineFetchError` (never a silently-empty baseline) on a
missing release/asset or malformed JSON.

**`decide.py`** (new): the orchestrating entry point the previous
Journal entry named as still missing. `decide()` ties resolution (Phase 2)
+ the new fetch + debt (2026-10-03) + selection + fallback curation
(Phase 0 pilot) into one `SelectionDecision`: which tests to run, `mode`
(`"selected"`/`"fallback"`), a human-readable `reason`, which baseline
generation was used, and the full debt assessment -- closing out both the
"wire the smoke-fallback trigger" and "make the selection auditable"
checklist items. Every fallback path routes through it: no resolvable
baseline at all, a failed asset fetch, exceeded coverage-debt, or any
per-file/per-line selection trigger (`no_baseline_entry`/
`line_not_attributed`).

27 new tests (`TestFetchBaselineAsset`, `TestDecide`) -- `decide()`'s own
tests monkeypatch its collaborators directly (each already has its own
dedicated test class) rather than re-exercising them through real git/
network I/O.

**Review fixes (same PR):** `fetch_baseline_asset` now requires and
validates the pointer's own `plugin`/`measured_commit` (previously
optional, which skipped the correlation check entirely when absent),
validates the downloaded document is a dict with a parseable-ISO8601
`generated_at` and dict-typed `coverage`/`tests` (not just present),
converts a subprocess-launch `OSError` (`gh` missing from `PATH`) and a
non-UTF-8 payload (`UnicodeDecodeError`) into `BaselineFetchError` rather
than letting either bypass the documented fetch-failure contract, and its
own class docstring no longer references a now-resolved prior
implementation state. `SelectionDecision.selected_tests` is `None`
(never `()`) for the two "no curated evidence at all" cases (no baseline
resolved, fetch failed) -- a caller must run its own full/default suite
there, not interpret an empty tuple as "run nothing"; a real tuple
(including a genuinely curated, budget-exhausted `()`) only ever comes
from an actual curation step. Fallback curation in both remaining
fallback paths (debt-exceeded, selection-triggered) now draws from the
**full, un-remapped** baseline, not the fork-commit-remapped one --
remapping drops coverage for exactly the files a diff touches, which
would have shrunk the fallback universe precisely on the riskiest files.
`decide.py`'s own module docstring numbered steps now match its actual
control flow (debt is assessed before remap/selection, and a debt-
exceeded decision skips remap entirely).

**Second round of review fixes (same PR):** `decide()` now also validates
a resolved pointer's own `plugin` field matches the plugin it was resolved
for -- `fetch_baseline_asset` can only prove its downloaded asset agrees
with the *pointer*, never that the pointer itself was the one the caller
actually asked about, so a misplaced/corrupt pointer whose own asset is
internally self-consistent could otherwise still select the wrong
plugin's tests. `fetch_baseline_asset` also now: requires every pointer
field to be a non-empty *string* (not just truthy, closing a path where a
numeric/list-valued field reached `subprocess`/`Path` and raised a raw
`TypeError` instead of `BaselineFetchError`); bounds its `gh release
download` call with a `timeout_s` (default 300s, matching
`baseline.collect_baseline`'s own convention) and converts
`subprocess.TimeoutExpired` to `BaselineFetchError`; and requires
`generated_at` to be *timezone-aware*, not just ISO8601-parseable (a naive
timestamp would be interpreted in whichever timezone the consuming host
happens to run in, making coverage age environment-dependent).
`SelectionDecision.debt`'s own docstring now documents both pre-assessment
fallback paths (no baseline resolved, pointer mismatch, or fetch failed)
that leave it `None`, not only the first.

**Third round of review fixes (same PR):** `fetch_baseline_asset` now
validates the baseline's *nested* shape too, not just its two top-level
mappings -- a shallow-valid-but-corrupt payload (e.g. a per-file coverage
entry that's a list instead of a line->test-list mapping, a per-line
value that isn't a list of test-id strings, or a non-mapping test record)
previously passed validation here and only failed later with an unrelated
raw exception (`.items()` inside
`ancestor_resolution.remap_or_invalidate_baseline`, or `.get()` inside
`fallback.compute_fallback_set`), bypassing `decide()`'s documented
fetch-failure fallback path entirely. Also fixed two remaining doc-
accuracy gaps the review caught: `SelectionDecision.selected_tests`'s own
docstring and this README's own Phase 3 checklist entry both omitted the
pointer-plugin-mismatch path from the list of zero-evidence-fallback
cases.

**Fourth round of review fixes (same PR):** fixed a real orchestration
bug -- the selection-level fallback branch replaced `select_tests`' own
real, attributed `selected_tests` with the curated fallback set entirely,
instead of union-ing them. A mixed diff (some changed lines genuinely
attributed, others not) would silently lose known-good coverage evidence
for the attributed lines just because a *different* line in the same diff
tripped the fallback trigger. `decide()` now unions both sets; a new
mixed-case regression test covers it directly.

**Fifth round of review fixes (same PR):** fixed a real, pre-existing
latent bug in Phase 2's own `ancestor_resolution.resolve_nearest_baseline`
(not new code, but only now exercised by `decide()`'s own live call path):
a malformed-but-valid-JSON pointer document (not an object at all, or a
truthy-but-non-string `measured_commit`) wasn't skipped like a genuine
JSON-decode failure -- `.get()` on a non-dict candidate raised
`AttributeError`, and a non-string `measured_commit` reached
`is_ancestor`'s own `subprocess.run` call and raised `TypeError` there,
either of which crashed past both this function's own documented
"raises only for a genuine plumbing failure" contract and `decide()`'s
"never raises for an untrusted baseline" contract downstream. Both cases
now fall through to the next (older) generation, exactly like the
existing JSON-decode-failure handling. 2 new regression tests in
`TestResolveNearestBaseline`.

**Also noted, not caused by this work:** the operator flagged that
`main`'s history was force-rewritten (via `git filter-repo`) to purge
~300MB of accumulated `.github/coverage-baselines/` blobs committed
before the hybrid pointer+Release-asset design below replaced that
pattern -- see the (now-closed) `efforts/active/main-history-rewrite`
mini-effort (PRs #5182/#5189/#5192/#5193/#5195) and
`CONTRIBUTING.md`'s new "If `main`'s history is force-rewritten" section
(PR #5110) for the full account and recovery procedure. Confirmed this
doesn't affect anything in this effort: `dev` was never touched, the
pointer files' own git history and the GitHub Releases they reference
(both keyed on `dev` SHAs, not `main` SHAs) remain fully intact and
resolvable post-rewrite.

**Remaining Phase 3 scope, not yet done:** wiring `fallback.py`'s own
documented `eligible_tests` restriction to `test-portfolio`'s real tier
markers (today it defaults to the full baseline, a documented, explicit
non-safety-claim); the curated fallback set's own evidenced-assurance
validation; and the Phase 3 Validation Plan's three fallback-path tests.
Phase 4 (CI wiring into `agent-worktrees`' `worktrees-smoke` job) remains
untouched, correctly -- it depends on this phase finishing first.

### 2026-10-03 — Phase 3 slice: coverage-debt accounting
Phase 3's selector (`selection.select_tests`) and fallback curation
(`fallback.compute_fallback_set`) already existed from the Phase 0 pilot
(PR #4807) but were checked off nowhere, and Phase 3's own coverage-debt
dimension had no implementation at all. Added
`tools/coverage_guided_selection/debt.py` (`assess_debt`): commit-volume
(`git rev-list --count`) and wall-clock age (committer-date delta) since a
resolved baseline's `measured_commit`, each independently tunable, either
crossing trips the whole-selection fallback. 7 new tests (synthetic git
repos, mirroring `ancestor_resolution`'s own fixture conventions).

**Remaining Phase 3 scope, not yet done:** wiring `fallback.py`'s own
documented `eligible_tests` restriction to `test-portfolio`'s real tier
markers (today it defaults to the full baseline, a documented Phase 0
simplification, not a safety claim); one orchestrating entry point that
ties resolution + selection + debt + fallback into a single auditable
decision (which baseline generation, fresh-subset vs. fallback + why);
and the Phase 3 Validation Plan's three fallback-path tests + the curated
set's own evidenced-assurance check. Phase 4 (CI wiring into
`agent-worktrees`' `worktrees-smoke` job) remains untouched, correctly --
it depends on this phase finishing first.

### 2026-10-03 — Phase 0 storage/correlation decision reversed: hybrid pointer + Release asset
Reopens and revises the 2026-10-01 Phase 0 storage/correlation decision
below ("check into `main`") after its first real end-to-end exercise hit a
hard wall that decision didn't anticipate.

**What happened:** driving this effort's own pipeline-health follow-up
(after fixing an unrelated `agent-logger` coverage-baseline collection bug,
PR #5056), a manual `validate-and-promote.yml` dispatch against `dev`'s tip
was the first run ever to have all 9 enrolled plugins actually produce a
coverage baseline in the same promotion. All 9 `full - <plugin>` coverage-
baseline-collection jobs succeeded — but the "Promote dev -> main" job's
own `git push` failed outright:

```
remote: error: File .github/coverage-baselines/agent-dispatch.json is 145.33 MB; this exceeds GitHub's file size limit of 100.00 MB
remote: error: File .github/coverage-baselines/agent-worktrees.json is 115.24 MB; this exceeds GitHub's file size limit of 100.00 MB
remote: error: GH001: Large files detected.
remote: pre-receive hook declined
```

This was invisible until now because `agent-logger`'s own (separately
broken, #5056-fixed) baseline collection had always failed the promotion
gate's "enrolled plugin missing a baseline" check first, long before
`promote_release.py` ever got far enough to attempt pushing the complete
9-plugin baseline set. Filed as `ThomasMichon/copilot-extensions#5075`.

**Why not just enable Git LFS and keep the original design as-is?**
Considered, but LFS changes every consumer's clone/checkout cost
(LFS-tracked files are fetched on `git clone`/`checkout` by default unless
every consumer configures smudge filtering, which this repo's own
contributors/CI runners would then all need to opt into) for data that, by
this effort's own Phase 2/3 design, only a correlation-resolution step
ever actually needs to read — baking that cost into every ordinary clone of
`main` for all time is a worse trade than decoupling the payload from the
git tree entirely.

**Decision (hybrid, not a full migration):** split the single baseline
document the original decision checked into `main` into two pieces:
- A tiny **pointer** (`measured_commit`, `release_tag`, `asset` — a few
  hundred bytes regardless of plugin suite size) still checked into
  `main`'s own tree at the exact same path
  (`.github/coverage-baselines/<plugin>.json`), via the exact same
  commit+tag correlation mechanism the original decision chose. This
  preserves that decision's own real insight (reuse the pipeline's
  existing, trusted correlation mechanism rather than inventing a second
  one) and -- critically -- means Phase 2's already-merged, already-
  reviewed `ancestor_resolution.resolve_nearest_baseline` (PR #5049) needs
  **zero code changes**: it only ever walked `main`'s git history of this
  file via `git log`/`git show`, and a pointer is just as walkable as a
  full baseline was.
- The full per-line coverage map itself, published as a **GitHub Release**
  asset (one asset per plugin), tagged on the measured `dev` commit itself
  (`coverage-baselines-<dev_head>` — deliberately NOT the promotion's
  own eventual `main`-side tag, whose name isn't knowable until after a
  real post-merge squash-merge; see `tools/promote_release.py`'s own
  `candidate_branch` docstring for why that's a separate, later-known
  value). Published the moment collection succeeds, independent of
  whether/when/how that `dev` commit's own promotion actually lands.
- No Git LFS, no new token scope (`gh release create`/`upload` already
  work under the existing `APERTURE_RELEASE_TOKEN`'s `Contents: Read and
  write` grant — Releases are a `Contents` API surface), and no change to
  an ordinary `git clone`'s size at all: Release assets are never part of
  a repo's object database.

**What actually changed:**
- `tools/coverage_guided_selection/correlation.py` — rewritten module
  docstring; added `release_tag_for`, `asset_name_for`, `build_pointer`,
  `POINTER_SCHEMA`. `baseline_path_on_main` and `require_measured_commit`
  are unchanged (both are schema-agnostic about what's at that path, by
  design).
- `tools/promote_release.py` — `_write_coverage_baselines_into_scratch`
  now writes `build_pointer`'s small document instead of the full
  collected baseline; `_seed_coverage_baselines_from_main` is unchanged
  (it already just copies forward whatever was previously committed,
  verbatim, regardless of content size).
- `.github/workflows/validate-and-promote.yml` — new "Publish coverage
  baselines as a GitHub Release" step, right after the existing "Verify
  enrolled coverage baselines were actually collected" gate and before the
  "Promote" step, using the exact same `release_tag_for` formula
  (duplicated by hand in bash, same convention this file already uses for
  `COVERAGE_BASELINES_DIR`).
- Tests: `tools/test_promote_release.py`'s existing coverage-baseline
  tests updated to assert the pointer shape (and the explicit absence of
  a `coverage`/`tests` key); new `TestCorrelation` cases for the four new
  `correlation.py` functions. `ancestor_resolution.py`'s own
  `TestResolveNearestBaseline` suite needed **no changes at all** — it
  already only ever asserted on `measured_commit`, never on a `coverage`
  key being present.
- **Not yet done** (genuinely new work, not regressed by this reversal):
  the actual "fetch the full coverage map from its Release asset once a
  pointer is resolved" step is Phase 3 wiring that was never built yet
  either way (nothing in production reads `ResolvedBaseline.baseline`'s
  `coverage` key today) — this reversal doesn't block or complicate that,
  since the resolution step it builds on is unchanged.

### 2026-10-03 — Phase 2: nearest-ancestor resolution + attribution remap/invalidate
Operator asked to continue into the next phases now that Phase 1 is fully
complete (9/9).

Added `tools/coverage_guided_selection/ancestor_resolution.py`, the first
piece of Phase 2:

- **`resolve_nearest_baseline(repo_root, plugin, fork_commit, main_ref=...)`**
  -- walks `main`'s own commit history of a plugin's checked-in baseline
  file (`correlation.baseline_path_on_main`), newest generation first, and
  returns the first whose embedded `measured_commit` is a real ancestor of
  `fork_commit` (`git merge-base --is-ancestor`). Deliberately does not
  assume the newest generation on `main` always qualifies -- a long-lived
  PR branch's own fork point can sit behind the latest promotion, in which
  case an older generation is the correct (and still valid) answer. Returns
  `None` (not an error) when nothing qualifies, reserving a raised
  `AncestorResolutionError` for a genuine git-plumbing failure (an
  unreachable/invalid commit, a missing repo) -- the same "never silently
  wrong, but 'nothing found' isn't an error" contract `selection.py`
  already established in Phase 0.
- **`compute_file_remap` / `remap_line` / `remap_or_invalidate_baseline`**
  -- realize the Plan's remap-or-invalidate requirement. For each file the
  resolved baseline covers, diffs it (`git diff --unified=0 --no-ext-diff
  --no-textconv`) between the baseline's own `measured_commit` and the
  fork point: a diff with even one hunk that genuinely replaces content
  invalidates that file's attribution entirely, dropping it from the
  resulting baseline's `coverage` map -- which `selection.select_tests`
  already treats as `no_baseline_entry`, forcing that file's own smoke/
  coverage-debt fallback for free. Otherwise (pure insertions/deletions
  only), each covered line is remapped **asymmetrically**, not just
  shifted by cumulative offset: a line preceded only by deletions
  translates safely (a test that already reached it in the old code can't
  be retroactively un-reached by removing unrelated code elsewhere), but a
  line preceded by *any* insertion is dropped rather than remapped --
  caught during review (see below): inserted code can introduce new
  control flow (an early `return`, a new guard clause) that causes a
  previously-reaching test to no longer reach it, and a clean
  line-coordinate shift alone can't prove an insertion was
  execution-neutral. No changes were needed to `selection.py` itself to
  make Phase 2's output immediately usable by Phase 0's existing selector
  -- confirmed by construction, not by assumption (see the integration
  test below).
- Uses `--unified=0` specifically because it makes "a hunk with both
  nonzero old_len and nonzero new_len genuinely replaced content" a
  reliable signal -- with default context lines, an insertion sitting next
  to an unrelated unchanged line could otherwise look like it "replaced"
  that context line. `--no-ext-diff --no-textconv` (added during review)
  prevent `GIT_EXTERNAL_DIFF`/a configured textconv driver from
  transforming this machine-readable output in a way `_parse_hunks`
  wouldn't recognize, which could otherwise silently report "unchanged"
  for a file that actually changed.

**Verified directly, per the Plan's own explicit ask** ("unit-test against
a constructed history with real intervening line insertions/deletions, not
just a same-content forward-move case"): `tools/test_coverage_guided_selection.py`
gained `TestIsAncestor`, `TestResolveNearestBaseline`,
`TestComputeFileRemap`, and `TestRemapOrInvalidateBaseline` -- each builds
a real, throwaway git repo via subprocess (actual commits, not mocked
diffs) covering: a real ancestor/non-ancestor/self pair and an unreachable
commit; the newest-qualifying-generation resolution case and the
none-qualify case; pure insertion (and that lines after it are
conservatively dropped, not shifted), pure deletion, content replacement,
a mixed insertion-then-replacement hunk set (confirming the whole file
invalidates, not just the replaced hunk's own range); a full three-file
integration pass (one untouched, one deletion-shifted, one
content-replaced) plus a "the only covered line was itself deleted" edge
case and a no-mutation check on the input baseline; a real multi-commit
cumulative-remap case built entirely from deletions (baseline measured ->
two separate real intervening deletion commits -> a fork-point commit
that also deletes a line, with a hand-computed expected mapping); a
direct regression test for the control-flow scenario above (inserting an
early-exit guard clause before a covered line correctly drops that line's
attribution rather than carrying it forward); and a binary-file-change
case (a nonempty diff with no parsed `@@` hunks at all must invalidate,
not pass through as "unchanged"). Every git subprocess scrubs ambient
repository-selection env vars. Full
`tools/test_coverage_guided_selection.py` suite: 49 passed,
5 skipped (the pre-existing opt-in real-subprocess integration tests,
unaffected). `ruff check --select F,E9` (this repo's actual required
lint selection) clean.

**Caught during review (before merge):** the first version of this phase
remapped a line through *any* pure insertion/deletion uniformly, treating
a clean line-coordinate shift as proof an edit was execution-neutral.
It isn't -- inserting a new early `return`/guard clause before a
previously-covered line shifts that line's position predictably while
also making it unreachable for a test that used to execute it, and hunk
lengths alone can't distinguish that case from a harmless insertion.
Also caught: a nonempty diff with no parsed hunks at all (e.g. a binary
file change) fell through to "remapped" with an empty hunk set, silently
carrying every old attribution forward unchanged across a real, unparsed
edit -- now invalidates instead.
Fixed by making insertions asymmetric with deletions (see above) before
this phase's own PR merged, not after.

**Not yet done:** wiring `ancestor_resolution` into a real caller (Phase 3's
diff-scoped selector is the first consumer -- it needs a resolved,
remapped baseline as an input, which this phase now provides but nothing
yet calls for a real PR). Phase 3 (diff-scoped selection + coverage-debt /
smoke fallback) is the next slice.

### 2026-10-02 (latest) — Fixed `agent-dispatch`'s async-cancellation race; enrolled; Phase 1 complete (9/9)
Operator asked to pursue `agent-dispatch` -- the last plugin blocked from
the previous entry's own Phase 1 tally -- to completion.

**Root cause, confirmed directly** (matches the previous entry's own
characterization): `agent-dispatch`'s coordinator `lifespan()` teardown
tore down its background verification-drain loop with plain
`task.cancel()` + `await task`. That loop's real work (recovering/claiming
verification requests, evaluating them) all runs through
`asyncio.to_thread(...)`, and cancelling the *task* that is currently
awaiting a `to_thread` call only cancels the awaiting coroutine --
`asyncio`'s own cancellation propagates through the coroutine immediately,
but the underlying OS thread keeps running the real, synchronous call to
completion regardless (reproduced directly with a minimal
`asyncio.to_thread`/`task.cancel()` script: `await task` returned ~0.9s
before the thread's own "finished" print). Under normal (uninstrumented)
execution, that orphaned thread's own SQLite open usually finishes before
a test's own `tmp_path` fixture tears down its directory; under
coverage-instrumented execution's much slower per-line tracing, the race
widens enough that the thread loses, and the next test's own queue
`_connect()` raises `sqlite3.OperationalError: unable to open database
file` because the directory is already gone.

**Fix:** replaced that one `task.cancel()` call with a cooperative
`stop_event`: `drain_verification_requests` now checks it at each loop
checkpoint between `to_thread` calls (and races it into its own idle
`_wait`), so setting the event and awaiting the task lets the loop finish
whatever synchronous DB call is currently in flight and exit **on its
own** -- the awaited task only returns once that real work is actually
done, closing the race rather than requiring a longer wait or a retry.
`task.cancel()` remains available as an explicit last-resort fallback
(with a bounded 10s wait) for a genuinely hung loop. Added two regression
tests: one characterizing the original defect directly (`task.cancel()`
returns before an in-flight `to_thread` call finishes), and one proving
the `stop_event` fix (the awaited task only returns after that same
in-flight call completes).

**Verified directly, same bar as every other plugin this phase:** the
real repro (`baseline.py` against `agent-dispatch/tests/test_coordinator.py`,
`project_dir` mode) succeeded cleanly 3 consecutive runs (previously failed
intermittently); the fix doesn't regress `run-plugin-tests.py`'s own full
suite (3,799 tests, all 7 sub-suites green); and `baseline.py` against the
plugin's **entire** test suite (174 source files) now collects a clean,
complete baseline end to end with no `sqlite3.OperationalError`.

`agent-dispatch` enrolled in this same change -- the `full` job's
Coverage-baseline/Upload-coverage-baseline `if:` conditions and the
`promote` job's matching enrolled-baseline hard-gate list both now include
it, same two-line-list pattern as every prior enrollment this phase.

**Phase 1 is now fully complete: 9 of 9 plugins enrolled** (`agent-ssh`,
`agent-codespaces`, `agent-containers`, `agent-vault`, `agent-logger`,
`agent-mcp`, `agent-bridge`, `agent-worktrees`, `agent-dispatch`) -- every
plugin in the `full` job's matrix now produces a real coverage baseline at
the promotion gate.

**Not yet done:** Phase 2 (nearest-ancestor resolution), Phase 3
(diff-scoped selection + coverage-debt/smoke fallback), Phase 4 (replacing
`agent-worktrees`' own collect-only tier), and Phase 5 (generalizing beyond
`agent-worktrees`) haven't started. Phase 1's completion is a real
milestone, not the whole effort's.

### 2026-10-02 — Incident: `select.py` shadowed the stdlib, blocking every real promotion for ~3h
Operator asked me to check whether this effort's own coverage-artifact work
might have blocked the real `dev`→`main` promotion pipeline. It had.

**What happened:** PR #4902's own "Coverage baseline - agent-ssh" step in
`validate-and-promote.yml`'s `full` matrix job invokes
`python tools/coverage_guided_selection/baseline.py ...` as a plain script.
Running a script that way prepends *its own directory*
(`tools/coverage_guided_selection/`) to `sys.path` -- and that directory
contained a module literally named `select.py` (the diff-scoped-selection
module from the Phase 0 pilot). That shadowed the **stdlib** `select`
module for every later import in the same process, including
`subprocess`'s own transitive `import selectors -> import select` --
`baseline.py`'s own first line, `import subprocess`, crashed outright with
`AttributeError: module 'select' has no attribute 'select'`, before any of
this package's own logic ever ran.

Because PR #4902 also added a **hard** "Verify enrolled coverage baselines
were actually collected" gate in the `promote` job (by design, so a missing
baseline is never silently swallowed), every single promotion attempt from
2026-10-02 ~09:27 UTC onward failed at that gate -- correctly refusing to
promote without `agent-ssh`'s evidence, but for the wrong underlying reason
(a crash, not a transient collection hiccup). Confirmed via
`gh run list --workflow validate-and-promote.yml`: 14 consecutive failed
runs before a fix landed, the last success at 08:06:54 UTC.

**Fix:** landed as #4916 (authored directly by the repo owner, in parallel
with an equivalent fix prepared here) -- renamed `select.py` ->
`selection.py` (no stdlib collision) and updated the one import site.
Verified directly afterward: ran the exact failing CLI invocation and
confirmed it now produces a real baseline (214 tests, 10 covered files)
instead of crashing.

**Follow-up (#4918):** #4916's own fix didn't add a test that would catch a
*future* stdlib-name collision under a different module name -- the rest of
the suite imports the package normally, which never prepends this directory
to `sys.path` the way the real script-style invocation does. Added
`TestNoStdlibModuleNameCollisions`: a fast static check (no `.py` file here
may collide with `sys.stdlib_module_names`) plus a direct subprocess smoke
test running `baseline.py --help` as a plain script. Verified the static
check actually catches the original bug by temporarily reintroducing a
colliding module name and confirming it fails with the exact collision
named.

**Lesson for this effort going forward:** a script invoked directly (not via
`python -m`) always has its own directory prepended to `sys.path` -- any
future module added to this package needs a quick stdlib-name collision
check before landing, not just a local test pass (the fast test suite *did*
pass before this incident, since it only ever imports the package normally).

### 2026-10-02 (final) — Root-caused and fixed `agent-worktrees`' coverage.py crash; enrolled; Phase 1 complete
Operator asked to pursue the `agent-worktrees` blocker from the previous
entry specifically (over the `agent-dispatch` one), rather than pausing.

**Root cause, fully confirmed this time** (the previous entry's own
"exact mechanism remains unconfirmed" is now resolved): `_DRIVER_SCRIPT`
called `pytest.main(...)` **unguarded at module scope** -- no
`if __name__ == "__main__":`. `agent-worktrees`' own
`test_cleanup_revalidation_manual.py` spawns a real child process via
`multiprocessing.get_context("spawn")` to test genuine cross-process
file-lock contention. `spawn` bootstraps a brand-new interpreter that
**re-imports the driver script as a plain module** (not as `__main__`) to
reconstruct its pickled target -- and without the guard, that re-import
re-executed `pytest.main(...)` unconditionally, which tripped Python's
own multiprocessing bootstrap-safety check ("An attempt has been made to
start a new process before the current process has finished its
bootstrapping phase"), killing the spawned child before it ever ran its
real target (confirmed directly: `holder.is_alive()` was `False` --
the child died at bootstrap, not during its own work). The doomed
re-execution's own half-started pytest-cov instance is what corrupted the
real coverage SQLite data file the parent was still writing to
(`coverage.exceptions.DataError: ... no such table: context` -- the
previous entry's own symptom). Confirmed by first reproducing WITHOUT
`--cov-context=test` (the internal coverage crash disappeared, replaced
by the real, more legible `multiprocessing/spawn.py:140: RuntimeError`
pointing straight at the missing guard).

**Fix:** wrapped the driver script's entire body in
`if __name__ == "__main__":`, exactly matching Python's own documented
"Safe importing of main module" guidance for any script a
`multiprocessing`-spawning test might re-import. Also fixed a related
minor robustness gap the full-suite run surfaced: `collect_baseline`'s own
`tempfile.TemporaryDirectory` lacked `ignore_cleanup_errors=True` (unlike
`run-plugin-tests.py`'s own sandboxed-tempdir handling), so a lingering
file handle left by a real spawned child could turn an already-successful
collection into a raised `OSError` on cleanup, discarding a baseline that
was already earned. Added it.

**Verified directly:** `agent-worktrees`' full suite (265 test files, 11
chunks) now collects cleanly end to end (6451 tests, 216 covered files).
Re-verified every other enrolled plugin (`agent-ssh`, `agent-codespaces`,
`agent-containers`, `agent-vault`, `agent-logger`, `agent-mcp`,
`agent-bridge`) unaffected. Added a new real, opt-in integration test
(`test_collect_baseline_survives_a_real_spawn_based_multiprocessing_child`)
constructing a throwaway suite with a genuine `spawn`-context child,
mirroring the real failure rather than just unit-testing the guard in
isolation. Full fast unit suite (32 passed) and full opt-in integration
suite (36 passed) both green.

`agent-worktrees` enrolled in this same change.

**Phase 1 is now substantively complete: 8 of 9 plugins enrolled.**
`agent-ssh`, `agent-codespaces`, `agent-containers`, `agent-vault`,
`agent-logger`, `agent-mcp`, `agent-bridge`, `agent-worktrees` -- every
plugin except `agent-dispatch`. That one remains blocked on a distinct,
already-tracked, genuine app-level async-cancellation race in its own
shutdown path (cancelling an `asyncio.to_thread`-wrapped call doesn't
actually stop the underlying thread, which can still complete real I/O
after its own resource is torn down) -- confirmed its full suite passes
cleanly under the trusted `run-plugin-tests.py` runner, so this is
`agent-dispatch`'s own application-code fix to make, not a `baseline.py`
tooling problem, and stays out of this effort's own scope.

**Not yet done:** watching a real promotion land `agent-worktrees`' own
baseline on `main`; `agent-dispatch`'s own fix (separate domain, tracked
issue); Phase 2 (nearest-ancestor resolution) hasn't started.

### 2026-10-02 (yet later still) — Phase 1: enroll `agent-bridge`
Operator chose `agent-bridge` next, out of the 3 remaining plugins.

By far the largest plugin enrolled so far: 178 test files, 2993 collected
tests, 8 sub-suites under the trusted `run-plugin-tests.py` runner's own
chunking. A genuine proof point for the chunking fix the previous entry
landed, not just a repeat of an already-small suite. Enrolled with the
same generalized pilot-plugin-list pattern; no further code changes
needed beyond the two-line list addition.

**Verified directly:** `baseline.py` run against `agent-bridge`'s real
suite collects a clean baseline end to end (2993 tests, 142 covered
files) in one run, chunked into ~8 sequential pytest processes
automatically. Fast unit suite (32 passed) re-confirmed unaffected.

Phase 1 now covers 7 of 9 plugins. **Not yet done:** watching a real
promotion land `agent-bridge`'s baseline on `main`; the operator's next
choice of which plugin(s) to enroll from the 2 still remaining
(`agent-dispatch`, `agent-worktrees`).

### 2026-10-02 (yet later) — `baseline.py` chunking fix; `agent-mcp` enrolled
Operator asked to fix the scaling limitation from the previous entry
directly (unblock `agent-mcp`) rather than continuing to the next
unrelated plugin.

**Root-caused the real failure mode precisely, not just the process-count
theory from the previous entry.** Teaching `baseline.py` to chunk a large
suite the same way `run-plugin-tests.py` does (`_plan_chunks`, splitting
into sequential 25-file groups via the same `partition` helper, one pytest
process per chunk, results merged via a new `_merge_chunk_results` --
durations union plus a genuine per-line test-name union for any source
file touched by tests from more than one chunk) changed the failure from a
silent crash into real, readable pytest output -- which showed the actual
cause: `OSError: AF_UNIX path too long`. `agent-mcp`'s own real-socket
cutover tests create Unix-domain sockets under pytest's `tmp_path`
fixture, and without an explicit `--basetemp`, that fixture nests under
whatever `TMPDIR` `_subprocess_env`'s `isolated_environment` redirects to
-- deep enough (`.../sandbox/tmp/pytest-of-<user>/pytest-<n>/...`) to
exceed `AF_UNIX`'s 108-byte `sun_path` limit. `run-plugin-tests.py` never
hits this because it always passes its own short, explicit `--basetemp`;
`baseline.py` never did. Added the same explicit `--basetemp` (one per
chunk, directly under the ephemeral collection tempdir, well short of the
limit) to the driver script.

**Verified directly**, not just reasoned about: `agent-mcp`'s full suite
(634 tests, 48 covered files) now collects a clean baseline end to end.
Re-ran every already-enrolled plugin (`agent-ssh`, `agent-codespaces`,
`agent-containers`, `agent-vault`, `agent-logger`) after the change and
all five still collect cleanly -- the single-chunk path for a suite within
the limit is bit-for-bit the same invocation as before chunking existed.
Added fast, mocked unit tests for `_plan_chunks` (small suite stays
unsplit; a single file stays unsplit; a large suite splits into the
expected bounded groups) and `_merge_chunk_results` (duration union;
coverage-line union across chunks), plus a new real, opt-in
end-to-end integration test that forces a tiny `max_files_per_chunk` and
confirms a shared module's coverage is genuinely attributed to tests from
every chunk, not just whichever one happened to run first.

`agent-mcp` is now enrolled in this same change -- the whole point of the
fix. Phase 1 now covers 6 of 9 plugins total.

**Not yet done:** watching a real promotion land `agent-mcp`'s baseline
(and the still-pending `agent-vault`/`agent-logger` ones) on `main`; the
operator's next choice of which plugin(s) to enroll from the 3 still
remaining (`agent-bridge`, `agent-dispatch`, `agent-worktrees`).

### 2026-10-02 (later still) — Phase 1: enroll `agent-vault` and `agent-logger`; `agent-mcp` deferred
Operator chose the next three Phase 1 plugins out of the 6 remaining:
`agent-mcp`, `agent-vault`, `agent-logger`.

**`agent-vault` and `agent-logger` enrolled cleanly** -- same generalized
wiring pattern as the previous round, just appended to the shared
pilot-plugin list and the `promote` job's matching enrolled-baseline gate.
`baseline.py` run directly against both real suites produces a clean
baseline end to end.

**`agent-mcp` deliberately NOT enrolled this round -- a real, reproducible
scaling limitation, not a transient flake.** Running `baseline.py` against
its full 54-file suite fails consistently with a bare `exit 1` and almost
no captured output (one stray `agent_mcp.watchdog` log line, no pytest
summary at all) -- while `python tools/run-plugin-tests.py agent-mcp`
(the trusted runner) passes cleanly, 625 tests across 3 sub-suites.
Isolated `plugins/agent-mcp/tests/test_watchdog.py` alone through
`baseline.py` and confirmed it passes fine by itself, and reproduced the
full-suite failure twice more to rule out a one-off. Root cause (reasoned
from the evidence, not confirmed via a crash dump): `baseline.py` collects
an entire plugin's suite as **one** pytest process, while
`run-plugin-tests.py` always chunks a suite into sequential 25-file
sub-suites specifically to bound per-process resource usage (see its own
`Limits.max_processes` containment). `agent-mcp` ships real
process/watchdog-management tests that likely spawn more live
subprocesses/threads than most other plugins; running its entire suite
unchunked in one process plausibly exceeds a process-count ceiling the
trusted runner's own chunking exists to avoid, abruptly killing the
process before it can flush its own summary. Fixing this properly means
teaching `baseline.py` to chunk a large suite the same way (and merge
coverage data across chunks) -- a real design change, not a one-line fix
like the `agent-containers` environment gap two entries up, so it's
tracked as a follow-up rather than rushed into this enrollment PR.

**Not yet done:** the `baseline.py` chunking fix for large suites (tracked
externally, in whichever adopter's own issue tracker this effort's
downstream consumers use); watching a real promotion land both new
baselines on `main`; and the operator's next choice of which plugin(s) to
enroll from the 4 still remaining (`agent-bridge`, `agent-dispatch`,
`agent-mcp` itself once the chunking fix lands, `agent-worktrees`).

### 2026-10-02 (later) — Phase 1: enroll `agent-codespaces` and `agent-containers`
Operator chose the next two Phase 1 plugins to wire ("incrementally work
towards full coverage across all plugins"), out of the 8 remaining in
`validate-and-promote.yml`'s own `full` matrix.

**Generalized the per-plugin wiring** rather than copy-pasting a third
near-identical `if: matrix.plugin == '...'` block: the `full` job's
Coverage-baseline/Upload-coverage-baseline steps now gate on
`contains(fromJSON('["agent-ssh","agent-codespaces","agent-containers"]'),
matrix.plugin)`, and `--cov-source` is derived from `matrix.plugin` itself
(`plugins/<plugin>/src/<plugin-with-underscores>` -- every enrolled plugin's
own `src/` package name follows this exact rule) instead of a hardcoded
per-plugin path. The `promote` job's enrolled-baseline hard-gate list
(`for plugin in agent-ssh agent-codespaces agent-containers`) stays in the
same commit, per the existing "keep both lists in sync" contract. Scaling to
a 4th+ plugin going forward only ever touches these two lists.

**Found a real environment-isolation gap enrolling `agent-containers`:**
running `baseline.py` against its real suite failed one test
(`test_profile_spec_can_describe_project_scoped_picker_source`) that passes
cleanly under the trusted `run-plugin-tests.py` runner. Root-caused (not
guessed): this machine's ambient `AGENT_RT_ROOT` env var leaked into
`baseline.py`'s ephemeral collection subprocess (`_subprocess_env` copied
`os.environ` wholesale), and `agent-containers`' own `provider_ssh.py` reads
`AGENT_RT_ROOT` ahead of the test's monkeypatched `RUNTIME_DIR` substitute --
`run-plugin-tests.py`'s own `isolated_environment` already scrubs this exact
var (and others) for the trusted path, `baseline.py` never did. Exported
`plugin_test_containment.py`'s existing `_ALWAYS_SCRUB_NAMES` as a public
`ALWAYS_SCRUB_NAMES` and reused it in `_subprocess_env`, so a baseline is
only ever collected under the same containment the real validation gate
already guarantees -- re-ran `baseline.py` against `agent-containers` after
the fix and it now collects cleanly, with no regression against the
existing `agent-ssh` pilot. Added a fast, mocked regression test
(`test_subprocess_env_scrubs_ambient_containment_variables`) asserting
every `ALWAYS_SCRUB_NAMES` entry is absent from `_subprocess_env`'s own
built env, so this exact containment gap can't regress unseen.

**Verified directly**, not just by code reading: `baseline.py` run against
both new plugins' real test suites (a local `--measured-commit` smoke run
of each) now produces a clean baseline end to end, and
`tools/test_coverage_guided_selection.py` +
`tools/test_plugin_test_containment.py` (CI's own exact invocation of both)
pass unchanged.

**Not yet done:** watching a real promotion after this wiring lands and
confirming `.github/coverage-baselines/agent-codespaces.json` and
`agent-containers.json` actually appear on `main` (same sequencing caveat
as `agent-ssh`'s own entry below), and the operator's next choice of which
plugin(s) to enroll after these two.

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

**Review response (same PR):** three real findings addressed before
merge. (1) A missing/transiently-failed collection could silently promote
with no baseline recorded at all despite `agent-ssh` being "enrolled" --
added a hard, fail-loud "Verify enrolled coverage baselines were actually
collected" step in the `promote` job (the collection step itself stays
`continue-on-error` so a coverage-tool bug never blocks a real release
`run-plugin-tests.py` already validated; this new step is the actual
enforcement point). (2) A more serious correctness bug:
`_write_coverage_baselines_into_scratch` only overlaid this run's own
freshly-collected files, never seeding what already existed on `main` --
since `scratch` starts from a plain `dev` checkout with no baselines at
all, any promotion round with no fresh collection for an already-published
plugin would have silently dropped its last-known-good baseline entirely
(the same regression class `_seed_versions_from_main` already exists to
prevent for version numbers). Added `_seed_coverage_baselines_from_main`,
called before the fresh overlay, plus a regression test
(`test_promote_preserves_an_existing_main_baseline_when_no_fresh_one_is_collected`).
(3) This Journal entry's own "not yet done" note (see below) originally
implied this PR's own merge could validate the real round trip -- corrected
to name the actual sequencing (workflow YAML resolves from `main`, not
`dev`; a baseline-only change is a deliberate no-op) rather than overclaim.

**Not yet done** (left for the next increment): watching this actually land
through a live `validate-and-promote.yml` run and confirming a real
baseline reaches `main`. This needs more than just this PR merging to
`dev` -- important timing/sequencing the first version of this note
glossed over (review finding, PR #4902):
`repository_dispatch`/`workflow_run`-triggered runs of this workflow always
resolve its own YAML from `main`, not `dev` (see this file's own top-of-file
comment on why), so the promotion that first lands THIS wiring still runs
the *old* workflow and cannot use it. Only the **next** promotion after
that -- once this wiring itself is live on `main` -- can actually attempt
collection. And that next promotion must carry a **genuine new `dev`
content change**: a coverage-baseline update alone is deliberately excluded
from the no-op-promotion comparison (see
`test_promote_a_second_time_with_only_a_coverage_baseline_change_is_a_no_op`),
so a promotion with nothing else to promote stays a no-op and checks in no
baseline either. The real validation step is watching the first ordinary
promotion *after* this wiring reaches `main` and confirming
`.github/coverage-baselines/agent-ssh.json` actually appears in that
commit -- not assuming this PR's own merge proves the round trip.

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
