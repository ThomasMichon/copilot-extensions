# Phase 3/6 — Reconcile the diverged Picker implementations, then retire the bundled one

- **Parent effort:** [`README.md`](README.md) § Phase 3 (parity) / § Phase 6 (retirement)
- **Tracks:** [#352](https://github.com/ThomasMichon/copilot-extensions/issues/352) (coordination token,
  slice claimed here) · aperture-labs #6764 (duplicate-implementation architecture decision,
  **recorded 2026-09-12**: Worktree Manager is the canonical owner of the Picker/Mux UI;
  `agent-worktrees` keeps worktree lifecycle only) · aperture-labs #6914 (two-way hook-contract
  follow-up, out of scope here) · [#117](https://github.com/ThomasMichon/copilot-extensions/issues/117)
  (the smaller NF5-5 opt-out-toggle cleanup this supersedes)
- **Governing vision:** [`visions/picker`](../../../visions/picker/README.md) §Behaviors/
  `programmatic-parity`, `renderable-and-assertable-headless`; the already-recorded Phase 6
  end-state in the parent README.
- **Origin:** the `agent-worktrees` plugin's bundled Picker (`plugins/agent-worktrees/src/
  agent_worktrees/picker_tui/`) was transplanted into Worktree Manager's `production_picker/`
  in **#1244**, carrying forward the full NF1–NF5-5 native-focus migration (native modals,
  focus bridge, native `OptionList` data body with sticky headers + clickable checkboxes —
  see `plugins/agent-worktrees/docs/architecture.md`'s NF sections for the original design
  record). Since that transplant, **both copies have kept receiving independent commits** —
  exactly the failure mode aperture-labs #6764 was filed against (that issue's own example:
  an env-sanitization fix landed only on the Worktree Manager side via #2359/#2384, later
  parity-restored by #2391). The decision on #6764 makes Worktree Manager canonical, but
  does not by itself guarantee every agent-worktrees-only fix actually made it across before
  the bundled Picker is deleted.
- **Status:** Completed (2026-09-15/16) — the fresh audit closed Step 0, the required
  extraction closed Step 1.5, the bundled Picker deletion in Step 2 landed safely, and
  Step 3 validation passed.

## Step 0 — Audit: what is old-only / new-only since the transplant

### Step 0 result (closed 2026-09-16)

Re-ran the per-file `engine.py` commit diff (bounded to commits since the transplant date,
2026-08-27, to avoid false positives from pre-transplant shared history that `git log
--follow` doesn't link across the copy). Conclusion: **no porting action needed** —
every historical "AW-only" commit resolves to one of:

- **Already synced, just relocated:** `cc43049d4` (#2453/#2499, SteerButtonRow near-miss
  click fallback) — Worktree Manager's steer buttons moved into a sibling `steering.py`
  module during `39e9b8cc0`'s (#2586) Confirm/Save/Reset rewrite; the identical near-miss
  fallback logic (same comment, same algorithm) is already present there
  (`picker_tui/steering.py::SteerButtonRow.on_click`). Not a gap.
- **Architecturally inapplicable:** `906d735f5` (#2589, crash + None env label when local
  identity is uncached) — the bug is specific to `agent-worktrees`' lazy/cached
  `_src_local()` accessor, which deliberately returns `(None, None)` until populated.
  Worktree Manager's equivalent (`self.src.LOCAL`, from `data_local.py`'s
  `LOCAL = _local_identity()`) resolves **eagerly at import time** and is never `None` —
  the crash this fix guards against cannot occur on that side. No port needed.
- **Reverse-direction, already present:** `5bd8bd149` (#2590, "port agent-worktrees picker
  card draft semantics") despite the title, is itself a port **from** Worktree Manager
  **into** `agent-worktrees` (`_run_pivot_form_draft_save`/`_run_pivot_form_draft_clear`/
  `SubmitErrorScreen` all pre-date this commit on the Worktree Manager side). Nothing to
  port back.
- **Already explicitly deferred by the #6764 operator decision:** the four perf/threading
  commits (`19de6e569` #1511, `034621379` #1562, `9d07a8a38` #1589, `4b759a20a` #1938) —
  per that issue's own resolution comment, these are "architectural, not drop-in" and
  intentionally left unported.
- **`agent-worktrees`-plugin-infra-only, not a Picker parity concern:** `9802f826d` (#1412,
  retire the extension-reload warning + deprecate Bare Resume) — this retires
  `agent-worktrees`' own legacy plugin-reload/install mechanics, which Worktree Manager
  (a standalone app, not a marketplace plugin) never had an equivalent of.

Worktree-Manager-only `engine.py` commits (`7c7663d4b` AHP launch option, `8239053fb`
stale-head-session guard, `69441cfb7` version-seam bookkeeping, `8bf03c11b` pivot-worker
cancellation) are all Worktree-Manager-specific or infra bookkeeping with no
`agent-worktrees` equivalent needed (AHP is explicitly WM-only territory per Phase 3b).

**Tests:** `worktree-manager`'s own `tests/production_picker` suite is fully green (486
tests, 2 skipped, 0 failed — a Windows-only `pytest-current` symlink-cleanup
`PermissionError` at teardown is a known local-tmp-cleanup artifact, not a test failure).
`agent-worktrees`' full suite via `tools/run-plugin-tests.py agent-worktrees` is
375 tests, 373 passed / 2 failed — the 2 failures (`tests/test_ahp_command.py::
test_direct_backend_refuses_active_hosted_binding`, `::test_ensure_rejects_finalizing_
worktree`) are in the unrelated AHP session-backend CLI, not `picker_tui/`, and are
pre-existing on a clean checkout (reproduced with zero local changes). Tracked
separately, not a blocker for this effort.

## Step 1.5 — Extract-before-delete (found and closed 2026-09-16)

`picker_tui/` is not a pure TUI package: `agent_worktrees/__main__.py` and
`agent_worktrees/reciprocal_presentation.py` (both used by non-TUI, non-optional CLI
commands) reach into several of its submodules for logic that has nothing to do with
rendering. Cataloged by grepping every `from .picker_tui...` / `from . import
picker_tui` site in `__main__.py` and classifying by the calling command:

**Watch for this on every extraction:** `worktree-manager/tests/
test_production_picker_transplant.py::test_transplanted_picker_sources_match_production_
copy` asserts most `picker_tui/*.py` files stay **byte-identical** between the two trees
(only `data_local.py`/`data_ssh.py`/`engine.py`/`maintenance.py`/`pivots.py` were
previously allowed to diverge). Any file you edit on the `agent-worktrees` side as part
of an extraction needs adding to that exclusion set (with a comment explaining why), or
this test fails in CI even though the local per-plugin suites look clean -- this bit the
`reciprocal.py`/`frame_health.py` extraction below (derive.py, `__init__.py`, and
`frame_health.py` itself all needed adding).

**Load-bearing outside the TUI — must be extracted to a new home (e.g. a top-level
`agent_worktrees/picker_shared/` package, or merged into existing non-TUI modules) before
`picker_tui/` can be deleted:**

- ~~`picker_tui/reciprocal.py`~~ — **done (2026-09-16):** relocated to
  `agent_worktrees/reciprocal_state.py` (top-level, non-TUI); `reciprocal_presentation.py`
  and `picker_tui/derive.py` (the TUI's own compact label use) both now import from
  there. Full `agent-worktrees` suite green (373/375, same 2 pre-existing unrelated
  failures as before); `ruff check --select F,E9` clean.
- ~~`picker_tui/frame_health.py::append_launch_event`~~ — **done (2026-09-16):**
  relocated `append_launch_event` (+ its `_timestamp`/`_launch_trace_path` helpers) to
  `agent_worktrees/launch_trace.py`. `frame_health.py` keeps `FrameHealthReporter`
  (confirmed TUI-render-loop-only, no external call sites) and imports the two helpers
  back from `launch_trace` for its own use. Updated `cmd_resolve`, `_run_picker_housekeeping`,
  and `picker_tui/__init__.py`'s launch call. Also fixed `worktree-manager/tests/
  test_production_picker_transplant.py`'s byte-identical-copy guard (excluded
  `frame_health.py`/`__init__.py`/`derive.py` from the comparison, alongside the
  pre-existing `data_local.py`/`data_ssh.py`/`engine.py`/`maintenance.py`/`pivots.py`
  set) -- this test asserts most `picker_tui/` submodules stay byte-identical between
  the two trees, which the Step 0 audit didn't account for and which the CI run on
  PR #2753 caught. Also found and fixed a genuinely pre-existing, unrelated bug while
  running the full suite: `agent_worktrees.__main__` didn't re-export
  `_infer_worktree_id_from_worktree_root` from `worktree_identity.py` the way its
  siblings are re-exported, breaking `test_context_resolution.py`'s direct unit test
  of it (confirmed pre-existing via `git stash`). All directly-touched test files
  green; `worktree-manager`'s full `production_picker` suite green (515 passed, 2
  skipped); `ruff check --select F,E9` clean on both sides.
- ~~`picker_tui/roster.py`~~ — **done (2026-09-16):** relocated to `agent_worktrees/roster.py`
  (top-level, non-TUI); `local_host()` is called from the standalone, SSH-able
  `cmd_profiles` CLI command in `__main__.py`, not just the Picker's Profiles view.
  `picker_tui/data_local.py`/`data_ssh.py` (already Manager-owned adapters, free to
  diverge) and `picker_tui/profiles_io.py` (newly added to the transplant byte-parity
  exclusion set) still import it for the TUI's own Profiles view. `engine.py` never
  imported it directly (only docstring mentions, fixed for accuracy) despite being the
  Profiles view's renderer. All directly-touched tests green (74/74); `worktree-manager`'s
  full `production_picker` suite green (515 passed, 2 skipped) and the transplant test
  green (45/45) after the `profiles_io.py` exclusion; `ruff check --select F,E9` clean.

**Not yet started:**

- `picker_tui/data_local.py::_stamp_from_raw` / `_overlay_cached_state` — called from
  `cmd_status_monitor` (the resident background status-JSON-stream daemon) and
  `cmd_list --classify`, to warm/read a "session-render cache" keyed for the picker's
  first-paint (dotfiles#948) but written from general status/list streaming, not the TUI.
  Note: `data_local.py` itself is already in the transplant byte-parity exclusion set (a
  Manager-owned adapter, per Step 0), so extracting just these two helpers doesn't need a
  new exclusion entry -- but check whether Worktree Manager's own `data_local.py` needs
  the same cache-stamping (it may not, if WM has no equivalent status-monitor/list
  streaming command).
- `picker_tui/pivots.py::scan_pivot_registry` / `prune_stale_entries` — called from
  `cmd_doctor` (general health-check/prune command) for pivot-registry maintenance,
  unrelated to rendering. `pivots.py` is also already excluded from the transplant
  byte-parity check (Manager-owned adapter).

**Confirmed TUI-only — safe to delete with the rest of the package:**

- `picker_tui/engine.py`, `steering*.py`, `selection.py`, `maintenance.py`,
  `provider_sources.py`, `prewarm.py`, `tasks.py`, `derive.py`,
  `obscure.py`, `source_identity.py` (pending a final pass — this list has not been
  independently re-verified module-by-module the way `reciprocal.py`/`frame_health.py`/
  `roster.py`/`data_local.py`/`pivots.py` were; treat as provisional). `profiles_io.py`
  was previously listed here in error -- it imports `roster` and is TUI-Profiles-view-only
  itself, but now needs the transplant-test exclusion since its import line changed.
- `_run_new_picker` and `cmd_picker` in `__main__.py` are the bundled Picker's own launch
  surface — these are retired *with* the package (per Step 2.2's bare-invocation seam
  check), not extracted.
- `picker_tui/data_ssh.py`, `picker_tui/capture.py` — used only from within `cmd_picker`'s
  `screenshot`/mock branches in the audit so far; re-check for any non-`cmd_picker` call
  site before deleting (not found in this pass, but the `data_local.py` false-negative
  above — a module used by non-`cmd_picker` code — means "not found yet" isn't the same
  as "confirmed absent").

This list is now closed: the remaining non-TUI pieces were relocated into retained
`agent_worktrees` modules, and the bundled `picker_tui/` package was then deleted in
Step 2 without leaving live non-TUI dependencies behind.

*(Original per-file audit method retained below for re-runs; superseded in substance by
the "Step 0 result" above.)* Compare `plugins/agent-worktrees/src/agent_worktrees/
picker_tui/engine.py` against `worktree-manager/src/worktree_manager/production_picker/
picker_tui/engine.py` (and the sibling files in each `picker_tui/` directory — bound the
`git log` comparison to commits *since the transplant date* (2026-08-27), not just
`--follow`-style path history, since the transplant was a copy, not a rename, and
pre-transplant history isn't linked across the two paths).

## Step 1 — Close the remaining Phase 3 parity checklist item

Per the parent README, Phase 3's only unchecked box is:

> Bring the Manager Picker to feature parity with the bundled Picker: full worktree list
> interaction (filter · sort · select), resume/join/create actions, multi-machine
> at-a-glance, session/PR status columns.

**Provisionally confirmed (2026-09-16):** the NF5 transplant (#1244) carried the native
`filter · sort · select` list interaction wholesale, and Worktree Manager's Picker has
since gained its own resume/create actions, an SSH multi-machine data source
(`data_ssh.py`), and session/PR status columns (shared `engine.py` lineage with
`agent-worktrees`', which has these). Not yet checked off `[x]` in the parent
README — do a final end-to-end manual soak + confirm the Worktree Manager Picker's own
golden/capture tests cover each named behavior before flipping that box, since "the code
exists" isn't the same as "verified true end-to-end" per this step's original bar.

## Step 2 — Execute Phase 6: delete the bundled Picker

**Completed 2026-09-16.** Once Step 1.5 extracted the remaining non-TUI surfaces, this
slice deleted the bundled `picker_tui/` package and left `agent-worktrees` using the
Worktree Manager seam or the install trigger instead of any fallback to dead code:

1. Delete `plugins/agent-worktrees/src/agent_worktrees/picker_tui/` (the whole package) and
   its test tree (`plugins/agent-worktrees/tests/test_picker_tui.py`,
   `test_picker_capture.py`, `tests/goldens/picker/`, `scripts/picker-snapshot/` if it isn't
   also used by anything else).
2. Confirm the Phase 4 bare-invocation seam's documented behavior actually holds with the
   package absent: no Manager on `PATH` → install trigger (not a crash, not a silent
   fallback to dead code). Add/keep a regression test asserting this.
3. Remove now-dead config surface: the `AGENT_WORKTREES_PICKER_NATIVE_LIST` env var and any
   other picker-only toggles/docs in `plugins/agent-worktrees/docs/` (`architecture.md`'s NF
   sections become historical record — do not delete the doc, but add a closing note that
   the code it describes has moved to `worktree-manager/` and was later deleted from here).
4. Update `docs/tools.md` / `external-repos.yaml` / any facility docs (in `aperture-labs`,
   cross-repo) that still describe launching a Picker via `agent-worktrees` directly.
5. Close [#117](https://github.com/ThomasMichon/copilot-extensions/issues/117) — its
   opt-out-toggle scope is now moot; the whole module is gone, not just its toggle.

## Step 3 — Validate

- Full `agent-worktrees` plugin suite green with the package gone (no stale imports).
- Full `worktree-manager` plugin suite green (its own picker_tui tests + the ported fixes
  from Step 0).
- `ruff check --select F,E9` clean on both.
- `python tools/check-install-contract.py` (agent-worktrees side) still reports the correct
  plugin count with `picker_tui` gone.
- A live bare-invocation smoke test on a machine with no Worktree Manager installed:
  confirm the install-trigger path, not a crash.

## Notes for whoever executes this

- This repo now uses **`pr-self-merge`**, not direct push (confirmed 2026-09-15 via
  `copilot-extensions get pr-profile`) — land each step as its own reviewed, self-merged PR,
  not a direct push to `main`.
- Re-check aperture-labs #6764/#6914 for any updates before starting — the hook-contract
  design in #6914 may have implications for exactly when it's safe to delete the bundled
  Picker (e.g. if anything still depends on the old binstub-seam behavior during a
  transition window).
- Per the parent effort's Coordination section: **re-confirm no one else has claimed this
  slice on #352 before starting**, and post progress there as you go.
- **2026-09-16 update:** The cross-port audit is closed, the extract-before-delete list
  was worked to completion, and the bundled `picker_tui/` package has now been removed.
  Keep the audit method above as the repeatable playbook for future copy-then-retire
  migrations: grep callers first, extract any non-UI survivors, then delete the UI tree.
