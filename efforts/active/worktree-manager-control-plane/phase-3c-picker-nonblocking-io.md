# Phase 3c — Picker non-blocking I/O (pivot loads, menu opens, action execution/progress)

## Why this phase exists

A prior session shipped three targeted picker UX fixes (row detail line,
Actions-menu claims fold, and two deferred imports —
[#2967](https://github.com/ThomasMichon/copilot-extensions/pull/2967)) and, while
investigating a fourth reported symptom ("New worktree"/per-worktree menus feel
slow to open), traced the real cause to `PickerScreen.setup()`'s pivot-registry
scan running **synchronously on the render thread** — but a same-session attempt
to fix it introduced a cross-test data race and was reverted rather than shipped
half-verified. This phase is the design/architecture work asked for explicitly
as a follow-up: make **every** I/O-touching surface of the Picker — pivot loads
(built-in and plugin-contributed), menu opens, and action execution with
progress reporting — consistently non-blocking, with the generation-safety this
repo's own test suite already proved is required.

This is a **design/planning** artifact first. It records the full current-state
audit (so a future slice doesn't have to re-derive it), the specific failure
mode a naive fix hits, and an ordered, independently-shippable slice plan.
Implementation lands slice-by-slice against this plan, not in one PR.

## Current-state audit (evidence-based, `worktree-manager/src/worktree_manager/production_picker/picker_tui/`)

### Already non-blocking (verified by direct inspection, not assumed)

- **Contributed/registered pivot list loads.** `tasks.RegisteredPivotRuntime`
  runs a pivot's declared `list` command on a daemon thread per machine,
  caches the result, and tracks in-flight fetches (`_inflight`) so the render
  loop only ever reads a snapshot (`tasks.py`). A `list --stream`-capable
  provider streams NDJSON envelope rows incrementally; a non-streaming one
  falls back to a bounded one-shot subprocess, still off-thread.
- **Contributed pivot actions.** `run_action`/`run_resolved` are plain blocking
  subprocess calls, but every engine.py call site wraps them in `_run_bg` (open
  the result screen/status line update deferred until the daemon thread's
  callback lands via `app.call_from_thread`). `run_action_stream` additionally
  consumes a `kind: "progress"` action's NDJSON progress envelope
  (`{"type":"progress","pct":..,"msg":..}` / `"done"` / `"error"`) and drives
  `ProgressScreen`'s `"action-stream"` render mode live.
- **Per-worktree Actions menu.** `_push_wt_submenu` opens the `SubMenuScreen`
  **immediately** from the record's cached liveness; only when a real
  worktree needs an authoritative re-verify does it kick `_run_bg("Actions",
  _verify, _done, quiet=True)`, refining the already-open menu's verbs in
  place when the probe lands (`_open_submenu`, `_refresh_wt_submenu`).
- **Bulk Cleanup/Sync/Stop.** `_open_cleanup`/`_open_sync`/`_start_stop` build
  their scope dialog from `self.data` (already in memory) with zero I/O, then
  drive execution through `ProgressScreen`'s items-list mode
  (`self.progress`/`_advance_progress`, backed by a `MaintenanceExecutor` poll
  on its own interval) — never a blocking call on the render thread.
- **New-worktree / Clean-Sync scope dialogs.** `_open_optmenu`, `_impact_rows`
  and the `ScopeDlgScreen` they push are pure in-memory computations over
  `self.data`; no subprocess, no filesystem read, before the modal renders.
- **Maintenance / Tasks / Configuration menu opens.** `_open_maint_menu`,
  `_open_task_menu`, `_open_cfgmenu` all filter already-loaded in-memory
  collections (`self.data`, `self.registered_pivots`, `self.config_sections`);
  none does I/O before pushing its modal.
- **Live (multi-machine "All") worktree data load.** `_setup_skeleton` paints
  chrome immediately (`_load_pivots(scan=False)`, empty lists) and
  `_setup_live_async`/`_setup_live_pivots` do the real per-machine SSH
  gather **and** the pivot-registry scan on background threads, applying
  results via `_apply_from_worker` (`app.call_from_thread`). This is the
  reference implementation the rest of this plan generalizes.

### Still blocking (the actual gap)

- **`PickerScreen.setup()`'s pivot-registry scan.**
  `self._load_pivots()` (default `scan=True`) calls
  `pivots_mod.scan_pivot_registry()`, which materializes active plugin
  payloads, classifies every manifest, and calls
  `resolve_active_plugins()` — all synchronously, on the render thread, at
  **four** call sites:
  1. the initial non-live mount (`on_mount` → `else: self.setup()`);
  2. the manual `r` reload key, dispatched straight from the render-thread key
     handler;
  3. `_run_wt_action`'s `_done` callback (a contributed worktree action just
     finished, re-scan to reflect state);
  4. `_run_config_section`'s `_done` callback (same reason, for config
     sections).
  Only the **live** path (`_setup_skeleton`/`_setup_live_pivots`) already
  defers this. The plain/local path — almost certainly the common case for
  an operator working one project — does not, and this is what makes the
  picker (and, transitively, the very next keypress including Enter-to-open-
  a-menu) feel slow right after launch, reload, or any action that re-scans.
- **The non-live worktree DATA load itself.** `self.data = self.src.load()`
  inside `setup()` runs at the exact same four call sites, immediately after
  the pivot scan. For the local/machine-ssh source this is the
  `agent-worktrees list --json --classify` subprocess (git + session +
  mux-liveness gather) — potentially the single slowest call in the whole
  startup path, and it is just as unguarded as the pivot scan.
- **Built-in single-row lifecycle actions carry no progress detail.** Open /
  Resume / Sync / Cleanup / Finalize / Stop / Reclaim / Restore / Repair /
  Dispose-hosted-session all run through plain `_run_bg` — already
  non-blocking (this is not a hang risk) — but surface only a footer spinner
  + static label, unlike a contributed pivot's `kind: "progress"` action or a
  bulk maintenance run, both of which show live percentage/message detail.

## Case study: why the reverted prototype broke, and what it demands of any real fix

Last session's prototype made `setup()`'s pivot scan async by mirroring
`_setup_live_pivots`: kick a daemon thread running `_scan_pivot_payload()`,
then `self._apply_from_worker(lambda: self._install_pivot_payload(payload))`.
Syntactically correct, and it worked for a single manual test — but running the
**full** `tests/production_picker/test_picker_tui.py` suite (not a `-k`
filtered slice) surfaced 9 failures, including a `KeyError: 'machine'` inside
`_scope_data()` that never reproduced in isolation. The mechanism: nothing
gates a background scan that is still in flight when its owning `setup()` call
is superseded — by a later `setup()` call on the **same** screen (a second `r`
reload before the first scan lands), or by test teardown racing a still-running
daemon thread against the next test's fresh `PickerScreen`. A stale result
landing after a newer one is not just "wrong data momentarily" — it can
overwrite fresher state with an inconsistent partial snapshot mid-render.

This repo already has the right primitive for the *teardown* half of this
problem (`PickerScreen._bg_cancel`, a one-shot `threading.Event` set in
`on_unmount`, checked by `_run_bg`'s worker before it marshals a result back —
see the fix in #2735). What it does not yet have is the *supersession* half:
a `setup()`/data-reload can legitimately re-run **many times** across one
screen's lifetime (mount, every `r`, every action-triggered rescan), so a
one-shot cancel flag is not enough — the guard needs to be a **monotonically
increasing generation/epoch**, not a boolean.

## Proposed architecture

### 4.1 A generation-guarded background-task primitive

Add an epoch counter to `PickerScreen`, alongside the existing `_bg_cancel`:

```python
self._epoch = 0          # bumped at the start of every setup()/reload/rescan
```

A new helper, `_run_bg_epoch(label, work, done, *, quiet=False)`, is a thin
wrapper around the existing `_run_bg`: it captures `epoch = self._epoch` at
call time, and its `done` callback is itself wrapped so that, immediately
before running the caller's `done(result)`, it checks
`epoch == self._epoch and not self._bg_cancel.is_set()` — a supersede *or* a
teardown either one drops the result silently (DEBUG log only, mirroring the
existing `_bg_cancel` log line), never applying stale state. `setup()`/every
reload entry point calls `self._epoch += 1` as its very first statement,
before doing anything else — so a superseding call always wins the race by
construction, with no dependence on thread scheduling order.

This is additive to `_run_bg`, not a replacement: pivot-action verbs, steer
submissions, and other ALREADY-safe one-shot flows keep using plain `_run_bg`
unless they specifically need supersession semantics (only `setup()`/reload
does, since it is the only op that legitimately re-runs concurrently with
itself).

### 4.2 Wiring the primitive into `setup()`

Split `setup()` into two phases, applied uniformly at all four existing call
sites (initial mount, `r` key, and the two post-action `_done` callbacks):

1. **Synchronous skeleton** — everything that is already in-memory-cheap:
   `self._load_pivots(scan=False)` (built-ins only), machine/tab resolution
   from already-known config, clearing stale selection state. This alone must
   never exceed one render frame.
2. **`_run_bg_epoch`-guarded async phase** — a single background thread that
   does BOTH the real pivot scan (`_scan_pivot_payload()`) and the worktree
   data load (`self.src.load()`), then applies both together in one
   epoch-checked callback. Doing them together (not as two independent
   guarded operations) matters: a screen must never show data from
   generation N painted alongside pivots from generation N-1, so they share
   one epoch check and one atomic apply.

Every one of the four call sites becomes: bump the epoch, kick the async
phase, return immediately — the caller (mount, the `r` handler, or a `_done`
callback) no longer blocks on either the scan or the data load.

### 4.3 Menu loads — formalize the existing pattern, guard against regressing it

The audit above found every menu-opening code path is **already**
non-blocking; the "menu is slow" symptom traces entirely to 4.2's render-
thread block, not to any menu-opening code itself. The action here is
preventive, not corrective: add a regression-test fixture that fails a test
if a menu-open call (`_open_optmenu`, `_open_cleanup`, `_open_sync`,
`_open_maint_menu`, `_open_task_menu`, `_open_cfgmenu`, `_open_submenu`)
synchronously invokes `subprocess.Popen`/`subprocess.run` on the calling
thread. This is exactly the kind of regression `setup()`'s pivot scan
represents (blocking work reachable from a key-dispatch path) and would have
flagged it before it shipped.

### 4.4 Action execution + progress reporting

No functional gap: every action execution path (contributed-pivot actions,
built-in single-row lifecycle verbs, bulk maintenance runs) is already
non-blocking via `_run_bg`/`ProgressScreen`. The gap is presentational —
built-in single-row verbs show a spinner + static label, not the live
percentage/message detail a contributed `kind: "progress"` action or a bulk
run gets.

- **In-repo, no dependency:** nothing required — the "everything must be
  non-blocking" requirement is already met here.
- **Optional enhancement, in-repo:** a generalized `_run_bg_stream` wrapper
  that reuses `ProgressScreen`'s existing `"action-stream"` render mode for
  ANY `_run_bg`-driven action, not only contributed-pivot ones — but this only
  has something to render once the invoked command actually emits the NDJSON
  progress envelope.
- **Cross-repo dependency (tracked separately, not this repo):** the built-in
  verbs shell out to `agent-worktrees` CLI commands (`restart`, `stop`,
  `reclaim`, `repair`, `finalize`/cleanup, `sync`) that today return once,
  at the end, with no intermediate envelope. Real percentage progress for
  these needs `agent-worktrees` to opt in to the same NDJSON contract its own
  contributed-pivot-action convention already defines. File a companion issue
  there rather than block this phase on it; `_run_bg_stream` degrades to the
  plain spinner automatically when a command doesn't emit the envelope, so
  this repo's side can land independently and light up per-verb as the engine
  gains support.

### 4.5 Test infrastructure

- Promote the epoch-aware "wait for the async result to land" polling helper
  (prototyped ad hoc last session as `_await_registered_pivots`, then reverted
  with the rest of the prototype) into a documented, reusable test utility —
  e.g. `tests/production_picker/_async_helpers.py` — so Slice 2 (and any
  future async work) has one supported way to synchronize a test with a
  background epoch-guarded operation instead of each test inventing its own
  polling loop.
- Add the 4.3 "no synchronous subprocess on menu-open" guard as an autouse
  `conftest.py` fixture, not an opt-in one — it should protect every test in
  the module by default.

## Phased rollout

- **Slice 1 — the primitive.** Add `_epoch`/`_run_bg_epoch` to
  `PickerScreen`, no behavior change anywhere yet. Unit tests only
  (supersession drops a stale result; a torn-down screen drops via
  `_bg_cancel` same as today; a normal single in-flight call still applies).
- **Slice 2 — migrate `setup()`.** Wire 4.2 at all four call sites. Update the
  test surface that currently assumes synchronous pivot/data population
  (using the Slice-(4.5) helper). Validation for this slice explicitly
  includes running the **full** `tests/production_picker/` suite at least
  twice in immediate succession (not a `-k` filtered subset) — the exact gap
  that let the reverted prototype's race through undetected.
- **Slice 3 — the regression guard.** Land the 4.3 conftest fixture ahead of
  or alongside Slice 2, so Slice 2 itself is proven not to reintroduce a
  synchronous menu-open path.
- **Slice 4 — progress-reporting proposal (cross-repo).** Write up the NDJSON
  progress-envelope ask for `agent-worktrees`' built-in lifecycle verbs as its
  own issue there; once available, add the in-repo `_run_bg_stream` wrapper
  (4.4) and cut built-in verbs over per-verb as engine support lands.

## Validation

- Slice 1: new unit tests for `_run_bg_epoch`'s supersede/teardown/normal-path
  behavior (mirrors the existing `test_run_bg_...` coverage for `_bg_cancel`).
- Slice 2: full `tests/production_picker/` suite green, run twice back-to-back;
  a manual before/after timing check (`time` a cold `worktree-manager picker`
  open on a machine with several installed pivot plugins) to confirm the
  perceived latency actually drops, not just that tests pass.
- Slice 3: a deliberately-reintroduced synchronous call in a scratch branch
  should make the new fixture fail, proving the guard actually guards.

## Non-goals of this phase

- Rewriting the already-async live/multi-machine loader path — only the
  single-machine/local path lacks equivalent treatment.
- Shipping new progress-percentage UI in this repo ahead of the upstream
  `agent-worktrees` contract Slice 4 depends on; Slice 4 here is the proposal
  + the degrade-gracefully wrapper, not an assumption that the envelope
  exists yet.
- Any change to the contributed-pivot manifest contract itself — this phase
  changes when/how the Picker *consumes* pivot data, not the
  [contribution contract](../../../worktree-manager/docs/plugin-contribution-contract.md),
  which stays at version 1.
