# Phase 3d — Retire the Picker's in-process engine-module boundary (`_engine_runtime.py`)

- **Parent effort:** [`README.md`](README.md) § Phase 3d
- **Tracks:** [#3359](https://github.com/ThomasMichon/copilot-extensions/issues/3359),
  [#3360](https://github.com/ThomasMichon/copilot-extensions/issues/3360)
- **Scope of this doc:** the ordered implementation plan for removing the
  Picker's last in-process `agent_worktrees.*` import boundary, building on the
  evidence-gathering inventory already recorded here.
- **Status:** Planned — #3359's vendored-lib prework is done via PR
  [#3368](https://github.com/ThomasMichon/copilot-extensions/pull/3368),
  Group D's `profiles` dependency is closed via Phase 3e / PR
  [#3626](https://github.com/ThomasMichon/copilot-extensions/pull/3626), and
  the remaining Group A/B/C work is now sequenced below as independently
  landable PRs. Phase 3c's prerequisite for Group C is satisfied by PR
  [#4278](https://github.com/ThomasMichon/copilot-extensions/pull/4278).

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

1. [ ] **Pin the low-frequency public read surface for Group A, additive only.**
   `pivot_manifest.py` and `update_stage.py` should stop depending on
   in-process module imports, but the first PR should add the public seam
   without changing either caller yet.
   - Extend/pin the engine-facing contract for the exact one-shot reads the
     Picker still needs: `config.install_dir()` / `config._home()` via the
     existing scalar `get <key>` surface (new picker-supported keys rather than
     a new import seam), `state_root_module.resolve_state_root(...)` via the
     existing `state-root --json` family (pin the subset the Picker consumes, or
     add a thin JSON wrapper if the current output shape is too broad), and a
     new tiny **read-only** `update-indicator --json` verb for the glyph. Keep
     it explicitly distinct from the existing `stage-update --json` command,
     which performs the marketplace staging work rather than merely reporting
     the cheap status `indicator_state()` reads.
   - Pin the **explicit project scope** for those reads. In particular, any
     `state-root --json` reuse must go through `<project> state-root --json` (or
     an equivalent explicit `--project`/repo-scoped wrapper), not a child
     process inheriting whatever cwd happens to exist when the Picker asks.
     Record the fallback/error behavior for "no active project" and
     adopted-anchor cases at the same time.
   - Move the update-indicator polling path off the Textual UI thread before it
     shells out. The current `_poll_update_state()` runs from `_tick()`; once it
     becomes a subprocess read, Step 1 must route it through the existing
     background-worker/callback path so a slow/hung engine cannot freeze render.
   - Add Worktree Manager-side `engine_client` wrappers for those reads, but
     leave `pivot_manifest.py` / `update_stage.py` on the compatibility shim in
     this step.
   - Update `plugins/agent-worktrees/docs/engine-picker-contract.md` so these
     reads are documented as part of the pinned process-boundary contract rather
     than merely existing as implementation detail.

2. [ ] **Promote Group B's project/config/ssh decisions to a narrow public CLI
   seam, additive only.** This should be public `--json` CLI surface, not a
   stable importable Python API: the governing contract for the control plane is
   still "Picker reaches agent-worktrees only through CLI verbs," and replacing
   `_engine_runtime.py` with another import surface would preserve the coupling
   this phase exists to remove.
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

3. [ ] **Reimplement Group B's Picker-owned lifecycle sweeps directly in
   worktree-manager, additive first.** Operator direction resolved this cluster:
   `reap_orphan_mux_sessions`, `_sweep_managed_on_exit`,
   `_sweep_launcher_shells_on_exit`, `_sweep_finished_sessions_on_cadence`, and
   `_start_picker_monitor_root` become Worktree Manager-owned logic because they
   govern the Picker's own process lifetime, not agent-worktrees' state model.
   - Introduce a manager-owned housekeeping/runtime module that ports the
     current behavior into worktree-manager under its own tests. Recent
     2026-09-26 promotion-pipeline fixes remove the earlier "cross-repo porting
     is too painful" pressure, so a one-time code port is now acceptable where
     ownership is genuinely moving.
   - Define the coordination boundary up front: once the Manager-owned sweeper
     lane is enabled for production Picker sessions, agent-worktrees' generic
     lifecycle sweepers must either skip those Manager-owned rows/session names
     entirely or consume the Manager-produced view rather than mutating the same
     tracking/worktree/session state in parallel. This step is not "copy the
     code and hope"; it is "establish one owner for these sessions, then port."
   - Keep this step additive: the new local implementation exists and is tested,
     but `runner.py` still uses the current compatibility path until the next
     step performs the actual cutover.
   - Preserve behavior, not private names: this step should carry over the
     sweep/monitor semantics that matter to the Picker, while leaving
     agent-worktrees' remaining internal helpers free to evolve independently.

4. [ ] **Perform the Group A + Group B cutover in one crisp PR.** Once Steps 1-3
   are landed, switch the three remaining non-hot-path call sites off the
   compatibility boundary together.
   - `pivot_manifest.py` and `update_stage.py` move to the new Group A public
     surface.
   - `runner.py` switches to the Step 2 public verbs for bootstrap,
     stale-anchor repair, and remote planning, while its housekeeping/monitor
     lifecycle moves to the Step 3 manager-owned implementation.
   - Include the old-engine remote fallback in `worktree_manager.__main__`:
     either `runner.compatibility_remote_plan()` is cut over to the same public
     seam in this step, or the fallback is explicitly version-gated/retired
     here so an older engine cannot silently keep exercising the private import
     path after the main runner flow is clean.
   - At the end of this step, Groups A and B's **runner/pivot/update** paths no
     longer rely on
     `engine_module(...)`, underscore-prefixed `agent_worktrees` helpers, or
     any in-process `agent_worktrees` import for production Picker behavior.

5. [ ] **Add Group C's batched reconcile-and-stamp verb in agent-worktrees,
   unused at first.** Operator direction resolved the ownership question here:
   the batch verb belongs in `agent_worktrees`, because `tracking.yaml`'s
   format and file-lock semantics are already engine-owned and the correctness
   of this slice depends on keeping that lock scope with the format owner.
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

6. [ ] **Cut `data_local.py` over to the Group C batched verb, using Phase 3c's
   now-landed worker path.** This is the Group C cutover PR.
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

7. [ ] **Delete `_engine_runtime.py` and the remaining proxy shims, then lock in
   the regression guard.** This is the cleanup PR after every live call site is
   already off the import boundary.
   - Remove `_engine_runtime.py` and any leftover `production_picker/*.py`
     proxy modules whose only job was `engine_module(...)` pass-through.
   - Add a focused regression guard that fails if the production Picker grows a
     new in-process `agent_worktrees` dependency for Groups A/B/C (for example
     a small source-level test/scan keyed specifically to
     `worktree_manager.production_picker`, not a repo-wide style rule).
   - Reconcile the phase doc / README wording to the final post-cutover state
     so future work does not treat `_engine_runtime.py` as a still-valid seam.

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
