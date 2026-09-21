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

### Current pecking order (snapshot, 2026-09-20, post-`agent-worktrees __main__.py` namespace split)

`python tools/rank-module-size.py --limit 20` (vendored duplicates folded in
— re-run before starting a phase, this list moves; it already has once per
PR merged during this effort — treat it as a live command, not a frozen
table):

| Lines | Over cap | File | Notes |
|------:|---------:|------|-------|
| 26,303 | +25,303 | `plugins/agent-worktrees/src/agent_worktrees/__main__.py` | First dedicated slice landed: context/services/repos/related dispatch extracted into sibling `*_cli.py` modules; still the worst offender, with the live session/status/PR/install/reap surfaces left for later dedicated slices |
| 9,267 | +8,267 | `worktree-manager/.../picker_tui/engine.py` | The file that motivated this effort (#2788/#2794 regression); it drifted again while this slice was in flight, so the baseline was manually widened (9191 → 9267) to restore a green full-tree guard pending its own future split |
| 9,169 | +8,169 | `libs/installation-context/installation_context.py` (+17 vendored copies) | Split the **canonical** copy only; `sync-installation-context.py` propagates to every vendored copy |
| 6,873 | +5,873 | `plugins/agent-bridge/src/agent_bridge/__main__.py` | The live-orchestration CLI-registration giant; still a dedicated-slice item, not a quick opportunistic split |
| 6,817 | +5,817 | `plugins/agent-bridge/src/agent_bridge/session_manager.py` | |
| 5,855 | +4,855 | `plugins/agent-worktrees/src/agent_worktrees/tracking.py` | |
| 5,161 | +4,161 | `plugins/agent-index/scripts/cell-runtime.py` | |
| 4,970 | +3,970 | `plugins/agent-dispatch/src/agent_dispatch/__main__.py` | Already partially split (`producers_cli.py` et al. extracted) — still a strong model for how far a CLI-registration split can continue |
| 4,675 | +3,675 | `plugins/agent-codespaces/src/agent_codespaces/__main__.py` | CLI registration surface |
| 4,508 | +3,508 | `plugins/agent-dispatch/src/agent_dispatch/queue.py` | The original motivating case for the cap itself |
| 3,512 | +2,512 | `plugins/agent-dispatch/src/agent_dispatch/supervisor.py` | |
| 2,940 | +1,940 | `plugins/agent-bridge/src/agent_bridge/agent_registry.py` | |
| 2,669 | +1,669 | `plugins/agent-worktrees/src/agent_worktrees/sessions.py` | |
| 2,583 | +1,583 | `plugins/agent-codespaces/src/agent_codespaces/config.py` | |
| 2,429 | +1,429 | `tools/clean-room/scenarios/agent-index-installation-cells/scenario.py` | Clean-room fixture, not production code — lower urgency |
| 2,369 | +1,369 | `tools/clean-room/scenarios/progressive-context-disclosure-baseline/fixture.py` | Clean-room fixture, not production code — lower urgency |
| 2,307 | +1,307 | `plugins/agent-worktrees/src/agent_worktrees/pr_ops.py` | |
| 2,195 | +1,195 | `plugins/agent-worktrees/src/agent_worktrees/reconcile.py` | |
| 2,124 | +1,124 | `plugins/agent-worktrees/src/agent_worktrees/session_projection.py` | |
| 2,107 | +1,107 | `plugins/agent-worktrees/src/agent_worktrees/config.py` | |

**Suggested next pick (Phase 2, next slice):** continue the dedicated
`plugins/agent-worktrees/src/agent_worktrees/__main__.py` campaign while the
cohesive seams are fresh: the remaining live lifecycle/status/session/handoff
surfaces still dominate the table and should keep landing as isolated, fully
validated slices. If that blast radius is too high for the moment, fall back to
the clean-room fixtures
(`tools/clean-room/scenarios/agent-index-installation-cells/scenario.py` /
`.../progressive-context-disclosure-baseline/fixture.py`) or the still-large
production module `plugins/agent-worktrees/src/agent_worktrees/pr_ops.py`.

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
