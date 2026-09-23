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

### Current pecking order (snapshot, 2026-09-21, post-`agent-bridge/session_manager.py` split)

`python tools/rank-module-size.py --limit 20` (vendored duplicates folded in
— re-run before starting a phase, this list moves; it already has multiple
passes during this effort — treat it as a live command, not a frozen table):

| Lines | Over cap | File | Notes |
|------:|---------:|------|-------|
| 8,162 | +7,162 | `plugins/agent-worktrees/src/agent_worktrees/__main__.py` | Tenth dedicated slice landed: `status-monitor` now lives in `status_monitor_cli.py`, the resident runtime stays in `status_monitor_runtime.py`, and the entire no-project / bare-launch / Worktree Manager front door now lives in `front_door_cli.py`. Live `cmd_*` inventory is down to `cmd_launch`, `cmd_execution_leg`, excluded `cmd_copilot`, thin-wrapper `cmd_resolve`, and thin-wrapper `cmd_handoff_trace` — i.e. the remaining command-handler seams are effectively exhausted. |
| 5,145 | +4,145 | `plugins/agent-index/scripts/cell-runtime.py` | |
| 5,032 | +4,032 | `plugins/agent-dispatch/src/agent_dispatch/__main__.py` | Already partially split (`producers_cli.py` et al. extracted) — still a strong model for how far a CLI-registration split can continue |
| 4,753 | +3,753 | `plugins/agent-codespaces/src/agent_codespaces/__main__.py` | CLI registration surface |
| 4,508 | +3,508 | `plugins/agent-dispatch/src/agent_dispatch/queue.py` | The original motivating case for the cap itself |
| 3,946 | +2,946 | `plugins/agent-worktrees/src/agent_worktrees/tracking.py` | Partial split landed: the claim/follow-up/orphanage ledger now lives in `tracking_claims.py`, asserted head/handoff/create primitives in `tracking_lifecycle.py`, and the hook/session-registry + repo-freshness helpers in `tracking_session_registry.py`. What's left is the persistence-heavy core: `WorktreeRecord`, YAML load/save/merge, locking/stamp-queue machinery, and the remaining parse/serialize compatibility helpers. |
| 3,521 | +2,521 | `plugins/agent-dispatch/src/agent_dispatch/supervisor.py` | |
| 2,963 | +1,963 | `plugins/agent-bridge/src/agent_bridge/agent_registry.py` | Now `agent-bridge`'s dominant remaining offender after the `session_manager.py` mixin split. |
| 2,662 | +1,662 | `plugins/agent-worktrees/src/agent_worktrees/sessions.py` | |
| 2,583 | +1,583 | `plugins/agent-codespaces/src/agent_codespaces/config.py` | |
| 2,429 | +1,429 | `tools/clean-room/scenarios/agent-index-installation-cells/scenario.py` | Clean-room fixture, not production code — lower urgency |
| 2,369 | +1,369 | `tools/clean-room/scenarios/progressive-context-disclosure-baseline/fixture.py` | Clean-room fixture, not production code — lower urgency |
| 2,307 | +1,307 | `plugins/agent-worktrees/src/agent_worktrees/pr_ops.py` | |
| 2,207 | +1,207 | `plugins/agent-dispatch/src/agent_dispatch/supervisor_daemon.py` | |
| 2,195 | +1,195 | `plugins/agent-worktrees/src/agent_worktrees/reconcile.py` | |
| 2,124 | +1,124 | `plugins/agent-worktrees/src/agent_worktrees/session_projection.py` | |
| 2,107 | +1,107 | `plugins/agent-worktrees/src/agent_worktrees/config.py` | |
| 2,078 | +1,078 | `worktree-manager/src/worktree_manager/production_picker/picker_tui/data_ssh.py` | Now the largest remaining `worktree-manager` / production-picker module after the `engine.py` split; coherent same-package follow-up if the campaign stays in this area |
| 2,011 | +1,011 | `plugins/agent-worktrees/src/agent_worktrees/finalize.py` | |
| 2,006 | +1,006 | `plugins/agent-machines/src/agent_machines/resources.py` | |

**Suggested next pick (Phase 2, next slice):** if the priority is still the
largest remaining production offender overall, take
`plugins/agent-worktrees/src/agent_worktrees/__main__.py`. If the operator
wants to stay in `worktree-manager` / the production-picker package that
opened this effort, `picker_tui/data_ssh.py` is still the clearest local
follow-up. If the operator wants to stay in `agent-worktrees`,
`tracking.py` remains the clearest partially-resolved target: split the
persistence/serialization core (`load_record`, `_save_record_unlocked`,
locking, stamp queue, and the record/PR parse-merge helpers) away from the
still-large composition root. If the operator prefers a fresh
`agent-worktrees` file instead of another pass on the same one, `pr_ops.py`
remains the next best candidate. **Within `agent-bridge`, the next slice is now
`agent_registry.py`**: with `__main__.py` down to 485 lines and
`session_manager.py` down to 771, the registry is the plugin's new dominant
remaining offender.

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
      - [x] `agent-bridge/session_manager.py` (6,282 live lines at slice start,
            single `SessionManager` class with existing mixin precedent) —
            split by responsibility into `_SessionCoreMixin`
            (`session_core.py`), `_SessionHostConnectionMixin`
            (`session_host_connection.py`), `_SessionParityMixin`
            (`session_parity.py`), `_SessionHostRecoveryMixin`
            (`session_host_recovery.py`), `_SessionMonitoringMixin`
            (`session_monitoring.py`), `_SessionStartMixin`
            (`session_start.py`), `_SessionResumeMixin`
            (`session_resume.py`), `_SessionPromptMixin`
            (`session_prompts.py`), `_SessionLifecycleMixin`
            (`session_lifecycle.py`), and `_SessionHandoffMixin`
            (`session_handoff.py`), composed back through a 771-line
            `session_manager.py` compatibility root that still owns the shared
            helper/exception surface and the test monkeypatch seams. This
            followed the same "single large class sharing instance state"
            mixin pattern proven by `db.py`, while preserving
            `agent_bridge.session_manager.AcpClient` / `spawn` / helper
            monkeypatch compatibility by routing moved call sites back through
            the composition root where tests rely on that surface.
      - [x] `agent-bridge/agent_registry.py` (2,963 live lines at slice start,
            mixed top-level registry/topology helpers + one large `AgentResolver`
            class + namespace/relay support) — split by **real responsibility**
            rather than by a blind midpoint: shared constants/types moved to
            `agent_registry_common.py`; namespace contracts + CLI-backed
            providers to `agent_registry_namespace.py`; registry parsing,
            auto-discovery, related/repo registry reads, and topology-derived
            roster synthesis to `agent_registry_topology.py`; credential-relay
            profile helpers to `agent_registry_relay.py`; and the
            `AgentResolver` implementation to `agent_registry_resolver.py`.
            `agent_registry.py` now stays a compatibility/composition root that
            re-exports the historical surface, keeps the monkeypatch-heavy seams
            (`_detect_local_machine`, `resolve_repo_remote`,
            `_agent_worktrees_bin`) at the old import path, and still owns
            resolver construction / built-in namespace registration so existing
            imports and tests stay unchanged.
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
            - `agent-dispatch/__main__.py` first slice landed: extracted the
              coordinator/service family into `coordinator_cli.py`
              (serve/deploy/cutover, federation, installer-readiness,
              print-endpoint, and health) while keeping `__main__.py` as the
              composition root and re-export surface for every moved symbol the
              test suite monkeypatches. Remaining seams are still strong and
              separate: create/spawn, task lifecycle/steering/query, declarative
              loop surfaces, supervise registration wiring, and the
              resolve/run/evaluate/charter family.
            - `agent-dispatch/__main__.py` second slice landed: extracted the
              create/spawn family into `create_cli.py`
              (`create`/`propose`/`approve`, `producer-fence`, payload-file
              reads, cross-machine dispatch, spawn reservation + embody/bridge
              launch), again keeping `__main__` as the composition root and
              monkeypatch surface. Remaining seams are now the task lifecycle +
              steering + query surfaces, the declarative loop families,
              supervise registration wiring, and the
              resolve/run/evaluate/charter family.
            - `agent-dispatch/__main__.py` third slice landed: extracted the
              read/query family into `task_query_cli.py`
              (`list`, `doctor`, board/inbox helpers, `find`, `sweep`,
              `watch`, `payload`, `result`, `consume`, and `mcp`) while keeping
              `__main__` as the composition root and re-export seam. Remaining
              seams are now the task lifecycle + steering surfaces, the
              declarative loop families, supervise registration wiring, and the
              resolve/run/evaluate/charter family.
            - `agent-dispatch/__main__.py` fourth slice landed: extracted the
              steering/card family into `steering_cli.py`
              (`card set/show/draft`, `steer submit/take`) while keeping the
              root module as the compatibility re-export seam. Remaining seams
              are now the task lifecycle/admin surface, the declarative loop
              families, supervise registration wiring, and the
              resolve/run/evaluate/charter family.
            - `agent-dispatch/__main__.py` fifth slice landed: extracted the
              parser-registration-only families into tiny sibling modules —
              `execution_registration_cli.py`, `declaration_loops_cli.py`,
              `registrar_parser_cli.py`, and `supervise_registration_cli.py` —
              so the composition root no longer has to inline those giant
              argparse registration tables. Remaining seams are now the task
              lifecycle/admin command bodies themselves plus the
              registrar/execution helper implementations that still live in
              `__main__.py`.
            - `agent-dispatch/__main__.py` sixth slice landed: extracted the
              task lifecycle/admin command bodies into `task_lifecycle_cli.py`
              (`claim`, `worktree-status`, `show`, `claimant`, `yield`,
              `start`/`suspend`/`resume`/`release`, `pause`/`unpause`,
              `embody`, `force-stop`, `reset`, `progress`, `focus`,
              `complete`, `abandon`, and `reattach`) while deliberately
              leaving the shared helper seams (`_simple`, `_split_owner`,
              `_owner_from_identity`, `_resolve_owner`, `_hold_actor`,
              `_read_result`) in `__main__` for the other already-extracted
              families that still call through them. Remaining seams are now
              the registrar/runtime helper band and the
              resolve/run/evaluate helper band.
            - `agent-dispatch/__main__.py` seventh slice landed: extracted the
              resolve/run/evaluate/charter command bodies into
              `execution_cli.py` while keeping the dash-dash parser shim and
              the shared helper seams in `__main__`. Remaining work is now
              concentrated in the registrar/runtime helper band plus the
              shared compatibility helpers still used across the extracted
              modules.
            - `agent-dispatch/__main__.py` eighth slice landed: extracted the
              task lifecycle/admin argparse registration table into
              `task_lifecycle_registration_cli.py`, leaving the root module
              with the registrar/runtime helper band, the dash-dash parser
              shim, and the deliberately retained shared compatibility helpers.
            - `agent-dispatch/__main__.py` ninth slice landed: extracted the
              remaining shared CLI helpers into `shared_cli.py` and the
              registrar/runtime helper band into `registrar_runtime_cli.py`,
              bringing `__main__.py` down to a **919-line composition root**
              under the module-size cap. Final landing path: after **PR #3316**
              cleared the shared red-main blockers (`#3276`
              payload-invocation fallout, then `#3315`
              version-consistency/module-size drift on `main`), **PR #3283**
              rebased cleanly onto `main` and re-entered the final review/merge
              gate.
            - `agent-bridge/__main__.py` slice landed: the file is now a
              **485-line composition root** with focused sibling modules for
              service start/status (`service_start_cli.py`), daemon/process
              lifecycle (`service_process_state.py`, `service_process_cli.py`),
              venue parity + ZDD deploy (`venue_cli.py`), inventory/catalog
              commands (`inventory_cli.py`), session maintenance
              (`session_maintenance_cli.py`), prompt/targeting
              (`session_targeting_cli.py`), session start/read-target helpers
              (`session_start_cli.py`), streaming/wait/read/result
              (`session_streaming_cli.py`), lifecycle/handoff/agent mode
              (`session_lifecycle_cli.py`), and config/doctor
              (`config_cli.py`). Compatibility was preserved by re-exporting
              the historical `__main__` helper and command symbols so the
              existing monkeypatch-heavy tests still hit the moved
              implementations through the old import surface.
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
      - [x] The vendored-copy canonical
            `libs/installation-context/installation_context.py` (9,169,
            +18 vendored copies elsewhere, folded into one ranking row at slice start) —
            split only the canonical source into a 37-line composition root plus
            fourteen sibling `_installation_context_*.py` fragments (all under
            the 1,000-line cap) and propagate that exact file set with
            `sync-installation-context.py`. The fragment seams follow the file's
            real responsibility bands: base helpers, file/digest I/O, source
            normalization, receipts, snapshot provenance, runtime-slot ownership,
            runtime-slot completion, resolution, activation, legacy attribution,
            legacy transition, legacy retirement, maintenance, and mode/CLI.
            The composition root now executes the fragments into one shared
            module namespace so the vendored standalone loader contract and the
            existing monkeypatch-heavy test seams remain behavior-identical.
            Bumped every consuming plugin:
            `agent-bridge` `0.4.0-dev537`, `agent-codespaces`
            `0.4.0-dev151`, `agent-containers` `0.1.2-dev152`,
            `agent-dispatch` `0.1.2-dev165`, `agent-index` `0.1.0-dev196`,
            `agent-logger` `0.1.2-dev30`, `agent-machines`
            `0.1.0-dev132`, `agent-mcp` `0.2.0-dev133`, `agent-ssh`
            `0.1.0-dev100`, `agent-vault` `0.1.0-dev112`, and
            `agent-worktrees` `1.5.5-dev230` (with marketplace
            `metadata.version` `1.7.7-dev199`).

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

