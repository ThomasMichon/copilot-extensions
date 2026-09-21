# Module Componentization Discipline

- **Slug:** `module-componentization-discipline`
- **Repo:** copilot-extensions (repo-wide: every plugin, `libs/`,
  `worktree-manager/`, and `tools/`)
- **Branch(es):** `worktree/tmichon-cloud1-win-20260916-140121-80aa` (Phase 0)
- **Created:** 2026-09-16
- **Status:** Active
- **Vision:** none dedicated — this effort establishes the standing
  componentization policy itself (`CONTRIBUTING.md`'s Componentization
  bullet), rather than advancing a pre-existing vision document.
- **Umbrella issue:** #2805
- **Authorship:** AI-assisted; reviewed and directed by the repository owner.

## Guiding Intent

A module's size should be a proactive design decision, not something a guard
only catches after the fact. `tools/check-module-size.py`'s 1,000-line hard
cap + shrink-only baseline (added after `agent-dispatch`'s `queue.py` reached
~7,200 lines with no guard watching it) makes *new, unbounded growth*
impossible — but the baseline it introduced also *grandfathered in* 70+
distinct pre-existing files (108 baseline entries once vendored copies are
folded in), several of them an order of magnitude over the cap, with no
active pressure to shrink any of them. This effort turns that backstop into a
proactive discipline: agents and contributors should recognize a module
accreting unrelated responsibilities and split it *as they touch it*, not wait
for a line-count failure — and the same standard should extend to shell,
PowerShell, and TypeScript sources the automated guard doesn't scan yet, and
to test modules via behavioral-contract splitting with filterable attribution.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Host worktree | Owns the effort, Phase 0 docs/tooling, and coordinates later phase PRs | Current agent-worktrees session |

## Coordination

- **Topology:** one host worktree for Phase 0; later phases (module/test
  splits) each land as their own independent PR against `main`, claimed off
  the Plan below.
- **Host (owns PRs):** the current worktree for Phase 0; whichever
  worktree/session picks up a later phase owns that phase's PR.
- **Delegates:** none yet.
- **Handoff:** each phase updates this effort's Plan/Journal at its
  boundary; `python tools/rank-module-size.py` is the live source of truth
  for what's next, not a frozen snapshot in this file.

## Context

