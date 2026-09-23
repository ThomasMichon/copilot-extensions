# Phase 3d — Retire the Picker's in-process engine-module boundary (`_engine_runtime.py`)

## Why this phase exists

`worktree-manager/src/worktree_manager/production_picker/_engine_runtime.py`
is self-documented, in its own module docstring, as a **"Temporary
compatibility boundary to the active agent-worktrees runtime."** It resolves
the active agent-worktrees install (namespaced-then-legacy slot, or a local
checkout fallback), injects its `src/` plus several of its vendored
`libs/*/src` onto `sys.path`, and lets the Picker `importlib.import_module()`
9 `agent_worktrees.*` submodules directly, in-process — a different plugin's
own private CLI implementation, not a shared library. This already caused two
live production bugs this session (#3319, #3327): a lazily-populated
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
  is that discussion's evidence base.

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
subprocess-per-call is affordable here. **Needs:** confirm or add a stable
`--json` verb for install-dir and state-root, following the same pinning
discipline `docs/engine-picker-contract.md` already established for
`engine_client` in Phase 3.

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

### Group D — `profiles.py`: not a CLI-root problem at all — a vendoring problem, like #3359

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
all. It is also called live during interactive menu rendering
(`engine_profiles_view.py`), which would make a subprocess round-trip both
the wrong shape and too slow regardless of the CLI-root question. **Fix:
vendor `profiles` as a 4th shared lib**, exactly like #3359 — copy it to
`worktree-manager/libs/profiles`, declare it in `pyproject.toml`, register
with `tools/check-vendored-libs-sync.py`. No subprocess design needed here.

### `update_stage.py`

Deferred-resolution shim (see its own docstring) backing only the Picker's
cosmetic version-update indicator glyph — genuinely low-frequency (called
at most once per Picker session, after first paint). Same disposition as
Group A: a safe, low-risk subprocess-conversion candidate once a `--json`
verb exists for "is an update available."

## Revised remediation shape (supersedes the issue's "9 uniform read paths" framing)

| Group | Modules | Real shape | Disposition |
|---|---|---|---|
| A | `config` (pivot_manifest only), `state_root`, `update_stage` | low-frequency, one-shot reads | convert to `--json` CLI verbs |
| B | `__main__`/`cli.*`, `config` (runner.py's `set_active_project`/`load_config`/`load_machines_yaml`) | private process-lifecycle internals, several managing the Picker's *own* process | reclassify as worktree-manager-owned logic, or promote a narrow public API per call site — **not** a uniform subprocess conversion |
| C | `tracking`, `pr_ops`, `reclaim`, `sessions` (all via `data_local.py`) | one atomic read-reconcile-write hot-path loop, per Picker refresh | needs one new **batched** `--json` verb; sequence after Phase 3c |
| D | `profiles` | pure, dependency-free logic called during interactive rendering | vendor as a shared lib (#3359-style), not a CLI conversion |

`_engine_runtime.py` retires only once every group above has either
converted, been reclassified, or (Group D) stopped needing the boundary at
all.

## Open questions for the operator / design review

1. **Group B ownership split.** For each private `cli.*`/`config_module.*`
   call in `runner.py`, is the underlying decision genuinely
   agent-worktrees' to own (→ promote a narrow public API) or is it really
   worktree-manager's own process concern that should never have reached
   into agent-worktrees' internals (→ reimplement locally)? This needs a
   per-call-site answer, not a blanket policy.
2. **Group C verb shape.** Should the new batched reconcile-and-stamp verb
   live in `agent_worktrees` (since it owns `tracking.yaml`'s format and
   lock), or should worktree-manager keep doing the reconciliation itself
   but through smaller, already-existing verbs plus a lock-safe
   read-modify-write contract agent-worktrees exposes for exactly this case
   (e.g. an optimistic-concurrency stamp verb)? Affects both correctness
   (lock semantics across a process boundary) and performance (verb count
   per refresh).
3. **Sequencing against Phase 3c.** Confirm Group C conversion work should
   wait for Phase 3c's non-blocking I/O primitive, since both touch
   `data_local.py`'s exact call site and stacking them independently risks
   the same kind of data race Phase 3c's own history already surfaced once.