### 2026-09-21 — Phase 2 continued: `agent-worktrees/__main__.py` resolve design slice
- Took the design-budget slice the prior three entries had explicitly deferred,
  and kept the scope to **`cmd_resolve` only**. Instead of another mechanical
  move, first mapped its real sub-flows and the state each closes over:
  selector normalization / codename resolution, JSON-vs-interactive dispatch,
  SSH machine/environment handoff planning, base/new/resume launch planning,
  and the legacy picker + system-menu fallback. Then introduced small explicit
  state carriers for those seams rather than letting the extracted modules keep
  reaching through one giant closure: `ResolveCommandState` in `resolve_cli.py`
  owns the top-level selector flags plus lazy config loading; `ResolveLaunchContext`
  in `resolve_launch_cli.py` carries the shared config/args/profile/record/
  preflight state for base/new/resume planning; and `ResolvePickerContext` in
  `resolve_picker_cli.py` carries the config/args/tracking/platform state the
  legacy ANSI picker loop and its system-menu sub-flows reuse.
- Landed the split as five sibling modules, each named for the sub-flow it now
  owns: `resolve_cli.py` (parser registration + `cmd_resolve` dispatch),
  `resolve_launch_cli.py` (profile selection plus base/new/resume planners),
  `resolve_machine_cli.py` (cross-machine / cross-environment SSH handoff
  planning), `resolve_picker_cli.py` (legacy picker fallback, manager handoff,
  machine sub-menu), and `resolve_system_cli.py` (the legacy picker's cleanup /
  update / status / daemon-worktree system menu). `agent_worktrees.__main__`
  remains the composition root and compatibility surface: `cmd_resolve` is now
  a thin wrapper, `build_parser()` delegates the `resolve` parser stub to the
  new module, and the moved helpers are re-exported back onto `__main__` so the
  existing direct-import and monkeypatch seams the test suite depends on keep
  landing in exactly the old place.
- Net result: `plugins/agent-worktrees/src/agent_worktrees/__main__.py`
  dropped from **13,669** lines at the start of this slice to **11,300**
  after the baseline refresh (**29,173 → 11,300** across the
  eight-slice campaign so far). The new modules land at **645** lines
  (`resolve_cli.py`), **642** (`resolve_launch_cli.py`), **234**
  (`resolve_machine_cli.py`), **578** (`resolve_picker_cli.py`), and **472**
  (`resolve_system_cli.py`), all under the 1,000-line cap.
- Validation matched the effort's stricter bar. `ruff check --select F,E9`
  passed on `__main__.py`, the five new modules, and the one touched test
  guard. The full `python tools/run-plugin-tests.py agent-worktrees` run again
  reproduced only the same independently-confirmed pre-existing
  `tests/test_doctor.py` failures (**551 passed, 10 failed, 1 skipped**) on its
  second sub-suite stop, so per the runbook I followed with a broad targeted
  resolve-area sweep reaching the moved planner/picker/profile/codename/cross-
  machine surfaces (**559 passed, 19 skipped, 4486 deselected**). After the
  split, `python tools/check-module-size.py --refresh-baseline`, plain
  `python tools/check-module-size.py`, `python tools/check-install-contract.py`,
  and `python tools/check-version-consistency.py` all pass, and read-only
  dogfooding also passes for `agent-worktrees resolve --help` and
  `agent-worktrees copilot --help`.
- Remaining backlog changed shape materially. The high-risk `resolve` knot is
  gone; what remains of the launch surface in `__main__` is primarily
  `cmd_copilot` plus compatibility glue, while the heavier absolute line count
  is now the resident-monitor / hook / status / history region. The next slice
  should re-rank those seams fresh rather than assuming the work still revolves
  around `resolve`. Version bump for this slice: `agent-worktrees`
  `1.5.5-dev216` and marketplace `metadata.version` `1.7.7-dev185`.

### 2026-09-21 — Review follow-up: stale config cache after project relocation
- PR #3178's last GitHub Copilot review round landed a real, medium-severity
  finding (`resolve_cli.py:616`) only ~11 seconds before the PR auto-merged --
  the same race the earlier stale-marketplace-purge follow-up hit -- so it
  never reached that PR's `main` commit.
- The bug: `_resolve_noninteractive_worktree` calls
  `_relocate_active_project_for_worktree(worktree_id)` unconditionally, then
  reloads config via `state.load_config()` -- but that reload is a no-op
  whenever `state.config` is already cached from an earlier base-repo probe,
  so a relocation to a *different* project's worktree still returns the
  *original* project's config. Preflight, session environment, and the
  emitted launch plan would then silently use the wrong project's repo
  settings. The JSON-output sibling path a few lines above already handles
  this correctly (`if _relocate_active_project_for_worktree(worktree_id):
  state.config = None`); this path just didn't mirror it.
- Fixed by mirroring that exact pattern: only clear `state.config` when
  `_relocate_active_project_for_worktree` reports it actually relocated
  something, then let the existing `load_config()` call re-populate it.
  Confirmed via `Select-String` that none of the other four `resolve_*`
  sibling modules from the prior slice call
  `_relocate_active_project_for_worktree` at all, so this was the only call
  site with the gap.
- Validation: targeted `python tools/run-plugin-tests.py agent-worktrees -k
  "resolve or relocate"` -- 242 passed, 18 skipped; `ruff check --select
  F,E9` on the touched file; `check-module-size.py`,
  `check-install-contract.py`, and `check-version-consistency.py` all clean.
  Version bump: `agent-worktrees` `1.5.5-dev217`, marketplace
  `metadata.version` `1.7.7-dev186`.
- **Follow-up on the follow-up:** this PR's own review round (this time
  landing well before merge, since the process note above was already
  heeded) asked for regression coverage of the fix itself, not just of
  `_relocate_active_project_for_worktree` in isolation. Added
  `test_noninteractive_resume_reloads_config_after_relocation` to
  `test_resolve_cross_project.py`, which drives
  `_resolve_noninteractive_worktree` end-to-end with a pre-cached stale
  config and asserts every downstream call (profile validation, preflight,
  profile resolution, resume) receives the freshly-reloaded post-relocation
  config, never the stale one. Full targeted suite: 243 passed, 18 skipped.
- **Process note:** this is the second time a genuine, late-arriving Copilot
  review finding has landed within seconds of an auto-merge and needed a
  separate recovery PR. Worth treating as a pattern: after pushing a final
  fix commit in response to review feedback, wait for one more full review
  round to actually complete (or an explicit "no new findings" confirmation)
  before self-merging, rather than merging as soon as checks go green.

