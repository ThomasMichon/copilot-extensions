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

### Current pecking order (snapshot, 2026-09-16)

`python tools/rank-module-size.py --limit 15` (vendored duplicates folded in
— re-run before starting a phase, this list moves):

| Lines | Over cap | File | Notes |
|------:|---------:|------|-------|
| 28,384 | +27,384 | `plugins/agent-worktrees/src/agent_worktrees/__main__.py` | Worst offender by nearly 3x; a CLI registration surface — prime candidate for the `producers_cli.py`-style split |
| 9,169 | +8,169 | `libs/installation-context/installation_context.py` (+14 vendored copies) | Split the **canonical** copy only; `sync-installation-context.py` propagates |
| 8,695 | +7,695 | `worktree-manager/.../picker_tui/engine.py` | The file that motivated this effort (#2788/#2794 regression) |
| 6,721 | +5,721 | `plugins/agent-bridge/src/agent_bridge/session_manager.py` | |
| 6,575 | +5,575 | `plugins/agent-bridge/src/agent_bridge/__main__.py` | Second CLI registration surface |
| 5,201 | +4,201 | `plugins/agent-worktrees/src/agent_worktrees/tracking.py` | |
| 5,161 | +4,161 | `plugins/agent-index/scripts/cell-runtime.py` | |
| 4,691 | +3,691 | `plugins/agent-dispatch/src/agent_dispatch/__main__.py` | Already partially split (`producers_cli.py` et al. extracted) — a model for how far a `__main__.py` split can still go |
| 4,636 | +3,636 | `plugins/agent-codespaces/src/agent_codespaces/__main__.py` | CLI registration surface |
| 3,979 | +2,979 | `plugins/agent-dispatch/src/agent_dispatch/queue.py` | The original motivating case for the cap itself |
| 3,421 | +2,421 | `plugins/agent-dispatch/src/agent_dispatch/supervisor.py` | |
| 2,917 | +1,917 | `plugins/agent-bridge/src/agent_bridge/agent_registry.py` | |
| 2,714 | +1,714 | `plugins/customizing-copilot/skills/reviewing-customizations/scripts/scan-customizations.py` | |
| 2,608 | +1,608 | `plugins/agent-worktrees/src/agent_worktrees/sessions.py` | |
| 2,554 | +1,554 | `plugins/agent-codespaces/src/agent_codespaces/config.py` | |

Full list: `python tools/rank-module-size.py --limit 70`. Files within a small
margin of their own ceiling (most likely to tip over next from unrelated
feature work, per the `engine.py` incident above):
`python tools/rank-module-size.py --near-cap 25`.

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
