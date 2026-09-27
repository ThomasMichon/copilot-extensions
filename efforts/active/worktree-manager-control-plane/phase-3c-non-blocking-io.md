# Phase 3c — Picker non-blocking I/O

- **Parent effort:** [`README.md`](README.md) § Phase 3c
- **Scope of this doc:** the ordered implementation plan for making the
  Picker's remaining I/O-touching setup/reload path consistently non-blocking,
  plus landed-step notes as each ordered slice merges.
- **Status:** Complete — Steps 1-2 merged 2026-09-26 as
  [#3903](https://github.com/ThomasMichon/copilot-extensions/pull/3903) and
  [#4007](https://github.com/ThomasMichon/copilot-extensions/pull/4007);
  Step 3 landed in
  [#4144](https://github.com/ThomasMichon/copilot-extensions/pull/4144);
  Step 4 landed in
  [#4164](https://github.com/ThomasMichon/copilot-extensions/pull/4164);
  Step 5 landed in
  [#4278](https://github.com/ThomasMichon/copilot-extensions/pull/4278).
  The in-repo non-blocking setup/reload cutover is complete; the optional
  follow-on for richer built-in verb progress is tracked separately in
  [#4274](https://github.com/ThomasMichon/copilot-extensions/issues/4274).
- **Governing visions:**
  - [`visions/picker`](../../../visions/picker/README.md):
    §Features/`decision-support-before-cost`, `programmatic-parity`;
    §Behaviors/`live-not-snapshot`, `graceful-capability-scaling`;
    §Non-Goals/*Not the editor or the agent session* and *Not in-process with
    the engine — it sits on top of the CLI*.
  - [`visions/plugins/agent-worktrees`](../../../visions/plugins/agent-worktrees/README.md):
    §Behaviors/`presentation-is-process-boundary-only`;
    §Non-Goals/*Not a terminal or multiplexer owner*.
  - [`visions/installer`](../../../visions/installer/README.md):
    §Features/`optional-worktree-agent-control-plane` — the Picker is an
    optional control-plane surface, so latency fixes must preserve graceful
    degradation rather than inventing a new required runtime.

## Why this needs its own ordered plan

The remaining Phase 3c gap is not "some code is slow"; it is a specific
ownership and concurrency problem.

`PickerScreen.setup()` still performs the pivot-registry scan and the
single-machine data load synchronously on the render/key-handling thread, and
four current entrypoints reach that path directly:

1. initial non-live mount (`engine_loading.py:23-29`)
2. manual `r` reload (`engine_input.py:142-146`)
3. post-config-section rescan (`engine_pivot_actions.py:70-92`)
4. post-contributed-worktree-action rescan (`engine_worktree_actions.py:474-494`)

The already-landed live path proves the desired UX shape exists:
`_setup_skeleton()` paints immediately and `_setup_live_async()` /
`_setup_live_pivots()` do the real work off-thread
(`engine_loading.py:175-276`). The problem is that the shared
single-machine/setup path never got the same treatment.

The hazard is not merely "remember to use a thread." The README records a
same-session prototype that backgrounded the setup scan, then backed it out
after it introduced a stale-result race: an older scan could still land after
a newer `setup()` call and overwrite the fresher state. I could not find a
standalone shared commit or closed PR containing that exact reverted prototype
in repo history; the README is the authoritative record of the failed attempt.
The closest discoverable prior art is adjacent code that already solved the
same class of bug elsewhere:

- `tasks.RegisteredPivotRuntime` carries a generation guard specifically so an
  `invalidate()` that fires mid-fetch drops the stale result instead of
  repainting pre-action state (`tasks.py:203-356`,
  `test_pivot_streaming.py:148-181`).
- the alternate Manager-side `picker_app.py` uses `state.generation` plus
  `run_worker(...)` to discard superseded loads (`picker_app.py:430-486`).

That is why this slice needs its own ordered plan: the correct fix shape is
not "move work to a thread" but "move work to a thread **and** make every
result apply only if it still belongs to the latest generation of that
screen's setup/reload cycle."

## Current-state inventory (`dev` as of 2026-09-26)

| File / lines | What lives there now | Why it matters to Phase 3c |
|---|---|---|
| `worktree-manager/src/worktree_manager/production_picker/picker_tui/engine_loading.py:23-29` | Non-live mount calls `self.setup()` directly. | This is one of the four current synchronous entrypoints. |
| `worktree-manager/src/worktree_manager/production_picker/picker_tui/engine_input.py:142-146` | The `r` key handler calls `self.setup()` inline on the key-handling thread. | A repeated reload can supersede an earlier reload; any async cutover needs stale-result protection. |
| `worktree-manager/src/worktree_manager/production_picker/picker_tui/engine_pivot_actions.py:70-92` | `_run_config_section()` already offloads the subprocess itself via `_run_bg`, but its `_done` callback re-enters `self.setup()` synchronously. | Finishing an action must not hand control back to a blocking rescan. |
| `worktree-manager/src/worktree_manager/production_picker/picker_tui/engine_worktree_actions.py:474-494` | `_run_wt_action()` has the same shape: the action is off-thread, then `_done` synchronously calls `self.setup()`. | This is the second post-action rescan the README names. |
| `worktree-manager/src/worktree_manager/production_picker/picker_tui/engine_runtime.py:253-430` | `setup()` starts the prewarm thread, invalidates registered-pivot runtimes, re-derives tab metadata, and in the non-live path performs `self.src.load()` inline (`386`). | This is the actual synchronous hot path Phase 3c must move off the render thread. |
| `worktree-manager/src/worktree_manager/production_picker/picker_tui/engine_pivots.py:10-70` | `_load_pivots(scan=True)` does a real manifest/pivot scan inline; `_scan_pivot_payload()` is the safe off-thread equivalent. | The seam already exists; the missing piece is using it on every setup/reload caller with generation safety. |
| `worktree-manager/src/worktree_manager/production_picker/picker_tui/engine_loading.py:175-276` | Live mode already uses `_setup_skeleton()` plus background `_setup_live_async()` / `_setup_live_pivots()`. | This is the reference pattern for the non-live/single-machine cutover. |
| `worktree-manager/src/worktree_manager/production_picker/picker_tui/engine_worktree_actions.py:189-247` | `_open_submenu()` opens the Actions menu immediately from cached state, then offloads authoritative liveness verification with `_run_bg(...)`. | Menu-open latency is already solved here; Phase 3c should guard against regressing it, not redesign it. |
| `worktree-manager/src/worktree_manager/production_picker/picker_tui/engine_pivot_actions.py:303-351` and `tasks.py:580-688` | D4 progress actions already stream NDJSON progress off-thread into `ProgressScreen`. | Progress-reporting execution is already non-blocking; the remaining gap is built-in verbs having only spinner/status text. |
| `worktree-manager/tests/production_picker/test_picker_tui.py:8138-8175,8267-8325` | The test suite already asserts form submission is offloaded and the Actions menu opens immediately while background verification runs. | These are the natural anchors for the "future synchronous menu-opens fail fast" regression guard. |
| `worktree-manager/tests/production_picker/test_pivot_streaming.py:148-181` and `worktree-manager/src/worktree_manager/picker_app.py:430-486` | Existing generation-guard prior art. | Phase 3c should reuse this repo's established stale-result contract rather than invent a new one. |

## Target end-state

### 1. All four current `setup()` entrypoints become non-blocking

The four render-thread callers listed above stop doing real I/O inline. Each
one should return immediately after:

1. bumping the setup epoch,
2. publishing the lightweight "loading/skeleton" state that is safe to render
   immediately,
3. scheduling the real setup/reload work in the background.

There is no fifth ad hoc path. Initial mount, manual reload, and both
post-action rescans all go through one shared setup-reload launcher so the
generation rules are identical everywhere.

### 2. One epoch-guarded background primitive owns stale-result rejection

Phase 3c needs a dedicated primitive for the setup/reload family, separate from
plain `_run_bg`. Its contract should be:

- **Monotonic epoch:** the screen owns a counter (for example
  `_setup_epoch: int`) incremented before every setup/reload/rescan request.
- **Captured ownership:** every scheduled background run captures the epoch it
  belongs to and carries it all the way to apply time.
- **Apply only if still current:** UI mutation happens only when the captured
  epoch still equals the screen's current epoch **and** the screen has not
  already torn down (`_bg_cancel` / equivalent mounted-state guard).
- **Superseded results are discarded, not merged:** a stale worker may finish,
  log at debug level, and exit; it may not partially apply pivot data, list
  data, or metadata onto a newer screen state.
- **Atomic payload:** the background worker gathers one coherent payload
  containing the real pivot scan plus the real setup/data-load result, and the
  apply step installs that payload as one unit. Phase 3c must not create a
  mixed-generation state where pivots come from epoch N and rows from epoch
  N+1 (or the reverse).
- **No new ownership for plugin logic:** the primitive schedules work and
  guards application; it does not redefine how pivots are scanned or how the
  source gathers rows.

This is exactly the same stale-write problem `RegisteredPivotRuntime.invalidate`
and `picker_app.py` already solved; Phase 3c simply needs the setup/reload path
to adopt that same contract.

### 3. "Every I/O-touching Picker surface is consistently non-blocking" has a precise meaning

For this slice, that statement means:

- **Built-in pivot/load path:** the current `setup()` family no longer blocks
  mount, reload, or action-completion callbacks on `scan_pivot_registry()` or
  `src.load()`.
- **Contributed pivot loads:** remain on their existing background runtime with
  generation guards intact; Phase 3c does not regress them.
- **Menu opens:** continue to render from already-available state first and
  refine asynchronously if truth needs re-verification.
- **Progress/reporting actions:** continue to stream or spin without freezing
  the UI; Phase 3c does not move them backward to synchronous work.

### 4. Regression guard: test the UI-thread boundary, do not trust code review alone

The right guard here is a test-first boundary, not a lint rule. Python already
has enough legitimate background threading and helper indirection in this code
that an AST-level lint would be noisy and easy to route around.

The regression guard should instead make these failures impossible to miss:

- **Setup/reload guard:** gate `_scan_pivot_payload()` and `src.load()` behind
  `threading.Event`s in tests, then assert that initial mount / `r` reload /
  post-action rescan still return control to the test app immediately and paint
  a loading/skeleton state before the gates are released.
- **Menu-open guard:** keep explicit tests that `_open_submenu()` and the other
  menu-openers can mount their modal/screen before any blocking verification or
  subprocess completes.
- **Stale-result guard:** force two back-to-back setup requests and prove the
  older result is discarded even if it completes last.

If a future edit reintroduces a synchronous menu-open or a synchronous
setup-rescan, these tests should fail deterministically.

## Ordered implementation steps

Each step lands as its own PR. The sequence stays additive first, then performs
one crisp cutover from synchronous `setup()` callers to the epoch-guarded path,
then tightens regression coverage and cleanup.

[x] **Step 1 — add the epoch-guarded setup-reload primitive and reusable
      async-test helper, unused.** Landed in
      [#3903](https://github.com/ThomasMichon/copilot-extensions/pull/3903):
      added the screen-owned `_setup_epoch` / `_setup_applied_epoch` /
      `_setup_failed_epoch` counters plus `_start_setup_reload_worker()` as an
      additive, still-unused epoch-guarded launcher; extracted synchronous
      `setup()` into `_collect_setup_payload()` + `_apply_setup_payload()`
      seams with `_prime_setup_reload()` / `_invalidate_setup_reload_caches()`
      split around them; added the reusable
      `wait_for_current_setup_epoch_applied` test helper in
      `tests/production_picker/conftest.py`; and added
      `test_setup_reload_epoch.py` covering supersession, stale-failure,
      teardown-drop, and atomic-apply contract behavior. The four production
      callers remain unchanged in this PR (`engine_loading.on_mount`,
      `engine_input`'s `r` handler, `_run_config_section()`'s `_done`, and
      `_run_wt_action()`'s `_done` still call synchronous `setup()` exactly as
      before). Validation: targeted `test_setup_reload_epoch.py` +
      `test_picker_first_paint.py` green; full `worktree-manager` suite
      matched the known Windows baseline at `1473 passed, 7 skipped, 13 failed`
      (the same 3 unrelated `test_data_ssh_sources.py` failures plus 10
      symlink-privilege failures on this machine).
   - Add the screen-owned setup epoch and a dedicated helper for scheduling a
     setup/reload worker plus applying only-current results.
   - Extract the synchronous `setup()` work into explicit "collect payload" and
     "apply payload" seams without changing any call site yet.
   - Add a reusable test helper for "wait until the current setup epoch has
     applied" so later steps do not each invent their own polling loop.
   - No behavior change: the four callers still use the current synchronous
     path in this PR.

[x] **Step 2 — cut over the initial non-live mount to the new primitive.**
      Landed in
      [#4007](https://github.com/ThomasMichon/copilot-extensions/pull/4007):
      `engine_loading.on_mount()` now paints the existing `_setup_skeleton()`
      placeholder in non-live mode and immediately launches the Step 1
      epoch-guarded `_start_setup_reload_worker()` off-thread instead of
      calling synchronous `setup()`. `_after_first_refresh()` and the live
      `_setup_live_async()` / `_setup_live_pivots()` path stay structurally
      unchanged aside from shared "wait for async mount readiness" helpers in
      capture/TUI tests. `_apply_setup_payload()` now clears the initial
      loading footer/debug state once the async mount result applies, so the
      eventual screen matches the old synchronous mount output instead of
      staying stuck on "loading". Scope discipline was re-verified before
      merge: the only production call-site cut over in this step is
      `engine_loading.on_mount`; `engine_input.py`, `engine_pivot_actions.py`,
      and `engine_worktree_actions.py` still call synchronous `setup()` and
      remain Step 3 work. Validation: targeted
      `test_setup_reload_epoch.py` + `test_picker_first_paint.py` green; full
      `worktree-manager` suite matched the updated Windows baseline at
      `1478 passed, 7 skipped, 13 failed` (unchanged known failures: the same
      3 unrelated provider-source failures plus 10 symlink-privilege failures
      on this machine).
   - Replace `on_mount`'s `self.setup()` call with an immediate skeleton/setup
     launcher that schedules the real payload collection off-thread.
   - Keep the already-good live path (`_setup_skeleton()` /
     `_setup_live_async()`) unchanged except for any shared helper reuse.
   - Prove the cold mount path paints before a blocked `_scan_pivot_payload()` /
     `src.load()` completes.

[x] **Step 3 — cut over the remaining three current callers: manual `r`
      reload and both post-action rescans.** Landed in
      [#4144](https://github.com/ThomasMichon/copilot-extensions/pull/4144):
      replaced the three remaining render-thread `self.setup()` callers in
      `engine_input.py`, `engine_pivot_actions.py`, and
      `engine_worktree_actions.py` with the shared
      `_start_setup_reload_worker()` epoch launcher, so every UI-triggered
      setup/reload path now supersedes older work instead of re-entering the
      synchronous setup hot path inline. Added deterministic Step 3 race
      coverage in `test_setup_reload_epoch.py` for rapid repeated `r`
      reloads plus both rescan-vs-manual-reload orderings for config-section
      completions and contributed worktree actions, asserting only the newer
      epoch ever applies. Validation: targeted
      `test_setup_reload_epoch.py` green; full `worktree-manager` suite
      matched the Windows baseline at `1493 passed, 7 skipped, 13 failed`
      (unchanged known failures: the same 3 provider-source failures plus 10
      symlink-privilege failures on this machine).
   - Replace direct `self.setup()` calls in `engine_input.py`,
     `engine_pivot_actions.py`, and `engine_worktree_actions.py` with the same
     epoch-guarded launcher introduced in Step 2.
   - At this point, no render-thread caller should invoke the old synchronous
     setup entrypoint directly any more.
   - Add explicit concurrent-rescan tests: rapid `r` twice, and "action
     completion rescan races a manual reload" both deterministically resolve to
     the newer epoch.

[x] **Step 4 — land the regression guards for menu opens and UI-thread
      setup I/O.** Landed in
      [#4164](https://github.com/ThomasMichon/copilot-extensions/pull/4164):
      added explicit Phase 3c UI-thread boundary coverage in
      `tests/production_picker/test_setup_reload_epoch.py` for the three
      remaining setup/reload callbacks (`engine_input.py`'s `r` handler,
      `_run_config_section()`'s rescan callback, and `_run_wt_action()`'s
      rescan callback), all with `_collect_setup_payload()` blocked behind a
      gate so the test fails immediately if any callback is changed back to
      synchronous inline setup or another direct-I/O path. Kept Step 2's blocked
      mount test as the fourth call-site guard and explicitly marked the
      existing Actions-menu liveness and steer-submit offload tests as part of
      the same standing Phase 3c non-blocking boundary. Deliberately did **not**
      install a broad default-on autouse fixture for every interactive picker
      test: the suite still contains many intentional direct
      `screen.setup_sync_for_tests()` unit tests
      (`test_picker_first_paint.py`, `test_picker_tui.py`, and this module's
      own synchronous baseline checks), so a suite-wide guard would either
      false-positive or force unrelated tests onto the async path just to
      satisfy the fixture. Validation: targeted boundary tests green; full
      `worktree-manager` suite matched the Windows baseline at `1498 passed,
      7 skipped, 13 failed` (unchanged known failures: the same 3
      provider-source failures plus 10 symlink-privilege failures on this
      machine).
   - Promote the existing offload tests for Actions-menu verification and
     progress/reporting flows into the standing Phase 3c boundary.
   - Add the blocking-gate tests described above for setup/mount/reload.
   - Deliberately keep the guard as explicit tests rather than a default-on
     autouse fixture: the current interactive-picker corpus still carries many
     intentional direct `screen.setup_sync_for_tests()` / baseline tests
     whose job is to exercise synchronous helper seams, so a suite-wide
     guard would destabilize unrelated coverage instead of protecting just
     the UI-thread boundary.

[x] **Step 5 — cleanup, rename, and document the final shape.** Landed in
     [#4278](https://github.com/ThomasMichon/copilot-extensions/pull/4278):
     renamed the remaining synchronous helper to
     `setup_sync_for_tests()` so production code no longer advertises a
     callable inline `setup()` entrypoint; updated every deliberate
     synchronous test call site and related comments/docstrings to the new
     test-only name; audited the Step 1 helper seams and retained only the
     ones still genuinely shared by `_start_setup_reload_worker()` and the
     synchronous test helper (`_prime_setup_reload()` and
     `_invalidate_setup_reload_caches()` stay because both paths still need
     them, while no temporary production compatibility wrapper remains in
     front of the worker path); and closed the documentation loop in this
     effort. Filed the optional richer-progress follow-on as
     [#4274](https://github.com/ThomasMichon/copilot-extensions/issues/4274)
     instead of leaving the cross-repo proposal implicit. Validation:
     targeted `test_setup_reload_epoch.py`,
     `test_picker_first_paint.py`, and `test_picker_tui.py` all green; the
     full `tests/production_picker/` suite matched the standing Windows
     baseline twice back-to-back at `775 passed, 3 skipped, 3 failed` (the
     same provider-source failures), and the full `worktree-manager` suite
     matched the Step 4 Windows baseline shape at `1500 passed, 7 skipped,
     13 failed` (the same 3 provider-source failures plus 10 symlink-
     privilege failures).

## Validation

### Contract-level validation

- A supersession test proving epoch N's result is discarded when epoch N+1 is
  requested before N applies, even if N finishes last.
- A stale-failure counterpart: a stale worker raising an exception must not
  clobber a newer epoch's already-applied success.
- A teardown test proving a worker finishing after picker exit drops its
  outcome exactly the way `_run_bg` already drops post-unmount action results.
- An atomic-apply test proving pivot payload and row payload from different
  epochs cannot be mixed into one visible screen state.

### Step-specific validation

1. **Step 1**
   - New unit tests for the setup-epoch primitive only.
   - No behavior-change regressions in the existing picker test suites.

2. **Step 2**
   - Initial non-live mount paints before a deliberately blocked
     `_scan_pivot_payload()` / `src.load()` returns.
   - The eventual applied state still matches the pre-cutover mount result once
     the background worker is allowed to finish.

3. **Step 3**
   - Rapid repeated `r` presses do not freeze, crash, or briefly show stale
     pivots/rows.
   - A config-section completion rescan racing a manual reload resolves to the
     newer epoch's data.
   - A contributed worktree action completion rescan racing a manual reload
     resolves to the newer epoch's data.

4. **Step 4**
   - Existing menu-open tests remain green:
     `test_actions_menu_liveness_verify_is_offloaded` and the form-submit
     offload tests continue to prove menu/action responsiveness.
   - The new setup/reload boundary tests fail if a scratch edit reintroduces
     direct blocking I/O on the UI thread.

5. **Step 5**
   - Full `worktree-manager/tests/production_picker/` suite green at least
     twice back-to-back, because the reverted prototype's race only surfaced in
     broader suite execution, not in a narrow `-k` subset.
   - `git diff --check` clean and no doc-link drift after the rename/final
     cleanup.

### End-to-end scenarios that must pass before the final cleanup PR lands

- **Cold picker open on a plugin-rich machine:** the first frame paints before
  the real pivot scan and row load complete, then settles to the same final
  state without freezing the operator's first keypress.
- **Manual reload storm:** hammer `r` multiple times; only the last reload's
  result applies and no stale flash/crash occurs.
- **Action-completion race:** start a contributed action whose `_done` path
  schedules a rescan, then trigger a manual reload before the first rescan
  lands; the final screen reflects only the newest epoch.
- **Menu-open immediately after launch/reload:** the Actions menu and other
  modal opens still appear immediately while truth-verification or subsequent
  background work continues off-thread.
- **Progress actions remain live:** a D4 progress-reporting action continues to
  update `ProgressScreen` while the rest of the picker remains responsive.

## Cross-repo proposal

Phase 3c itself does **not** require `agent-worktrees` changes to achieve the
non-blocking objective. The optional follow-on is about **richer progress
detail**, not correctness:

- built-in lifecycle verbs such as Stop / Reclaim / Repair / Restore / Sync /
  Finalize are already non-blocking here because they run through `_run_bg`
  and never freeze the UI;
- unlike contributed D4 actions, those verbs do not currently emit an NDJSON
  progress envelope, so the Picker can only show a spinner/status line rather
  than real percentage/message updates.

The proposal is therefore a separate `agent-worktrees` follow-up: let the
built-in lifecycle verbs optionally emit the same NDJSON progress contract that
`tasks.run_action_stream(...)` already consumes. Worktree Manager can then wire
those verbs into the existing `ProgressScreen` streaming mode incrementally.
This proposal is now tracked in
[#4274](https://github.com/ThomasMichon/copilot-extensions/issues/4274) and
must not block the in-repo setup/reload cutover above.

## Non-Goals of this slice

- **Not a rewrite of the live/multi-machine loader.** The live path already
  paints skeleton-first and loads off-thread; Phase 3c is about bringing the
  remaining setup/reload family up to that standard.
- **Not Phase 3d's engine-boundary retirement.** The `data_local.py`
  read-reconcile-write hot path and the broader `_engine_runtime.py`
  retirement stay in Phase 3d's plan; Phase 3c only gives that future work a
  safe non-blocking setup/reload seam to land against.
- **Not a pivot-manifest contract change.** This slice changes how the Picker
  schedules and applies scans, not what a contributed pivot declares.
- **Not a new session-host or menu UX design.** The existing Actions menu,
  progress screen, and modal structure stay conceptually the same; only their
  blocking behavior is being constrained and guarded.
- **Not an assumption that built-in verbs already have progress percentages.**
  The cross-repo proposal above is additive and optional; spinner-only built-in
  progress remains acceptable until the engine grows the envelope.