### 2026-09-21 — Phase 2 continued: `agent-worktrees/__main__.py` session-metadata + maintenance + git slice
- Took the next **real** cohesive seams left after the resolve split instead of
  forcing the still-risky live launch core. Extracted three sibling modules
  while keeping `__main__.py` as the composition root and compatibility
  surface: `session_metadata_cli.py` now owns the session/history/effort
  family (`session-lock`, `session-role`, `history-digest`, `effort-focus`,
  `claimant-liveness`, `codename-lookup`) plus the supporting succession /
  binding helpers; `maintenance_cli.py` now owns the diagnostics/maintenance
  family (`hygiene`, `dev`, `backfill-sessions`, `doctor`,
  `reconcile-binstubs`, `register-project-entry`, `anchor-check`,
  `config-migrate`) plus the backfill/doctor render helpers; and `git_cli.py`
  now owns the `git` collaboration subgroup (`sync`, `feature-branch`,
  `merge-to-feature`) plus its parser/target-resolution helpers. Rewired
  `build_parser()` to delegate those parser stubs to the new modules and
  re-exported the moved names back onto `agent_worktrees.__main__` so existing
  monkeypatch seams and direct imports keep landing where the tests expect.
- Net result: `plugins/agent-worktrees/src/agent_worktrees/__main__.py`
  dropped from **11,305** lines at slice start to **9,420** (a **1,885-line**
  reduction this pass; **29,173 → 9,420** across the full nine-slice
  campaign). The new modules land at **655** (`session_metadata_cli.py`),
  **987** (`maintenance_cli.py`), and **219** (`git_cli.py`) lines, all under
  the 1,000-line cap. With `cmd_resolve` already thin and `cmd_copilot`
  explicitly excluded, the remaining `__main__.py` backlog is now much more
  sharply constrained: the resident `status-monitor` body, the bare/front-door
  launch seam (`cmd_help_unrouted`, Worktree Manager fallback,
  `cmd_noninteractive_bare`, `cmd_headless_bare`), and the shared launch/router
  glue around `cmd_launch` / `cmd_execution_leg`.
- Validation stayed at the stricter componentization bar. Ruff (`--select
  F,E9`) passed on `__main__.py` and all three new modules. The first full
  `python tools/run-plugin-tests.py agent-worktrees` pass exposed one real
  regression from the move itself: the transferred `effort-focus release
  --transfer` path had accidentally preserved the wrong `follow_up` /
  disposition behavior. Restored the original semantics, then re-ran a broad
  extracted-area targeted sweep covering the moved command families and their
  preserved seams (**311 passed, 1 skipped, 4789 deselected**). The final full
  plugin run again reproduced only the same independently-confirmed
  pre-existing `tests/test_doctor.py` failures (**551 passed, 10 failed,
  1 skipped**) already called out in prior slices, so the extraction itself is
  green apart from that known unrelated suite issue.
- GitHub Copilot's first review round on the PR then found one real
  compatibility seam miss before merge: `cmd_session_role()` and
  `cmd_history_digest()` were still calling the extracted module's private
  `_resolve_worktree_for_read()` directly, so tests or callers monkeypatching
  `agent_worktrees.__main__._resolve_worktree_for_read` no longer intercepted
  those commands. Fixed by routing both handlers back through the re-exported
  `__main__` helper surface (`_core()._resolve_worktree_for_read(...)`), which
  restores the exact monkeypatch seam the slice promised to preserve.
- The fresh post-fix review round then caught one smaller previously-missed
  compatibility contract: the extracted `cmd_effort_focus()` had changed the
  fallback invalid-action exit code from the pre-extraction `1` to `2`. Even
  though argparse normally prevents that path, the handler is still
  re-exported for direct callers/tests, so restored the original `return 1`
  behavior before merge.