Triggered by reviewing `namankanakiya/copilot-extensions#2785` on the
`odsp-web-harness` side: its `guards + lint` CI check was failing on
`worktree-manager/.../engine.py` exceeding its own grandfathered ceiling by 48
lines — a regression introduced by an unrelated, already-merged PR (#2788/
#2794). That a single feature PR could push a baselined file further over its
ceiling and land anyway (the baseline is per-file shrink-only, but nothing
stops a file from growing right up against its existing ceiling commit by
commit until it tips over) is exactly the failure mode a *proactive*
discipline — not just the existing reactive guard — is meant to prevent.

### Current pecking order (snapshot, 2026-09-21, post-`agent-worktrees __main__.py` worktree-operations slice)

`python tools/rank-module-size.py --limit 20` (vendored duplicates folded in
— re-run before starting a phase, this list moves; it already has once per
PR merged during this effort — treat it as a live command, not a frozen
table):

| Lines | Over cap | File | Notes |
|------:|---------:|------|-------|
| 13,669 | +12,669 | `plugins/agent-worktrees/src/agent_worktrees/__main__.py` | Seventh dedicated slice landed: the worktree-operations block now lives in `list_cli.py`, `claims_cli.py`, `follow_ups_cli.py`, and `worktree_ops_cli.py`. The largest remaining seam is now even more clearly the launch/session-control core (`copilot`, especially `resolve`'s picker/remux/handoff planner), which has been deferred by three fresh passes and now wants a dedicated design pass rather than another blind extraction attempt |
| 9,267 | +8,267 | `worktree-manager/.../picker_tui/engine.py` | The file that motivated this effort (#2788/#2794 regression); it drifted again while this slice was in flight, so the baseline was manually widened (9191 → 9267) to restore a green full-tree guard pending its own future split |
| 9,169 | +8,169 | `libs/installation-context/installation_context.py` (+17 vendored copies) | Split the **canonical** copy only; `sync-installation-context.py` propagates to every vendored copy |
| 6,873 | +5,873 | `plugins/agent-bridge/src/agent_bridge/__main__.py` | The live-orchestration CLI-registration giant; still a dedicated-slice item, not a quick opportunistic split |
| 6,751 | +5,751 | `plugins/agent-bridge/src/agent_bridge/session_manager.py` | |
| 5,872 | +4,872 | `plugins/agent-worktrees/src/agent_worktrees/tracking.py` | |
| 5,161 | +4,161 | `plugins/agent-index/scripts/cell-runtime.py` | |
| 4,886 | +3,886 | `plugins/agent-dispatch/src/agent_dispatch/__main__.py` | Already partially split (`producers_cli.py` et al. extracted) — still a strong model for how far a CLI-registration split can continue |
| 4,692 | +3,692 | `plugins/agent-codespaces/src/agent_codespaces/__main__.py` | CLI registration surface |
| 4,508 | +3,508 | `plugins/agent-dispatch/src/agent_dispatch/queue.py` | The original motivating case for the cap itself |
| 3,512 | +2,512 | `plugins/agent-dispatch/src/agent_dispatch/supervisor.py` | |
| 2,940 | +1,940 | `plugins/agent-bridge/src/agent_bridge/agent_registry.py` | |
| 2,680 | +1,680 | `plugins/agent-worktrees/src/agent_worktrees/sessions.py` | |
| 2,583 | +1,583 | `plugins/agent-codespaces/src/agent_codespaces/config.py` | |
| 2,429 | +1,429 | `tools/clean-room/scenarios/agent-index-installation-cells/scenario.py` | Clean-room fixture, not production code — lower urgency |
| 2,369 | +1,369 | `tools/clean-room/scenarios/progressive-context-disclosure-baseline/fixture.py` | Clean-room fixture, not production code — lower urgency |
| 2,307 | +1,307 | `plugins/agent-worktrees/src/agent_worktrees/pr_ops.py` | |
| 2,207 | +1,207 | `plugins/agent-dispatch/src/agent_dispatch/supervisor_daemon.py` | |
| 2,195 | +1,195 | `plugins/agent-worktrees/src/agent_worktrees/reconcile.py` | |
| 2,124 | +1,124 | `plugins/agent-worktrees/src/agent_worktrees/session_projection.py` | |

**Suggested next pick (Phase 2, next slice):** the remaining `agent-worktrees`
`__main__.py` work is no longer "find another obvious lower-risk seam" — this
slice just consumed that seam. What remains is the live launch/session-control
core: `cmd_copilot`, and especially `cmd_resolve`'s nested picker/remux/
handoff/create-or-resume/launch-plan flow. Treat that as a **dedicated design
pass**, not another blind extraction: first define the state-carrying
abstractions that would let its picker, restore/remux, and launch-planning
sub-flows move independently without changing behavior. If that design budget
isn't available, pivot entirely to a different large production module
(`plugins/agent-worktrees/src/agent_worktrees/tracking.py`,
`plugins/agent-worktrees/src/agent_worktrees/pr_ops.py`, or the canonical
`libs/installation-context/installation_context.py`) rather than taking a
third/fourth "maybe it will untangle itself" swing at `resolve`.

Full list: `python tools/rank-module-size.py --limit 70`. Files within a small
margin of their own ceiling (most likely to tip over next from unrelated
feature work, per the `engine.py`/`agent-bridge __main__.py`/`coordinator.py`
incidents above): `python tools/rank-module-size.py --near-cap 25`.

## Request

Verbatim from the operator:

> Let's start an effort to holistically deal with the module-size issue. We
> need agents to proactively break down modules as they go: modules should
> have at most a couple of related classes or functions in them, and should
> devise ways to factor out larger flows, like CLI `__main__.py` registration
> systems, into smaller, modular ones. Python, SH, PS1, TS, etc file can all
> be broken down safely. In our effort, produce overarching coding
> guidelines for this, then produce a skill/runbook for post-processing
> scripts to do breakdowns according to our rules. Finally, identify a
> pecking order of our biggest offenders (largest, or closest to their
> allowances), and prioritize breaking them down. The same also applies to
> test suites: we want smaller, nimbler test files instead of huge runner
> modules. Tests need attribution and tagging, to make it easier for runners
> to filter to the tests they want to run.

## Plan

### Phase 0 — guidelines, skill, tooling, and test attribution (done)
- [x] Extend `CONTRIBUTING.md`'s Componentization bullet: cap is a backstop
      not a target; name the CLI/route-registration-table shape explicitly
      (model: `agent-dispatch`'s existing `producers_cli.py`/`recipes_cli.py`/
      `supervise_cli.py` extraction); state explicit cross-language scope
      (`.sh`/`.ps1`/`.ts`).
- [x] Add the `componentizing-modules` skill/runbook under
      `plugins/customizing-copilot/skills/` (seam-finding, safe extraction,
      re-validation including `--refresh-baseline`, vendored-copy-canonical
      handling, and the parallel test-module procedure).
- [x] Add `tools/rank-module-size.py` (ranks distinct baselined offenders by
      size or by proximity to their own ceiling; folds identical vendored
      copies into one row).
- [x] Add the informational `@pytest.mark.contract(name)` marker
      (`tools/pytest_portfolio_guard.py`), documented in `TESTING.md`
      alongside the existing enforced `portfolio_tier`/`effect` markers.
- [x] Register the new skill in `plugins/customizing-copilot/README.md`'s
      skill table; verify `check-docs-consistency.py` and
      `check-runbook-references.py` both pass.
- [x] Capture the current pecking order snapshot and this effort's plan.

### Phase 1 — pilot split (done)
- [x] Validate the `componentizing-modules` runbook against a real,
      repo-scale split before touching a live orchestration CLI. Picked
      `plugins/customizing-copilot/skills/reviewing-customizations/scripts/scan-customizations.py`
      (2,714 lines) rather than one of the `__main__.py` CLI-registration
      offenders originally proposed as the pilot: it has near 1:1 dedicated
      test coverage (`test_scan_customizations.py`, 2,684 lines) and no
      security/session-orchestration blast radius, unlike `agent-bridge`'s or
      `agent-worktrees`' `__main__.py` — both of which this very effort's own
      tooling runs on top of. Split into a 565-line shim (`Finding`/`Report` +
      `run()`/`main()`) plus `scan_plugin_sources.py` (440),
      `scan_session_context.py` (588), `scan_skills.py` (330),
      `scan_agents.py` (262), and `scan_text_files.py` (144). Preserved the
      hyphenated-filename `importlib.spec_from_file_location` loader contract
      the test file depends on by importing every `scan.<name>` the test
      touches into the shim, following the file's own pre-existing
      `instruction_projections` sys.path pattern. Pure structural split, no
      behavior change; `tools/run-plugin-tests.py customizing-copilot` passed
      (154 tests, same single pre-existing unrelated failure as before/after);
      `scan-customizations.py` dropped out of the baseline entirely (now
      under the 1,000-line cap). The actual CLI-registration `__main__.py`
      offenders remain queued in Phase 2, now informed by this proof.

### Phase 2 — top offenders
- [ ] Work down `tools/rank-module-size.py`'s ranked list, prioritizing
      CLI-registration shapes and small `--near-cap` margins, one PR per
      module (pure split, no behavior change, per the skill's Step 2).
      - [x] `agent-bridge/db.py` (2,437 lines, single `Database` class) —
            split via **mixin classes** (`db_core.py`, `db_schema.py`,
            `db_sessions.py`, `db_live_sessions.py`, `db_events.py`,
            `db_prompts.py`, `db_maintenance.py`), composed back into one
            `Database(...)` in a 45-line `db.py` shim. A new decomposition
            pattern beyond Phase 1's free-function split: safe for a single
            large class because `self.<method>()` resolves through the MRO
            regardless of which mixin file defines it, so no call-graph
            tracing was needed, only grouping by responsibility.
            `agent-dispatch/coordinator.py` was considered but deferred —
            its `create_app()` is a FastAPI factory with routes closing over
            local variables (`queue`, `bus`, `directory`, ...), a genuinely
            harder/riskier shape than either Phase 1 or this mixin split;
            it needs its own dedicated design pass, not a quick mechanical
            move. Bumped `agent-bridge` to `0.4.0-dev491`.
      - [x] `agent-dispatch/coordinator.py` (2,509, a demonstrated repeat
            drifter) — split the FastAPI app-factory along its real route
            seams using the same `register_*_routes(app, ...)` shape already
            proven by `coordinator_registries.py`: extracted
            `coordinator_status.py` (`/health`, `/events`),
            `coordinator_directory.py` (directory + satellites),
            `coordinator_tasks.py` (task CRUD/claim/governance/producer-scope
            routes), `coordinator_spawn.py` (spawn reservations + routing
            assignments), plus `coordinator_auth.py` and
            `coordinator_loops.py` for shared auth/background-loop state.
            Left `coordinator.py` as the composition root + lifespan owner
            (2,509 → 699 lines). `run-plugin-tests.py agent-dispatch`
            initially caught a real regression in the public internal test
            seam (`test_loop_governance` monkeypatching `_run_supervised_cycle`
            / `_GOVERNANCE_BACKOFF_SECONDS`) that the first extraction had not
            preserved; fixed by re-exporting those coordinator-owned symbols
            and adding thin wrappers so monkeypatching `agent_dispatch
            .coordinator` still reaches the moved loop code. Final validation:
            full `run-plugin-tests.py agent-dispatch` green across all 5
            sub-suites, targeted `-k` coverage green, ruff green, baseline
            refreshed to remove `coordinator.py`, and `agent-dispatch` bumped
            to `0.1.2-dev151`.
      - [ ] The `tools/clean-room/scenarios/*` fixtures (2,429 / 2,369) —
            lower urgency (not production code), but validating a split
            means actually running the Docker-based clean-room scenario
            (confirmed available on this machine), which is slow — budget
            real time for it rather than treating it as a quick win.
      - [ ] The `__main__.py` CLI-registration giants
            (`agent-worktrees` 26,303 after its first slice; `agent-bridge`
            6,595; `agent-dispatch` 4,691; `agent-codespaces` 4,675) — each
            needs its own dedicated slice with the full plugin test suite
            (not just guards) green before and after, given their live-
            orchestration blast radius. Do not rush these late in a long
            session; each deserves a fresh-context pass.
            - `agent-worktrees/__main__.py` first slice landed: extracted the
              non-destructive namespace/composition surfaces into
              `context_cli.py`, `services_cli.py`, `repos_cli.py`, and
              `related_cli.py` (context/introspection, services/worktree
              routing, repos/accounts, and related/state-root/knowledge
              dispatch). Remaining seams for follow-up slices: PR/finalize,
              install/update/register/uninstall, status/status-segment/
              status-context/status-updater/status-monitor, and the
              session/handoff/reap/reclaim/remux/restart lifecycle.
            - `agent-worktrees/__main__.py` second/third slices landed:
              `finalize_cli.py` + `pr_state_cli.py` now hold the PR/finalize
              family, and `status_cli.py` + `status_bar_cli.py` +
              `status_updater_cli.py` + `status_monitor_runtime.py` now hold
              the status family. Remaining seams: the
              session/handoff/reap/reclaim/remux/restart lifecycle, then the
              install/update/register/uninstall/profile/picker surface.
            - `agent-worktrees/__main__.py` fourth through seventh slices
              landed: the lifecycle/install/picker, update/runtime-reconcile,
              session-binding/inspection, and worktree-operations families now
              live in their own sibling modules. The remaining seam is no
              longer "whatever safe block is left" -- it is the launch core
              itself (`cmd_copilot`, especially `cmd_resolve`), and it now
              merits a dedicated design pass rather than another blind
              mechanical slice.
      - [ ] The vendored-copy canonical
            `libs/installation-context/installation_context.py` (9,169,
            +14 copies) — validate `sync-installation-context.py --check`
            and `check-vendored-libs-sync.py` as part of this one's
            re-validation, not just the owning plugin's tests.

### Phase 3 — cross-language cap
- [ ] Design what "module size" means for `.sh`/`.ps1`/`.ts` (line count vs.
      function count vs. both), then extend `tools/check-module-size.py` (or
      a sibling checker) to enforce it automatically.

### Phase 4 — test suite componentization
- [ ] Identify oversized test runner modules, split by behavioral contract,
      and backfill `@pytest.mark.contract` attribution as each is split
      (retrofitting onto already-small, single-contract files is not
      required).

## Validation Plan

- [x] Phase 0: `python tools/check-module-size.py`,
      `python tools/check-skills.py plugins/customizing-copilot/skills/componentizing-modules/SKILL.md`,
      `python tools/check-docs-consistency.py`,
      `python tools/check-runbook-references.py`, and
      `ruff check --select F,E9 tools/rank-module-size.py tools/pytest_portfolio_guard.py`
      all pass.
- [x] Phase 1 pilot: `python tools/run-plugin-tests.py customizing-copilot`
      passes (same single pre-existing unrelated failure before/after),
      `ruff check --select F,E9` on every touched/new file passes,
      `check-module-size.py` stays green with `scan-customizations.py`
      dropped from the baseline after `--refresh-baseline`, and a direct
      `python scan-customizations.py . --strict` smoke invocation succeeds.
- [ ] Each Phase 1/2 split: the touched plugin's
      `python tools/run-plugin-tests.py <plugin>` passes, `check-module-size.py`
      stays green, and `--refresh-baseline` is run (and its diff committed)
      whenever a baselined file shrinks below its prior ceiling.
- [ ] Phase 3: the extended guard is exercised against at least one
      intentionally oversized `.sh`/`.ps1`/`.ts` fixture before being wired
      into CI/pre-push.
- [ ] Phase 4: each split test module still passes its suite, and
      `@pytest.mark.contract` selection (`pytest -m 'contract("...")'`)
      returns the expected tests for at least one split family.

## Proposal

Phase 0's guideline/skill/tooling/marker shape (this document) is the
accepted proposal; it's already implemented on this effort's branch. Phase 1
onward has no open design question yet — each is a mechanical application of
the Phase 0 runbook, picked up as capacity allows.

## Journal

### 2026-09-16 — Kickoff + Phase 0
- Effort created from an operator request following review of
  `namankanakiya/copilot-extensions#2785` and the `engine.py`/#2788/#2794
  module-size regression it surfaced.
- Landed `CONTRIBUTING.md` guideline extension, the `componentizing-modules`
  skill, `tools/rank-module-size.py`, and the `@pytest.mark.contract` marker
  + `TESTING.md` documentation. All Phase 0 validation passing (see
  Validation Plan). Opened umbrella issue #2805.

### 2026-09-16 — Trunk unblock (second live incident, same failure mode)
- After merging Phase 0 (#2807), `main`'s CI showed `guards + lint` failing
  again — this time `worktree-manager/src/worktree_manager/__main__.py` had
  grown from 1426 to 1432 lines, over its own grandfathered ceiling, from an
  unrelated already-merged change. A second live occurrence of exactly the
  failure mode motivating this effort, independent of the `engine.py`/
  #2788/#2794 incident.
- Applied the manual, reviewed baseline widen `tools/check-module-size.py`
  itself directs for this case (1426 → 1432) to restore trunk-green, rather
  than block on a full split. Added to the Phase 2 backlog: this file is
  already a CLI-registration-shaped `__main__.py` and a good next split
  candidate precisely because it's now grown twice while ungoverned.

### 2026-09-16 — Phase 1 pilot split landed
- Split `scan-customizations.py` per the plan above (see Phase 1). Delegated
  the mechanical extraction (tracing ~80 cross-referenced names against the
  test file's `scan.<name>` surface) to a sub-agent with a tightly bounded
  spec; verified independently afterward (module sizes, ruff, the full
  plugin test suite, docs/skill guards, and a direct CLI smoke invocation)
  before committing. Bumped `customizing-copilot` to `0.1.0-dev71`.
- Landing this PR required a manual rebase: `main` had drifted twice more in
  the interim (`agent-bridge/__main__.py` 6575→6595,
  `agent-dispatch/coordinator.py` 2338→2509) — a fourth and fifth live
  occurrence of the exact regression class this effort exists to address,
  now observed in three different plugins' CLI/coordination files within a
  single working session. This is strong, repeated real-world evidence for
  the Phase 2 backlog priority, not just a one-off incident.

### 2026-09-16 — Checkpoint: pausing Phase 2 for a fresh-context continuation
- Three PRs merged this session: #2807 (Phase 0: guidelines/skill/tooling/
  marker), #2810 (trunk unblock, `worktree_manager/__main__.py`), #2813
  (Phase 1 pilot: `scan-customizations.py` split).
- While preparing *this* checkpoint PR, CI caught **two more** live drift
  instances on `main` itself: `agent-worktrees/config.py` (2122→2133) and
  `agent-worktrees/pr_contract.py` (1463→1470), from #2814 (unrelated,
  already merged). Sixth and seventh occurrences of the same regression
  class observed in a single session. Applied the same reviewed-widen to
  both, bundled into this checkpoint PR since it was already open and small.
- Deliberately stopping here rather than starting a Phase 2 slice late in an
  already-long session: the remaining backlog's easier, safer items
  (`coordinator.py`, `db.py`, the clean-room fixtures) still deserve a full
  test-coverage check + independent verification pass like Phase 1 got, and
  the remaining giants (`__main__.py` CLI-registration files, the vendored
  `installation-context` canonical) carry real live-orchestration blast
  radius that should never be rushed. Updated the pecking-order snapshot
  above and Phase 2's checklist with a concrete, reasoned next-pick and
  ordering rationale so a fresh session (or a handoff successor) can start
  immediately without re-deriving this analysis.
- **Next action for whoever picks this up:** read the "Suggested next pick"
  note above, check `agent-dispatch/coordinator.py` vs. `agent-bridge/db.py`
  test coverage ratios (same method as Phase 1: compare the module's line
  count to its dedicated test file's), then follow the
  `componentizing-modules` skill exactly as Phase 1 did — delegate the
  mechanical trace-and-extract to a sub-agent with a bounded spec if the
  file is large/cross-referenced, then independently re-verify before
  committing.

### 2026-09-16 — Phase 2 continued: `agent-bridge/db.py` mixin split
- Context utilization was still low (~34%) after the checkpoint above, so
  continued rather than waiting for a handoff. Compared `coordinator.py`
  (FastAPI app factory, routes closing over local `queue`/`bus`/`directory`
  state — genuinely harder to split safely) against `db.py` (one `Database`
  class, ~75 methods, all operating through `self` — safe to split via
  **mixin classes**, a new decomposition shape this effort hadn't used yet:
  `self.<method>()` resolves through the MRO regardless of which mixin file
  defines it, so no call-graph tracing is needed, only grouping by
  responsibility). Picked `db.py` as lower-risk.
- Delegated the extraction to a sub-agent (same pattern as Phase 1): split
  into `db_core.py`, `db_schema.py`, `db_sessions.py`, `db_live_sessions.py`
  (split out of the sessions group once it proved too large on its own),
  `db_events.py`, `db_prompts.py`, `db_maintenance.py`, composed into a
  45-line `db.py` shim. Independently verified: `check-module-size.py`,
  ruff, docs/skill guards, and `run-plugin-tests.py agent-bridge` (580
  passed, same 2 pre-existing unrelated failures in
  `test_bootstrap_check_reconcile_opt_in.py` before and after — confirmed
  unrelated to `db.py`). Bumped `agent-bridge` to `0.4.0-dev491`. Deferred
  `coordinator.py` to its own future slice given the FastAPI-closure
  complexity noted above.

### 2026-09-16 — Real regression caught by CI, not local validation: a lesson for the skill
- CI on the `db.py` PR failed `agent-bridge`'s full suite with 19 real
  failures (`TypeError: _EventsMixin._event_continuity() takes 2 positional
  arguments but 3 were given`) — the extraction had dropped `@staticmethod`
  from `_event_continuity` when moving it into `_EventsMixin`. My own local
  `run-plugin-tests.py agent-bridge` run had reported "580 passed" and
  looked identical to the pre-split baseline, but it never actually reached
  the failing tests: the runner groups tests into sub-suites and stops at
  the first failing one, and sub-suite 1 always contains the pre-existing,
  unrelated `test_bootstrap_check_reconcile_opt_in.py` failures — so
  sub-suites 2–6 (including `test_cursor_routes.py`/`test_delivery_cursor.py`)
  never ran locally at all. Fixed the decorator, verified with a `-k` filter
  targeting the affected files directly (bypasses sub-suite grouping: 88
  passed, 1 skipped), then let CI's full-matrix run confirm before merging.
- **Documented this as a new Step 2/Step 3 addition to the
  `componentizing-modules` skill**: grep the original file for
  `@staticmethod`/`@classmethod`/`@property`/`@cached_property` before
  starting and confirm each survives the move; and never trust a local
  `run-plugin-tests.py` run that stops at sub-suite 1 as full coverage when
  the plugin has any pre-existing failure — use a `-k` filter targeting the
  changed area, or treat CI's full-matrix run as the real gate. This applies
  to every future Phase 2/3/4 split, not just this one.

### 2026-09-17 — Trunk unblock (eighth+ninth live occurrence)
- Discovered via an unrelated PR's CI failure: `main` had drifted past the
  guard again, this time in two files at once —
  `plugins/agent-worktrees/src/agent_worktrees/picker_support/pivot_registry_scan.py`
  (1026, over the flat 1000-line cap) and
  `worktree-manager/src/worktree_manager/production_picker/picker_tui/pivots.py`
  (2411, over its 2159-line grandfathered ceiling, the file already flagged
  unclaimed in the pecking-order table above). An eighth and ninth live
  instance of the same regression class.
- Fixed both as a trunk-unblock split rather than a baseline widen, since
  `pivots.py` was already backlogged and `pivot_registry_scan.py` had no
  baseline entry to widen (newly over cap, not grandfathered). Split
  `pivot_registry_scan.py`'s classify/finding/remedy helpers into a new
  `pivot_registry_classify.py` sibling (1026 → 453 lines); split
  `pivots.py` along the same manifest/materialization/scan seams
  `agent-worktrees` already established for the equivalent logic, into
  `pivot_manifest.py` + `pivot_registry_materialization.py` +
  `pivot_registry_scan.py` (2411 → 102-line facade).
- Caught a real regression the split's own (mis-targeted) validation
  missed: `run-plugin-tests.py worktree-manager` silently reports "no
  registered suite" (worktree-manager is out-of-plugin and isn't wired into
  that runner at all — see its dedicated CI job), so the actual
  `worktree-manager` test suite never ran until invoked directly
  (`cd worktree-manager && test-supervisor -- uv run --extra dev pytest -q`).
  That run caught 8 failures: several tests monkeypatched private symbols
  directly on the old `pivots` module object; once the split moved those
  symbols' real definitions elsewhere, the old patch target silently
  stopped intercepting the real call site. Fixed by retargeting each test's
  monkeypatch to the symbol's new module home (not by re-adding a
  re-export shim). Full `worktree-manager` suite: 916 passed after the fix.
  Added to the running lesson list: **`run-plugin-tests.py` not covering a
  target is itself a silent gap** — confirm the actual CI-equivalent
  command for any out-of-plugin component before trusting a runner's report
  that "nothing to test" means nothing broke.

### 2026-09-20 — Phase 2 continued: `agent-dispatch/coordinator.py` route split
- Picked up the harder FastAPI app-factory shape deferred in the `db.py`
  entry above, but followed the exact registrar pattern already proven in
  this file by `coordinator_registries.py` rather than inventing a new
  abstraction. Split the route families into `coordinator_status.py`
  (`/health`, `/events`), `coordinator_directory.py` (directory +
  satellites), `coordinator_tasks.py` (producer-scope, task CRUD/claim,
  drain/recover, lifecycle/steer), and `coordinator_spawn.py`
  (spawn reservations + routing assignments); pulled the shared bearer auth
  into `coordinator_auth.py` and the background-loop/cutover helpers into
  `coordinator_loops.py`; left `coordinator.py` as the composition root and
  lifespan owner. Net result: `coordinator.py` dropped from 2,509 lines to
  699 and fell out of the baseline entirely.
- Validation matched the skill's stricter post-`db.py` rules. The first full
  `python tools/run-plugin-tests.py agent-dispatch` pass reached sub-suite 3
  and caught a real regression: `test_loop_governance.py` monkeypatches
  coordinator-owned internals (`_run_supervised_cycle`,
  `_GOVERNANCE_BACKOFF_SECONDS`) directly, and the first extraction had only
  re-exported the moved loop functions, not preserved that monkeypatch seam.
  Fixed by re-exporting the expected symbols and wrapping the moved loop
  entrypoints so a patch applied to `agent_dispatch.coordinator` still
  threads through to `coordinator_loops.py`. Re-validated with a targeted
  `-k "loop_governance or coordinator or spawn_reservation or satellites or
  producer_fences or registrations or schedule_registry or routing_provenance
  or federation"` run (509 passed, 2579 deselected) and a final full
  `run-plugin-tests.py agent-dispatch` pass across all 5 sub-suites
  (716 passed/5 skipped; 411 passed/6 skipped; 626 passed/14 skipped; 601
  passed/1 skipped; 704 passed/4 skipped). Ruff (`--select F,E9`) passed on
  every touched/new file, `check-module-size.py --refresh-baseline` removed
  `coordinator.py` from `tools/module-size-baseline.json`, and the required
  full-tree `check-module-size.py` run additionally surfaced a separate live
  drift in `worktree-manager/.../picker_tui/engine.py`; applied a manual,
  reviewed widen there (9191 → 9267) so the guard is green again while that
  file stays in the backlog. Bumped `agent-dispatch` to `0.1.2-dev151`.

### 2026-09-20 — Phase 2 continued: first `agent-worktrees/__main__.py` namespace slice
- Took the first deliberate bite out of the biggest offender in the whole
  repo: rather than touching the highest-blast-radius live session/status/
  handoff engine on the first pass, peeled off the **non-destructive CLI
  namespace surface** and left `__main__.py` as a composition root. Extracted
  `context_cli.py` (deploy-instructions, machine-context, get, install-status,
  installer-readiness, state-root / coordination-readiness / config-root /
  knowledge), `services_cli.py` (services + worktree namespace dispatch),
  `repos_cli.py` (repos/accounts dispatch + registration-account clarify
  helper), and `related_cli.py` (related-repo dispatch and its graft/doctor/
  resolve helpers). Rewired `build_parser()` to delegate those parser stubs to
  the new modules and re-exported the moved helper/handler symbols back onto
  `agent_worktrees.__main__` so existing monkeypatch seams and direct imports
  kept working. Net result: `__main__.py` dropped from 29,173 lines to 26,191
  (still far over cap, but a real first dent).
- Validation followed the componentization runbook's stricter rules. Ruff
  (`--select F,E9`) passed on `__main__.py` plus all four new modules. The
  first full `python tools/run-plugin-tests.py agent-worktrees` pass caught
  real compatibility regressions in the preserved seams (`_WORKTREE_VERBS`,
  `cmd_get`'s lease-origin helper, `related` anchor monkeypatching, and the
  interactive `_clarify_registration_account` credential-pin flow); fixed
  each by routing through the re-exported `__main__` surfaces or restoring the
  original behavior exactly. The next full pass reached sub-suite 2 and then
  failed in `tests/test_doctor.py`; verified those 10 failures are **pre-
  existing on untouched HEAD** by reproducing the same `missing_repo_entry` /
  doctor expectations in a separate clean worktree at the same commit, so the
  moved slice was not the cause. Because `run-plugin-tests.py` stops at the
  first failing sub-suite, ran a broad targeted follow-up over every touched
  area (`cli_routing`, `context_resolution`, `machine_context`, `state_root`,
  `knowledge_plugins`, `related`, `config_graft_e2e`, `repos_gh`,
  `claims_cmd`, `follow_ups_cmd`): **542 passed, 2 skipped, 4436 deselected**.
  `check-module-size.py --refresh-baseline` lowered the grandfathered ceiling
  for `agent-worktrees/__main__.py`, but rebasing onto newer `main` pulled in
  a further 110 lines of unrelated upstream growth while this PR was in flight,
  and the final post-review compatibility alias restoration added 2 more lines,
  so the baseline then needed explicit reviewed widens to the final merged-file
  size (26,191 → 26,301 → 26,303) to keep the full-tree guard honest. The
  required `check-module-size.py`, `check-install-contract.py`, and
  `check-version-consistency.py` all pass afterward. A rebase onto newer
  `main` also revealed that `agent-worktrees` `1.5.5-dev200` had already
  landed elsewhere, so this slice took the next patch `-devN` bump instead:
  `agent-worktrees` `1.5.5-dev201` and `.github/plugin/marketplace.json`
  `metadata.version` `1.7.7-dev173`.

### 2026-09-20 — Phase 2 continued: `agent-worktrees/__main__.py` PR/finalize slice
- Kept the second bite deliberately cohesive: rather than mixing the status and
  session-lifecycle surface into the same PR, peeled off the PR/finalize family
  into two sibling modules while leaving `__main__.py` as the composition root.
  Extracted `finalize_cli.py` (`post-exit`, `finalize`, `push-changes`,
  `create-pr`, `attribution-audit`, `mark-complete`) and `pr_state_cli.py`
  (`set-pr`, `pr-ready`, `pr-status`, `pr-complete`). `build_parser()` now
  delegates those parser stubs to the new modules, and `agent_worktrees.__main__`
  re-exports the moved handler/helper names so existing monkeypatch seams and
  direct imports keep working.
- Net result: `plugins/agent-worktrees/src/agent_worktrees/__main__.py`
  dropped from 26,303 lines to 25,306. That is still wildly over the cap, but
  it takes another real chunk out of the CLI engine without mixing in behavior
  changes. The remaining `__main__.py` backlog is now more sharply defined:
  the status family, then the session/handoff/reap lifecycle, then the
  install/update/register surface.
- Validation stayed at the stricter componentization bar. Ruff (`--select
  F,E9`) passed on `__main__.py`, `finalize_cli.py`, and `pr_state_cli.py`.
  The full `python tools/run-plugin-tests.py agent-worktrees` pass again hit
  the same pre-existing `tests/test_doctor.py` failures in sub-suite 2
  (**550 passed, 10 failed, 1 skipped**) rather than anything in the moved
  surface, so followed the runbook and ran a targeted sweep over the extracted
  area instead: **201 passed, 1 skipped, 4785 deselected**. `python
  tools/check-module-size.py --refresh-baseline` lowered
  `tools/module-size-baseline.json`'s ceiling for `agent_worktrees/__main__.py`
  from 26,303 to 25,306. The required install/version guards still pass after
  the slice. Follow-up review fixes advanced the same PR's version once more to
  stay ahead of newer `main`, so the final bump for this pass is
  `agent-worktrees` `1.5.5-dev203` and marketplace `metadata.version`
  `1.7.7-dev175`.

### 2026-09-21 — Phase 2 continued: `agent-worktrees/__main__.py` status-family slice
- Took the next cohesive seam named in the prior entry instead of mixing it
  with the session lifecycle: extracted the whole status family out of
  `plugins/agent-worktrees/src/agent_worktrees/__main__.py` while preserving
  `__main__` as the composition root and monkeypatch surface. The split is
  intentionally finer-grained than a single giant `status_cli.py` blob so the
  new modules themselves stay under the 1,000-line guard: `status_cli.py`
  holds the fleet/per-worktree `status` read surface, `status_bar_cli.py`
  holds `status-segment` / `status-context` plus their render helpers,
  `status_updater_cli.py` holds the per-session updater and project/runtime
  helpers, and `status_monitor_runtime.py` holds the resident monitor runtime
  helpers (`reconcile-sessions`, restart/claim/registry/mux helpers). Rewired
  `build_parser()` to delegate parser stubs to those modules and re-exported
  the moved names back onto `agent_worktrees.__main__` so existing direct
  imports and monkeypatch seams still land on the right call sites.
- Net result: `plugins/agent-worktrees/src/agent_worktrees/__main__.py`
  dropped from 25,269 lines at rebase-complete `HEAD` to 23,292 (a 1,977-line reduction this
  slice). The four new modules land at 218 / 757 / 562 / 799 lines
  respectively, all safely under the cap. The remaining `__main__.py` backlog
  is now more sharply constrained to the session/handoff/reap/reclaim/remux/
  restart lifecycle and then the install/update/register/uninstall/profile/
  picker surface.
- Validation stayed at the stricter componentization bar. Ruff (`--select
  F,E9`) passed on `__main__.py` and all four new modules. The first full
  `python tools/run-plugin-tests.py agent-worktrees` pass surfaced only two
  real seam regressions introduced by the extraction itself: (1) a missing
  re-export for `_monitor_pending_handoff_request`, and (2) preserved
  monkeypatch seams that needed the moved helpers to defer back through
  `agent_worktrees.__main__` rather than calling only their local copies.
  Fixed those, then re-validated with a broad targeted sweep over the moved
  surfaces and their compatibility seams (**652 passed, 4 skipped, 4339
  deselected**). The final full plugin run again reproduced the same
  independently-confirmed pre-existing `tests/test_doctor.py` failures
  (**550 passed, 10 failed, 1 skipped**) that already existed on untouched
  `HEAD`, so the extraction itself is green apart from that known unrelated
  suite issue. Also tightened the shared test fixture in
  `plugins/agent-worktrees/tests/conftest.py` to reset active-project state
  via the original setter captured before per-test monkeypatching, which
  preserves the prior isolation guarantee while keeping teardown stable under
  the newly-preserved monkeypatch seams.
- Followed through on the remaining contract checks after the code stabilized:
  `python tools/check-module-size.py --refresh-baseline`, the plain
  `python tools/check-module-size.py` guard, `python tools/check-install-contract.py`,
  and `python tools/check-version-consistency.py` all pass afterward. Rebasing
  onto newer `main` during publication pulled in 26 more unrelated upstream
  lines in `agent-worktrees/__main__.py`, so the final validation branch
  needed the same explicit reviewed baseline widen prior slices already used
  for this scenario (23,249 → 23,275). Addressing the substantive Copilot
  review finding then added a further 17 lines to `__main__.py`, so the final
  publishable branch needed one last matching reviewed widen (23,275 →
  23,292) to keep the shrink-only guard honest.
  Rebasing also advanced `main` to `agent-worktrees` `1.5.5-dev204`, so the
  final publishable branch needed the next patch `-devN` bump on top of the
  slice itself. This slice therefore lands as `agent-worktrees`
  `1.5.5-dev205` and marketplace `metadata.version` `1.7.7-dev177`.

### 2026-09-21 — Phase 2 continued: `agent-worktrees/__main__.py` lifecycle + install/picker slice
- Took the next two cohesive seams named by the prior entry instead of trying
  to force the remaining CLI engine into one PR. First, extracted the whole
  destructive lifecycle family out of
  `plugins/agent-worktrees/src/agent_worktrees/__main__.py` while preserving
  `__main__` as the composition root and monkeypatch surface:
  `handoff_cli.py` now owns `handoff-cutover`, `handoffs-check`, and
  `embody`; `reap_cli.py` owns `reap-shells`, `reap-sessions`, and the
  finished/managed sweep helpers; `reclaim_cli.py` owns `reclaim`, `remux`,
  and `restart`; and `cleanup_gc_cli.py` owns `cleanup`, `gc`, and the
  revalidation helpers. Second, with budget still available, peeled off the
  safe operator/install surface into `picker_profiles_cli.py`
  (`profiles`, `terminal-fragment`, `repair`, `picker`, `validate`) and
  `installation_cli.py` (`install`, `register`, `uninstall`, plus the managed
  instruction / registry helpers they depend on). `build_parser()` now
  delegates those parser stubs to the new modules, and `agent_worktrees.__main__`
  re-exports the moved helpers/handlers/constants back onto the old surface so
  existing direct imports and monkeypatch seams keep landing exactly where the
  tests expect.
- Net result: `plugins/agent-worktrees/src/agent_worktrees/__main__.py`
  dropped from 23,292 lines at the start of this slice to 18,939 (a 4,353-line
  reduction this pass; 29,173 → 18,939 across the four-slice campaign so far).
  The six new modules land at 596 / 845 / 536 / 695 / 627 / 821 lines
  respectively, all safely under the 1,000-line guard. That leaves the
  remaining `__main__.py` backlog much more sharply defined: the update /
  runtime-reconcile path, then the remaining launch/session-control core
  (`copilot`, `resolve`, hook/session registration and recovery, plus router
  glue and residual shared helpers).
- Validation stayed at the same stricter componentization bar. Ruff
  (`--select F,E9`) passed on `__main__.py` and all six new modules. The first
  targeted test sweep surfaced only seam/compatibility regressions introduced by
  the extraction itself: missing re-exports for `_INSTRUCTION_MARKER`,
  `_NO_AUTO_CLEAN_ENV`, `_AUTO_CLEAN_GRACE_ENV`, `discover_plugin_dir`, and
  `_repo_for_record`; a monkeypatch seam that needed `cmd_embody()` to defer
  codename resolution back through `agent_worktrees.__main__`; and the Windows
  Terminal refresh helper needing to honor monkeypatched
  `_resolve_terminal_install_script` / `discover_plugin_dir` from the legacy
  surface instead of only its local copy. Fixed those, then re-ran the broad
  extracted-area sweep covering the lifecycle/install/picker surface and its
  preserved seams (**753 passed, 1 skipped, 4294 deselected**). The final full
  plugin run again reproduced only the same independently-confirmed
  pre-existing `tests/test_doctor.py` failures (**551 passed, 10 failed,
  1 skipped**) that already existed on untouched `HEAD`, so the extraction
  itself is green apart from that known unrelated suite issue.
- Followed through on the remaining contract checks after the code stabilized:
  `python tools/check-module-size.py --refresh-baseline` lowered
  `tools/module-size-baseline.json`'s ceiling for `agent_worktrees/__main__.py`
  to the new size, and the plain `python tools/check-module-size.py` guard
  stayed green with all six new modules below cap. This slice also needs the
  usual plugin version bump: `agent-worktrees` `1.5.5-dev206` and marketplace
  `metadata.version` `1.7.7-dev178`.

### 2026-09-21 — Phase 2 continued: `agent-worktrees/__main__.py` update/runtime-reconcile slice
- Took the next explicit priority named by the prior entry and kept the move
  purely structural: extracted the **update / runtime-reconcile /
  pre-launch-planning** surface out of
  `plugins/agent-worktrees/src/agent_worktrees/__main__.py` while preserving
  `__main__` as the composition root and monkeypatch surface. The split needed
  one extra subdivision to stay honest to this effort's own guard: the
  user-facing command surface and pre-launch/update planners now live in
  `update_cli.py` (`update`, `pre-launch`, `reconcile-plugins`,
  `uninstall-plugins`, plus their parser registration and compatibility
  wrappers), while the payload/runtime inventory and reconcile helpers live in
  `update_runtime.py` (`_registered_plugin_targets`,
  `_update_registered_plugins`, `_reconcile_registered_runtimes`,
  `_update_modules`, anchor self-heal/sync helpers, and payload/runtime
  installer discovery). `build_parser()` now delegates those parser stubs to
  `update_cli.py`, and `agent_worktrees.__main__` re-exports the moved helper
  names, constants, and compatibility module attributes (`socket`, `svc`) back
  onto the legacy surface so existing direct imports and monkeypatch seams keep
  landing exactly where the test suite expects.
- Net result: `plugins/agent-worktrees/src/agent_worktrees/__main__.py`
  dropped from 18,939 lines at the start of this slice to **17,175**
  (a 1,764-line reduction this pass; 29,173 → 17,175 across the five-slice
  campaign so far). The new modules land at **898** lines
  (`update_cli.py`) and **754** lines (`update_runtime.py`), both under the
  1,000-line cap, and `python tools/check-module-size.py --refresh-baseline`
  lowered `tools/module-size-baseline.json`'s grandfathered ceiling for
  `__main__.py` to match the new post-slice size. The remaining
  `__main__.py` backlog is now much sharper: the live launch/session-control
  core (`copilot`, `resolve`, session register/deregister/lifecycle/binding/
  recovery/lineage, `note-handoff`, `bind-nudge`) plus the worktree-operations
  block and residual router/composition glue.
- Validation stayed at the same stricter componentization bar. Ruff
  (`--select F,E9`) passed on `__main__.py`, `update_cli.py`, and
  `update_runtime.py`. The first full
  `python tools/run-plugin-tests.py agent-worktrees` pass surfaced only
  extraction-seam regressions introduced by the move itself: dropped
  re-exports for `_resolve_repo_remote` / `_pr_flow_profile`, preserved
  monkeypatch seams for `socket` / `svc`, the pre-launch planner still
  consulting the old installed-plugin locator instead of the runtime
  resolution helper the tests patch, and two payload-update edge cases around
  retired-plugin purge context and update-vs-install error reporting. Fixed
  each, then re-ran a broad targeted sweep over every touched area and its
  preserved seams (**243 passed, 4805 deselected**). The final full plugin
  run again reproduced only the same independently-confirmed pre-existing
  `tests/test_doctor.py` failures (**551 passed, 10 failed, 1 skipped**) that
  already existed on untouched `HEAD`, so the extraction itself is green apart
  from that known unrelated suite issue. The other required guards also pass
  after the slice: plain `python tools/check-module-size.py`,
  `python tools/check-install-contract.py`, and
  `python tools/check-version-consistency.py`.
- Deliberately stopped short of the remaining launch/session-control core even
  though it is now the top remaining seam. After tracing it, the risk call is
  unchanged from the operator's warning: that surface still interleaves live
  mux/session recovery, handoff lineage, status-updater re-seeding, session
  projection/context emission, and explicit/implicit binding fallbacks in the
  exact code path this fleet's sessions use to launch and bind themselves.
  That is a valid next slice, but it wants a fresh-context pass with room to
  stop if the shared mutable state still resists a clean pure move. This slice
  therefore takes the clean update/runtime win and leaves the live session core
  explicitly queued rather than forcing it. Version bump for this pass:
  `agent-worktrees` `1.5.5-dev207` and marketplace `metadata.version`
  `1.7.7-dev179`.

### 2026-09-21 — Review follow-up: stale marketplace purge guard
- GitHub Copilot posted one real post-open review finding on PR #3156 after
  the bounded wait window: `_update_registered_plugins()` was adding a context
  to the browse/purge set even when `copilot plugin marketplace update` had
  failed, which could let a stale marketplace browse classify an inactive
  installed plugin as retired and uninstall it. Fixed the follow-up in
  `update_runtime.py` by treating marketplace refresh success as the gate for
  purge-eligible browse contexts (including the `None`/global-catalog path),
  while leaving the payload refresh itself best-effort.
- Added a targeted regression test in
  `tests/test_update_registered_plugins.py` proving that a failed marketplace
  refresh neither runs the retired-plugin uninstall path nor even attempts the
  browse-based purge classification; the payload refresh still proceeds
  opportunistically as before. Re-ran the same extracted-area targeted suite
  (**244 passed, 4805 deselected**) and the full plugin suite again
  reproduced only the same independently-confirmed pre-existing
  `tests/test_doctor.py` failures (**551 passed, 10 failed, 1 skipped**).
- Version bump for the follow-up PR: `agent-worktrees` `1.5.5-dev208` and
  marketplace `metadata.version` `1.7.7-dev180`.

### 2026-09-21 — Phase 2 continued: `agent-worktrees/__main__.py` session-binding slice
- Re-ran the explicit risk assessment on the remaining launch/session-control
  area before editing. The verdict split in two: the **session binding /
  inspection hook surface** turned out to be safely movable because its shared
  state is already explicit (`args`, session ids, worktree ids, tracking YAML,
  session-state paths) and the moved handlers can reach the existing helpers
  through the same thin `__main__` compatibility surface prior slices use.
  But `cmd_copilot`/`cmd_resolve` did **not** become a clean seam under the
  same inspection. `resolve` still spans roughly 2.7k lines of nested picker,
  remote-machine, restore/remux, create-or-resume, and launch-plan code in one
  function-shaped blob; forcing that move in the same slice would still be a
  risky edit to the live launch path rather than a clean mechanical extract.
- Landed the safe half as a pure structural split. The session hook/binding
  family now lives in `session_binding_cli.py` (`register-session`,
  `deregister-session`, `bind-session`, `bind-nudge`, `note-handoff`, plus the
  shared binding/nudge/title helpers) and `session_inspection_cli.py`
  (`session-lifecycle`, `session-binding`, `session-recovery`,
  `session-lineage`). `build_parser()` now delegates those parser
  registrations, and `agent_worktrees.__main__` re-exports the moved command
  functions plus the compatibility helpers/tests still reach directly
  (`_activate_session_binding`, `_bind_nudge_should_fire`,
  `_bind_nudge_decision`, `_capture_session_title`), preserving the old
  monkeypatch/import surface while leaving `__main__` as the composition root.
- Net result: `plugins/agent-worktrees/src/agent_worktrees/__main__.py`
  dropped from **17,175** lines at the start of this slice to **16,108**
  after rebasing onto upstream's bound-agent follow-up
  (#3157; that PR added 13 lines to the same file while this slice was in
  flight), so the structural split still nets a **1,067-line** reduction for
  this pass (**29,173 → 16,108** across the
  six-slice campaign so far). The new modules land at **946** lines
  (`session_binding_cli.py`) and **162** lines (`session_inspection_cli.py`),
  both under the 1,000-line cap, and
  `python tools/check-module-size.py --refresh-baseline` lowered
  `tools/module-size-baseline.json`'s ceiling for `__main__.py` accordingly.
- Validation matched the effort's stricter bar. `ruff check --select F,E9`
  passed on `__main__.py`, `session_binding_cli.py`, and
  `session_inspection_cli.py`. The first full
  `python tools/run-plugin-tests.py agent-worktrees` pass surfaced five real
  extraction regressions, all on preserved private test seams
  (`_capture_session_title` and `_bind_nudge_should_fire` no longer exposed on
  `agent_worktrees.__main__`); re-exported them and re-ran. The targeted
  extracted-area sweep then passed (**339 passed, 4710 deselected**), and the
  full plugin suite again reproduced only the same independently-confirmed
  pre-existing `tests/test_doctor.py` failures (**551 passed, 10 failed,
  1 skipped**) that already existed on untouched `HEAD`. The other required
  guards also pass after the slice: plain `python tools/check-module-size.py`,
  `python tools/check-install-contract.py`, and
  `python tools/check-version-consistency.py`.
- Remaining backlog for the next slice is now more explicit. The still-deferred
  high-risk seam is the **launch core**, not the already-extracted session
  binding hooks: `cmd_copilot`, and especially `cmd_resolve`'s nested planner
  and picker flow. If that still feels too entangled under fresh inspection,
  take the lower-risk worktree-operations block next (`list`, `claims`,
  `follow-ups`, `create`, `run`, `sync`) rather than forcing the launch path.
  Version bump for this slice after rebasing over #3157: `agent-worktrees`
  `1.5.5-dev211` and
  marketplace `metadata.version` `1.7.7-dev181`.

### 2026-09-21 — Phase 2 continued: `agent-worktrees/__main__.py` worktree-operations slice
- Took the exact lower-risk seam the prior entry queued instead of re-litigating
  `cmd_copilot`/`cmd_resolve` for a third time. Extracted the worktree-operations
  block out of `plugins/agent-worktrees/src/agent_worktrees/__main__.py` while
  preserving `__main__` as the composition root and monkeypatch surface:
  `list_cli.py` now owns `list` plus its cache/stream/read helpers,
  `claims_cli.py` owns the claim-ledger/readiness surface, `follow_ups_cli.py`
  owns the itemized follow-up ledger, and `worktree_ops_cli.py` owns
  `create` / `run` / `remove-system` / `sync` plus their shared owner-claim
  helpers and picker-local `sync_one` / `finalize_one` adapters. `build_parser()`
  now delegates those parser stubs, and `agent_worktrees.__main__` re-exports
  the moved handlers/helpers/constants back onto the legacy surface so existing
  direct imports and monkeypatch seams keep landing exactly where the tests
  expect.
- Net result: `plugins/agent-worktrees/src/agent_worktrees/__main__.py`
  dropped from **16,108** lines at the start of this slice to **13,669**
  after the final baseline refresh (**29,173 → 13,669** across the
  seven-slice campaign so far). The new modules land at **508** lines
  (`list_cli.py`), **835** (`claims_cli.py`), **271** (`follow_ups_cli.py`),
  and **775** (`worktree_ops_cli.py`), all under the 1,000-line cap.
- Validation stayed at the same stricter componentization bar. `ruff check
  --select F,E9` passed on `__main__.py` and all four new modules. The full
  `python tools/run-plugin-tests.py agent-worktrees` run again stopped in the
  same pre-existing `tests/test_doctor.py` failures (**551 passed, 10 failed,
  1 skipped**) already independently confirmed on untouched `HEAD`, so per the
  runbook I followed with a broad extracted-area sweep covering the moved list /
  claims / follow-ups / create / run / sync surfaces and their preserved seams
  (**374 passed, 4678 deselected**). The other required guards all passed after
  the slice: `python tools/check-module-size.py --refresh-baseline`, plain
  `python tools/check-module-size.py`, `python tools/check-install-contract.py`,
  and `python tools/check-version-consistency.py`. Read-only dogfooding also
  passed for `list --json`, `claims --json`, `follow-ups --json`, and
  `create` / `run` / `sync --help`.
- Remaining backlog is now even sharper than the prior entry: the extracted
  "safer alternative" is gone. The still-deferred large seam is the live
  launch/session-control core itself -- `cmd_copilot`, and especially
  `cmd_resolve`'s intertwined picker/remux/handoff/create-or-resume/launch-plan
  flow. Three fresh assessments have now reached the same conclusion: it wants
  a dedicated design pass (likely splitting its picker, restore/remux, and
  launch-planning sub-flows behind an explicit shared state object), not
  another blind slice. Version bump for this slice:
  `agent-worktrees` `1.5.5-dev212` and marketplace `metadata.version`
  `1.7.7-dev182`.
