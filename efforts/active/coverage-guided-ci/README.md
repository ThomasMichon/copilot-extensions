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
      classifies each touched file's `--unified=0` diff as a pure
      insertion/deletion (remappable) or as containing at least one hunk
      that both removes and adds lines (content actually changed --
      invalid); `remap_or_invalidate_baseline` applies that per file across
      a whole baseline, dropping invalidated files from the ``coverage``
      map entirely -- which `selection.select_tests` already treats as
      `no_baseline_entry`, so no changes were needed there to make
      Phase 2's output usable by Phase 0's existing selector.
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
  resolved baseline covers, diffs it (`git diff --unified=0`) between the
  baseline's own `measured_commit` and the fork point: a diff composed
  entirely of pure insertion/deletion hunks (no hunk both removes and adds
  lines) is cleanly remappable -- every covered line number is translated
  through the cumulative offset, with a line that was itself deleted
  dropping out silently (correct: it no longer exists to be covered). A
  diff with even one hunk that genuinely replaces content invalidates that
  file's attribution entirely, dropping it from the resulting baseline's
  `coverage` map -- which `selection.select_tests` already treats as
  `no_baseline_entry`, forcing that file's own smoke/coverage-debt fallback
  for free. No changes were needed to `selection.py` itself to make
  Phase 2's output immediately usable by Phase 0's existing selector --
  confirmed by construction, not by assumption (see the integration test
  below).
- Uses `--unified=0` specifically because it makes "a hunk with both
  nonzero old_len and nonzero new_len genuinely replaced content" a
  reliable signal -- with default context lines, an insertion sitting next
  to an unrelated unchanged line could otherwise look like it "replaced"
  that context line.

**Verified directly, per the Plan's own explicit ask** ("unit-test against
a constructed history with real intervening line insertions/deletions, not
just a same-content forward-move case"): `tools/test_coverage_guided_selection.py`
gained `TestIsAncestor`, `TestResolveNearestBaseline`,
`TestComputeFileRemap`, and `TestRemapOrInvalidateBaseline` -- each builds
a real, throwaway git repo via subprocess (actual commits, not mocked
diffs) covering: a real ancestor/non-ancestor/self pair and an unreachable
commit; the newest-qualifying-generation resolution case and the
none-qualify case; pure insertion, pure deletion, content replacement, a
mixed insertion-then-replacement hunk set (confirming the whole file
invalidates, not just the replaced hunk's own range); and a full
three-file integration pass (one untouched, one cleanly-shifted, one
content-replaced) plus a "the only covered line was itself deleted" edge
case and a no-mutation check on the input baseline. Full
`tools/test_coverage_guided_selection.py` suite: 46 passed, 5 skipped
(the pre-existing opt-in real-subprocess integration tests, unaffected).
`ruff check --select F,E9` (this repo's actual required lint selection)
clean.

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
