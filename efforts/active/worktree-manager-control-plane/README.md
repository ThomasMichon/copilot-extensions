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
  out of agent-worktrees, matching the Mux/AHP precedent)
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

## Context

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

### Phase 3b — Relocate Mux + AHP execution mechanics out of agent-worktrees (Planned — #2062)
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
- [ ] **Slice 2 (Mux):** relocate Mux launch/reattach/remux mechanics
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
      [`phase-3b-mux-relocation.md`](phase-3b-mux-relocation.md).
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
      - [ ] **Sub-slice 3 (direction set 2026-09-14; planned 2026-09-17, not yet implemented):**
            split the resident status-monitor's push/observe legs into
            Worktree Manager — agent-worktrees keeps sole ownership of
            accumulating/tracking session status; Worktree Manager takes a
            **companion mux daemon** that owns the worktree⇄mux mapping,
            notifies agent-worktrees when managed worktrees gain/lose live
            panes, and applies the resident monitor's rendered status back
            into mux status bars. Reviewed, ordered plan:
            [`phase-3b-substatus-monitor-relocation.md`](phase-3b-substatus-monitor-relocation.md).
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
- [ ] Update the Worktree Manager Picker to select Mux presentation and/or the
      AHP backend independently per launch/resume/create action, rather than
      assuming exactly one of them.
- [ ] Keep both mechanics fully functional through the relocation — this is a
      location and ownership change, not a behavior regression; existing
      worktrees with a recorded `session_backend` binding must keep resolving
      correctly against the relocated code.

