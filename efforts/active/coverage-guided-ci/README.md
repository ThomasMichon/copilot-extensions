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
- [ ] Execute the existing selector for the `agent-dispatch` required PR lane,
      with a bounded cross-cutting smoke floor and the unchanged twelve-minute
      [`ci.yml`](../../../.github/workflows/ci.yml) job deadline.
      Proposal and acceptance gates:
      [`dispatch-pr-selection.md`](dispatch-pr-selection.md).
      This bounded slice does not activate the unrelated
      [Phase 4 shadow consumer](#phase-4--rollout-replace-agent-worktrees-collect-only-tier)
      or take over standalone Fleet Manager baseline enrollment
      ([#6783](https://github.com/ThomasMichon/copilot-extensions/pull/6783)).
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
- [ ] Investigate Windows `agent-worktrees` durations from rotation run
      37731754633 (tracking issue #5682). Preserve the 300-second sub-suite
      ceiling and behavioral coverage; target at least 20% headroom
      (240 seconds or less) in each sub-suite, and verify the entire
      plugin fits its aggregate budget. Collect individual setup/call
      durations on a hosted Windows runner, not just total job time.
      Initial local profiling: `test_pr_complete.py` took 150.61 seconds
      for 17 passing tests; claimant-guard tests took 56.14 seconds for
      18 tests, predominantly repeated Git-fixture setup. An immutable
      session seed with independent copied object stores reduces the
      combined complete/claimant/nudge subset from approximately 233
      seconds to 139 seconds, but the original 25-file group still exceeds
      300 seconds locally. This is progress, not completion; the large
      `test_pr_ops.py` remains a principal profiling target. Local timings
      are load-sensitive and must not be substituted for hosted evidence.
      Hosted candidate run 37739890830 confirms the aggregate budget is
      also binding: groups 1-6 took 77.01, 59.45, 156.05, 258.77,
      140.88, and 21.75 seconds. Group 7 then received only 170.531
      seconds of the 900-second plugin budget and timed out there.
      Group 4 alone misses the 240-second headroom target. A manually
      requested diagnostic profile may temporarily allow complete
      timing collection; scheduled/default runs keep their existing
      budgets. A diagnostic pass is never performance acceptance.
      Complete hosted diagnostic 37742776238 passed 7,425 tests with
      42 platform/explicit-tier skips. Its 12 pytest group timings were
      76.26, 58.46, 152.28, 241.44, 148.37, 20.59, 531.79,
      153.87, 88.37, 77.19, 137.72, and 28.24 seconds (1,714.58
      seconds total, excluding runner/setup overhead). This proves real
      behavioral success at that revision, not compliance with 900 seconds.
- [ ] Correct the rotation's scheduled-event branch condition: schedules
      execute on `main`, so requiring `github.ref == refs/heads/dev` skips
      the selection job. Scheduled runs should check out `dev` internally;
      manual runs should exercise their requested revision. Pin all matrix
      jobs to the exact selection checkout SHA and expose per-test durations
      and unbuffered progress so a timeout still leaves identifiable evidence.
      Rotation must remain internal to CI, with no settings-changing PRs.

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

### 2026-10-17 - Accelerate fresh registry parsing without caching authority
- A single projected historical retry spends5.44 seconds without any subprocess.
  Its Python profile performs814 machine-registry parses,983 total YAML parses
  and993 file opens. Pure-Python YAML contributes3.58 cumulative seconds to the
  7.74-second instrumented call; do not cache or omit the fresh reads.
- Follow the existing tracking parser's safe-backend pattern in the shared
  machine-transport file parser: prefer PyYAML's safe C loader when available,
  with its safe Python loader as fallback. The same instrumented case now takes
  4.74 seconds, with the814/983/993 parse/open counts unchanged. This is a
  measured local hot-path improvement, not a complete normal qualification.
- Run the full file-contract class against both safe backends. New cases retain
  aliases, merge keys, quoted scalars and UTF-8 values, then require subsequent
  file replacement, malformed/unsafe-tag failures and deletion to be observed.
  The complete shared library passes317 cases. All299 actual handoff,
  machine-identity and alias-slot consumer cases pass in206.47 seconds inclusive,
  retaining all real lease transfers and later authority-refusal cases.
- Local-cache copy tests reuse only an empty initialized Git store and copy all
  mutable state independently. A real commit and config/exclude mutations prove
  the seed and sibling unchanged; the complete file passes93 cases with4
  existing POSIX skips in39.73 seconds inclusive. Do not infer a whole-plugin
  gain from fixture reuse or compare grouped cumulative cost to this wall time.
- This is a below-altitude parser/fixture optimization with unchanged public
  behavior, fresh path/identity resolution and budgets. The shared library
  README and TESTING document it; the existing transitive multi-plugin
  changefile carries the release intent. No first-provision behavior changes,
  so no new provisioning clean-room lane applies. Current-head hosted normal
  execution, repeat, latest required CI, genuine review and actual merge remain
  owed before Phase4.
- The complete seventh25-file group now passes716 cases with5 existing skips
  in272.55 pytest seconds,282.05 including cleanup. This is below the existing
  300-second deadline but still misses the240-second margin target; it is not
  whole-plugin qualification.
- Diagnose the missing current CI rather than assuming queue delay: the PR is
  conflicting against advancing dev. Protect all eighteen owned commits with
  a backup, consolidate their diff after successive replay conflicts, and
  reconcile once onto dev027887e06949. Preserve the incoming stamp/provision
  declaration helper, native Bash environment and quoted-argument proof along
  with the owned retention guard and native-launcher refusals. Regenerate the
  one merged Bridge human-output declaration from canonical source.
  Range-diff retains the owned functional changes; the only disappearing
  vision hunk is now already present upstream.
- All13 install contracts, whole-repository F/E9, module ceilings and
  transitive changefile coverage pass. The complete reconciled installer-guard
  and generator selection passes235 cases with14 existing platform skips in
  87.56 seconds. Predecessor894's hosted normal run remains in progress and is
  retained as predecessor evidence only; fresh-head required CI and repeated
  normal acceptance still precede merge.

### 2026-10-17 - Keep cold installer cleanup within the existing lifecycle budget
- The fifth normal group reproduces a cold direct-hook failure: stamping
  reports success but the lifecycle exits124 while its finalizer searches for
  the absent optional construction-cleanup command. A timestamped isolated
  reproduction locates the stall at command discovery, not source admission
  or the existing ten-second lifecycle deadline.
- Probe the loaded Function provider in all nine affected PowerShell installers
  instead. Native regressions execute all ten actual guards under both installed
  hosts with absent, loaded and autoloadable functions. A poisoned module and
  the old command-discovery negative control prove the import side effect; the
  existing same-host cleanup-failure case still verifies install-lock release.
  The complete finalizer suite passes22 cases with14 existing POSIX skips.
- The complete hook file passes132 cases in26.92 seconds including cleanup, but
  its cold case remains close to the existing lifecycle limit. A source inventory
  shows360 nested bytecode files among1006 stamp-eligible files. Extend the
  already-intended cache exclusion into recursive source copying; native copy
  checks retain binary/hidden/literal-path/empty-directory coverage on both hosts.
  The final native cold hook, stamp, bootstrap and both copy-host selection
  passes6 cases in39.34 seconds including cleanup; this is correctness evidence,
  not proof of meaningful cold-path latency reduction.
- This is a below-altitude repair of the existing stamp and cleanup contracts.
  TESTING and the install contract describe the boundary; installer source
  declarations are regenerated, all13 install contracts pass, and the existing
  multi-plugin changefile covers each affected plugin. Native fresh-root stamp
  and poisoned-module regressions supply the lightweight first-touch check;
  repeated complete exact-head qualification and fresh Copilot review remain
  blocking, with no deadline, resource, worker-count or skip changes.
- Subsequent normal-group diagnostics pass806 cases with6 existing skips in
  141.60 seconds and575 cases with21 skips in198.48 seconds, then reach the
  unchanged300-second limit in group7. A cost-attributed retry identifies native
  launch and real Git-lease cases, not a newly failing assertion. Inherited
  PowerShell module-search roots still reach outside the isolated home and
  cause expensive unrelated discovery. Give the runner a sandbox module root;
  both actual hosts retain core commands and admit a synthetic external module
  only through explicit host-state opt-in. The environment/native guard
  selection passes12 cases. Four affected real launcher cases now pass in
  15.00 seconds inclusive; complete group timing remains open.
- Creation-tail repair now shares only initialized immutable Git input and
  copies each anchor independently, writing fresh empty topology rather than
  discovering unrelated overlays. All55 prior file cases pass in83.73 seconds
  inclusive. The new native seed/config/ref/object isolation guard passes in
  10.16 seconds inclusive; the complete normal group and aggregate budgets remain
  open. With isolated modules and native Git, group7 reaches about96 percent
  before its unchanged300-second deadline, still short of the240-second target.
  Its remaining major costs are the real historical handoff/lease contracts and
  repeated local-cache refresh contracts. Do not treat this as qualification.

### 2026-10-17 - Keep reducing normal-group cost without waiving qualification
- The local complete normal attempt stops in its third25-file group at the
  unchanged300-second group deadline, after two passing groups. Repeating only
  that group with the hosted native-Git lookup also reaches the same deadline;
  a subsequent cost-attributed run completes692 cases and5 existing skips in
  288.84 pytest seconds,304.03 including cleanup. It does not satisfy the
  240-second margin target and is not a complete900-second qualification.
- Case-level cost attributes142.23 seconds to creation contracts,101.10 to
  creation-tail repair,43.41 to context resolution and34.82 to config, with
  additional fork/refspec costs. Reuse the existing actual published seed for
  six rerun-only creation contracts; keep first-publication/body/title tests
  fresh. Reuse an actual initialized anchor/worktree for context resolution,
  copy every mutable Git store, repair links with Git, and recreate registration
  and tracking. A new native mutation case verifies sibling refs/config/files
  and the complete seed bytes remain unchanged.
- Both complete touched files pass77 cases in81.83 seconds including cleanup.
  The complete25-file group passes693 cases and5 existing skips in288.43 pytest
  seconds,302.94 inclusive. This establishes correctness, not a meaningful
  aggregate performance gain or the240-second target. Keep the budget gate open
  and profile later normal groups instead of repeating this measurement.
- The focused slow fork/live-identity case also passes with actual Git calls;
  repeated URL/authority probes remain real, not replaced with cached decisions.
  No product behavior, provisioning contract, timeout, worker count or skip is
  changed. These are below-altitude fixture changes with TESTING documentation,
  so no new clean-room lane applies. Remove both temporary cost plugins.
- Publish exactee129abe3484 for the preceding handoff fixture change and request
  a genuine fresh Copilot review twice after bounded waits. Required CI39009136621
  and normal Windows rotation39009289939 remain queued. Cancel obsolete owned
  unstarted diagnostic/CI runs to release their concurrency lanes; do not claim
  stale-head checks or request acceptance from a diagnostic run.

### 2026-10-17 - Isolate non-repository handoff fixture discovery
- A real subprocess profile of one complete legacy offer/accept case shows
  144 offline Git HEAD probes taking14.782 seconds against three deliberately
  non-repository graph anchors. Do not add a production negative cache or assume
  that a missing `.git` excludes parent, bare or environment-selected repos.
- Instead, the graph fixture isolates only committed-config discovery for its
  three exact owned anchors. Every project config, machine topology and ledger
  read remains fresh; all other anchors delegate to the real resolver.
  Existing post-offer invalidation cases and actual Git lease CAS stay intact.
- All72 file cases pass with exactly two workers through the contained normal
  runner in99.92 seconds including cleanup. This includes all six real lease
  transfers and the post-offer authority refusals. Changed-code lint is clean.
  The change is test-only/below-altitude; it does not alter first provisioning
  and needs no new clean-room lane. TESTING documents the narrow boundary.
- Current-head required CI39002010809 and diagnostic rotation39007545197 remain
  queued without an assigned runner. Neither supplies a passing gate. A complete
  local normal run is in progress with unchanged time/resource limits; repeated
  exact-head Windows qualification and a fresh real review remain open.

### 2026-10-16 - Reconcile reviewed retention safeguards before current PR gates
- The operator confirms preserving the merged retention placement/resource
  safeguards from #6845 with this change's distinct private setuptools metadata
  correction. Rebase the twelve owned commits onto reviewed dev2a0161ec51cf,
  keeping a pre-rebase backup. Preserve the incoming installer-process receipt
  fixtures, actual installed-shell owner tests and candidate promotion phase;
  retain the owned stdio, seed, macOS failure and native stat assertions.
- Keep canonical transitive library attribution and extended-path operations.
  The private retained `setup.cfg` uses the same native path adapter; every
  shipped runtime receives the regenerated canonical support bytes. Preserve
  the reviewed repository-local plugin-registration vision rather than carrying
  a stale deletion from the long-lived branch.
- The combined editable/retention/resource regression selection passes33
  cases, including actual setuptools with authored and absent configuration,
  in117.97 seconds. The test environment initially lacked setuptools; provision
  declared backend prerequisites through seasoning, then restore and run
  serially to avoid concurrent changes to the same venv.
- Promotion/workflow/support contracts pass104 cases in30.88 seconds. Native
  seed exclusion and nonregular-input refusal pass18 cases in51.00 seconds
  including cleanup with exactly two workers. The complete installed-owner file
  passes20 cases with17 existing platform skips in106.55 seconds inclusive.
  Both support synchronization checks, changed-code lint and module caps pass.
- The preceding exact57baad hosted Windows companion qualification38734761785
  completes successfully: attribution122 passes and3 existing skips in128.08
  seconds inclusive, logger223.77 and mcp60.70. These close the four attribution
  failures but do not close the worktrees900-second budget or repeat gate.
  Fresh reconciled-head CI, real review and complete normal performance evidence
  remain owed before merge; no superseded check authorizes the current head.

### 2026-10-15 - Current required CI is green; normal rotation exposes attribution gaps
- Publish exact2d3b0fa2f84e after a clean11-commit rebase onto reviewed
  dev00000eff04; every owned patch remains identical in range-diff.
  Required CI38727464481 passes, including all four previously failing
  jobs, the native macOS lanes and Windows artifact checks.
- Normal exact-head Windows rotation38728631199 still exhausts900 seconds:
  worktrees groups1-9 pass61.52/20.25/122.94/121.67/78.28/83.67/68.65/
  57.59/107.97 seconds; group10 of17 has161.688 seconds remaining and the
  final cleanup-inclusive result is907.97. No complete normal pass or
  repeated240-second group margin is established. Index passes569.41
  seconds under its existing policy; its352.25-second third group is not
  evidence of the worktrees240-second target.
- The same rotation exposes four attribution failures: Unicode repository
  gating emits an empty object, and three Bash guidance fixtures fail to
  create their target. Reproduce the native Unicode failure before editing.
  Actual Git emits UTF-8, but headless PowerShell captures it through OEM437,
  corrupting non-ASCII code points before literal path validation. Scope
  console UTF-8 to the policy entrypoint and restore it in `finally`;
  retain every original input/path check. The existing Unicode case now
  exercises both native PowerShell7 and5.1 without new skips.
- Bash fixtures need shell-native paths and both HOME and USERPROFILE to
  select the same isolated home on Windows. Their cold path now forwards
  to the actual test interpreter instead of an ambient Windows Store alias.
  The warm no-Python case launches actual tools from their real locations
  on Windows so DLL lookup is preserved; its PATH still contains no Python.
  No Bash production fallback, cached authority or safety check is changed.
- Unit/simple native tier: both Unicode hosts pass through the contained
  runner in18.00 seconds including cleanup. With checksum-verified jq1.8.1
  and the resolved Git Bash toolchain, all three actual Bash contracts pass
  in33.17 seconds including cleanup, rather than skipping prerequisites.
  These changes do not affect first-provision, so a separate clean-room
  installer lane is inapplicable. Full local attribution and a fresh hosted
  exact-head attribution run remain open.
- The attempted complete local attribution portfolio retains its normal
  300-second group limit and times out at301.72 seconds including cleanup;
  no functional failure is reported before interruption. This is not a
  passing complete suite. Use a real hosted complete attribution rotation
  to qualify the four fixes; the independent worktrees900-second gate stays
  open regardless of that result.
- Separate local evidence remains unresolved: exhaustive rebase-file execution
  exceeds300 seconds and reports a transient Windows sharing violation during
  cleanup; its sandbox is subsequently gone and no owned descendant survives.
  Broad codespaces native installer tests exceed300 seconds, while the exact
  changed release-materialization contract passes24.31 seconds and the full
  Linux codespaces job passes in required CI. Neither local failure is waived
  or relabeled as a passing complete suite.

### 2026-10-15 - Reuse actual published snapshot inputs for rebase contracts
- Snapshot-scheme replay and legacy checked-out-head fixtures now copy the
  existing immutable real-published seed instead of re-running initial
  publication in every test. Each still owns separate object stores,
  remotes, tracking and worktree links. Actual rebase, ancestry/raw-object
  proof, lease publication, legacy checkout and hook rejection are unchanged;
  no authority read, assertion, timeout or test is removed.
- Unit/simple end-to-end tier: the same four native contracts pass through
  the canonical contained runner with exactly two pytest workers. Baseline
  elapsed143.34 seconds including cleanup; first candidate120.78; repeat
  candidate107.01 (pytest95.70). Repeat call durations30.08/25.81/17.18,
  with shared per-worker setup21.25. This is native isolated Git behavior,
  not a mocked subprocess or fresh-environment installation change.
- Clean-room tier is not applicable to this fixture-only optimization.
  Hosted external tier and the complete file/group/portfolio budget remain
  open: these four selected cases do not establish a complete900-second
  Windows portfolio or240-second group margin. The earlier full-file
  timeout still requires a complete candidate run; don't label it resolved
  on the strength of this slice.

### 2026-10-15 - Repair first-touch policy fanout and required CI integration
- Required CI38719024425 reports four failing jobs: guards, Windows artifact
  workflow contracts, agent-pull-requests and agent-codespaces. The installer
  failures are stale shipped policy copies after the canonical first-touch
  correction, not invalid hashes; regenerate all13 runtime consumers with
  `sync-versioned-runtime.py` and retain materialization's conflict refusal.
  A repository regression now compares every runtime's two support files
  against canonical bytes, including non-shell-engine consumers.
- The owned macOS runner lane now provisions system pytest and pyyaml through
  the canonical seasoning adapter before running the same two test files.
  Extend the existing workflow guard to require that provisioning order.
  Move the unchanged runtime-retention assertions into the existing shared
  guard helper; the original test seam remains available and every module
  satisfies the unchanged1000-line cap.
- Unit tier:382 policy, installer-engine, install-contract and workflow cases
  pass in395.82 seconds; the new support-copy regression and full sync suite
  pass36 cases in9.96 seconds. Both canonical fanout checks, install-contract,
  module-size, changed-code F/E9 and whitespace checks pass.
- Simple end-to-end tier: real preview release materialization of both
  previously failing consumers succeeds with local copies of both engines,
  canonical support assets and no skipped materialization. The initial
  Windows preview destination exceeded MAX_PATH; repeat with the same
  task-owned destination's extended-path form succeeds without a code change.
  Clean-room first-touch evidence remains the preceding journal entry.
  Native macOS execution and latest required hosted CI remain open; local
  workflow contracts are not a substitute for those external lanes.

### 2026-10-15 - Repair seasoned editable first-touch after base reconciliation
- Rebase the eight owned commits onto reviewed dev080963723459, preserving
  the incoming Windows installation-context lane, owned macOS lane and
  stricter explicit test-path admission. Range-diff retains the seven
  remaining functional patches unchanged. The runner/context selection
  consumers pass126 cases with two existing platform skips.
- The new seasoned installer exposes a real Windows first-touch failure:
  the retained setuptools project references the original live `src`, but
  `egg_info` defaults its output there and follows a long relative source
  path from the OS temporary namespace. Real setuptools fails to build
  editable metadata; the earlier synthetic-backend test does not exercise
  either behavior.
- Keep the retention namespace on Windows under the editable inputs'
  deterministic common parent, retaining the single-volume refusal.
  Override only the retained `setup.cfg`'s `egg_info.egg_base` to `.`,
  preserving other authored sections and options. Live Python source
  binding, resolver-equivalence admission, age filtering and compatibility
  mode remain unchanged; original metadata/configuration is never rewritten.
- Canonical `run-plugin-tests.py agent-worktrees --prepare-only --reinstall`
  now passes with the production correction, rebuilding the actual owned
  venv through normal seasoning. Two first-touch tests against real
  setuptools pass, with and without authored `setup.cfg`; both prove
  private metadata cleanup, no source `egg-info`, unchanged original
  configuration, retained-input mutation safety, editable provenance and
  subsequent live imports/console entry points.
- The complete seasoning-policy suite passes153 cases in232.52 seconds,
  with two workers and unchanged process deadlines. A preceding run using
  unnecessary per-fixture external-wheel caches times out one actual
  backend build; use UV's normal configured cache/feed instead while still
  requiring fresh target venvs and full seasoned isolated-build resolution.
  Changed-code F/E9 lint and whitespace checks pass.
- The simple end-to-end and fresh-environment tiers are exercised locally.
  Exact-head hosted Windows validation and the normal full-matrix/repeated
  900/240-second completion gate remain open; this installer repair is not
  evidence that the performance gap is closed.

### 2026-10-15 - Complete the reconciled functional matrix; normal budget remains open
- All seven owned commits rebase onto reviewed dev2182afbff462, retaining a
  pre-rebase backup. The only conflict joins the independent assignment-
  reconnect and native-installer testing-guide sections; both survive.
  Range-diff confirms no functional change to those commits. Published
  head1c01ab6ef95c passes the rebased native catalog, strict timeout,
  repaired encoding and recovered-copy consumers in82.52 seconds including
  cleanup; the cold catalog takes4.04 seconds under15.
- Complete exact-head diagnostic38694888835 passes every group and both
  companion portfolios. Worktrees takes1555.36 seconds including cleanup.
  Its16 pytest group durations are68.21,22.24,145.50,112.93,81.16,85.19,
  82.29,54.66,114.19,290.62,162.15,53.20,77.39,63.95,69.05 and37.19
  seconds. The real cold catalog passes in2.81 seconds under15. Newly
  integrated late consumers are now exercised, but group10 still exceeds
  the240-second headroom target and this extended run is not normal acceptance.
- Same-head complete normal38698989021 passes both companions and groups1-9,
  then exhausts900 seconds during group10/16 at908.33 seconds including
  cleanup. Completed group times are63.36,20.92,132.44,129.94,86.68,76.30,
  69.44,65.75 and117.54 seconds. Group10 receives118.953 seconds.
  The cold catalog passes in2.54 seconds under15. No full normal pass,
  repeat, changed ceiling or flake acceptance is claimed.
- Required current-head CI38694600551 and the latest metadata-dependent
  CI38695206758 both pass. Genuine re-review is requested after the automatic
  ten-minute wait; the latest actual Copilot verdict remains onb004.
  Request acceptance does not substitute for a current review.
- Current group10 profiling still attributes most time to actual recovery,
  rewrite and rebase calls, not fixture setup. A read-only native experiment
  confirms Git's `--log-size` frames the exact custom-formatted author/message
  byte output, including its unstripped trailing bytes. That may permit one
  fresh native comparison per pair without delimiter ambiguity or a growing
  whole-history metadata cache. Compatibility, exact formatting and real
  consumer cost must be proved before adopting it; no implementation or
  performance claim follows from that framing experiment alone.
- The bounded parity probe rejects that shortcut: signed SHA-1 commits with
  `log.showSignature=true` emit signature diagnostics outside the size frames.
  Fifty-six remaining native SHA-1/SHA-256 cases preserve exact output across
  arbitrary NUL/non-UTF8 bytes, notes, styling, replacement refs and missing
  trailing newlines; eight signature cases do not admit the simple frame
  parser. Keep the original per-pair author/message commands unchanged.
- Native clean/conflict publication profiling identifies duplicate parent-range
  reads shared by patch-ID generation and tree reconstruction. Read each of
  those same pinned ranges once per verification, retaining every ancestry,
  merge-tree, identity, lease and publication check. Identical three-consumer
  baseline/candidate runs both pass; the candidate removes eight duplicate
  native range reads across four decisions. Their total128.90/108.39-second
  times contain scheduling noise and are not a claimed20-second improvement.
  The removed native probes account for1.04 seconds in the baseline.
- All29 focused raw-object, malformed-tree, timeout, identity and native
  conflict consumers pass. Final snapshot/refspec public replay checks prove
  both consumers share one fresh row pair and subsequent actual publication
  reads both again; all four cases pass in158.73 seconds including cleanup,
  with exactly two workers and unchanged ceilings. The earlier combined
  two-file selection exhausts300 seconds and reports a Windows cleanup sharing
  violation whose cause remains unclassified; its sandbox is subsequently gone and no owned
  descendant remains. A complementary public selection passes five cases with
  one existing contained-mode skip in243.52 seconds inclusive. These results
  do not close the complete hosted900/240-second or repeatability gates.

### 2026-10-14 - Reconcile batch evidence injection and reuse real recovery inputs
- Exactbed5 diagnostic38685956800 reaches group10/16 and fails at1246.62
  seconds including cleanup. Groups1-9 pass in86.41,24.50,174.81,132.30,
  81.51,77.53,70.35,58.45 and116.51 pytest seconds. The traced cold catalog
  passes in4.27 seconds under15, which does not classify the earlier deadline
  failures or establish repeatability. Both companion portfolios pass.
- Group10 reports669 passes,16 existing skips and one failure in386.06
  seconds. The newly integrated `synced-encoding` evidence tripwire patches
  the old per-ref commit resolver, while this change's real resolver now uses
  a NUL-input native batch. Its intended UnicodeEncodeError is never injected,
  so the actual intact proof correctly selects settlement rather than rewrite.
  The exact case reproduces locally. Inject the same exception at the real
  batch boundary, match the complete requested ref and require one probe at the
  first preview. Do not relax production proof or consent rules.
- Before fixture reuse, all nine defect variants pass within a contained
  222.66-second run and all six mutation/non-commit variants pass in118.56
  seconds including cleanup. Exactly two workers and the existing30/300/900
  bounds, including the files' existing120-second item marks, remain unchanged.
- Actual setup instrumentation measures9.406 seconds and49 Git calls building
  the recovered input, plus6.094 seconds and32 calls building its backed-sync
  input. These are construction-only operations, not the recovery decision
  under test. Reuse the already-existing independently copied recovery fixture
  for advanced-head consumers and construct genuine backed inputs, with and
  without a saved base, through the same success-only run-local seed protocol.
  Every case still executes its own previews, fresh proof checks, sign-off,
  retained refs, native push/CAS, audit, bookkeeping and injected races.
- The complete legacy recovery file passes55 cases in216.94 pytest seconds.
  All23 existing advanced-head cases and both backed-input isolation cases
  pass in the complete file run; a new non-backed isolation case initially
  assumes a checkpoint and fast-forward publication that this intentionally
  rewritten input does not have. Correct that test to require original
  checkpoint absence and use the exact observed remote lease for its
  disposable-copy mutation. No recovery behavior is changed.
- All15 imperfect-evidence cases pass after input reuse. Final native
  recovered/backed/base-backed isolation plus the complete seed-cache file
  pass16 cases in63.62 seconds including cleanup, covering actual cross-process
  exclusion, interrupted readiness, independent indexes/refs/tracking/evidence,
  native publication and unchanged complete seed bytes. The three recovery
  files'96 cases are covered across those bounded complete/complementary runs.
  Lint and whitespace checks pass. These are correctness and setup-cost
  results, not whole-portfolio hosted timing acceptance.
- The new head has no required CI because the live dev base has advanced and
  GitHub reports this PR conflicted. Retain a backup and reconcile that base
  before seeking current CI/review. Oldb351 green checks cannot certifybed5.
  Complete current-head normal execution, repeat, <=240-second group margin,
  genuine review and actual merge remain owed before Phase4.

### 2026-10-14 - Capture native catalog deadline phases without relaxing the gate
- Diagnostic38682040821 reports only the stdout-reader deadline; it does not
  expose partial stderr or identify the expensive hosted phase. Three bounded
  local fresh-profile observations take3.422,3.016 and2.437 seconds, with
  discovery plus launcher/helper taking1.812,1.500 and0.937 seconds.
  These measurements do not classify the hosted15-second failure.
- The generated PowerShell binding emitter now uses the existing opt-in
  `COPILOT_EXTENSIONS_BOOT_TRACE` switch for stderr-only phase timestamps.
  Mark discovery, launcher entry, Python bootstrap entry, helper completion,
  and completed binding. Do not cache authority, provision, retry a failed
  helper, alter native invocation, or extend a deadline.
- The real cold parity test enables tracing and reports partial stderr before
  re-raising its original15-second TimeoutExpired. Its timeout tripwire proves
  one invocation and the same exception, not a passing fallback or retry.
  Native tests retain complete stdout equality and read-only/default-quiet
  checks. Targeted PowerShell7 contracts pass9 cases with two existing skips
  in27.96 seconds; PowerShell5.1 passes8 with the same two existing skips
  in27.35 seconds. Both exact cold native shells emit valid JSON and the
  Python-entry marker under15 seconds (4.125 and3.157 seconds locally).
  All13 generated manifests remain synchronized.
- Required metadata CI38682282806 completes successfully onb3511d2a2e.
  This does not clear the incomplete hosted portfolio, performance repeats,
  or absent current-head Copilot verdict. The diagnostic change must earn
  new exact-head evidence; phase classification and real cost repair remain
  owed before complete normal acceptance or Phase4.

### 2026-10-14 - Reconcile strict promotion coverage with the owned macOS lane
- The current-head complete diagnostic38682040821 fails at468.83 seconds
  including cleanup in group5/16: the real cold catalog emitter again exceeds
  its unchanged15-second bound. Group5 reports864 passes,2 existing skips and
  one failure in83.63 seconds; groups1-4 pass in68.95,21.77,141.07 and131.79
  pytest seconds, and both companions pass. Two normal-run catalog passes do
  not establish repeatability. The single-Python cost reduction is real but
  has not resolved this deadline failure; phase-level cold native evidence is
  still required before another speculative repair or aggregate claim.
- Publishedb3511d2a2e complete normal run38679652383 passes both companions
  and groups1-9, then fails the unchanged900-second aggregate at901.28 seconds
  including cleanup during group10/16. Completed group pytest durations are
  107.75,24.48,163.03,133.30,81.63,87.43,79.21,66.33 and117.55 seconds.
  Group10 starts with about18 seconds remaining. The real cold catalog parity
  test passes in5.87 seconds under15, and the nonregular-output fixture no longer
  fails. This is not complete normal acceptance or a repeat.
  Current-head CI38679635570 remains queued at the last check, not green.
  Genuine current-head Copilot review is requested after the automatic wait;
  request acceptance is not a verdict, and the latest actual review remains
  the olderb004 review.
- Current-head CI38674089531 fails only `guards + lint` and its dependent
  required PR gate. Its merge snapshot includes the newly merged promotion
  verification contract from#6697: every PR-required job must also be required
  by the immutable promotion aggregate. This task's existing `test-runner-macos`
  lane is present in the PR aggregate but absent from the incoming promotion
  aggregate. This is a coupled integration defect, not a waiver or a reason to
  supersede another contributor's open PR.
- Retain the publisheda9e16be665 backup and rebase the four owned commits onto
  current reviewed dev9b399fff72. All four apply cleanly, retaining the incoming
  recovery-head preview and promotion-verification changes. Intermediate head
  35e5c7359b is not published or accepted performance evidence.
- Require the existing macOS lane in the promotion aggregate without changing
  its native runner, item limits, five-minute job bound or ordinary/manual
  selection. Extend the real aggregate-script tripwire so isolated macOS
  skipped/failed/cancelled results refuse even when every other required job is
  green. The complete promotion-verification file passes66 cases in29.02
  seconds under the30-second item bound; all13 generated payload manifests
  remain in sync. TESTING documents the strict aggregate behavior.
- These are below-altitude reconciliation changes preserving intended native
  coverage, not new merge authority or a relaxed CI/promotion gate. Exact-head
  normal and repeated hosted acceptance, current CI and review remain owed.
- After reconciliation, both repaired hosted failure families pass all six
  cases in34.89 seconds with exactly two workers; the cold emitter takes9.74
  seconds under15. A broader advanced-head/imperfect-evidence selection
  instead loses two120-second-bounded workers and is stopped without a complete
  result. That local failure is unresolved, not a passing recovery proof; the
  next normal hosted run must exercise the newly integrated consumers too.

### 2026-10-14 - Preserve native stat attributes in the nonregular-input fixture
- Exact-head normal run38674105042 validates726d07c431 with exactly two
  workers and unchanged normal bounds. Both companions and groups1-6 pass.
  Their pytest durations are86.83,24.20,161.07,135.08,79.38 and80.75 seconds.
  The real cold catalog parity test now passes in5.46 seconds under15; that
  closes this run's catalog failure, not the full portfolio or repeat gate.
- Group7/16 reports482 passes,24 existing skips and one failure in72.39
  seconds; aggregate execution fails at664.31 seconds including cleanup.
  `test_nonregular_input_is_rejected_before_open[output]` reconstructs an
  `os.stat_result` from its sequence fields, losing the Windows
  `st_file_attributes` value. The safety check sees `None`, not a native
  attribute mask, and raises TypeError before the intended nonregular refusal.
  The exact failing case reproduces locally under the30-second item bound.
- Repair only that fixture: copy the native named stat fields and change only
  `st_mode` to FIFO. Explicitly verify preserved Windows attributes/reparse tags.
  Keep production link/reparse and regular-file checks unchanged; every
  existing no-open, no-Git-before-exclude-refusal and byte-preservation
  assertion remains active. This is not a permissive `None` fallback in
  production.
- The complete local-cache file passes91 cases with four existing native-POSIX
  FIFO skips in148.44 seconds using exactly two work-stealing workers and the
  unchanged30-second item limit. TESTING records the cross-platform fixture
  contract. The next exact-head complete normal run, repeat and all merge gates
  remain required; Phase4 has not begun.

### 2026-10-14 - Resolve catalog binding in the selected launcher process
- The group5 native-emitter deadline remains a real failure, not a flake
  waiver. Direct profiling establishes that this emitter does not load the
  pane-owner compiler or launch a product service. A fresh local binding takes
  0.727 seconds, including0.658 seconds loading its installation primitive.
  The exact cold `-NoProfile -File` parity test passes locally; that is not
  evidence that the failed hosted run met its unchanged15-second deadline.
- Remove the Windows catalog's redundant Python discovery process from the
  canonical generator. The isolated `py -3` bootstrap reports its interpreter
  and executes this payload's actual binding helper in that same interpreter.
  Interpreter absence retains the existing non-WindowsApps fallback; a helper
  failure never tries another interpreter. Empty helper output reports an
  explicit invalid-response refusal. Binding and installation evidence remain
  fresh, read-only, source-qualified and uncached.
- Two alternating fresh-profile native comparisons return identical complete
  catalogs: old/new5.920/4.973 seconds and new/old4.407/5.289 seconds. These
  measurements motivate the change but do not establish the hosted deadline or
  the900-second complete portfolio gate.
- Native regression cases retain launcher flags and app-alias refusal, exercise
  unavailable-interpreter fallback, helper failure and empty output, and require
  a later manifest mutation to block the previously selected binding. The
  user-site startup tripwire still runs its real control and forbids startup
  writes under isolated invocation. Fix its launcher fixture to forward the
  actual bootstrap output instead of discarding it.
- The complete read-only-profile probe exposes a PowerShell engine
  `ModuleAnalysisCache` write after its baseline. Disable only that engine file
  cache with PowerShell's documented null-device setting in the catalog test
  environment; retain every profile path and every product-state comparison.
  The complete binding suite passes30 cases with six existing POSIX skips
  in176.63 seconds. The targeted portfolio additionally passes the unchanged
  cold emitter parity test in7.35 seconds; generation checks all13 manifests.
- The final bootstrap also passes nine native launcher/read-only/user-site
  cases on Windows PowerShell5.1 in49.00 seconds, with three existing POSIX
  skips. Use Python single-quote literals inside the PowerShell bootstrap so
  legacy native argument passing preserves `__main__`. The final exact cold
  emitter test passes in8.53 seconds, and nine targeted generator/native
  catalog-consumer cases pass in10.01 seconds under the30-second item bound.
  An unnecessarily broad generator/consumer invocation is stopped after699
  seconds without a completed result; it is not passing bounded evidence.
  Do not repeat that unbounded invocation as acceptance.
- Documentation impact: the payload-invocation README describes single-process
  discovery and preserved refusal behavior; TESTING documents the engine-cache
  isolation without weakening product read-only claims. This is below-altitude
  process-cost reduction, not a new capability, authority or resident daemon.
  Complete normal hosted execution, repeat, current required CI, genuine
  exact-head review and actual merge remain owed before Phase4.

### 2026-10-14 - Integrate reviewed guard repair and fix native Bash admission
- Published44cfb64e1d normal38664703323 passes both companions and groups1-4
  (70.48,21.49,143.25,124.19 pytest seconds), then fails group5/16 at478.92
  seconds inclusive. Group5 has964 passes, two existing skips and one
  failure in103.37 seconds: the real generated command-catalog emitter exceeds
  its unchanged15-second subprocess deadline in
  `test_session_start_catalog_matches_native_emitter`. This is not aggregate
  acceptance and its cause is still unclassified, not waived.
  Exact-head CI38664079041 passes every required job. Re-requested genuine
  Copilot review only after the automatic wait; API acceptance is not a verdict.
- Incorporate the reviewed, merged scanner repair#6729 from current dev
  `2e1d19f58789`. A25-commit rebase encounters conflicts in multiple old
  commits; abort it without a stash or hard reset, retain the verified
  `779896ca5c63` backup, and consolidate the owned history with a byte-identical
  source tree before rebasing once. Reconcile both independent guard blocks
  and retain the upstream inherited-Git-context tests. Result `ccd05b680050`
  is an intermediate tree, not hosted acceptance.
- The reconciled Windows guard slice reports93 passes and four real failures
  in71.12 seconds inclusive. Its local Bash admission accepts the WSL launcher
  on PATH because an echo probe succeeds; Windows fixture paths and script
  execution then cease to represent the intended native consumer. The
  writer-ok case also produces a false success without a writer marker.
- Admit explicit supported Bash overrides and native Git/PATH candidates while
  rejecting System32/WindowsApps WSL launchers. Preserve the unchanged actual
  helper bodies and subprocess bounds. Require the writer's real marker on
  paths that should invoke it; payload-hash failure still forbids that write.
  All five actual shipped writer/hash cases plus four admission contracts pass
  in69.58 seconds inclusive, with no new skip or timeout increase.
  The complete reconciled guard file and both upstream native Git-context cases
  then pass all101 items in93.41 seconds inclusive.
- This is a native guard-fixture repair, not a product installer change or a
  first-provision claim. The complete normal hosted portfolio, repeat,
  current required CI, genuine review and actual merge remain owed.

### 2026-10-14 - Profile complete diagnostic costs; batch fresh recovery objects
- Follow-up native profiling finds repeated destination snapshots in explicit
  rewrites and per-commit tree reads in replay verification. Identity and
  repository now come from one fresh destination snapshot, retaining target
  selection and push-time rechecks. Pinned replay trees use one fresh, strictly
  framed native batch; merge reconstruction, raw author/message comparisons,
  ancestry and journal checks remain unchanged.
- The exact hook/dry-run scenario launches43 rather than47 native processes,
  but its call time is20.60 versus20.63 seconds: no wall-clock gain is claimed.
  Four real positive/manual-conflict replay decisions compare against the
  published implementation with alternating measurement order and identical
  results. Their native launches fall44 to36 and measured call time totals7.41
  to6.17 seconds; individual conflict timings get slower. This is bounded
  function-level evidence, not whole-portfolio or hosted acceptance.
- The fresh-destination/repoint/Azure/hook slice passes4 cases in146.53 seconds
  inclusive. Initial replay-tree, raw-object, repeated-region and unstripped-
  message contracts pass20 cases in52.02 seconds inclusive; stricter framing
  adds three refusal cases and the complete23-case slice passes in44.09 seconds
  inclusive. Real manual publication passes one case, and explicitly enabled
  conflict continuation passes both publication-command cases
  in71.38 seconds inclusive under comparison instrumentation.
  The final19-case tree batch file passes in31.23 seconds inclusive with native
  notes, signature-display and color configuration present in both hash formats.
- Complete local rewrite attempts expire after33 items with either worksteal
  or load scheduling. A complementary split passes16 items, then expires
  after20 more; neither establishes complete correctness or margin. Recovery
  authorization finishes all49 pytest items in288.80 seconds, but shutdown
  exceeds the unchanged300-second containment deadline at303.39 seconds
  inclusive. These failures remain failures, not flake waivers.
- Unit and disposable-native consumer contracts are exercised. This change
  does not alter first-provision behavior, so it adds no clean-room provision
  lane. Exact-head normal hosted execution, repeated margin, current CI and
  genuine review remain required before merge and Phase4. The external
  contributor's scanner repair#6729 is reviewed and merged; incorporate it
  through the next safe rebase rather than duplicating their change.
- The complete b004 diagnostic run38579042117 exposes the largest remaining
  call costs in rewrite, recovery, reconciliation and rebase, not fixture
  setup. It is still diagnostic-only: its relaxed execution is not the
  unchanged900-second portfolio gate.
- Two unchanged native publication/recovery scenarios under two workers
  measure407 ordinary Git calls plus18 byte-oriented calls. Recovery resolution
  now reads its two or three commit objects in one fresh native batch, with
  NUL-input framing and strict result count, commit type and complete object
  IDs. Retaining the local/published/lineage refs uses one create-only atomic
  transaction, preserving the original heads or failing before any partial
  retention. Every later decision still probes again; no authority is cached.
- The same instrumented scenarios subsequently measure371 ordinary plus30
  byte-oriented calls:24 fewer native Git launches overall. Their elapsed time
  rises from77.19 to89.98 seconds inclusive on this host. This proves a reduced
  launch count, not a wall-clock speedup, group margin or hosted acceptance.
- Combining all checkpoint/batch contracts in one300-second group expires
  after30 completed items. Separate complete files pass15 batch and17
  checkpoint cases in300.75 seconds inclusive; the checkpoint file itself
  takes257.16 pytest seconds. Adding native SHA-1 and SHA-256 repositories
  then passes all17 batch cases in40.02 seconds inclusive.
  The complete normal portfolio, required repeat, current CI/review and merge
  remain owed; Phase4 live selection stays blocked.
- Further bounded local worksteal rewrite attempts expire after15 and33
  completed items. The full40-case rewrite file does pass with two-worker
  load scheduling in289.63 pytest seconds;49 authorization cases pass in230.28.
  Exhaustive rebase validation exceeds300 seconds as one file, then its
  complementary selectors pass9 and24 cases in590.66 seconds inclusive,
  retaining the existing opt-in native-headless skip. No new skip or deadline
  increase is introduced, and these correctness results do not prove the
  hosted worksteal grouping has sufficient margin.
- A later same-version native-Git/worksteal probe again passes the17 batch
  and17 checkpoint files but expires in rewrite with setup-error indicators
  before pytest can print their details. The smaller cross-group diagnostic
  reproduces no setup error but expires after35 successful rewrite items;
  that setup-error cause remains unclassified. Three read-only shared-host
  samples show99.71,100 and100 percent CPU utilization. This is environmental
  evidence, not a flake waiver or a substitute for hosted normal acceptance.
- Final strict-input coverage also refuses non-string refs before launching
  Git. All19 batch contracts pass in37.99 seconds including cleanup, and their
  Ruff check passes. Unit/native disposable-repo correctness is exercised;
  first-provision behavior is unchanged, so this slice adds no separate
  first-provision clean-room lane. Available hosted normal portfolio execution
  remains required before any merge or Phase4 cutover.
- Exact2b3dcdf702 normal38641581989 passes both companion portfolios and
  groups1-9, then expires during group10/16 at909.41 seconds including cleanup.
  Completed groups take67.97,21.25,112.61,107.12,78.12,76.28,89.30,54.39 and
  125.50 pytest seconds; group10 receives146.703 seconds. There is still no
  complete normal pass or repeated group margin. Current CI38641200877 passes
  every required job, but the latest actual Copilot review remains on b004.
  Re-requested review on2b only after the automatic wait elapsed; API acceptance
  is not a review verdict.
- A minimal same-sandbox batch/checkpoint/rewrite sequence passes all three
  actual native cases in251.55 seconds inclusive without reproducing the
  earlier setup errors. They remain unclassified, not waived.
- The real read-only missing-policy helper contract retains both fresh probes
  and its complete fixture byte/mtime assertions under instrumentation:
  twelve native type compilations take4.113835 seconds across two invocations.
  The first compilation in each consumes1.229 and1.319 seconds. No compiled
  policy proof, backend substitution or timeout change is introduced; compiler
  batching has not been measured as a successful remedy for the aggregate gap.

### 2026-10-14 - Normal absence-probe candidate still misses aggregate acceptance
- Exact afea6195b normal rotation38604705528 passes both companion portfolios
  and groups1-9, then expires during group10/16 at904.22 seconds including
  cleanup. The completed groups take77.85,21.57,169.09,127.13,67.37,80.01,
  56.41,54.02 and140.37 pytest seconds. Group10 receives90.844 seconds.
  The complete unchanged900-second gate and repeated final-head proof remain
  unmet; this result is not dismissed as host variance or a flake.
- A contained real-Git trace of creation and recovery preserves actual
  transports and exposes duplicate committed-config reads inside one hook
  protected-branch decision. Reuse that decision's already-read mapping for
  default-branch fallback, preserving machine-local/drop-in precedence and
  fresh reads on every later decision. This is not a cross-call policy cache
  or a bypass of any publication, reconciliation or hook check.
- The hook/creation/recovery slice passes41 cases in98.11 seconds inclusive.
  All224 hook/config/drop-in/graft cases then pass in167.90 pytest seconds.
  Combining the three heavy recovery/rewrite files into one local validation
  group exceeds300 seconds, so their complete files are run separately, without
  increasing a deadline:49 recovery-authorization,40 rewrite,17 checkpoint and
  163 anchor-guard cases pass in803.92 seconds including cleanup. The heavy
  files take240.47,269.15 and224.53 pytest seconds locally; this is correctness
  evidence, not hosted group headroom or whole-portfolio acceptance.
  Exact02f2e0b5c normal38617126598 subsequently passes both companions and
  groups1-9, then expires during group10/16 at905.84 seconds including cleanup.
  Those groups take66.04,22.06,115.59,132.36,69.41,100.40,69.08,55.51 and139.59
  pytest seconds; group10 receives112.281 seconds. No complete normal pass
  or repeatable margin is established.
- Required CI38616956526 fails the customization scanner's output-free-stack
  contract in merge snapshot e53e9ab6b (head02f2e0b5c into integration028fc2e0).
  The actual inventory flags only agent-worktrees. The cause is already owned
  by another contributor's open PR#6729, which recognizes stderr diagnostics
  without allowing stdout. Posted the concrete job/snapshot evidence there;
  do not pre-patch or supersede that contributor. Consume their reviewed
  change after it lands, then obtain genuinely passing current-head CI.

### 2026-10-14 - Normal progress reaches group14; batch fresh committed-config absence
- Normal exact993817dd8 run38596822361 passes both companion portfolios and
  groups1-13 before the unchanged aggregate expires in group14,905.67 seconds
  including cleanup. Their pytest durations are48.28,15.49,76.80,75.07,53.23,
  49.13,47.94,33.66,104.20,129.92,112.60,46.49 and32.83 seconds; group14
  receives54.703 seconds. Completed groups have ample240-second headroom, but
  groups15-16 still need complete normal execution and repeated final-head proof.
  Verbose relaxed diagnostics are not interchangeable aggregate timing evidence.
- The preserved Git trace also shows sixteen repetitions of each of the three
  absent committed-config blob probes. Prove all candidates absent using one
  fresh literal tree query. If any candidate may exist, retain the original
  ordered reads, first-match priority, dirty-checkout independence, ref hopping
  and YAML handling. Do not eagerly read unselected blobs, cache absence, or
  alter the shared30-second/per-probe15-second deadlines.
- Unexpected presence-probe exit, timeout and non-text output warn explicitly
  and retain the existing bounded ordered fallback. Real-Git contracts verify
  two total queries for complete absence, later committed configuration becoming
  visible, literal wildcard-looking paths, and empty/malformed/valid first-match
  handling without reading a lower-priority blob.
- All16 committed-reference contracts pass, followed by253 complete config,
  drop-in, graft, context and registered-repository consumer cases in68.50 seconds
  inclusive under unchanged30/300/900 runner limits and exactly two workers.
  Lint and whitespace checks pass. Full normal aggregate, current CI/review,
  merge/promotion and the parent effort remain open.

### 2026-10-14 - Complete expanded diagnostic and fresh transport query batching
- Corrected b004 diagnostic38579042117 passes all389 files across16 groups and
  its index/pull-request companions. Worktrees takes1600.70 seconds including
  cleanup; its sixteen pytest durations are70.41,24.02,142.74,152.39,80.60,90.43,
  61.01,61.27,150.79,237.12,200.25,97.77,34.67,84.12,43.77 and32.76 seconds.
  Group10's2.88-second margin below240 is not meaningful headroom, and the
  approximately700-second aggregate deficit remains. Relaxed profiling is not
  normal acceptance. Required2d0a302 CI38583615821 subsequently passes every job.
- A temporary diagnostic wraps actual Git calls without substituting behavior
  in five preserved creation/recovery/rewrite cases. The baseline performs990
  calls, including202 remote-URL reads and105 individual transport-setting
  reads. Capture fetch/push URLs once per fresh authority snapshot, deriving the
  target and pinned transport from those exact URLs. Read the same seven known
  settings in one escaped, NUL-framed query, preserving original key grouping,
  repeated values, embedded newlines and valueless booleans. No cached authority
  or publication/reconciliation/lease recheck is introduced.
- New real-Git contracts prove exact query counts, literal remote names, absence,
  later and between-read repoints, and explicit read/framing failures before any
  push. Existing fictitious-provider fixtures now label the actual captured
  target at the correct seam, preserving their real URL reads and transports,
  rather than patching an older duplicate slug/identity query.
- The four transport/publication/hook/deadline modules pass99 cases with2 existing
  platform skips in195.84 seconds inclusive. Full recovery/rebase/rewrite plus
  fresh-evidence modules pass90 cases with16 existing platform skips across
  bounded groups in495.38 seconds inclusive. All65 creation/fork/refspec cases
  pass; full operator recovery/custom-head/authority and dependent push/provider/
  tracking modules pass447 cases in533.36 seconds inclusive. Item/group/plugin
  runner limits and exactly two workers remain unchanged.
- The identical five-case post-change diagnostic performs823 calls:125 URL reads
  and15 setting reads, a167-call reduction. Its total110.36 seconds is slower
  than the baseline96.95 seconds on this particular local repeat, despite fewer
  launches, so **query-count reduction is not claimed as aggregate performance
  acceptance**. Fresh hosted normal timings must decide that gate. Current review,
  repeated complete900-second margin, merge/promotion and the parent effort remain
  open.

### 2026-10-14 - Thin fleet hosted pass and measured independent allocation inputs
- Corrected-head CI38578850049 passes the entire Windows test-runner job,
  including the real thin fleet command:597 passes,6 existing skips and7 explicit
  full-distribution deselections in104.08 pytest seconds under the unchanged
  120-second containment deadline. The run still fails its required gate because
  fleet-manager's test changes need a pending changefile; add the required entry,
  rather than bypassing the guard.
- Instrumenting the actual retirement module measures86 repetitions of its
  setup-only active-allocation construction at7.291 seconds total. Construct this
  real admitted/cooperatively committed input once, close its SQLite connections,
  and copy both databases into independent case-owned files. Preserve real
  constructor validation and every retirement, rollback, migration, fencing and
  native process-race assertion. Verify the seed remains byte-identical and add
  an actual two-copy retirement/gateway-release isolation contract.
- The complete retirement module now passes87 cases in13.27 seconds, versus
  the instrumented unchanged86-case module's20.41 seconds. The complete thin
  command passes598 cases in108.07 pytest seconds once locally; a local repeat
  still hits120 seconds near completion. Total preparation/cleanup wall times
  are131.40 and130.66 seconds respectively; the runner's existing120-second
  containment deadline excludes dependency preparation, so these are not invented
  inclusive120-second passes. Final-head hosted CI and margin remain required,
  and neither SQLite durability nor replay thresholds are relaxed.

### 2026-10-14 - Expanded matrix exposes authority fixtures and thin fleet deadline
- Normal exact1e96d3340 run38552319558 passes its index and pull-request
  companions, but worktrees still exceeds900 seconds at904.91 including cleanup.
  Reconciliation expanded the actual portfolio to389 files in16 groups. Its
  first nine pytest durations are68.22,22.60,132.28,111.71,71.86,88.13,72.80,
  64.83 and155.87 seconds; group10 receives95.297 seconds before termination.
  All completed groups have headroom, but complete aggregate acceptance is absent.
- Diagnostic38570293070 reaches group14 and finds three obsolete receiving-side
  authority fixtures: terminal managed reactivation and both bare-id owning-project
  registrations. The real registration seam now loads
  `config_authority.load_project_authority_config`, not the fixtures' older
  `config.load_project_config` seam. Supply the same isolated project configuration
  at the new seam without bypassing the mutation fence. Full owning-project and
  terminal-conclusion modules pass293 cases in99.44 seconds including cleanup.
  Groups15-16 still need execution on a corrected head.
- That diagnostic's first fourteen pytest durations are65.91,21.54,122.12,106.94,
  76.82,98.89,85.41,67.49,152.24,214.24,189.76,91.64,37.13 and70.07 seconds.
  This relaxed diagnostic is never normal-budget acceptance. Recovery/rewrite
  group10 and publication/reconciliation group11 remain major measured costs.
- Required CI38551967717 is cancelled because its Windows test-runner step
  actually exceeds the unchanged120-second thin fleet containment deadline,
  not because of an unexplained transient cancellation. Other jobs pass, and the
  required aggregate gate fails. A bounded delegate finds no safe complete fix
  and makes no edits; direct follow-through identifies duplicated module-local
  wheel construction. Share only the real immutable wheel through one session
  fixture, preserving independent actual installs, removal, authentication and
  offline probes; assert its bytes remain unchanged after both consumers.
- The complete thin command passes597 cases with6 existing skips and7 explicit
  full-distribution deselections in113.15 pytest seconds once, but its unchanged
  120-second deadline fails on the next repeat. Including preparation/cleanup,
  those invocations take133.73 and130.72 seconds respectively. This is a measured
  setup-cost reduction, **not a resolved deadline finding**. The1024-event replay
  threshold test performs actual durable commits and takes approximately15 seconds;
  neither its threshold nor full SQLite durability is weakened.
- A genuine Copilot review-request timeline event is recorded at11:07:27 UTC
  for the current published head, but no new review has yet arrived. Fresh required
  CI, complete corrected-head diagnostic, normal repeated900-second acceptance,
  thin fleet deadline, actual merge and the parent effort remain open.

### 2026-10-14 - Reconciled integration head before fresh hosted acceptance
- Preserved the committed private tip and rebased all seventeen owned commits
  onto integration commit6e319fbb5a. Equivalent upstream absent-wrapper coverage
  is retained. Installer recovery keeps both selected-generation refusal and
  actual unselected cold rebuilding, including the upstream byte-for-byte
  immutability checks. Native CPU ledger comparison retains upstream tolerance.
  The independent hand-made landing fixture also retains upstream isolation from
  external Codespaces claim warnings.
- Post-rebase contained checks pass110 landing/object/absent-wrapper cases in
  238.83 seconds including cleanup,20 native-reader cases in24.73 seconds,
  and all four selected/unselected repair variants in194.56 seconds. Worktrees
  uses exactly two workers with unchanged30/300/900-second limits; installer
  cases retain their existing module timeout, not a newly raised budget.
  The hand-made finalization call takes26.09 seconds locally: narrow local
  headroom is not proof of repeatable hosted margin.
- The dependency fingerprint changed upstream, so the managed runner restored
  its actual worktrees environment before testing. Conflict-file lint and
  whitespace checks pass. This is post-rebase correctness evidence, not complete
  hosted portfolio acceptance. Fresh normal exact-head rotations, current review
  and required CI remain owed before merge; the parent coverage effort stays open.

### 2026-10-14 — Complete current profile and batched finalization evidence
- Exact d5a72dd8a normal run38486852124 passes the native pane family and
  groups1-9, then exceeds the unchanged aggregate limit at902.25 seconds
  including cleanup. Group9 takes232.15 seconds; group10 receives23.344.
  CI38486865466 succeeds. This is not whole-portfolio acceptance.
- Complete diagnostic38487424477 succeeds across all15 current groups.
  Their pytest timings are44.56,18.34,120.54,126.13,72.42,67.23,66.62,
  62.10,231.72,134.16,117.15,79.68,51.54,57.10 and29.35 seconds.
  Approximately1,300 seconds including runner overhead remains well outside
  the normal900-second aggregate gate. Groups9-11 remain major measured costs.
- Local default30-second deadlines expose repeated Git object and creation
  queries in finalization. Reuse the classifier's existing bounded literal
  object helpers for changed-file evidence, rather than two child processes
  per path. Sixteen real changed paths now require three Git calls instead of
  thirty-four. Deletion and directory replacement evidence remains exact,
  mode-only differences retain content semantics, and unreadable Git output
  refuses landing with a diagnostic.
- Fresh zero commit counts already prove there is no additional work, so
  creation-point history is consulted only for positive counts. Identical
  tracked/current references cannot be ahead of themselves. Historical
  publication proofs, local source-shadow refusal and all initial, locked and
  post-termination rechecks remain in place; there is no cross-call cache.
  The squash probe reads both tree IDs in one actual Git invocation and
  validates the exact two-object output, including the native
  `--revs-only --end-of-options` protocol.
- Hoist only identical merged landing inputs and Git-only resource-producer
  setup. Physical copied object stores and per-case records, reservations and
  fences preserve independent mutation. Report and actual-finalization checks
  exercise the same prepared history as distinct cases. The latter declares
  the fixture's absent mux/process backend rather than reaching live host
  services; dedicated real teardown races remain unchanged.
- The integrated landing/resource/object-helper portfolio passes101 cases
  with two existing platform skips in191.19 seconds including cleanup, using
  exactly two workers and unchanged30-second test and300-second group bounds.
  The broader finalization run identifies unwanted calls to the host mux
  from Git-only fixtures. Explicit module-scoped mux absence keeps real process
  termination and all lock/termination/reconciliation races intact. One fresh
  recovery contract,50 gate/diverged/PR cases and85 precondition/rollback cases
  pass in413.78 seconds including bootstrap and cleanup; the two consumer
  groups take164.03 and194.14 seconds. No runner limit or existing test marker
  changes; the recovery module retains its existing180-second override.
- Restore the hand-made input's exact original history rather than constructing
  and undoing an unnecessary pull-forward. Empty Git templates, clone-time
  identity configuration and omission of unused commit-ID reads reduce setup
  launches without replacing commits or removing assertions. Both genuinely
  cold report/finalize cases now pass within the30-second default, at39.33
  seconds including worker startup and cleanup. All three input seeds pass
  real commit/push and physical object-store isolation checks in35.45 seconds
  inclusive. The latest full focused run's one hand-made isolation failure
  was a publisher starting from main instead of the merged dev tip; its
  targeted correction passes all three variants. New hosted normal runs,
  repeated exact-head performance proof and current review remain open.

### 2026-10-13 — Native compiler stall localized; current aggregate still fails
- Normal exact e499a306b run38475963492 fails the unchanged aggregate limit
  at903.16 seconds including cleanup. Group9 passes731 cases/16 skips in
  250.23 seconds, missing the240-second headroom target; group10 receives
  only26.172 seconds. CI38476029570 and both complete auxiliary rotations
  38475999369 and38479795960 pass. Repeated full performance acceptance and
  the current review gate remain open.
- The reconciled portfolio now has371 files in15 groups, not the earlier12.
  Diagnostic38481070639 cannot profile the remaining groups because the real
  native pane launch again exceeds15 seconds. Its captured PowerShell trace
  stops at `Add-Type` for the pane's Job Object, before receipt or child argv.
  This is new localized evidence, not an accepted flake or a budget result.
- Bind the same native Job Object calls directly through .NET P/Invoke
  metadata, avoiding launch-time C# compilation without bypassing ownership.
  Independent native ABI declarations query the actual job handle and exact
  kill-on-close flags; terminating the actual wrapper reaps its sleeping
  child. These five native ABI/lifetime/parser cases pass in22.45 seconds
  inclusive. The unchanged pane receipt/argv/15-second family passes five
  cases in210.51 seconds inclusive; its actual native launch takes7.07 seconds.
  New hosted whole-portfolio, normal-repeat and complete timing proof remains
  required; neither local result satisfies those gates.
- The complete native Job Object/argv/manifest family passes36 cases with
  two existing platform skips in140.81 seconds inclusive using exactly two
  workers. A serial first attempt observes a15-second malformed-manifest
  rejection timeout (35 passes); that exact rejection subsequently passes
  in8.78 seconds. The failure is retained as unresolved repeatability
  evidence, not dismissed as a pre-existing or accepted flake. No rejection
  timeout or production error-reporting behavior was changed.
- A bounded review of the new source-recovery test module finds no Git setup
  to hoist: it contains provider/string-normalization contracts, so no edits
  or assertion reductions were made. A separate contained recovery profile
  passes one real case, measuring289 Git calls consuming129.38 seconds on
  the loaded local host; that profile is not hosted performance acceptance.

### 2026-10-13 — Native pane passes normal; aggregate and bridge findings remain
- Exact3baa5f764b normal Windows38464746868 passes the native pane/complete
  group5 (946 passes/5 skips,72.80 seconds) and group9 (731 passes/16 skips,
  235.47 seconds), then fails the unchanged aggregate900-second limit during
  group10 at902.39 seconds including cleanup. Group9's4.53-second headroom
  is not meaningful repeatable margin; no complete/repeated acceptance exists.
- Complete CI38464798456 passes every job. Auxiliary38464783059 passes
  codespaces and handoff but fails bridge's stale-discovery report fixture:
  thirteen legitimate fresh retries invalidate its incidental count-of-one
  assertion. Re-dispatched38467496235 passes the complete codespaces,
  containers and harness portfolios; the cancelled superseded dispatch ran
  no jobs and supplies no evidence. All these runs are terminal.
- Distinguish stale/fresh report identities, synchronize the actual first
  threaded scan, and await its captured task after release. Assert no stale
  report is ever applied, including after actual late completion; do not
  weaken the generation fence or rely on a50ms callback sleep. The exact
  regression passes locally in22.69 seconds inclusive.
- Complete local roster-cache validation exposed a real Python3.11 ownership
  bug: wait_for's extra provider task can survive repeated cancellation while
  the holder finishes cancelled, unlike Python3.12's same-task timeout behavior.
  Two preserved cancellation/ownership contracts reproduced it, including a
  lost second cancellation signal. Keep provider execution in the holder's
  standard timeout context, with the supported Python3.10 backport dependency;
  retain timeout values, forwarding and all ownership/generation assertions.
  This realizes the existing reader-ownership invariant, not a new policy.
  All51 roster-cache cases pass on3.11 in35.97 seconds inclusive; actual fresh
  dependency restores and the same51 cases pass on3.10 and3.12 in75.36 and
  66.86 seconds inclusive. Final3.11 environment restore remains tracked.
- A bounded rewrite-module fixture review finds no further surgical reduction:
  remaining fresh setup is the actual ownership/URI/publication contract.
  No edits or discarded assertions resulted. Group10 storage-probe profiling
  remains a targeted diagnostic, not full-portfolio acceptance.
- The3.11 test environment is restored. Normal bounded group10 profiling
  fails300 seconds/304.89 inclusive, with142 storage probes consuming71.41
  seconds (median0.531, max1.057). Overlap Windows directory reads using two
  joined metadata threads and bounded batches, while retaining every fresh
  size query, unchanged probes/limits and two pytest workers. Eleven paired
  live samples consistently reduce probe latency (parallel0.155–0.934 versus
  serial0.258–1.698 seconds). The diagnostic group reaches65% without the prior
  worker deaths, but still fails300/308.23 inclusive; neither outcome is normal
  full-portfolio acceptance. Full containment/runner contracts pass47 cases
  with2 platform skips, including fresh growth/removal, real overlap and
  unexpected failure propagation; install-contract validation reports zero
  violations. Full hosted performance and POSIX/native confirmation remain owed.

### 2026-10-13 — Canonical matrix green; native launch and budget still block
- Published58e5d46c9f to the existing PR. Complete CI38456689270 passes every
  job, including real Linux/macOS runner permissions and native CPU accounting.
  Normal Windows38456658314 again fails the actual pane launch before receipt,
  argv or diagnostic output,385.89 seconds inclusive. Its index and pull-request
  portfolios pass. No normal-budget acceptance or native root fix is claimed.
- Same-head runner-debug attempt2 passes the pane launch, then exceeds the
  unchanged900-second aggregate ceiling at903.61 seconds including cleanup in
  group10. Group9 passes731 cases/16 skips in246.01 seconds, still above240.
  Tracing remains diagnostic-only and does not waive either failure.
- Auxiliary38456674105 actually selects codespaces, containers and harness:
  containers passes, the complete updated installer recovery family passes,
  then codespaces finds three new audit-fixture failures and harness finds five
  mention-guard fixture failures. Rotation inputs are rechecked against the
  actual candidate selector rather than assuming the previous roster.
- POSIX shell quoting had been routed through Windows CMD, and literal
  backslash filenames cannot exist on Windows. Invoke the real generated
  login-shell argv through host-native Bash, normalize fixture paths, and retain
  all real corrupt-store/linked-worktree assertions with Windows space-bearing
  names. The audit family passes16 cases/2 skips in35.50 seconds inclusive;
  restoring the corrupt-store case on Windows additionally passes in16.66.
  An initial local run with the Git command wrapper exceeded the unchanged item
  budget; the passing runs use its verified same-version native executable.
- Quote mention-guard fixture file arguments in their intended POSIX grammar.
  Run actual hooks with the current interpreter and inherited executable
  discovery instead of a python3 alias and an environment lacking PATH.
  All42 mention-guard cases pass in18.62 seconds inclusive. Production policy
  and fail-closed scanning remain unchanged.
- Group9's expensive partial-publication refusal variants now copy one genuinely
  built lost-acknowledgement/rebased input with independent stores. The success
  case still constructs and exercises the whole pipeline. Actual provider
  observation, independent-graph refusal, concurrent pushes and unchanged seed
  tracking/refs are checked. All four refusal variants pass in171.80 seconds
  inclusive with two workers and unchanged limits. Complete hosted timing
  remains owed. Current low-severity review comments now describe durable
  contracts instead of the review rounds that introduced them.
- Isolate the captured native pane test's console with CREATE_NO_WINDOW rather
  than letting PowerShell share the worker's console. Actual launcher/wrapper,
  receipt, child argv and15-second assertions remain unchanged. All162 combined
  handoff/refusal cases pass in248.08 seconds inclusive with two workers and
  unchanged budgets. This is a test process-shape correction, not a claimed
  production root fix; repeated complete normal hosted validation still owes
  proof that the native stall is gone.

### 2026-10-13 — Full-matrix regressions and remaining budget deficit
- Normal exact-head92d8e9e6d run38444426871 failed at920.47 seconds including
  cleanup during group10. Group9 passed731 cases/16 skips in247.49 seconds,
  missing the240-second margin target. Companion index and pull-request
  portfolios passed; neither complete nor repeated final-head acceptance exists.
- Complete CI38444768801 exposed a POSIX private-parent regression on Linux
  and macOS: recursive group creation implicitly gave its parent0755 instead
  of0700. Explicitly create the parent with0700 before the group. Local runner
  contracts pass26 cases/1 platform skip; real POSIX confirmation remains owed.
  The same run exposed floating-point rounding between incremental CPU-ledger
  additions and Python3.12 sum. Use strict approximate equality only there,
  retaining native sampling, counts, ownership, liveness and all other assertions.
  Focused native validation passes1 case/1 platform skip in56.05 seconds inclusive.
- Auxiliary Windows38444830314 passed bridge and handoff portfolios but found
  two obsolete codespaces recovery expectations: selected incomplete generations
  intentionally refuse deletion. Preserve and assert that refusal separately
  from genuine unselected cold rebuilding, including uv-only executable discovery,
  unchanged markers, payload bytes and absence of venv/pip mutation on refusal.
  Materialize and guard runtime-only payload once, with independent physical
  copies per installer. All13 cases pass across bounded selections; the complete
  module observed12 passes before its300-second limit during the final marketplace
  case,303.70 seconds inclusive. The two marketplace cases separately pass in
  105.97 seconds inclusive. Full-module/hosted acceptance is still open.
- Remove repeated setup-only publication/upstream advancement in recovery
  fixtures without removing actual sync, backup, reflog, race or push operations.
  Preserve first-publication/legacy cases and isolate actual publication authority
  profiles per case, including real child probes. Recovery17 cases and
  publication21 cases pass in separate normal bounded groups,225.79 and85.00
  seconds respectively,327.95 seconds inclusive together. Their combined group
  twice exceeded300 seconds; these local results are correctness evidence, not
  a claim of hosted performance margin. All original completion gates remain open.

### 2026-10-13 — Phase 6: run-local immutable PR inputs, acceptance still open
- Reconciled the existing source and pending edits without replacing the PR.
  Normal exact b16a8e079 run38300992971 again exceeded900 seconds, at905.25
  including cleanup during group10. Group9 completed711 passes/16 skips in
  214.70 seconds; index and pull-request portfolios passed. No repeated
  complete final-head margin is established.
- Existing session-scoped PR input seeds were still reconstructed by each
  worker in every new pytest group. Publish the base, published, fork and
  both rebased head-scheme seeds once in the runner-owned plugin sandbox,
  outside basetemps. A required actual cross-process sidecar lock excludes
  concurrent builders; readiness appears only after successful construction.
  Incomplete or malformed inputs fail explicitly rather than being consumed.
  Every test retains independently copied Git object stores, records, refs,
  worktree links and its actual operation. Direct uncontained pytest retains
  separate session-local construction; no global cache or deadline changes.
- Focused normal contained two-worker validation passed all74 cache/rewrite
  contracts in203.00 seconds including cleanup. Additional seed assertions
  prove repair and mutation leave the original Git administrative links and
  configuration unchanged; all13 seed contracts pass in24.81 seconds
  including cleanup, and all26 runner contracts pass. Actual two-process
  exclusion, interrupted-builder recovery refusal and subsequent-group cache
  retention are exercised, not mocked away. These are bounded correctness
  checks, not full-portfolio acceptance; repeated exact-head hosted runs,
  current review and all original completion gates remain required.
- Backed up the private source before reconciling129 incoming dev commits.
  Preserved canonical retention/runtime-scope/stderr guards alongside the
  bounded candidate scan; kept private-directory permissions in the cleanup
  runner. Relocated the incoming fork-publication expectation into its split
  module: transport now pins its URL, so unrelated remote configuration need
  not remain locked throughout a push. The canonical renderer no longer
  clones a plugin script tree; retained that new standalone-engine contract.
  All166 combined seed/rewrite/publication/renderer cases pass, with2 valid
  skips, in294.55 seconds including cleanup across two targeted groups.
  All83 guard/runner/containment cases pass with2 platform skips, and the
  complete installation-contract guard reports zero violations.
- Dispatch38437619344 failed checkout before any portfolio ran because its
  ref input was an abbreviated SHA, treated as a branch name. It supplies no
  performance evidence. Subsequent dispatches must use the full immutable
  commit ID; all normal budget and repeated-final-head gates remain open.
- Normal reconciled-head38439733548 failed after466.34 seconds in group5's
  actual pane-wrapper launch, before receipt or child argv. Both companion
  portfolios passed. Two complete local group5 reproductions each pass946
  cases/5 valid skips; the native stall's root cause is not established.
  Its same-head runner-debug rerun passed that launch but failed later on
  a new canonical transport test's Unicode stdout under the Windows code
  page. Pin only the real helper's Python output mode to UTF-8, preserving
  actual PowerShell transport and exact decoded intent. All151 launch-request
  cases pass with1 platform skip and explicit non-UTF8 host defaults.
- Enable the existing copied-wrapper trace for explicit runner-debug
  reruns, without enabling extended profile ceilings. Byte-fidelity tests
  retain ordinary unmodified scripts and cover both explicit diagnostics.
  Such reruns remain diagnostic-only, not accepted portfolio repeats.

### 2026-10-12 — Phase 6: isolate raw pane fallback and retain failed budget evidence
- Complete local normal validation reached group3, surfaced an environment-
  dependent fallback test and then exceeded300 before printing its summary.
  Focused bounded fail-fast evidence identified the raw psmux builder case:
  it unintentionally discovered an installed manager wrapper while asserting
  wrapper-absent behavior. Pinned a genuinely absent fixture path instead of
  changing production discovery; all four builder contracts pass.
  The local full run remains a failure, not hosted budget acceptance.
- Normal hosted run38236277611 also exhausted the unchanged aggregate ceiling,
  at911.91 seconds including cleanup after eight completed groups. Its
  pull-request and index portfolios passed. Run38238620168 was cancelled
  before jobs started; exact-head run38239800303 remains queued. None of
  these observations proves the required repeatable final-head margin.
- Run38239800303 subsequently failed its normal900-second aggregate limit
  at912.36 seconds including cleanup, again during group9. The completed
  pull-request and index portfolios passed. Earlier passes are not final-head
  acceptance and this failure remains an acceptance blocker.
- Removed repeated setup-only publication and rebase from20 generic recovery
  items. They now receive independent copies of one genuinely recovered-plan
  topology per worker; each confirmation, transport, race, hook, audit and CLI
  operation remains real and per-case. Provider-specific preview and frozen
  attribution setup stays fresh. Successful publication additionally proves
  the seed's tracking bytes and remote refs are unchanged. All49 recovery
  cases pass in a targeted contained group,134.64 seconds including cleanup,
  with two workers and unchanged deadlines. This is focused behavioral proof,
  not full-portfolio performance acceptance.
- An earlier all-module `-k` baseline timed out at300 seconds and ultimately
  reported5341.84 seconds including cleanup. It did not complete the recovery
  portfolio and is not comparable timing evidence for the targeted group.
  Fresh complete hosted execution remains necessary.
- Published recovery consolidation at8e03ad486. Dispatch38255992672 used
  the wrong rotation date and selected bridge, codespaces and handoff, not
  worktrees; it provides no worktrees budget evidence. Verified the selector
  before dispatch38257654861 with2026-10-08, which correctly selects worktrees,
  index and pull-request portfolios. Its latter two portfolios pass; worktrees
  remains in progress.
- The mistaken extra matrix exposed25 launch-policy test failures and one
  native owner-response failure. Policy tests registered under their temporary
  directory but still checked the separate runner profile; isolated the test
  profile instead of weakening the production ownership fence. The native
  Windows owner trampoline had not explicitly inherited its parent's redirected
  stdio, so the real command exited successfully without returning its JSON.
  Explicitly forward stdin/stdout/stderr while retaining windowless flags and
  exit status. A direct flag/stream/exit regression and the actual installed-peer
  integration pass. All15 owner-launch cases and50 policy cases pass locally,
  with5 legitimate platform skips; mandatory patch changefiles record both
  encountered fixes. Complete external verification remains outstanding.
- Final65d2dcfcb run38259035124 failed the real pane launch's15-second bound
  before receipt or child argv existed; the last trace stopped at Add-Type
  compiling the Job ownership type. Same-head retry38262166277 passed that
  portion but lost a pytest worker in the native background-prune observer,
  failing at554.81 seconds with the portfolio incomplete. Both index and
  pull-request jobs passed. Neither worker death nor the compiler stall has an
  established root cause or accepted-flake waiver.
- The native observer passes locally alone and within its original25-file
  group; no crash fix is claimed. That complete group exposed two genuine
  renderer-fixture failures instead: copying unrelated host-generated bytecode
  exceeded Windows path limits. Excluded only Python bytecode caches, retaining
  all source scripts and real subprocess discovery/rendering. A separate stale
  local dependency wheel lacked the source's include_projects parameter;
  rebuilding that one owned test-venv package from its actual local source
  without global cache deletion restored the correct ABI. All47 renderer
  contracts pass. This is not an upstream dependency-API workaround.
- Twenty-five post-rebase refspec ownership and legacy-refusal items now copy
  a real published/rebased seed independently instead of repeating setup-only
  creation and rebase. Their actual guarded rewrite, metadata persistence,
  subprocess races and refusal assertions remain unchanged. Public rebase,
  snapshot, changed-creation-identity and destination-change scenarios stay
  fresh. Actual configured-project publication also proves seed tracking bytes
  and remote refs unchanged. Both complete rewrite modules pass55 cases in
  167.42 seconds including cleanup with two workers and normal ceilings.
  Fresh complete hosted budget and reliability evidence remains required.
- Normal exact737523424 run38266750151 completed all14 Windows groups:
  8,484 passed and64 legitimate skips,776.19 seconds including cleanup.
  Every group stayed below240 seconds; the slowest pytest portion was133.29.
  Index and pull-request jobs also passed. This is the first passing candidate
  result, not repeated final-head acceptance or proof that prior native stalls
  are fixed. The PR subsequently became conflicting after40 newdev commits;
  preserved an explicit local backup before authorized private-branch
  reconciliation. Fresh reconciled-head full repeats and review/check gates
  remain required; the effort and original objective stay open.
- Reconciled the backed-up private PR onto1703cc42b after40 interveningdev
  commits, preserving the two new revised-legacy test bodies exactly by AST.
  Canonical revised legacy records may now rewrite with exact live authority;
  removed only the obsolete corrected-record refusal parameter, retained
  every other refusal and the new acceptance/authority cases. All61 rewrite
  cases pass after reconciliation. Combined the new canonical adopter-navigation
  guard/tests with the bounded Markdown walker, retaining canonical directory
  diagnostics and exclusions;73 guard/runner/rotation cases pass.
- Current review correctly identified tracing as extra work inside a timed
  production-wrapper regression. Normal mode now copies the wrapper byte-for-
  byte; only the existing explicit diagnostic profile enables tracing.
  A focused regression proves both paths; all5 prompt/diagnostic cases pass
  with the unchanged15-second bound. This corrects instrumentation fidelity,
  not a claimed root-cause fix for every earlier hosted native failure.
- Reconciled-head normal38270806820 passed the native cases and all completed
  behavioral groups, but exhausted900 seconds during group10 at903.75 seconds
  including cleanup. Group9 completed711 passing cases/16 skips in233.90
  seconds. This is failed aggregate acceptance, not a successful repeat of the
  earlier776.19-second candidate.
- Removed nine more setup-only publication/rebase sequences by extending
  independent prepared state to both head schemes for moved-remote leases,
  fork/repoint refusal, hook/dry-run and legacy one-command migration. Each
  actual operation and assertion remains; both public real-rebase cases,
  new revised-legacy cases, creation-identity and changed-destination cases
  retain fresh setup. Complete rewrite families pass61 cases in176.73 seconds
  including cleanup under two workers and unchanged limits. Snapshot and
  refspec actual legacy publication additionally verify immutable seed
  tracking bytes and remote refs stay unchanged.
- Normal a974d959e run38275068702 again exhausted900 seconds during group9,
  at913.41 seconds including cleanup, so the planned second run was not
  dispatched. All completed groups passed behavior; repeated aggregate
  acceptance remains unmet.
- Consolidated four lease disposition items into two short real workflows,
  preserving explicit acquisition context, default active context, plain renew
  retention, changed disposition and durable inspection assertions while
  removing repeated setup acquisitions. All7 lease CLI cases pass; longest
  call7.31 seconds. Thirteen provider-live status/thread items now use real
  persisted records rather than unrelated Git initialization; actual pushed-head
  identity and locking cases keep Git. All37 status cases pass. No backend
  transport or safety assertion was replaced by a stub.
- Complete diagnostic73dec5005 run38281140520 attempted all14 groups:
  8,571 passed/66 legitimate skips in1149.95 seconds including cleanup.
  Extended diagnostic ceilings and tracing were explicit, so this result is
  NOT normal acceptance. The complete timing profile identifies cumulative
  lease-store67.25 seconds and registry/repository74.77 seconds, among other
  expensive real-Git families; phase costs overlap across two workers.
- Scoped implementation delegates consolidated lease preview/apply/idempotence
  without repeating acquisition/renewal/release setup, retaining all original
  assertions and adding ref/OID immutability checks. Registry receipt refusal
  now precedes successful registration in the same real scenario. Nine
  credential fixtures retain real Git config but omit unrelated history;
  four refusal branches need only directories. Clone/classification/sync and
  concurrency still use real histories. Parent reviewed the actual confined
  diffs and independently verified all172 combined lease/registry/repository
  cases under normal limits in117.69 seconds including cleanup; the longest
  combined lease call15.30 seconds remains below30. Delegates made no
  production, manifest, deadline, worker, or Git-history mutations. Two more
  redundant test items are deliberately consolidated, not silently skipped.
- Normal exact c56193d8d run38285765285 completed the entire worktrees
  portfolio:8,569 passed/66 valid skips in816.73 seconds including cleanup,
  slowest pytest group172.94 seconds and all wall-clock groups below240.
  The matrix still failed because index's two-cell integration did not stop
  one service gracefully; the repeat sequence correctly stopped. This is
  a first current worktrees pass, not repeated final-head acceptance.
- Index cleanup now reuses control identities already observed during the
  real isolation checks rather than querying health again at teardown, and
  skips already-exited owned children. It preserves ownership-mismatch,
  distinct OS-assigned ports, independent liveness and the existing15-second
  graceful wait; verifies zero exit and retains any actual control error in
  a failed-stop diagnostic. Complete installation-cell family passes101 cases
  with7 platform skips; the strengthened actual two-service case passes again.
  The original failure lacked its swallowed control error, so this is a
  cleanup fidelity/reliability improvement, not an established root-cause
  claim for every historical native failure. A mandatory index patch
  changefile records the bounded test-only change.
- Normal4d2893221 run38290387827 passed index and pull-request portfolios
  but again exhausted the worktrees900-second aggregate, at902.88 seconds
  including cleanup during group10. All completed behavioral groups passed;
  repeated final-head margin remains open.
- Four lease compaction/preview/filter/race cases now receive independently
  copied bare remotes and deep-copied observations from one genuinely
  acquired/renewed/released seed per worker. Actual compaction, unchanged
  preview, kind filtering and concurrent reacquisition CAS remain per-case
  and real; compaction proves the seed ref unchanged. Every other actual
  lease lifecycle remains fresh. All18 lease-store cases pass under normal
  limits; longest call18.72 seconds and first seed setup11.57 seconds.
  No timeout, source transport, tier or mutable-state sharing changed.
- Normal693afb682 run38295666963 again exhausted900 seconds at911.64
  including cleanup; all completed behavioral groups and both companion
  portfolios passed. No repeat was dispatched and the candidate is not
  accepted.
- Public rewrite CLI's two head schemes and seven revised-legacy
  acceptance/authority inputs now reuse already-real rebased state as well,
  removing only setup creation/rebase. Actual CLI parser/squash/rewrite,
  provider observation, exact authority, generation and ref assertions remain
  unchanged. AST comparison proves canonical revised-legacy decorators and
  every operation/assertion after the input assignment remain exact.
  Fresh creation, ownership refresh, changed-creation/destination and actual
  synchronization/rebase families remain. All61 rewrite cases pass under
  original limits in164.81 seconds including cleanup; no production change
  or added timeout exception is involved.

### 2026-10-09 — Phase 6: exact-head repeat exposes a bounded pane-wrapper stall
- Reconciled head 78b44a061 passed normal hosted run 37894850444 in
  865.27 seconds including cleanup (maximum group 161.49 seconds).
  Repeat 37897693769 failed the real initial-prompt wrapper integration
  test's existing 15-second subprocess bound, not the plugin deadline.
  The failed group completed in 154.81 seconds; total elapsed before
  failure was 331.08 seconds including cleanup. This is unresolved
  evidence, not a pass or an accepted flake.
- The same real Windows launch passes locally with two workers in
  1.82, 2.09, and 2.03 seconds. No root cause is established from these
  isolated successes. Added bounded failure diagnostics for the receipt,
  captured child argv, and the last executed PowerShell wrapper statement
  so another hosted failure identifies startup, child, or teardown stage.
  Three diagnostic regressions and the real launch pass. The wrapper,
  owner Job Object, launch transport, assertions, and every timeout remain
  unchanged. Fresh hosted evidence and diagnosis remain required.
- Diagnostic-enabled normal run 37995292958 passed all 7,433 cases with
  42 legitimate skips in 695.47 seconds including cleanup; the slowest
  group took 124.73 seconds and the real wrapper took 4.26 seconds.
  The earlier isolated stall remains unproven, not silently reclassified
  as harmless. Current review also caught an omitted `os.walk` error
  callback in the docs-consistency guard; enumeration now raises the
  original error and a denied-subtree regression proves it fails closed.
  All eight docs-scan regressions pass with unchanged exclusions.
- Reconciled again onto current `dev` after the PR became conflicting.
  All 308 upstream PR test methods survive in the split modules; all seven
  newly changed upstream contracts are AST-identical after relocation,
  including the new six-case stale-reassignment regression. Retained the
  already-merged file-based pane launcher rather than resurrecting the old
  encoded-command transport; receipt/child diagnostics remain, while the
  encoded-script-only trace prefix is no longer applicable. The underlying
  original stall's cause remains unknown.
- The stronger upstream PR identity merge exposed a real seed-copy bug:
  relocation changed a published record's repository identity without
  advancing its revision, so an equal-revision on-disk record correctly
  restored the seed's old identity. The fixture now marks its intentional
  relocation as a new revision. No production merge protection is weakened.
  The reconciled contract run passed 85 cases and exposed exactly these
  two seed-isolation failures; after the fixture correction all eight
  seed/real-launch/diagnostic cases pass, and touched Python lint is clean.
- Normal current-head runs 37998223104 and 37998284414 both exposed
  the same new incoming real pre-push rejection test: template-free seed
  initialization had also omitted the conventional empty `hooks` directories.
  Restored only those empty directories in the immutable seed (no sample
  hooks or repeated Git initialization), with independent-copy hook-state
  assertions. The real refspec pre-push rejection and all four Git-copy
  regressions now pass. All 139 directly affected guard/runner/rotation
  regressions pass. No ceiling or behavioral assertion was relaxed.
- The corrected head (`3bee91142`) is now published. Standard per-PR CI
  passes in full on this exact commit, including the Windows-latest jobs.
  Re-dispatching this effort's own "Windows coverage rotation" workflow
  against this exact head -- the established practice for the runs above
  -- is no longer possible: that workflow's `select` job now hard-gates on
  `github.ref == 'refs/heads/dev'` and every checkout step pins
  `ref: dev`, a restriction added later in this same effort's Phase 6 to
  mirror `validate-and-promote.yml`'s dev-only promotion gate. It was not
  dev-restricted when the runs above were dispatched against this PR
  branch directly. Re-litigating that restriction to unblock one PR is out
  of scope here; tracked as a separate, narrow follow-up (add an optional
  `ref` input honored only on `workflow_dispatch`, leaving the `schedule`
  trigger pinned to `dev`). Evidence for this exact fix therefore shifts
  from a pre-merge hosted dispatch to the next scheduled daily rotation
  against `dev` post-merge -- which is in fact how the original hooks-dir
  regression was caught in the first place, so this is a real evidentiary
  path, not a waiver. If that rotation surfaces a new failure specific to
  this fix, it reopens as a tracked regression like any other rotation
  finding.
- The next reviewer identified a POSIX ownership gap: polling could reap
  the group leader before teardown, allowing its numeric PID to be reused.
  POSIX exit observation now uses `waitid(WNOWAIT)` so the controller's
  zombie pins its group/session identity until teardown completes; capture
  and revalidate that identity immediately before both TERM and KILL.
  Stale/reused identities fail explicitly and retain the sandbox. Windows
  Job teardown is unchanged. All 35 runner/containment regressions pass,
  including mismatch-before-each-signal and non-reaping observation.
  Native Linux checks prove both normal-controller-exit and timeout reap
  real file-holding descendants, and an exited controller remains
  observable without releasing its PID. The Linux pytest module was not
  installed in that lane; these real checks use only the standard library.
- Both normal runs on 86a74d3a0 exhausted the unchanged aggregate budget
  (900.77 and 901.08 seconds including cleanup) after new upstream
  recovery/rebase contracts expanded the real call work. Completed groups
  stayed below 240 seconds, but that is not aggregate acceptance. Reuse
  independently copied real published state for these setup-only initial
  publications, retaining every actual recovery, rebase, guarded push,
  concurrency, backup, and ref-retention operation under test. Legacy and
  explicit snapshot publication still execute their real setup path.
  Recovery passes all 17 cases; default rebase passes 13 with its existing
  16 explicit-tier skips. Both snapshot/refspec seed copies have explicit
  real commit/push/tracking isolation coverage. Local concurrent seed
  checks also hit per-item timeouts, so local success is not claimed as
  repeat performance acceptance; the same refspec check passes alone.
- Run 38083381384 still exhausted the aggregate ceiling (902.28
  seconds including cleanup), despite all completed groups staying below
  240 seconds. Publication setup reuse is not yet enough; more real cost
  reduction is owed, not another timeout increase.
- The operator approved retaining a newly merged canonical rotation flow:
  scheduled validation always targets `dev`; manual runs use a validated
  explicit ref input, pin one SHA for every matrix job, and never report
  candidate-PR failures as shared `dev` health. Removed only the overlapping
  branch-selection implementation while keeping two workers, native Git,
  diagnostic mode and unchanged budgets. Preserved the incoming encrypted
  attribution refresh contract in its split module. All 44 runner/rotation
  regressions and the real incoming attribution contract pass.
- A subsequent review's claimed `(0, 0)` falsiness is not a Python bug:
  nonempty tuples are truthy. A direct empty-native-group regression proves
  the existing probe preserves `(0, 0)` without invoking `ps`; no speculative
  production workaround was added.
- Complete diagnostic 38091020889 passed the expanded portfolio (7,901
  passes and 57 explicit/platform skips) but took 987.73 seconds including
  cleanup. Normal 38091013070 stopped at 903.39 seconds; neither is
  performance acceptance. Reported call work totals 1,361.20 seconds,
  versus 192.70 seconds setup. The largest modules include recovery
  (86.33), publication (86.19), landing (80.72), providers (79.71),
  tracking (71.65), preconditions (64.68), and lease-store (57.26).
- Twenty fork-rerun/tracking scenarios still called initial publication
  solely to prepare state. Bound only those scenarios to independent real
  published copies and let the shared helper reuse existing publication;
  all actual fork movement, feedback commit, rerun, concurrency and
  stale-record operations remain unchanged. AST equivalence confirms
  both entire test modules differ only in fixture binding names.
  Publication's full 32-case module passes. The combined and tracking-only
  local runs still exceed existing bounds or lose workers to per-item
  timeouts; a directly selected changed stale-reassignment case passes.
  Those are unresolved performance evidence, not permission to lift limits
  or claim the host is harmless. Hosted full validation remains required.
- The operator rejected a four-worker experiment and explicitly directed
  consolidation by valuable coverage. Audited independent PR, landing,
  and provider tracks. Fifteen record-merge cases now use real isolated
  YAML records, removing irrelevant Git initialization/publication,
  fork movement, remote deletion and feedback commits; every identity,
  revision, stale-write and serialization assertion remains. The separate
  real locked push-identity and fork-publication tests retain Git boundaries.
  All 15 record-only cases pass with calls at 0.18 seconds or less on
  this host, instead of multi-second Git scenarios.
- The existing squash-later-edit scenario now also checks the probe leaves
  refs unchanged before the later edit, preserving the original assertion
  without rebuilding its topology in a separate test. It passes. Combining
  full base-aware/ref-deletion cases crossed the existing 30-second item
  bound locally, so those attempted merges were reverted rather than raising
  the timeout. The recovery sequence merge was also withdrawn without
  claiming validation because another live runner owns the host slot.
  Record-only narrowing and the one safe topology consolidation are the
  retained slice; further consolidation still needs hosted cost evidence.
- Extended that same boundary to fourteen manual registration/status cases:
  real YAML/config resolution, attribution freeze, identity clearing and
  persistence remain, but no irrelevant Git repository is built. All fourteen
  pass. Four provider-ready transition cases similarly use a persisted manual
  PR record instead of publishing Git branches before testing a provider
  snapshot; draft/undraft, failure, no-op and legacy-label assertions all
  pass, with call time no more than 0.06 seconds and setup no more than
  0.07 seconds. Create/publication integration remains separately covered.
  Normal run 38104483993 still exceeded900 (902.48 including cleanup)
  before this further narrowing; it remains a failure.
- Review identified that the live-only zombie filter also fed the process-slot
  budget. Split those measurements: zombies count toward `max_processes`,
  while only teardown excludes non-file-holding zombies. Native Linux checks
  prove a retained zombie consumes one process slot but zero live teardown
  slots, and real descendant cleanup still works on normal exit and timeout.
  Direct limit regression supplies 130 slots against the128 bound.
- Normal run 38106876346 completed the narrowed suite in779.06 seconds
  including cleanup (7,900 passes,57 legitimate skips; max group130.15),
  but repeat38108676486 exhausted900 at901.94 seconds. The first pass's
  120.94-second margin is real for that run, not yet repeatable acceptance.
  Retained newly merged canonical local-only/bounded guard implementations
  and canonical no-window containment launch after explicit operator
  approval; our overlapping guard hunks/tests were removed. Reconciled
  runner/guard tests pass117 cases with one Windows-inapplicable skip.
- Consolidated five read-only inconclusive head/ref/upstream checks into one
  real Git topology. Every original invalid-input branch still asserts
  refusal with a distinct case label, including the genuine `rev-list`
  error path. It passes in1.66 seconds call plus5.13 session setup locally,
  removing four repeated topology copies and remote relinks without mocking
  away the Git failures. Normal hosted run38111811512 on the reconciled
  predecessor remains in flight; this further consolidation needs its own
  exact-head proof.
- Current-head38112134295 passed the complete portfolio in725.05 seconds
  including cleanup (7,899 passes,57 legitimate skips; max group126.95).
  Identical-head repeat38113440807 instead surfaced an unrelated
  ten-second host process census inside a pair-integrity doctor test.
  Narrowed that test's unrelated daemon-health reporting boundary; pair
  discovery/repair/JSON persistence remain real and pass in0.26 seconds.
  The production census timeout is unchanged and its own tests remain.
- Review caught one explicit exhaustive snapshot-rebase case selected by
  a hardcoded scheme rather than parametrization. It now selects the fresh
  unpublished snapshot fixture, retaining its real initial publication.
  The explicit-tier legacy case passes. Linux runner CI also exposed an
  incoming fake-process test that still mocked the pre-identity helper
  signature; updated its identity/exit/teardown doubles without changing
  production ownership checks. All40 local runner/containment cases pass
  with one Linux-only skip; native Linux verifies the zombie-heavy limit.
- Head2a35ab6ba completed normal38116527309 at833.98 seconds including
  cleanup (7,899 passes/57 legitimate skips, max group136.89); repeat
  38116536837 is still queued. Current Linux CI exposed eight installer
  tests asserting the obsolete payload CWD; the safe canonical installer
  runs its coordinator from the profile using absolute inputs. Retained
  the already-merged upstream maintenance-CWD correction and environment-
  scoped admission after explicit operator approval, and aligned only the
  stale test CWD expectation. Four real Windows reserved-exit cases pass,
  with four existing Unix skips; a patch changefile accompanies this
  agent-index test correction. Reconciled runner/rotation tests pass50 cases
  with one Linux-only skip. Fresh exact-head Linux and Windows evidence
  remains required after this rebase; no historical pass substitutes for it.
- Removed unnecessary per-file metadata copies from the precondition
  topology, retaining Git file contents/refs/reflogs and real independent
  publisher/object isolation. Its mutation regression and all60 precondition
  cases pass under the unchanged bounds. The analogous landing-family
  experiment could not complete its full local validation within300 seconds,
  so that unproven change was withdrawn. No ceiling was raised.
- Review identified an early aggregate-timeout return between groups that
  exited before the shared final elapsed report. It now breaks with124,
  joins cleanup, exits the sandbox, and reaches the same cleanup-inclusive
  report as every other timeout. Direct regression proves second group never
  starts and65.00 seconds final cleanup-inclusive elapsed is reported.
  All41 runner/containment cases pass locally with one Linux-only skip.
- Consolidated three duplicate malformed-codename publication scenarios:
  the retained real stale-marker publish also asserts no marker token and no
  attribution-head stamp, preserving each distinct security assertion while
  removing two repeated publications. The non-string guard is pure Python
  and no longer asks for an unrelated Git fixture. Folded unchanged caller-
  body preservation into the explicit no-attribution override scenario,
  removing one more duplicate publication. All four retained scenarios pass.
- Further narrowed five marker-policy branches to the real persisted
  `_finish_auto_open`/provider boundary: encrypted identity, first-touch
  codename allocation/failure, malformed stale marker and disabled attribution.
  Separate initial-create and implicit-default integration/preflight checks
  retain full Git-to-provider wiring. All five retain their original provider,
  payload and persisted-record assertions, passing at0.08 seconds or less
  call locally rather than repeatedly publishing Git to test marker policy.
- The operator approved retaining the stronger already-merged agent-index
  reserved-exit regression (absolute coordinator/context assertions), dropping
  our duplicate ancillary test/changefile. No agent-index diff remains.
  Prior normal38123503844 and38125538648 still timed out at901.45 and
  906.55 seconds; neither is accepted. More exact-head proof remains owed.
- Provider-boundary candidate e61d41061 completed normal38179596848 in
  679.56 seconds including cleanup (7,944 passes/58 legitimate skips,
  maximum group110.73). Required rebase preserved incoming publication
  ownership/revision and terminal active/open state checks, but its exact-head
  runs38181136009/38181140945 exposed new fixture relocation obligations:
  the recorded attested rewrite destination must relocate with a copied
  remote, and synthetic fork movement must attest its new destination.
  Both now follow the actual copied remote identity with explicit record
  revision updates; fresh/legacy target tests clear ownership when their
  contract says no attestation exists. Production repoint/rewrite fences
  remain unchanged. All19 rebase/seed cases pass (16 existing explicit-tier
  skips), and all53 publication/state cases pass.
- The hosted authority-registration subprocess also failed because its
  Python child wrote a checkmark to a cp1252 pipe. The test now launches the
  real child with explicit UTF-8 and decodes the same encoding; concurrent
  registry/ledger assertions are unchanged and pass. No normal timeout or
  worker count changed. These fixes require fresh complete hosted evidence.
- Normal38183013006 reached the newly expanded group9 and exposed one
  observation-race test whose synthetic reassociation omitted the required
  record revision bump. The stronger production merge correctly kept the
  prior equal-revision identity, so the test was not actually creating its
  stated concurrent change. Advanced the synthetic reassociation revision;
  all three observation contracts pass, including rejection of the genuinely
  changed identity. No production identity protection changed. The failed
  run's group9 also took254.91 seconds, missing the240 headroom target;
  this is still unresolved performance evidence, not acceptance.
- The operator approved retaining upstream's legacy/unattested synthetic
  fork scenario rather than our reattestation variant, while keeping copied
  published-state relocation genuinely attested. Incoming config-bearing
  existing-push tests were preserved in the split module and both pass.
- Eleven fork-rerun/locked identity cases now independently copy one immutable
  legacy topology (separate fork objects, pruned origin feature ref and
  feedback commit), eliminating repeated setup-only fork movement/commits.
  The seed prepares refs through real local Git plumbing; tests still perform
  the actual rerun, repoint, lock, publication and concurrency operations.
  Direct fork mutation/push proves seed isolation; rerun and locked repoint/
  identity checks pass. Initial full-copy fork seed setup exceeded30 locally,
  so replaced its redundant setup pushes with copied objects and explicit
  ref preparation; validated setup is19.22 seconds, later copies1.21-2.35,
  and checked calls2.14-8.61. Combined publication validation still exceeds
  the local300 group bound; no ceiling increase or complete-pass claim.
- Copied published fixture relocation now derives its identity from the exact
  local URL the fixture just installed instead of repeatedly rediscovering
  that known URL through Git subprocesses. The real destination probes and
  equality assertion remain in seed-isolation tests and all guarded
  production operations. All20 seed/rebase cases pass (16 existing
  explicit-tier skips), preserving attested source identity without adding
  another unrelated setup probe per copied test.
- Review correctly identified macOS Python3.10-3.12 lacks `os.waitid`.
  Replaced that platform-specific admission requirement with a portable
  private-pipe controller protocol: the real command reports status while
  the owner remains alive until verified group teardown, so its PID still
  cannot be recycled before signals. The workload does not inherit the
  reporting descriptor or control environment. Stale-group revalidation,
  live-only teardown and zombie resource accounting remain unchanged.
  All44 local runner/containment cases pass with one Linux-only skip;
  native Linux proves real normal/timeout descendant reaping and absence
  of control-env leakage. Added a focused change-scoped native macOS3.12
  runner lane to prove the previously unsupported platform directly.
  Its hosted result is still required; no untested fallback is claimed.
- Native macOS3.12 lane114751708302 on e2699c86f actually executed
  the portable ownership/cleanup checks:43 passed,2 platform skips in9.22s.
  This is direct live-platform evidence, not a Linux proxy for missing
  `waitid`. Full Windows candidate38230533458 and prior known-identity
  candidate38227561414 are still queued behind hosted capacity.
- Fork candidate38226500432 failed the real pane-wrapper15-second bound
  after receipt and captured child arguments already existed. It therefore
  stalled after command startup/argument delivery, not transport decoding.
  Added trace only to the copied real wrapper to expose its last executed
  statement on timeout, retaining executable logic, ownership job and bound.
  The real launch plus all three bounded diagnostics pass locally; the
  underlying hosted after-child stall remains unresolved until its next
  failure supplies that evidence.
- Normal rotation no longer requests a verbose line for every case:
  unbuffered group/progress, complete failure reports and slowest30 item
  durations remain. Explicit diagnostic mode retains verbose/all-duration
  output. Ten rotation contracts pass and test selection/worker/resource
  settings are unchanged. This removes avoidable output volume; no runtime
  acceptance is inferred until hosted exact-head evidence completes.
- Known-identity candidate38227561414 reached group9 and exposed a split
  caller retaining the pre-mutation in-memory record after canonical fork
  movement cleared ownership and advanced its revision. Reloaded the
  persisted record after every direct setup move, matching the incoming
  upstream scenario rather than weakening equal-revision merge protection.
  The actual recorded-fork-disappearance contract passes. Its prior full run
  remains a failure; current full portfolio proof is still required.
- Shared the existing real-YAML record fixture with six newly added
  native/legacy state-preservation cases, removing unrelated Git creation/
  publication while retaining byte-for-byte non-mutation and terminal/live
  classification assertions, including raw legacy YAML loading. All20
  combined manual/state cases pass; state calls are0.05 seconds or less.
- The prompt integration test had launched an extra PowerShell merely to
  execute the generated quoted PowerShell command. It now asserts the real
  `-File` builder shape and runs the same quoted launcher/manifest once;
  actual launcher, wrapper, native ownership job, receipt and child argv
  remain exercised. All four launch/diagnostic cases pass, with real call
  reduced locally from4.94 to3.25 seconds. No bound changed and no hosted
  stall is declared solved without fresh full evidence.
- Quiet predecessor38234236124 repeated the already-fixed stale fork-record
  failure and exposed an ancillary callback-burst test racing its own
  three-second callback hold against twenty serial disk-backed registrations.
  Snapshot production is now gated until all registrations complete, creating
  a genuine twenty-event burst before exercising the same eight-worker bound
  and no-loss assertions. Callback hold, test/group/plugin bounds and runtime
  implementation are unchanged. All170 pull-request plugin cases pass;
  a patch changefile records the test-only correction. No competing external
  contributor implementation was displaced.

### 2026-10-08 — Phase 6: complete hosted timing evidence and a second cost-reduction candidate
- `dev` advanced during review and contained a broader, already-merged
  watch-daemon correction (joined shutdown, unique atomic-write files,
  serialized replacement, and stronger restart/concurrency tests).
  The operator approved retaining that canonical implementation and removing
  only this PR's superseded watch-daemon slice/changefile. Owned history was
  backed up, net-squashed, and rebased once; both the upstream documentation
  additions and dependency version updates were preserved. No competing fix
  was forced over the upstream contribution. All 158 directly affected
  regression tests pass after reconciliation; fresh review and hosted
  verification remain required for the rewritten head.
- The review-fixed full Windows worktree run 37885149671 passed all
  intended cases in 540.84 seconds including cleanup (maximum group
  95.04 seconds). Its repeat 37886775797 passed worktrees too, but
  exposed a separate real `agent-pull-requests` restart-test race:
  two in-process daemon generations remained live and attempted the same
  subscription temporary file, causing a Windows PermissionError.
  The daemon pollers now honor the existing shutdown event promptly; the
  restart test shuts down and joins the old generation before constructing
  the replacement, preserving durable subscribers. All 53 plugin tests pass
  in the contained runner; a mandatory patch changefile accompanies the fix.
- The revised Copilot request workflow hit its licensed-account REST quota,
  not a code/test failure. Observed reset was 05:26:38 UTC; an owned bounded
  backoff waited until that time before one failed-job retry. No token/account
  switch, bypass merge, or repeated rate-limit retry was used. The actual
  subsequent reviewer verdict remains required.
- In response to the operator's pre-push cost question, timed every actual
  guard rather than blaming the push bound. The unchanged 16-check sequence
  took 244.50 seconds: headless-launch 89.15, bridge contracts 44.75,
  docs consistency 28.64, and install contracts 27.40 seconds.
  Whole-tree traversal, duplicate AST work and repeated immutable-evidence
  probes dominated, not pytest execution.
- Preserved the full gate while reducing work: existing exclusions are
  pruned during enumeration; headless candidates parse once and duplicate
  contents share analysis without sharing path-specific diagnostics; bridge
  evidence avoids repeatedly probing the same available full commit IDs.
  Missing evidence/mutable refs remain rechecked and recovery behavior is
  preserved. Exact candidate/diagnostic and recovery regressions pass
  (102 tests). The final same-machine 16-check sequence passed in 57.79
  seconds, versus 244.50 seconds before. No publication or test timeout was
  raised, no guard was removed, and no policy exclusion was widened.
- The actual Copilot review raised a Medium teardown race: closing a
  kill-on-close Windows Job Object initiates asynchronous descendant
  termination, so group deletion could begin while a descendant still held
  files. Fixed the entire cleanup contract: explicit Job termination and
  raw active-process-count confirmation before handle close, POSIX live-group
  confirmation after signals, controller reaping after forced kill, and no
  cleanup scheduling on unproven teardown. A typed failure retains the named
  sandbox instead of deleting beneath a possibly live process. Native Windows
  regressions cover file-holding descendants after both normal controller exit
  and timeout; portable tests cover delayed membership and retained failure
  state. Review remains gated on the revised verdict and fresh hosted evidence.
- Strict same-code normal runs 37865342941 and 37866787409 both
  completed every intended case: 7,430 passed and 42 legitimate skips
  across all 13 groups. Actual plugin elapsed time INCLUDING joined
  cleanup was 567.25 and 857.44 seconds respectively, below the
  unchanged 900-second ceiling. Maximum group times were 90.26 and
  148.38 seconds, well below the 240-second headroom target.
  The slower repeat has 42.56 seconds of aggregate margin; do not
  describe the first run's 332.75-second margin as guaranteed. Readiness
  now has real repeat evidence, but review, merge, promotion, and scheduled
  firing remain distinct gates; another normal run is planned while review
  proceeds.
- Publication encountered the existing 180-second Git-push bound while
  running all pre-push checks; no hook was skipped or timeout enlarged.
  The feed-neutrality guard repeatedly walked the full checkout once per
  pattern, including local test environments before filtering exclusions.
  It now walks once and prunes only its original excluded directories.
  Actual candidate sets are exactly identical (499 paths), measured
  13.84 seconds before versus 1.51 seconds after; all 14 guard tests
  pass. This removes a directly encountered publication bottleneck without
  weakening feed policy or changing test budgets.
- Normal run 37859573130 attempted every intended case (7,430 passed,
  42 skipped), with all 13 pytest group times below 240 seconds
  (maximum 187.39 seconds, total 873.49 seconds). However, its
  first-group-to-final-status elapsed time was approximately 955.76
  seconds, including 52 seconds of final sandbox cleanup. This is NOT
  aggregate-budget acceptance even though the old runner reported success.
- Fixed two real budget blind spots: an exited subprocess no longer
  bypasses the elapsed wall check, and the plugin deadline is checked after
  all cleanup. Cleanup of a fully reaped group's owned directory now
  overlaps the next group through one bounded thread; all futures are joined
  before outer sandbox removal and errors propagate. Short Windows `t/gN`
  paths remain unchanged, reusing the framework's readonly-aware removal
  rather than adding a path-length-increasing directory suffix.
  Deadline/cleanup/rotation regressions pass (33 tests). A real two-group
  execution with the final short paths passed all 57 cases in 84.48 seconds
  including cleanup;
  the complete hosted candidate must still prove actual aggregate headroom.
- Complete uncensored candidate diagnostic 37834155432 passed every
  group, but still took 1,714.21 seconds of pytest time. Durations above
  0.05 seconds attribute 1,319.85 seconds to calls and 173.27 seconds
  to setup; further fixture-only optimization cannot plausibly close the
  gap by itself. The operator explicitly approved trying bounded
  two-worker Windows execution plus further actual cost reduction, keeping
  all existing budgets. The rotation now opts only `agent-worktrees` into
  `pytest-xdist -n 2 --dist=loadfile` initially, subsequently changed to
  `-n 2 --dist=worksteal` in the current rotation; default local execution and all
  other plugins remain serial. Both workers stay in the same containment
  job. This is an experiment awaiting hosted acceptance, not a budget
  change or declaration of completion.
- Removed 32 repeated initial `create_pr` calls that merely prepared
  post-publication, push, and finalize scenarios. A real publication is
  performed once per pytest worker session; each case receives independent
  copied Git stores and a relocated tracking record. Fresh-publication and
  tested rerun calls remain real. Mechanical inverse AST checks prove
  the transformed cases preserve all later calls, assertions, decorators,
  and parameterization. Two added mutation regressions verify the published
  seed's complete byte snapshot remains unchanged across copied commit,
  push, and tracking updates.
- Contained two-worker checks pass under unchanged limits: 38 publication
  and topology cases in 280.72 seconds, then all 35 parametrized push/finalize
  cases in 198.84 seconds locally. The rotation worker-selection guard
  (9 tests), focused lint, and install-contract gate pass. These timings are
  not hosted full-suite acceptance; the complete hosted two-worker run is
  still required before readiness.
- The uncensored profile also identified `test_terminal_conclusion.py`
  (73.89 seconds of reported calls), whose helper rebuilt the same Git
  topology inside each call. It now copies an immutable session topology
  and recreates only per-case tracking/session state. Mechanical inverse
  AST checks preserve every existing operation/assertion/decorator; an
  additional real commit/push/worktree-removal regression leaves both the
  seed and sibling copy byte-identical. All 53 terminal-conclusion cases
  pass locally under the same contained limits in 83.53 seconds.
- First normal two-worker hosted run 37852839930 still exhausted the
  900-second aggregate limit in group 9. Groups 1-8 passed in 64.11,
  39.48, 114.33, 217.55, 58.36, 78.63, 55.46, and 186.10
  seconds; every completed group met 240-second headroom, but the
  remaining groups were not attempted. File-affinity scheduling left
  uneven call-cost loads, so the two-worker experiment now uses work
  stealing. The CI test process also resolves the same-version packaged
  native Git to avoid Git for Windows' launcher
  process; no persistent PATH/config changes are made.
- All 124 modified contracts pass locally with work stealing and native
  Git in two contained groups (67 in 272.25 seconds, 57 in 50.06
  seconds), including the real pre-push hook and isolation regressions.
  A prior accidentally broadened local selection included the entire
  finalize-precondition module and exhausted its 300-second group budget;
  it did not complete and is not acceptance evidence. The final hosted
  candidate must still attempt every intended case under normal budgets.
- A focused native-Git probe removed every other Git distribution directory
  from the process PATH and still passed the real pre-push hook contract.
  Therefore the final CI optimization prepends only native Git's binary
  directory, leaving shell lookup priority unchanged rather than broadly
  preferring Git's shell/tool directory.
- Hosted run 37858035629 exposed a setup bug before tests started:
  `Get-Command git -CommandType Application` returned three executables,
  which PowerShell concatenated into one invalid command name. The
  resolver now selects the first PATH match explicitly. Reproducing the
  three-match PATH locally and executing the actual CI setup block passes;
  the regression guard and all 9 rotation tests pass. This failed run is
  setup evidence only and does not establish any suite timing.
- The unchanged-budget combined candidate run 37831230027 still fails
  aggregate acceptance, not an assertion: its first seven groups passed
  in 63.52, 47.92, 149.31, 280.58, 73.69, 72.85, and 49.10
  seconds; group 8 received only 154.218 seconds remaining and timed
  out there. Group 4 also misses the 240-second target. No claim of
  performance completion is supported. Diagnostic mode now emits every
  setup/call/teardown duration over 0.05 seconds, rather than only the
  top 30, so the next complete profile can attribute aggregate cost
  rather than extrapolating from a censored summary.
- Hosted diagnostic run 37742776238 attempted all 12 Windows groups:
  7,425 passed, 42 skipped. Group 7 took 531.79 seconds and group 4
  took 241.44 seconds; total pytest time was 1,714.58 seconds.
  Default-budget acceptance remains open.
- Reused immutable finalize landing/precondition Git seeds with independent
  copied object stores, remote retargeting, and commit/push isolation
  regressions. This removes approximately 696 net repeated setup Git calls.
  The original 86 behavioral test bodies remain unchanged.
- Split all 307 methods of the oversized PR-operations module into 12
  explicit behavioral-contract modules. Independent AST multiset comparison
  proves every test body, signature, decorator, and parameterization is
  preserved. This improves grouping granularity; it does not itself reduce
  aggregate work.
- Identified a separate runner-side cost: the storage-budget probe repeated
  filesystem stat calls for every accumulated temporary file every two
  seconds. Cached `os.scandir` metadata measured 0.027 seconds versus
  1.612 seconds for the same 15,766,315-byte representative Windows tree.
  Accounting limits, sampling cadence, subprocess containment, and normal
  timeout budgets are unchanged. Nested-size and no-link-following/removal
  regressions plus the existing persistent-environment-drift check pass.
  This microbenchmark is not full-suite acceptance.
- Removed unused sample templates and automatic maintenance from the tiny
  shared PR seed and avoided unnecessary file-metadata copies. The two
  real-Git PR seed isolation regressions and a real create/push contract
  passed focused contained runs. The new finalize regressions initially
  inferred bare repos implicitly; fixed their probes to use explicit
  `--git-dir` without weakening `safe.bareRepository` policy.
- Focused contained validation of all copied topologies now passes
  (4 regressions, 34.89 seconds). All split PR contracts collect
  (311 parametrized cases), and 23 selected helper/CLI/real-publication
  cases pass in 25.68 seconds. Focused F/E9 lint and install-contract
  validation also pass. These are prerequisites, not full-suite acceptance.
- Local full finalize validation remains incomplete: a normal-budget run
  timed out during `test_a_commit_after_a_sync_still_counts` before reaching
  the second module. Preserve this failure and obtain hosted evidence for the
  combined candidate rather than claiming local or diagnostic-mode success
  meets the target. Publication remains draft; neither the performance goal
  nor the scheduled-event correction is landed.
- Documentation impact: `TESTING.md` documents independent fixture copies,
  the PR contract split, and cached storage probes. No production plugin
  behavior, installer, or fresh-machine provision path changed, so the
  clean-room tier does not apply. Hosted Windows full-suite validation is the
  real external lane and must clear unchanged budgets before readiness.

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
Windows username pasted verbatim into an earlier journal
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