- Followed through on the remaining contract checks after the code stabilized:
  `python tools/check-module-size.py` passed, `python tools/check-module-size.py
  --refresh-baseline` lowered `tools/module-size-baseline.json`'s ceiling for
  `agent_worktrees/__main__.py` to **9,420**, and
  `python tools/check-install-contract.py` plus
  `python tools/check-version-consistency.py` both pass. Dogfooded the moved
  surfaces through the plugin test venv with read-only `--help` / `--json`
  invocations (`session-role`, `history-digest`, `effort-focus`,
  `claimant-liveness`, `codename-lookup`, `hygiene`, `doctor`, `anchor-check`,
  and the `git` subgroup's usage forms). Version bump for this slice:
  `agent-worktrees` **`1.5.5-dev220`** and marketplace `metadata.version`
  **`1.7.7-dev188`**.

### 2026-09-21 — Phase 2 continued: `agent-worktrees/__main__.py` status/front-door slice
- Took the two last strong command-surface seams still left in
  `agent-worktrees/__main__.py` without touching the explicitly forbidden
  `cmd_copilot`. The resident monitor command itself now lives in a dedicated
  `status_monitor_cli.py` (parser + `cmd_status_monitor()` only), while the
  already-existing `status_monitor_runtime.py` stays the runtime/reconcile/
  restart helper module. Separately, the no-project / bare-launch front door
  now lives in `front_door_cli.py`: global `--project` extraction, the
  `<repo> <slug>` sibling router, no-project command classification, project
  resolution from cwd, `cmd_help_unrouted()`, Worktree Manager probing and
  handoff, the install trigger, and the noninteractive/headless bare fallback
  surfaces all moved there as one cohesive seam. `__main__.py` remains the
  composition root and compatibility surface by re-exporting those moved names
  back onto `agent_worktrees.__main__`.
- The first attempt put `cmd_status_monitor()` straight into
  `status_monitor_runtime.py`, but that produced a brand-new **1,086-line**
  module and failed `check-module-size.py`. Rather than widen the baseline on a
  file that had just been created, split the command surface back out into
  `status_monitor_cli.py` so the runtime helper file lands at **800** lines and
  the command wrapper at **303**. `front_door_cli.py` lands at **897** lines.
  Net result for the parent module: `plugins/agent-worktrees/src/agent_worktrees/__main__.py`
  dropped from **9,420** lines at slice start to **8,140** (a **1,280-line**
  reduction this pass; **29,173 → 8,140** across the full ten-slice campaign).
- The live `cmd_*` inventory after this slice is now down to exactly:
  `cmd_launch`, `cmd_execution_leg`, the explicitly excluded `cmd_copilot`,
  thin-wrapper `cmd_resolve`, and thin-wrapper `cmd_handoff_trace`. That is the
  campaign's key structural result: the clean command-family seams in
  `__main__.py` are effectively exhausted. Pushing further would mean carving
  internal helper logic out of the launch/execution composition root, not
  continuing the same strong "one command family → one sibling module" pattern.
- Validation caught and then closed several compatibility-seam regressions that
  came specifically from moving monkeypatch targets out of `__main__`. The
  targeted routing/lock sweep (**139 passed**) first exposed missing re-exports
  (`_NO_PROJECT_COMMANDS`, `_CORE_SLUGS`, Worktree Manager constants) and local
  helper calls inside `front_door_cli.py` that bypassed monkeypatched
  `agent_worktrees.__main__` aliases. Fixed by re-exporting the constants and
  routing the moved helper calls back through `_core_helper(...)` / the
  re-exported `__main__` surface where callers/tests already patch them.
  A second targeted monitor/launch sweep (**147 passed**) then caught one more
  preserved test seam: `tests/test_status_monitor.py` still monkeypatches
  `agent_worktrees.__main__.loop_governance_mod`, so restored that import as a
  compatibility re-export even though the real implementation now lives in the
  extracted command module.
- Final validation for the slice: Ruff (`--select F,E9`) passes on
  `__main__.py`, `front_door_cli.py`, `status_monitor_cli.py`, and
  `status_monitor_runtime.py`; `python tools/run-plugin-tests.py
  agent-worktrees -k "cli_routing or bridge_lock"` passes (**139 passed**);
  `python tools/run-plugin-tests.py agent-worktrees -k "status_monitor or
  launch_cmd"` passes (**147 passed**); and the full
  `python tools/run-plugin-tests.py agent-worktrees` run again stops only at the
  same independently-confirmed unrelated `tests/test_doctor.py` failures
  (**551 passed, 10 failed, 1 skipped**) already accepted as pre-existing.
  `python tools/check-module-size.py --refresh-baseline` lowered
  `tools/module-size-baseline.json`'s ceiling for `agent_worktrees/__main__.py`
  to **8,140**, and `python tools/check-install-contract.py` plus
  `python tools/check-version-consistency.py` both pass. Dogfooded the touched
  surfaces with read-only help/output paths through the plugin test venv:
  global `--help`, `status-monitor --help`, `reconcile-sessions --help`,
  `status-monitor-restart --help`, plain `reconcile-sessions` JSON output, the
  no-project bare help fallback, and the Worktree Manager install trigger.
  Version bump for this slice: `agent-worktrees` **`1.5.5-dev223`** and
  marketplace `metadata.version` **`1.7.7-dev191`**.

### 2026-09-21 — Tooling fix: `--changed-since` no longer falls back to a fully unscoped sweep on baseline touch
- PR #3205 (the status/front-door slice above) merged despite CI's `guards +
  lint` job reporting `FAILURE` -- caught while auditing that merge. The real
  cause: `check-module-size.py --changed-since REF` unconditionally fell back
  to a **fully unscoped, whole-tree sweep** whenever a diff touched
  `tools/module-size-baseline.json` -- which is every single slice of this
  campaign, since every split legitimately lowers its own file's ceiling. That
  full sweep then failed the PR on completely unrelated, already-baselined
  files elsewhere (`agent-bridge/__main__.py`, `agent_registry.py`,
  `client.py`; `agent-dispatch/__main__.py`, `bridge.py`, `embody.py`,
  `supervisor.py`) that had drifted slightly past their own recorded ceilings
  via other, unrelated merged PRs -- exactly the unfair-attribution failure
  mode `--changed-since` exists to prevent, just reintroduced through the one
  documented exception meant to keep a baseline edit self-consistent.
- Fixed `tools/check-module-size.py`: added `_changed_baseline_keys(base_ref)`,
  which diffs `tools/module-size-baseline.json` between the merge-base and
  `HEAD` to find exactly which entries a diff itself added, changed, or
  removed. When a diff touches the baseline, scope is now
  `_changed_py_files(...) | _changed_baseline_keys(...)` -- this diff's own
  changed `*.py` files, plus every baseline entry it itself edited -- instead
  of `only_paths=None` (full sweep). This still catches the original concern
  the full-sweep fallback existed for (a baseline edit that silently
  mismatches a file's actual size, even one outside the diff's `*.py` list),
  because that file's entry is, by definition, part of the diff's own
  baseline edit and therefore always in scope. It just stops re-litigating
  every *other*, untouched entry on every single baseline-touching PR.
- Added three regression tests to `tools/test_check_module_size.py`,
  replacing the old (now-inaccurate) "falls back to full sweep" test:
  `test_changed_since_still_checks_a_baseline_entry_this_diff_edits` (the
  original safety property, preserved), `test_changed_since_with_baseline
  _touch_does_not_blame_unrelated_drift` (the exact PR #3205 shape,
  reproduced with real git branching/rebase so the "unrelated" growth
  genuinely lands via a separate, already-advanced trunk commit rather than
  this diff's own commits), and
  `test_changed_since_with_baseline_touch_still_catches_this_diffs_own_growth`
  (confirms the widened scope isn't a loophole for this diff's *own* growth).
  All 20 tests in the file pass; `ruff check --select F,E9` clean. Confirmed
  the plain, flagless full sweep (used for push-time/scheduled runs, not PR
  gating) still correctly reports the seven pre-existing agent-bridge/
  agent-dispatch violations above -- this fix only narrows the *PR-time,
  `--changed-since`-scoped* gate, never detection elsewhere.
- No version bump needed (`tools/` is a repo-root path, not vendored into any
  plugin, per `CONTRIBUTING.md`'s version-bump scope).
- **Those seven pre-existing agent-bridge/agent-dispatch violations remain
  unaddressed** -- they're genuine Phase 2 organic-drift backlog, exactly the
  kind of thing the watchdog/decomposer mechanism (not this PR-time gate, and
  not this campaign's own agent-worktrees-focused slices) exists to pick up.
  Noted here rather than fixed in scope of this tooling PR.

### 2026-09-21 — Phase 2 continued: `agent-worktrees/tracking.py` obligations + lifecycle + session-registry slice
- Pivoted the campaign away from the now-exhausted `agent-worktrees/__main__.py`
  command-family work and into `plugins/agent-worktrees/src/agent_worktrees/tracking.py`.
  Read the full file first, then split along its real responsibility seams rather
  than forcing a midpoint cut. The result is a partial but clean extraction:
  `tracking_claims.py` now owns the claim/follow-up/orphanage ledger (data
  models plus mutation helpers), `tracking_lifecycle.py` owns the asserted
  head/handoff/create primitives, and `tracking_session_registry.py` owns the
  hook/session-registry runtime helpers (`register_session` /
  `deregister_session`, activation history, repo freshness, and fsmonitor
  cleanup). `tracking.py` stays the compatibility/composition root for the
  persistence-heavy core and re-exports the moved surfaces so existing callers
  and tests keep their import/monkeypatch targets.
- Net result for the parent module: `tracking.py` dropped from **5,872** lines
  at slice start to **3,946** by `tools/rank-module-size.py`'s accounting
  (still over cap, but materially smaller). The three new sibling modules land
  at **624** (`tracking_claims.py`), **493** (`tracking_lifecycle.py`), and
  **396** (`tracking_session_registry.py`) lines respectively. The remaining
  `tracking.py` bulk is now much more sharply defined: `WorktreeRecord`, YAML
  load/save/merge logic, record locking + stamp-queue persistence helpers, and
  the PR/controller/session-backend parse/serialize compatibility machinery.
- Validation caught two compatibility regressions created by the first pass,
  both caused by moved internals bypassing established public seams. First, the
  initial split hid `_derive_initial_controller_relations` from
  `create_new_record()`'s compatibility path, which the `claim_handoffs` and
  broader tracking tests surfaced immediately; fixed by having the extracted
  lifecycle module import the controller-relation helpers directly rather than
  reaching back through a no-longer-re-exported private alias. Second, the
  orphanage tests still monkeypatch `agent_worktrees.tracking.orphanage_path`;
  the moved `rehome_abandoned_obligations()` / `remove_orphaned_obligations()`
  initially called the sibling module's local helper directly, bypassing that
  public seam. Fixed by routing those calls back through the re-exported
  `tracking.orphanage_path` / `tracking.load_orphaned_obligations_strict`
  surface so tests and downstream monkeypatching keep working exactly as before.
- Final validation for the slice: Ruff (`--select F,E9`) passes on
  `tracking.py` and all three new sibling modules. The first full
  `python tools/run-plugin-tests.py agent-worktrees` run flushed out the two
  real regressions above; after those fixes, the second full run again stops
  only at the same independently-confirmed unrelated `tests/test_doctor.py`
  cluster (**551 passed, 10 failed, 1 skipped**). To ensure every touched area
  still had direct coverage despite that early stop, ran a focused
  `python tools/run-plugin-tests.py agent-worktrees -k "tracking or claimant or
  claim_handoffs or obligation or orphanage or follow_up or repo_freshness or
  register_session or session_lifecycle or terminal_conclusion"` sweep, which
  passed **638** tests with **4,489** deselected. `python tools/check-module-size.py`
  and `python tools/check-module-size.py --refresh-baseline` pass, lowering
  `tools/module-size-baseline.json`'s ceiling for
  `agent_worktrees/tracking.py` to match the new post-slice size. `python
  tools/check-install-contract.py` and `python tools/check-version-consistency.py`
  both pass. Dogfooded the moved live/read paths through read-only CLI usage:
  `agent-worktrees register-session --help`, `deregister-session --help`,
  `claimant-liveness --help`, `claimant-liveness anomalous-potato/proj/wt-1`,
  `anchor-check --help`, `hygiene --help`, and the generic top-level
  `agent-worktrees --help`.
- Version bump for this slice: `agent-worktrees` **`1.5.5-dev228`** and
  marketplace `metadata.version` **`1.7.7-dev196`**.

### 2026-09-21 — Phase 2 continued: `libs/installation-context/installation_context.py` canonical split
- Took the effort's largest remaining vendored offender exactly as the runbook
  requires: split only the canonical
  `libs/installation-context/installation_context.py`, then propagated the new
  file set with `python tools/sync-installation-context.py` rather than editing
  any downstream copy by hand. The file's actual seams were not "one more CLI"
  or "one giant class"; they were a long sequence of top-level responsibility
  bands. The resulting canonical shape is a **37-line composition root** plus
  fourteen sibling fragments, each under the 1,000-line cap:
  `_installation_context_base.py` (352), `_installation_context_files.py`
  (854), `_installation_context_source.py` (653),
  `_installation_context_receipts.py` (667),
  `_installation_context_snapshot.py` (480),
  `_installation_context_runtime_slot_ownership.py` (636),
  `_installation_context_runtime_slot_completion.py` (797),
  `_installation_context_resolution.py` (553),
  `_installation_context_activation.py` (424),
  `_installation_context_legacy_attribution.py` (836),
  `_installation_context_legacy_transition.py` (781),
  `_installation_context_legacy_retirement.py` (512),
  `_installation_context_maintenance.py` (717), and
  `_installation_context_mode_cli.py` (900).
- The crucial compatibility constraint was **not** just imports; it was the
  vendored-loader and monkeypatch seam. This primitive is loaded both as a
  standalone script and through `importlib.util.spec_from_file_location(...)`
  from vendored `_installation_context.py` copies inside plugin packages, and
  its tests monkeypatch root-module helper names heavily. A normal
  `from .foo import ...` module split would have broken both shapes. The chosen
  composition root instead executes the fragments into one shared module
  namespace, preserving the existing single-module runtime semantics while
  still breaking the canonical source into reviewable, capped files. Ruff's
  `F821` undefined-name checks are suppressed **only** on the shared-namespace
  fragments themselves, with an inline note explaining why; the composition root
  and ordinary modules still lint normally.
- Sync propagation now carries the whole Python fragment set, not just the one
  root file: `tools/sync-installation-context.py` copies every
  `_installation_context_*.py` helper into both the script vendoring surface
  (`plugins/<plugin>/scripts/installation-context/`) and the packaged runtime
  surface (`plugins/<plugin>/src/<pkg>/_installation_context*.py` plus
  `worktree-manager/src/worktree_manager/`). Updated the related vendoring and
  packaging tests so staged payloads copy the full helper set, not a lone
  `installation_context.py`, and so byte-identity assertions cover the new
  fragments in the packaged copies too.
- Validation and fix-ups: the first broad adopter run flushed out one real
  regression created by the initial cut — the receipt-state helper trio
  (`_assert_positive_integer`, `_assert_receipt_state`,
  `_assert_receipt_generation`) had been left behind just above the original
  split boundary, so vendored `stamp_context()` callers raised
  `NameError: _assert_receipt_state is not defined`. Moved those helpers into
  the receipts fragment, re-synced all adopters, and re-ran the affected
  installation-context-targeted suites until the new shape was green.
- Final validation for this slice:
  `python tools/sync-installation-context.py --check`,
  `python tools/check-vendored-libs-sync.py`,
  `python tools/check-module-size.py`,
  `python tools/check-module-size.py --refresh-baseline`,
  `python tools/check-install-contract.py`, and
  `python tools/check-version-consistency.py` all pass. Ruff (`--select F,E9`)
  passes across the touched/created canonical files and synced copies. Full
  `python tools/run-plugin-tests.py <plugin>` runs were executed for every
  vendor plugin; only `agent-index` and `agent-mcp` were fully green end to
  end on this Windows host, while the others stopped in independently observed,
  unrelated Windows/bash or host-specific failures already present in their
  own suites. Per the componentization runbook's pre-existing-failure rule,
  targeted installation-context-adjacent sweeps then passed for every affected
  surface we changed:
  `agent-bridge -k "handoff_cli or worktree_ownership"`,
  `agent-codespaces -k "worktrees_peer"`,
  `agent-containers -k "worktrees_peer or provider_ssh"`,
  `agent-dispatch -k "procutil"`,
  `agent-index -k "effective_config"`,
  `agent-logger -k "worktrees_peer"`,
  `agent-machines -k "cell_lifecycle"`,
  `agent-mcp -k "bootstrap_stamp_selection or payload_invocation"`,
  `agent-ssh -k "payload_invocation"`,
  `agent-vault -k "payload_invocation"`, and
  `agent-worktrees -k "registry_paths or reconcile"`.
  Read-only dogfood also passed for every vendored script copy
  (`python ...\\installation_context.py --help`) and every packaged vendored
  module surface (loader-accurate `spec_from_file_location` import smoke for
  the seven packaged `_installation_context.py` copies).
- Resulting backlog shift: the canonical installation-context giant is gone
  from the pecking order entirely. The repo's largest remaining baselined
  offender is now `worktree-manager/.../picker_tui/engine.py` at 9,267 lines;
  within `agent-worktrees`, the most coherent follow-up remains the
  persistence/serialization core left in `tracking.py`.

### 2026-09-21 — Phase 2 continued: `worktree-manager/.../picker_tui/engine.py` split
- Closed the loop on the file that opened this effort in the first place:
  read the full `picker_tui/engine.py`, reconciled it to the picker vision's
  UI-fidelity requirement, and split it along the seams the file itself had
  already grown into instead of forcing a synthetic midpoint cut. The result is
  a thin **597-line composition root** plus sibling modules by responsibility:
  `engine_helpers.py` (596), `engine_regions.py` (527), `engine_focus.py` (99),
  `engine_dialogs.py` (943), `engine_live_screens.py` (365),
  `engine_views.py` (743), `engine_profiles_view.py` (517),
  `engine_pivots.py` (247), `engine_loading.py` (460),
  `engine_runtime.py` (457), `engine_model.py` (430),
  `engine_selection.py` (435), `engine_rendering.py` (880),
  `engine_input.py` (360), `engine_maintenance_actions.py` (426),
  `engine_worktree_actions.py` (739), and `engine_pivot_actions.py` (627).
  Every extracted file stays under the 1,000-line cap.
- The structural shape matches the runbook's "one giant stateful class" advice:
  `PickerScreen` stays the single state owner, but its method families now live
  in focused mixins (pivot discovery, live loading, data/model queries,
  selection/navigation, rendering, maintenance/worktree actions, and
  registered-pivot actions). The already-separable satellites moved completely
  out of the root file too: native focus widgets, modal screens, live progress
  / message viewers, and the four body-view components. `steering.py` now
  reaches `FocusGroup` through the new focused module instead of importing the
  entire engine, avoiding a new circular edge while preserving every existing
  import path the tests and preview scripts use (`engine.py` re-exports the
  old public surfaces).
- This slice directly addresses the effort's origin-story failure mode from
  #2788/#2794. That regression happened because unrelated UX work had only one
  enormous place to land, so another small feature could quietly push the same
  already-baselined monolith further over its ceiling. After this split,
  those concerns have bounded homes: a future tweak to list rendering, modal
  UX, background loading, or worktree actions no longer has to pile onto a
  single 9,267-line file just because that's where everything happened to live.
- Validation/fix-ups mattered here because this is a human-facing TUI, not a
  backend-only helper. The first extraction pass surfaced exactly the kind of
  compatibility seams this effort is meant to harden: exported helper symbols
  (`_resolve_version`, `_idle_timeout_secs`, palette constants, relation icons)
  and monkeypatch seams (`POLL_SECS`) still had to live at the historical
  `engine` import surface; `_ZONE_WIDGET` had to remain a class attribute, not
  disappear into an extracted module; and the property shims onto
  `profiles_view` had to preserve their getter/setter decorators. Fixed those,
  re-ran Ruff until the new module graph was clean, then re-ran the full
  worktree-manager suite until the production-picker corpus was fully green.
- Final validation for the slice:
  `test-supervisor -- uv run --extra dev pytest -q` passed
  **1084 tests** with **3 skipped**;
  `uv run ruff check --select F,E9 src/worktree_manager/production_picker/picker_tui/engine.py src/worktree_manager/production_picker/picker_tui/engine_*.py src/worktree_manager/production_picker/picker_tui/steering.py`
  passed; read-only import/dogfood coverage was exercised implicitly by the
  production-picker test suite plus the worktree-manager CLI tests. Version bump
  for this slice: `worktree-manager` **`0.1.0-dev61`** in both
  `worktree-manager/pyproject.toml` and `worktree-manager/src/worktree_manager/__init__.py`.
- Resulting backlog shift: `engine.py` disappears from the module-size
  pecking order entirely. The repo's largest remaining baselined offender is
  now `plugins/agent-worktrees/src/agent_worktrees/__main__.py` at 8,145
  lines; within the same `worktree-manager` picker package, the next coherent
  local target is `picker_tui/data_ssh.py` at 2,078 lines.

### 2026-09-21 — Phase 2 continued: `plugins/agent-bridge/src/agent_bridge/__main__.py` split
- Pivoted this campaign into `agent-bridge`'s largest untouched offender and
  treated it as the runbook's canonical CLI-registration split rather than a
  "move a few helpers" nibble. The live file's seams held up under a full read:
  a thin shared surface at the top, then distinct command families
  (service start/status and remote-carrier verbs; daemon/process-management;
  venue parity + installer-internal deploy; inventory/catalog verbs; session
  maintenance; send/create targeting; session start/read-target selection;
  streaming/wait/read/result; lifecycle/handoff/agent mode; config/doctor).
  Extracted those into focused sibling modules and left `__main__.py` as the
  composition root + compatibility surface. Final shape:
  `__main__.py` **485** lines (from **6,971**),
  `service_start_cli.py` (643),
  `service_process_state.py` (331),
  `service_process_cli.py` (641),
  `venue_cli.py` (341),
  `inventory_cli.py` (425),
  `session_maintenance_cli.py` (274),
  `session_targeting_cli.py` (544),
  `session_start_cli.py` (336),
  `session_streaming_cli.py` (612),
  `session_lifecycle_cli.py` (371), and
  `config_cli.py` (167). Every extracted file stays under the 1,000-line cap.
- The key design constraint was **compatibility, not just import hygiene**.
  This CLI has extensive tests -- and real sibling modules like
  `restart_worktree_cli.py` -- that import or monkeypatch historical
  `agent_bridge.__main__` helpers directly. A naive extraction would have left
  tests (and any runtime monkeypatch seam) mutating dead aliases. The chosen
  pattern preserves the old surface by re-exporting the moved functions from
  `__main__.py`, while the new modules resolve cross-calls through the live
  `agent_bridge.__main__` module (`core._foo`) wherever a monkeypatch seam or
  back-compat call path mattered. This mirrors the coordinator split's lesson:
  keep the old seam alive until callers stop depending on it.
- Validation surfaced exactly those seam hazards. The first cut broke tests that
  monkeypatch `__main__._resolve_target`, `__main__._live_sender_label`,
  `__main__._live_reply_to`, `__main__._spawn_detached_argv`, and
  `__main__._spawn_watchdog_replacement` because the moved code had started
  calling its own local implementations directly. Fixed by routing those calls
  back through the composition root so existing monkeypatches still steer the
  real runtime path. After that, the focused CLI corpus was green again without
  changing behavior.
- Final validation for the slice:
  `python tools/run-plugin-tests.py agent-bridge` hit **569 passed / 2 failed /
  2 skipped** before stopping in sub-suite 1 on two pre-existing Windows
  `bash.exe` pathing failures in
  `tests/test_bootstrap_check_reconcile_opt_in.py`
  (`test_sh_skips_spawn_without_opt_in`,
  `test_sh_attempts_spawn_with_opt_in`), unrelated to this Python-only split.
  Per the runbook's early-stop warning, a targeted follow-up run covering the
  touched CLI/runtime surfaces then passed:
  `python tools/run-plugin-tests.py agent-bridge -k "attention_wait or agent_single_lookup_cli or cli_mode_launch or create_singleton_cli or dynamic_bind or emit_back or phased_timeouts or project_override or prompt_file or remote_operations or result_snapshot or send_coming_up or service_port_liveness or service_stop_wedged or session_selection or status_and_read or stream_sim or wmi_broker_spawn or payload_invocation or send_help or restart_worktree_cli"`
  -> **330 passed, 2 skipped**. Ruff (`--select F,E9`) passed across every
  touched/created CLI module. `python tools/check-module-size.py` passed and
  `--refresh-baseline` removed `agent-bridge/__main__.py` from the shrink-only
  baseline entirely.
- Read-only dogfood stayed within the daemon-safety guardrails: validated only
  safe surfaces (`agent-bridge --help`, `agent-bridge version`,
  `agent-bridge status --help`, `agent-bridge session-usage --help`, parser
  coverage via `test_payload_invocation.py` / `test_send_help.py`) and did **not**
  start, stop, restart, drain, spawn, or otherwise touch a live bridge daemon.
- Resulting backlog shift: `agent-bridge/__main__.py` disappears from the
  pecking order entirely. The plugin's next dominant target is now
  `plugins/agent-bridge/src/agent_bridge/session_manager.py` at **6,751**
  lines, which is also the repo's second-largest remaining offender overall
  after `agent-worktrees/__main__.py`. Recommendation: if the campaign stays in
  `agent-bridge`, take `session_manager.py` next; the new CLI split removed the
  plugin's front-door sprawl, leaving that session core as the clear follow-up.

### 2026-09-21 — Phase 2 continued: `plugins/agent-bridge/src/agent_bridge/session_manager.py` mixin split
- Took the exact follow-up seam the prior `agent-bridge/__main__.py` slice had
  queued: the session core had become the plugin's dominant remaining offender,
  and unlike the CLI registrar it was one huge stateful class with an **existing
  mixin precedent already in-file** (`_RecoveryDormancyMixin`,
  `_HostLivenessMixin`). That made it the campaign's clearest re-use of the
  `db.py` pattern instead of a new decomposition shape.
- Kept the shared support surface in `session_manager.py` itself (helpers,
  exceptions, `Session`, and the factory) and split only the class body by real
  responsibility, preserving runtime and test seams. Final mixins/files:
  `session_core.py` (drain/persistence/rehydrate/gc + the core constructor),
  `session_host_connection.py` (Session Host launch/forward/relay/reattach
  plumbing), `session_parity.py` (parity-only relay interruption and container
  replacement), `session_host_recovery.py` (authority recovery + reattach/
  respawn decisions), `session_monitoring.py` (heartbeat/disconnect/idle and
  stranded-host sweeps), `session_start.py` (fresh launch + failed-start parity
  cleanup), `session_resume.py` (resume/resync), `session_prompts.py`
  (prompt submit/queue/usage/reapable state), `session_lifecycle.py`
  (interrupt/stop/end teardown), and `session_handoff.py`
  (auto-handoff + successor seeding). The composition root now inherits those
  mixins plus the pre-existing dormancy/liveness mixins.
- The tricky part was **compatibility, not line cutting**. `agent-bridge`'s
  tests aggressively monkeypatch `agent_bridge.session_manager.AcpClient`,
  `spawn`, `_resolve_remote_ai_plugin_dirs`, `_resolve_relay_launch_env`,
  `_claim_codespace`, and `_release_codespace_claim`. A naive extraction would
  have left those monkeypatches steering dead aliases. The moved methods that
  touch those seams now resolve them back through the live
  `agent_bridge.session_manager` module, mirroring the compatibility discipline
  from the earlier `agent-bridge/__main__.py` split.
- Result: `session_manager.py` shrank **6,282 -> 771** lines in the live file,
  and every extracted sibling stayed under the 1,000-line cap.
- Validation matched the stricter post-`db.py` discipline: the full
  `python tools/run-plugin-tests.py agent-bridge` run reached the same two
  pre-existing Windows `bash.exe` path failures in
  `tests/test_bootstrap_check_reconcile_opt_in.py`
  (`test_sh_skips_spawn_without_opt_in`,
  `test_sh_attempts_spawn_with_opt_in`) while otherwise going green at
  **569 passed / 2 failed / 2 skipped**. A targeted follow-up on the moved
  runtime surfaces then passed green:
  `python tools/run-plugin-tests.py agent-bridge -k "session_manager or auto_handoff or attention_wait or acp_agent or acp_ws or startup_reattach_nonblocking or status_and_read or remote_operations or result_snapshot or stream_sim"`
  -> **396 passed / 1 skipped / 2196 deselected**. Ruff (`--select F,E9`)
  passed across all touched/created modules.
- Resulting backlog shift: `session_manager.py` disappears from the pecking
  order entirely, and `plugins/agent-bridge/src/agent_bridge/agent_registry.py`
  at **2,963** lines becomes the plugin's next dominant remaining offender.
  Repo-wide, the largest remaining production target stays
  `plugins/agent-worktrees/src/agent_worktrees/__main__.py`.

### 2026-09-21 — Phase 2 continued: `plugins/agent-bridge/src/agent_bridge/agent_registry.py` structural split
- Took the exact seam the prior `session_manager.py` journal entry queued, but
  the shape was **hybrid**, not a one-pattern repeat: `agent_registry.py` mixed
  several independent top-level responsibility bands (registry parsing, local
  auto-discovery, topology/repo-registry synthesis, credential-relay profile
  helpers, namespace CLI boundary types) with one large stateful
  `AgentResolver`. The clean split was therefore **free-function modules plus a
  class extraction**, not one more all-mixin move.
- Extracted five focused siblings, each under the cap: `agent_registry_common.py`
  (shared constants + `AgentConfig`/`NamespaceAgentInfo`/core exceptions),
  `agent_registry_namespace.py` (the `NamespaceResolver` contract plus
  `CliNamespaceResolver` / `RestrictedCliNamespaceResolver`),
  `agent_registry_topology.py` (parse/load/discover + related/repo-registry
  + topology-derived roster helpers), `agent_registry_relay.py`
  (`FileTokenValidator` / `FileTokenAuthorizer` + relay-profile application),
  and `agent_registry_resolver.py` (the full `AgentResolver` implementation).
  Left `agent_registry.py` as the compatibility/composition root: it re-exports
  the historical surface, keeps the patch-heavy helper seams
  (`_detect_local_machine`, `resolve_repo_remote`, `_agent_worktrees_bin`) at
  the original import path, and still owns `build_resolver()` /
  `daemon_resolver()` / built-in namespace registration.
- The tricky part was, again, **compatibility rather than line cutting**. The
  existing tests patch `agent_bridge.agent_registry._detect_local_machine`,
  `_agent_worktrees_bin`, `resolve_repo_remote`, and even `Path.home` / `os.name`
  on the root module to steer resolver and binstub behavior. A naive extraction
  would have left those patches hitting dead aliases. The moved call sites that
  depend on those seams now bounce back through the live
  `agent_bridge.agent_registry` module so the historical monkeypatch surface
  keeps steering the real implementation.
- Result: `agent_registry.py` shrank **2,963 -> 451** lines, fell completely out
  of the shrink-only baseline, and every new sibling module stayed well under
  the 1,000-line cap.
- Validation matched the runbook's stricter post-`db.py` discipline. The full
  `python tools/run-plugin-tests.py agent-bridge` pass still stops in sub-suite
  1 on the same two pre-existing Windows `bash.exe` path failures in
  `tests/test_bootstrap_check_reconcile_opt_in.py`
  (`test_sh_skips_spawn_without_opt_in`,
  `test_sh_attempts_spawn_with_opt_in`) and otherwise reports
  **569 passed / 2 failed / 2 skipped**. A targeted follow-up over the moved
  registry/relay surfaces passed green:
  `python tools/run-plugin-tests.py agent-bridge -k "agent_registry or relay_profile"`
  -> **172 passed / 1 skipped / 2420 deselected**. Ruff (`--select F,E9`)
  passed across all touched/created modules, `python tools/check-module-size.py`
  stayed green, and `--refresh-baseline` removed only the
  `agent_registry.py` entry from `tools/module-size-baseline.json`.
- Bumped `agent-bridge` to `0.4.0-dev542`.
- `python tools/rank-module-size.py --limit 100` disproved the tempting "plugin
  clear" claim: `agent-bridge` still has several oversized baselined modules
  (`client.py`, `routes/sessions.py`, `acp_client.py`, `routes/worktrees.py`,
  `transport.py`, `remote_operations.py`, `app.py`, `result_snapshot.py`,
  `models.py`, `session_host/spawner.py`). So this slice retires the registry
  offender, but **does not** make `agent-bridge` module-cap clean yet. The
  plugin's next dominant remaining offender is now `client.py` at 1,710 lines.

### 2026-09-22 — Phase 2 continued: `plugins/agent-dispatch/src/agent_dispatch/__main__.py` coordinator slice
- Started `agent-dispatch/__main__.py` with the safest strong seam instead of
  trying to land the whole 5,046-line registrar in one shot: the
  coordinator/service family. Extracted a new `coordinator_cli.py` (**653**
  lines) holding `serve`/`deploy`/`_cutover`/`_retire-supervisors`,
  federation `run`/`status`, and the read-only coordinator surfaces
  (`health`, `installer-readiness`, `print-endpoint`), while leaving
  `__main__.py` as the composition root plus the compatibility surface the
  existing tests and sibling modules import/monkeypatch. Net result for the
  parent module: `__main__.py` shrank **5,050 -> 4,357** lines, and the new
  sibling stays comfortably under the 1,000-line cap.
- The key design constraint was the same one this effort already learned in
  `agent-bridge/__main__.py` and `agent-worktrees/__main__.py`: **moved
  symbols were not enough; moved call sites also had to preserve the old
  monkeypatch seams.** The first cut exposed exactly that: deploy tests that
  patch `agent_dispatch.__main__.time`, `_reap_abandoned_passive`,
  `_reap_superseded_coordinators`, and `_federation_rendezvous` were no longer
  steering the real runtime path once those functions lived in a sibling
  module. Fixed by routing those specific cross-calls back through the live
  `agent_dispatch.__main__` module when a patched replacement exists, while
  keeping the default path local so the extracted module remains a normal
  runtime implementation rather than a bag of wrappers.
- Validation for the slice stayed at the full-plugin bar rather than a narrow
  smoke check. `python tools/run-plugin-tests.py agent-dispatch` passed across
  all six sub-suites after one deploy/federation compatibility iteration:
  **3,303 passed / 34 skipped total** (sub-suite counts:
  `754/5`, `418/10`, `641/14`, `663/1`, `731/0`, `96/4` passed/skipped).
  Focused follow-up during the fix loop also passed for the moved surfaces:
  `python tools/run-plugin-tests.py agent-dispatch -k "deploy_cli or lazy_start or cli or satellite_status"`
  -> **382 passed / 2955 deselected**. Required guards then passed:
  `ruff check --select F,E9 plugins/agent-dispatch`,
  `python tools/check-module-size.py`,
  `python tools/check-module-size.py --refresh-baseline`,
  `python tools/check-install-contract.py`, and
  `python tools/check-version-consistency.py`.
- Read-only dogfood stayed within the safety bar by using the plugin test venv
  only: `python -m agent_dispatch --help`, `deploy --help`,
  `federation status --help`, and `print-endpoint` all succeeded without
  starting/stopping/restarting a live coordinator. The baseline refresh lowered
  the grandfathered ceiling for `plugins/agent-dispatch/src/agent_dispatch/__main__.py`
  to the new **4,357**-line size. Version bump for this slice:
  `agent-dispatch` **`0.1.2-dev174`**.
- This is explicitly **not** the end of the `__main__.py` campaign. The file is
  still oversized, but the remaining seams are now clearer than before:
  create/spawn, task lifecycle + steering + query, the declarative loop
  families, supervise registration wiring, and the resolve/run/evaluate/charter
  family. The next slice should keep the same discipline: pick one family,
  preserve every `agent_dispatch.__main__.<name>` seam tests patch, validate
  the full plugin suite, then land.

### 2026-09-22 — Phase 2 continued: `plugins/agent-dispatch/src/agent_dispatch/__main__.py` create/spawn slice
- Took the next strong seam immediately after the coordinator pass instead of
  trying to attack the remaining 4,357-line root wholesale: the create/spawn
  family. Extracted a new `create_cli.py` (**760** lines) holding the task
  creation/proposal surface, producer-fence inspection/handoff, payload file
  reads, cross-machine create+embody dispatch, and the full spawn reservation /
  embody / bridge launch flow. `__main__.py` now stays the composition root and
  compatibility re-export surface for the moved names, and dropped again from
  **4,357 -> 3,624** lines.
- The compatibility hazard here was broader than the raw parser block. Tests
  patch `agent_dispatch.__main__._scope_repo`, `_read_payload_file`,
  `producer_capability_value`, `_spawn_route`, `_do_spawn`,
  `_release_failed_created_spawn`, and `_cmd_create`, so the extracted module
  could not simply call its own local helpers without severing that seam. The
  moved call sites now deliberately route through the live `agent_dispatch
  .__main__` module for those compatibility-sensitive edges, while the ordinary
  implementation still lives in `create_cli.py`.
- Validation stayed at the same full-plugin bar as the first slice. `python
  tools/run-plugin-tests.py agent-dispatch` again passed all six sub-suites
  green after the extraction: **3,303 passed / 34 skipped total** (sub-suite
  counts: `754/5`, `418/10`, `641/14`, `663/1`, `731/0`, `96/4`
  passed/skipped). Focused create-family follow-up coverage also passed during
  the seam-fix loop:
  `python tools/run-plugin-tests.py agent-dispatch -k "propose_queue or recipes or spawn_reservation or embody or cli"`
  -> **549 passed / 2788 deselected**, plus the narrower post-fix pass
  `-k "test_create_cli_prefers_capability_command_resolver or
  test_create_cli_reads_remote_capability_envelope or propose_queue or recipes
  or spawn_reservation or embody or cli"` -> **549 passed / 2788 deselected**.
  Required guards then passed:
  `ruff check --select F,E9 plugins/agent-dispatch`,
  `python tools/check-module-size.py`,
  `python tools/check-module-size.py --refresh-baseline`,
  `python tools/check-install-contract.py`, and
  `python tools/check-version-consistency.py`.
- Read-only dogfood for the moved surface stayed bounded to help output only:
  `python -m agent_dispatch create --help`,
  `python -m agent_dispatch propose --help`, and
  `python -m agent_dispatch producer-fence status --help` through the plugin
  test venv. The shrink-only baseline was then lowered again, from **4,357** to
  **3,624** lines for `plugins/agent-dispatch/src/agent_dispatch/__main__.py`.
  Version bump for this slice: `agent-dispatch` **`0.1.2-dev176`**.
- Remaining seams are still clean enough to continue the same pattern: the
  task lifecycle + steering + query surface, the declarative loop families,
  supervise registration wiring, and the resolve/run/evaluate/charter family.
  The file is still over cap, but it is now materially smaller and less
  entangled than when this campaign entered `agent-dispatch`.

### 2026-09-22 — Phase 2 continued: `plugins/agent-dispatch/src/agent_dispatch/__main__.py` task-query slice
- Took the next least-coupled family after create/spawn: the read/query
  surfaces. Extracted a new `task_query_cli.py` (**486** lines) holding peer
  browse, `list`, `doctor`, the board/inbox helpers and constants,
  `find`, `sweep`, `watch`, `payload`, `result`, `consume`, and `mcp`.
  `__main__.py` stayed the composition root/re-export seam and shrank again
  from **3,624 -> 3,162** lines.
- The seam discipline here was the same as the prior slices: moved call sites
  that tests or sibling modules may steer through `agent_dispatch.__main__`
  still route through the live root for compatibility-sensitive helpers
  (`_scope_repo`, `_browse_peer`, `_consume_already_spent`, `_client`, and the
  board helpers exposed on `__main__`). This kept the existing `test_cli.py`,
  `test_doctor.py`, and `test_coordinator.py` style coverage green without
  changing behavior.
- Validation again met the full plugin bar. `python tools/run-plugin-tests.py
  agent-dispatch` passed all six sub-suites green:
  **3,306 passed / 34 skipped total** (sub-suite counts:
  `756/5`, `418/10`, `641/14`, `663/1`, `732/0`, `96/4`
  passed/skipped). Focused follow-up for the moved family also passed:
  `python tools/run-plugin-tests.py agent-dispatch -k "doctor or board_cli or
  _board_group or _board_activity or _board_sort_key or _board_keep or
  _cmd_consume or payload or result or inbox or find or sweep"`
  -> **215 passed / 1 skipped / 3124 deselected**. Required guards then passed:
  `ruff check --select F,E9 plugins/agent-dispatch`,
  `python tools/check-module-size.py`,
  `python tools/check-module-size.py --refresh-baseline`,
  `python tools/check-install-contract.py`, and
  `python tools/check-version-consistency.py`.
- Read-only dogfood for the moved surface stayed to help paths only:
  `python -m agent_dispatch list --help`,
  `doctor --help`, `inbox --help`, and `payload --help` through the plugin
  test venv. The shrink-only baseline was lowered again, from **3,624** to
  **3,162** lines for `plugins/agent-dispatch/src/agent_dispatch/__main__.py`.
  Version bump for this slice: `agent-dispatch` **`0.1.2-dev177`**.
- Remaining seams are now clearer still: the task lifecycle + steering
  family, the declarative loop / registrar family, supervise registration
  wiring, and the resolve/run/evaluate/charter family. The file is not yet
  near the 1,000-line cap, but it is now substantially smaller and more
  family-structured than when this sequence started.

### 2026-09-22 — Phase 2 continued: `plugins/agent-dispatch/src/agent_dispatch/__main__.py` steering slice
- Took the smallest high-confidence seam next: the human-in-the-loop
  steering/card family. Extracted a new `steering_cli.py` (**191** lines)
  holding `card set`, `card show`, `card draft save/clear`, `steer submit`,
  and `steer take`. `__main__.py` remains the composition root/re-export seam
  and dropped again from **3,162 -> 2,945** lines.
- Compatibility risk here was mainly parser and owner-resolution routing:
  existing CLI coverage expects the moved handlers to keep using the
  `agent_dispatch.__main__` seams for `_client`, `_resolve_owner`, and
  `_owner_from_identity`, and the parser surface still needs the
  `agent_dispatch.__main__._cmd_*` attributes for any import/patch callers.
  The extracted module therefore routes those edges back through the live root
  while keeping the actual implementation body in `steering_cli.py`.
- Validation again met the full plugin bar. `python tools/run-plugin-tests.py
  agent-dispatch` passed all six sub-suites green:
  **3,306 passed / 34 skipped total** (sub-suite counts:
  `756/5`, `418/10`, `641/14`, `663/1`, `732/0`, `96/4`
  passed/skipped). Focused follow-up on the moved steering surfaces also
  passed:
  `python tools/run-plugin-tests.py agent-dispatch -k "card_steer or wake_outbox or cli"`
  -> **382 passed / 2958 deselected**. A one-off coordinator GC test flaked
  once during the first full run (`test_background_gc_auto_recovers_gone_owner`
  observed an already-requeued task), immediately passed in isolation on
  re-run, and the subsequent full suite passed clean; no code change was
  needed for that unrelated transient. Required guards then passed:
  `ruff check --select F,E9 plugins/agent-dispatch`,
  `python tools/check-module-size.py`,
  `python tools/check-module-size.py --refresh-baseline`,
  `python tools/check-install-contract.py`, and
  `python tools/check-version-consistency.py`.
- Read-only dogfood for the moved surface stayed to help output only:
  `python -m agent_dispatch card set --help`,
  `card show --help`,
  `steer submit --help`, and
  `steer take --help` through the plugin test venv. The shrink-only baseline
  was lowered again, from **3,162** to **2,945** lines for
  `plugins/agent-dispatch/src/agent_dispatch/__main__.py`.
  Version bump for this slice: `agent-dispatch` **`0.1.2-dev178`**.
- Remaining seams are now concentrated in the genuinely larger surfaces:
  task lifecycle/admin, the declarative loop / registrar family, supervise
  registration wiring, and the resolve/run/evaluate/charter family.

### 2026-09-22 — Phase 2 continued: `plugins/agent-dispatch/src/agent_dispatch/__main__.py` parser-registration slice
- Took the safest non-behavioral reduction next: the **argparse registration
  tables** for the already-extracted command families. Rather than move more
  command bodies at once, this slice only pulled the parser wiring into four
  tiny sibling modules:
  `execution_registration_cli.py` (**56** lines),
  `declaration_loops_cli.py` (**59**),
  `registrar_parser_cli.py` (**37**), and
  `supervise_registration_cli.py` (**105**). `recipes_cli.py` also gained its
  own `register_recipes_commands(...)` helper. The effect is purely
  structural/compositional: `__main__.py` still re-exports the historical
  command symbols and remains the root, but it no longer carries those long
  parser blocks inline.
- Net result for the root module: `plugins/agent-dispatch/src/agent_dispatch/__main__.py`
  shrank **2,945 -> 2,204** lines without touching the actual runtime command
  implementations. This slice deliberately avoided the higher-risk lifecycle
  bodies after a failed first draft of a larger extraction showed that the
  remaining command/helper interdependence is real enough to deserve a tighter,
  more surgical follow-up rather than another broad automated carve.
- Validation still met the full plugin bar. `python tools/run-plugin-tests.py
  agent-dispatch` passed all six sub-suites green:
  **3,311 passed / 31 skipped total** (sub-suite counts:
  `761/5`, `418/11`, `641/14`, `663/1`, `732/0`, `96/4`
  passed/skipped). Focused parser-family follow-up also passed:
  `python tools/run-plugin-tests.py agent-dispatch -k "recipes or registrar_cli
  or repository_issue_loop_cli or worker_charter or driver or hibernation or
  resolution or supervisor or deploy_cli or lazy_start or cli"`
  -> **835 passed / 5 skipped / 2506 deselected**. Required guards then
  passed: `ruff check --select F,E9 plugins/agent-dispatch`,
  `python tools/check-module-size.py`,
  `python tools/check-module-size.py --refresh-baseline`,
  `python tools/check-install-contract.py`, and
  `python tools/check-version-consistency.py`.
- Version bump for this slice: `agent-dispatch` **`0.1.2-dev179`**. The
  shrink-only baseline was lowered again, from **2,945** to **2,204** lines
  for `plugins/agent-dispatch/src/agent_dispatch/__main__.py`.
- Remaining work is now concentrated almost entirely in the still-heavy command
  implementation bands: task lifecycle/admin, the registrar helper/runtime
  surface, and the resolve/run/evaluate helpers. The file is much closer to a
  true composition root now, but still above the 1,000-line cap.

### 2026-09-22 — Phase 2 continued: `plugins/agent-dispatch/src/agent_dispatch/__main__.py` task-lifecycle body slice
- Took the largest remaining implementation band next: the task lifecycle/admin
  command bodies. Extracted a new `task_lifecycle_cli.py` (**375** lines)
  holding the actual handlers for `claim`, `worktree-status`, `show`,
  `claimant`, `yield`, `start`/`suspend`/`resume`/`release`,
  `pause`/`unpause`, `embody`, `force-stop`, `reset`, `progress`, `focus`,
  `complete`, `abandon`, and `reattach`.
- Deliberately **did not** move the shared helper seams
  (`_simple`, `_split_owner`, `_owner_from_identity`, `_resolve_owner`,
  `_hold_actor`, `_read_result`) in this pass. Earlier drafts that tried to
  move helpers and bodies together widened the blast radius because the already
  extracted create/steering/query families still call through those helpers via
  `agent_dispatch.__main__`. Leaving those helpers in place let the command
  bodies move cleanly while preserving every existing monkeypatch surface.
- Net result for the root module: `plugins/agent-dispatch/src/agent_dispatch/__main__.py`
  shrank **2,203 -> 1,843** lines. This is still above the 1,000-line cap, but
  the remaining code is now much closer to a true compatibility/composition
  root plus a narrower band of helper logic.
- Validation again met the full plugin bar. `python tools/run-plugin-tests.py
  agent-dispatch` passed all six sub-suites green:
  **3,311 passed / 31 skipped total** (sub-suite counts:
  `761/5`, `418/11`, `641/14`, `663/1`, `732/0`, `96/4`
  passed/skipped). Focused follow-up over the moved lifecycle/admin surfaces
  also passed:
  `python tools/run-plugin-tests.py agent-dispatch -k "claimant or complete or
  start or yield or focus or pause or unpause or embody or force-stop or
  reset or progress or resolution or cli"` -> **646 passed / 8 skipped /
  2692 deselected**. Required guards then passed:
  `ruff check --select F,E9 plugins/agent-dispatch`,
  `python tools/check-module-size.py`,
  `python tools/check-module-size.py --refresh-baseline`,
  `python tools/check-install-contract.py`, and
  `python tools/check-version-consistency.py`.
- Read-only dogfood for the moved surface will use the plugin test venv help
  paths (`claim --help`, `complete --help`, `abandon --help`, `focus --help`,
  `embody --help`, `force-stop --help`) in the PR validation step. Version bump
  for this slice: `agent-dispatch` **`0.1.2-dev180`**.

### 2026-09-22 — Phase 2 continued: `plugins/agent-dispatch/src/agent_dispatch/__main__.py` execution-body slice
- Took the remaining explicit command-family implementation band for
  resolve/run/evaluate/charter. Extracted a new `execution_cli.py`
  (**288** lines) holding `_run_resolution_step`, `resolve`, the
  hibernate-the-wait `run` path, evaluator application, and `charter show`.
  The command family's parser registration had already moved in the prior
  slice, so this pass was the matching body extraction.
- Compatibility stayed the same pattern as the earlier slices: the real
  command bodies moved, but call sites that tests patch through
  `agent_dispatch.__main__` still route back through the live root where
  needed (`_client`, `_resolve_owner`, `_run_resolution_step`,
  `_spawn_detached_waiter`, `_suspend_for_detached_wait`). This preserved the
  existing monkeypatch-heavy hibernation / resolution / evaluator coverage
  without changing behavior.
- Net result for the root module: `plugins/agent-dispatch/src/agent_dispatch/__main__.py`
  shrank **1,843 -> 1,576** lines. At this point the root is no longer a large
  command-body registrar; what remains is predominantly the registrar runtime
  helpers (`_declaration_summary`, `_cmd_registrar`), the dash-dash parser
  compatibility shim, and the shared helper seams retained on purpose because
  multiple extracted families still call through them.
- Validation again met the full plugin bar. `python tools/run-plugin-tests.py
  agent-dispatch` passed all six sub-suites green:
  **3,311 passed / 31 skipped total** (sub-suite counts:
  `761/5`, `418/11`, `641/14`, `663/1`, `732/0`, `96/4`
  passed/skipped). Focused follow-up over the moved execution family also
  passed:
  `python tools/run-plugin-tests.py agent-dispatch -k "recipes or driver or
  hibernation or resolution or worker_charter or evaluate or cli"`
  -> **440 passed / 5 skipped / 2901 deselected**. Required guards then
  passed: `ruff check --select F,E9 plugins/agent-dispatch`,
  `python tools/check-module-size.py`,
  `python tools/check-module-size.py --refresh-baseline`,
  `python tools/check-install-contract.py`, and
  `python tools/check-version-consistency.py`.
- Read-only dogfood for the moved surface stayed to help output only:
  `python -m agent_dispatch charter show --help`,
  `resolve --help`, `run --help`, and `evaluate --help` through the plugin
  test venv. The shrink-only baseline was lowered again, from **1,843** to
  **1,576** lines for `plugins/agent-dispatch/src/agent_dispatch/__main__.py`.
  Version bump for this slice: `agent-dispatch` **`0.1.2-dev181`**.
- Remaining work is now concentrated in the registrar/runtime helper band and
  the deliberately-retained shared utility seams. This is the first point in
  the campaign where the residual `__main__.py` surface starts to look less
  like “one more family” and more like a shared compatibility kernel.

### 2026-09-22 — Phase 2 continued: `plugins/agent-dispatch/src/agent_dispatch/__main__.py` task-lifecycle registration slice
- Took the next safest structural reduction after the execution-body pass: the
  remaining **task lifecycle/admin argparse table**. Extracted a new
  `task_lifecycle_registration_cli.py` (**370** lines) holding the parser
  registration for `claim`, `worktree-status`, `start`/`suspend`/`resume`/
  `release`, `yield`, `complete`, `abandon`, `reattach`, `pause`/`unpause`,
  `embody`, `force-stop`, `reset`, `heartbeat`, `progress`, `focus`, and
  `detach`.
- This slice stayed deliberately parser-only, matching the prior registration
  extraction pattern: the command bodies remain in `task_lifecycle_cli.py`, but
  parser callbacks resolve through the live `agent_dispatch.__main__` module via
  `_resolve_cli_module()` so monkeypatches against root exports still steer the
  real handlers. That preserved the compatibility surface without re-inlining a
  long parser block into the composition root.
- Net result for the root module:
  `plugins/agent-dispatch/src/agent_dispatch/__main__.py` shrank
  **1,576 -> 1,221** lines. At this point the file is no longer carrying any
  large command-family registration band; what remains is almost entirely the
  shared compatibility/runtime kernel: client-targeting helpers, registrar
  runtime helpers, the dash-dash parser shim, and a small amount of composition
  glue.
- Validation again met the full plugin bar. Focused follow-up over the moved
  lifecycle parser surface passed:
  `python tools/run-plugin-tests.py agent-dispatch -k "claim or worktree-status or claimant or complete or pause or unpause or focus or force-stop or reset or cli"`
  -> **526 passed / 2 skipped / 2818 deselected**. Required guards then
  passed: `ruff check --select F,E9 plugins/agent-dispatch`,
  `python tools/check-module-size.py`, and the slice then refreshed the
  shrink-only baseline from **1,576** to **1,221** lines for
  `plugins/agent-dispatch/src/agent_dispatch/__main__.py`.
- Read-only dogfood for this parser-only slice will stay to help output only
  (`claim --help`, `complete --help`, `progress --help`, `focus --help`) in the
  PR validation step. Version bump for this slice:
  `agent-dispatch` **`0.1.2-dev182`**.
- Remaining work is now tightly concentrated in the registrar/runtime helper
  band, the dash-dash parser compatibility shim, and the handful of shared
  utility seams intentionally left on `__main__` because multiple extracted
  families still route through them.

### 2026-09-22 — Phase 2 continued: `plugins/agent-dispatch/src/agent_dispatch/__main__.py` shared-helper + registrar-runtime slice
- Took the residual compatibility kernel as the final `__main__` reduction
  pass rather than stopping at "close enough." Extracted a new `shared_cli.py`
  (**115** lines) for the shared CLI helper/re-export surface
  (`_split_owner`, `_simple`, `_owner_from_identity`, `_resolve_owner`,
  `_hold_actor`, `_read_result`, and the dash-dash parser shim) plus a new
  `registrar_runtime_cli.py` (**162** lines) for the remaining registrar/runtime
  helper band (`_reject_worktree_checkout_as_repo_root`,
  `_declaration_summary`, `_cmd_registrar`).
- The compatibility rule stayed the same as every prior slice: `__main__`
  still re-exports the historical names, and the moved helpers resolve the
  live `agent_dispatch.__main__` module at call time for any edge where tests
  monkeypatch root attributes (`_client`, `_emit`, `_identity`,
  `_owner_from_identity`, `_cmd_run`, `_cmd_recipes_drive`). The only bug this
  first exposed was a test that monkeypatches `agent_dispatch.__main__.Path`;
  fixed by keeping `Path` re-exported from `__main__` even though the actual
  helper body moved.
- Net result for the root module:
  `plugins/agent-dispatch/src/agent_dispatch/__main__.py` shrank
  **1,221 -> 919** lines. That finally crosses the campaign's target line: the
  file is now an actual composition root under the 1,000-line cap rather than a
  grandfathered giant with only the worst bands shaved off.
- Validation again met the plugin-local bar. Focused follow-up over the moved
  shared-helper/registrar/runtime seams passed:
  `python tools/run-plugin-tests.py agent-dispatch -k "registrar or split_owner or owner_from_identity or complete_resolves_owner_from_identity or progress_resolves_owner_from_identity or start_resolves_owner_from_identity or yield_resolves_owner_from_identity or run or recipes or repository_issue_loop or reviewer_loop or cli"`
  -> **767 passed / 4 skipped / 2575 deselected**. Required guards then passed:
  `ruff check --select F,E9 plugins/agent-dispatch`,
  `python tools/check-module-size.py`, and the slice refreshed the shrink-only
  baseline from **1,221** to **919** lines for
  `plugins/agent-dispatch/src/agent_dispatch/__main__.py`.
- Read-only dogfood for this slice will stay to help output only (`--help`,
  `registrar discover --help`, `run --help`, `recipes drive --help`) in the PR
  validation step. Version bump for this slice:
  `agent-dispatch` **`0.1.2-dev183`**.
- This completes the `agent-dispatch/__main__.py` componentization objective:
  no oversized residual band remains in the root module. What is left there is
  the normal composition-root scaffolding (imports, parser assembly, client
  targeting/bootstrap helpers, and `main()`), not an uncaught command-family
  seam.
- Final landing path: the under-cap slice set first hit the shared red-main
  blocker from **#3276** (`agent-pull-requests` missing
  `payload-invocation.json`), then a second shared red-main blocker from
  **#3315** (repo-wide version-consistency/module-size drift after rebasing).
  Once **PR #3316** landed the shared fix on `main`, the rebased
  `agent-dispatch` branch again passed its full local validation bar and
  pushed cleanly back into the review/CI gate. The `agent-dispatch` work
  itself never required a behavioral follow-up beyond compatibility-seam fixes
  inside the extracted helpers.
