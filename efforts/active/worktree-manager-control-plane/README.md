# Worktree Manager — Out-of-Plugin Control Plane (installer · configurator · picker)

- **Slug:** `worktree-manager-control-plane`
- **Repo:** copilot-extensions (control-plane home; PR-required `main`, self-merge)
- **Branch(es):** per-phase `pr/<slug>` worktrees → landed to `main`
- **Created:** 2026-08-17
- **Status:** Active <!-- Draft | Active | Blocked | Done -->
- **Umbrella issue:** [#352](https://github.com/ThomasMichon/copilot-extensions/issues/352)
  (remaining Worktree Manager work — adoption/discovery, visual manager, presets)
- **Sub-issues:** [#355](https://github.com/ThomasMichon/copilot-extensions/issues/355)
  (prerequisite provisioning),
  [#356](https://github.com/ThomasMichon/copilot-extensions/issues/356) /
  [#357](https://github.com/ThomasMichon/copilot-extensions/issues/357)
  (configurator: adoption + per-plugin config),
  [#1478](https://github.com/ThomasMichon/copilot-extensions/issues/1478)
  (manual mux restoration for unreachable active sessions),
  [#2062](https://github.com/ThomasMichon/copilot-extensions/issues/2062)
  (relocate Mux + AHP execution mechanics out of agent-worktrees),
  [#3359](https://github.com/ThomasMichon/copilot-extensions/issues/3359)
  (vendor worktree-manager's own agent-procutil/dropin-registry/plugin-activation
  copies instead of borrowing agent-worktrees' live — fixed, PR
  [#3368](https://github.com/ThomasMichon/copilot-extensions/pull/3368)),
  [#3360](https://github.com/ThomasMichon/copilot-extensions/issues/3360)
  (retire `_engine_runtime.py`'s in-process import of agent-worktrees' own CLI-
  root modules),
  [#3390](https://github.com/ThomasMichon/copilot-extensions/issues/3390)
  (relocate terminal-profile handling — `profiles.py`/`terminal_fragment.py` —
  out of agent-worktrees, matching the Mux/AHP precedent),
  [#5210](https://github.com/ThomasMichon/copilot-extensions/issues/5210)
  ("Launch in new window" re-owns terminal-spawning mechanics Phase 3b already
  relocated, and bypasses mux-daemon registration as a result)
- **Vision:** **vision-closing** against three already-stated visions (no
  revision needed to close their delta vs. reality; the recent
  `session-hosting` split narrowed which of these visions govern the
  Mux/AHP items):
  - [`visions/installer`](../../../visions/installer/README.md) —
    §*Features*/`optional-worktree-agent-control-plane`,
    `bare-invocation-launches-configurator`, `visual-configurator`,
    `one-line-bootstrap`, `core-install-via-real-flow`, `self-updating`;
    §*Behaviors*/`out-of-plugin-delivery`,
    `control-plane-is-optional-plugins-are-self-sufficient`,
    `knows-the-plugins-without-coupling-to-them`.
  - [`visions/picker`](../../../visions/picker/README.md) —
    §*Features*/`front-door-entry`, `first-run-onboarding-entry`,
    `decision-support-before-cost`, `programmatic-parity`;
    §*Behaviors*/`render-derive-not-own`, `live-not-snapshot`,
    `graceful-capability-scaling`, `renderable-and-assertable-headless`.
  - [`visions/session-hosting`](../../../visions/session-hosting/README.md) —
    Concepts/*Session-host provider* (Mux presentation and AHP backend as
    composable axes, currently both owned by the Worktree Manager); the
    matching Non-Goal in
    [`visions/plugins/agent-worktrees`](../../../visions/plugins/agent-worktrees/README.md)
    that agent-worktrees carries no provider-specific config union.
  - Parent: [`visions/agent-fabric`](../../../visions/agent-fabric/README.md).
- **Reality docs:** [`worktree-manager/README.md`](../../../worktree-manager/README.md) ·
  [`plugins/agent-worktrees/docs/engine-picker-contract.md`](../../../plugins/agent-worktrees/docs/engine-picker-contract.md) ·
  [`plugins/agent-worktrees/docs/picker.md`](../../../plugins/agent-worktrees/docs/picker.md) ·
  [`plugins/agent-worktrees/docs/architecture.md`](../../../plugins/agent-worktrees/docs/architecture.md)

## Guiding Intent

Deliver the **Worktree Manager** — the single, standalone, **out-of-plugin** app
that (1) **bootstraps** a bare machine into a working harness, (2) **configures,
validates, updates, and repairs** it, and (3) serves as the **optional worktree-
and agent- control-plane** (picking, launching, and managing agent sessions). The
central architectural move this effort tracks is **extracting the interactive
Picker out of the `agent-worktrees` plugin** and re-homing it in the Manager,
where it belongs — while keeping the plugins **fully self-sufficient without it**.

Why out-of-plugin: a plugin is inert until a session launches, so the code that
must *guarantee* the plugins' prerequisites cannot itself be one of those inert
plugins. The Manager is the one piece that must work **before** the plugins do,
and it is fetched and run as its own payload rather than through the plugin pipe.

The end-state a user should see: running a project's bare binstub with no
arguments **hands off to the Manager's control-plane** when it is installed (the
interactive Picker); when the Manager is **absent**, the binstub shows a
trustworthy **install/onboarding trigger** rather than silently substituting an
in-plugin surface; and any `<project> <verb>` invocation continues to run
**headless** against the plugin engine, unaffected. The Picker lives in exactly
one place — the Manager — and reads worktree state **only** across the process
boundary (`agent-worktrees --json`), owning no worktree logic of its own.

## Participants

_Solo effort — one participant drives all phases; recorded here (rather than
omitted per the template's single-effort allowance) solely so an
agent-worktrees worktree can durably bind to this effort via `effort-focus
bind` and refuse to finalize before the effort reaches `Status: Done`._

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Solo operator (private control repo) | Drives every phase end-to-end: claims the next unclaimed Plan item in the Journal, dispatches implementation, lands each slice's PR, and journals the outcome. | `copilot-extensions` worktrees created from a private control repo; no standing feature branch between slices. |

## Context

**Cross-link (2026-09-28):** the companion `mux-daemon`'s own zero-downtime
update story (graceful cutover on `worktree-manager update`, generation
self-retire, so a stale daemon can never stack up after a rapid-fire
release) is tracked by a separate, dedicated effort —
[`graceful-cutover-worktrees-and-ssh`](../../2026/09/29 graceful-cutover-worktrees-and-ssh/README.md)
(covers `agent-worktrees` and `worktree-manager` adopting the same
pre-existing `zdd`/`graceful-daemon-cutover` pattern already proven by
`agent-bridge`/`agent-dispatch`/`agent-index`, plus `agent-ssh` as an
**audit candidate** — its own README documents no persistent daemon today,
so that effort's Phase 3 may conclude no adoption is needed there at all) —
not owned here, to avoid fragmenting that cross-plugin rollout across
per-plugin efforts.

The Manager already exists and is being built out in phases (see
[`worktree-manager/README.md`](../../../worktree-manager/README.md)): the
out-of-plugin skeleton, a dependency-free plugin-knowledge catalog, and
prerequisite detection + core-install driving are in place, alongside read-only
harness state views (`projects` / `repos` / `worktrees` / `plugins`).

The Picker extraction is underway on **both** sides of the process boundary:

- **Manager side.** `worktree-manager/src/worktree_manager/picker_app.py` is a
  Textual Picker that reaches worktree data **only** through
  `engine_client` → `agent-worktrees … --json`. It imports nothing from the
  plugin, takes an **injected source** so live/fixture/demo data render
  identically, and offers a headless SVG capture for golden checks. This is the
  surface the plugin's still-bundled `picker_tui` is being **retired in favour
  of**.
- **Plugin side.** The bare-invocation **seam** in the `agent-worktrees` binstub
  resolves a no-args launch to: the out-of-plugin Manager when a **usable**
  `worktree-manager` is on `PATH` (gated by a fast `--version` health probe so a
  stale/incompatible stub can never capture the seam), else the **still-bundled**
  Picker while it ships, else the **install trigger** once the bundled Picker is
  retired. The engine ↔ Picker `--json` contract is pinned so the Manager can
  degrade gracefully against an older engine.

This effort is the **single coherent home** that ties the Manager-build issues
(#352 / #355 / #356 / #357) to the Picker-extraction and seam work, and traces
all of it to the `installer` and `picker` visions. It records **delta-closure
state only** — the visions themselves state the target and are not edited to log
progress.

## Request

Build the standalone, out-of-plugin **Worktree Manager** app so the harness is
turnkey even before any plugin's own installer can run, and make it the single
home for the interactive Picker and Mux/session-multiplexer management —
extracted out of the `agent-worktrees` plugin, not duplicated alongside it.
Keep every plugin (including `agent-worktrees`) fully self-sufficient without
the Manager; a bare invocation hands off to it when present and offers a
trustworthy install/onboarding trigger when absent. Retire the bundled,
in-plugin Picker once the extracted one reaches parity — this is the
operator-visible end-state, not an indefinite dual-implementation state.

## Plan

Phases are ordered by dependency, not calendar. Checked items are already
realized in `main`; unchecked items are the remaining delta.

### Phase 0 — Out-of-plugin skeleton (Done)
- [x] Standalone `worktree-manager/` payload, delivered **outside** the plugin
      pipe; versioned install slot + `current-version` marker +
      `~/.local/bin/worktree-manager` binstub; one-line bootstrap
      (`bootstrap.{ps1,sh}`); user-level source override (`config.toml`,
      `worktree-manager source`). Closes installer §`out-of-plugin-delivery`,
      §`one-line-bootstrap`, §`self-updating` (bootstrap/self-install slice).
- [x] **Bootstrap prerequisite auto-provisioning (git-optional).** The one-liner
      no longer hard-fails on a bare machine: `uv` is auto-installed user-local
      (no admin) when missing (session `PATH` amended; restart prompted when it
      can't), and `git` is installed best-effort where a package manager exists,
      else the payload is fetched as a **GitHub codeload tarball** so the bootstrap
      never dead-ends without `git`. The same git-optional fallback is mirrored in
      `self_update` (`manager_tarball_url` + `_fetch_via_tarball`), so updates work
      git-lessly too. Closes installer §`prerequisite-provisioning`,
      §`restart-aware`, §`legible-and-consent-driven`, and completes
      §`one-line-bootstrap` (bare machine, no pre-installed harness tooling).
      Shipped in worktree-manager `0.1.0-dev13`.

### Phase 1 — Plugin-knowledge model (Done)
- [x] Dependency-free catalog of the harness plugins (what exists, what a repo can
      enable) with no coupling to the plugins themselves. Closes installer
      §`knows-the-plugins-without-coupling-to-them`.

### Phase 2 — Prerequisites & core install (Done — #355)
- [x] Detect baseline prerequisites, plan/provision the missing ones
      (restart-aware, idempotent), and **drive the harness's own** `agent-worktrees`
      core install by locating and calling its real `install.{ps1,sh}` — never
      reimplemented. `doctor` (read-only) / `setup` (dry-run by default, `--apply`).
      Closes installer §`prerequisite-provisioning`, §`core-install-via-real-flow`,
      §`idempotent-and-re-runnable`, §`restart-aware`.

### Phase 3 — Extracted Picker over the engine boundary (In progress)
- [x] `picker_app.py` Textual Picker reads live worktrees **only** via
      `engine_client` → `agent-worktrees --json`; owns no worktree state; injected
      source (live/fixture/demo); headless SVG capture. First state-view slice
      (`worktrees`) shipped.
- [x] Pin the engine ↔ Picker `--json` contract
      ([`docs/engine-picker-contract.md`](../../../plugins/agent-worktrees/docs/engine-picker-contract.md));
      client tolerates an older engine by degrading a request rather than failing.
- [x] Bring the Manager Picker to **feature parity** with the bundled Picker:
      full worktree list interaction (filter · sort · select), resume/join/create
      actions, multi-machine at-a-glance, session/PR status columns. Closes picker
      §`front-door-entry`, §`decision-support-before-cost`, §`programmatic-parity`,
      §`render-derive-not-own`, §`live-not-snapshot`. **Ordered plan, including the
      2026-09-15 divergence audit (which agent-worktrees-only Picker fixes since the
      #1244 transplant still need porting before this box is honestly checked):**
      [`phase-3-picker-parity-and-retirement.md`](phase-3-picker-parity-and-retirement.md).
- [x] Add an engine-owned **manual mux restoration** operation for a worktree
      whose bound Copilot process remains live but unreachable after its terminal
      or mux wrapper disappears. The engine must refuse an existing live mux or
      ambiguous owner, reuse the guarded reclaim path, and resume the same
      persisted session through the normal mux launcher. Expose the operation to
      both Picker implementations over the JSON process boundary; neither UI owns
      process discovery or termination policy. Tracks #1478 and closes
      agent-fabric §`recover-not-lose` plus picker §`programmatic-parity`.
- [ ] Add an optional same-machine **AHP session backend** for create and resume
      actions. agent-worktrees creates the exact managed worktree and remains the
      lifecycle authority; the backend creates or reattaches one durable hosted
      session at that path, records a typed binding, and launches any visible mux
      pane as a hard-bound client attachment. Closing the client detaches without
      ending the hosted session, unavailable or mismatched hosts fail closed, and
      finalization requires confirmed disposal or explicit transfer. Tracks #1657
      and closes agent-worktrees §`explicit session binding` plus picker
      §`explicit-launch-target`, §`render-derive-not-own`, and
      §`programmatic-parity`.

### Phase 3b — Relocate Mux + AHP execution mechanics out of agent-worktrees (Done — #2062)
- [x] **Slice 1 (AHP):** move the AHP session backend
      (`agent_worktrees/ahp_backend.py`, the `session_backend`/`is_ahp` config
      schema, and the branches it threads through `__main__.py`,
      `tracking.py`, `finalize.py`, and `config_dropins.py`) out of the
      `agent-worktrees` plugin. Per the corrected
      [`session-hosting`](../../../visions/session-hosting/README.md) vision,
      AHP is a near-term concern of the **Worktree Manager** control-plane
      app, not a config mode of agent-worktrees and not a permanent
      alternative to Mux — an AHP-hosted session may still be Mux-wrapped for
      terminal presentation. The #1657/#1998 slice shipped the right
      *behavior* in the wrong *location*; this item is the architecture
      correction, not new capability. Reviewed, ordered plan:
      [`phase-3b-ahp-relocation.md`](phase-3b-ahp-relocation.md).
      - [x] Step 1: additive generic `execution_leg`/`ExecutionLegBinding`
            read path + `derive_execution_leg()` compatibility view in
            `tracking.py`. No behavior change: nothing writes `execution_leg:`
            yet, existing `session_backend:` output stays byte-identical.
      - [x] Steps 2-4: generic fenced `execution-leg get/set/clear` CLI;
            Manager-owned AHP provider/config/dependency over the public engine
            subprocess boundary; production Picker default-off AHP controls and
            launch/resume/create cutover for exact engine-created worktrees.
      - [x] Steps 5-6: delete the legacy agent-worktrees AHP backend/config path
            and complete the remaining launcher-contract test migration.
- [x] **Slice 2 (Mux):** relocate Mux launch/reattach/remux mechanics
      (`launch-session.{sh,ps1,cmd}`, `pane-wrapper.{sh,ps1}`, `cmd_remux`)
      from `agent-worktrees` to the Worktree Manager, consistent with the same
      vision. agent-worktrees keeps mux **liveness observation**
      (`sessions.has_mux_session`, `LiveVerdict`, `verify_worktree_active`) —
      that is legitimate provider-observation ingestion per the vision, not
      launch/reattach mechanics — while the launcher scripts and the
      restore/reattach *action* move. Reuses the #1478/#1491 remux design's
      safety invariants (refuse an existing live mux or ambiguous owner) under
      the new ownership boundary. **Clean cutover, not indefinite dual-path:**
      the migrated agent-worktrees implementation is canonical (no
      reconciliation with Worktree Manager's earlier fledgling Picker-launch
      prototype); absent Worktree Manager, `cmd_launch` falls back to a small,
      new direct non-mux invocation, not a retained copy of the launcher
      scripts. Reviewed, ordered plan:
      [`phase-3b-mux-relocation.md`](phase-3b-mux-relocation.md). **Done** —
      the relocation itself finished in the earlier sub-slices, and PR
      [#3891](https://github.com/ThomasMichon/copilot-extensions/pull/3891)
      closes the last Picker-independence / legacy-compatibility follow-ons
      tracked immediately below this slice.
      - [x] Sub-slice 2a Step 1: copied `launch-session.{sh,ps1,cmd}` +
            `pane-wrapper.{sh,ps1}` verbatim (hash-verified) into
            `worktree-manager/bin/`; proved the existing `_copy_payload`
            deployment mechanism ships them with zero packaging changes.
      - [x] Sub-slice 2a Step 2, repoint + direct-fallback + Manager-Picker
            wiring: `cmd_launch` repoints to the relocated launcher with a
            direct non-mux fallback, and (the actual live-regression fix)
            Worktree Manager's own `_run_launch` now delegates ordinary
            local, non-AHP launches to that SAME relocated script instead of
            the never-wired `launcher.compose_launch()` path — validated
            against the full test suites (see journal). **Deletion of the
            old in-plugin scripts deliberately deferred** to a follow-up PR
            pending live-hardware proof.
      - [x] Sub-slice 2b: added purely-additive `mux-remux-plan`/
            `mux-pane-status` queries plus a Worktree Manager executor that
            drives them; agent-worktrees' own `cmd_remux`/`_perform_remux`/
            `remux_bare_copilot` remain untouched as its zero-provider-mode
            fallback (the bundled Picker's standalone Restore action).
      - [x] Sub-slice 2c (fixed 2026-09-14): the launcher scripts relocated
            into `worktree-manager/bin/` dot-source `session-options.ps1`
            and `psmux-path.ps1` (which itself resolves
            `psmux-passthrough.conf`) via a `$PSScriptRoot`-relative path —
            the copy in Sub-slice 2a Step 1 omitted these terminal/helper
            scripts, so Worktree Manager-launched sessions had a silently
            unconfigured psmux status bar. Copied
            `session-options.{sh,ps1}`, `apply-mux-keybinds.{sh,ps1}`,
            `psmux-passthrough.conf`, and `psmux-path.ps1` verbatim into
            `worktree-manager/bin/` alongside the launcher, with a
            regression test asserting the sibling files exist and are
            wired, and bumped `__version__` (`0.1.0-dev36` →
            `0.1.0-dev37`) so already-installed machines actually redeploy
            the corrected payload.
      - [x] **Sub-slice 3 (direction set 2026-09-14; planned 2026-09-17; Done
            2026-09-26 — #3865):**
            split the resident status-monitor's push/observe legs into
            Worktree Manager — agent-worktrees keeps sole ownership of
            accumulating/tracking session status; Worktree Manager takes a
            **companion mux daemon** that owns the worktree⇄mux mapping,
            notifies agent-worktrees when managed worktrees gain/lose live
            panes, and applies the resident monitor's rendered status back
            into mux status bars. Reviewed, ordered plan:
            [`phase-3b-substatus-monitor-relocation.md`](phase-3b-substatus-monitor-relocation.md).
            Final state: Manager-owned sessions never repopulate
            `status-monitor.d`, never fall back to resident direct
            `_monitor_mux_set()` writes, and recover monitor/daemon restarts via
            Worktree Manager live-mapping republication instead (2026-09-27
            follow-on: also republished on an independent keep-alive cadence,
            not restart-only -- see Journal and the plan doc). Unmanaged /
            zero-provider sessions keep the existing direct/status-updater
            fallback lane unchanged.
      - [x] **Sub-slice 4 (landed 2026-09-14): same-config marketplace-cell
            resolution + generic installed-binstub invocation.** Cross-cuts
            the `marketplace-scoped-installations` effort's installation-mode
            governance. Two prior gaps: `engine_client.py`'s
            `installed_engine_command()` was hardcoded to agent-worktrees
            only, and `production_picker/_engine_runtime.py`'s "temporary
            compatibility boundary" decided legacy-vs-namespaced by checking
            only whether `COPILOT_EXTENSIONS_CONTEXT` was *set*, never the
            actual shared installation-mode policy — so Worktree Manager
            could disagree with what agent-worktrees itself would decide for
            the identical policy file. Fixed by:
            - New generic `agent_plugin_runtime.py`: `legacy_plugin_root`,
              `resolve_installed_plugin_slot`/`_command` walk the same
              marker-selected immutable slot (`current-version` /
              `last-known-good` / newest `versions/*`) for **any** `agent-*`
              plugin id, not just agent-worktrees — never PATH, never a bare
              command name.
            - `marketplace_cells_enabled()` vendors
              `libs/installation-context/installation_context.py` byte-
              identical (via `tools/sync-installation-context.py`, extended
              with a `STANDALONE_PYTHON_ADOPTERS` list for non-plugin
              payloads) and calls its own `resolve_installation_mode()` for
              the global `installationMode.enabled` policy bit — the exact
              function every agent-* plugin's own bootstrap/doctor path
              calls. A namespaced root is only ever considered when this
              returns true **and** an explicit context names the exact
              plugin; absent/disabled policy always falls back to legacy,
              matching the resolver's own documented default.
            - `engine_client.installed_engine_command()` and
              `_engine_runtime._active_runtime_source()` both now resolve
              through this shared module instead of two divergent, ad-hoc
              mechanisms.
            - **Explicitly deferred, by design:** this is the vision's
              "explicit management context" path (Worktree Manager is not a
              marketplace plugin and has no cell identity), not a fourth
              `libs/peer-launch` consumer. `peer_launch.py`'s `OWNERS`/
              structural cell-root validation remains plugin-to-plugin only;
              extending it to a non-plugin caller category is a separate,
              explicitly-scoped follow-on if ever needed for a use case that
              requires peer-launch's stronger activation-generation
              revalidation-at-execution-time guarantees (which this read-only
              discovery boundary does not attempt to provide).
- [x] Update the Worktree Manager Picker to select Mux presentation and/or the
      AHP backend independently per launch/resume/create action, rather than
      assuming exactly one of them. **Verified** — the Picker already carried
      independent `No Mux` (presentation) and `AHP` (backend) toggles for the
      local Open/Resume submenu and the New Worktree options dialog; PR
      [#3891](https://github.com/ThomasMichon/copilot-extensions/pull/3891)
      adds the missing combined-toggle regressions and the specific
      `AHP+no-mux` launch-path regression; together with the existing
      `direct+mux`, `direct+no-mux`, and `AHP+mux` coverage, the four-way
      matrix is now explicit rather than inferred indirectly from separate
      tests.
- [x] Keep both mechanics fully functional through the relocation — this is a
      location and ownership change, not a behavior regression; existing
      worktrees with a recorded `session_backend` binding must keep resolving
      correctly against the relocated code. **Verified** — the legacy
      `session_backend:` → `derive_execution_leg()` compatibility view, the
      generic `execution-leg get` CLI, and the Manager-side resume path all
      continue to honor an old-shaped persisted AHP binding; PR
      [#3891](https://github.com/ThomasMichon/copilot-extensions/pull/3891)
      revalidated that contract while closing the Picker-independence follow-on.

Phase 3b is complete: the AHP backend now lives in Worktree Manager, mux
launch/monitor ownership has moved out of `agent-worktrees`, the Picker treats
backend vs. presentation as independent axes, and legacy `session_backend`
bindings still resolve correctly through the compatibility view.

### Phase 3c — Picker non-blocking I/O (Done — Steps 1-5 landed; optional progress-envelope follow-up tracked in #4274)
- [x] Make every I/O-touching Picker surface — pivot loads (built-in and
      plugin-contributed), menu opens, and action execution with progress
      reporting — consistently non-blocking, closing the gap found while
      investigating a "menus feel slow" report after
      [#2967](https://github.com/ThomasMichon/copilot-extensions/pull/2967):
      `PickerScreen.setup()`'s pivot-registry scan and single-machine data
      load run synchronously on the render thread at four call sites (initial
      mount, the `r` reload key, and two post-action rescans), unlike the
      already-async live/multi-machine loader path. A same-session attempt to
      fix this with a naive background thread introduced a cross-test data
      race (a stale scan landing after a newer `setup()` call) and was
      reverted rather than shipped unverified. Full audit, the specific
      failure mode, and an ordered slice plan (a generation/epoch-guarded
      background-task primitive, migrating `setup()` onto it, a regression
      guard against future synchronous menu-opens, and a cross-repo proposal
      for built-in-verb progress percentages):
      [`phase-3c-non-blocking-io.md`](phase-3c-non-blocking-io.md). **Step 1
      landed in PR [#3903](https://github.com/ThomasMichon/copilot-extensions/pull/3903)**:
      additive-only `_setup_epoch` / `_start_setup_reload_worker()` seam,
      extracted `_collect_setup_payload()` / `_apply_setup_payload()`, reusable
      `wait_for_current_setup_epoch_applied` test helper, and isolated contract
      tests; the four production call sites still use synchronous `setup()`
      exactly as before. **Step 2 landed in PR
      [#4007](https://github.com/ThomasMichon/copilot-extensions/pull/4007)**:
      the initial non-live mount now paints `_setup_skeleton()` first and
      launches `_start_setup_reload_worker()` off-thread from `on_mount`,
      preserving the live path while leaving the other three synchronous
      `setup()` callers untouched for Step 3; the new blocking-gate mount
      coverage and updated capture/TUI readiness helpers keep the async mount
      contract deterministic across the suite. **Step 3 landed in PR
      [#4144](https://github.com/ThomasMichon/copilot-extensions/pull/4144)**:
      the `r` reload handler and both post-action rescans now launch the same
      epoch-guarded setup worker instead of calling synchronous `setup()`,
      and new Step 3 race coverage proves rapid repeated reloads plus both
      config-section/worktree-action rescan vs manual reload orderings always
      resolve to the newer epoch. **Step 4 landed in PR
      [#4164](https://github.com/ThomasMichon/copilot-extensions/pull/4164)**:
      explicit UI-thread boundary tests now fail fast if mount, reload, or
      action-completion rescan code paths regress to blocking setup I/O, while
      the existing Actions-menu and steer-submit offload coverage is
      documented as part of the same guardrail. **Step 5 landed in PR
      [#4278](https://github.com/ThomasMichon/copilot-extensions/pull/4278)**:
      the lingering synchronous helper was renamed to the test-only
      `setup_sync_for_tests()`, direct unit-test callers were updated, the
      setup/reload docs were reconciled to the final worker-owned production
      path, and the separate built-in progress-envelope follow-up was filed as
      [#4274](https://github.com/ThomasMichon/copilot-extensions/issues/4274).

### Phase 3d — Retire the Picker's in-process engine-module boundary (Done — #3360)

_(agent-recommended scoping below the two linked issues; the issues
themselves are operator-filed.)_

`production_picker/_engine_runtime.py` — its own docstring calls it a
"temporary compatibility boundary" — is the last major violation of the
picker vision's process-boundary-only Non-Goal. Historically this boundary
covered 9 `agent_worktrees.*` submodules; after Phase 3e retired the
`profiles` proxy plus PRs [#4317](https://github.com/ThomasMichon/copilot-extensions/pull/4317),
[#4322](https://github.com/ThomasMichon/copilot-extensions/pull/4322),
[#4323](https://github.com/ThomasMichon/copilot-extensions/pull/4323), and
[#4324](https://github.com/ThomasMichon/copilot-extensions/pull/4324) cut
the direct Group A/B runner/pivot/update call sites over to subprocess or
Manager-owned seams, 9 engine modules still remain live here (`activity`,
`config`, `gc`, `pr_ops`, `reap_cli`, `reclaim`, `sessions`,
`status_monitor_runtime`, `tracking`) — including 5 legacy proxy/shim modules
and 4 explicit housekeeping-owned imports — still
imported **in-process** via whole-module `__getattr__` proxies or inline
`engine_module(name)` calls, sharing agent-worktrees' own venv/sys.path
instead of going through the `--json` engine boundary every other Picker
read path uses. This directly caused two live production bugs already fixed
this session (#3319, #3327). #3359 is the mechanical, low-risk half
(vendor the 3 borrowed shared libs, `agent-procutil`/`dropin-registry`/
`plugin-activation`) — **done**, PR
[#3368](https://github.com/ThomasMichon/copilot-extensions/pull/3368).
#3360 is the harder half (the CLI-root modules themselves) and now has a
reviewed ordered plan, not just an evidence dump — see
[`phase-3d-engine-runtime-retirement.md`](phase-3d-engine-runtime-retirement.md)
for the full call-site inventory, the operator-resolved ownership decisions,
and the PR-by-PR sequencing behind each checkbox below. Group D's `profiles`
branch is already closed via Phase 3e / PR
[#3626](https://github.com/ThomasMichon/copilot-extensions/pull/3626), and
Phase 3c's non-blocking worker prerequisite for Group C is now satisfied via
PR [#4278](https://github.com/ThomasMichon/copilot-extensions/pull/4278).

- [x] **#3359 — vendor the 3 borrowed shared libs.** Landed via
      [#3368](https://github.com/ThomasMichon/copilot-extensions/pull/3368):
      `worktree-manager/libs/{agent-procutil,dropin-registry,plugin-activation}`
      + declared `pyproject.toml` dependencies + `_engine_runtime.py`'s
      sys.path injection narrowed to the libs still needed for the CLI-root
      boundary itself (`plugin-resolve`, `config-migrate`,
      `single-instance-lease`).
- [x] **`profiles` moves to worktree-manager as a full relocation, not a
      vendored copy — see Phase 3e (#3390).** Step 1 landed: the model
      (`TargetSel` + load/save/default-selection) relocated verbatim to
      `worktree_manager.terminal_profiles`;
      `profiles_io.py`/`engine_profiles_view.py` import it directly, and
      `production_picker/profiles.py`'s proxy shim is deleted. Originally
      scoped here as a #3359-style vendored-lib fix; operator direction
      revised this to a full ownership move (terminal handling of every kind
      is leaving agent-worktrees, matching the Mux/AHP precedent) — the
      remaining steps (registry-read boundary, fragment-generation core,
      CLI surface, `install.ps1` repoint, clean cutover) continue under
      Phase 3e since they also touch agent-worktrees' own
      `profiles`/`terminal-fragment`/`repair` CLI verbs, not just this
      Picker's read path.
- [x] **Write the reviewed ordered plan and resolve Open Questions 1-3.**
      Landed in
      [`phase-3d-engine-runtime-retirement.md`](phase-3d-engine-runtime-retirement.md):
      Group B is now split by ownership (Picker lifecycle sweeps move into
      worktree-manager; project/config/ssh resolution stays engine-owned behind
      a new public CLI seam), Group C's batch verb is explicitly engine-owned,
      and Group C's former Phase 3c prerequisite is recorded as satisfied by
      PR [#4278](https://github.com/ThomasMichon/copilot-extensions/pull/4278).
- [x] **Step 1 — pin and cut over Group A's low-frequency public read
      surface.** Landed in PR
      [#4317](https://github.com/ThomasMichon/copilot-extensions/pull/4317):
      added/pinned `picker-paths --json`, reused `state-root --json`, extended
      `stage-update` with `--indicator-state --json`, documented the contract in
      `engine-picker-contract.md`, and moved `pivot_manifest.py` /
      `update_stage.py` off the in-process engine boundary onto the same
      subprocess client pattern the rest of the Picker already uses.
- [x] **Step 2 — add Group B's narrow public CLI seam for project/config/ssh
      decisions.** Landed in PR
      [#4322](https://github.com/ThomasMichon/copilot-extensions/pull/4322):
      added/pinned `picker-bootstrap --json` and
      `repair-stale-anchor --json`, documented them in
      `engine-picker-contract.md`, confirmed the existing `resolve --json`
      remote-launch payload already covered Group B's machine/environment
      needs without a shape change, and introduced a Manager-owned
      `ProjectBootstrap` binding record plus `engine_group_b.py` for the
      later cutover. Additive only: `runner.py` still uses the old
      compatibility path until Step 4. See
      [`phase-3d-engine-runtime-retirement.md`](phase-3d-engine-runtime-retirement.md).
- [x] **Step 3 — reimplement Group B's Picker-owned lifecycle sweeps directly
      in worktree-manager, additive first.** Landed in PR
      [#4323](https://github.com/ThomasMichon/copilot-extensions/pull/4323):
      added `worktree_manager.production_picker.housekeeping` and
      `monitor_roots` as the Manager-owned home for the Picker's lifecycle
      sweeps / monitor-root glue, recorded the Step 4 coordination boundary
      explicitly (Manager-owned mux-session names from `mux-mapping.json`,
      Manager-owned worktree ids from that registry plus `execution_leg.provider
      == ahp`, Manager-owned launcher shells from the relocated
      `worktree-manager/bin/launch-session.*` / `pane-wrapper.*` path), and
      proved parity with the current engine behavior through new Worktree
      Manager tests. Kept additive-only per plan: `runner.py` still uses the
      compatibility path and agent-worktrees' live sweeper behavior is
      unchanged until Step 4. See
      [`phase-3d-engine-runtime-retirement.md`](phase-3d-engine-runtime-retirement.md).
- [x] **Step 4 — cut `runner.py`, `pivot_manifest.py`, and `update_stage.py`
      over to the new seams.** Landed in PR
      [#4324](https://github.com/ThomasMichon/copilot-extensions/pull/4324):
      `runner.py` now consumes the Step 2 bootstrap/repair verbs and the Step 3
      Manager-owned housekeeping/monitor modules, while
      `worktree_manager.__main__` retires the old private remote-plan fallback
      in favor of an explicit "engine too old" failure. The direct Group A/B
      `runner.py` / `pivot_manifest.py` / `update_stage.py` call sites no
      longer use `engine_module(...)` or underscore-prefixed engine helpers; the
      remaining live `_engine_runtime.py` surface is the Step 3
      housekeeping-owned engine imports plus Group C. Only Group C remains
      before `_engine_runtime.py` can be deleted. See
      [`phase-3d-engine-runtime-retirement.md`](phase-3d-engine-runtime-retirement.md).
- [x] **Step 5 — add Group C's batched reconcile-and-stamp `--json` verb in
      agent-worktrees, unused at first.** Landed in PR
      [#4327](https://github.com/ThomasMichon/copilot-extensions/pull/4327):
      added the engine-owned `picker-reconcile-local --json` batch (optional
      repeated `--worktree-id`, otherwise "all current-platform local records")
      plus the matching unused-at-first Manager wrapper
      `production_picker.engine_group_c`, while keeping the existing best-effort
      lock scope exactly as-is (no new batch-wide tracking lock; only the
      helpers' short-lived stamp windows). The response reuses the Picker's
      existing Group C list-row field names for the returned `rows` subset and
      surfaces batch counters in `summary`, so Step 6 can consume it without a
      second vocabulary. `data_local.py` is intentionally unchanged here; only
      the additive seam landed. See
      [`phase-3d-engine-runtime-retirement.md`](phase-3d-engine-runtime-retirement.md).
- [x] **Step 6 — cut `data_local.py` over to the batched Group C verb via the
      now-landed Phase 3c worker path.** Landed in PR
      [#4350](https://github.com/ThomasMichon/copilot-extensions/pull/4350):
      the production
      Picker's local classify load and per-row Refresh now call
      `production_picker.engine_group_c.picker_reconcile_local(...)` once per
      refresh epoch (or once per targeted row refresh) and merge the returned
      `rows[{id,pr,prs,pr_count,session_bound_live,session_lock_live,session_lock_stale,stale_lock_pids,mux_session,mux_clients,mux_attached}]`
      payload directly onto Picker rows instead of importing
      `tracking`/`pr_ops`/`reclaim`/`sessions` in-process. The old
      `_start_pr_reconcile()` / `_start_bound_live_reconcile()` background
      hooks are gone; the authoritative classify load already carries the Group
      C reconcile result, so one setup/reload epoch now yields one batched
      reconcile call instead of two competing post-load threads. The remaining
      `production_picker.config` proxy consumers named in the plan were drained
      by replacing `production_picker.config` itself with a Manager-owned
      direct-file reader/cache layer (`data_local.py`, `data_ssh.py`,
      `engine_loading.py`, `profiles_io.py`, `roster.py`,
      `picker_tui/__init__.py`), and the Actions menu's last authoritative
      liveness probe in `engine_worktree_actions.py` now reuses the targeted
      Group C engine call instead of `sessions.verify_worktree_active()` +
      `tracking.stamp_mux_live()`. Validation on the final tree: the full
      `worktree-manager/tests/production_picker/` suite passed twice back-to-
      back at `700 passed, 2 skipped`; the full `worktree-manager` suite
      (excluding the two standing hangs) matched the rebased machine baseline
      at `1439 passed, 6 skipped, 11 failed`; the full `agent-worktrees` suite stayed in
      the established unrelated-failure envelope (final counts recorded in the
      Journal entry below); `ruff check --select F,E9` passed for both packages;
      `check-install-contract.py` and `check-version-consistency.py` passed; and
      `check-version-bump.py` still reports the same unrelated pre-existing
      drift on `delegation-guidance`, `efforts`, `harness-knowledge`, and
      `wsl-setup`. Cache-first first paint remains intact (`classify=False` /
      bootstrap rows still skip the Group C batch), and the new regression
      coverage proves the hot path stayed O(1) in subprocesses rather than one
      subprocess per worktree/helper. See
      [`phase-3d-engine-runtime-retirement.md`](phase-3d-engine-runtime-retirement.md).
- [x] **Step 7 — retire `_engine_runtime.py` and its remaining proxy shims.**
      Landed in PR
      [#4357](https://github.com/ThomasMichon/copilot-extensions/pull/4357):
      moved the last non-Picker compatibility bootstrap to
      `worktree_manager.agent_worktrees_runtime`, deleted
      `production_picker/_engine_runtime.py` plus the dead
      `config`/`pr_ops`/`reclaim`/`sessions`/`tracking` proxy modules, and
      added a focused source-level regression guard proving
      `worktree_manager.production_picker` no longer carries a direct
      `agent_worktrees` import. With Step 7 landed, Groups A/B/C and the full
      Phase 3d plan are complete. See
      [`phase-3d-engine-runtime-retirement.md`](phase-3d-engine-runtime-retirement.md).
      Step 8 below resolves the 2026-09-27 re-audit gap by draining
      `housekeeping.py` off `agent_worktrees_runtime.engine_module(...)`
      too, so the final boundary guard now matches the phase's actual
      substance rather than only its literal-import wording.
      **Cross-linked 2026-09-27** (`agent-worktrees-authoritative-daemon`
      effort, Phase 4): Step 5's batched verb should register against that
      effort's `tracking_write.py` verb registry
      (`register_verb`/`dispatch`/`write_with_boot`, plus capability-aware
      endpoint selection and the `AmbiguousWriteOutcome` contract) rather
      than inventing a second wire shape. That effort's own Phase 4 also
      documents this call site's CURRENT direct in-process
      `tracking.stamp_bound_live`/`stamp_mux_live`/`stamp_session_state`
      usage (`data_local.py`, via `_engine_runtime.py`) as a KNOWN,
      deliberately-out-of-scope exception to its sibling-plugin write guard
      (`tools/check-no-sibling-tracking-writes.py`) -- Step 6/7 above
      should REMOVE this direct call site entirely once the batched verb
      lands, not add it to that guard's protected surface; that guard also
      deliberately scans only `plugins/*` (`worktree-manager/` sits
      outside it entirely). Any future decision to widen the guard's scope
      to cover `worktree-manager/` too is separate from this migration and
      would need its own coordination.
- [x] **Step 8 — resolve the flagged `housekeeping.py` residual gap.**
      Completed in this branch: the startup/exit housekeeping lane now uses
      public engine verbs or Manager-owned direct file/lock reads for all 7
      previously-indirected module usages. `config.tracking_dir()` moved to the
      existing Manager-owned `project_config` reader; the mux orphan reap moved
      to `reap-sessions --json` extended with repeatable `--worktree-id` and
      `--include-manager-owned`; launcher-shell and finished/managed-worktree
      sweeps now call `reap-shells --json --yes`, `sweep-managed --json`, and
      `sweep-finished-sessions --json`; the finished-session grace constant is
      local; and Picker monitor-root upkeep now checks the resident
      status-monitor lock locally, spawning the public `agent-worktrees
      status-monitor` command only when absent instead of importing
      `status_monitor_runtime` in-process on every heartbeat. No vision carve-
      out was needed because no deliberate exception remains.

### Phase 3e — Relocate terminal-profile handling out of agent-worktrees (Done — #3390)

Operator direction (2026-09-23), while scoping Phase 3d's `profiles`
disposition: terminal handling of *every* kind — not just Mux presentation
and the AHP backend (already relocating per Phase 3b / #2062) — is leaving
`agent-worktrees` for the Worktree Manager control-plane, in phases, the
same eventual direction as `agent-worktrees update` becoming
`worktree-manager update`. Vision updated in
[`visions/plugins/agent-worktrees`](../../../visions/plugins/agent-worktrees/README.md#non-goals--boundaries)
(new Non-Goal) and
[`visions/installer`](../../../visions/installer/README.md#optional-worktree-agent-control-plane)
(feature text) — see each vision's 2026-09-23 Provenance entry.

**Scope:** `agent_worktrees.profiles` (the `TargetSel` terminal-profile
*selection* model + its `~/.<project>/config.yaml` persistence) and
`agent_worktrees.terminal_fragment` (the real Windows Terminal / Tabby
fragment generator that mirrors the selection) — plus the CLI surface built
on them (`agent-worktrees profiles` / `terminal-fragment` / the
terminal-mirroring parts of `repair`, all in `picker_profiles_cli.py`) and
agent-worktrees' own bundled legacy Picker's `picker_support/data_local.py`
call site.

- [x] Write the reviewed, ordered migration plan (matching
      `phase-3b-ahp-relocation.md`'s shape): full current-state inventory
      with exact file/line evidence, target ownership shape, back-compat for
      existing on-disk `terminal_profiles:` records, and independently-
      landable steps.
      [`phase-3e-terminal-profile-relocation.md`](phase-3e-terminal-profile-relocation.md).
      **Finding: the boundary is bigger than `profiles.py` alone** —
      `terminal_fragment.py` (1016 lines) structurally couples to
      agent-worktrees' own project/repo registry
      (`collect_local_projects` imports `config`/`installer`/`repos`), and
      `install.ps1` carries its own PowerShell terminal-integration
      functions calling into the Python CLI. `profiles.py` itself (235
      lines) has no such coupling and is a clean, mechanical move.
- [x] **Step 1 — relocate `profiles.py` verbatim into worktree-manager**
      (an owned module, not a vendored copy); update worktree-manager's
      `profiles_io.py`/`engine_profiles_view.py` to import it directly,
      closing out Phase 3d's `profiles` checkbox. agent-worktrees keeps its
      own copy + CLI verbs working during this step (two copies briefly,
      matching Phase 3b's own transitional shape). Landed as
      `worktree_manager.terminal_profiles`; full production_picker suite
      green (658 passed, same 3 pre-existing unrelated failures as #3359).
- [x] **Step 2 — resolve the registry-read boundary (operator direction:
      direct file read).** Extended `harness_state.py` — worktree-manager's
      existing dependency-free `repos.yaml`/`projects.yaml`/per-project
      `config.yaml` reader — with `SshEnvironment`/`RosterMachine` +
      `project_roster()` (a verbatim port of
      `terminal_fragment._load_roster`) and `ProjectInfo.wsl_distro`/
      `wsl_state`/`roster` fields, giving Step 3 everything
      `collect_local_projects` reads today.
- [x] **Step 3 — relocate the GUID/state-diagnosis/reconciliation core** of
      `terminal_fragment.py` into worktree-manager (`build_fragment`,
      `stable_guid`, `reconcile_generated_profiles`, `diagnose_wt_state`,
      `migrate_selection_to_keys`), reusing `harness_state`'s
      `RosterMachine`/`SshEnvironment` and `terminal_profiles`'s selection
      model. 35 of 37 original tests ported verbatim; **Step 3b** (new,
      split out) covers rewiring `collect_local_projects` itself onto
      `harness_state.build_projects()` — deferred pending a small
      `anchor`-override/`display_name` decision.
- [x] **Step 3b — rewire `collect_local_projects`** onto
      `harness_state.build_projects()`. Found the projects.yaml `anchor`
      override is real (not dead code) — many agent-worktrees tests exercise
      a repos.yaml-absent, anchor-only project — so extended
      `ProjectInfo` with `anchor`/`display_name` fields (`repo.path or
      entry.get("anchor")`) rather than dropping the fallback.
- [x] **Step 4 — give worktree-manager an equivalent CLI/config surface**
      for `profiles get/apply` and
      `terminal-fragment [--explain|--doctor|--migrate-selections]`. Takes
      an explicit `<project>` positional (this CLI's own convention) instead
      of agent-worktrees' cwd-based default; `apply` persists but doesn't
      yet mirror to disk (`mirrored: false` — Step 5's scope).
- [x] **Step 5a — build the real deploy/mirror mechanism** in
      worktree-manager: `terminal_fragment.deploy_fragment()` computes the
      fragment write + `generatedProfiles`/`settings.json` reconciliation
      unconditionally; only `apply=True` writes anything. Wired as
      `terminal-fragment --deploy [--live]` / `profiles apply --mirror
      [--live]`, both defaulting to dry-run per operator direction (no safe
      CI test path for live Windows Terminal state).
  - [x] **Step 5b — repointed `install.ps1`'s** `Deploy-TerminalScripts`/
        `Sync-TerminalState`/`Get-SettingsProfileGuids`/
        `Clean-TerminalSettingsJson` at the new owner (PR #3457), following
        Phase 3b Slice 2a's launcher-script repoint pattern: health-checked,
        version-gated (`>= 0.1.0-dev75`) present-or-fallback to the
        unchanged local implementation.
  - [x] **Step 5c — live-machine validation trial**, operator-supervised on
        a live machine: confirmed the installed `install.ps1` actually ran
        the new Worktree Manager path (`--live: writes applied`) and left
        the real fragment/`state.json`/`settings.json` byte-identical to
        before (idempotent, non-destructive). `--live` is proven safe on
        this machine; still off by default everywhere else until
        independently exercised.
- [x] **Step 6 — clean, decisive cutover** (Mux/AHP precedent): delete
      agent-worktrees' `profiles`/`terminal-fragment` CLI verbs, the
      terminal-mirroring parts of `repair`, and (pending the bundled-Picker
      disposition question) `picker_support/data_local.py`'s/
      `profiles_io.py`'s Profiles-grid path — the actual deletion commit,
      kept last and separate for revertability. **Landed** — PR
      [#3626](https://github.com/ThomasMichon/copilot-extensions/pull/3626),
      including follow-up fixes from Copilot's own PR review: repointed
      `install.ps1`'s `Deploy-TerminalFragmentLocally` fallback (no more
      shell-out to the retired `terminal-fragment` verb), repointed the
      bundled Picker's local-Apply mirror path and remote SSH dispatch onto
      Worktree Manager's own `terminal_fragment.deploy_fragment`/CLI
      directly, and fixed a real pre-existing bug in
      `terminal_fragment.deploy_fragment()` (it reported `plan.applied =
      True` unconditionally whenever `apply=True`, even when nothing was
      actually written) with a new regression test.
- [x] **Step 7 — close out Phase 3d's `profiles` checkbox** once
      worktree-manager's Picker call sites import the relocated module
      directly. **Verified** — Step 1's direct-import cutover already
      landed in PR #3398, and Step 6 removes the last agent-worktrees-side
      compatibility verbs/modules that would have kept the old surface
      alive.

Phase 3e is complete: every scope item (`agent_worktrees.profiles`,
`agent_worktrees.terminal_fragment`, their CLI surface, and the bundled
legacy Picker's terminal-mirroring call site) now lives solely in
worktree-manager.

### Phase 4 — Bare-invocation seam & handoff (Plugin side landed; end-state pending)
- [x] Plugin binstub seam resolves a no-args launch to a **usable** Manager on
      `PATH` (health-probed), else the still-bundled Picker, else the install
      trigger; a stale/incompatible `worktree-manager` stub can never capture the
      seam. Realizes installer §`bare-invocation-launches-configurator`,
      §`control-plane-is-optional-plugins-are-self-sufficient`.
- [x] Onboarding polish for the **absent-Manager** path: the install trigger reads
      as a **guided first-run onboarding**, not an error, and points at the
      trustworthy bootstrap. Landed via [#4838](https://github.com/ThomasMichon/copilot-extensions/pull/4838):
      the scope stayed intentionally **narrow** to the plugin-side
      `cmd_manager_install_trigger` copy/tests. The richer in-Picker
      setup-first onboarding home remains the separately tracked
      Worktree-Manager-side work in #542, with #540/#541 as install-side
      prerequisites and #357 as the broader configurator track. This lands the
      **absent-Manager seam's slice** of picker
      §`first-run-onboarding-entry` and installer
      §`onboards-from-empty-gracefully`; it does **not** claim the broader
      Manager-side setup-first/home experience is done here.
- [x] **Generic control-plane-provider registration contract.** Landed via
      [`phase-4-control-plane-provider-registration.md`](phase-4-control-plane-provider-registration.md)
      (design) plus the implementation PR: the bare-invocation seam no longer
      health-probes a literal `worktree-manager` binstub on `PATH`. It now
      discovers providers through a consumer-owned manifest registry at
      `~/.agent-worktrees/control-plane-providers.d/<provider>.json`
      (test/operator override:
      `AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR`), each manifest declaring
      a stable `provider` identity, an absolute `command` argv (no `PATH`
      lookup), and a `minimum_version` compatibility floor. Selection is
      explicit-one-active-provider: `AGENT_WORKTREES_CONTROL_PLANE_PROVIDER`
      names one, else exactly one valid manifest is used, else the seam fails
      closed to the existing bundled-Picker/install-trigger fallback --
      deliberately not a speculative multi-provider marketplace.
      `worktree-manager` is the **reference implementation**: its own
      `self_install.py` writes the manifest on install/update, so it is
      discovered through the same generic path a third-party provider would
      use, not a separate hardcoded special case living alongside it. All
      existing diagnostics (broken/incompatible/older-provider rejection,
      falling back to the bundled Picker) are preserved verbatim, just
      parameterized over the selected manifest instead of a literal binstub
      name. The separate `_usable_worktree_manager_launcher_dir()` probe
      (locating the relocated Mux/AHP launcher scripts, a different Phase 3b
      concern) is explicitly out of scope and unchanged. Proven generic with
      a synthetic, differently-named registered provider test, not just the
      shipped Manager. Validation: `plugins/agent-worktrees/tests/test_cli_routing.py`
      full suite (122 passed); `worktree-manager/tests/test_self_install.py`
      full suite (23 passed, 2 skipped -- pre-existing Windows
      symlink-privilege gaps); full `worktree-manager` suite (1400 passed, 9
      failed -- all pre-existing Windows symlink-privilege/unrelated-timing
      gaps, same established baseline); full `agent-worktrees` suite (6348
      passed, 52 skipped, 1 failed -- the one failure is an unrelated
      `test_status_monitor_cutover_helper.py` file-rename `Access is denied`
      race against this machine's own live background status-monitor/mux-
      daemon processes, not touched by this change). `ruff`, install-contract,
      version-bump, version-consistency, and changefile-presence all pass.
- [ ] **Clarifying note (not a gap):** worktree creation does **not** need a
      callback *from* agent-worktrees *into* Worktree Manager to set up Mux.
      The interactive path already inverts that: Worktree Manager itself drives
      creation through agent-worktrees' `--json` engine boundary and then owns
      launch (Phase 3b), so it already knows when a worktree it just created
      needs a Mux session -- there is no async notification gap to design there.
      The *programmatic* path (`agent-worktrees create`, docs/mux.md's
      automated/scripted case) is deliberately non-mux by design (a script
      edits in its own process), so it correctly never needs one either.

### Phase 5 — Configurator: adoption, discovery, per-plugin config (Planned — #356 / #357)
- [ ] First-harness-repo adoption + repo discovery/registration; edit config the
      harness already reads (link a knowledge repo, per-plugin config, machine &
      connectivity). Closes installer §`first-harness-repo-adoption`,
      §`repo-discovery-and-registration`, §`machine-and-connectivity-config`,
      §`repo-plugin-enablement`.
- [ ] Visual configurator surface (beyond today's read-only state views). Closes
      installer §`visual-configurator`.

### Phase 6 — Retire the bundled Picker (Done — the operator-visible end-state)
- [x] Once the Manager Picker reaches parity (Phase 3), remove the in-plugin
      Textual `picker_tui`. The seam's fallback then flips **automatically**
      (detected by the absence of the `picker_tui` package): with no Manager
      installed, a bare launch surfaces the **install trigger** instead of any
      in-plugin Picker. This is the behavior a user currently expects but does not
      yet get, because the bundled Picker is deliberately retained until parity.
      Deletion + validation steps:
      [`phase-3-picker-parity-and-retirement.md`](phase-3-picker-parity-and-retirement.md)
      § Step 2/3. Closes
      [#117](https://github.com/ThomasMichon/copilot-extensions/issues/117) (the
      smaller opt-out-toggle cleanup this supersedes).

### Phase 7 — Health, updating & presets (Ongoing)
- [x] **Stranded cutover passive blocking a bare `self-install`.** A passive
      mux-daemon left behind by a crashed/interrupted `self_update()`
      (`spawn_passive` pins its `cwd` inside the version slot being cut
      over to) could collide with a later `self-install --apply` targeting
      the same slot, raising a Windows `PermissionError`. `self_install()`
      now reaps the stranded passive via the existing breadcrumb-driven
      recovery, holding the shared cutover lease across the whole
      reap-plus-slot-mutation and re-checking install need under that
      lease (a concurrent install can complete while waiting for it).
      Closes [#4999](https://github.com/ThomasMichon/copilot-extensions/issues/4999)
      via [#5000](https://github.com/ThomasMichon/copilot-extensions/pull/5000).
- [x] **PID-reuse-safe mux-daemon termination (mux-daemon call site).**
      `_terminate_mux_daemon_pid` identifies its target by command-line/root
      match, then signals by PID — a reused PID between the check and the
      signal could kill an unrelated process. Landed via
      [#5060](https://github.com/ThomasMichon/copilot-extensions/pull/5060):
      routes through `zdd.diagnostics.terminate_pid_if_identity`, with the
      identity token (`process_start_time`) captured **freshly, immediately
      before the kill** — not a breadcrumb schema change as originally
      scoped below, but a simpler, equally-safe design: re-derive liveness
      and cmdline/root identity live at the kill moment (an independent,
      cheap recheck this consumer already has via `_iter_mux_daemon_pids`),
      rather than trusting a value recorded once at `spawn_passive` time.
      Its own docstring states this closes
      [#5006](https://github.com/ThomasMichon/copilot-extensions/issues/5006)
      **for this call site**. The originally-scoped breadcrumb schema change
      (`new_pid_start_time` threaded through `zdd.cutover.CutoverOrchestrator`,
      a shared-library change touching every `zdd` consumer) turned out to be
      unnecessary for this consumer and is not planned unless a genuine
      second consumer surfaces that cannot cheaply re-verify liveness/identity
      at its own kill site the way this one does — a broader per-consumer
      audit (2026-10-05) found **at least one real additional case**:
      `plugins/agent-bridge/src/agent_bridge/service_process_cli.py`'s
      `_kill_pid` (bare `taskkill`/`os.kill`, no identity-bound termination)
      called after a separate `_pid_is_agent_bridge` identity check — the
      same check-then-bare-kill window. The audit was still not exhaustive
      across every plugin.
- [x] **PID-reuse-safe termination (agent-bridge `_kill_pid` site).** Fixed
      the remaining site the 2026-10-05 audit above found:
      `_kill_pid` now routes through
      `zdd.diagnostics.terminate_pid_if_identity` with a
      `process_start_time` token captured immediately before the kill,
      mirroring `_terminate_mux_daemon_pid`'s design exactly — identity
      *mismatch* (pid reuse) skips the kill entirely; an unavailable
      identity primitive (e.g. non-Linux POSIX with no `pidfd_open`) falls
      back to the prior unconditional signal/`taskkill`, preserving this
      function's existing guarantee on those platforms rather than
      silently losing kill capability there. `_kill_pid` has several
      callers (`_service_stop`'s victim loop, `_force_kill_agent_bridge_tree`'s
      Windows path); fixing it in the one shared function covers all of
      them, not just the specific `_service_stop` call site the audit
      named. Closes [#5006](https://github.com/ThomasMichon/copilot-extensions/issues/5006)
      fully (no further known sites).
- **Background daemon rotation.** Resident per-version mux-daemons
      accumulate indefinitely: `activate_after_update()`'s cutover is only
      attempted opportunistically (at whichever session's `self_update()`
      call happens to run next) and a daemon with even one long-lived
      client can block its own retirement forever with no retry. Proposed
      5-phase plan (observability → retire-idle-daemon → client-side
      re-resolution at idle boundaries → periodic sweep → validation),
      modeled on `agent-bridge`/`agent-dispatch`'s own drain/cutover
      conduct and `agent-worktrees`' existing cooldown-throttled resident
      reaper. Tracked as
      [#5001](https://github.com/ThomasMichon/copilot-extensions/issues/5001).
  - [x] Phase 1, aggregate slice: `worktree-manager mux-daemon status
        [--json]` (aliased `daemons status`) lists every resident
        mux-daemon for the install root -- pid/port/active-endpoint flag,
        and (when reachable, owner-verified, and running compatible code)
        its own version/attached-client-COUNT/busy state. Landed via
        [#5131](https://github.com/ThomasMichon/copilot-extensions/pull/5131)
        plus a follow-up,
        [#5392](https://github.com/ThomasMichon/copilot-extensions/pull/5392),
        that closed 9 rounds of Copilot review findings #5131 itself
        merged before it could absorb (see Journal) -- including a
        HIGH-severity control-token-disclosure fix (OS-owner verification
        via Windows SIDs/POSIX uid, plus a narrowing post-connection
        re-check), a coalescing bug that undercounted concurrent health
        probes, and an alias that over-exposed the internal `mux-daemon`
        command group.
  - [ ] Phase 1, attribution slice (still open): attributing each
        attached client to its specific project/worktree_id/mux_session
        identity (and that connection's own busy state) -- the issue's
        original Phase 1 scope, explicitly deferred in #5131/#5392 since
        no existing wire RPC or in-memory structure correlates a live
        connection to a mapping entry yet. A **preliminary, non-attributing
        registry listing** landed as a separate, smaller step:
        `worktree-manager daemons mappings [--json]`
        ([#5467](https://github.com/ThomasMichon/copilot-extensions/pull/5467))
        reads the Manager's own disk-backed mapping registry
        (`mux_mapping_registry.MuxMappingRegistry.snapshot()`, shared
        across every resident daemon for a root, not owned by one specific
        pid) and lists every mapping it has ever registered -- project/
        worktree_id/mux_session/`live`/`attached_clients`. This is
        explicitly NOT the attribution slice: it cannot correlate a live
        wire connection to an identity or that connection's busy state,
        which still needs a wire-protocol change (a client_id encoding
        caller identity) that doesn't exist today. It also surfaced a
        real, pre-existing production gap: `attached_clients` is only
        ever as accurate as what a `register()` caller supplies, and
        neither shipped launch path (`bin/launch-session.sh`/`.ps1`) ever
        passes `--attached-clients`, so it reads `0` for every real
        mapping today -- populating/refreshing that field at its real
        source is tracked separately as
        [#4564](https://github.com/ThomasMichon/copilot-extensions/issues/4564).
  - [ ] Phases 2-4 (the actual retirement sweep): remain open. **Ordering
        note for whoever picks this up:** despite the numbering, Phase 2
        (retire-idle-daemon) cannot be implemented first in isolation —
        it depends on Phase 3's client-side re-resolution behavior (how a
        caller notices and reconnects to a rotated daemon) being *designed*
        first, even though Phase 3 is built/landed later. Write Phase 3's
        design (a short planning doc, matching the Phase 3b/3e precedent)
        before claiming Phase 2's implementation slice, or claim Phase 3's
        design-only slice first and let Phase 2 follow it.
- [x] `doctor`/validation breadth: plugin-catalog alignment (coverage)
      reporting landed (PR #4986), covering unmet-plugin-prerequisite/
      cross-plugin-drift detection. The governing vision
      (installer §`health-doctoring-and-validation`) also covers missing
      prerequisites, stale/broken binstubs, and mis-registered repos —
      doctor already reported the prereq/core-install/binstub pieces from
      earlier phases, and the dedicated mis-registered-repos check landed
      via PR #5099 (see Journal), closing this item.
- [ ] Plugin updating & alignment: largely covered already (`worktree-manager
      update` + `agent-worktrees update`/`reconcile-plugins`); remains open
      only for whatever further cross-plugin alignment surfacing doctor/
      configurator work turns up. Closes installer
      §`plugin-updating-and-alignment`.
- [ ] **Harness-plugin onboard presets.** Closes installer
      §`harness-plugin-onboard-presets` (revised in place from the earlier
      §`git-referenced-presets`, see the 2026-10-03 Journal entry for the
      supersession history): a `<repo>-harness` plugin ships its own onboard
      config (related-repo declarations, CodeSpace/venue settings) as part
      of its payload, discovered/merged the same way `agent-codespaces`'
      `load_merged_config()` already layers per-repo
      `.copilot-extensions/agent-codespaces/config.yaml` across adopted
      repos, with Worktree
      Manager eventually folding that merged result into its own
      `harness_state`/`doctor` surface. No design doc exists yet — that is
      the next real open item if this phase is picked up again.

### Phase 8 — Reconcile deferred backlog

- [ ] Accept manager and worktree-control candidates only through
      [`migration-intake`](../migration-intake/README.md)'s deduplication and
      ownership gate.
- [ ] Revalidate accepted technical scope against the current installer, picker,
      and engine contracts; return obsolete or unsafe candidates for explicit
      disposition.
- [ ] Place each accepted public tracker item in exactly one existing phase,
      extending this plan before implementation when necessary.
- [x] Cached worktree-status projection for external consumers: expose a
      manager/engine-backed, cached worktree-status view (e.g. the
      agent-dispatch Tasks-pane's Worktree Status card) over the `--json`
      engine boundary so external consumers stop polling `agent-worktrees`
      directly per render. Landed under the
      `agent-worktrees-external-status-accelerator` effort via
      [PR #3102](https://github.com/ThomasMichon/copilot-extensions/pull/3102)
      (merged 2026-09-21).
- [ ] Keep configuration examples synthetic and repository-neutral.

### Phase 9 — Relocate "Launch in new window" terminal spawning out of agent-worktrees (Done — #5210, PR #5232)

Continues the Phase 3b/3e relocation precedent for a capability that landed
*after* Phase 3b closed and re-introduced the exact violation that phase
eliminated. **Symptom** (confirmed live, 2026-10-04): a worktree opened via the
Picker's "Launch in new window" action has a genuinely live, attached psmux
session, but Worktree Manager's `mux-mapping.json` live-session registry never
learns about it — the entry stays `"live": false` at a stale pre-launch
timestamp, because this launch path never runs
`Invoke-ManagedMuxRegister`/`mux-daemon register`.

**Root cause:** `agent-worktrees copilot --headed`
(`plugins/agent-worktrees/src/agent_worktrees/copilot_cli.py` +
`headed_launch.py`, added by #4593, 2026-09-29 — three days after Phase 3b's
relocation closed) creates/resumes the session in-process via
`cmd_embody`/`sessions.mux_new_session`, then pops a new, visible OS terminal
window **itself** (`headed_launch._windows_spawn`: `wt.exe new-tab` /
`CREATE_NEW_CONSOLE`; `_posix_spawn` for POSIX terminals) instead of going
through `worktree-manager/bin/launch-session.{ps1,sh}` — the only place that
performs the mux-daemon registration. This is exactly the "GUI window"
presentation concern the `session-hosting` vision already assigns to the
Worktree Manager, re-introduced into agent-worktrees.

- [x] **Step 1 — relocate terminal-spawning mechanics.** Moved the
      `wt.exe`/`CREATE_NEW_CONSOLE`/`osascript`/POSIX-terminal-emulator
      probing from `plugins/agent-worktrees`' `headed_launch.py` into a new
      `worktree_manager.new_window_spawn` module, generalized to wrap an
      arbitrary argv (not a fixed `attach-session` command), alongside the
      already-relocated `launch-session.{ps1,sh}`.
- [x] **Step 2 — make "new window" a launch-plan modifier, not a dedicated
      verb.** Added `LaunchRequest.new_window`, composable with the existing
      mode/no_mux/ahp fields; `_run_relocated_mux_launch` opens the new OS
      window running the *same* `launch-session.{ps1,sh}` plan execution used
      for every other launch when set, so `Invoke-ManagedMuxRegister` always
      runs regardless of which window modifier was chosen (and refuses,
      rather than silently falling back to blocking the live Picker, for a
      remote/AHP/missing-script plan). `picker_tui/headed_actions.py`'s
      "Launch in new window" Actions verb now calls `_run_launch` in-process
      with `new_window=True` instead of `agent-worktrees copilot --headed`,
      forwarding the submenu's own `no_mux`/`ahp` toggles (a review finding:
      composing with the separate "Bare resume" entry remains unsupported,
      documented as a known limitation).
- [x] **Step 3 — retire the agent-worktrees terminal-popping path.** Removed
      `headed_launch.py` and `copilot --headed`/`--json` entirely;
      `agent-worktrees copilot` keeps only its plain attach-only semantics,
      closing the Non-Goal the `session-hosting` vision already states ("Not
      a configuration mode of agent-worktrees").
- [x] **Step 4 — regression coverage.** Added `test_new_window_spawn.py`
      (direct platform-dispatch tests mirroring the deleted agent-worktrees
      coverage, plus an AppleScript-injection regression) and
      `TestRunLaunchNewWindow` in `test_production_picker_transplant.py`
      (argv parity between new-window and ordinary launches, explicit-env
      composition with `--no-mux`, and refusal guards for remote/AHP/missing-
      script requests).

Review also surfaced and fixed two concurrency hazards introduced by running
`_run_launch` in-process from a *live* Picker TUI thread (every other call
site only ever runs after `app.exit()` has torn the TUI down): a thread-
unsafe `contextlib.redirect_stdout` (replaced with a nested, refcounted,
thread-scoped stdout proxy that composes safely with pytest's own per-test
`capsys`/`capfd` swap) and a global `os.environ` mutation for `--no-mux`
(replaced with an explicit child `env` passed to the spawn call). A High-
severity AppleScript-injection finding (shell-quoting alone doesn't escape
the AppleScript string literal the quoted command is embedded in) was also
fixed with a dedicated `_applescript_quote` helper and regression test.

### Bug sweep — linked open bugs (2026-09-24)

_Correlated via a facility-driven sweep of open `bug`-labeled issues against active efforts (VEI + direct review). Not yet triaged into a numbered phase — listed here as upcoming work for whoever picks this effort back up._

- [x] **#2426** Worktree Manager/Picker can show/act on the wrong project's content
  - Showing/acting on the wrong project's content is a direct control-plane bug.
  - Fixed via [PR #5413](https://github.com/ThomasMichon/copilot-extensions/pull/5413)
    (see Journal, 2026-10-05); closes the issue.

## Validation Plan

- **Headless render + golden checks.** The Manager Picker's `capture_svg` renders
  with no terminal, so list/interaction states are asserted as fixtures — closes
  picker §`renderable-and-assertable-headless`, §`programmatic-parity`.
- **Contract conformance.** Exercise the `--json` engine verbs the Picker depends
  on against both a current and an older engine to prove graceful degradation.
- **Mux restoration safety.** Prove the recovery verb refuses attached/live-mux
  and ambiguous-owner states, performs no mutation in preview mode, retires only
  a confirmed unreachable no-mux owner, and launches exactly one mux-wrapped
  resume of the selected persisted session.
- **AHP backend lifecycle.** Prove exact-path `target=workspace` creation,
  deterministic hard-bound reattach without an anchor/default session, durable
  zero-client state, fail-closed host/version mismatch, and a finalization barrier
  for live or unknown hosted sessions.
- **Clean-room / fresh-box.** Bootstrap → provision → core-install → bare-launch
  handoff exercised on a disposable fresh machine (the repo's clean-room rig),
  including the absent-Manager onboarding path and the stale-stub health-probe
  rejection.
- **Non-agentic + idempotent.** `setup` is dry-run by default and re-runnable;
  re-running the bootstrap one-liner is version-gated (a no-op when current).
- **"New window" registers like every other muxed launch (Done, Phase 9).**
  A launch using the relocated "new window" modifier runs the IDENTICAL
  `launch-session.{ps1,sh}` argv an ordinary muxed Resume launch would
  (proved by `test_new_window_argv_matches_the_ordinary_launch_argv`), so
  its `Invoke-ManagedMuxRegister` call always fires the same way. A
  `--no-mux` launch composed with `new_window` passes its override as an
  explicit child `env` to the spawn call rather than mutating `os.environ`
  globally (`test_new_window_passes_no_mux_as_an_explicit_child_env_not_a_global_mutation`).

## Coordination

`copilot-extensions` is public and may be driven from more than one private
control repo. **Stale pointer, corrected 2026-09-25:** this section long named
[#352](https://github.com/ThomasMichon/copilot-extensions/issues/352) as the
shared coordination token, but #352 (the Installer & Configurator umbrella)
was itself closed as completed on 2026-09-17 — after that point every
"claim a slice on #352" comment landed on an already-closed issue with no
one watching it, silently defeating the claiming discipline this section
describes. **There is currently no dedicated open coordination-token issue
for this effort.** Until one exists, claim a slice by adding a dated entry to
this file's own Journal (below) naming the exact sub-item before starting it,
and check the Journal's most recent entries for an unreleased claim before
picking up new work — the same discipline the closed issue used to host,
just recorded here instead. Land changes serially through the PR-required
`dev` branch (`main` only ever moves via the CI promotion pipeline, never a
direct PR target — see CONTRIBUTING.md). Downstream private plans may
**link to** this effort and
its issues; the public artifacts stay self-contained and general-purpose.

This effort has already paid the cost of two sessions landing independently
diverging work on the same Picker/Mux surface without being aware of each
other (see the linked duplicate-implementation issue this effort's Phase 3b
exists to correct). **[#2530](https://github.com/ThomasMichon/copilot-extensions/issues/2530)**
tracks a related `agent-bridge` capability gap -- no way for a session to
discover a same-machine peer working a different repo, or a same-repo peer on
a different machine -- that would give future contributors a way to *notice*
overlapping work before it diverges, rather than relying on issue-comment
claiming discipline alone.

## Journal

- **2026-10-06** — Claimed and landed Phase 7's remaining #5006 site:
  `plugins/agent-bridge/src/agent_bridge/service_process_cli.py`'s
  `_kill_pid` now routes through `zdd.diagnostics
  .terminate_pid_if_identity` with a freshly-captured `process_start_time`
  token, mirroring `worktree_manager.mux_daemon_cutover
  ._terminate_mux_daemon_pid`'s #5060 design exactly (identity mismatch
  skips the kill; an unavailable identity primitive falls back to the
  prior unconditional signal/`taskkill` rather than regressing kill
  capability on platforms without `pidfd_open`). Fixed in the single
  shared `_kill_pid` function rather than only its `_service_stop` call
  site, so every caller benefits. Added `test_kill_pid_identity.py`
  covering all three outcomes directly. PR: pending. Full `agent-bridge`
  suite run locally: 3099 passed, 2 unrelated pre-existing timing flakes
  (`test_host_index_claims.py`, `test_local_cache_refresh.py`, both
  process-timing assertions unrelated to this change); `test_session_host.py`
  hits a pre-existing pytest-capture `OSError: Bad file descriptor`
  teardown issue on this machine even run alone, unrelated to this diff
  (no session-host code touched).
- **2026-10-06** — Landed a preliminary, non-attributing registry listing
  for #5001's Phase 1 attribution slice: `worktree-manager daemons
  mappings [--json]` (PR
  [#5467](https://github.com/ThomasMichon/copilot-extensions/pull/5467),
  3 Copilot review rounds). This does NOT satisfy the attribution
  requirement itself: no wire-level client_id encodes caller identity
  today, so a live connection can't be correlated back to a project/
  worktree_id/mux_session or that connection's busy state -- reworded the
  docstring/changefile/tests accordingly rather than overclaiming
  completion, and left the attribution slice itself open in the Plan
  above. Also surfaced (not introduced) a real production gap while
  implementing this: `attached_clients` reads `0` for every real mapping
  today because neither shipped launch path
  (`bin/launch-session.sh`/`.ps1`) ever populates `--attached-clients` --
  already tracked as
  [#4564](https://github.com/ThomasMichon/copilot-extensions/issues/4564).
  Merged via Maintainer admin bypass after the SAME unrelated CI job
  (`agent-worktrees (collect-only + guard tests)`, its own already-tracked
  6-minute coverage-shadow-step timeout, #5340/#5461) failed 3 consecutive
  times with the actual review verdict APPROVED and every other check
  green.
- **2026-10-05** — Revised the Plan's Phase 7 entries to reflect two
  findings from continuing this effort: (1) #5006's PID-reuse-safe
  mux-daemon termination is already resolved for the mux-daemon call site
  via #5060, using a simpler live-reverification design than this Plan's
  original breadcrumb-schema proposal — marked `[x]`; (2) clarified the
  Phase 2-4 ordering note under #5001's "Background daemon rotation" bullet
  — Phase 2 genuinely depends on Phase 3's design existing first, despite
  the numbering, which read ambiguously before. No vision revision was
  needed: neither finding changes a stated behavior/guarantee in
  `visions/installer` or `visions/picker`, only the effort's own
  in-progress Plan detail. Review on the landing PR (#5473) caught a real
  gap in the first pass: the claim that a broader per-consumer audit found
  "no such case" was wrong — `agent-bridge`'s `service_process_cli.py`
  (`_kill_pid` / `_pid_is_agent_bridge`) has the identical check-then-
  bare-kill window, just not yet remediated. Corrected the Plan text to
  record that specific site instead of claiming none exist.
- **2026-10-03/06** — Claimed and landed Phase 7's "Background daemon
  rotation" Phase 1 (observability), copilot-extensions#5001: added
  `worktree-manager mux-daemon status [--json]` (aliased `daemons status`)
  enumerating resident mux-daemons with pid/port/active/version/attached-
  client/busy state. PR #5131 merged (via Maintainer admin bypass) after
  three rounds of Copilot review fixes but BEFORE a fourth round's fixes
  could reach that now-closed branch -- the remaining commits were
  cherry-picked onto a fresh branch off `dev` and landed as a separate
  follow-up, PR #5392, which absorbed six MORE review rounds (9 total
  across both PRs) before merging clean. Real, substantive findings fixed
  along the way: a HIGH-severity control-token-disclosure bug (a lookalike
  process from a different local OS account could receive the cutover
  bearer token -- fixed with Windows-SID/POSIX-uid ownership verification
  plus a narrowing post-connection re-check, since full elimination needs
  a wire-protocol-level peer-authentication redesign affecting every
  `ControlClient` caller, explicitly scoped out and left for future work
  under the same issue); a coalescing bug that let two concurrent health
  probes undercount each other's load; an alias that accidentally exposed
  the entire internal `mux-daemon` command group instead of just `status`;
  and a macOS/BSD gap in the post-connection identity re-check (local
  `ps -o lstart=` fallback, since the shared `zdd` library's
  `process_start_time()` only implements Windows/Linux). Also fixed a
  pre-existing, unrelated flaky test
  (`test_resident_monitor_restart_republishes_live_mapping_to_new_
  generation`) whose 5s join timeout was too tight for this machine's
  current load (observed up to 15.5s for a nominal ~0.6s workload),
  widened to 20s with an explicit thread-liveness assertion.
  Phases 2-4 (the actual retirement sweep) remain open.
- **2026-10-05** — Claimed and landed the **bug sweep's #2426 candidate 2**
  audit (the router's `_PROJECT_ARG_SLUGS` sibling-plugin scoping question
  left open by the maintainer's own 2026-09-16 comment on #2426). Audited
  every remaining `_CORE_SLUGS` sibling (`dispatch`, `containers`, `logger`,
  `vault`, `mcp`) for whether it has a top-level `--project` the router
  could meaningfully inject: none do currently, so adding any of them
  mechanically would only break them with an argparse error, not fix
  anything — `dispatch` has no project concept at all (cross-project task
  queue); `containers`' own `--project` is a distinct, subcommand-scoped
  concept (a Picker source label) unrelated to "the repo this invocation is
  about"; `logger`/`vault`/`mcp` each resolve config by ascending from cwd
  with no top-level `--project` surface, and `vault` in particular is
  plausibly *intentionally* cwd-scoped (a secrets operation tied to the
  literal directory stood in) per the issue's own caution. One sibling
  *did* have a clear, confirmed-safe case: `agent-ssh`'s `mesh-status`/
  `refresh-mesh` resolve `machines.yaml` by ascending from cwd
  (`mesh.find_machines_file`) with no stated CWD-pinning rationale, so a
  `<repo> ssh mesh-status` invocation silently acted on whatever repo the
  caller's raw cwd happened to resolve to — ignoring the explicitly-named
  `<repo>` binstub, exactly candidate 2's reported symptom. Landed via
  [PR #5413](https://github.com/ThomasMichon/copilot-extensions/pull/5413).
  First pass forwarded a top-level `--project` for `agent-ssh` to resolve
  itself (mirroring `agent-bridge`/`agent-codespaces`' `--project` pattern,
  #1080) — review correctly caught that this violates the plugin-stack
  layering rule (`docs/patterns/a-la-carte-independence.md`): `agent-ssh`
  sits *below* `agent-worktrees`, so it must never reach upward
  (`shutil.which("agent-worktrees")`) to resolve a project name itself,
  unlike `agent-bridge`/`agent-codespaces` which sit *above* it. Revised to
  have the router resolve the project and chdir the child process itself
  (`_resolve_project_checkout` via `repos.py`'s own in-process registry) —
  `agent-ssh` needed no CLI changes at all, since its existing cwd-ascending
  `machines.yaml` resolution now simply sees the correct cwd. An
  unresolvable `--project` fails the routed call closed (nonzero exit)
  rather than silently running against the caller's original cwd. A second
  review round caught two more real issues: the router's own `<repo> <slug>`
  dispatch only ever threaded the resolved `project` through for
  `_PROJECT_ARG_SLUGS` members, so every real `<repo> ssh …` call actually
  reached `_route_to_sibling_plugin` with `project=None` and the whole
  chdir/fail-closed path was dead code in practice — caught only because
  review asked for an end-to-end test through `m.main(...)` rather than
  trusting a unit test that called the helper directly; and the chdir had
  been scoped per-*slug* rather than per-*verb*, so it would have silently
  broken `ssh`'s own relative-path arguments (e.g. `emit-profile CONFIG
  --module MODULE`) and wrongly failed machine-global verbs (`doctor`) closed
  on an unresolvable project. Fixed by threading `project` through whenever
  a slug has *either* kind of consumer, and replacing the per-slug
  `_PROJECT_CHDIR_SLUGS` with a per-verb `_PROJECT_CHDIR_VERBS` map
  (`{"ssh": {"mesh-status", "refresh-mesh"}}`) that `_route_to_sibling_plugin`
  consults via `_chdir_verb_for`, with every other `ssh` verb staying a
  plain cwd-preserving alias. New routing tests in agent-worktrees'
  `test_cli_routing.py` (an end-to-end `m.main(...)` test for the threading
  bug, a non-chdir-verb regression case); full `agent-ssh` suite unaffected
  (clean dev shows the same 2 pre-existing/environmental failures — a
  Windows `bash.exe` path-mangling issue in
  `test_shared_installer_engine_manifest_kind.py` -- plus an unrelated,
  non-reproducing test-ordering flake seen once in `test_host_restore.py`
  that passed on rerun and in isolation).
  Candidate 1 (the Picker's silent `projects[0].name` fallback) was already
  fixed in #2732; this closes out candidate 2's open scope. Closes #2426.

- **2026-10-04/05** — Executed and landed Phase 9 (PR #5232, merged): moved
  `wt.exe`/`CREATE_NEW_CONSOLE`/`osascript`/POSIX-terminal probing into a new
  `worktree_manager.new_window_spawn` module (generalized to an arbitrary
  argv); added `LaunchRequest.new_window` as a composable launch-plan
  modifier wired through `_run_relocated_mux_launch`/`_run_launch` (split
  into a new `relocated_launch.py` sibling module to stay under
  `__main__.py`'s module-size cap); rewired `headed_actions.py` to call
  `_run_launch` in-process instead of the retired
  `agent-worktrees copilot --headed`; deleted `headed_launch.py` and
  `copilot --headed`/`--json` entirely. Review (PR #5232) caught real bugs
  beyond the original plan: a High-severity AppleScript-injection gap
  (shell-quoting alone doesn't escape the AppleScript string literal the
  quoted command sits inside — fixed with `_applescript_quote` + a
  regression test), a thread-unsafe `contextlib.redirect_stdout` (this is
  the first call site to run `_run_launch` from a *live* Picker TUI thread
  rather than after `app.exit()`; fixed with a nested, refcounted,
  thread-scoped stdout proxy that composes correctly with pytest's own
  per-test `capsys` swap, which is what surfaced the bug), a global
  `os.environ` mutation leaking `WORKTREE_NO_MUX=1` into later/concurrent
  spawns (fixed with an explicit child `env`), and dropped `no_mux`/`ahp`
  submenu toggles (fixed by forwarding them through the dispatch site).
  Restored direct platform-dispatch tests (`test_new_window_spawn.py`)
  matching the deleted agent-worktrees coverage. 362 tests passing across
  the touched surfaces (agent-worktrees' `test_copilot.py` full +
  `-k "headed or copilot"` suite subset, worktree-manager's new-window/
  relocated-launch suites). A full worktree-manager suite run separately
  surfaced 8 pre-existing, unrelated Windows-symlink-extraction failures
  (none touch files in this diff) — not this phase's concern. Phase 9 is
  **Done**.
- **2026-10-04** — Filed Phase 9 and issue #5210: an operator-reported symptom
  ("Launch in new window" doesn't set up mux instances correctly with the
  status monitor) traced to `agent-worktrees copilot --headed`/
  `headed_launch.py` (added by #4593, 2026-09-29) re-owning terminal-window-
  spawning mechanics — exactly the class of thing Phase 3b relocated out of
  agent-worktrees three days earlier (closed 2026-09-26, PR #3891). Confirmed
  live: four worktrees launched via "Launch in new window" had genuinely live,
  attached psmux sessions while `mux-mapping.json` still showed `"live":
  false"` at a stale pre-launch timestamp, because this path never runs
  `worktree-manager/bin/launch-session.{ps1,sh}`'s
  `Invoke-ManagedMuxRegister`. No vision revision needed — `session-hosting`
  already states the governing boundary ("both the Mux presentation layer and
  the AHP backend are owned and driven by the Worktree Manager... rather than
  by agent-worktrees"); this phase closes a reality gap against it, the same
  as Phase 3b/3e. Planned as a 4-step relocation (move the spawn mechanics,
  make "new window" a launch-plan modifier instead of a dedicated verb,
  retire the agent-worktrees path, add regression coverage) rather than a
  one-line bug-sweep item, since it is architecturally identical to Phase
  3b/3e's own work. Next: submit this plan for review, then execute Step 1.
- **2026-10-03** — Landed the dedicated mis-registered-repos doctor check
  (Phase 7's remaining `doctor`/validation-breadth gap), closing
  installer §`health-doctoring-and-validation`'s last open item for this
  Phase. Investigated `agent-worktrees`' own `doctor.py`
  `missing_repo_entry` finding first
  (issue [#2961](https://github.com/ThomasMichon/copilot-extensions/issues/2961))
  to see if it could be surfaced directly; it detects the opposite
  direction (an adopted project with no `repos.yaml` entry at all) and
  lives inside `agent-worktrees`' own mutation-capable surface, so
  importing it would break `harness_state.py`'s explicit read-only,
  no-plugin-import boundary. Scoped instead as a small, self-contained
  read-model addition (`mis_registered_repos()`), surfaced in
  `worktree-manager doctor` the same way plugin-catalog alignment was in
  PR #4986. Landed as
  [#5099](https://github.com/ThomasMichon/copilot-extensions/pull/5099)
  after 4 Copilot review rounds, each catching a real correctness gap:
  (1) `build_repos()`'s deliberate cross-platform path fallback meant a
  repo registered only under a sibling platform's key was wrongly
  inspected as if it were this platform's own — fixed with a dedicated
  `_exact_platform_key()` (no fallback, WSL-aware); (2) a filesystem-
  marker-only git-checkout test (`.git` exists, or `HEAD`+`objects`
  present) would pass an empty/corrupt directory — replaced with a real
  `git rev-parse --is-bare-repository`/`--show-toplevel` probe
  (`GIT_*` env cleared); (3) the platform detector's `Darwin` branch
  returned a nonexistent `repos.yaml` `macos` key (agent-worktrees itself
  maps Darwin to `linux`) — fixed, and a bare `~/src/repo` registration
  wasn't expanded before the filesystem check — fixed via
  `Path.expanduser()`; (4) an inconclusive git probe (binary missing,
  timeout) was silently dropped, which would make `doctor` claim full
  success for a repo it genuinely couldn't verify — reworked
  `mis_registered_repos()` to return a 3-way status (`missing`/`not-git`/
  `unknown`) so `doctor` surfaces the unknown case visibly without
  treating it as confirmed drift or exit-status-blocking.

  Along the way, hit and fixed an unrelated repo-wide CI blocker: PR #5096
  had merged a `pr-workflow.md` prose edit using a bare
  `agent-worktrees repos add ...` command instead of the catalog-resolved
  `argv[0]` placeholder form, tripping the marketplace-isolation
  `bare-agent-command` guard and failing `guards + lint` on `dev` for
  every PR. Fixed directly as a tiny separate PR
  ([#5103](https://github.com/ThomasMichon/copilot-extensions/pull/5103)),
  merged first, then rebased this PR onto the fix.

  Final merge was also blocked by a second, unrelated and still-open CI
  issue: `tools/test_run_tests_in_devcontainer.py`'s tests assume a real
  `devcontainer` CLI is on `PATH` (only `_bring_up`/`_run_tests` are
  mocked, not `_devcontainer_exe()` itself) and fail when the CI runner's
  image lacks it — confirmed failing on `dev` itself at the same time
  (not caused by this PR), and non-deterministic across reruns of the
  identical commit. Filed
  [#5107](https://github.com/ThomasMichon/copilot-extensions/issues/5107)
  to track it (left unfixed — bigger, unrelated surface) rather than
  scope-creep this PR, and merged #5099 via the resolved Maintainer
  bypass once the failure was confirmed environmental.

- **2026-10-03** — Claimed and landed a bounded Phase 7 slice: fixed a
  stranded-cutover-passive bug in `self_install()` (a crashed
  `self_update()` could leave a passive mux-daemon's `cwd` pinned inside a
  version slot, colliding with a later bare `self-install --apply` on the
  same slot and raising a Windows `PermissionError`). Diagnosed live on an
  operator machine via `psutil`-based process/cwd correlation (four
  resident per-version mux-daemons, each a legitimately live pinned
  worktree session — not orphans — plus one genuine stray child process
  pinned into the newest staged slot). Filed
  [#4999](https://github.com/ThomasMichon/copilot-extensions/issues/4999),
  landed the fix as
  [#5000](https://github.com/ThomasMichon/copilot-extensions/pull/5000)
  after four Copilot review rounds. Findings and disposition: (1) first
  round — hold the cutover lease through the whole reap-plus-slot-mutation
  and defer rather than mutate unprotected on a busy lock (fixed); don't
  unconditionally clear the recovery breadcrumb on a failed termination,
  since the same breadcrumb's `old` endpoint may still need
  `recover_stale_cutover()`'s undrain (fixed — breadcrumb now left
  untouched, a later reap simply finds the pid already dead); (2) second
  round caught a dead duplicate function definition left by an earlier
  edit, and asked to fail CLOSED (defer) when the cutover machinery itself
  isn't importable rather than silently proceeding unprotected (both
  fixed); it also flagged a genuine PID-reuse gap in the PRE-EXISTING
  `_terminate_mux_daemon_pid` (identity checked by cmdline/root, but the
  kill signal is issued separately by bare PID) — scoped out rather than
  rushed in, since closing it properly needs a breadcrumb schema change
  (`process_start_time` alongside `new_pid`) in the shared
  `zdd.cutover.CutoverOrchestrator` affecting every `zdd` consumer, not a
  `worktree-manager`-local fix; tracked instead as
  [#5006](https://github.com/ThomasMichon/copilot-extensions/issues/5006),
  cross-referenced from `_terminate_mux_daemon_pid`'s own docstring; (3)
  third round caught the real remaining race — `needs_install()` was
  checked once before acquiring the lease but never re-checked after, so a
  concurrent self_install()/self_update() finishing while this call waited
  for the lease could have its just-activated slot blindly rmtree'd and
  recopied — fixed by re-checking under the lease and short-circuiting to
  `already-current`; (4) fourth round approved clean after confirming (via
  direct review-thread replies) that the identity-test, changefile, and
  documentation/cutover-impact findings from earlier rounds were already
  addressed in intervening commits. Also filed, as separate properly-scoped
  follow-ups rather than folding into this bug fix: #5006 above, and
  [#5001](https://github.com/ThomasMichon/copilot-extensions/issues/5001) —
  a 5-phase design for a background daemon-rotation sweep (resident
  per-version mux-daemons otherwise accumulate indefinitely, since
  `activate_after_update()`'s cutover is only attempted opportunistically
  and a daemon with any long-lived client can block its own retirement
  forever with no retry). Both tracked under this Phase 7's Plan above;
  neither started.
- **2026-10-03** — Claimed and landed a bounded Phase 7 slice: extended
  `worktree-manager doctor` to run `model.coverage()` and report
  plugin-catalog alignment (uncovered/phantom/published-prereq-gap)
  alongside the existing mux-daemon-health report, in both human-readable
  and `--json` output — closing part of the "doctor/validation breadth"
  wording (installer §`health-doctoring-and-validation`). Landed as PR
  [#4986](https://github.com/ThomasMichon/copilot-extensions/pull/4986)
  after **four** Copilot review submissions (three "changes recommended" /
  "needs a closer look" rounds, then an approval that still carried one
  open low-severity finding). Findings and disposition: (1) initial round
  flagged both the "no catalog drift" success message being wrong when
  only non-blocking uncovered plugins were present, and missing
  published-prereq-gap regression coverage — both fixed (message now
  matches `plugins --reconcile`'s existing ok/uncovered distinction; added
  the gap-rendering/exit-status test); (2) second round flagged a manual
  version bump conflicting with this repo's changefile-only release
  workflow for PRs into `dev` — reverted (`check-changefile-presence.py`
  replaced `check-version-bump.py` for this path per `ci.yml`); that round
  also re-surfaced the still-open documentation finding from round one;
  (3) third round, after README docs were added, flagged that a clean
  report under remote discovery (no local checkout) implied full
  validation when `coverage()` actually skips the published-prerequisite
  check entirely without a checkout — qualified as membership-only, with a
  regression test; (4) the approval still listed "add the required
  Documentation impact statement to the PR description" as an open
  low-severity finding (CONTRIBUTING.md's required-before-opening
  statement, not the README content itself, which was already in place).
  **Correction to this entry's first draft:** that draft claimed the
  statement was "addressed by editing the PR description before merge,"
  but the `gh pr edit` used to do so silently truncated the body at an
  un-escaped backtick in a PowerShell argument — the PR merged with the
  edit never actually applied, so the low-severity finding was in fact
  still open at merge time (caught by this very journal PR's own Copilot
  review, round 2, auditing the inaccurate claim against the live PR
  record). Fixed post-merge by re-editing #4986's description via a
  `--body-file`, verified present in the PR's current body. Validation:
  targeted `test_doctor.py` (8/8 after the fixes), full `worktree-manager`
  suite (1407 passed, the same 9 pre-existing Windows
  symlink-privilege/daemon-race failures noted in the prior session's
  entry, 4 skipped, 1 deselected known flake), `ruff`, install-contract,
  version-consistency, and changefile-presence checks all green. Phase 7
  remains open — `doctor` plugin-alignment is one slice of "doctor/
  validation breadth, plugin updating & alignment, and git-referenced
  presets"; plugin updating/alignment already has `worktree-manager
  update` + `agent-worktrees update`/`reconcile-plugins`, and
  git-referenced presets (installer §`git-referenced-presets`, tracked by
  issue #358) remains fully undesigned — the next natural slice if this
  phase is picked up again.

- **2026-10-03** — Operator direction: the installer §`git-referenced-presets`
  vision item (a human ingesting a shareable config bundle by explicit Git
  reference) is **superseded**, not merely deferred — revised in place to
  §`harness-plugin-onboard-presets` in the same PR as this entry
  (`visions/installer/README.md`, per this repo's cross-repo-sequencing
  rule: the vision update lands before any further realization work). The
  real direction is a `<repo>-harness` plugin shipping its own onboard
  "preset" — related-repo declarations, CodeSpace/venue support, and more
  — as part of its own plugin payload, picked up automatically rather than
  ingested by reference. Investigated the existing substrate this would
  build on (no code named "preset" exists yet — this is a forward design, not
  something already implemented under a different name):
  `worktree-manager/src/worktree_manager/harness_state.py`'s
  `build_state()`/`build_repos()`/`build_projects()` already sweep the
  registered-projects manifest (`~/.agent-worktrees/{repos,projects}.yaml`)
  plus each repo's own `enabledPlugins` into a read-only "checkout layout"
  model Worktree Manager already consumes; separately,
  `plugins/agent-codespaces/src/agent_codespaces/config.py`'s
  `load_merged_config()` already layers and deep-merges a generic
  `.copilot-extensions/agent-codespaces/config.yaml` across every *adopted*
  repo (including a
  `codespace_plugins:` list explicitly documented in-code as "same entry
  shape as a harness plugin's `codespacePlugins` manifest array"). The
  onboard-preset mechanism is the natural extension of both: a
  `<repo>-harness` plugin's own payload carries the equivalent default
  config, discovered/merged the same layered way, with Worktree Manager
  eventually folding that merged result into its own `harness_state`/
  `doctor` surface. No design doc exists for this yet (not even a stub) —
  it is the real next open item for anyone picking up presets, replacing
  (not just updating) the original git-ref-ingestion framing. Updated the
  Phase 7 checklist above to reflect the supersession, revised the
  governing installer vision in place
  (§`git-referenced-presets` → §`harness-plugin-onboard-presets`, plus the
  `visions/README.md` one-line summary), and left a comment on issue
  [#358](https://github.com/ThomasMichon/copilot-extensions/issues/358)
  pointing at this entry. **Correction to this entry's original draft:**
  that draft claimed this PR itself fixed issue
  [#5030](https://github.com/ThomasMichon/copilot-extensions/issues/5030)
  (the `troubleshooting-agent-dispatch/SKILL.md` marketplace-isolation
  `bare-agent-command` guard failures blocking `origin/dev`'s required
  `guards + lint`/`PR gate` check). By the time this branch could rebase
  cleanly, PRs #5037 and #5039 had already landed the real upstream fix —
  this PR carries none of it, just a rebase onto it. The only actual
  change this PR makes to that skill is a small, genuinely incremental
  follow-up (flagged by this same review round): the "Before you start"
  catalog-path note only resolved the `agent-dispatch` placeholder even
  though the body already used `agent-bridge` and `agent-mcp` placeholders
  too — extended the note to cover all three, with its own changefile.

- **2026-10-02** — Added a genuine `## Participants` declaration (this
  effort predates that template convention and had none) solely so an
  agent-worktrees worktree could durably bind to this effort via
  `effort-focus bind` per operator request: "this worktree should claim the
  effort so it can't finalize until the effort is done." Bound a worktree
  on this machine with participant "Solo operator (private control repo)"
  and slice "Phase 7 — Health, updating & presets (Ongoing)" — the only
  currently-open-ended Plan phase, since Phase 4 is otherwise fully closed
  and Phases 5/8 remain merely Planned rather than actively worked. That
  worktree now carries the completion gate: it cannot
  `effort-focus release --completed` until `Status: Done` and every
  Plan/Validation Plan checkbox is resolved or explicitly transferred.
  Docs-only.

- **2026-10-02** — Landed Phase 4's generic control-plane-provider
  registration contract (claimed 2026-10-01). Resumed a prior session's
  interrupted work rather than restarting: found 11 real, well-sequenced
  commits already in place (design doc, provider manifest template,
  `self_install.py` registration on install, `front_door_cli.py`'s registry-
  based discovery replacing the literal `worktree-manager` PATH probe,
  synthetic-provider + Windows-hardening tests, version bumps) but the
  effort docs/Journal were never finished and no PR had been opened.
  Reviewed the full diff end to end against the design doc
  ([`phase-4-control-plane-provider-registration.md`](phase-4-control-plane-provider-registration.md))
  before trusting it: confirmed the manifest schema, discovery/selection
  rule, and preserved diagnostic quality all matched the documented design,
  and that `worktree-manager` consumes its own registry entry as the
  reference implementation rather than keeping a parallel hardcoded path.
  Rebased onto `origin/dev` twice (a large volume of unrelated repo activity
  landed during the gap), resolving a 3-file version-number conflict by
  re-bumping past the now-current `dev283` to `dev284`. Validation was
  unusually difficult on this run: this machine is heavily loaded with many
  long-lived background `agent-worktrees`/`worktree-manager` daemons
  (status-monitor, mux-daemon instances across several installed versions),
  which produced two classes of environment-level flakiness unrelated to
  this change -- a stuck prior test process surviving a multi-hour machine
  idle/suspend gap (0.02s CPU after 15+ wall-clock hours, force-killed), and
  intermittent `git` subprocess hangs inside unrelated test fixtures
  (`test_hooks.py`'s commit, `conftest.py`'s `pr_repo` push) when running the
  full suite as a single long session. Isolated the one chronically-slow
  file (`test_ext_reload_warning_retirement.py`'s preview-materialize test)
  and proved it passes cleanly on its own (8/8, 111s) before it was ever
  blamed as a regression. Eventually got one full, clean `agent-worktrees`
  run to completion (1:04:10 wall-clock, genuinely CPU-active throughout, not
  stuck): **6348 passed, 52 skipped, 1 failed** -- the sole failure
  (`test_status_monitor_cutover_helper.py::test_activate_after_update_cuts_over_and_converges`)
  is an unrelated Windows file-rename `Access is denied` race against this
  machine's own live status-monitor processes, not a file this change
  touches. Full `worktree-manager` suite: 1400 passed, 9 failed (all
  pre-existing Windows symlink-privilege gaps), 4 skipped. Targeted
  new-feature suites (`test_cli_routing.py` 122/122;
  `worktree-manager/tests/test_self_install.py` 23/25, 2 skipped for the
  same symlink-privilege reason) both fully green. `ruff`, install-contract,
  version-bump, version-consistency, and changefile-presence checks all
  pass. Landed as a single PR (the work was already a coherent, additive-
  then-cutover-in-one unit by the time it was resumed -- not re-split into
  per-step PRs since no intermediate state needed independent landing).

- **2026-10-01** — Claiming Phase 4's "Open question, not yet designed"
  item: the bare-invocation seam's hardcoded `worktree-manager`-binstub
  health-probe should become a generic, pluggable **registration contract**
  (marker file/env var/capability probe) any conforming third-party
  Picker/control-plane provider could satisfy. **Operator direction
  (2026-10-01): design and implement the generic contract now** (explicitly
  chosen over the alternative of formally closing this as
  intentionally-single-provider). Working solo per standing operator
  directive; recorded here per this effort's own Coordination-section
  claiming discipline since #352 is closed.

- **2026-10-01** — Closed the Phase 4 **absent-Manager onboarding polish**
  item via [#4838](https://github.com/ThomasMichon/copilot-extensions/pull/4838),
  after first resolving the scope ambiguity explicitly. Re-read this effort's
  Guiding Intent + Phase 4 wording, the full picker
  §`first-run-onboarding-entry` vision text/journal, the installer
  §`onboards-from-empty-gracefully` behavior, and issues #540/#541/#542/#357.
  Result: this checkbox is the **narrow plugin-side seam copy** only, not the
  broader Worktree-Manager-side setup-first home. The evidence lined up in one
  direction: the Guiding Intent distinguishes "Manager absent → trustworthy
  install/onboarding trigger" from "Manager present → control-plane/front
  door"; #542 already owns the richer first-run Picker/home behavior in the
  standalone Manager, with #540/#541 already closed as its install-side
  prerequisites, while #357 remains the broader configurator track. Implemented
  only the concrete plugin-side gap that remained: `cmd_manager_install_trigger`
  now frames the missing Manager as an **expected first-run state**, names the
  bootstrap as the way to get the interactive front door, keeps the repository
  verification link + exact bootstrap command, and preserves the explicit
  headless-commands-still-work reassurance. Tightened the routing test to lock
  that calmer onboarding framing in place. While validating, current `dev`
  surfaced two unrelated-but-real agent-worktrees suite blockers on this
  machine: the POSIX direct-install bootstrap test assumed `sh` existed on
  Windows, and two mux-status-link tests leaked host Worktree-Manager routing
  state. Fixed both in the same PR so the required full suite could run green,
  without broadening the feature scope.

- **2026-09-29** — Claiming Phase 4's "onboarding polish for the
  absent-Manager path" item: making the install-trigger path (what fires
  when a bare invocation resolves to no usable Worktree Manager) read as a
  guided first-run onboarding experience rather than an error, per the
  Guiding Intent's picker §`first-run-onboarding-entry` and installer
  §`onboards-from-empty-gracefully` closures this item targets. Working
  solo per standing operator directive; recorded here per this effort's own
  Coordination-section claiming discipline since #352 is closed.

- **2026-09-29** — Resumed this exact worktree after an interrupted landing
  pass rather than restarting from scratch. Found one good committed slice
  already landed locally (`d7d1183d4`, the Step 8 housekeeping cutover) plus a
  second, uncommitted continuation on top of it: a legitimate follow-on fix in
  `production_picker.monitor_roots` / `engine_group_d` / related tests, but
  with the `worktree-manager` version accidentally rolled **backward** from the
  committed `0.1.0-dev96` to `0.1.0-dev95`. Reconciled that interruption
  artifact first by restoring the forward-only committed value
  `0.1.0-dev96` while leaving the actual bump itself promotion-owned via the
  already-present changefile, matching this repo's versioning workflow.
  Finished the in-flight fix rather than reverting it: the Manager-owned
  monitor-root port now treats an existing
  `status-monitor.lock` as reusable only when it still matches the current
  `agent-worktrees` runtime prefix **and** the caller's current mux capability,
  and otherwise respawns `agent-worktrees status-monitor` with the same clean
  detached environment contract the engine-side helper uses (no inherited
  session/project argv handoff, no leaked session credentials). This closes the
  real related bug the interrupted diff was heading toward: after an
  `agent-worktrees` upgrade or a change from no-mux to mux-capable execution,
  `ensure_status_monitor_running()` could previously keep deferring forever to
  a stale or incapable resident monitor just because its PID was still live.
  Also completed the uncommitted Group D follow-through by forwarding
  `dry_run` / `idle_grace_secs` through
  `production_picker.housekeeping.reap_orphan_mux_sessions()` into the public
  `reap-sessions --json` seam, tightening the targeted test coverage for both
  the Manager wrappers and the monitor-respawn gating. Phase 3d remains a clean
  **Done — #3360** with no residual exception wording; the accompanying Step 8
  plan write-up and engine-picker contract doc now reflect the final landed
  state rather than the interrupted intermediate one.

- **2026-09-28** — Resolved the residual `housekeeping.py` in-process
  boundary gap flagged 2026-09-27. Per-call-site outcome, matching the
  final Step 8 write-up in
  [`phase-3d-engine-runtime-retirement.md`](phase-3d-engine-runtime-retirement.md):
  `config.tracking_dir()` moved to the existing Manager-owned
  `project_config` reader; the Manager-owned row discovery path now scans the
  tracking YAMLs directly for only the `worktree_id` +
  `execution_leg.provider` facts it needs; orphan mux-session reap now shells
  out through the public engine seam once per housekeeping pass
  (`reap-sessions --json`, additively widened with repeatable
  `--worktree-id` plus `--include-manager-owned`); managed/finalized sweep
  wrappers now shell out via focused `sweep-managed --json` /
  `sweep-finished-sessions --json` commands; launcher-shell reap now uses the
  existing public `reap-shells --json --yes` verb; the finished-session grace
  default is local data rather than an imported engine constant; and Picker
  monitor-root upkeep now stays Manager-owned by checking the resident
  `status-monitor.lock` locally and spawning the public `status-monitor`
  command only when absent, instead of importing
  `status_monitor_runtime._ensure_status_monitor()` on every heartbeat. Added
  new Worktree Manager regression coverage (`test_engine_group_d.py`,
  refreshed `test_housekeeping.py` / `test_monitor_roots.py`,
  tighter `test_production_picker_runtime_boundary.py`) plus agent-worktrees
  coverage for the widened `reap-sessions` contract and the focused
  housekeeping commands (`test_reap_orphans.py`, `test_auto_clean.py`). This
  removes the need for any Picker-vision exception: Phase 3d's header is back
  to a clean **Done**, and Step 7's old "literal import only" residual note is
  retired rather than carried as a documented carve-out.

- **2026-09-28** — Claiming the residual `housekeeping.py` in-process
  boundary gap flagged 2026-09-27 (Phase 3d Step 7's "Done" claim was
  incomplete). Investigating whether to convert
  `production_picker/housekeeping.py`'s 7-module in-process usage
  (`config`/`tracking`/`sessions`/`activity`/`reap_cli`/`gc`/
  `status_monitor_runtime` via `agent_worktrees_runtime.engine_module(...)`)
  to `--json` verbs/subprocess calls, weighing the flagged
  subprocess-per-sweep cost/latency tradeoff for exit-hook/cadence-timer
  call sites, or documenting a deliberate carve-out if conversion proves
  unjustified. Working solo per standing operator directive; recorded here
  per this effort's own Coordination-section claiming discipline since #352
  is closed.

- **2026-09-27** — Re-audited Phase 3d Step 7's "Done" claim (PR #4357,
  landed concurrently by another session) instead of taking it at face
  value, per this effort's own investigate-before-trusting-prior-reports
  discipline. Finding: the Step 7 regression guard
  (`test_production_picker_runtime_boundary.py`) only scans for a literal
  `import agent_worktrees` statement inside `production_picker/*.py`, and
  passes clean -- but `production_picker/housekeeping.py` (Step 3's ported
  Picker-owned lifecycle sweeps) still calls
  `worktree_manager.agent_worktrees_runtime.engine_module(...)` for 7
  modules (`config`, `tracking`, `sessions`, `activity`, `reap_cli`, `gc`,
  `status_monitor_runtime`) -- the identical dynamic
  `importlib.import_module("agent_worktrees.<name>")` mechanism the deleted
  `_engine_runtime.py` used, just relocated one module up so the
  package-scoped literal-import scan doesn't trip on it. This satisfies the
  guard's letter, not the phase's own stated substance ("no in-process
  `agent_worktrees` import for production Picker **behavior**," not merely
  "no import statement in this **package**"). Did not attempt a fix this
  pass: genuinely converting `housekeeping.py`'s 7-module usage to `--json`
  verbs is a real, undecided design tradeoff (these sweeps run from
  process-exit hooks and a background cadence timer, not the Picker's own
  refresh cycle, so a subprocess-per-sweep cost/latency profile needs actual
  evaluation, unlike Group A/B/C's already-established low-frequency or
  already-async call sites). Recorded the gap explicitly in both this file
  (Phase 3d's Step 7 checkbox, header changed to "Done, one residual gap
  flagged") and
  [`phase-3d-engine-runtime-retirement.md`](phase-3d-engine-runtime-retirement.md)'s
  own Step 7 entry, rather than silently letting an incomplete boundary
  closure stand as fully "Done." Docs-only; no code changed. Left as an open
  item for whoever picks this up next: either do the conversion, or
  deliberately amend the governing visions' exit criteria to carve out
  `housekeeping.py` as a reasoned exception instead of an accidental one.

- **2026-09-27** — Fixed a real production regression discovered live during
  facility diagnosis: since Phase 3b Sub-slice 3 (#3865), the companion
  `mux-daemon` only republished a live mux mapping to the resident
  status-monitor on a status-monitor lock **generation change** (i.e. only
  right after a monitor restart) -- never on an ongoing cadence. Because the
  monitor's own managed-mux cache treats a pushed mapping as stale after
  `mux_link.MAPPING_STALE_AFTER_SECONDS` (45s), every managed session's mux
  status bar silently went blank 45s after the last monitor restart and
  stayed blank until the next one -- for hours, facility-wide, with no error
  anywhere. Root-caused live (process census, `py-spy dump`, direct
  `register`/`publish_live_observation` replay) rather than assumed from the
  symptom; confirmed by reproducing the exact "renders once after a restart,
  then never again" signature the operator reported. Fix: `mux_daemon.py`'s
  resident loop now also republishes on an independent
  `LIVE_MAPPING_BACKSTOP_INTERVAL_S` (20s, safely under the 45s staleness
  window) keep-alive cadence, regardless of generation. The decision itself
  (`live_mapping_republish_due`) is a new pure helper in the sibling
  `mux_mapping_registry` module -- extracted there, not inlined in
  `mux_daemon.py`, purely to keep that already-999-line module under this
  repo's shrink-only module-size ceiling. New regression tests: an
  integration test proving a second republish fires with no generation
  change at all, and unit coverage of the extracted helper's six decision
  branches. Full `worktree-manager` suite: 1562 passed, 4 skipped, same
  pre-existing unrelated simulated-failure warning.
- **2026-09-27** — Landed Phase 3d Step 6, PR
  [#4350](https://github.com/ThomasMichon/copilot-extensions/pull/4350).
  Cut `worktree_manager.production_picker.picker_tui.data_local` over to the
  Step 5 `picker-reconcile-local --json` seam: the authoritative local classify
  load and per-row Refresh now make one batched `engine_group_c` call and map
  the returned row subset straight onto the Picker's raw row vocabulary
  (`pr`/`prs`/`pr_count`, `session_bound_live`, `session_lock_live` /
  `session_lock_stale` / `stale_lock_pids`, `mux_session` /
  `mux_clients` / `mux_attached`) before normalization, while cache-only first
  paint keeps the existing cached-row shape and deliberately does **not** wait
  on that batch. Replaced the old post-load reconcile pair
  (`_start_pr_reconcile()` / `_start_bound_live_reconcile()`) with this single
  load-time batch path, so one setup/reload epoch schedules one Group C batch
  instead of two background threads that raced the same tracking writes. Drained
  the remaining config-proxy tail by replacing
  `worktree_manager.production_picker.config` with Manager-owned direct-file
  readers/caching used by `data_local.py`, `data_ssh.py`, `engine_loading.py`,
  `profiles_io.py`, `roster.py`, and `picker_tui/__init__.py`; also repointed
  `engine_worktree_actions.py`'s Actions-menu liveness reverify to the targeted
  Group C engine call instead of `sessions.verify_worktree_active()` /
  `tracking.stamp_mux_live()`. Validation: new regression coverage added for
  behavior preservation, cache-first first paint skipping the batch, one
  reconcile batch per setup/reload epoch, and O(1) subprocess count across
  multi-row loads; full `worktree-manager/tests/production_picker/` passed
  twice (`700 passed, 2 skipped` both runs); full `worktree-manager` suite
  excluding the two standing hangs matched the current machine baseline at
  `1439 passed, 6 skipped, 11 failed` (the same symlink-privilege families plus
  the already-upstream `test_mux_daemon` failure on this Windows machine); full
  `agent-worktrees` suite remained in its existing
  unrelated-failure envelope (same families as Step 5; exact final count from
  the validating run recorded on the PR); `ruff check --select F,E9` passed for
  both packages; `check-install-contract.py` and
  `check-version-consistency.py` passed; `check-version-bump.py` still reports
  the unrelated pre-existing drift on `delegation-guidance`, `efforts`,
  `harness-knowledge`, and `wsl-setup`. With Step 6 done, only Step 7 (delete
  `_engine_runtime.py` + the remaining proxy shims and add the regression
  guard) remains for Phase 3d.
- **2026-09-27** — Landed Phase 3d Step 7, PR
  [#4357](https://github.com/ThomasMichon/copilot-extensions/pull/4357).
  Deleted the production Picker's last in-package engine import seam:
  `worktree_manager.production_picker._engine_runtime` moved to the top-level
  compatibility helper `worktree_manager.agent_worktrees_runtime`, the dead
  `production_picker.config` / `pr_ops` / `reclaim` / `sessions` /
  `tracking` pass-through shims were removed, and `housekeeping.py` plus the
  remaining transplant/conftest callers now import the top-level helper
  instead of anything under `production_picker`. Added a focused
  `test_production_picker_runtime_boundary.py` guard that proves those deleted
  files stay gone and that no Python source under
  `worktree_manager.production_picker` imports `agent_worktrees` directly.
  Validation: focused runtime-boundary/transplant/housekeeping/regression
  lanes green (`367 passed` for the main Step 7 slice, plus `63 passed` for the
  config-reader/runtime-helper follow-up lane); full `worktree-manager` suite
  matched the current unrelated Windows baseline at
  `14 failed, 1552 passed, 7 skipped, 1 warning`; `ruff check --select F,E9`
  passed; Phase 3d's ordered plan is now fully complete.
- **2026-09-27** — Landed Phase 3d Step 5, PR
  [#4327](https://github.com/ThomasMichon/copilot-extensions/pull/4327).
  Added Group C's additive, unused-at-first engine seam instead of cutting the
  Picker over yet: `agent_worktrees.picker_reconcile_cli` now owns the new
  `picker-reconcile-local --json` verb, which runs the existing engine-side
  record loop in one coarse-grained call (list records, best-effort active-PR
  reconcile, bound/mux/session-lock readback, engine-owned bound/mux stamp
  writes, then a `rows` + `summary` payload whose field names intentionally
  mirror the Picker's current Group C list-row vocabulary). On the Manager
  side, `worktree_manager.production_picker.engine_group_c` adds the matching
  version-skew-aware client wrapper and payload parser, but `data_local.py`
  remains untouched for Step 6's later cutover. Kept the lock contract exactly
  where the plan required it: no new cross-record/global tracking lock, no
  provider/network call while holding a batch-wide write lock, and no logic
  reimplementation in the client -- only the existing engine-owned
  `tracking`/`pr_ops`/`reclaim`/`sessions` helpers orchestrated server-side.
  Validation: targeted new contract tests green
  (`plugins/agent-worktrees/tests/test_picker_reconcile_local.py`,
  `worktree-manager/tests/production_picker/test_engine_group_c.py`); full
  `worktree-manager` suite (excluding the two standing hangs
  `test_data_ssh_sources.py` / `test_launch_trace.py`) finished at `1294
  passed, 2 skipped, 10 failed`, staying within the effort's unrelated baseline
  envelope; full `agent-worktrees` suite finished at `5738 passed, 50 skipped,
  6 failed`, likewise only in pre-existing/environmental families on this
  machine (`test_launch_cmd`, `test_lazy_dispatch`, `test_module_invocation`,
  `test_mux_status_link`, `test_session_conduct`). `ruff check --select F,E9`,
  `python tools/check-install-contract.py`, and
  `python tools/check-version-consistency.py` passed; `python
  tools/check-version-bump.py` still reports the same pre-existing unrelated
  plugin-version drift on `delegation-guidance`, `efforts`,
  `harness-knowledge`, and `wsl-setup`.

- **2026-09-27** — Claiming Phase 3d Step 5 ("Add Group C's batched
  reconcile-and-stamp verb in agent-worktrees, unused at first") per
  [`phase-3d-engine-runtime-retirement.md`](phase-3d-engine-runtime-retirement.md)'s
  ordered implementation plan. Working solo per standing operator directive;
  recorded here per this effort's own Coordination-section claiming
  discipline since #352 is closed.

- **2026-09-27** — Landed Phase 3d Step 4, PR
  [#4324](https://github.com/ThomasMichon/copilot-extensions/pull/4324).
  Performed the crisp Group B cutover that Step 2/3 were staged for:
  `worktree_manager.production_picker.runner` now binds
  `context.ProjectBootstrap` from `engine_group_b.picker_bootstrap()`, switches
  cwd only from that payload's engine-owned decision, runs background
  stale-anchor repair through `engine_group_b.repair_stale_anchor()`, and
  routes its orphan-reap / managed-worktree / launcher-shell / finished-session
  / monitor-root lifecycle through the Manager-owned
  `production_picker.housekeeping` + `monitor_roots` modules instead of
  `agent_worktrees.__main__`. `worktree_manager.__main__`'s old-engine remote
  compatibility path is explicitly retired rather than reimplemented: an engine
  too old for `resolve --json --machine ...` now fails clearly instead of
  silently importing private engine helpers. The parent-side bootstrap binding
  is now the authoritative project identity read by downstream
  `context.project()` / `context.project_bootstrap()` consumers, so Group B is
  fully done and only Group C remains in Phase 3d. Real boundary shrink: the
  post-Group-A 6-module proxy/shim surface is down to 5
  (`config`, `pr_ops`, `reclaim`, `sessions`, `tracking`) because
  `production_picker.__main__` is deleted, while the total remaining live
  in-process engine-module surface is 9 once the Step 3 housekeeping-owned
  imports (`activity`, `gc`, `reap_cli`, `status_monitor_runtime`) are counted
  too. Validation: targeted Group B seam + cutover regressions passed, including agent-worktrees'
  `test_context_resolution.py` bootstrap/repair coverage plus
  worktree-manager's `test_engine_group_b.py`, `test_housekeeping.py`,
  `test_production_picker_transplant.py`, `test_picker_app.py`, and
  `test_picker_preview_mode.py`; full `worktree-manager` suite (excluding the
  two standing hangs `test_data_ssh_sources.py` / `test_launch_trace.py`)
  matched the current unrelated baseline at `1290 passed, 2 skipped, 11
  failed`; full `agent-worktrees` suite stayed red only in unrelated existing
  families on this machine at `5732 passed, 50 skipped, 9
  failed` (`test_launch_cmd`, `test_lazy_dispatch`, `test_module_invocation`,
  `test_mux_status_link`, `test_registration_home`, `test_session_conduct`,
  `test_status_monitor_windows`); `ruff check --select F,E9`,
  `tools/check-install-contract.py`, and
  `tools/check-version-consistency.py` passed; `tools/check-version-bump.py`
  still reports the same pre-existing unrelated unbumped-plugin drift on
  `delegation-guidance`, `efforts`, `harness-knowledge`, and `wsl-setup`.

- **2026-09-27** — Claiming Phase 3d Step 4 ("Perform the remaining Group B
  cutover in one crisp PR" — switching `runner.py`'s bootstrap, stale-anchor
  repair, remote planning, and housekeeping/monitor lifecycle over to the
  Step 2/3 seams, plus resolving `worktree_manager.__main__`'s old-engine
  remote fallback) per
  [`phase-3d-engine-runtime-retirement.md`](phase-3d-engine-runtime-retirement.md)'s
  ordered implementation plan. Working solo per standing operator directive;
  recorded here per this effort's own Coordination-section claiming
  discipline since #352 is closed.

- **2026-09-27** — Landed Phase 3d Step 3, PR
  [#4323](https://github.com/ThomasMichon/copilot-extensions/pull/4323).
  Ported Group B's Picker-owned lifecycle housekeeping into Worktree Manager
  without cutting the live runner over yet: added
  `worktree_manager.production_picker.housekeeping` for the orphan-mux reap
  plus the managed/launcher/finished lifecycle-boundary sweep wrappers, and
  added `worktree_manager.production_picker.monitor_roots` as the Manager-owned
  home for Picker heartbeat roots while preserving the existing engine-consumed
  `status-monitor-roots.d/picker-*.json` schema. Nailed the Step 4 ownership
  split up front instead of deferring it: Manager-owned mux-session names come
  from Worktree Manager's live `mux-mapping.json` registry, Manager-owned
  worktree rows are that registry's ids plus rows whose resolved
  `execution_leg.provider` is `ahp`, and Manager-owned launcher shells are the
  orphan-shell candidates whose positive launcher signature resolves to the
  relocated `worktree-manager/bin/launch-session.*` / `pane-wrapper.*` path.
  Explicitly kept this slice additive-only: `production_picker.runner` still
  uses the old compatibility path and agent-worktrees' live sweeper behavior is
  unchanged until Step 4 activates the Manager-owned lane. Validation:
  targeted new Group B parity tests green; full `worktree-manager` suite
  (excluding the two standing hangs `test_data_ssh_sources.py` /
  `test_launch_trace.py`) matched the current unrelated baseline at `1274
  passed, 2 skipped, 13 failed`; full `agent-worktrees` suite matched the
  current unrelated baseline at `5733 passed, 50 skipped, 7 failed`; `ruff
  check --select F,E9` passed for both packages; `python
  tools/check-install-contract.py` and `python tools/check-version-consistency.py`
  passed; `python tools/check-version-bump.py` still reports the same
  pre-existing unrelated unbumped-plugin drift on `delegation-guidance`,
  `efforts`, `harness-knowledge`, and `wsl-setup`.

- **2026-09-27** — Claiming Phase 3d Step 3 ("Reimplement Group B's
  Picker-owned lifecycle sweeps directly in worktree-manager, additive
  first" — `reap_orphan_mux_sessions`, `_sweep_managed_on_exit`,
  `_sweep_launcher_shells_on_exit`, `_sweep_finished_sessions_on_cadence`,
  `_start_picker_monitor_root`) per
  [`phase-3d-engine-runtime-retirement.md`](phase-3d-engine-runtime-retirement.md)'s
  ordered implementation plan. Working solo per standing operator directive;
  recorded here per this effort's own Coordination-section claiming
  discipline since #352 is closed.

- **2026-09-27** — Landed Phase 3d Step 2, PR
  [#4322](https://github.com/ThomasMichon/copilot-extensions/pull/4322).
  Promoted Group B's project/config/ssh ownership split into a narrow public
  engine seam without cutting over the live `runner.py` path yet: added
  `agent-worktrees`' versioned `picker-bootstrap --json` bootstrap verb
  (authoritative project + cwd-switch + default live/local mode) and
  `repair-stale-anchor --json` targeted repair action, documented both in
  `plugins/agent-worktrees/docs/engine-picker-contract.md`, and confirmed the
  existing `resolve --json` remote-launch payload already supplied the Picker's
  machine/environment answer so Step 2 needed no speculative shape growth
  there. On the Manager side, added
  `worktree_manager.production_picker.engine_group_b` and extended
  `production_picker.context` with an authoritative `ProjectBootstrap` binding
  record so downstream Picker/data helpers can consume the parent-owned
  identity once Step 4 performs the actual cutover. Explicitly kept this slice
  additive-only per plan: no `runner.py` call site moved in this PR, and the
  compatibility boundary remains live until Step 4. Validation: targeted Group
  B seam tests green on both sides; full `worktree-manager` suite (excluding
  the two standing hangs `test_data_ssh_sources.py` /
  `test_launch_trace.py`) matched the current unrelated baseline at `1274
  passed, 2 skipped, 13 failed`; full `agent-worktrees` suite on this machine
  remained red only in unrelated baseline families at `5733 passed, 50
  skipped, 7 failed` (`test_launch_cmd`, `test_lazy_dispatch`,
  `test_module_invocation`, `test_mux_status_link`,
  `test_profile_assignment`, `test_session_conduct`); `ruff check --select
  F,E9`, `tools/check-install-contract.py`, and
  `tools/check-version-consistency.py` passed; `check-version-bump.py` still
  reports the same pre-existing unrelated unbumped-plugin drift on
  `delegation-guidance`, `efforts`, `harness-knowledge`, and `wsl-setup`.

- **2026-09-27** — Claiming Phase 3d Step 2 ("Promote Group B's
  project/config/ssh decisions to a narrow public CLI seam, additive only")
  per
  [`phase-3d-engine-runtime-retirement.md`](phase-3d-engine-runtime-retirement.md)'s
  ordered implementation plan. Working solo per standing operator directive;
  recorded here per this effort's own Coordination-section claiming
  discipline since #352 is closed.

- **2026-09-27** — Landed Phase 3d Group A, PR
  [#4317](https://github.com/ThomasMichon/copilot-extensions/pull/4317).
  Added/pinned the low-frequency public read seam the Picker still needed
  (`picker-paths --json`, existing `state-root --json`, additive
  `stage-update --indicator-state --json`), documented it in
  `plugins/agent-worktrees/docs/engine-picker-contract.md`, and cut
  `pivot_manifest.py` plus the cosmetic `update_stage.py` glyph reader over
  to Worktree Manager-side subprocess helpers instead of the in-process
  `_engine_runtime.py` import path. `update_stage` stayed in scope exactly as
  the Group A table planned: the new reader degrades older engines to
  `"idle"` rather than failing startup, while `pivot_manifest.py` now keeps its
  `state-root` visibility gate on the engine-owned `--json` surface too.
  Real boundary shrink: `state_root` is no longer reached through
  `engine_module(...)`, and `update_stage.py` no longer imports the engine at
  all; the remaining live boundary surface is the explicit Group B/C set
  (`config`, `pr_ops`, `reclaim`, `sessions`, `tracking`, `__main__`). New
  regression coverage proves the verb payloads and caller behavior, including
  the older-engine graceful-degradation path for the glyph. Validation:
  targeted Group A tests green; full `agent-worktrees` suite matched the
  current unrelated baseline at `5733 passed, 50 skipped, 5 failed`; full
  `worktree-manager` suite (excluding the two standing hangs:
  `test_data_ssh_sources.py` / `test_launch_trace.py`) matched the current
  unrelated baseline at `1260 passed, 2 skipped, 13 failed`; `ruff check
  --select F,E9` passed; `check-install-contract.py` and
  `check-version-consistency.py` passed; `check-version-bump.py` still reports
  the same pre-existing unrelated unbumped-plugin drift already present on
  `origin/dev`.

- **2026-09-27** — Wrote Phase 3d's ordered implementation plan
  ([`phase-3d-engine-runtime-retirement.md`](phase-3d-engine-runtime-retirement.md)),
  replacing the old "evidence only + open questions" shape with the same
  PR-sized additive-seam → cutover → cleanup structure used by Phases 3b,
  3c, and 3e. Recorded today's operator decisions directly in the doc:
  Group B splits at the true ownership boundary (Picker process-lifecycle
  sweeps move into worktree-manager; project/config/ssh resolution stays
  engine-owned behind a new public CLI seam), Group C's batched
  reconcile-and-stamp verb is engine-owned, and the former Phase 3c
  sequencing question is now satisfied by PR
  [#4278](https://github.com/ThomasMichon/copilot-extensions/pull/4278).
  The ordered plan now sequences 7 remaining implementation steps, records
  Group D as already closed by Phase 3e, and leaves no design questions
  open before implementation begins.
- **2026-09-27** — Claiming Phase 3d's Group A conversion (the "Design +
  convert the low-frequency, one-shot CLI-root reads" checkbox): convert
  `pivot_manifest.py`'s in-process `config.install_dir()` / `config._home()`
  / `state_root_module.resolve_state_root(...)` reads to `--json` CLI verbs
  over the engine boundary, per
  [`phase-3d-engine-runtime-retirement.md`](phase-3d-engine-runtime-retirement.md)'s
  Group A disposition. Working solo per standing operator directive;
  recorded here per this effort's own Coordination-section claiming
  discipline since #352 is closed.
- **2026-09-27** — Landed Phase 3c Step 5, PR
  [#4278](https://github.com/ThomasMichon/copilot-extensions/pull/4278).
  Renamed the old synchronous Picker setup helper to
  `setup_sync_for_tests()` so no production code or comment surface still
  advertises an inline `setup()` UI entrypoint, updated every intentional
  synchronous test call site to the explicit test-only name, and refreshed the
  related source/test commentary to describe the final worker-owned production
  path instead of the pre-cutover shape. Audited the Step 1 seams before
  deleting anything and kept only the helpers that still have a real shared
  job: `_prime_setup_reload()` and `_invalidate_setup_reload_caches()` remain
  because both `_start_setup_reload_worker()` and the synchronous test helper
  still need them; no production compatibility wrapper remains in front of the
  async setup/reload path. Recorded the optional richer built-in progress
  follow-on explicitly as issue
  [#4274](https://github.com/ThomasMichon/copilot-extensions/issues/4274)
  instead of leaving the proposal implicit in the phase plan. Validation:
  targeted picker tests green; full `tests/production_picker/` suite matched
  the standing Windows baseline twice back-to-back at `775 passed, 3 skipped,
  3 failed` (unchanged known provider-source failures), and the full
  `worktree-manager` suite matched the Step 4 baseline shape at
  `1500 passed, 7 skipped, 13 failed` (unchanged known failures: the same 3
  provider-source failures plus 10 Windows symlink-privilege failures).

- **2026-09-27** — Landed Phase 3c Step 4, PR
  [#4164](https://github.com/ThomasMichon/copilot-extensions/pull/4164).
  Promoted the standing "no blocking I/O on the render thread" contract from
  an implicit test pattern into an explicit Phase 3c boundary: added
  blocking-gate tests in
  `worktree-manager/tests/production_picker/test_setup_reload_epoch.py` for
  the manual `r` reload handler plus the config-section and contributed
  worktree-action rescan callbacks, all instrumented so a direct synchronous
  `setup()` / `_collect_setup_payload()` call would block the test and fail
  with a call-site-specific message. Reused the already-landed blocked-mount
  regression from Step 2 as the fourth setup/reload call-site guard, and
  updated the existing Actions-menu liveness and steer-submit offload tests to
  say explicitly that they are part of the same Phase 3c UI-thread boundary.
  Evaluated the plan's "default-on broad fixture" option and rejected it on
  purpose: the interactive picker suite still contains many intentional direct
  `screen.setup_sync_for_tests()` tests covering lower-level synchronous seams,
  so a suite-wide autouse guard would destabilize unrelated tests and obscure
  the actual regression surface. Validation: targeted Phase 3c boundary tests green; full
  `worktree-manager` suite matched the Windows baseline at `1498 passed,
  7 skipped, 13 failed` (unchanged known failures: the same 3 unrelated
  provider-source failures in `test_data_ssh_sources.py` plus 10 Windows
  symlink-privilege failures).

- **2026-09-26** — Landed Phase 3c Step 3, PR
  [#4144](https://github.com/ThomasMichon/copilot-extensions/pull/4144).
  Cut the last three render-thread setup callers over to the Step 1/2
  epoch-guarded reload worker: manual `r` reload in `engine_input.py`, the
  config-section completion rescan in `engine_pivot_actions.py`, and the
  contributed worktree-action completion rescan in
  `engine_worktree_actions.py` now all call
  `_start_setup_reload_worker()` instead of synchronous `setup()`. Re-grepped
  `worktree-manager/src/worktree_manager/production_picker/` afterward to
  confirm no production `self.setup(` render-thread caller remains. Added
  deterministic Step 3 race coverage in
  `tests/production_picker/test_setup_reload_epoch.py`: rapid repeated `r`
  reloads no longer block while a stale worker is outstanding, and both
  config-section/worktree-action rescan vs manual reload orderings apply only
  the newer epoch's payload with no stale pivot/row flash. Validation:
  targeted `test_setup_reload_epoch.py` green; full `worktree-manager` suite
  matched the known Windows baseline at `1493 passed, 7 skipped, 13 failed`
  (unchanged known failures: the same 3 unrelated provider-source failures in
  `test_data_ssh_sources.py` plus 10 symlink-privilege failures on this
  machine). Bumped `worktree-manager` `0.1.0-dev85` -> `0.1.0-dev86`.

- **2026-09-26** — Landed Phase 3c Step 2, PR
  [#4007](https://github.com/ThomasMichon/copilot-extensions/pull/4007).
  Cut the initial non-live picker mount over to the Step 1 epoch-guarded
  worker: `engine_loading.on_mount()` now paints `_setup_skeleton()` and
  launches `_start_setup_reload_worker()` off-thread instead of calling
  synchronous `setup()`, while the existing live path stays structurally
  unchanged. Added Step 2 regression coverage proving the cold non-live mount
  paints before a blocked `src.load()` returns and that the eventual applied
  state exactly matches the old synchronous `setup()` result, then taught the
  capture/TUI test harnesses to wait for the async non-live mount result
  where they had previously assumed a synchronous ready screen. Explicitly
  re-verified scope before merge: the remaining direct `setup()` callers are
  still only the `r` reload handler plus the two post-action rescans, so Step
  3 stays isolated. Validation: targeted
  `tests/production_picker/test_setup_reload_epoch.py` +
  `test_picker_first_paint.py` green; full `worktree-manager` suite matched
  the rebased machine baseline at `1478 passed, 7 skipped, 13 failed`
  (unchanged known failures: 3 unrelated provider-source failures plus 10
  Windows symlink-privilege failures).
  **Step 4 lands in PR
  [#4164](https://github.com/ThomasMichon/copilot-extensions/pull/4164)**:
  added explicit blocking-gate coverage for the three remaining UI-thread
  setup/reload callbacks (`engine_input.py`'s `r` handler plus the config-
  section and worktree-action rescan callbacks), all with
  `_collect_setup_payload()` deliberately blocked so the tests fail if any
  callback is rewired back to synchronous `setup()` or another direct-I/O
  path. Kept Step 2's blocked-mount regression as the fourth call-site
  guard and explicitly marked the existing Actions-menu liveness and
  steer-submit offload tests as part of the same standing Phase 3c
  non-blocking boundary. Deliberately did **not** make this a default-on
  autouse fixture for the whole interactive-picker suite: many existing
  tests intentionally call `screen.setup()` directly to exercise lower-
  level synchronous seams, so a broad fixture would destabilize unrelated
  tests instead of guarding just the UI-thread boundary. Validation:
  targeted boundary tests green; full `worktree-manager` suite matched the
  updated Windows baseline at `1498 passed, 7 skipped, 13 failed`
  (unchanged known failures: the same 3 provider-source failures plus 10
  symlink-privilege failures on this machine).

- **2026-09-26** — Landed Phase 3c Step 1, PR
  [#3903](https://github.com/ThomasMichon/copilot-extensions/pull/3903).
  Added the additive-only epoch-guarded setup/reload seam in
  `worktree-manager`: `PickerScreen` now owns `_setup_epoch`,
  `_setup_applied_epoch`, `_setup_failed_epoch`, and the dedicated
  `_start_setup_reload_worker()` launcher, while synchronous `setup()` was
  split into `_prime_setup_reload()` + `_collect_setup_payload()` +
  `_invalidate_setup_reload_caches()` + `_apply_setup_payload()` with no
  caller cutover yet. Added the reusable async test helper
  `wait_for_current_setup_epoch_applied` in
  `tests/production_picker/conftest.py`, and new
  `test_setup_reload_epoch.py` contract coverage for supersession,
  stale-failure, teardown-drop, and atomic-apply behavior. Explicitly
  re-verified scope discipline before merge: `engine_loading.on_mount`, the
  `r` reload handler in `engine_input.py`, `_run_config_section()`'s `_done`,
  and `_run_wt_action()`'s `_done` all still call synchronous `setup()`
  exactly as before. Copilot review surfaced three real follow-up fixes
  before merge: synchronous `setup()` now advances the shared epoch too (so
  it supersedes any older worker), discarded live payloads cancel their
  unowned `LiveLoader` before returning, and the supersession tests now
  serialize the first worker's start before launching the second so the
  contract is deterministic instead of scheduler-dependent. A later review
  round found one more real teardown race: a live setup payload whose
  callback had already been marshalled to the UI thread could still leak its
  `LiveLoader` if the picker unmounted before that callback ever ran, so the
  final tree now tracks pending setup payloads explicitly, disposes them from
  `on_unmount`, and covers both the marshal-failed and callback-dropped
  disposal paths in the new test file. The last follow-up was ownership
  transfer itself: applying a newer live payload now cancels the previously
  owned loader before replacing `self.loader`, with a dedicated regression
  test so repeated live reloads cannot accumulate orphaned loaders. The final
  review round also closed the apply-failure ownership gap by disposing a
  collected payload if `_invalidate_setup_reload_caches()` or
  `_apply_setup_payload()` raises after the payload has already been popped out
  of the pending map, including the still-live synchronous `setup()` path.
  Validation:
  targeted
  `tests/production_picker/test_setup_reload_epoch.py` +
  `test_picker_first_paint.py` green; full `worktree-manager` suite matched
  the machine's known baseline at `1473 passed, 7 skipped, 13 failed`
  (unchanged: 3 unrelated `test_data_ssh_sources.py` failures plus 10
  Windows symlink-privilege failures).

- **2026-09-26** — Closed the final two Phase 3b follow-on checkboxes, PR
  [#3891](https://github.com/ThomasMichon/copilot-extensions/pull/3891).
  Investigation found the functional work was already in place: the
  production Picker's local Open/Resume submenu and New Worktree options
  already modelled `No Mux` and `AHP` as independent toggles, and
  agent-worktrees already kept a reader-side `session_backend:` compatibility
  path through `derive_execution_leg()` / `execution-leg get`. The gap was
  proof, not implementation. Added the missing combined-toggle regressions in
  `worktree-manager`, plus the specific `AHP+no-mux` launch-path regression
  that completes the explicit four-combination matrix alongside the pre-
  existing `direct+mux`, `direct+no-mux`, and `AHP+mux` tests, and
  revalidated the legacy binding contract against the current
  `agent-worktrees` tests. Marked Phase 3b **Done** in the plan: both
  remaining explicit bullets are now checked, and there is no further open
  item in the Phase 3b section itself.
  Validation: focused `worktree-manager` Picker/launch toggle tests green;
  focused `agent-worktrees` legacy-execution-leg compatibility tests green;
  `python tools/check-install-contract.py` green; full `agent-worktrees`
  suite green (`5605 passed, 50 skipped, 1 warning`); full
  `worktree-manager` suite still matches the repo's unrelated failure class
  in `tests/production_picker/test_data_ssh_sources.py` and, on this
  Windows machine without symlink-creation privilege, additionally hits 10
  symlink-fixture failures before any changed-code regression (`1464 passed,
  7 skipped, 13 failed` total).

- **2026-09-26** — Re-authored the Phase 3c planning doc to match the
  Phase 3b/3e ordered-slice discipline and renamed it to
  [`phase-3c-non-blocking-io.md`](phase-3c-non-blocking-io.md). The new
  plan now carries the same reviewed shape as the other active sub-slice
  docs: governing-vision header, explicit "Why this needs its own ordered
  plan" hazard statement, evidence-based current-state inventory, precise
  target end-state, additive-seam → cutover → cleanup ordered steps,
  contract/step/end-to-end validation, a bounded cross-repo proposal note,
  and explicit Non-Goals. No implementation landed in this pass; Phase 3c
  remains planning-only, but the README status line now says so plainly.

- **2026-09-26** — Merged Phase 3b Slice 2 Sub-slice 3 Step 6, PR
  [#3865](https://github.com/ThomasMichon/copilot-extensions/pull/3865).
  Completed the final cleanup/deletion pass for the resident Mux
  status-monitor split: manager-owned sessions no longer repopulate
  `status-monitor.d`, the resident monitor no longer falls back to direct
  `_monitor_mux_set()` writes for that lane, and Worktree Manager now
  republishes live mux mappings when either daemon restarts so the cleanup
  preserves restart recovery without reviving the legacy writer path.
  Review-round fixes were small but real: rebasing onto newer `dev` forced a
  second standalone version bump (`worktree-manager` `0.1.0-dev82` →
  `0.1.0-dev83`), and the new registry-pruning helpers had to be extracted
  out of `__main__.py` just enough to satisfy this repo's shrink-only
  module-size ceiling. Validation: new end-to-end scenario tests now cover
  the full Step 6 gate explicitly (`worktree-manager`: manager-owned
  launch/teardown wire round-trip, daemon-restart republish, and
  resident-monitor-restart republish; `agent-worktrees`: manager-owned
  registry-prune/no-direct-write plus the existing zero-provider fallback
  lane). Full `worktree-manager` suite: 1443 passed, 7 skipped, same 3
  pre-existing unrelated failures in
  `tests/production_picker/test_data_ssh_sources.py`; full
  `agent-worktrees` suite: 5604 passed, 50 skipped, 1 warning. This closes
  all 6 ordered steps of Sub-slice 3; Phase 3b itself remains **In
  progress** because the broader optional same-machine AHP backend and the
  remaining Phase 3b Mux follow-on items are still open in the Plan above.

- **2026-09-26** — Merged Phase 3b Slice 2 Sub-slice 3 Step 5, PR
  [#3859](https://github.com/ThomasMichon/copilot-extensions/pull/3859).
  Retired the per-session updater as the registration-time shim for
  Manager-owned mux sessions: `register-session` / `bind-session` now read
  the live Worktree Manager mux-mapping registry, register the matching mux
  session with the resident status monitor directly, and skip spawning
  `status-updater` entirely on that Manager-owned lane. Unmanaged sessions
  keep the old reseed path, and monitor-disabled / monitor-unavailable cases
  still fall back to the per-session updater exactly as before. Copilot
  review found no code defect in the implementation itself; the only follow-up
  was adding the required Documentation impact statement to the PR body
  before merge. Validation: new targeted registration-path regressions green
  (`agent-worktrees`: 112 passed across `test_register_session.py`,
  `test_bind_session.py`, and `test_status_updater.py`); full
  `worktree-manager` suite: 1440 passed, 7 skipped, same 3 pre-existing
  unrelated failures in `tests/production_picker/test_data_ssh_sources.py`;
  full `agent-worktrees` suite: 5603 passed, 50 skipped, 1 warning. Step 5
  of this sub-slice's 6-step ordered plan is complete; only Step 6 (final
  cleanup/deletion) remains open.

- **2026-09-26** — Merged Phase 3b Slice 2 Sub-slice 3 Step 4, PR
  [#3849](https://github.com/ThomasMichon/copilot-extensions/pull/3849).
  Cut the resident monitor's served-session/reconcile loop over to the new
  Manager-fed observation source for Manager-owned mux sessions:
  `_monitor_sweep()` now serves the union of unmanaged
  `status-monitor.d`+direct-scan sessions and Manager-owned managed-cache
  sessions; `pane_reaper.observe()` follows that same union; and
  `ResidentSessionReconciler.observe_mux()` now sees Manager-owned live mux
  sessions from that union whenever a successful direct scan is available,
  instead of assuming every served session came from `_monitor_list_sessions()`.
  Copilot review surfaced four real follow-up fixes before merge: clearing
  stale `ctx_done`/`published` state on cache-only managed-session
  incarnation changes; preserving the complete-snapshot contract by never
  sending a cache-only managed subset to `observe_mux()` with no successful
  direct scan; selecting the fresh live cache row per mux session when two
  `(project, worktree_id)` entries reuse a session name; and falling back to
  the direct mux incarnation when Manager omits `session_incarnation`, plus
  a lazy `mux_link` import so `worktree_status_audit` keeps its
  dependency-light import boundary. Validation: targeted managed-monitor
  regressions green after the final fixes (`agent-worktrees`: 7 passed for
  `test_status_monitor.py`/`test_worktree_status_audit.py`, plus 11 passed
  for `test_active_paths.py` + `test_verify_worktree_active.py` earlier in
  the slice). Full `worktree-manager` suite: 1438 passed, 7 skipped, same 3
  pre-existing unrelated failures in
  `tests/production_picker/test_data_ssh_sources.py`. Full
  `agent-worktrees` suite on the final tree passed cleanly at 5600 passed,
  50 skipped, 1 warning, which is better than this slice's earlier known
  `tests/test_update_stage.py` baseline. Step 4 of this sub-slice's 6-step
  ordered plan is complete; Steps 5-6 (updater-shim retirement and final
  cleanup) remain open.

- **2026-09-26** — Merged Phase 3b Slice 2 Sub-slice 3 Step 3, PR
  [#3825](https://github.com/ThomasMichon/copilot-extensions/pull/3825).
  Wired the managed-session cutover all the way through: `worktree-manager`
  launch/join and pane-exit paths now register/remove Manager-owned mux
  mappings through `mux-daemon`, ensure the companion daemon, and publish
  `mux-live-v1` upserts/tombstones into `agent-worktrees`; the resident
  `status-monitor` now routes rendered `@aw_*` payloads back through
  `mux-status-v1` for sessions present in the managed-mux cache while
  leaving unmanaged sessions on the old direct writer path. Review surfaced
  five real follow-up fixes before merge: bumping the standalone Manager
  payload to `0.1.0-dev80` so the launcher/daemon cutover actually ships,
  moving revisionless mapping allocation under the registry lock so two
  concurrent register calls cannot reuse the same `mapping_revision`,
  adding direct wire-client coverage for `agent_worktrees.mux_status_link`,
  scrubbing long-lived daemon spawns of relayed auth tokens, and updating
  `mux_daemon.py`'s lifecycle docstring now that the launch path is live.
  Validation: targeted suites green after the final review
  fixes (`agent-worktrees`: 108 passed; `worktree-manager`: 111 passed).
  Full `worktree-manager` suite after those fixes: 1436 passed, 7 skipped,
  same 3 pre-existing unrelated failures (`tests/production_picker/
  test_data_ssh_sources.py`). Full `agent-worktrees` suite on this slice's
  final code path remained at the same known baseline failures outside this
  change area: 5556 passed, 50 skipped, 4 pre-existing unrelated failures
  (`tests/test_update_stage.py`); after the final module-size-only helper
  extraction and the added direct client tests, the touched monitor/wire
  surfaces reran green as targeted tests before merge. Step 3 of this
  sub-slice's 6-step ordered plan is complete; Steps 4-6 (served-set
  cutover, updater-shim retirement, and final cleanup) remain open.

- **2026-09-26** — Merged Phase 3b Slice 2 Sub-slice 3 Step 2, PR
  [#3724](https://github.com/ThomasMichon/copilot-extensions/pull/3724)
  (squash-merged into `dev` as `7fe5d2017`). Went through 5 review rounds
  before landing, each fixing genuine concurrency/data-integrity findings
  surfaced against the daemon/registry design: tombstone-vs-delete
  semantics and two distinct stale-resurrection paths for `remove()`;
  serializing the daemon's whole check-then-spawn boot sequence, then
  replacing that with a true single-instance lease held for the daemon's
  entire lifetime (closing a direct-CLI-run bypass and a residual
  TOCTOU on exit-time lock cleanup); a coalescing-key derivation
  (`status_push_key`) that folds in both `rendered_at` and a values hash so
  distinct renders never silently coalesce; a per-worktree in-process lock
  plus a **durable** (registry-persisted, restart-surviving) last-applied
  ordering fence so concurrent/out-of-order renders can't violate
  last-write-wins; revalidating a mapping's mux session against the real
  mux server before ever writing to it; rechecking the mapping fresh
  immediately before the actual apply (not from an earlier snapshot);
  fencing in-flight handlers before shutdown/lease release; and two
  further registry-fencing refinements (equal-revision resurrection past a
  tombstone, and not inheriting a stale ordering fence across a genuinely
  new mapping incarnation). Two narrower findings (bounding concurrent
  request handling in `work_coalescing_singleton` itself) were declined
  with rationale -- a pre-existing characteristic of the shared library
  affecting every existing caller, not a regression from this PR, tracked
  as a follow-up rather than blocking. Split `MuxMappingRegistry` out into
  its own `mux_mapping_registry.py` module partway through, since the
  round-4 additions pushed `mux_daemon.py` past this repo's 1000-line
  module cap. Final state: 91 targeted tests green (including real
  two-concurrent-callers and two-racing-daemons spawn-race tests, a real
  handler-in-flight-during-shutdown test, and a durable-fence-survives-
  restart test); full `worktree-manager` suite 1431 passed, 7 skipped,
  same 3 pre-existing unrelated failures (`test_data_ssh_sources.py`,
  untouched by this change) throughout. Step 2 of this sub-slice's 6-step
  ordered plan is complete; Steps 3-6 (the managed-session cutover, the
  resident monitor's observation-source switch, updater retirement, and
  final cleanup) remain unclaimed.

- **2026-09-25** — Landed Phase 3b Slice 2 Sub-slice 3 Step 2 (Worktree
  Manager mux-companion daemon + mapping registry, still off the main
  launch path), PR TBD. Added `worktree_manager/mux_daemon.py`: a host-wide
  resident daemon publishing `mux-daemon.lock` (namespaced `manager_mux_*`
  rendezvous fields, same lockfile-rendezvous + loopback-JSON pattern as
  `agent_worktrees`' `hook_ipc`/`classify_daemon`/`mux_link`) and serving
  `mux-status-v1` requests (`build_compute`/`apply_status_options`,
  mirroring `_monitor_mux_set`'s bounded subprocess `set-option` shape).
  `MuxMappingRegistry` is the Manager-owned `worktree_id ⇄ mux session`
  mapping -- deliberately always disk-backed rather than one long-lived
  in-memory cache, since register/remove calls come from short-lived CLI
  invocations, not a process that outlives the mapping -- with the same
  monotonic-`mapping_revision` guard and cross-process advisory file lock
  `ManagedMuxCache` uses; restart recovery is automatic (no separate
  in-memory state to warm). `ensure_daemon_running` proves liveness with a
  real subscribe/release wire round-trip rather than a PID/start-time
  check. `register_mapping`/`remove_mapping`/`get_mapping` are reachable
  via a new `worktree-manager mux-daemon run|ensure|register|remove|show`
  CLI surface -- none of it yet called by any real launch/join/restore/
  remux action or the production Picker (Step 3's job). Vendored
  `work_coalescing_singleton` into `worktree-manager/libs/` (byte-identical
  to the other two copies per `check-vendored-libs-sync.py`). Added
  `tests/test_mux_daemon.py` and `tests/test_mux_daemon_cli.py` (41 tests:
  registry persistence/monotonicity, rendezvous parsing, compute-handler
  validation, an end-to-end real-socket round trip, the resident daemon's
  idle-exit lifecycle, and the CLI surface against a scratch runtime
  root). Full `worktree-manager` suite (`uv run --extra dev pytest`): 1401
  passed, 7 skipped, 3 pre-existing unrelated failures (all in
  `test_data_ssh_sources.py`, a Picker-provider-source path-validation area
  this change never touches; confirmed via `git diff --stat` showing no
  overlap). Bumped `worktree-manager` to `0.1.0-dev78` (pyproject.toml +
  `__init__.py`, kept in sync per `check-version-consistency.py` -- this
  package versions directly rather than through the plugin changefile
  flow, since it is delivered out-of-plugin).

- **2026-09-25** — Landed Phase 3e Step 6's remaining review-round fixes and
  closed out the phase. PR
  [#3626](https://github.com/ThomasMichon/copilot-extensions/pull/3626)
  squash-merged into `dev` (`3ffc65145`), retiring agent-worktrees'
  `profiles`/`terminal-fragment` CLI verbs and the bundled-Picker
  Profiles-grid path now that Worktree Manager fully owns Terminal Fragment
  handling; Copilot's own PR review caught and this session fixed real
  regressions in the same PR (see Step 6's own bullet above for the full
  breakdown). Updated `visions/plugins/agent-worktrees/README.md` and
  `visions/installer/README.md` to state the boundary explicitly:
  agent-worktrees ceases to own Terminal Fragments but continues to own
  per-project binstubs. Marked Phase 3e **Done** in this README and its own
  plan doc — all 7 ordered steps landed, no open items remain in this
  phase. (Phases 3b/3c/3d remain in flight; see their own sections above
  for current status.)

- **2026-09-25** — Fixed two unrelated bugs blocking `copilot-extensions`'
  `dev`->`main` promotion pipeline (the `full - agent-worktrees` CI job in
  `validate-and-promote.yml`), discovered while chasing this effort:
  `install.sh`'s `err()` helper wrote its health-gate rejection message to
  stdout instead of stderr (PR
  [#3702](https://github.com/ThomasMichon/copilot-extensions/pull/3702)),
  and `peer_launch_adapter.validate_context()` mis-parsed a Windows-style
  install-receipt path's parent directory on POSIX hosts (PR
  [#3704](https://github.com/ThomasMichon/copilot-extensions/pull/3704)).
  Both squash-merged; confirmed promotion resumed (`origin/main` advanced
  to `3968500af`, `release: promote dev ... to main (#3708)`), unblocking
  every contributor's merged-but-stuck `dev` work, including this effort's
  own Phase 3e Step 6.

- **2026-09-25** — Merged Phase 3b Slice 2 Sub-slice 3 Step 1, PR
  [#3650](https://github.com/ThomasMichon/copilot-extensions/pull/3650)
  (squash-merged into `dev` as `059c25a35`). Went through 21 review rounds
  before landing (see the PR's own comment history for the full per-round
  breakdown); the last three rounds each fixed a genuine finding surfaced
  after a mid-session rebase onto the latest `dev` (which had itself landed
  the Stage D lazy-dispatch decoupling since this branch's prior rebase):
  rejecting an out-of-range rendezvous port before returning an endpoint,
  resolving `status-monitor`'s managed-mux runtime home through the
  cluster-free `_core_helper` path instead of a direct (and, under lazy
  dispatch, unbound) `core._aw_runtime_home()` access, and gating
  `CoalescingServer.close()`'s `shutdown()` call on the serve thread still
  being alive rather than trusting its one-way readiness event alone (with
  a regression test in both vendored `work_coalescing_singleton` copies,
  which also caught and fixed a latent bug in the prior round's own test
  that patched a bound method after the thread's target had already been
  captured). Full suite re-run after the rebase: 8 pre-existing unrelated
  failures (same four families as before -- git credential pinning,
  lazy-dispatch cluster-scan parity, repos-clone auth-arg injection,
  update-stage indicator state; one fewer than previously observed,
  consistent with an unrelated upstream fix landing during the rebase),
  5469 passed, 52 skipped. Step 1 of this sub-slice's 6-step ordered plan
  is complete; Steps 2-6 (the Worktree Manager mux-companion daemon itself,
  Mux-launch integration, and the writer-ownership cutover) remain
  unclaimed.

- **2026-09-25** — Landed Phase 3b Slice 2 Sub-slice 3 Step 1 (additive
  daemon-link contract + resident managed-mux cache seam), PR TBD. Added
  `mux_link.py`: rendezvous-parseable `managed_mux_*` fields published in
  the same `status-monitor.lock` alongside `HookIpcServer`/`classify_daemon`/
  `worktree_status_daemon`'s own namespaced fields; a thread-safe
  `ManagedMuxCache` keyed by `(project, worktree_id)` whose `apply_observation`
  rejects an incoming `mapping_revision` lower than the one already on file
  (scoped per key, so a stale/out-of-order `live: false` event can never
  clobber a newer live mapping); an `InProcessRuntime` wired into
  `cmd_status_monitor` the same way `worktree_status_daemon.InProcessRuntime`
  is (lock-extra publication, `has_active_demand()` feeding the monitor's own
  idle-strike/empty-exit logic, shutdown in the same `finally`). `_monitor_sweep`
  gained an optional
  `managed_mux_cache` parameter: when present, its currently-live session
  names are merged into the existing `catalog_observer` call alongside the
  direct mux scan's own set -- but never added to `served`, and never
  triggers a `set-option` write of its own, per this step's explicit
  no-writer-ownership-change scope. Client-side `mux_live_via_daemon`/
  `mux_live_with_boot` push helpers are pinned (mirroring
  `worktree_status_daemon.status_via_daemon`/`status_with_boot` exactly) but
  not yet called by anything -- Step 2's Worktree Manager mux-companion
  daemon is the first real caller. No behavior change to ordinary sessions:
  the cache starts (and, until a Manager daemon exists to push into it,
  stays) empty. Added `tests/test_mux_link.py` (cache monotonicity/
  validation/rendezvous/wire-helper/runtime-lifecycle coverage) plus two new
  `_monitor_sweep` tests proving the merge is observation-only. Full
  `agent-worktrees` suite run: 9 pre-existing failures confirmed unrelated
  (git credential pinning, paired-carve harness attribution, update-stage
  indicator state -- none touch status-monitor/mux_link, and `git diff`
  confirms this change touches none of those files), 5459 passed, 51
  skipped; the targeted `test_mux_link.py`/`test_status_monitor.py` suites
  are fully green (120/120). `ruff check --select F,E9`,
  `check-module-size.py`, `check-install-contract.py`, and
  `check-version-consistency.py` all clean. Added a pending
  `.changefiles/*.json` entry naming `agent-worktrees` (`dev`, since this
  session's own PR builds on the already-in-flight `1.5.5` patch series)
  per CONTRIBUTING.md's changefile flow -- the real version bump is
  applied by the promotion pipeline, not hand-edited in this tree.

- **2026-09-23** — Landed Phase 3e Step 5b/5c (install.ps1 repoint + live
  trial), PR [#3457](https://github.com/ThomasMichon/copilot-extensions/pull/3457).
  Asked the operator how to proceed before touching `install.ps1` (this
  step, unlike 5a, cannot be a pure dry-run: repointing the installer means
  the next real update/install exercises the new write path for real).
  Operator direction: "repoint and trial now" -- treat this machine's own
  `agent-worktrees update` as the supervised live trial. Repointed
  `Deploy-Shortcuts` to call a new `Deploy-TerminalFragmentViaWorktreeManager`
  first: resolves the `worktree-manager` binstub via `Get-
  UsableWorktreeManagerBin` (PATH lookup + `--version` health check +
  `Test-WorktreeManagerVersionAtLeast` >= `0.1.0-dev75`, the version that
  added `--deploy`/`--mirror`), and calls `terminal-fragment <project>
  --machine <k> --deploy --live`; falls back to the pre-existing PowerShell
  implementation (extracted verbatim into `Deploy-TerminalFragmentLocally`,
  behaviourally unchanged) when Worktree Manager is absent, unhealthy, or
  too old -- the same present-or-fallback shape Phase 3b Sub-slice 2a's mux
  relocation used. Validated in stages before landing: a dry-run preview
  proved worktree-manager's fragment output was byte-identical to the
  already-installed fragment and converged to zero plan changes. After
  landing, ran `worktree-manager update` (`0.1.0-dev65` -> `dev75`) and
  `agent-worktrees update` (`1.5.5-dev260` -> `dev261`) on that machine,
  then invoked the installed `install.ps1`'s `refresh-profiles` action
  directly: its own output confirmed the NEW path ran ("Windows Terminal
  profiles deployed via Worktree Manager" + the deploy plan's `-> LIVE:
  writes applied.`), and a before/after diff of the real fragment/
  `state.json`/`settings.json` showed everything byte-identical with no
  spurious `settings.json.wt-backup-*` file created -- a clean, idempotent,
  non-destructive real deploy. `--live` is proven safe on this machine
  (Step 5c done here); it remains off by default everywhere else until
  independently exercised. Bumped `agent-worktrees` `1.5.5-dev260` ->
  `dev261`.
- **2026-09-23** — Landed Phase 3e Step 5a (deploy/mirror mechanism), PR
  [#3445](https://github.com/ThomasMichon/copilot-extensions/pull/3445).
  Added `terminal_fragment.deploy_fragment(machine, current_project=None,
  apply=False)`: computes the new fragment JSON, GUID staleness/change
  detection against the on-disk fragment, and the
  `generatedProfiles`/`settings.json` reconciliation (reusing the
  already-ported `reconcile_generated_profiles`) unconditionally; only
  `apply=True` performs any write, in the same reconcile-before-write order
  `install.ps1`'s `Deploy-Shortcuts`/`Sync-TerminalState` used (avoids the
  race where WT reads the new fragment while stale GUIDs are still in
  `state.json`). Before starting this slice, flagged the live-state risk to
  the operator per the predecessor handoff's explicit blocker: chose
  "dry-run flag first, defer live writes until proven safe." Wired
  accordingly: `terminal-fragment <project> --deploy [--live]` and
  `profiles <project> apply --mirror [--live]` both default to a full
  preview and only write with the explicit `--live` flag; `apply` without
  `--mirror` is byte-for-byte unchanged. Added
  `test_terminal_fragment_deploy.py` (dry-run-never-writes, apply-writes-
  and-reconciles-a-fixture-WT-state, idempotent-second-dry-run) plus 4 new
  CLI-dispatch tests. Full non-picker suite green (51/51 in the touched
  suites; 1134 passed / 3 pre-existing unrelated Windows path-validation
  failures across the full suite via a real venv install). Bumped
  `0.1.0-dev74` -> `dev75`. **Still open, tracked in the phase doc:** Step
  5b (repoint `install.ps1`) and Step 5c (an operator-supervised live trial
  of `--live` against a real Windows Terminal install, before this
  mechanism is treated as proven safe or agent-worktrees' own deploy path
  is retired).
- **2026-09-23** — Landed Phase 3e Step 4 (CLI surface): added
  `worktree-manager terminal-fragment <project> [--machine K]
  [--explain|--doctor|--migrate-selections]` and `worktree-manager profiles
  <project> get|apply [--machine K] [--set '<json>'] [--json]`. Deliberately
  adapted the shape rather than replicating agent-worktrees' cwd-based
  `--machine`/`--project` defaults: takes an explicit `<project>` positional
  (matching this CLI's own `projects`/`repos` convention) and resolves
  `--machine` from that project's own `config.yaml` `machine:` field via a
  direct file read (`harness_state`-style) — deliberately sidesteps Phase
  3d's still-open Group A/B question about `config.load_config()`'s
  cwd-based active-project resolution rather than reaching back into it.
  Added `terminal_fragment.detect_platform()`/`detect_env_label()` (ported,
  dependency-free) so the local env label resolves without that same
  dependency. `profiles apply` persists the selection but always reports
  `mirrored: false` — deploying to a real Windows Terminal fragment is
  Step 5's scope, not this command's. Verified end-to-end against a
  synthetic `USERPROFILE` home (manual `get`/`apply`/`--explain` round
  trip) plus 6 new automated CLI-dispatch tests
  (`test_terminal_fragment_cli.py`). Full non-picker suite green (466
  passed, up from 460); `production_picker` suite unaffected (658 passed,
  same 3 pre-existing unrelated failures). worktree-manager bumped
  `0.1.0-dev73` -> `dev74`.
- **2026-09-23** — Landed Phase 3e Step 3b (`collect_local_projects`
  rewiring): rewired `collect_local_projects`/`preview_local`/
  `migrate_local_selections` onto `harness_state.build_projects()`,
  completing the `terminal_fragment.py` relocation. Investigated Step 3's
  deferred `anchor`-override question directly rather than re-asking:
  grepped the whole agent-worktrees tree and found `entry.get("anchor")` is
  read by `config.py`/`doctor.py`/`front_door_cli.py` and exercised by
  several existing test fixtures (`test_doctor.py`,
  `test_projects_registry.py`, `test_registry_paths.py`) registering a
  project via `anchor:` with **no** matching `repos.yaml` entry at all —
  real, actively-used, not dead code as the Step 3 hedge suspected. Extended
  `harness_state.ProjectInfo` with `anchor`/`display_name` fields, resolved
  as `repo.path or entry.get("anchor")`, and used that for `project_roster()`
  instead of `repo.path` alone — closing a real gap the naive rewrite would
  have introduced. Ported the 2 original disk-collection tests (rewritten
  against a synthetic HOME + `home_dir` kwarg rather than monkeypatching
  agent-worktrees internals) plus a new
  `test_collect_local_projects_honors_projects_yaml_anchor_override` proving
  the fix. Full non-picker suite green (460 passed). worktree-manager
  bumped `0.1.0-dev72` -> `dev73`. Phase 3e's disk-collection/build side is
  now fully relocated; Steps 4-6 (CLI surface, `install.ps1` repoint, clean
  cutover) remain.
- **2026-09-23** — Landed Phase 3e Step 3 (`terminal_fragment.py`'s pure
  core): `worktree_manager.terminal_fragment` — `build_fragment`,
  `stable_guid`/GUID helpers, `reconcile_generated_profiles`,
  `diagnose_wt_state`, `migrate_selection_to_keys`, and their dataclasses —
  ported byte-for-byte (no behavior change), reusing `harness_state`'s
  `RosterMachine`/`SshEnvironment` (Step 2) and `terminal_profiles` (Step 1)
  instead of redefining them. 35 of the original 37 agent-worktrees tests
  ported verbatim (import paths only); split out **Step 3b** for the 2
  disk-collection tests (`collect_local_projects` itself), since rewiring
  it onto `harness_state.build_projects()` surfaced a small but real gap:
  a projects.yaml-level `anchor` override `anchor_for()` falls back to,
  which evidence (`register_project()` never writes it) suggests may be
  dead code — flagged rather than silently dropped — plus a `display_name`
  field `harness_state.ProjectInfo` doesn't carry yet (a real, written
  field, unlike `anchor`). Full non-picker suite green (457 passed, up
  from 422 + 35 new). worktree-manager bumped `0.1.0-dev71` -> `dev72`.
- **2026-09-23** — Operator direction resolved Phase 3e's Open Question 1:
  the registry-read boundary is a **direct file read**, not a new
  agent-worktrees CLI verb — matching worktree-manager's existing
  `harness_state.py` module, which already reads `repos.yaml`/
  `projects.yaml`/per-project `config.yaml` directly as a documented,
  dependency-free contract. Landed Phase 3e Step 2: extended
  `harness_state.py` with `SshEnvironment`/`RosterMachine` dataclasses,
  `project_roster()` (a verbatim port of
  `terminal_fragment._load_roster`), and `ProjectInfo.wsl_distro`/
  `wsl_state`/`roster` fields populated in `build_projects()` — giving
  Step 3's relocated fragment-builder everything `collect_local_projects`
  reads today, through the same file-reading contract. New
  `test_build_projects_reads_wsl_and_roster` covers the join; full
  non-picker suite green (422 passed). worktree-manager bumped
  `0.1.0-dev70` -> `dev71`.
- **2026-09-23** — Landed Phase 3e Step 1 (`profiles.py` relocation):
  `worktree_manager.terminal_profiles` (verbatim copy of
  `agent_worktrees.profiles`, no behavior change — same
  `~/.<project>/config.yaml` `terminal_profiles:` key/shape); repointed
  `profiles_io.py`/`engine_profiles_view.py` to import it directly; deleted
  `production_picker/profiles.py`'s `engine_module("profiles")` proxy shim,
  closing Phase 3d's `profiles` checkbox. `test_profiles_io.py` repointed to
  the same module. agent-worktrees' own `profiles`/`terminal-fragment`/
  `repair` CLI verbs and `profiles.py` copy are untouched (kept working per
  the plan's transitional shape). Full `production_picker` suite green (658
  passed, same 3 pre-existing unrelated Windows path-validation failures as
  #3359). worktree-manager bumped `0.1.0-dev69` -> `dev70`.
- **2026-09-23** — Wrote Phase 3e's migration plan
  ([`phase-3e-terminal-profile-relocation.md`](phase-3e-terminal-profile-relocation.md)),
  closing that phase's first checkbox. Evidence-gathering past the Picker's
  own call sites found the boundary is bigger than `profiles.py` alone:
  `terminal_fragment.py` (1016 lines) structurally couples to
  agent-worktrees' own project/repo registry via `collect_local_projects`
  (imports `config`/`installer`/`repos` to enumerate every registered
  project), and `install.ps1` carries its own PowerShell terminal-
  integration functions calling into the Python CLI — a second-language
  surface. `profiles.py` itself (235 lines) has zero such coupling and is a
  clean, mechanical relocation. Split the phase's checklist into 6 ordered
  steps (relocate `profiles.py` first, closing Phase 3d's dependent
  checkbox; resolve the registry-read boundary; relocate the GUID/
  reconciliation core; give worktree-manager an equivalent CLI/config
  surface; repoint `install.ps1`; clean cutover last). Left 3 open design
  questions in the doc (registry-read shape, the bundled legacy Picker's
  disposition, sequencing against Phase 3d) — no implementation started.
- **2026-09-23** — Operator direction on Phase 3d's `profiles` disposition:
  not a vendored-lib fix (as originally scoped below) — terminal handling of
  every kind is leaving agent-worktrees for the Worktree Manager, matching
  the standing Mux/AHP relocation precedent (#2062), eventually including
  `agent-worktrees update` itself becoming `worktree-manager update`. Added
  Phase 3e (Planned — #3390, filed this session) to track the relocation of
  `profiles.py`/`terminal_fragment.py` and agent-worktrees' own
  `profiles`/`terminal-fragment`/`repair` CLI verbs; revised Phase 3d's
  `profiles` checkbox to point at it instead of vendoring. Updated
  [`visions/plugins/agent-worktrees`](../../../visions/plugins/agent-worktrees/README.md)
  (new Non-Goal) and
  [`visions/installer`](../../../visions/installer/README.md) (feature text)
  with matching Provenance entries. No relocation implementation yet — Phase
  3e opens with writing the reviewed migration plan, following
  `phase-3b-ahp-relocation.md`'s shape.
- **2026-09-22** — Added Phase 3d (Planned — #3359, #3360): retire
  `_engine_runtime.py`'s in-process import of agent-worktrees' own CLI-root
  modules. Landed #3359 (vendor the 3 borrowed shared libs) via
  [#3368](https://github.com/ThomasMichon/copilot-extensions/pull/3368),
  full test suite green (656 passed, 3 pre-existing unrelated Windows
  path-validation failures confirmed out of scope). For #3360, did the
  requested drill-down before any conversion work: a grep-verified call-site
  inventory across all 9 proxied modules revealed the issue's own "9 read
  paths, each needs a JSON verb" framing doesn't fit the evidence — one
  cluster (`runner.py`'s `cli._sweep_*_on_exit`/`reap_orphan_mux_sessions`/
  `_start_picker_monitor_root`) is private process-lifecycle internals
  managing the Picker's *own* process, not a read path; another
  (`data_local.py`'s `tracking`/`pr_ops`/`reclaim`/`sessions` cluster) is one
  atomic read-reconcile-**write** loop over `tracking.yaml` per Picker
  refresh, needing a new batched verb rather than per-attribute conversion,
  and sequenced after Phase 3c since both touch the same hot call site; and
  `profiles` turns out to be pure dependency-free logic — a vendoring fix
  like #3359, not a CLI-conversion one. Full inventory + revised remediation
  shape + open design questions:
  [`phase-3d-engine-runtime-retirement.md`](phase-3d-engine-runtime-retirement.md).
  Conversion implementation not started pending design-question answers.
- **2026-09-19** — Added Phase 3c (Planned): design/architecture/plan for
  consistently non-blocking Picker I/O across pivot loads, menu opens, and
  action execution/progress, written up after #2967's picker UX fixes
  surfaced (and a reverted same-session prototype for) `setup()`'s
  synchronous pivot-registry scan + single-machine data load blocking the
  render thread. See
  [`phase-3c-non-blocking-io.md`](phase-3c-non-blocking-io.md)
  for the full audit, the specific race the reverted prototype hit, and the
  ordered slice plan. No implementation in this pass -- planning only.

- **2026-09-17** — Finished Phase 3b Slice 1 Steps 5-6. Deleted
  agent-worktrees' remaining AHP implementation path
  (`cmd_session_backend`, `ahp_backend.py`, `SessionBackendConfig`, the
  `config.d` `session_backend` validation block, and the
  `websocket-client` dependency) while keeping the reader-side legacy
  `session_backend:` → `execution_leg` translation shim in `tracking.py`.
  Closed an inventory gap the design doc had missed: both the in-plugin and
  relocated Worktree Manager `launch-session.{sh,ps1}` copies still shelled
  into the legacy `session-backend status`/`ensure` verbs on the **bare/direct**
  launch path. They now read only `execution-leg get`: an already-persisted
  AHP leg still resumes exactly as before, but creating a **new** AHP session
  through the bare path is deliberately gone and now warns to use the
  Worktree Manager Picker, keeping AHP session establishment fully owned by
  `worktree_manager.ahp_provider`. Completed the remaining test migration:
  retargeted the launcher contract and agent-worktrees JSON/live-signal tests
  to `execution_leg`, removed the obsolete plugin-local AHP backend/command
  tests, and added missing Worktree Manager provider + relocated-launcher
  regression coverage. Version bumps: agent-worktrees `1.5.5-dev134`,
  marketplace metadata `1.7.7-dev120`, Worktree Manager `0.1.0-dev47`.
  Validation: changed-file targeted tests passed (`agent-worktrees` 103;
  `worktree-manager` 42); full `worktree-manager` suite passed with the two
  known hanging production-picker tests excluded (`805 passed, 1 skipped`).
  Full `agent-worktrees` suite ran to completion and now shows only five
  unrelated pre-existing failures in this environment —
  `tests/test_pr_ops.py::TestRefreshHeadObservation::test_concurrent_reassociation_rejects_returned_observation`
  plus four `tests/test_update_stage.py` indicator tests that pass in
  isolation but fail after earlier suite pollution — with `4481 passed,
  47 skipped` otherwise. `ruff check --select F,E9` on both packages,
  `python tools/check-install-contract.py`,
  `python tools/check-version-consistency.py`, and
  `python tools/check-version-bump.py` all passed.

- **2026-08-17** — Effort authored to give the Worktree Manager rework a single
  coherent home in-repo, tying the Manager-build issues (#352 / #355 / #356 /
  #357) to the Picker-extraction and bare-invocation seam, and tracing both to the
  `installer` and `picker` visions. Recorded current reality: Phases 0–2 landed;
  Phase 3 (extracted Picker over the engine boundary) and Phase 4 (plugin seam)
  substantially in place; Phase 6 (retire the bundled Picker → install-trigger
  end-state) still pending parity.
- **2026-08-17** — Bootstrap prerequisite auto-provisioning (git-optional).
  Reworked `bootstrap.{ps1,sh}` to provision `uv` (user-local, restart-aware) and
  best-effort `git`, with a GitHub-tarball fallback when `git` is absent; mirrored
  the tarball fallback into `self_update` (`manager_tarball_url` +
  `_fetch_via_tarball`). Added derivation + fallback tests (132 green). Closes the
  §one-line-bootstrap × §prerequisite-provisioning × §restart-aware delta at the
  bootstrap entry — the app-level provisioning (#355) was already done; this
  closes the bootstrap-entry tail. worktree-manager `0.1.0-dev13`.
- **2026-08-17** — Clean-room validated the bootstrap on a fresh box. Added the
  `worktree-manager-bootstrap` Tier-P scenario (`tools/clean-room/`): on the
  **pristine** image (no uv) the published one-liner self-provisions uv (0.12.5),
  publishes `current-version` + slot, deploys the `~/.local/bin/worktree-manager`
  binstub, and the binstub runs on a stock login PATH — **7 passed, 0 failed**.
  Turns the unit-tested behavior into a hard fresh-box PASS. The git-absent
  tarball fallback stays unit-tested; a no-git image variant is a noted follow-up.
- **2026-08-31** — Added #1478 to Phase 3: a single engine-owned manual
  mux-restoration operation, surfaced by both Picker implementations. The design
  explicitly restarts/resumes persisted session state instead of attempting to
  reparent an arbitrary live Windows console process into a new ConPTY.
- **2026-08-31** — Implemented the #1478 slice. `agent-worktrees remux` now
  keeps the live `reptyr` adoption path on Linux/WSL and adds a guarded Windows
  reclaim-before-resume path that refuses an existing mux or ambiguous owner.
  Both Picker implementations expose **Restore** when an unreachable bound
  process has a known head session; the standalone Manager prepares recovery
  through the JSON engine boundary, then launches through the installed project
  binstub so the normal mux wrapper is restored.
- **2026-09-03** — Accepted #1657 into Phase 3 as the optional same-machine AHP
  session-backend slice. The reviewed contract keeps worktree and finalization
  authority in agent-worktrees, treats mux clients as detachable presentation,
  and requires exact-path binding plus fail-closed lifecycle handling.
- **2026-09-04** — Corrected course after #1657/#1998 landed the AHP backend
  *inside* `agent-worktrees` (`ahp_backend.py` + a `session_backend.is_ahp`
  config branch threaded through `__main__.py`/`tracking.py`/`finalize.py`/
  `config_dropins.py`). The new `session-hosting` vision (#2054) establishes
  that agent-worktrees is pure durable agency state and never a home for a
  provider-specific config union; a follow-up correction clarified that Mux
  (terminal presentation) and AHP (session backend) are **composable, not
  mutually exclusive** — an AHP-hosted session may still be Mux-wrapped — and
  that both currently belong to the **Worktree Manager** control-plane, not to
  agent-worktrees and not to a brand-new fourth plugin. Added Phase 3b to track
  the physical relocation as an architecture correction (same behavior,
  corrected ownership), filed as #2062.
- **2026-09-04** — Wrote the reviewed, ordered migration plan for Phase 3b
  Slice 1 (AHP only): [`phase-3b-ahp-relocation.md`](phase-3b-ahp-relocation.md).
  Full current-state inventory with exact file/line evidence; target shape
  (`session_backend` → generic `execution_leg` with an opaque provider blob,
  a new `execution-leg set/get/clear` CLI verb replacing `session-backend`);
  reader-side back-compat for existing on-disk `session_backend:` records; six
  ordered, independently-landable implementation steps, each its own
  version-bumped PR. Deliberately scoped AHP-only — Mux relocation (Slice 2)
  is more deeply coupled to agent-worktrees' liveness reducers and three
  platform launcher scripts, so it is sequenced after Slice 1 proves the
  pattern. Also noted, out of scope for this plan: `worktree-manager`'s
  `runner.py` reaches the engine for launch/mutate actions through an
  in-process `sys.path` import of `agent_worktrees` internals
  (`_engine_runtime.py`, its own docstring calls it "temporary"), which is a
  live violation of the Picker vision's process-boundary-only Non-Goal; Slice
  1 replaces that boundary only for the AHP call path, not for the rest of
  the interactive Picker's engine usage.
- **2026-09-04** — Landed Phase 3b Slice 1 Step 1 (`tracking.py` additive
  groundwork): added `ExecutionLegBinding` (generic `provider`/`state`/
  `binding_revision`/opaque `blob`), the matching `WorktreeRecord` fields
  (`execution_leg`/`execution_leg_opaque`/`execution_leg_raw`), a generic
  `execution_leg:` read/reconcile/serialize path mirroring the existing
  `session_backend:` handling exactly, and `derive_execution_leg()` — a pure,
  read-time function that translates a legacy AHP `session_backend` binding
  into the generic shape without storing or serializing anything new. Nothing
  writes `execution_leg:` yet, so on-disk output for every existing worktree
  is byte-identical; 15 new tests (including a non-mapping-`blob` opaque-
  preservation case caught by PR review) plus the existing 285-test
  agent-worktrees suite (including all `ahp`/`tracking`/`finalize` tests)
  pass unmodified. agent-worktrees bumped to `1.5.5-dev17` (two concurrent
  drivers took `dev15`/`dev16` on `main` first; rebased and re-bumped each
  time per the repo's serial-merge norm).
- **2026-09-04** — Wrote the reviewed, ordered migration plan for Phase 3b
  Slice 2 (Mux): [`phase-3b-mux-relocation.md`](phase-3b-mux-relocation.md).
  Full current-state inventory (launcher/wrapper script sizes, `cmd_remux`,
  `reclaim.py`, `sessions.py`'s liveness observation) established a key
  difference from AHP: Mux liveness is **observed live** against the mux
  server, never persisted as a binding, so this slice needs no
  `execution_leg` writer. Recommends **not** attempting the relocation in one
  slice given the launcher scripts' combined size (~3,500 lines across three
  platforms); proposes Sub-slice 2a (move the already-externally-invoked
  launcher/wrapper scripts + repoint `cmd_launch`'s path resolution, zero
  logic change) and Sub-slice 2b (split `cmd_remux` into a detection query
  that stays in agent-worktrees and a relaunch action that moves). Explicitly
  keeps `sessions.py`'s liveness reducers, `reclaim.py`, and `reclaim_one` in
  agent-worktrees as legitimate observation/generic-termination tooling.
- **2026-09-05** — Operator directive: Mux gets a **clean, decisive cutover**
  (one canonical implementation, no indefinitely-maintained pair), while
  **AHP remains explicitly opt-in**; the migrated agent-worktrees
  implementation is authoritative — Worktree Manager's own earlier, fledgling
  Picker-launch prototype is retired scaffolding, not something to reconcile
  with. Revised `phase-3b-mux-relocation.md` accordingly: reconciled "clean
  cutover" against the already-published `installer` vision text ("muxing is
  a capability this app provides ... run non-muxed when it is absent") —
  the cutover deletes the old in-plugin launcher scripts in the same PR that
  repoints `cmd_launch`, replacing them with a small, new **direct non-mux
  fallback** rather than a retained duplicate launcher. Added the same
  invariant explicitly to `phase-3b-ahp-relocation.md`'s design (AHP is never
  auto-selected; the relocation must not introduce an implicit-selection
  path). Landed Sub-slice 2a Step 1: copied `launch-session.{sh,ps1,cmd}` +
  `pane-wrapper.{sh,ps1}` verbatim (hash-verified byte-identical) into
  `worktree-manager/bin/`; proved with a new test
  (`test_bin_directory_is_deployed_into_the_slot`) that the existing
  `self_install._copy_payload` mechanism already deploys a payload-sibling
  `bin/` directory with **zero packaging code changes**, since it
  `shutil.copytree`s the whole payload dir and the bootstrap clones the whole
  repo before `cd`-ing into `worktree-manager/`. Validated against the full
  `test_picker_capture.py` golden/ANSI/SVG regression suite (21 targeted
  tests, all passing) — the "visualizer-validator" regression gate the
  operator called out. worktree-manager bumped to `0.1.0-dev33`. Noted (not
  fixed, pre-existing, unrelated to this change): `tests/production_picker`'s
  full suite hangs partway through `test_data_ssh_sources.py`/
  `test_launch_trace.py`, likely a real-SSH-subprocess test with no mock/
  timeout in this environment — a separate, pre-existing issue to file.
- **2026-09-10** — Implemented Phase 3b Slice 2 Sub-slice 2a Step 2's
  **repoint + direct-fallback** portion. `agent-worktrees cmd_launch` now
  resolves Worktree Manager's relocated launcher from
  `WORKTREE_MANAGER_ROOT` + `current-version`, health-probes the versioned
  install before using it, and otherwise falls back to a new small direct
  non-mux path that reuses the normal `resolve` plan and still runs
  `post-exit` after the child exits. Also fixed the relocated POSIX launcher
  to resolve `pane-wrapper.sh` relative to its own installed `bin/`
  directory, matching the existing PowerShell `$PSScriptRoot` behavior. **Plan
  deviation recorded deliberately:** the old in-plugin launcher/wrapper files
  remain deployed as the rollback path until live hardware proves the relocated
  path preserves mux, post-exit, and activity journaling; the plan's deletion
  checkbox stays open for that follow-up cleanup PR. Version bumps:
  agent-worktrees `1.5.5-dev58`, marketplace metadata `1.7.7-dev56`,
  worktree-manager `0.1.0-dev36`.
- **2026-09-10** — Diagnosed and closed the actual live regression this slice
  exists to fix, one layer deeper than the `cmd_launch` repoint above. Once
  Worktree Manager's own `worktree-manager` binstub first appeared on `PATH`
  on live hardware, the bare-invocation seam handed the entire interactive
  session to Worktree Manager's OWN transplanted production Picker, whose
  `_run_launch` routed every local, non-AHP launch through
  `launcher.compose_launch()`/`execute()` — a `MuxCapability` seam that has
  **never** been wired to a real backend (`set_mux_capability()` is never
  called anywhere), so every such launch silently ran non-muxed with no
  `post-exit`/activity journaling, bypassing `cmd_launch`/`launch-session.ps1`
  entirely. Fixed by making `_run_launch` delegate ordinary local, non-AHP
  launches to the SAME relocated `<own-install>/bin/launch-session.*` script
  (verbatim reuse, per the operator directive), passing the already-resolved
  `plan.worktree_id` so a `mode == "new"` request cannot trigger a second
  worktree creation by re-issuing `--new` inside the script's own resolve.
  AHP-attached launches are deliberately left on `launcher.launch()` (AHP's
  `attach_plan()` rewrite would be clobbered by the script's own re-resolve);
  real mux support for AHP attachment remains a separate, explicitly scoped
  follow-up. Added regression tests proving delegation on both platforms,
  `--worktree-id` substitution for `new`, `WORKTREE_NO_MUX` threading, and
  that AHP still bypasses the relocated script. Full `worktree-manager` suite:
  761 passed / 2 skipped (only the 3 pre-existing, unrelated Windows
  provider-registry POSIX-path failures remain, confirmed unaffected). As an
  interim mitigation on the affected machine while this PR was in flight, the
  Worktree Manager binstub was temporarily removed from `PATH` so the
  bare-invocation seam fell back to the bundled, already-mux-capable Picker;
  it should be safe to restore once this fix is deployed.
- **2026-09-10** — A second, independent session hit the same live
  regression on the same machine before the fix above had landed, and drafted
  its own `agent-worktrees`-side safety gate (raising
  `_WORKTREE_MANAGER_MIN_PICKER_VERSION` past every released Worktree Manager
  build). Rebasing onto the real fix (the two entries above,
  [#2429](https://github.com/ThomasMichon/copilot-extensions/pull/2429))
  made that gate redundant, so it was reverted rather than landed alongside
  the real fix — avoiding two competing mitigations for the same regression.
  While independently re-running the full `agent-worktrees` suite to validate
  against the merged fix, found and fixed three real, previously-masked test
  bugs (unrelated to the regression itself): `test_registry_paths.py` and
  `test_session_context_companions.py` both spawned subprocesses without
  scrubbing this test process's own inherited Copilot session-identity env
  vars (`COPILOT_PLUGIN_ROOT`, `COPILOT_AGENT_SESSION_ID`, etc. — leaked
  whenever the suite runs, as it normally does, from inside a live Copilot CLI
  session), and `test_config.py`'s
  `test_cp_related_pr_map_includes_knowledge_overlay` had a stale mock
  signature masked by the code under test's own `except Exception` fallback.
  Landed as [#2439](https://github.com/ThomasMichon/copilot-extensions/pull/2439)
  (full suite: 4092 passed, 47 skipped, 0 failed). Also filed
  [#2523](https://github.com/ThomasMichon/copilot-extensions/issues/2523)
  (unrelated, general session-guidance gap surfaced in the same session:
  agent-worktrees' cross-repo session guidance should require fully-qualified
  `owner/repo#N` issue/PR references, since a bare `#N` auto-links to the
  current session's backing repo and can silently 404 against the wrong one).
- **2026-09-12** — Taking sole ownership of this effort going forward (no
  other agent actively claiming a slice via #352 at this time). Revised
  Sub-slice 2b's design in
  [`phase-3b-mux-relocation.md`](phase-3b-mux-relocation.md) after
  discovering the coupling runs deeper than originally scoped: `remux.py`'s
  POSIX action calls `sessions.py` mux-naming/argv-building helpers
  (`mux_session_name`, `build_mux_new_window_argv`,
  `build_mux_new_session_argv`) that are pervasive utilities used well beyond
  remux, not remux-specific logic safe to duplicate into Worktree Manager.
  Revised design mirrors the resolve/execute pattern Sub-slice 2a already
  established: a new planning-only `agent-worktrees mux-remux-plan --json`
  query keeps all tmux-naming/argv-building and guard logic in
  agent-worktrees (unchanged in substance, just relocated out of
  `_perform_remux`), and Worktree Manager becomes a thin executor — running
  the returned POSIX `argv` directly, or (Windows) calling the
  already-existing `agent-worktrees reclaim --bare-only --yes --json` then
  relaunching through its own relocated launcher in ordinary resume mode.
  This means the Windows path needs **no new relaunch code** in Worktree
  Manager at all. Docs-only; implementation not started.
- **2026-09-12** — Second correction to Sub-slice 2b's design, made before
  any implementation code was written. The prior revision's step 3 still
  said `cmd_remux`/`_perform_remux`/`remux_bare_copilot`'s execution gets
  **deleted** from agent-worktrees. That's unsafe: `_perform_remux` is not
  solely the standalone `remux` verb's backend -- `_restore_before_resume`
  also calls it internally, backing `resolve --restore` and, through it, the
  bundled Picker's own **"Restore"** action, which must keep working with
  **zero** session-host providers present (the bundled Picker still ships
  and is still mux-capable until Phase 6c retires it). Deleting the action
  would have regressed exactly the class of live bug this effort exists to
  prevent. Sub-slice 2b is now **purely additive**: agent-worktrees' existing
  remux/`--restore` action machinery is untouched and permanently stays (its
  own zero-provider fallback); the new `mux-remux-plan` query only extracts
  the guard/target-resolution logic into a shared function both the existing
  action and the new query call, so Worktree Manager gains an independent
  second consumer of the same plan for its own eventual "Restore"
  Picker-parity action, without duplicating any tmux-naming logic. No
  deletion, no cutover, no regression risk to the existing standalone path.
- **2026-09-09** — Implemented the reviewed Phase 3b AHP relocation Steps 2-4
  without deleting the legacy path. agent-worktrees now exposes fenced,
  provider-neutral `execution-leg get/set/clear` JSON verbs, preserves legacy
  `session_backend` reads, and treats active/unknown generic legs as cleanup
  blockers. Worktree Manager now owns loopback AHP configuration and protocol
  behavior, resolves account/token through pinned engine subprocess commands,
  creates or verifies the exact resolved worktree session, persists the opaque
  AHP leg, and composes the authenticated client attachment. The production
  Picker adds independent default-off `AHP` controls to New Worktree and
  Open/Resume. Validated 17 focused agent-worktrees tests, 296 Worktree Manager
  engine/config/provider/launch/Picker tests, touched-Python Ruff `F,E9`,
  version consistency, install contract, docs consistency, and `git diff
  --check`; all passed. Bumped agent-worktrees to `1.5.5-dev49`, marketplace
  metadata to `1.7.7-dev45`, and Worktree Manager to `0.1.0-dev34`.
- **2026-09-12** — Landed the (twice design-corrected, see the two entries
  above) Sub-slice 2b implementation as
  [#2552](https://github.com/ThomasMichon/copilot-extensions/pull/2552):
  purely additive `mux-remux-plan`/`mux-pane-status` queries in
  agent-worktrees (extracting the guard/target-resolution logic out of
  `_perform_remux` into a shared function, called by both the existing
  standalone `remux`/`--restore` action and the new queries) plus a thin
  Worktree Manager executor that runs the returned POSIX `argv` directly, or
  on Windows calls `reclaim --bare-only --yes --json` then relaunches through
  the already-relocated launcher in ordinary resume mode. Confirmed
  agent-worktrees' own `cmd_remux`/`_perform_remux`/`remux_bare_copilot`
  remain completely untouched — they stay as agent-worktrees' permanent
  zero-provider-mode fallback backing the bundled Picker's standalone
  "Restore" action, per the corrected design. Marks Phase 3b Slice 2
  (Mux relocation) fully landed except the still-deliberately-deferred
  Sub-slice 2a old-in-plugin-script deletion, and there is still no Picker
  UI wiring for a Worktree Manager-side "Restore" action (CLI-only for now).

- **2026-09-14** — Fixed a live regression (Sub-slice 2c): Worktree Manager
  was not properly configuring the Mux (psmux) status bar for sessions it
  launches. Root cause: `launch-session.ps1` dot-sources
  `session-options.ps1` and `psmux-path.ps1` (and `session-options.ps1`
  itself resolves `psmux-passthrough.conf`) via `$PSScriptRoot`-relative
  paths, but Sub-slice 2a Step 1's verbatim copy into
  `worktree-manager/bin/` only carried `launch-session.{sh,ps1,cmd}` and
  `pane-wrapper.{sh,ps1}` — not the terminal/helper scripts. The dot-source
  failure is swallowed (a status-bar tweak must never block a launch), so
  the gap was silent rather than an error. Copied
  `session-options.{sh,ps1}`, `apply-mux-keybinds.{sh,ps1}`,
  `psmux-passthrough.conf`, and `psmux-path.ps1` verbatim from
  `plugins/agent-worktrees/terminal/` and `plugins/agent-worktrees/scripts/`
  into `worktree-manager/bin/` (hash matched), documented the sibling
  requirement in `worktree-manager/bin/README.md`, added a regression test
  asserting the dot-source strings and files' presence, and bumped
  `__version__` (`0.1.0-dev36` → `0.1.0-dev37`) so already-installed
  machines actually redeploy the corrected payload (caught by Copilot
  review on [#2666](https://github.com/ThomasMichon/copilot-extensions/pull/2666),
  which also flagged the initially-missed `psmux-path.ps1` dependency, a
  drift-guard gap, and a version-consistency gap). Also found and fixed,
  via the same review round, a genuine pre-existing infinite-loop bug in
  `apply-mux-keybinds.ps1`'s `Persist-Block` trailing-blank-line trim: when
  exactly one blank line remains, `$lines[0..($lines.Count - 2)]` evaluates
  PowerShell's `0..-1` range as two elements instead of shrinking to empty,
  so the trim loop never terminates. Fixed identically in both the
  canonical `plugins/agent-worktrees/terminal/apply-mux-keybinds.ps1` and
  the copied `worktree-manager/bin/apply-mux-keybinds.ps1` (kept
  byte-identical), with a structural regression test in
  `test_terminal_decoupling.py` and a byte-identity drift guard in
  `test_self_install.py`. Bumped agent-worktrees' own version surfaces
  (`plugin.json`, `pyproject.toml`, `.github/plugin/marketplace.json`:
  `1.5.5-dev110` → `1.5.5-dev111`) so version-gated plugin updates don't skip
  this fix for installed agent-worktrees copies (a repeat of the same
  version-consistency lesson, this time on the plugin side rather than
  Worktree Manager's). `worktree-manager`'s `test_self_install.py` suite
  passes (12/12); `agent-worktrees`' `test_terminal_decoupling.py` passes
  (14/14); `tools/check-version-consistency.py` passes across both.

- **2026-09-14** — Operator direction for a new Sub-slice 3 (not yet
  designed): migrating the Picker and Mux handling to Worktree Manager is
  explicitly a **separate concern from the AHP effort**. Going forward,
  Worktree Manager takes ownership of the Mux-facing legs of the resident
  status-monitor: agent-worktrees' daemon keeps accumulating/tracking
  session status (unchanged, sole authority), Worktree Manager owns a new
  **push subscriber** that writes that status into Mux, and Worktree Manager
  owns a new **Mux subscriber** that observes session create/destroy and
  writes the observation back to agent-worktrees. Recorded as direction only
  in `phase-3b-mux-relocation.md`'s new Sub-slice 3 section — the transport,
  write-back contract, and relationship to the existing per-session
  `status-updater` fallback still need an ordered plan, per this effort's
  own "plan before code" discipline (mirrors how Sub-slices 1/2 each got a
  reviewed plan doc before implementation started).

- **2026-09-14** — Landed Sub-slice 4: same-config marketplace-cell
  resolution + generic installed-binstub invocation. Prompted by an operator
  question about how Worktree Manager and agent-worktrees interact, which
  surfaced two divergent, non-cell-aware resolution mechanisms:
  `engine_client.installed_engine_command()` (agent-worktrees-only,
  legacy-root-only) and `production_picker/_engine_runtime.py` (checked only
  whether `COPILOT_EXTENSIONS_CONTEXT` was set, never the actual
  installation-mode policy). Neither could ever disagree with agent-worktrees
  in practice today (namespaced installation remains clean-room-only per
  `installation-mode-governance.md`), but neither was *structurally*
  guaranteed to agree either, once namespaced rollout reaches persistent
  machines.

  Added `worktree_manager/agent_plugin_runtime.py`: a generic,
  plugin-id-parameterized resolver reusable for any `agent-*` plugin (not
  just agent-worktrees), and `marketplace_cells_enabled()`, which vendors
  `libs/installation-context/installation_context.py` byte-identical
  (`tools/sync-installation-context.py`, extended with a new
  `STANDALONE_PYTHON_ADOPTERS` list for non-plugin standalone payloads) and
  calls its own `resolve_installation_mode()` for the global policy bit --
  the exact function every agent-* plugin's own bootstrap/doctor path
  already calls. Rewired both `engine_client.py` and `_engine_runtime.py` to
  resolve through this one shared module. Deliberately did **not** make
  Worktree Manager a fourth `libs/peer-launch` consumer: peer-launch's
  `OWNERS`/structural cell-root validation is a plugin-to-plugin contract
  requiring the caller to itself own a cell identity, which Worktree Manager
  (an explicit management-context caller per the `installation-cells`
  vision, not a marketplace plugin) structurally cannot satisfy without a
  separate, explicitly-scoped design decision -- recorded as an open
  follow-on, not silently hacked around.

  Validation: new `tests/test_agent_plugin_runtime.py` (7 tests) proves the
  "same config" guarantee directly -- an explicit context matching plugin id
  is ignored whenever the shared policy is absent/disabled, and only used
  when the policy is enabled, exactly mirroring what agent-worktrees' own
  bootstrap would decide for the identical file. Updated
  `test_production_picker_transplant.py`'s existing context-preference test
  to require the policy gate too, and added the disabled-policy fallback
  case. `libs/installation-context/tests/test_vendoring.py` updated so its
  synthetic-adopter sandboxing isn't polluted by the new standalone-payload
  list. Full `worktree-manager` suite: 798 passed (pre-existing, unrelated
  environment failures confirmed present on `main` before this change).
  `engine_client`/`agent_plugin_runtime`/`production_picker_transplant`
  targeted runs: 64+43+7 all green.

  **Follow-up review rounds on [#2674](https://github.com/ThomasMichon/copilot-extensions/pull/2674)
  found four more real issues, all fixed in the same PR before merge:**
  bumped Worktree Manager's own payload version (`0.1.0-dev37` → `dev38`, in
  sync with `pyproject.toml`, so version-gated installs actually redeploy
  this resolver); `resolve_installed_plugin_slot` now checks the interpreter
  exists *before* selecting a slot, so a complete-but-damaged
  `current-version` slot correctly falls through to `last-known-good` / the
  newest remaining `versions/*` (matching the original single-function
  resolver's behavior, which the refactor into two functions had
  regressed); `_engine_runtime`'s legacy fallback now shares
  `legacy_plugin_root()` instead of a second, `AGENT_HOME`-blind
  `USERPROFILE`-only computation; and, most importantly, a **HIGH-severity
  finding**: `_namespaced_plugin_root` originally trusted `pointer.parent`
  after only checking the raw JSON's `pluginId` field, so any
  user-controlled directory containing a minimal `{"pluginId": ...}` blob
  plus a crafted `versions/*/bin/python` would be accepted and later
  executed. Fixed by validating the receipt through the vendored
  `validate_context_receipt` (real schema/version, canonical
  marketplace-id format, and -- critically -- that the receipt sits at the
  exact canonical path derived from its own declared identity under the
  real durable home) instead of trusting any file that merely claims the
  right `pluginId`. Also fixed a self-inflicted regression along the way: an
  early `if profile is None: return False` in `marketplace_cells_enabled()`
  made every POSIX policy check return `False` unconditionally, since
  `_canonical_os_profile` deliberately returns `None` on POSIX so the
  vendored resolver derives the canonical passwd-database home itself.
  New/updated tests include a forged-receipt rejection test and real
  `namespace.json`/`install.json` fixtures built from
  `libs/installation-context/fixtures/source-identities.json`'s existing
  vectors (mirroring the construction `libs/installation-context`'s own
  governance tests use), replacing the earlier minimal JSON stand-ins.

  **A further review round on the same PR found six more issues, all fixed
  before merge:** the vendored `_installation_context.py` (9,169 lines)
  needed a `tools/module-size-baseline.json` entry, exactly like the other
  vendored copies already have, or the module-size guard would fail CI.
  More substantively: `_validated_plugin_root` (renamed
  `_validated_legacy_root`) accepted a bare `install.json` for the **legacy**
  root too, so a forged receipt dropped directly into `~/.agent-worktrees`
  could still bypass `validate_context_receipt` entirely -- fixed by
  restricting the legacy root to the `deploy-manifest.json` shape only, and
  reusing `_namespaced_plugin_root`'s already-fully-validated root
  (never re-validated the weaker way) for the namespaced case.
  `_engine_runtime._context_runtime_root()` still separately checked only
  the raw `pluginId` and returned `pointer.parent` directly, bypassing the
  new validated resolver entirely for the Picker's own import path -- fixed
  by routing it through `agent_plugin_runtime._namespaced_plugin_root`, the
  exact same function `resolve_installed_plugin_command` uses.
  `marketplace_cells_enabled()`'s one global-only policy check couldn't see
  marketplace- or plugin-scoped overrides; `_namespaced_plugin_root` now
  evaluates the effective policy from the *validated receipt's own*
  marketplace id via a second `resolve_installation_mode` call, so a
  marketplace- or plugin-scoped override is honored with full precedence,
  not just the coarse global bit. `_engine_runtime`'s marker walk didn't
  require `.install-complete.json` the way the command resolver does, so an
  in-progress install's `agent_worktrees` package directory could be
  imported early. And several tests set `USERPROFILE`/`HOME` directly, which
  the resolver deliberately ignores on POSIX (by design, to avoid trusting a
  possibly-spoofed variable) -- replaced with a shared `patch_profile` test
  helper (`tests/_installation_context_fixtures.py`) that patches the two
  profile-resolution seams directly, making the tests platform-portable
  instead of silently depending on the real test-runner account's home
  directory. Full `worktree-manager` suite after this round: 806 passed (the
  same 6 pre-existing, unrelated environment failures).
- **2026-09-17** — Authored the ordered plan for Phase 3b Slice 2
  Sub-slice 3 in
  [`phase-3b-substatus-monitor-relocation.md`](phase-3b-substatus-monitor-relocation.md),
  sharpening the earlier 2026-09-14 direction into the operator-mandated
  **two-daemon** architecture: `agent-worktrees` retains the resident
  status-monitor as sole status-data authority, `worktree-manager` gains a
  host-wide companion mux daemon that owns the worktree⇄mux-session/pane
  mapping, Worktree Manager notifies agent-worktrees about live-pane
  create/destroy, and agent-worktrees relays rendered status back through
  Worktree Manager for the actual `set-option` writes. The plan chooses the
  existing lockfile-rendezvous + loopback JSON IPC pattern (mirroring
  `hook_ipc.py` / `classify_daemon.py`) over inventing a new transport, and
  sequences the migration as additive seam → managed-session cutover →
  retirement of the per-session `status-updater` as a manager-owned path.
  Docs-only; no implementation started and no Phase 3b Plan checkbox changed.
- **2026-09-15** — Reconciliation: closed
  [#2532](https://github.com/ThomasMichon/copilot-extensions/pull/2532)
  ("reconcile mux status-bar parity gap + open items") as superseded without
  merging -- its branch predated (and its diff would have reverted) the
  already-landed Sub-slice 2b/2c/4 checkmarks and journal entries above,
  since the exact status-bar gap it tracked as an open checklist item was
  independently found and fixed via Sub-slice 2c (#2666) before this PR was
  reconciled. Extracted its three genuinely new, non-duplicated reconciliation
  notes (not lost in the supersession) directly into this document: the
  Phase 4 "open question" about a generic pluggable control-plane-provider
  registration contract (vs. today's hardcoded `worktree-manager` binstub-name
  probe), the "clarifying note" that no agent-worktrees→Worktree Manager
  creation callback is needed (Worktree Manager already drives creation
  through the `--json` engine boundary and owns launch itself), and the
  Coordination-section link to #2530 (the related `agent-bridge`
  session-discovery gap this effort's own Phase 3b duplicate-implementation
  history motivates). Docs-only.
- **2026-09-15** — Fixed [#2426](https://github.com/ThomasMichon/copilot-extensions/issues/2426)
  ("Worktree Manager/Picker can show/act on the wrong project's content"),
  candidate #1 of its two code-confirmed leads, in
  [#2732](https://github.com/ThomasMichon/copilot-extensions/pull/2732):
  `worktree-manager`'s `_cmd_picker` silently fell back to `projects[0].name`
  -- an arbitrary, registration-order-dependent project, not tied to caller
  intent -- whenever invoked with no explicit project and 2+ projects were
  registered, with no visible error. Confirmed live (via code trace) that the
  common `agent-worktrees` → `worktree-manager` binstub handoff seam always
  threads `--project` explicitly and never hits this path; the bug is only
  reachable via a more direct `worktree-manager picker` invocation with no
  positional project. Fixed by refusing with the full list of registered
  project names instead of guessing, when ambiguous; exactly one registered
  project remains a safe, unambiguous default. Also fixed a real module-size-
  baseline overage this fix itself introduced in `worktree_manager/__main__.py`
  (deliberately bumped the grandfathered ceiling in
  `tools/module-size-baseline.json` to match, per the guard's own documented
  escape hatch) -- unrelated to the two other pre-existing, already-tracked
  module-size failures on `main` (#2572/#2614) confirmed present independent
  of this PR. 4 new regression tests + full `worktree-manager` suite (322
  passed) + golden Picker-capture suite (12 passed, no rendering
  regression). **Candidate #2 from #2426 remains open**: the `<repo> <slug>`
  command-surface router in `agent-worktrees` only threads `--project` for
  `bridge`/`codespaces` (`_PROJECT_ARG_SLUGS`); every other routed sibling
  slug falls back to CWD-based project resolution, which could act on the
  wrong project if invoked from a foreign checkout's CWD. Distinct from the
  Worktree Manager Picker fix above -- affects other sibling plugins, not
  this effort's own Picker/Mux surface -- and is a candidate for whoever
  picks it up next.
- **2026-09-15** — Audited the Picker/Mux duplicate-implementation problem
  captured in the downstream architecture discussion, from the `agent-worktrees` (bundled
  Picker) side: `git log` comparison of the two `engine.py` files since the
  `#1244` transplant shows both sides have continued receiving independent
  commits (agent-worktrees-only: #1938, #2453/#2499, #2589, #2590;
  Worktree-Manager-only: #2355, #2586). Recorded the audit + an ordered
  reconciliation-then-retirement plan (closing Phase 3's parity checklist item
  honestly, then executing Phase 6's deletion of the bundled `picker_tui`) in
  [`phase-3-picker-parity-and-retirement.md`](phase-3-picker-parity-and-retirement.md),
  linked from both phases. This also formally supersedes
  [#117](https://github.com/ThomasMichon/copilot-extensions/issues/117) (a
  smaller, earlier-filed cleanup of just the bundled Picker's native-list
  opt-out toggle) -- Phase 6 now covers deleting the whole module, not just
  its toggle. Docs-only; no code changed. Slice claimed on #352 before
  landing, per this effort's own Coordination-section discipline.
- **2026-09-15** — Re-ran the full `picker_tui/` divergence audit across both
  trees before retirement. Classified the old-only history as: already present
  or superseded (`#1412`'s old Bare Resume warning path, `#1938`'s last-good row
  preservation, `#2453/#2499` superseded by Worktree Manager's `#2586`,
  `#2590` as the reverse-port of that same draft-semantics work, and `#1589`'s
  shared frame-health/reporting additions already landed on both sides), with
  one real remaining Manager gap: the bundled Pickers' later first-paint /
  uncached-local-identity hardening (`#1511`, `#1562`, `#2589`). Ported that
  parity slice into `worktree-manager` (chrome-first live startup, bootstrap row
  preservation until the roster becomes authoritative, lazy config-backed local
  metadata, and neutral placeholders instead of `None` crashes), then retired
  the last rollback-only surface by removing the old `AGENT_WORKTREES_PICKER_NATIVE_LIST`
  toggle so the native list is the sole remaining body. With parity proven by
  the Worktree Manager capture/TUI corpus, checked Phase 3's parity box, deleted
  `plugins/agent-worktrees/src/agent_worktrees/picker_tui/`, moved the
  plugin-still-needed non-UI support into `picker_support/`, updated the
  no-Manager seam/docs/installers to the install-trigger-only end-state, and
  superseded [#117](https://github.com/ThomasMichon/copilot-extensions/issues/117)
  by completion rather than a smaller toggle cleanup. Validation: full
  `worktree-manager` suite green (`843 passed, 2 skipped`), full
  `agent-worktrees` runner suite green, `ruff check --select F,E9`
  clean on both trees, `python tools/check-install-contract.py` still reports
  12 plugins, and a direct bare-launch smoke with no `worktree-manager` on
  `PATH` produced the documented install trigger instead of a crash.
