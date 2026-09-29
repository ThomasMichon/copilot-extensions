# Phase 3d — Retire the Picker's in-process engine-module boundary (`_engine_runtime.py`)

- **Parent effort:** [`README.md`](README.md) § Phase 3d
- **Tracks:** [#3359](https://github.com/ThomasMichon/copilot-extensions/issues/3359),
  [#3360](https://github.com/ThomasMichon/copilot-extensions/issues/3360)
- **Scope of this doc:** the ordered implementation plan for removing the
  Picker's last in-process `agent_worktrees.*` import boundary, building on the
  evidence-gathering inventory already recorded here.
- **Status:** Done — #3359's
  vendored-lib prework is done via PR
  [#3368](https://github.com/ThomasMichon/copilot-extensions/pull/3368),
  Group D's `profiles` dependency is closed via Phase 3e / PR
  [#3626](https://github.com/ThomasMichon/copilot-extensions/pull/3626), and
  **Groups A and B are implemented in PRs
  [#4317](https://github.com/ThomasMichon/copilot-extensions/pull/4317),
  [#4322](https://github.com/ThomasMichon/copilot-extensions/pull/4322),
  [#4323](https://github.com/ThomasMichon/copilot-extensions/pull/4323), and
  [#4324](https://github.com/ThomasMichon/copilot-extensions/pull/4324)**:
  the Picker's `pivot_manifest.py`, `update_stage.py`, and `runner.py` paths
  now use the pinned `picker-paths --json`, `state-root --json`,
  `stage-update --indicator-state --json`, `picker-bootstrap --json`,
  `repair-stale-anchor --json`, and `resolve --json` seams, plus the
  Manager-owned housekeeping / monitor-root lifecycle ports. Phase 3c's
  prerequisite for Group C is satisfied by PR
  [#4278](https://github.com/ThomasMichon/copilot-extensions/pull/4278), and
  Step 8 closes the 2026-09-27 `housekeeping.py` re-audit gap by moving the
  remaining Picker-owned lifecycle sweeps off in-process imports too.

## Why this phase exists

`worktree-manager/src/worktree_manager/production_picker/_engine_runtime.py`
is self-documented, in its own module docstring, as a **"Temporary
compatibility boundary to the active agent-worktrees runtime."** It resolves
the active agent-worktrees install (namespaced-then-legacy slot, or a local
checkout fallback), injects its `src/` plus several of its vendored
`libs/*/src` onto `sys.path`, and lets the Picker `importlib.import_module()`
historically 9 `agent_worktrees.*` submodules directly, in-process — now down
to 8 live runtime/root consumers after Phase 3e retired the `profiles` proxy,
but still a different plugin's own private CLI implementation rather than a
shared library. This already caused two live production bugs this session
(#3319, #3327): a lazily-populated
`agent_worktrees.__main__` attribute silently missing because importing the
module directly bypasses agent-worktrees' own CLI dispatch that would
normally populate it.

Two GitHub issues track the fix, split by risk:

- **[#3359](https://github.com/ThomasMichon/copilot-extensions/issues/3359)**
  — mechanical, low-risk: vendor worktree-manager's own copies of the 3
  shared libs (`agent-procutil`, `dropin-registry`, `plugin-activation`) it
  currently only reaches by riding along on `_engine_runtime.py`'s sys.path
  injection. **Done** — PR
  [#3368](https://github.com/ThomasMichon/copilot-extensions/pull/3368).
- **[#3360](https://github.com/ThomasMichon/copilot-extensions/issues/3360)**
  — the harder half: the 9 `agent_worktrees.*` CLI-root modules themselves.
  Explicitly filed "for discussion, not prescriptive" because it needs a real
  design tradeoff for the Picker's interactive/high-frequency reads. This doc
  preserves that evidence base and now turns it into the ordered implementation
  plan for the remaining work.

This is a **design/planning** artifact first, matching Phase 3c's own
pattern: record the full current-state call-site inventory (evidence, not
assumption) before committing to a conversion shape, since the issue's own
"one JSON verb per module" framing turns out not to fit several of the real
call sites.

## Call-site inventory (evidence-based)

Every actual attribute access reached through the 9 proxy modules /
`engine_module(name)` calls, grep-verified against
`worktree-manager/src/worktree_manager/production_picker/` (not just the
9 shim files, which are pure `__getattr__` pass-throughs and carry no logic
of their own).

### Group A — `pivot_manifest.py`: low-frequency, one-shot reads (safest conversion candidates)

```
config.install_dir()                                     # pivot_manifest.py:807
config._home()                                            # pivot_manifest.py:826
state_root_module.resolve_state_root(config_module.load_config())  # pivot_manifest.py:583
```

Runs once per pivot-registry scan pass (not per render frame). A genuine
subprocess-per-call is affordable here. **Concrete verb design for the Group A
conversion:**

- **New:** `<project> picker-paths --json` → `{"version": 1, "install_dir":
  "<abs>", "installed_plugins_dir": "<abs>"}`. This is the narrowest
  engine-owned shape that preserves the two actual downstream uses without
  promoting `config._home()` itself as public API: `pivot_manifest.pivots_dir()`
  still derives `<install_dir>/pivots`, and `installed_plugins_dir()` gets the
  exact engine-owned marketplace root directly instead of reconstructing it from
  a private helper.
- **Reuse as-is:** `<project> state-root --json` already emits the needed
  versionless resolution envelope (`state_root`/`source`/`repo`/`stateless`/
  `requires_external`/`bound`/`error`). `pivot_manifest.py` should switch to
  the subprocess client seam and read `state_root`.
- **Additive extension:** `<project> stage-update --indicator-state --json` →
  `{"version": 1, "indicator_state": "paused|checking|available|current|idle"}`
  as the pure read-only counterpart to the existing mutating staging verb.
  A newer Manager should treat an older engine rejecting `--indicator-state` as
  feature-unavailable and degrade the cosmetic glyph to `"idle"` rather than
  failing Picker startup.

All three remain **contract version 1** changes: one new pinned read verb, one
reuse of an already-pinned read verb, and one additive flag/payload on an
existing verb.

**Landing note — PR [#4317](https://github.com/ThomasMichon/copilot-extensions/pull/4317).**
This slice shipped exactly those seams plus the caller cutover: Worktree
Manager now routes `pivot_manifest.py` through subprocess helpers in
`production_picker.engine_group_a` (built on the same `engine_client.run_json`
pattern as the rest of the Picker), `update_stage.py` reads the additive
indicator verb and degrades older engines to `"idle"`, `_engine_runtime.py`
is no longer consulted for `state_root` or `update_stage`, and the remaining
boundary is explicitly narrowed to Group B/C's `config`, `__main__`, `pr_ops`,
`reclaim`, `sessions`, and `tracking` consumers. Validation in the PR:
targeted new contract/regression tests green; full `agent-worktrees` suite
matched the current unrelated baseline at `5733 passed, 50 skipped, 5 failed`;
full `worktree-manager` suite (excluding the two standing hangs) matched the
current unrelated baseline at `1260 passed, 2 skipped, 13 failed`; `ruff
check --select F,E9` passed; `check-install-contract.py` and
`check-version-consistency.py` passed; `check-version-bump.py` still reports
the same pre-existing unrelated unbumped-plugin drift on `origin/dev`.

### Group B — `runner.py`: private process-lifecycle internals (likely NOT a subprocess conversion at all)

```
cli._resolve_active_project(project)          # runner.py:31  (in _prepare(), called on every project activation)
cli._cwd_is_inside_project(assumed)           # runner.py:35
cli._in_ssh_session()                         # runner.py:39
config_module.set_active_project(resolved)    # runner.py:32
config_module.load_config()                   # runner.py:59, 152
cli._heal_stale_anchor_if_self_missing(config)# runner.py:62  (fire-and-forget background thread)
cli.reap_orphan_mux_sessions()                # runner.py:78
cli._sweep_managed_on_exit()                  # runner.py:81
cli._sweep_launcher_shells_on_exit()           # runner.py:82
cli._sweep_finished_sessions_on_cadence()     # runner.py:83
cli._start_picker_monitor_root()              # runner.py:109
config_module.load_machines_yaml(...)         # runner.py:154
cli._machine_key_for_display(config, machine) # runner.py:157
cli._resolve_ssh_alias(entry)                 # runner.py:196
```

Every `cli.*` name here is **underscore-prefixed** — a private
`agent_worktrees.__main__` helper never designed for an external caller at
all, let alone a stable CLI verb. Several of them (`reap_orphan_mux_sessions`,
the three `_sweep_*_on_exit` calls, `_start_picker_monitor_root`) manage
**the Picker's own process lifetime** — background threads, exit-time
cleanup sweeps, monitor-root selection for *this* running process — not a
read of agent-worktrees' on-disk state. A subprocess fundamentally cannot
run "inside this process's exit handler" or "as this process's background
monitor thread"; converting these to CLI calls is the wrong shape regardless
of call frequency.

**This is the key finding that revises the issue's own "9 read paths, each
needs a JSON verb" framing:** at least this whole cluster is not a read-path
problem. The real fix is more likely one of:

- reclassify this logic as **worktree-manager's own owned process-lifecycle
  concern** (it already owns the Picker process; it should not need
  agent-worktrees' private internals to manage its own threads/exit sweeps
  at all), reimplemented directly in worktree-manager, or
- promote a genuinely narrow, stable, *public* API on `agent_worktrees` for
  the specific decisions that really are agent-worktrees' to own (e.g.
  "resolve the active project for this cwd", "resolve an ssh alias for this
  machine entry") — decided per call site, not as a block.

Needs an explicit per-call-site disposition (own-it vs. promote-a-public-API)
before any conversion work starts here.

### Group C — `data_local.py`: the Picker's live hot path (needs a new batched verb, not per-attribute conversion)

```
tracking.list_records(tracking_path, platform_filter=plat)   # data_local.py:110, 174
tracking._pr_is_terminal(active)                              # data_local.py:118, 125
pr_ops._reconcile_active_pr(rec, config, best_effort=True)    # data_local.py:121
reclaim.resolve_bound_copilots()                              # data_local.py:183
sessions.mux_status_many([...])                                # data_local.py:208
tracking.stamp_bound_live(rec.worktree_id, True/False, ...)   # data_local.py:217, 223
tracking.stamp_mux_live(rec.worktree_id, ...)                  # data_local.py:240, 243
sessions.worktree_session_lock_state(rec)                      # data_local.py:271
sessions._normalize_path(rec.worktree_path)                    # data_local.py:348
tracking.stamp_session_state(...)                              # data_local.py:349
```

This is `data_local.py` — the Picker's **local single-machine worktree data
source**, run once per Picker refresh (Phase 3c names this exact call site as
its synchronous hot path: "initial mount, the `r` reload key, and two
post-action rescans"). It is not a set of independent reads: it is one tight
**read → reconcile → write** loop over every managed worktree record per
refresh —

1. read the tracking records (`list_records`),
2. reconcile each record's active PR state, potentially making a network
   call (`_reconcile_active_pr`, best-effort),
3. read live process/mux state (`resolve_bound_copilots`,
   `mux_status_many`, `worktree_session_lock_state`),
4. **write** the reconciled/observed state back
   (`stamp_bound_live`/`stamp_mux_live`/`stamp_session_state`) —
   `tracking.py`'s own file-lock-guarded mutation of `tracking.yaml`.

Two of the referenced names (`tracking._pr_is_terminal`,
`sessions._normalize_path`) are also private/underscore, though here they
read as pure helper logic rather than process-lifecycle actions.

Converting this **per attribute** to separate subprocess calls would (a)
multiply per-refresh subprocess spawns by up to ~6× the live worktree count
(every record potentially triggers reconcile + 2-3 stamps), and (b) cannot
preserve the read-then-write atomicity `tracking.py`'s in-process file lock
currently gives this loop across process boundaries — a subprocess call
cannot hold a lock open across a second, later subprocess call.

**This needs a new, single, atomic `--json` verb** that performs the whole
read-reconcile-stamp cycle for a batch of worktree records in one
agent-worktrees-owned call, before this call site can move off the
in-process boundary at all. **Sequence this after Phase 3c's non-blocking
I/O work lands** — both touch this exact call site, and Phase 3c's own
generation/epoch-guarded background-task primitive is a prerequisite for
calling a (necessarily slower, subprocess-based) batched verb from the
render thread without reintroducing the blocking-I/O problem Phase 3c
exists to fix.

### Group D — `profiles.py`: not a CLI-root problem, and not a vendoring problem either — a full relocation (superseded by #3390)

```
profiles_mod.TargetSel(...)              # engine_profiles_view.py:119, profiles_io.py:25
profiles_mod.is_default_on(...)          # engine_profiles_view.py:164
profiles_mod.has_selection(cfg_path)     # profiles_io.py:68
profiles_mod.load_selection(cfg_path)    # profiles_io.py:70
profiles_mod.normalize_selection(...)    # profiles_io.py:71
profiles_mod.self_diagonal(machine, env)# profiles_io.py:96
profiles_mod.save_selection(...)         # profiles_io.py:116
```

Every one of these takes a **caller-supplied config path** (`cfg_path`) and
returns/consumes a plain dataclass (`TargetSel`) or primitive — none of it
reads or mutates agent-worktrees' own runtime state. It is pure,
dependency-free logic, structurally identical to `agent_procutil` /
`dropin_registry` (#3359's vendored libs), not a CLI-internals problem at
all — this analysis originally proposed vendoring it as a 4th shared lib,
exactly like #3359.

**Superseded by operator direction:** `agent_worktrees.profiles` is not
Picker-only — it also backs agent-worktrees' own public `profiles` /
`terminal-fragment` / `repair` CLI verbs and the installer's real
Windows-Terminal-fragment mirror step (`terminal_fragment.py`). Vendoring a
byte-identical copy would leave agent-worktrees as the canonical owner
while the Picker rode a duplicate. The actual direction is a **full
relocation**, matching the standing Mux/AHP precedent (#2062): terminal
handling of every kind — including which terminal-app profiles exist on a
machine, not just Mux/AHP session mechanics — is leaving agent-worktrees
for the Worktree Manager control-plane, in phases. Tracked as its own
phase, not folded into this one, since it also touches agent-worktrees' own
CLI surface: see Phase 3e (#3390) and
[`visions/plugins/agent-worktrees`](../../../visions/plugins/agent-worktrees/README.md#non-goals--boundaries) /
[`visions/installer`](../../../visions/installer/README.md#optional-worktree-agent-control-plane).

### `update_stage.py`

Deferred-resolution shim (see its own docstring) backing only the Picker's
cosmetic version-update indicator glyph — genuinely low-frequency (called
at most once per Picker session, after first paint). Same disposition as
Group A: a safe, low-risk subprocess-conversion candidate once a `--json`
verb exists for "is an update available."

## Revised remediation shape (supersedes the issue's "9 uniform read paths" framing)

| Group | Modules | Real shape | Disposition |
|---|---|---|---|
| A | `config` (pivot_manifest only), `state_root`, `update_stage` | low-frequency, one-shot reads | convert via `picker-paths --json`, existing `state-root --json`, and `stage-update --indicator-state --json` |
| B | `__main__`/`cli.*`, `config` (runner.py's `set_active_project`/`load_config`/`load_machines_yaml`) | private process-lifecycle internals, several managing the Picker's *own* process | reclassify as worktree-manager-owned logic, or promote a narrow public API per call site — **not** a uniform subprocess conversion |
| C | `tracking`, `pr_ops`, `reclaim`, `sessions` (all via `data_local.py`) | one atomic read-reconcile-write hot-path loop, per Picker refresh | needs one new **batched** `--json` verb; sequence after Phase 3c |
| D | `profiles` | also agent-worktrees' own installer/CLI dependency, not Picker-only | full relocation out of agent-worktrees (Phase 3e / #3390) — not a vendored copy, not a CLI conversion |

`_engine_runtime.py` retires only once every group above has either
converted, been reclassified, or (Group D) moved out from under
agent-worktrees entirely.

One cleanup wrinkle the table above does not spell out: the generic
`production_picker.config` proxy is still imported by
`picker_tui/data_local.py`, `data_ssh.py`, `engine_loading.py`,
`profiles_io.py`, `roster.py`, `picker_tui/__init__.py`, and
`engine_worktree_actions.py`'s authoritative liveness check (which also still
imports the `sessions` and `tracking` proxies). Those do **not** form a fourth
design group with a new disposition question, but they **do** mean Step 7
cannot delete the `config` shim — or the remaining `sessions` / `tracking`
shims — merely because `runner.py`, `pivot_manifest.py`, and `update_stage.py`
are done. The ordered steps below therefore treat those remaining proxy
consumers as part of the Group C / final-cleanup work that must be drained
before the last shim deletion lands.

## Ordered implementation steps

Group D needs no further work here: the `profiles` branch of this inventory
was split into Phase 3e and is now fully done, so this phase's ordered plan
only has to retire the remaining Group A/B/C call sites. The sequence below
keeps the same discipline as Phase 3b / 3c / 3e: additive seam first, one
crisp cutover once the seam is proven, cleanup last.

Group A and both Group B clusters are independent of Phase 3c's loader work
and can proceed immediately. Group C's only hard prerequisite was the
epoch-guarded non-blocking setup/reload path from Phase 3c, and that is now
landed (PR #4278), so Group C is no longer blocked — it is merely sequenced
after its additive verb work so the final `_engine_runtime.py` deletion happens
once every remaining caller is already off the import boundary.

1. [x] **Pin and cut over Group A's low-frequency public read surface.**
   **Done in PR [#4317](https://github.com/ThomasMichon/copilot-extensions/pull/4317).**
   - Added/pinned `<project> picker-paths --json` for the two actual
     `pivot_manifest.py` path reads (`install_dir` and the derived installed
     plugins root), reused `<project> state-root --json` for the visibility gate,
     and extended `stage-update` additively with `--indicator-state --json`
     rather than inventing a second update-status command.
   - Kept the reads explicitly project-scoped by binding the production Picker's
     project in parent-owned context and routing the subprocess calls through
     `production_picker.engine_group_a`, which reuses `engine_client.run_json`
     and its error-envelope handling rather than restoring any in-process import.
   - Cut `pivot_manifest.py` and `update_stage.py` over in the same PR because
     both are genuinely one-shot/non-hot-path callers; older-engine rejection of
     `--indicator-state` degrades the cosmetic glyph to `"idle"`.
   - Documented the new/extended verbs in
     `plugins/agent-worktrees/docs/engine-picker-contract.md` and added
     contract/regression coverage on both sides of the seam.

2. [x] **Promote Group B's project/config/ssh decisions to a narrow public CLI
   seam, additive only.** **Done in PR [#4322](https://github.com/ThomasMichon/copilot-extensions/pull/4322).**
   This is public `--json` CLI surface, not a
   stable importable Python API: the governing contract for the control plane is
   still "Picker reaches agent-worktrees only through CLI verbs," and replacing
   `_engine_runtime.py` with another import surface would preserve the coupling
   this phase exists to remove.
   - **Concrete Step 2 seam design (decided before implementation):**
     - Add `<project> picker-bootstrap --json` as a new versioned bootstrap read
       returning
       `{"version":1,"project":"<resolved-project>","should_switch_cwd":<bool>,"cwd":"<normalized-abs-path>|null","default_live":<bool>}`.
       This deliberately exposes the **decision** `runner._prepare()` needs
       (authoritative project id + cwd-switch + default live/local mode), not
       the private helper names or intermediate config objects.
     - **No `resolve --json` payload/version change is planned for Step 2.**
       The existing remote-launch envelope already carries the production
       Picker's needed machine/environment answer
       (`action`/`ssh_alias`/`remote_command`/`machine`/`display_name`) for
       `--machine` / `--environment` / `--target-no-mux`; Step 2 reuses that
       seam as-is rather than widening it speculatively.
     - Add `<project> repair-stale-anchor --json` as a targeted, one-shot repair
       action returning
       `{"version":1,"project":"<resolved-project>","status":"unchanged|repaired|still-missing","self_present_before":<bool>,"self_present_after":<bool>}`.
       This replaces the effect of `_heal_stale_anchor_if_self_missing` without
       making the Picker depend on the helper's private import path.
     - On the Manager side, extend the existing
       `production_picker.context` binding surface with a versioned project
       bootstrap record (resolved project + cwd-switch + default_live) so
       downstream Picker/data helpers can consume the parent-owned binding once
       Step 4 cuts `runner.py` over, without introducing a new global context
       mechanism.
   - Landing details:
     - Added/pinned `picker-bootstrap --json` + `repair-stale-anchor --json` in
       `agent-worktrees`, documented both in
       `plugins/agent-worktrees/docs/engine-picker-contract.md`, and confirmed
       Step 2 needed **no** `resolve --json` payload change beyond reusing the
       already-pinned remote launch plan.
     - Added `worktree_manager.production_picker.engine_group_b` plus a
       parent-owned `production_picker.context.ProjectBootstrap` record so the
       eventual cutover can bind the authoritative engine answer once and reuse
       it across downstream Picker/data consumers.
     - Kept this PR additive-only: no `runner.py` call site moved yet, and the
       old in-process path remains the live production route until Step 4.
     - Validation: targeted Group B seam tests green on both sides; full
       `worktree-manager` suite (excluding the two standing hangs) matched the
       current unrelated baseline at `1274 passed, 2 skipped, 13 failed`; full
       `agent-worktrees` suite on this machine remained red only in unrelated
       baseline families (`5733 passed, 50 skipped, 7 failed`:
       `test_launch_cmd`, `test_lazy_dispatch`, `test_module_invocation`,
       `test_mux_status_link`, `test_profile_assignment`,
       `test_session_conduct`); `ruff check --select F,E9`,
       `tools/check-install-contract.py`, and
       `tools/check-version-consistency.py` passed, while
       `tools/check-version-bump.py` still reports the pre-existing unrelated
       unbumped-plugin drift on `delegation-guidance`, `efforts`,
       `harness-knowledge`, and `wsl-setup`.
   - Add one runner-scoped bootstrap verb (for example
     `<project> picker-bootstrap --json`) that returns the high-level decisions
     `runner._prepare()` actually needs: resolved project identity, whether the
     caller should switch cwd, the normalized cwd to switch to when needed, and
     the default live-vs-local mode. This replaces the *effect* of
     `_resolve_active_project`, `_cwd_is_inside_project`, `_in_ssh_session`, and
     `set_active_project` without exporting those private helpers one-by-one.
   - Pair that verb with a **parent-side binding step** in worktree-manager:
     once bootstrap resolves the authoritative project identity, the parent
     process records it in Manager-owned context and subsequent Picker/data
     helpers consume that bound identity instead of ambient cwd or a one-off
     child-process answer. The cutover is not complete until those downstream
     consumers are migrated to the parent-owned binding.
   - Reuse the already-pinned `<project> resolve --json ...` remote-launch seam
     as the public answer for machine/environment resolution. If a small gap
     remains for production Picker parity, close it there instead of teaching
     worktree-manager to call `load_config`, `load_machines_yaml`,
     `_machine_key_for_display`, or `_resolve_ssh_alias` directly.
   - Add a one-shot public verb for the stale-anchor repair hook (for example a
     picker-specific `repair-stale-anchor --json`, or an equivalent targeted
     `repair` subcommand) so `_heal_stale_anchor_if_self_missing` no longer
     rides a private in-process import either.

3. [x] **Reimplement Group B's Picker-owned lifecycle sweeps directly in
   worktree-manager, additive first.** **Done in PR
   [#4323](https://github.com/ThomasMichon/copilot-extensions/pull/4323).**
   Operator direction resolved this cluster:
   `reap_orphan_mux_sessions`, `_sweep_managed_on_exit`,
   `_sweep_launcher_shells_on_exit`, `_sweep_finished_sessions_on_cadence`, and
   `_start_picker_monitor_root` become Worktree Manager-owned logic because they
   govern the Picker's own process lifetime, not agent-worktrees' state model.
   - **Module home (landed):** the four sweep/cadence functions now live in
     `worktree_manager.production_picker.housekeeping`, with the Picker
     liveness root ported into sibling module
     `worktree_manager.production_picker.monitor_roots`. This deliberately does
     **not** extend the Phase 3b companion mux daemon itself: that daemon stays
     the resident cross-process mux/status writer, while these Group B behaviors
     are synchronous Picker-launch / Picker-exit / session-end housekeeping
     owned by the foreground Picker process.
   - **Manager-owned identification rule (recorded for Step 4's cutover):**
     Manager-owned mux-session names come from Worktree Manager's live
     `mux-mapping.json` registry; Manager-owned tracking rows/worktree ids are
     that registry's ids plus rows whose resolved `execution_leg.provider` is
     `ahp`; Manager-owned launcher shells are the orphan-shell candidates whose
     positive launcher signature resolves to the relocated
     `worktree-manager/bin/launch-session.*` / `pane-wrapper.*` path; Picker
     monitor roots intentionally keep the existing
     `status-monitor-roots.d/picker-*.json` schema so the resident monitor can
     keep consuming Picker liveness with no second protocol change.
   - **Coordination-boundary decision:** this step stays additive and does
     **not** change live agent-worktrees behavior yet. Step 4 will enable the
     Manager-owned lane and, in the same cutover, teach agent-worktrees'
     generic lifecycle sweepers to exclude Manager-owned targets by the rules
     above (or consume the Manager-produced ownership view) so only one side
     mutates a given mux session / tracking row / launcher-shell lane at a
     time.
   - Landing details:
     - Added `housekeeping.py` with a Manager-owned port of
       `reap_orphan_mux_sessions` plus the three lifecycle-boundary sweep
       wrappers, while keeping future ownership-cutover hooks explicit via
       helper functions for Manager-owned mux-session / worktree / launcher
       classification.
     - Added `monitor_roots.py`, a Manager-owned port of
       `agent_worktrees.monitor_roots`, preserving the engine-consumed on-disk
       heartbeat schema while moving Picker-root creation under Worktree
       Manager-owned code.
     - Added Worktree Manager tests proving parity against the current engine
       behavior for the new Group B module (`test_housekeeping.py`) and for the
       monitor-root port (`test_monitor_roots.py`).
     - Kept this step additive-only: `production_picker.runner` still uses the
       current compatibility path, and this PR does **not** change
       agent-worktrees' live sweeper behavior yet.
     - Validation: targeted new Group B parity tests green; full
       `worktree-manager` suite (excluding the two standing hangs
       `tests/production_picker/test_data_ssh_sources.py` /
       `tests/production_picker/test_launch_trace.py`) matched the current
       unrelated baseline at `1274 passed, 2 skipped, 13 failed`; full
       `agent-worktrees` suite matched the current unrelated baseline at `5733
       passed, 50 skipped, 7 failed`; `ruff check --select F,E9` passed for
       both packages; `python tools/check-install-contract.py` and
       `python tools/check-version-consistency.py` passed; `python
       tools/check-version-bump.py` still reports the same pre-existing
       unrelated unbumped-plugin drift on `delegation-guidance`, `efforts`,
       `harness-knowledge`, and `wsl-setup`.

4. [x] **Perform the remaining Group B cutover in one crisp PR.** **Done in PR
   [#4324](https://github.com/ThomasMichon/copilot-extensions/pull/4324).**
   Once Steps 2-3 were landed, the remaining non-hot-path `runner.py` call
   sites moved off the compatibility boundary together.
   - `runner.py` now binds `context.ProjectBootstrap` from
     `engine_group_b.picker_bootstrap()`, changes cwd only from that payload's
     `should_switch_cwd`/`cwd` decision, schedules background stale-anchor
     repair through `engine_group_b.repair_stale_anchor()`, resolves the
     default live/local mode from the bound bootstrap record, and swaps the
     old `agent_worktrees.__main__` housekeeping/monitor calls for
     `production_picker.housekeeping` / `monitor_roots`.
   - The parent-owned binding is now the authoritative project identity for
     downstream consumers that already read `context.project()` /
     `context.project_bootstrap()` (`pivot_manifest.py`, `update_stage.py`,
     `maintenance.py`, `data_local.py`, `engine_worktree_actions.py`, and the
     Step 3 housekeeping/monitor modules). The production Picker no longer
     relies on ambient cwd or per-call in-process answers once bootstrap
     resolves the project.
   - `worktree_manager.__main__`'s old-engine remote fallback was **retired,
     not reimplemented**. `resolve --json --machine ...` is already the pinned
     public seam for remote planning; the private fallback existed only for
     engines too old to support that seam, so cutting it over would have
     preserved the exact boundary violation this phase is removing. Older
     engines now fail clearly with `EngineFeatureUnavailable` instead of
     silently importing `agent_worktrees.__main__`.
   - `production_picker.__main__` is deleted and the direct Group A/B
     `runner.py` / `pivot_manifest.py` / `update_stage.py` call sites no longer
     import `agent_worktrees` in-process. The remaining `_engine_runtime.py`
     surface is **9** engine modules total: the **5** legacy proxy/shim
     modules (`config`, `pr_ops`, `reclaim`, `sessions`, `tracking`) plus the
     **4** explicit Step 3 housekeeping-owned imports
     (`activity`, `gc`, `reap_cli`, `status_monitor_runtime`) that still ride
     the compatibility boundary until the later cleanup work.
   - Validation: targeted Group B seam + cutover regressions passed, including
     the bootstrap/repair coverage in `test_context_resolution.py`, the
     Worktree Manager `test_engine_group_b.py` / `test_housekeeping.py` /
     `test_production_picker_transplant.py` lane, and the preview/reaper
     regressions added for this step. Full `worktree-manager` suite (excluding the two standing hangs
     `tests/production_picker/test_data_ssh_sources.py` /
     `tests/production_picker/test_launch_trace.py`) matched the current
     unrelated baseline at `1290 passed, 2 skipped, 11 failed`. Full
     `agent-worktrees` suite stayed red only in unrelated existing families on
     this machine at `5732 passed, 50 skipped, 9 failed`
     (`test_launch_cmd`, `test_lazy_dispatch`, `test_module_invocation`,
     `test_mux_status_link`, `test_registration_home`,
     `test_session_conduct`, `test_status_monitor_windows`); no failures
     touched the Group B seam. `ruff check --select F,E9`,
     `tools/check-install-contract.py`, and
     `tools/check-version-consistency.py` passed; `tools/check-version-bump.py`
     still reports the same pre-existing unrelated unbumped-plugin drift on
     `delegation-guidance`, `efforts`, `harness-knowledge`, and `wsl-setup`.

5. [x] **Add Group C's batched reconcile-and-stamp verb in agent-worktrees,
   unused at first.** **Landed in PR [#4327](https://github.com/ThomasMichon/copilot-extensions/pull/4327).**
   Operator direction resolved the ownership question here:
   the batch verb belongs in `agent_worktrees`, because `tracking.yaml`'s
   format and file-lock semantics are already engine-owned and the correctness
   of this slice depends on keeping that lock scope with the format owner.
   - **Concrete Step 5 contract design (decided before implementation):**
     - Add `<project> picker-reconcile-local --json` as the coarse-grained
       Group C verb. Request shape: no stdin/body payload, explicit project
       scope as usual, and an optional repeated `--worktree-id <id>` filter for
       future per-row refresh / targeted reload reuse; omitting the filter means
       "all current-platform local tracking records," matching today's
       `data_local.py` sweep.
     - Response shape: `{"version":1,"rows":[...],"summary":{...}}`, where
       each row intentionally reuses the Picker's existing list-row field names
       for the **Group C-owned subset only** (`id`, `pr`, `prs`, `pr_count`,
       `session_bound_live`, `session_lock_live`, `session_lock_stale`,
       `stale_lock_pids`, `mux_session`, `mux_clients`, `mux_attached`) so Step
       6 can layer this payload onto today's downstream consumers without
       inventing a second translation vocabulary. `summary` carries the batch
       counters / scope facts Step 6 needs to preserve today's reload decisions:
       `platform`, `requested_worktree_ids`, `record_count`,
       `pr_terminal_count`, `bound_visible_change_count`,
       `had_unresolved_bound`, and `mux_scan_ok`.
     - Versioning: this is an additive **contract v1** verb under
       `docs/engine-picker-contract.md`'s existing pinning discipline. Future
       rows/summary fields may be added, but the verb name and existing field
       meanings stay stable within contract version 1.
   - Add one batched `--json` verb that performs the current
     `data_local.py` loop inside agent-worktrees: list records, reconcile
     active PR state, read bound/mux/session-lock liveness, stamp the resulting
     cached state back through engine-owned helpers, and return the normalized
     payload the Picker needs.
   - Preserve today's **best-effort lock semantics** rather than inventing a new
     "hold one global lock across the whole refresh" behavior. The record
     enumeration remains lock-free, provider/network reconciliation continues to
     happen outside exclusive tracking writes, and the new verb uses only the
     same short-lived per-record or minimal-batch tracking lock windows the
     existing reconcile/stamp helpers already rely on. "Atomic" here means one
     process-boundary call and one engine-owned reconciliation authority, not a
     cross-record transaction that can pin tracking while a provider call runs.
   - Keep the verb coarse-grained. The point is specifically to avoid
     re-expressing `tracking.list_records`, `tracking._pr_is_terminal`,
     `pr_ops._reconcile_active_pr`, `reclaim.resolve_bound_copilots`,
     `sessions.mux_status_many`, `sessions.worktree_session_lock_state`, and
     `tracking.stamp_*` as a long series of per-record subprocess calls.
   - Add the matching Worktree Manager client wrapper and any payload parser
     tests, but do not cut `data_local.py` over in this step.
   - **Landing details:** implemented the engine-owned
     `picker-reconcile-local --json` verb in a dedicated
     `agent_worktrees.picker_reconcile_cli` module, documented it in
     `plugins/agent-worktrees/docs/engine-picker-contract.md`, and added the
     unused-at-first Manager wrapper in
     `worktree_manager.production_picker.engine_group_c`. The server-side verb
     calls the existing `tracking.list_records`, `tracking._pr_is_terminal`,
     `pr_ops._reconcile_active_pr(best_effort=True)`,
     `reclaim.resolve_bound_copilots`, `sessions.mux_status_many`,
     `sessions.worktree_session_lock_state`, `tracking.stamp_bound_live`, and
     `tracking.stamp_mux_live(..., sync=True)` helpers directly rather than
     re-expressing their logic in the client. Lock semantics are unchanged:
     record enumeration stays lock-free, provider/network work happens outside
     any new batch-wide lock, and the only writes are the helpers' existing
     short-lived stamp windows. `data_local.py` is intentionally untouched in
     this PR; Step 6 remains the cutover.
   - Validation: targeted new contract tests passed
     (`plugins/agent-worktrees/tests/test_picker_reconcile_local.py`,
     `worktree-manager/tests/production_picker/test_engine_group_c.py`);
     full `worktree-manager` suite (excluding the two standing hangs
     `test_data_ssh_sources.py` / `test_launch_trace.py`) finished at
     `1294 passed, 2 skipped, 10 failed` on this machine, staying within the
     effort's established unrelated-failure envelope; full `agent-worktrees`
     suite finished at `5738 passed, 50 skipped, 6 failed`, likewise with only
     unrelated pre-existing/environmental families red on this machine
     (`test_launch_cmd`, `test_lazy_dispatch`, `test_module_invocation`,
     `test_mux_status_link`, `test_session_conduct`). `ruff check --select
     F,E9`, `tools/check-install-contract.py`, and
     `tools/check-version-consistency.py` passed; `tools/check-version-bump.py`
     still reports the same pre-existing unrelated plugin-version drift on
     `delegation-guidance`, `efforts`, `harness-knowledge`, and `wsl-setup`.

6. [x] **Cut `data_local.py` over to the Group C batched verb, using Phase 3c's
   now-landed worker path.** **Landed in PR
   [#4350](https://github.com/ThomasMichon/copilot-extensions/pull/4350).**
   This is the Group C cutover PR.
   - Route the refresh-time reconciliation path through the new batched verb
     instead of the current direct imports, including the `reconcile_prs()`,
     `reconcile_bound_live()`, `_overlay_cached_state()`, and
     `_stamp_from_raw()` behavior that today depends on in-process access to
     `tracking` / `pr_ops` / `reclaim` / `sessions`.
   - Drain the remaining `production_picker.config` proxy consumers that are
     coupled to the same local-data/config-cache flow (`data_local.py`,
     `data_ssh.py`, `engine_loading.py`, `profiles_io.py`, `roster.py`,
     `picker_tui/__init__.py`) plus `engine_worktree_actions.py`'s last
     authoritative liveness check over the `config` / `sessions` / `tracking`
     shims, by moving each one onto its final direct file reader or explicit
     engine-client call, so Step 7 can delete those proxies for real instead of
     leaving a hidden tail.
   - Replace or coalesce the existing post-load reconcile hooks
     (`_start_pr_reconcile()` / `_start_bound_live_reconcile()`) rather than
     letting them survive beside the new batch path. One setup/reload epoch
     should schedule at most one reconciliation batch for this surface; no
     duplicate subprocesses or competing tracking writes after the cutover.
   - Keep the subprocess invocation off the render thread by reusing the
     epoch-guarded setup/reload infrastructure landed in Phase 3c. The
     dependency here is now **satisfied**, not speculative: Step 6 should build
     on that primitive instead of inventing a second ad hoc loader.
   - Confirm this step does not regress the cache-first first-paint shape or
     reintroduce per-row subprocess churn.
   - **Landing details:**
     - Replaced the Manager-side Group C imports with one
       `engine_group_c.picker_reconcile_local(...)` call in
       `picker_tui/data_local.py`'s authoritative classify load and targeted
       per-row Refresh path, then merged the returned
       `rows[{id,pr,prs,pr_count,session_bound_live,session_lock_live,session_lock_stale,stale_lock_pids,mux_session,mux_clients,mux_attached}]`
       subset directly onto the Picker's raw rows before normalization. The old
       Manager-side `reconcile_prs()` / `reconcile_bound_live()` helpers now
       degrade to summary readers over that same batch, preserving the narrow
       compatibility surface without reintroducing in-process engine access.
     - `data_local._overlay_cached_state()` now trusts the row's existing
       cached/batched `session_*` fields instead of calling
       `sessions.worktree_session_lock_state()` in-process, and
       `_stamp_from_raw()` is reduced to the same row-merge helper rather than
       stamping tracking/session state from imported engine helpers. This keeps
       cache-first first paint honest while making the later authoritative load
       the sole place the Group C batch runs.
     - Deleted the old post-load reconcile pair from `picker_tui/engine_runtime.py`.
       One setup/reload epoch's classify load already carries the authoritative
       Group C overlay, so the Picker no longer launches separate
       `_start_pr_reconcile()` / `_start_bound_live_reconcile()` threads after
       apply. The Phase 3c epoch-guarded setup/reload worker remains the only
       non-live loader path, and the live local two-phase loader still keeps the
       batch off the render thread.
     - Drained the remaining `production_picker.config` proxy tail by replacing
       `worktree_manager.production_picker.config` with Manager-owned direct
       file readers + a shared cache session over `~/.<project>/config.yaml`,
       `~/.<project>/worktrees`, the repo's in-repo config, and
       `machines.yaml`. That direct reader is now the final source for
       `picker_tui/data_local.py`, `data_ssh.py`, `engine_loading.py`,
       `profiles_io.py`, `roster.py`, and `picker_tui/__init__.py`.
     - Repointed `picker_tui/engine_worktree_actions.py`'s last authoritative
       liveness reverify to a targeted `picker-reconcile-local --json
       --worktree-id <id>` call instead of `sessions.verify_worktree_active()` +
       `tracking.stamp_mux_live()`, so menu-open liveness refinement still runs
       off-thread but no longer reaches the compatibility boundary.
     - Validation on the landed tree: the full
       `worktree-manager/tests/production_picker/` suite passed **twice**
       back-to-back at `700 passed, 2 skipped` both runs; the full
       `worktree-manager` suite (excluding the two standing hangs
       `test_data_ssh_sources.py` / `test_launch_trace.py`) matched the rebased
       machine baseline at `1439 passed, 6 skipped, 11 failed`; the full
       `agent-worktrees` suite remained within the established unrelated-failure
       envelope from Step 5 (same families, no new Group C failures); `ruff
       check --select F,E9` passed for both packages; `check-install-contract.py`
       and `check-version-consistency.py` passed; and `check-version-bump.py`
       still reports the same unrelated pre-existing version drift on
       `delegation-guidance`, `efforts`, `harness-knowledge`, and `wsl-setup`.

7. [x] **Delete `_engine_runtime.py` and the remaining proxy shims, then lock in
   the regression guard.** **Landed in PR
   [#4357](https://github.com/ThomasMichon/copilot-extensions/pull/4357).**
   This is the cleanup PR after every live call site is already off the import
   boundary.
   - Remove `_engine_runtime.py` and any leftover `production_picker/*.py`
     proxy modules whose only job was `engine_module(...)` pass-through.
   - Add a focused regression guard that fails if the production Picker grows a
     new in-process `agent_worktrees` dependency for Groups A/B/C (for example
     a small source-level test/scan keyed specifically to
     `worktree_manager.production_picker`, not a repo-wide style rule).
   - Reconcile the phase doc / README wording to the final post-cutover state
     so future work does not treat `_engine_runtime.py` as a still-valid seam.
   - **Landing details:** moved the last non-Picker compatibility bootstrap to
     the top-level `worktree_manager.agent_worktrees_runtime` helper, deleted
     `production_picker/_engine_runtime.py` plus the dead
     `config`/`pr_ops`/`reclaim`/`sessions`/`tracking` proxy modules, and
     repointed the remaining transplant/conftest + housekeeping callers to the
     top-level helper so `worktree_manager.production_picker` itself no longer
     imports `agent_worktrees` directly. Added
     `tests/test_production_picker_runtime_boundary.py`, which scans
     `src/worktree_manager/production_picker/` for direct `agent_worktrees`
     imports and fails if any deleted proxy file returns.
   - Validation: focused Step 7 regression lanes green
     (`tests/test_production_picker_runtime_boundary.py`,
     `tests/test_production_picker_transplant.py`,
     `tests/production_picker/test_housekeeping.py`,
     `tests/production_picker/test_closure_cross_surface_parity.py`,
     `tests/production_picker/test_picker_cache.py`,
     `tests/production_picker/test_picker_tui.py`,
     `tests/production_picker/test_profiles_io.py` -> `367 passed`;
     `tests/production_picker/test_config_readers.py` + the runtime-helper lane
     -> `63 passed`; `tests/production_picker/test_picker_first_paint.py -k
     "import_does_not_load_config"` -> `2 passed`). Full `worktree-manager`
     suite matched the current unrelated Windows baseline at
     `14 failed, 1552 passed, 7 skipped, 1 warning`; `ruff check --select
     F,E9`, `tools/check-install-contract.py`, and
     `tools/check-version-consistency.py` passed.

8. [x] **Resolve the flagged `housekeeping.py` residual gap.**
   The 2026-09-27 re-audit was right: Step 7's literal-import scan proved only
   that `production_picker/*.py` no longer contained a direct
   `import agent_worktrees` statement, while `housekeeping.py` still reached
   the same modules indirectly through
   `worktree_manager.agent_worktrees_runtime.engine_module(...)`. Step 8
   closes that substance-vs-letter gap per call site rather than by blanket
   assertion.
   - **`config.tracking_dir()`** — **converted** to the existing
     Manager-owned `production_picker.project_config.tracking_dir()` read. This
     call is just a stable path lookup (`~/.<project>/worktrees`), so keeping a
     dynamic engine import for it was unjustified.
   - **`tracking.list_records()` + `tracking.derive_execution_leg()`** —
     **converted** to a Manager-owned YAML scan of the tracking directory,
     reading only the `worktree_id` plus `execution_leg.provider` facts needed
     to identify Manager-owned rows. This runs once in the startup
     housekeeping thread, not per Picker refresh row, so direct file reads are
     cheap and avoid a "subprocess just to learn which ids to ask the engine
     about" loop.
   - **`sessions._list_mux_sessions()` / `_mux_session_activity()` /
     `worktree_id_from_mux_session()` / `kill_tmux_session()` plus
     `activity.log_event()`** — **converted** to the public CLI seam
     `reap-sessions --json`, additively extended with repeatable
     `--worktree-id` filters plus `--include-manager-owned` for the Manager's
     own mux lane. Result: one subprocess per startup sweep, not one per mux
     session.
   - **`reap_cli.sweep_managed_worktrees()`** — **converted** to new focused
     verb `sweep-managed --json`. This runs from exit/startup housekeeping, not
     the render loop, so the subprocess boundary is the right ownership line.
   - **`reap_cli.reap_orphan_launcher_shells()`** — **converted** to the
     already-public `reap-shells --json --yes` verb. Same low-frequency,
     background-triggered profile as above; no new engine surface needed.
   - **`gc.SESSION_GC_GRACE_SECS`** — **converted** to a local constant
     (`48h`) plus the existing env override. This was pure configuration data,
     not behavior worth importing a module for.
   - **`reap_cli.sweep_finished_session_worktrees()`** — **converted** to new
     focused verb `sweep-finished-sessions --json`, preserving the engine-owned
     cleanup semantics while removing the in-process import.
   - **`status_monitor_runtime._status_monitor_enabled()` /
     `_ensure_status_monitor()`** — **converted**, but deliberately not to a
     `--json` read. This callback runs on every 10s Picker heartbeat while the
     TUI is open, so a steady-state subprocess every tick would be the wrong
     cost profile. Instead, `production_picker.monitor_roots` now owns the
     lockfile check locally and spawns the public `agent-worktrees
     status-monitor` command only when the resident monitor is actually absent.
     Steady state therefore stays zero-subprocess-per-heartbeat, while the
     in-process engine import is gone. **2026-09-29 follow-on:** the
     Manager-owned port now also treats a live lock as reusable only when it
     still matches the current engine prefix and the caller's mux capability;
     otherwise it respawns the public `status-monitor` command with the same
     clean detached-runtime contract the engine-side helper uses. That closes
     the related "stale or no-mux resident monitor is reused forever after an
     upgrade / install-context change" bug discovered while landing Step 8,
     without reintroducing an in-process engine dependency.
   - **No vision carve-out was needed.** Step 8 removes the residual
     `housekeeping.py` in-process access rather than documenting it as an
     exception, so the Picker vision's process-boundary-only rule stays
     unconditional.

## Validation

### Contract-level validation

- `plugins/agent-worktrees/docs/engine-picker-contract.md` matches the final
  public surface the Picker now depends on; no Group A/B/C boundary remains
  "real but undocumented."
- Production Picker tests keep proving the process boundary, not just the happy
  path: once cleanup lands, no production code under
  `worktree_manager.production_picker` should require `engine_module(...)` or a
  direct `agent_worktrees.*` import.
- Existing Phase 3c non-blocking guarantees remain intact: a blocked engine verb
  may delay one background worker result, but it must not block first paint or
  the Textual event loop.

### Group-specific validation

1. **Group A**
   - Contract tests for the new scalar/path reads and `update-indicator --json`
     response shape.
   - Boundary tests prove the `state-root` wrapper is invoked with explicit
     project scope and that the update-indicator poll runs off the UI thread.
   - Targeted `pivot_manifest.py` / update-indicator tests prove the Picker
     still degrades cleanly when those verbs are unavailable or return empty
     state.

2. **Group B — public resolution seam**
   - Targeted CLI tests prove the new bootstrap verb and the reused
     `resolve --json` remote path return the same decisions the production
     Picker needs today, without exposing private helper names as contract.
   - Parent-context tests prove the resolved project identity is bound once in
     worktree-manager and reused consistently by later Picker/data consumers,
     rather than drifting with ambient cwd after `_engine_runtime.py` is gone.
   - Targeted runner tests prove remote launch planning, cwd switching, and the
     best-effort stale-anchor repair still behave correctly after cutover.

3. **Group B — manager-owned lifecycle sweeps**
   - Worktree Manager tests cover the local housekeeping thread, monitor-root
     setup/teardown, and the exit-time sweep behavior now that this logic lives
     under the Manager's ownership.
   - Coordination coverage proves Manager-owned sessions are swept by exactly
     one owner at a time: once cut over, agent-worktrees' generic sweepers no
     longer mutate the same rows/session state in parallel.
   - No Group B call site reaches underscore-prefixed `agent_worktrees` helpers
     after Step 4.

4. **Group C**
   - The new batch verb has engine-side tests for lock ownership, timeout, and
     stale-vs-fresh reconciliation behavior, including the guarantee that
     provider/network work does **not** hold tracking locks across the whole
     batch.
   - Setup/reload tests prove the batch replaces the old post-load reconcile
     hooks instead of running beside them; one epoch yields one reconciliation
     batch.
   - Picker tests prove refresh/setup still stay off-thread with a deliberately
     blocked batch verb, matching Phase 3c's standing non-blocking contract.
   - A focused regression test proves the cutover did **not** become "one
     subprocess per record/helper"; the hot path stays one batched engine call
     per refresh cycle.

5. **Final cleanup**
   - A narrow source-level guard (or equivalent targeted test) proves
     `_engine_runtime.py` and its last proxy shims are gone and do not return.
   - The README phase checklist and this doc agree on the final step breakdown
     so the effort remains resumable without re-reading old issue comments.

## Open questions for the operator / design review

1. ~~**Group B ownership split.**~~ **Resolved by operator direction
   (2026-09-27):** split the cluster exactly at the ownership boundary. The
   process-lifecycle sweeps (`reap_orphan_mux_sessions`,
   `_sweep_managed_on_exit`, `_sweep_launcher_shells_on_exit`,
   `_sweep_finished_sessions_on_cadence`, `_start_picker_monitor_root`) are
   reimplemented directly in worktree-manager, while the project/config/ssh
   decisions stay engine-owned behind a new narrow public CLI seam in
   agent-worktrees.
2. ~~**Group C verb shape.**~~ **Resolved by operator direction
   (2026-09-27):** the batched read-reconcile-stamp verb lives in
   `agent_worktrees`, not in worktree-manager. `agent_worktrees` already owns
   `tracking.yaml`'s on-disk format and file-lock semantics, so correctness here
   depends on keeping the lock-scoped mutation with the format owner rather than
   recreating it via optimistic concurrency from the Picker side.
3. ~~**Sequencing against Phase 3c.**~~ **Resolved by landed state
   (2026-09-27):** Phase 3c is complete (PR #4278), so Group C may now build on
   the epoch-guarded non-blocking worker path instead of waiting for it. Group A
   and Group B remain independently landable ahead of Group C, and the final
   `_engine_runtime.py` retirement still waits until all remaining groups are
   cut over.
