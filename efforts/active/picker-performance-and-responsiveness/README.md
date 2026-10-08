# Picker Performance & Responsiveness

- **Slug:** `picker-performance-and-responsiveness`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase `pr/<slug>` worktrees → landed to `dev`
- **Created:** 2026-10-04
- **Status:** Active <!-- Draft | Active | Blocked | Done -->
- **Vision:** [`visions/picker`](../../../visions/picker/README.md) —
  §Behaviors/`responsive-by-budget` (added alongside this effort's creation —
  the numeric budgets below are that section's literal Done bar), and extends
  `live-not-snapshot` / `graceful-capability-scaling` to agent-codespaces and
  agent-containers (Phase 2 below).
- **Umbrella issue:** _not yet filed — file once Phase 0 is sent for review_
- **Sub-issues:** _filed per-phase as each is scoped for execution_
- **Related, not absorbed — read these before picking up any phase:**
  - [`efforts/2026/10/07 pivot-streaming-transport`](<../../2026/10/07 pivot-streaming-transport/README.md>)
    — owns the CLI-relayed daemon fast-path mechanism itself (Phases 3a/3b/3c),
    the render-diffing work (Phase 4), and the resident-monitor hint-trust work
    (Phase 5), for the two pivots (agent-dispatch, agent-bridge) already in its
    scope. **This effort does not re-do that work.** Phase 2 below *extends*
    that same mechanism to agent-codespaces and agent-containers, which
    pivot-streaming-transport's own scope explicitly does not cover.
  - [`ThomasMichon/copilot-extensions#918`](https://github.com/ThomasMichon/copilot-extensions/issues/918)
    — the agent-worktrees resident status-updater daemon. Phase 3 below closes
    out its remaining CPU-under-scale gap; do not reopen phases it already
    shipped.
  - [`efforts/active/agent-worktrees-authoritative-daemon`](../agent-worktrees-authoritative-daemon/README.md)
    — already documents `record_cache.py`'s deep-copy-on-every-hit behavior
    (`README.md:1232-1251`). Phase 3 below turns that existing finding into a
    fix; it is not a new discovery.
  - [`efforts/active/worktree-manager-control-plane`](../worktree-manager-control-plane/README.md)
    — Phase 3c ("Picker non-blocking I/O") already moved the Picker's known
    blocking calls off the render thread; Phase 0 below is the one call that
    slipped past that sweep. That effort's `#5001` (mux-daemon generation
    rotation) and its Phase 4 provider-registration doc (`#5122`, the leaked-
    pytest-tmp_path manifest-corruption guard) already track the other two
    findings from this effort's originating diagnosis session — see the
    2026-10-04 journal entry below for the full list of what was and wasn't
    new.

## Guiding Intent

An operator opened a live diagnosis session (py-spy + process census, not a
synthetic benchmark) against a real, loaded machine and found the Picker
"massively slow to respond to keyboard events": barely able to navigate the
list, the Escape-to-exit-prompt gesture taking long enough that the follow-up
Tab-to-Exit keystrokes didn't register, and characters dropped while typing
into the New Worktree prompt's `Input` widget. That session's root-cause
chain is recorded in this effort's first journal entry.

This effort exists to turn that one incident into a **standing, numeric,
enforced bar** rather than a one-off bugfix: the Picker is not done being
performant when today's known slow paths are fixed — it is done when three
concrete budgets hold, are measured, and are guarded so a future regression of
the same shape (a synchronous call quietly reappearing on a hot path) is
caught before an operator notices it, not after.

**The effort is not done until all three hold, measured, on a representative
loaded machine (not just an idle demo environment):**

1. **Every keypress is acknowledged in <~100ms.** Navigation, selection, and
   typing into an open input never wait on a network/subprocess round trip.
2. **The Picker boots to its first interactive frame in <~2s.**
3. **An action menu opens in <~1s.**

## Participants

Single-session effort for now; no cross-machine/CodeSpace/container dispatch
is anticipated. Revisit if a phase needs parallel execution.

## Context

### What the originating diagnosis session found (2026-10-04)

Working from a live, loaded machine (not idle), using `py-spy dump`/`record`
against a running Picker process plus a Windows process census:

- **The concrete, previously-undetected regression (this effort's Phase 0):**
  `production_picker/picker_tui/engine_runtime.py`'s `_poll_update_state`
  called `update_stage.indicator_state()` **synchronously from `_tick`**
  (the Textual render-tick callback, on the asyncio main thread), every 5th
  frame (~2x/sec). That call chains into
  `engine_group_a.update_stage_indicator_state` →
  `engine_client.run_json` → a full `agent-worktrees stage-update
  --indicator-state --json` **subprocess** round trip — measured directly at
  **2.0-2.5 seconds per call** on the diagnosis machine (`Measure-Command`-
  style timing, 3 runs, not a one-off cold-start artifact). Since a single
  call already costs far longer than the 0.5s interval between calls, the
  render/input loop was blocked almost continuously — directly explaining
  every symptom in the operator's report. Confirmed live via `py-spy dump`:
  the `MainThread` stack sat inside `subprocess.communicate()` under
  `_poll_update_state` → `_tick`.

  This call's own docstring claimed it was "Cheap (two small files)" — a
  claim that was true of the *design intent* (a mount-time comment in
  `engine_loading.py` describes "the picker polls its status file") but not
  of the *actual implementation*: `update_stage.indicator_state()` has no
  client-side cache of its own at all, unlike its sibling
  `manager_update_check.py` (a fully analogous "is an update available"
  check) which does — `read_status`/`should_check`/`check_now` backed by a
  persisted, TTL-gated status file, with the expensive check dispatched via
  `_run_bg` and only `should_check()`'s cheap cache-staleness read on the
  render tick. The sibling `_poll_manager_update_state` right next to the
  broken method already demonstrates the correct idiom. A vestigial
  `self._upd_poll_frame = -1` in `engine_loading.py` (set once, never read
  anywhere) is the fossil of a throttle that was apparently planned but never
  wired up.

- **Everything else the diagnosis surfaced was already known and tracked**
  (confirmed by reading this repo's own effort docs, not assumed) —
  listed here so nobody re-investigates them from scratch:
  - Sustained CPU in the resident `agent-worktrees status-monitor --passive`
    daemon (caught live via `py-spy` inside `copy.deepcopy`,
    `record_cache.py`'s `cached_load`) is the exact behavior already
    documented in `agent-worktrees-authoritative-daemon/README.md:1232-1251`.
    Not new. **Phase 3** below is the fix.
  - Three concurrent `mux-daemon` processes spanning three different
    installed versions, never retired across upgrades, is exactly
    `worktree-manager-control-plane`'s tracked "Background daemon rotation"
    gap and its filed follow-up `#5001`. Not new; no new phase needed here.
  - A corrupted `~/.agent-worktrees/control-plane-providers.d/worktree-
    manager.json` (a leaked pytest `tmp_path` from `test_self_update_without_
    git`, which blocked every fresh Picker launch on the diagnosis machine
    outright) is exactly `copilot-extensions#5122`, already guarded (detect +
    warn) by `control_plane_providers.py`'s
    `_LeakedTestTmpPathManifestError`. Not new; the guard already exists,
    it just doesn't self-heal yet — out of this effort's scope unless a
    future phase is explicitly opened for it.
  - `agent-worktrees doctor` on the diagnosis machine found 102 stale
    "active" session records (back to 2026-09-02) and 39 orphaned empty
    session-state shells — pure data-hygiene on one operator's machine, not
    a code defect. Fixed live with `doctor --fix --gc-sessions
    --apply-daemon-health`; no phase here, it's not reproducible code
    behavior.

### What's genuinely new scope (not covered by any existing effort)

- **agent-codespaces and agent-containers pivots have no streaming/daemon-
  relay path at all.** `pivot-streaming-transport`'s Phase 3 scope is
  explicitly agent-dispatch (3a) and agent-bridge (3b/3c) only — both
  plugins the operator asked to include (`agent-worktrees`, `agent-dispatch`,
  `agent-codespaces`, `agent-containers`, `agent-bridge`) are not all
  covered yet. **Phase 2** below is the gap.
- **Boot-time (<~2s) has no owning effort or measurement at all.** Nothing
  in `pivot-streaming-transport` or `worktree-manager-control-plane` sets or
  measures a cold-boot budget. **Phase 4** below opens that investigation
  from scratch.
- **No regression harness holds any of these budgets over time.** Phase 0's
  bug is exactly the failure mode budgets-not-fixes is meant to prevent: it
  was invisible until an operator happened to notice. **Phase 5** below is
  the enforcement mechanism.

## Request

> Let's take the architectural analysis, use it to make some vision updates,
> and then dive in and focus on making the Worktree Manager UX smooth. It's
> clear we need the live-stream version of the pivot-command calls (so we
> can use HTTP/WS connections with `agent-worktrees`, `agent-dispatch`,
> `agent-codespaces`, `agent-containers`, and `agent-bridge` singletons to
> obtain data and perform commands when showing the respective pivots. And
> then we need to tackle the CPU-burn in status-monitor and similar areas.
> Get everything recorded into a performance and responsiveness focused
> effort. The effort isn't done until every key-press in Worktree Manager
> responds in <~100ms, and Worktree Manager itself can boot in under ~2s,
> and action menus for items open in <~1s.

## Plan

### Phase 0 — Fix the update-indicator poll's blocking subprocess call

- [x] Throttle `_poll_update_state` to a wall-clock interval
      (`UPDATE_STATE_POLL_SECS`, default 30s, env-overridable via
      `AGENT_WORKTREES_PICKER_UPDATE_STATE_POLL_SECS`, mirroring
      `engine_helpers._poll_secs()`'s existing convention) instead of a bare
      frame-count gate.
- [x] Dispatch the actual `update_stage.indicator_state()` call through
      `self._run_bg(...)`, mirroring `_poll_manager_update_state`'s existing
      pattern exactly (including re-checking `_update_state_pinned` in the
      `_done` callback, since the pin can land while the background call is
      still in flight).
- [x] Replace the dead `_upd_poll_frame` stub with real throttle state
      (`_last_update_state_poll`, `_update_state_poll_pending`).
- [x] Verified with `py-spy dump` against a live, freshly-launched Picker:
      `MainThread` now sits in `asyncio` event-loop polling / Textual
      rendering; the subprocess call is observed only on the
      `production-picker-reap-orphans`-style background thread, never on
      `MainThread`.
- [x] Full `tests/` suite (1322+ tests, excluding the pre-existing flaky
      `test_picker_tui.py` items — see Journal) passes unchanged;
      `tests/production_picker/test_update_stage.py` and
      `tests/test_manager_update_check.py` pass unmodified (this phase
      deliberately did not change `update_stage.py`'s public contract, to
      avoid touching those tests or any other code that calls
      `update_stage.indicator_state()` directly).
- [ ] New regression test asserting `_poll_update_state` never calls
      `update_stage.indicator_state()` from a context where
      `self.app.call_from_thread` would be unavailable (i.e., it always goes
      through `_run_bg`) — not yet written; see Validation Plan.

### Phase 1 — Verify Phase 0 under the same loaded conditions that surfaced it

_(Measurement phase — confirms Phase 0 actually moves the needle on the
operator's original complaint, not just on a clean idle machine.)_

- [ ] Re-run the original repro (`py-spy record` during sustained up/down
      navigation, Escape-to-exit-prompt, typing into the New Worktree prompt)
      on a similarly loaded machine and record keypress-to-repaint latency
      before vs. after Phase 0.
- [ ] Confirm the operator-reported symptoms (dropped characters in the New
      Worktree prompt's `Input`, Escape/Tab not registering) no longer
      reproduce.

### Phase 2 — Extend the daemon-relay fast path to agent-codespaces and agent-containers

_(Builds directly on `pivot-streaming-transport`'s Phase 3a/3b mechanism and
manifest flags (`stream`/`subscribe` on `RegisteredPivot`) — this phase is
wiring two more plugins into an existing mechanism, not inventing a new one.
Coordinate with `pivot-streaming-transport` rather than duplicating its
design review; this phase's own design doc should link back to
`efforts/2026/10/07 pivot-streaming-transport/phase-3-design.md` (archived)
for the shared contract instead of restating it.)_

- [ ] Confirm whether agent-codespaces and agent-containers each already run
      a persistent, addressable daemon analogous to agent-bridge's/agent-
      dispatch's (prior art this phase should extend, not assume) — audit
      their actual plugin architecture before design, the same gate Phase 3's
      own design review applied to agent-dispatch vs. agent-bridge (they
      turned out *not* to be symmetric).
- [ ] Add `stream`/`subscribe`-aware CLI support to each plugin's pivot `list`
      command (or confirm one already exists and only the pivot manifest flag
      is unset — `pivot-streaming-transport`'s own Context section found this
      exact gap for agent-bridge/agent-dispatch at the start of that effort).
- [ ] Flip each plugin's pivot manifest (`plugins/agent-codespaces/pivots/
      agent-codespaces.json`, `plugins/agent-containers/pivots/
      agent-containers.json`) to adopt the flags once the above lands.

### Phase 3 — Close the status-monitor CPU-under-scale gap

_(Turns the already-documented `agent-worktrees-authoritative-daemon`
finding into a fix; extends #918 rather than reopening it.)_

- [x] Gave `record_cache.cached_load()` an explicit opt-in
      (`copy_result=False`) non-deep-copying fast path, threaded through
      `tracking.load_record(copy_result=...)` and
      `tracking.list_records(copy_records=...)`, defaulting to the existing
      (safe) deep-copy behavior everywhere. Audited the one caller worth
      switching — `find_worktree_id_by_cwd` (the actual documented hot path:
      `sessions.verify_worktree_active` → `reclaim.resolve_bound_copilots` →
      here, called once per live session per sweep) only reads
      `rec.worktree_path`/`rec.worktree_id` and never mutates or retains a
      record — and switched only that one call site. Deliberately did NOT
      change the default for any of `list_records`'s ~45 other direct call
      sites (plus `load_record`'s own ~160 call sites): auditing all of them
      for mutate-safety in one pass was not something this session could do
      with confidence, so the fix stays a narrow, explicit opt-in rather
      than a global policy change.
- [x] Regression tests added: `test_record_cache.py`'s
      `test_copy_result_false_returns_the_cache_s_own_object` (identity
      check: `copy_result=False` hits/misses both return the cache's own
      object; the default path is unaffected) and `test_tracking.py`'s
      `test_list_records_copy_records_false_skips_the_deep_copy` +
      `test_find_worktree_id_by_cwd_unaffected_by_the_copy_skip` (functional
      correctness unchanged). All pass, plus the full existing
      `test_record_cache.py`/`test_tracking.py` suites (242 tests).
- [x] Re-measured at a fleet scale comparable to the diagnosis machine's
      (100+ tracked records) before/after, via
      `tests/bench_phase3_record_cache.py` (a manual, non-pytest-collected
      script — see Journal for the methodology and numbers): **~66-71%
      reduction** in `find_worktree_id_by_cwd` sweep time at 120 tracked
      records, directly isolating the `copy.deepcopy` cost this phase
      removed.

### Phase 4 — Boot-time budget (<~2s)

_(No existing effort measures or owns this; starts from a cold investigation,
not a known fix.)_

- [x] Profile a cold `worktree-manager`/Picker launch (`py-spy record` from
      process start to first interactive frame) on a representative machine
      and identify the dominant cost(s) — process/interpreter startup,
      module import graph, initial roster/config scan, or first-paint data
      fetch are all plausible and this phase should not assume which before
      measuring. Done, via `worktree-manager picker screenshot --demo` (the
      headless capture seam, driving the real production Picker's full
      bootstrap against deterministic mock data) under both `py-spy record`
      and `python -m cProfile`. See Journal for the full before/after numbers
      and what each profile actually showed.
- [ ] Narrow whichever cost(s) the profile actually shows dominate, to bring
      cold boot under ~2s. **Partially done:** the first profile's dominant
      cost (redundant marketplace-manifest re-parsing, see Journal) is fixed
      and measured. The *second* dominant cost it surfaced — the ~17-18
      per-project `git` subprocess calls during project/root discovery
      (`plugin_activation.resolver._git`, `bare_anchor.git_root`) — is now
      also fixed: `_git`'s `shutil.which("git")` PATH-rescan is cached once
      per process, and the bare-anchor fallback's two separate probe calls
      (`--is-bare-repository` then `--absolute-git-dir`) are folded into one
      `git rev-parse --is-bare-repository --absolute-git-dir` call. Isolated
      microbenchmark (not full-process wall-clock, which is noisy at this
      scale): resolving the cached executable is ~99.5% faster than the
      uncached `shutil.which` scan, and the combined bare-repo probe cuts
      that call site's own subprocess time ~53% (see Journal for numbers).
      **Still not reflected in end-to-end cold-boot wall-clock** (still
      ~8.4-9.2s, unchanged within noise) — profiling after this fix shows
      the git-call cost was never actually the largest remaining piece;
      Python module-import/`exec_module` overhead now dominates the
      cProfile trace. That import-overhead cost is a **different, not yet
      profiled call site** — a future session should profile *it*
      specifically (same discipline as every other call site in this
      phase: verify the shape, don't assume it matches a prior fix) before
      this item can be checked off. `visions/picker/README.md`'s
      `§Behaviors/responsive-by-budget` now names the three recurring cost
      shapes this phase keeps finding one call site at a time (eager/
      over-resolution, scanning everything installed instead of what the
      boot path actually needs, and monolithic cold-path modules) — the
      import-overhead investigation should be framed against those three
      (e.g. is the plugin/provider set being imported eagerly instead of
      on-demand? is a large shared module being imported in full for one
      small piece? is boot resolving every installed plugin instead of
      only the ones the first frame needs?) rather than treated as an
      unrelated new mystery.

### Phase 5 — Hold the budgets: a timing regression harness

_(The enforcement mechanism `§responsive-by-budget` requires: Phase 0's bug
was invisible until an operator noticed it. Prevents a repeat.)_

- [ ] A headless timing harness (building on the Picker's existing
      `capture`/headless-render seam —
      `visions/picker/README.md`'s `renderable-and-assertable-headless`) that
      measures keypress-to-repaint latency, boot-to-first-frame latency, and
      action-menu-open latency under a simulated/synthetic fleet load, and
      fails the budget if exceeded.
- [ ] Wire it into CI (or document why it can't run there, if a loaded-
      machine condition can't be reproduced deterministically) so a future
      regression of Phase 0's shape is caught pre-merge, not by an operator.

## Validation Plan

- [ ] **Phase 0:** the `py-spy`-verified live check above, plus a written
      regression test (see Phase 0's last unchecked item).
- [ ] **Phase 1:** before/after latency numbers recorded in the Journal,
      captured the same way the original diagnosis captured them (so the
      comparison is apples-to-apples), on a machine under comparable load.
- [ ] **Phase 2:** per-plugin before/after latency for that pivot's refresh
      path, plus the same fallback-safety tests `pivot-streaming-transport`'s
      Phase 1/2 already wrote for agent-dispatch/agent-bridge (stream flag
      absent/stale degrades to one-shot, never a hard failure).
- [x] **Phase 3:** a test proving the fast-path cache entry is
      never handed to a caller requesting the default (copy) behavior —
      `test_copy_result_false_returns_the_cache_s_own_object` — done.
      Before/after sweep-time measurement at a comparable record count —
      done, see Journal (~66-71% reduction at 120 records).
- [ ] **Phase 4 (partial):** cold-boot wall-clock time before/after, on the
      same machine/conditions, with the profile that justified the fix
      attached — done for the marketplace-reparse fix (~10.5-12.4s ->
      ~7.7-9.1s, see Journal). The git-subprocess-call fix is verified with
      an isolated microbenchmark instead (full-process wall-clock is too
      noisy at this call site's scale — see Journal), and does not move
      end-to-end cold boot, since profiling found a different cost
      (module-import overhead) now dominates. Still open for that newly
      surfaced dominant cost and for reaching the <~2s budget itself.
- [ ] **Phase 5:** the harness itself passing, plus one deliberately
      reintroduced synchronous call (the Phase 0 bug, reverted) proving the
      harness actually catches it before trusting it as a guard.

## Proposal

_Pending — file the umbrella issue once Phase 0 is sent for review, per this
effort's own "Umbrella issue" field above._

## Journal

### 2026-10-04 — Kickoff + Phase 0 landed

Originating diagnosis session (summarized in Context above) traced the
operator's "massively slow to respond to keyboard events" report to a single
concrete regression: `_poll_update_state` blocking Textual's main/render
thread on a ~2-2.5s subprocess round trip, roughly twice a second. Fixed by
throttling to a wall-clock interval and dispatching the actual call through
the codebase's existing `_run_bg` background-thread pattern (already used one
method away, in `_poll_manager_update_state`, for the exact same class of
problem) — see Phase 0 above for the precise diff description.

Verified: (1) live `py-spy dump` against a freshly built picker shows
`MainThread` is never observed inside `subprocess.communicate()` any more,
only idle-in-asyncio or actively rendering; (2) the full test suite (1322+
tests) passes with this change, modulo two pre-existing flaky
`test_picker_tui.py` tests confirmed independently flaky (reproduced failing
*and* passing in isolation, with and without this change applied — not a
regression) and one pre-existing, platform-unrelated `test_update.py` symlink
test failure (reproduced identically with the change reverted).

Everything else the diagnosis session turned up was cross-checked against
this repo's own effort docs and found already tracked (listed in Context
above, under "What the originating diagnosis session found") — recorded here
explicitly so a future reader doesn't re-file issues that already exist.

Opened this effort (rather than filing Phase 0 under
`worktree-manager-control-plane`, whose Phase 3c this bug slipped past) to
give the operator's numeric bar — <~100ms keypress, <~2s boot, <~1s menu-open
— a standing home with its own measurement and enforcement phases (1, 4, 5),
rather than letting the fix read as "one bug closed, done."

Phase 0 PR (#5258) merged to `dev`, promoted to `main` by this repo's
automated `validate-and-promote.yml` pipeline (no manual promotion step
needed — confirmed the fix's content is present in `origin/main`), and
deployed locally via `worktree-manager update` (installed version now
`0.5.3-dev1`, confirmed to contain the fix).

### 2026-10-04 — Phase 3 landed (partial — code fix only, CPU re-measurement still open)

Gave `record_cache.cached_load()` an explicit `copy_result=False` opt-out of
the per-hit `copy.deepcopy`, threaded through `tracking.load_record` and
`tracking.list_records`, and switched exactly one call site —
`find_worktree_id_by_cwd`, the actual documented hot path
(`sessions.verify_worktree_active` → `reclaim.resolve_bound_copilots` →
here) — after auditing it read-only-safe (only reads `worktree_path`/
`worktree_id`, never mutates). Deliberately kept the default (copy) behavior
for every other caller (`list_records` has ~45 direct call sites,
`load_record` ~160) rather than attempting a global audit in one session —
see Phase 3 above for the full reasoning.

Added regression tests proving (1) `copy_result=False` really does return
the cache's identical object, not a fresh copy, while the default path is
unaffected, and (2) `find_worktree_id_by_cwd`'s actual resolution behavior is
unchanged. Full `test_record_cache.py` + `test_tracking.py` suites (242
tests) pass. Did not run the full `agent-worktrees` plugin suite locally in
this session (it did not complete within a reasonable wait — likely just a
large, subprocess-heavy suite, not evidence of a hang, but not confirmed
either way); relying on this repo's own CI gate on the PR to cover it.

**Left open, explicitly:** re-measuring sustained daemon CPU before/after on
a comparable record count. The fix removes a cost directly observed live
(the exact `copy.deepcopy` frame `py-spy` sampled `MainThread` inside during
the original diagnosis), but that is reasoned evidence, not a fresh
measurement — a future session should still do the live-timed before/after
`pivot-streaming-transport`'s own Phase 5 methodology calls for, rather than
treating this phase as fully closed from the code change alone.

### 2026-10-05 — Phase 3's remaining measurement closed

Re-measured the `copy_result=False` fast path's actual cost reduction at a
fleet scale comparable to the diagnosis machine's (100+ tracked records),
rather than relying on the code-change reasoning alone. Rebuilt the working
environment first: the previous session's `pip install -e` links for
`agent-worktrees` and its local libs (`agent-procutil`,
`agent-plugin-resolve`, `agent-remote-login-shell`, etc.) all pointed at
now-finalized/deleted ephemeral worktrees from earlier sessions — reinstalled
each editable from this session's own worktree paths before anything would
import. (Left as a known rough edge — not fixed here, out of this effort's
scope — but worth a future harness issue: an editable install surviving a
`finalize`'d worktree's deletion silently breaks the next session that reuses
the same machine.)

Wrote `tests/bench_phase3_record_cache.py` (a manual, non-pytest-collected
script, not part of the regular suite): builds 120 real on-disk
`WorktreeRecord` YAML files via the same `tracking_lifecycle.create_new_record`
factory production code uses, warms the cache once, then calls the actual
`tracking.find_worktree_id_by_cwd` hot path 1,000 times (40 simulated live
sessions/sweep × 25 sweeps) — once with today's code unmodified
(`copy_records=False`), once with `tracking.list_records` wrapped to force
the old unconditional `copy_records=True` behavior, against the identical
on-disk fleet and call sequence, isolating exactly the cost Phase 3 removed.

**Result (median of 3 runs each):** before ≈ 27.7-29.0s / 1,000 calls (≈
27.7-29.0ms/call), after ≈ 8.3-9.4s / 1,000 calls (≈ 8.3-9.4ms/call) — a
**~66-71% reduction** in `find_worktree_id_by_cwd` sweep time at this record
count. This is a wall-clock sweep-time measurement (not a live `py-spy`
sustained-CPU-% capture against a running daemon, which would need a real
multi-machine fleet to reproduce faithfully) but exercises the identical
production code path at the diagnosis machine's own record-count scale, and
directly confirms the reasoned fix: removing the per-hit `copy.deepcopy`
roughly triples `find_worktree_id_by_cwd`'s throughput at 120 tracked
records. Phase 3 is now fully closed, including its previously-open
measurement item.

### 2026-10-05 — Phase 4 opened: cold-boot profiled, one dominant cost fixed

Profiled a cold `worktree-manager`/Picker launch for the first time this
effort — nothing existing measured or owned this budget. Used
`worktree-manager picker screenshot --demo --format text` (the production
Picker's own headless capture seam, against deterministic mock data) as a
representative cold-boot proxy: it exercises the real module import graph,
the real plugin-activation/plugin-resolve bootstrap, and the real Picker
mount/first-paint path, then exits — without needing an interactive
terminal. `Measure-Command` on this machine: **~10.5-12.4s**, roughly
5-6x the <~2s budget.

Profiled with both `py-spy record` and `python -m cProfile` (cProfile gave
cleaner call attribution; py-spy confirmed the same hot path live).
cProfile's cumulative-time ranking showed two concrete dominant costs, not
assumed up front:

1. **`plugin_activation.resolver._local_root`** (called once per *enabled
   plugin source*, both globally and per registered project) independently
   re-read and re-parsed the **same** marketplace manifest JSON file once per
   plugin that names it — with ~24-28 enabled plugins sharing 1-2
   marketplaces, this measured as 29 `load_marketplace` calls / 56
   `_load_json` calls / 177 `pathlib.read_text` calls in one bootstrap,
   ~9.1s cumulative in `_git`-adjacent subprocess/read paths combined.
2. A ~5.6s cost in repeated marketplace JSON loading specifically
   (`plugin_resolve.marketplace.load_marketplace`/`_load_json`), the same
   redundant-reparse shape as (1) and in fact the same root cause: `_local_root`
   has zero cross-call caching, so N plugins in the same marketplace pay N
   full re-reads of a file that cannot have changed mid-process.

This is structurally identical to Phase 3's `record_cache` fix (an
uncached, read-heavy call repeated once per item in a loop, re-parsing a
file nothing in-process could have changed) — not a new finding pattern,
just a new call site. Fixed by threading two optional, per-call caches
(`_manifest_cache`, `_marketplace_cache`, both plain dicts keyed on the
canonical marketplace root) through `_local_root`/`_marketplace_manifest`,
created once in `resolve_active_plugins()` and shared across every
plugin/scope that function resolves in one pass — scoped to the lifetime of
a single `resolve_active_plugins()` call only (this is a short-lived CLI
process; no staleness risk, same reasoning as `resolve.py`'s own pre-existing
analogous `loaded: dict[str, object] = {}` cache one function over, which
this mirrors). Both cache parameters default to `None` (meaning "no cache,
re-read every time"), so every other/test caller of these private helpers is
byte-for-byte unaffected.

`plugin-activation` is a **vendored** lib (`libs/plugin-activation` canonical
+ byte-identical copies in `plugins/agent-worktrees/libs/` and
`plugins/customizing-copilot/libs/`) — propagated the fix to all three
copies and bumped the shared version (`0.1.0-dev6` -> `0.1.0-dev7`) per
`tools/check-vendored-libs-sync.py`'s own requirement. `tools/
sync-vendored-libs.py --restore-canonical` was NOT used broad-strokes: a
first invocation also "fixed" unrelated pre-existing drift in four other,
untouched vendored libs (deleting their `tests/conftest.py`, modifying their
`README.md`) as a side effect of resyncing from whichever copy happened to
be newest — reverted those via `git checkout --`, and instead copied only
`resolver.py`'s content directly across the three `plugin-activation`
copies, so this change touches exactly what it means to.

**Result (Measure-Command, 3 runs each):** before ≈ 10.5-12.4s, after ≈
7.7-9.1s — the full suite of ~24-28 enabled plugins' marketplace-manifest
reads collapsed from N-per-marketplace down to 1-per-marketplace per
bootstrap. Re-profiled after the fix (cProfile): total profiled time dropped
from 11.169s to 5.823s (~48% less profiler-measured work), and
`_local_root`/`load_marketplace`/`_load_json` no longer appear anywhere near
the top of the cumulative-time ranking — confirming the fix actually
removed the cost the first profile attributed to it, not just coincidental
noise.

**Left open, explicitly:** cold boot is still ~4-4.5x over the <~2s budget.
The post-fix profile's new top cost is ~17-18 remaining `git` subprocess
calls (`plugin_activation.resolver._git`, `bare_anchor.git_root`) during
project/root discovery (`harness_state.build_projects`,
`engine_client.get_value`) — a different call site than the one fixed here,
not yet profiled deeply enough to say whether it has the same
"same-file/same-answer-every-call" shape this fix exploited, or needs a
different approach (e.g. batching the git calls, or caching per-project
git-root resolution across a single bootstrap the same way marketplace
loads now are). A future session should profile *that* specifically before
assuming it's fixable the same way — this session's own evidence (Phase 0's
"obviously the same idiom as its sibling" vs. Phase 3's "a real, audited,
narrow opt-in" vs. this session's "two call sites, same shape, same fix")
shows the right fix shape varies per call site and must be verified, not
assumed, each time.

### 2026-10-05 (later) — Modal backdrops reverted to translucent (out-of-phase, operator-directed)

Not a numbered Plan phase -- the operator asked, in this effort's context,
to restore "main screen contents behind dialogs" now that Phase 0's
render-thread block and Phase 4's marketplace-reparse cost are fixed.
That visual (translucent `ModalScreen` backdrops) had been traded away for
a *different*, already-shipped, already-measured reason in the separate
`picker-new-session-prompt-and-composer` effort (`#5074`: ~15-20% per-
keystroke render cost from Textual's full-recomposite-on-translucent-
screen behavior) -- a Textual compositing cost this effort's own Phase 0/4
fixes do not touch at all (different root causes entirely: a blocking
subprocess call, and a redundant file-parse paid once at boot). Reverted
all 18 current modal CSS declarations back to `background: $background
55%;` (see that effort's own 2026-10-05 Journal entry for the full
file list, reasoning, and the explicit trade-off this re-introduces).
Recorded here only as a pointer -- this effort does not own that finding
or its reversal, and does not re-litigate it.

### 2026-10-05 (later still) — Real crash diagnosed and fixed: stale payload + two durable process-lifecycle bugs

Not a numbered Plan phase either -- the operator reported the real Picker
crashing (or failing to open at all) after waiting ~30s, on the machine
this whole effort has been profiling against. Diagnosis, in order:

1. **Immediate cause: a stale installed payload.** This machine's real
   `worktree-manager`/`agent-worktrees` install (a versioned-slot payload
   under `~/.worktree-manager`/`~/.agent-worktrees`, NOT the pip-editable
   dev pointers this session had been using for profiling/testing -- those
   are a separate, parallel install path that was never what actually runs
   from `PATH`) predated all three of today's merged fixes. `worktree-manager
   update` (the real self-update/payload-install flow) brought it current
   (0.5.7-dev2 -> 0.5.7-dev3) from the now-auto-promoted `main`. Confirmed
   live (`psmux`, 35s wait) that the Picker now opens cleanly against
   `odsp-web-harness`.

2. **A durable bug, found while diagnosing: zombie Picker process trees
   with no owning terminal.** This machine had 7 such trees piled up (some
   days old) across 4 different installed-version generations. Root cause:
   `agent_worktrees.manager_launch_cli._exec_worktree_manager`'s Windows
   branch (`platform.system() == "Windows"`) used a plain
   `subprocess.Popen` + `wait()` -- unlike the POSIX branch, which really
   does `os.execvpe` and becomes the child (so the whole tree lives and
   dies as one process tied to its own console). If THIS process is torn
   down abruptly (console closed, owning session/worktree killed, anything
   that skips normal Python exception unwinding) nothing ever signals or
   waits on that multi-process launch chain (a `.cmd` shim -> `uv run` ->
   a venv interpreter -> the real module, sometimes re-exec'd through yet
   another interpreter) again -- it just runs forever. Fixed by containing
   the Windows launch in a `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` Windows Job
   Object via `agent_procutil.spawn_sync_in_kill_on_close_job` -- the exact
   same primitive `agent_machines.fleet_update` already uses for this
   identical failure class, just not yet applied to this call site. The OS
   itself kills everything still in the job the moment this process's own
   handle to it closes, for any reason, clean or not. Verified safe for the
   mux-daemon (a legitimately long-lived descendant of this same launch):
   it already uses `windowless_daemon_kwargs(breakaway=True)`/
   `detached_kwargs(breakaway=True)` everywhere it's spawned, specifically
   so it escapes a containing job and outlives any single Picker
   invocation -- confirmed by reading every status-monitor and mux-daemon
   spawn site, not assumed. Two new regression tests
   (`test_exec_worktree_manager_closes_job_on_windows_clean_exit`,
   `test_exec_worktree_manager_falls_back_to_kill_without_a_job`) prove the
   containment call happens and its handle is closed/killed on exit, not
   just that some process got spawned. One pre-existing test
   (`test_manager_handoff_binds_exact_engine_runtime`) patched
   `subprocess.Popen` directly and needed updating to patch the new
   interception point (`agent_procutil.spawn_sync_in_kill_on_close_job`)
   instead -- same behavior proven, different call site.

3. **A second durable bug, found via `agent-worktrees doctor`: two
   resident status-monitor daemons claiming the same "active" slot, and
   `doctor --fix` refusing to pick a winner.** Root-caused with a direct,
   in-process call to `daemon_health.doctor_report(apply=False)` (not
   guesswork from the CLI's own summarized output): the routing table's
   `active` entry correctly pointed at the new daemon generation this
   machine's `worktree-manager update` had just cut over to (pid 3356,
   generation 47) -- but the *separate* `status-monitor.lock` file still
   named the OLD generation (pid 11048), which a live `py-spy dump` showed
   stuck busy-looping inside its own pending-handoff-retire sweep, never
   reaching the point of relinquishing (or rewriting) its lock claim.
   `zdd.diagnostics._validated_owner` treated ANY disagreement between the
   lock and the routing table as fatal -- "no validated live owner" --
   refusing to vouch for *either* candidate, which is exactly backwards
   for `doctor`'s own job (restore a pristine state, not shrug at an
   ambiguous one): the routing table's `active` entry IS this service's
   own single, generation-numbered source of truth for who is currently
   being routed to, and is strictly fresher than a lock a stuck old
   generation never got around to updating. Fixed by giving
   `_validated_owner` a fallback: when the lock doesn't vouch for anyone,
   trust the routing table's `active` pid directly, but ONLY if a live,
   freshly-censused candidate actually holds that exact pid right now (no
   fabricating an owner nothing can back up -- covered by its own
   regression test). This also incidentally covers a related gap:
   `_inspect_superseded_generations` only ever checks the table's
   `active`/`previous` two-slot history, so a daemon stuck more than one
   generation behind (exactly pid 11048's case here) was invisible to it
   too, with no other path to ever being reaped. Three new
   `zdd.diagnostics` tests (previously an entirely untested module at the
   unit level -- only its rendered-finding-dict *output* had test
   coverage, not the validation logic producing those dicts) cover: the
   fallback firing on real disagreement, the ordinary agreeing case still
   using the lock alone (no behavior change for the common path), and the
   fallback correctly refusing to fabricate an owner when the table names
   a pid nothing currently holds.

`agent_procutil`, `manager_launch_cli.py`'s containing plugin
(`agent-worktrees`), and `zdd` are all vendored libs with multiple
consumer copies in this repo -- both fixes were propagated to every
existing copy (`check-vendored-libs-sync.py` confirms), with a version
bump per lib and a changefile per fanned-out consumer plugin, the same
discipline Phase 4's plugin-activation fix already established this
session.

**Landed and verified live** (PR #5411, merged after 5 CI reruns -- the
only failing check across all of them was the experimental, explicitly
`continue-on-error`/"shadow, non-blocking" coverage-guided-selection step
hitting its own job's tight 6-minute timeout, consistently at ~6m15-30s,
never a real test failure; all 150 real `agent-worktrees` tests passed
every single run. A pre-existing CI fragility -- this PR's large fanout
changefile count did not touch that step's own code -- not something to
chase further in this effort). `worktree-manager update` on the same
diagnosis machine (0.5.7-dev3 -> 0.5.8-dev1) deployed the real fix;
`agent-worktrees doctor --fix --apply-daemon-health` then genuinely
reaped a live duplicate daemon (`pid 63356 -> terminated
(windows-verified-handle)`), and a follow-up `doctor` run reported
`Resident daemon health: no abnormal cutover findings` -- clean. Note:
this fix prevents *future* orphaned launch trees and lets `doctor` reap
*future* duplicate daemons; it does not retroactively clean up the ~26
already-orphaned mux-daemon/Picker processes already piled up on this
specific shared machine from before the fix was deployed -- untangling
which of those are genuinely dead versus still serving another live
session's active work is a separate, higher-risk task on a shared
machine, not undertaken here.

### 2026-10-05 (new leg) — Phase 4's remaining git-subprocess cost fixed (partial)

Picked up the one concrete item the prior leg's handoff left open: the
post-first-fix profile's new top cost, ~17-18 `git` subprocess calls
(`plugin_activation.resolver._git`, `bare_anchor.git_root`) during
project/root discovery. Read `_verified_project_roots` (the loop that
verifies every registered project's identity once per `resolve_active_
plugins()` pass) and found two independent, narrow costs stacked at that
call site, not one:

1. **`_git` re-resolved `shutil.which("git")` on every single call** --
   a full `PATH` rescan (and, on Windows, a `PATHEXT`-extension probe per
   entry) for an answer that cannot change within a process's lifetime.
   Cached it behind `functools.lru_cache(maxsize=1)` on a new
   `_git_executable()` helper -- the same "same-answer-every-call"
   reasoning as this phase's first fix (marketplace-manifest re-parsing),
   just a different call site, and deliberately *not* threaded through a
   per-`resolve_active_plugins()`-call memo like that fix: the git
   executable's location is even more stable than a marketplace file (it
   cannot change for the life of the whole CLI process, not just one
   resolution pass), and keeping `_git`'s own signature unchanged matters
   here specifically -- `tests/test_plugin_activation.py` already has a
   test (`test_project_git_timeout_is_indeterminate`) that wholesale
   monkeypatches `resolver._git` with a bare `def timeout(*_args)`; adding
   a cache parameter to `_git`'s own signature would have broken that
   test's call site for no benefit, since the replacement function
   doesn't go through the real implementation at all in that test.
2. **`bare_anchor.git_root`'s fallback path made two separate git calls**
   (`--is-bare-repository`, then `--absolute-git-dir`) for every bare
   worktree-class anchor -- and *every* registered worktree-class project
   always takes this fallback (their `--show-toplevel` always fails first,
   by design, since a bare repo has no work tree). Confirmed `git
   rev-parse` accepts multiple query flags in one invocation, printing one
   line of output per flag in argument order, and folded the pair into a
   single `git rev-parse --is-bare-repository --absolute-git-dir` call,
   splitting the two-line response instead of spawning twice.

Both fixes preserve every existing caller's behavior exactly (the cache
is transparent, and the combined call reproduces the original two-call
decision logic line-for-line) -- verified with two new regression tests
(`test_git_executable_is_resolved_once_per_process`,
`test_bare_anchor_probe_is_one_combined_git_call` plus a rejection-path
counterpart) and the full existing `test_plugin_activation.py` suite (63
tests, all passing unchanged) in all three vendored copies, plus
`worktree-manager`'s own `test_harness_state.py` (19 tests) and
`agent-worktrees`'s `test_related_briefing.py`/`test_context_resolution.py`
(81 tests) as the two real consumers most directly exercising this path.

**Measured with an isolated microbenchmark** (`libs/plugin-activation/
tests/bench_phase4_git_calls.py`, a manual non-pytest script following
Phase 3's own bench-script precedent -- full-process wall-clock is too
noisy to isolate a cost this small against ~8s of total boot time):
resolving the cached git executable 200 times took 0.014-0.016s versus
2.99-3.09s uncached (~99.5-99.6% reduction); 30 combined bare-repo probes
against a real `git init --bare` repo took 5.895s versus 12.557s for 30
pairs of separate calls (~53.1% reduction in that call site's own
subprocess time).

**Left open, explicitly, and NOT a disproof of this fix:** re-measuring
full-process cold boot (`worktree-manager picker screenshot --demo
--format text`, `Measure-Command`, 3 runs) after this change still shows
~8.4-9.2s -- statistically unchanged from the ~7.7-9.1s the prior leg
measured after its own fix. Re-profiling with `cProfile` explains why:
the git-subprocess cost, while real and now smaller, was never actually
the largest remaining piece once profiled in isolation -- Python's own
module-import machinery (`importlib._bootstrap`'s `_find_and_load`/
`exec_module`, and `_io.open_code` reading `.py`/`.pyc` source) now
dominates the trace. This is a **different, not-yet-profiled-deeply call
site** than either of Phase 4's two fixes so far -- the same caution this
phase's own Journal has repeated at every step (Phase 0's "obviously the
same idiom as its sibling" vs. Phase 3's "a real, audited, narrow opt-in"
vs. the first Phase 4 fix's cache vs. this leg's cache-plus-call-folding)
applies here too: a future session should profile the import-overhead
cost specifically, with its own fresh `py-spy`/`cProfile` pass, rather
than assume it shares a fixable shape with any of the three call sites
already closed out. Propagated to all three vendored copies (`check-
vendored-libs-sync.py` confirms), version-bumped (`plugin-activation`
`0.1.0-dev7` -> `0.1.0-dev8`), with one changefile covering all 8 fanned-
out consumer plugins (`agent-bridge`, `agent-codespaces`, `agent-
dispatch`, `agent-logger`, `agent-machines`, `agent-worktrees`,
`customizing-copilot`, `worktree-manager`). Editable installs used only
for this leg's local profiling/testing were uninstalled before finishing,
per the prior leg's explicit correction.

`resolver.py` sits under `tools/check-module-size.py`'s shrink-only line
cap baseline, and this fix's own caching code pushed it 7 lines past its
exactly-at-ceiling grandfathered size. Rewrote the cache as compactly as
possible without sacrificing clarity (a module-level dict plus a single
comment, no new top-level function/decorator), then used the guard's own
sanctioned escape hatch for a case like this -- a manual, reviewed
baseline-ceiling edit (`tools/check-module-size.py`'s own docstring names
this as one of its two intended resolutions, alongside splitting the
module, which is out of scope for a narrow perf fix) -- raising the
ceiling from 1276 to 1283 for exactly the three copies this PR touches.