### Phase 3c — Picker non-blocking I/O (Planned)
- [ ] Make every I/O-touching Picker surface — pivot loads (built-in and
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
      [`phase-3c-picker-nonblocking-io.md`](phase-3c-picker-nonblocking-io.md).

### Phase 3d — Retire the Picker's in-process engine-module boundary (Planned — #3359, #3360)

_(agent-recommended scoping below the two linked issues; the issues
themselves are operator-filed.)_

`production_picker/_engine_runtime.py` — its own docstring calls it a
"temporary compatibility boundary" — is the last major violation of the
picker vision's process-boundary-only Non-Goal: 9 `agent_worktrees.*`
submodules (`config`, `profiles`, `pr_ops`, `reclaim`, `sessions`,
`tracking`, `__main__`, `update_stage`, `state_root`) are imported
**in-process** via whole-module `__getattr__` proxies or inline
`engine_module(name)` calls, sharing agent-worktrees' own venv/sys.path
instead of going through the `--json` engine boundary every other Picker
read path uses. This directly caused two live production bugs already fixed
this session (#3319, #3327). #3359 is the mechanical, low-risk half
(vendor the 3 borrowed shared libs, `agent-procutil`/`dropin-registry`/
`plugin-activation`) — **done**, PR
[#3368](https://github.com/ThomasMichon/copilot-extensions/pull/3368).
#3360 is the harder half (the CLI-root modules themselves) and needs real
design, not a blind mechanical conversion — see
[`phase-3d-engine-runtime-retirement.md`](phase-3d-engine-runtime-retirement.md)
for the full call-site inventory and the performance-tradeoff analysis behind
each checkbox below.

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
- [ ] **Design + convert the low-frequency, one-shot CLI-root reads.**
      `pivot_manifest.py`'s `config.install_dir()` / `config._home()` /
      `state_root_module.resolve_state_root(...)` run once per pivot-registry
      scan pass, not per render frame — the safest subprocess-conversion
      candidates. Needs: confirm/add a stable `--json` verb for each
      (install dir, state root), matching the existing
      `docs/engine-picker-contract.md` pinning discipline Phase 3 already
      established for `engine_client`.
- [ ] **Design the `runner.py` process-lifecycle call sites — likely NOT a
      subprocess conversion at all.** `_start_anchor_heal_check`,
      `reap_orphan_mux_sessions`, `_sweep_managed_on_exit`,
      `_sweep_launcher_shells_on_exit`, `_sweep_finished_sessions_on_cadence`,
      and `_start_picker_monitor_root` are private (`_`-prefixed),
      never-designed-for-external-callers `agent_worktrees.__main__`
      internals that manage the **Picker's own process lifetime**
      (background threads, exit-time sweeps) — not agent-worktrees state
      reads. A subprocess can't run "in this process's exit handler"; the
      real fix is likely reclassifying this logic as worktree-manager's own
      owned concern (or a new shared lib), not a CLI verb.
- [ ] **Design the `data_local.py` hot path — needs a new batched verb, not
      per-attribute conversion.** `tracking.list_records` /
      `tracking._pr_is_terminal` / `pr_ops._reconcile_active_pr` /
      `reclaim.resolve_bound_copilots` / `sessions.mux_status_many` /
      `sessions.worktree_session_lock_state` / `tracking.stamp_*` run as one
      read-reconcile-**write** loop over every managed worktree record, once
      per Picker refresh (Phase 3c's synchronous hot path) — `stamp_*`
      mutates `tracking.yaml` in-process using agent-worktrees' own file
      lock. Naive per-attribute subprocess conversion would multiply
      per-refresh subprocess spawns by up to 6× the worktree count and can't
      hold a cross-process lock across separate calls. Needs one new atomic,
      batched `--json` verb (read + reconcile + stamp in a single
      agent-worktrees-owned call) before any of this path can move off the
      in-process boundary — sequence this **after** Phase 3c's non-blocking
      I/O work lands, since both touch the same call site.
- [ ] Retire `_engine_runtime.py` (and its 9 proxy-module shims) once every
      call site above has converted or been reclassified.

### Phase 3e — Relocate terminal-profile handling out of agent-worktrees (Planned — #3390)

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
  - [ ] **Step 5b — repoint `install.ps1`'s** `Deploy-TerminalScripts`/
        `Sync-TerminalState`/`Get-SettingsProfileGuids`/
        `Clean-TerminalSettingsJson` at the new owner, following Phase 3b
        Slice 2a's launcher-script repoint pattern.
  - [ ] **Step 5c — live-machine validation trial** of `--live` against a
        real installed Windows Terminal, before it's treated as proven safe.
- [ ] **Step 6 — clean, decisive cutover** (Mux/AHP precedent): delete
      agent-worktrees' `profiles`/`terminal-fragment` CLI verbs, the
      terminal-mirroring parts of `repair`, and (pending the bundled-Picker
      disposition question) `picker_support/data_local.py`'s/
      `profiles_io.py`'s Profiles-grid path — the actual deletion commit,
      kept last and separate for revertability.

### Phase 4 — Bare-invocation seam & handoff (Plugin side landed; end-state pending)
- [x] Plugin binstub seam resolves a no-args launch to a **usable** Manager on
      `PATH` (health-probed), else the still-bundled Picker, else the install
      trigger; a stale/incompatible `worktree-manager` stub can never capture the
      seam. Realizes installer §`bare-invocation-launches-configurator`,
      §`control-plane-is-optional-plugins-are-self-sufficient`.
- [ ] Onboarding polish for the **absent-Manager** path: the install trigger reads
      as a **guided first-run onboarding**, not an error, and points at the
      trustworthy bootstrap. Closes picker §`first-run-onboarding-entry`, installer
      §`onboards-from-empty-gracefully`.
- [ ] **Open question, not yet designed:** the bare-invocation seam currently
      health-probes specifically for a `worktree-manager` binstub on `PATH` --
      it is not yet a generic, pluggable **registration** a third-party
      control-plane provider could satisfy without being named `worktree-manager`
      literally. If the seam is meant to stay open to a future alternative
      Picker/control-plane provider (not just the one shipped here), this needs
      an explicit registration contract (e.g. a well-known marker file/env var/
      capability probe any conforming provider can satisfy), not a hardcoded
      binstub-name check. Until that's decided, the seam is de facto
      single-provider.
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
- [ ] `doctor`/validation breadth, plugin updating & alignment, and
      git-referenced presets. Closes installer §`health-doctoring-and-validation`,
      §`plugin-updating-and-alignment`, §`git-referenced-presets`.

### Phase 8 — Reconcile deferred backlog

- [ ] Accept manager and worktree-control candidates only through
      [`migration-intake`](../migration-intake/README.md)'s deduplication and
      ownership gate.
- [ ] Revalidate accepted technical scope against the current installer, picker,
      and engine contracts; return obsolete or unsafe candidates for explicit
      disposition.
- [ ] Place each accepted public tracker item in exactly one existing phase,
      extending this plan before implementation when necessary.
- [ ] Cached worktree-status projection for external consumers: expose a
      manager/engine-backed, cached worktree-status view (e.g. the
      agent-dispatch Tasks-pane's Worktree Status card) over the `--json`
      engine boundary so external consumers stop polling `agent-worktrees`
      directly per render. In flight under the
      `agent-worktrees-external-status-accelerator` effort/PR
      [#3102](https://github.com/ThomasMichon/copilot-extensions/pull/3102)
      (not yet merged); no separate public issue is needed unless that PR
      does not land, since it already covers this exact scope.
- [ ] Keep configuration examples synthetic and repository-neutral.

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

## Coordination

`copilot-extensions` is public and may be driven from more than one private
control repo. **[#352](https://github.com/ThomasMichon/copilot-extensions/issues/352)
is the shared coordination token** for the remaining Worktree Manager work; claim
a slice there (comment/assign) before starting, and land changes serially through
the PR-required `main`. Downstream private plans may **link to** this effort and
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
  [`phase-3c-picker-nonblocking-io.md`](phase-3c-picker-nonblocking-io.md)
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
  aperture-labs #6764 was filed against, from the `agent-worktrees` (bundled
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
