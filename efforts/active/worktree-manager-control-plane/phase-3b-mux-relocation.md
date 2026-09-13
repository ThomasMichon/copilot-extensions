# Phase 3b, Slice 2 — Relocate Mux launch/reattach mechanics out of agent-worktrees

- **Parent effort:** [`README.md`](README.md) § Phase 3b
- **Tracks:** [#2062](https://github.com/ThomasMichon/copilot-extensions/issues/2062)
- **Governing vision:** [`visions/session-hosting`](../../../visions/session-hosting/README.md)
  Concepts/*Session-host provider*; the matching Non-Goal in
  [`visions/plugins/agent-worktrees`](../../../visions/plugins/agent-worktrees/README.md);
  the already-published [`visions/installer`](../../../visions/installer/README.md)
  §Features/`optional-worktree-agent-control-plane` ("muxing is a capability
  this app provides ... the plugins detect it and use it when present, and run
  **non-muxed** when it is absent").
- **Depends on:** [`phase-3b-ahp-relocation.md`](phase-3b-ahp-relocation.md)
  (Slice 1) proving the pattern of a relocated provider calling agent-worktrees
  only through its `--json` subprocess CLI. This slice does **not** wait for
  Slice 1's every step, but reuses its generic `execution-leg` verbs once they
  exist (see below).
- **Operator directive (2026-09-05):** the Mux relocation is a **clean,
  decisive cutover** — one canonical implementation, not an indefinitely
  maintained pair. The agent-worktrees implementation is proven and correct;
  it is migrated **verbatim**, not reconciled with, merged into, or replaced
  by Worktree Manager's own earlier, fledgling Picker-launch prototype (the
  Picker-slice-1/2/3 code predating the production-Picker transplant, PRs
  #494/#504/#505 — now retired scaffolding, not a competing implementation).
  Every step of this migration is validated against the existing Textual
  Picker golden/screenshot regression suite
  (`worktree-manager/tests/production_picker/test_picker_capture.py` and
  siblings) and clean-room scenarios, so "clean cutover" means *proven*, not
  merely *fast*.
- **Status:** In progress — Sub-slice 2a Step 1 landed (script files copied
  verbatim; deployment mechanism proven). Sub-slice 2a Step 2's
  `cmd_launch` repoint + direct non-mux fallback is now implemented; the
  old in-plugin scripts are intentionally still deployed as a temporary
  rollback until the relocated path is proven live on real hardware.
  Sub-slice 2b's design was revised (2026-09-12, see below) after discovering
  the actual coupling depth; implementation has not started.

## Why this is a different shape of problem than AHP

AHP is one ~350-line Python module plus a narrow persisted-record shape. Mux
is not:

| File | Size | Role |
|---|---|---|
| `plugins/agent-worktrees/bin/launch-session.ps1` | 1,844 lines | Windows launcher: resolves a JSON launch plan from `agent-worktrees resolve`, executes it natively (spawns/attaches PSMux, profile selection, Windows Terminal fragment handling, seed injection, handoff-cutover argv, AHP token handoff), calls `agent-worktrees post-exit` for finalization on exit. |
| `plugins/agent-worktrees/bin/launch-session.sh` | 1,203 lines | POSIX equivalent (TMux). Same `resolve` → execute → `post-exit` shape. |
| `plugins/agent-worktrees/bin/launch-session.cmd` | 13 lines | Thin `cmd.exe` shim, used only for `--stdio`/`--acp` (stdin-forwarding requirement); interactive launches skip straight to the `.ps1`. |
| `plugins/agent-worktrees/bin/pane-wrapper.{sh,ps1}` | 175 / 311 lines | Wraps the actual tmux/psmux pane command: graceful exit-code handling, `--aw-ahp-token-file` handoff (confirms Mux already wraps AHP sessions today), initial-prompt injection receipt. |
| `__main__.py` `cmd_launch` | ~80 lines | Resolves the installed launcher script path and execs into it, forwarding `--no-update`/`--no-mux`/`--verbose` as env vars. **Already an external-process boundary**, not an in-process import. |
| `__main__.py` `cmd_remux` / `_perform_remux` | ~120 lines | The `remux` verb: POSIX adopts a bare live process into tmux via `reptyr`; Windows previews then (`--yes`) applies a reclaim-before-resume plan. Calls `reclaim_one`. |
| `__main__.py` `reclaim_one` | ~40 lines | Picker's "Reclaim" action and the Windows remux apply step share this: resolves bound Copilot process(es) via `reclaim.resolve_bound_copilots` and terminates them. |
| `agent_worktrees/reclaim.py` | whole module | Process-table introspection: builds a process table, classifies homing (`bare`/`mux`/`unknown`), resolves which processes are "bound" to a worktree. **Generic observation tooling**, reused by Reclaim, remux, and orphan-detection — not launch mechanics. |
| `agent_worktrees/sessions.py` | `has_mux_session`, `has_mux_session_named`, `LiveVerdict`, `verify_worktree_active` | **Live liveness observation**, queried fresh from the mux control socket each time — there is no persisted "mux binding" record analogous to `SessionBackendBinding`. This is the aggregate-status reducer's input and legitimately belongs to agent-worktrees per the vision's *provider-observation-ingestion* feature. |

Two things follow from this inventory:

1. **Mux liveness is observed, not persisted.** Unlike AHP, there is no
   `session_backend`-shaped binding to migrate to the generic `execution_leg`
   record for Mux itself — `has_mux_session` asks the live mux server every
   time. So Slice 2 does not need an `execution_leg` write path the way
   Slice 1 does; it only needs one if/when a relocated Mux provider wants to
   record *which* worktree a pane belongs to in a way agent-worktrees should
   observe generically (see Non-Goals — not attempted in this slice).
2. **The launcher scripts already call agent-worktrees only through its CLI**
   (`resolve`, `post-exit`) — an external-process boundary, not an in-process
   import. Relocating the *files* is therefore a "move + repoint installed-
   path resolution" problem for `cmd_launch`, not a deep Python untangling.
   The size risk is in the **scripts' own internal complexity** (profile
   selection, Windows Terminal fragments, handoff-cutover argv, AHP token
   handoff), not in their coupling to agent-worktrees.
3. **The deployment mechanism already exists, unmodified.** Worktree
   Manager's `self_install._copy_payload` copies the *whole* payload
   directory (`shutil.copytree`) into each versioned slot, and the bootstrap's
   `git clone` fetches the whole repo before `cd`-ing into `worktree-manager/`.
   A `worktree-manager/bin/` sibling directory therefore deploys automatically
   through both the self-update and the fresh-bootstrap path with **zero**
   packaging code changes — proven by
   `test_bin_directory_is_deployed_into_the_slot` (Sub-slice 2a Step 1).

## What "clean cutover" means here

Reconciling the operator's directive with the two already-published vision
invariants that constrain this migration:

- [`session-hosting`](../../../visions/session-hosting/README.md) requires
  agent-worktrees to remain fully functional with **zero** session-host
  providers present (§Non-Goals: "not a requirement for a terminal or
  multiplexer").
- [`installer`](../../../visions/installer/README.md) already states the
  resolution: *"a terminal multiplexer is a heavy, invasive dependency ...
  muxing is a capability this app provides (the plugins detect it and use it
  when present, and run **non-muxed** when it is absent) rather than
  something the lightweight plugins carry."*

So the clean cutover is: **exactly one implementation of muxed launch**
(migrated verbatim into Worktree Manager, canonical, no parallel
reimplementation preserved from Worktree Manager's own earlier prototype),
and a **much smaller, genuinely different** fallback in agent-worktrees for
when Worktree Manager is absent — a **direct, non-muxed Copilot invocation**,
not a second copy of the mux launcher scripts. This is decisively simpler
than the "keep a legacy fallback launcher until a deprecation window closes"
model this document originally proposed, and removes any period where two
mux implementations exist side by side:

| Worktree Manager present? | `cmd_launch` behavior |
|---|---|
| Yes (usable, version-probed) | Exec into `<worktree-manager slot>/bin/launch-session.*` — the one true mux implementation. |
| No | Exec Copilot **directly, non-muxed** — a small, new code path in agent-worktrees, not the retained giant script. Degraded (no mux, no reattach, no remux), but functional, matching the already-published graceful-degradation invariant. |

The old in-plugin `launch-session.{sh,ps1,cmd}`/`pane-wrapper.{sh,ps1}` are
**deleted from agent-worktrees in the same cutover PR** that adds the direct
non-mux fallback and repoints resolution — there is no dual-maintenance
window for the mux implementation itself.

## Recommendation: still two sub-slices, but a decisive cutover in each

Given the size (3,500+ lines across three platforms) and that `cmd_remux`
mixes generic liveness detection (which should stay) with a launch action
(which should move), this document proposes **two further sub-slices**. Each
sub-slice's *own* cutover is clean (no lingering duplicate implementation);
splitting into two sub-slices is about bounding review/test size per PR, not
about leaving Mux itself half-migrated.

### Sub-slice 2a — Move launcher scripts; repoint resolution; add the non-mux fallback

1. **Step 1 (done):** copy `launch-session.{sh,ps1,cmd}` and
   `pane-wrapper.{sh,ps1}` verbatim into `worktree-manager/bin/`. Zero logic
   changes. Proved deployable via the existing `_copy_payload` mechanism
   (no packaging code change needed) with a new self-install test.
2. **Step 2 (repoint + direct fallback portion done; deletion deferred by an
   explicit deviation):**
   - [x] Changed `agent-worktrees`'s `cmd_launch` to resolve
     `<worktree-manager install dir>/bin/launch-session.*` via
     `WORKTREE_MANAGER_ROOT` + `current-version`, reusing the same
     `--version` health-probe pattern as the bare-invocation Manager seam.
   - [x] Added the **direct, non-mux fallback**: when no usable Worktree
     Manager / relocated launcher is found, `cmd_launch` now resolves the
     normal launch plan and runs Copilot directly in the target worktree, then
     calls `agent-worktrees post-exit` after the child exits.
   - [ ] **Delete** `plugins/agent-worktrees/bin/launch-session.{sh,ps1,cmd}`
     and `pane-wrapper.{sh,ps1}` from agent-worktrees. **Temporary
     deviation:** keep them deployed as the explicit rollback path until live
     proof shows the relocated Worktree Manager launcher preserves mux,
     post-exit, and activity journaling on real hardware. The prior regression
     that silently degraded interactive launches makes "prove first, delete
     second" the safer sequencing here.
   - [x] The scripts' own calls into `agent-worktrees resolve` /
     `agent-worktrees post-exit` are unchanged in contract — still the same CLI
     subprocess boundary, now invoked from a different installed location.
   - [x] **Closed the actual live regression this slice exists to fix:**
     Worktree Manager's OWN transplanted production Picker (`_run_launch` in
     `worktree_manager/__main__.py`) previously routed every local, non-AHP
     launch through `launcher.compose_launch()`/`execute()` -- whose
     `MuxCapability` has **never** been wired to a real backend (`_capability`
     stays `_NO_MUX` unless something calls `set_mux_capability()`, which
     nothing does). Once the Manager's own `worktree-manager` binstub is on
     `PATH`, the bare-invocation seam hands the WHOLE interactive session to
     that Picker, bypassing `cmd_launch`/`launch-session.ps1` entirely --  so
     the `cmd_launch` repoint above, while correct and necessary for
     agent-worktrees' own standalone/fallback path, does **not** by itself
     restore muxed launches once Worktree Manager is installed. `_run_launch`
     now delegates the whole local, non-AHP launch to the SAME relocated
     `<own-install>/bin/launch-session.*` script (verbatim reuse, per the
     operator directive above) instead of `launcher.launch()`: it passes the
     **already-resolved** `plan.worktree_id` (never re-issuing `--new`/`--base`,
     which would create a second worktree), threads `--bare-resume` and
     `WORKTREE_NO_MUX` exactly as `cmd_launch` does, and lets the script's own
     resolve/launch/attach/post-exit take over. AHP-attached launches are
     deliberately excluded (unchanged, still `launcher.launch()`) -- AHP
     already rewrites `plan.cmd` to the attach client via `attach_plan()`,
     and routing that through the script would re-resolve a vanilla plan and
     clobber the rewrite; AHP mux-wrapping remains a known, separately-scoped
     follow-up.
3. **Validation gate for the cutover PR (required, not optional):**
   - Full `worktree-manager/tests/production_picker/` suite, especially
     `test_picker_capture.py`'s golden character-grid, ANSI, and SVG
     screenshot assertions — proves the Picker's rendered launch/resume
     affordances are pixel/text-identical before and after.
   - `worktree-manager` and `agent-worktrees` full plugin suites
     (`python tools/run-plugin-tests.py agent-worktrees worktree-manager`).
   - Clean-room `agent-worktrees-solo` (proves agent-worktrees alone —
     i.e. exactly the non-mux fallback path — still round-trips
     register→create→finalize) and `worktree-manager-bootstrap` (proves the
     relocated scripts deploy on a pristine box). A **new combined**
     clean-room scenario exercising an actual interactive mux launch across
     both installed plugins is the strongest possible proof and should be
     authored before or immediately after this cutover lands, tracked as a
     follow-up if not ready in the same PR.

### Sub-slice 2b — Relocate the remux action; keep liveness detection

**Design revised 2026-09-12** after discovering the coupling runs deeper than
originally scoped below: `remux.py`'s POSIX reptyr-adoption action calls
`sessions.mux_session_name` / `build_mux_new_window_argv` /
`build_mux_new_session_argv` -- and those helpers are **not** remux-specific.
They are pervasive, load-bearing utilities used throughout `__main__.py` (list
rendering, session-catalog liveness, the bundled Picker's own native mux
launch when Worktree Manager is absent). Duplicating them into Worktree
Manager would be exactly the "parallel reimplementation" the operator's clean-
cutover directive forbids, and reads on `sessions.py` are exactly the vision's
*provider-observation-ingestion* feature this document already says stays put.
So the split is **not** "detection stays, both actions move as self-contained
logic" as originally written -- it mirrors the **resolve/execute pattern
Sub-slice 2a already established** for the main launch path: agent-worktrees
keeps sole ownership of *planning* (still owns every tmux-naming/argv-building
call), Worktree Manager owns only *executing* what agent-worktrees tells it to.

1. **New agent-worktrees query verb (planning only, no side effects, never
   terminates a process):**
   `agent-worktrees mux-remux-plan --worktree-id <id> [--session-id <id>]
   [--force-sudo | --no-force-sudo] --json`.
   - Refactors `_perform_remux`'s and `remux_bare_copilot`'s **guard/target-
     resolution** logic (already-has-live-mux refusal, ambiguous-owner
     rejection via `reclaim.py` unchanged, none-found) into this query,
     unchanged in substance -- reuses `reclaim.py` and `sessions.py` exactly
     as today.
   - **POSIX (Linux/WSL) result:** the *precise* reptyr pane-adoption `argv`
     the query would have run (built via the SAME
     `sessions.build_mux_new_window_argv`/`build_mux_new_session_argv`/
     `mux_session_name`, unchanged, still living here), the resolved
     `pid`/`session_id`/`session_name`, and whether `sudo` is required
     (`_needs_sudo()`, unchanged) -- but does **not** invoke it.
   - **Windows result:** the exact same preview shape `_perform_remux`
     already computes today (target `pid`/`session_id`, `reason`,
     `requires_resume`) -- but does **not** call `reclaim_one` itself; that
     becomes a separate, explicit step (below).
   - Every existing safety invariant (refuse live mux, refuse ambiguous
     owner, never resolve the calling process's own subtree) lives entirely
     in this query, verbatim.
2. **The action moves to Worktree Manager as a thin executor, not a
   reimplementation:**
   - **POSIX:** calls `mux-remux-plan --json`, runs the returned `argv`
     directly via `subprocess` (mechanical -- no tmux-naming knowledge of its
     own), then polls agent-worktrees' *existing, unchanged* liveness query
     (`has_mux_session` / the homing-observation path) to confirm the
     hand-over, mirroring `remux_bare_copilot`'s current verify-poll loop.
   - **Windows:** calls `mux-remux-plan --json` for the preview, then (on
     apply) calls the **already-existing, unchanged**
     `agent-worktrees reclaim --worktree-id <id> --bare-only --yes --json`
     verb (the same primitive Picker's plain "Reclaim" action already uses)
     to terminate the confirmed-unreachable owner, then relaunches through
     its **own relocated** `<bin>/launch-session.*` script from Sub-slice 2a
     in ordinary resume mode -- the identical path any normal Picker resume
     takes, not a new relaunch mechanic.
   - This means **no new Windows relaunch code** is needed in Worktree
     Manager at all: "reclaim-then-relaunch" is just "call the reclaim verb,
     then do a normal resume" -- both already-existing primitives.
3. `cmd_remux`, `_perform_remux`, and `remux.py`'s **subprocess-executing**
   entry point (`remux_bare_copilot`'s `subprocess.run(argv, ...)` call and its
   verify-poll loop) are **deleted** from agent-worktrees in the same PR that
   adds `mux-remux-plan` and the Worktree Manager consumer. `remux.py`'s
   **pure-computation** helpers (guard/target resolution, `_reptyr_pane_cmd`,
   `_needs_sudo`) are refactored into `mux-remux-plan`'s implementation,
   staying in agent-worktrees (they need `reclaim.py`/`sessions.py`, which
   never move).
4. `reclaim_one` (used by both Picker "Reclaim" and the Windows remux action)
   **stays in agent-worktrees unchanged** -- already true today, reconfirmed:
   it is a generic process-termination operation over `reclaim.py`'s
   process-table observation, invoked identically by both callers.
5. Preserve every existing safety invariant from #1478/#1491 verbatim: refuse
   an existing live mux, refuse an ambiguous owner, never reap the calling
   process's own subtree. These live entirely inside `mux-remux-plan` now, so
   "preserved" means the query's guard logic is moved, not rewritten.
6. Same clean-cutover rule: `cmd_remux`'s relaunch action is deleted from
   agent-worktrees in the same PR that lands Worktree Manager's consumer of
   `mux-remux-plan` -- no parallel remux implementations, and no window where
   two things can both try to adopt/reclaim the same bare process.


## What stays in agent-worktrees (not relocated, ever)

- `sessions.py`'s `has_mux_session`, `has_mux_session_named`, `LiveVerdict`,
  `verify_worktree_active` — live liveness **observation**, feeding the
  aggregate-status reducer. This is the vision's *provider-observation-
  ingestion* feature, not launch mechanics.
- `sessions.py`'s `mux_session_name`, `build_mux_new_window_argv`,
  `build_mux_new_session_argv` — **discovered during Sub-slice 2b design
  (2026-09-12)** to be pervasive planning/naming utilities used throughout
  `__main__.py` well beyond remux (list rendering, session-catalog liveness,
  the bundled Picker's own native mux launch). These stay so agent-worktrees
  remains the single owner of "how to name/build a mux session for worktree
  X"; Worktree Manager only ever *executes* an argv agent-worktrees already
  computed (`mux-remux-plan`), never rebuilds one itself.
- `reclaim.py` in full — process-table introspection and bound-process
  resolution, reused by Reclaim (a non-relaunching action) independent of
  which launcher a worktree uses.
- `reclaim_one` — generic termination, not Mux-specific.
- The `resolve`/`post-exit` CLI verbs — these are the generic "give me a
  launch plan" / "run finalization after the child exits" contract every
  launcher (Mux today, potentially others later) calls into. They are
  already provider-neutral in shape and do not move.

## Ordered implementation steps

1. **(Done)** Copy launcher/wrapper scripts into `worktree-manager/bin/`,
   unchanged; prove the deployment mechanism with a self-install test.
2. **(Done)** Add the health-probed new-location resolution **and** the
   direct non-mux fallback to `cmd_launch`; **defer deleting** the old
   in-plugin scripts until a follow-up live-proof cleanup PR removes the
   temporary rollback. Also closed the concurrent live regression where
   Worktree Manager's own `_run_launch` bypassed the relocated launcher
   entirely (see `README.md` journal, 2026-09-10).
3. Author (or extend) a combined clean-room scenario proving an actual
   interactive mux launch end-to-end across both installed plugins.
4. Implement Sub-slice 2b per the revised design above:
   a. Add `agent-worktrees mux-remux-plan --json` (planning-only query),
      refactoring `_perform_remux`'s/`remux_bare_copilot`'s guard and
      target-resolution logic into it unchanged in substance.
   b. Add Worktree Manager's executor: POSIX runs the returned `argv` +
      polls existing liveness; Windows calls the existing
      `agent-worktrees reclaim --bare-only --yes --json` then relaunches via
      its own relocated launcher in ordinary resume mode.
   c. Delete `cmd_remux`, `_perform_remux`, and `remux_bare_copilot`'s
      subprocess-executing/verify-poll portion from agent-worktrees in the
      SAME PR that lands (a) and (b) — no parallel remux implementations.
5. Update `test_remux.py`, `test_ahp_launcher_contract.py`, and any launcher-
   path-asserting test to the new location; add Worktree Manager tests for
   the relocated scripts and the new remux action.

## Validation

- Existing launcher contract tests continue to pass unmodified in spirit
  (only the resolved script path changes).
- The **full** `test_picker_capture.py` golden/ANSI/SVG suite passes
  unmodified — the Picker's rendered launch/resume/create affordances are
  provably unaffected by where the launcher scripts live.
- A worktree with no Worktree Manager installed still functions via the new
  direct non-mux fallback (no mux, no reattach, no remux — degraded but
  working), proven by the `agent-worktrees-solo` clean-room scenario.
- `remux` detection-only query returns the same verdict before and after the
  split, for the same fixture process states (existing live mux, ambiguous
  owner, unreachable bare process).
- `reclaim_one`/Picker "Reclaim" behavior is provably unaffected by this
  slice (it does not touch the launcher at all).

## Non-Goals of this slice

- **Not an internal decomposition of the 1,844/1,203-line scripts.** They
  move as-is; simplifying their internals is separate, future work.
- **Not a persisted Mux execution-leg record.** Mux liveness stays
  live-observed; this slice does not introduce a `provider: "mux"`
  `execution_leg:` writer. If a future need arises (e.g. cross-machine mux
  observation) it is a separate, explicitly scoped addition.
- **Not a change to `resolve`/`post-exit`'s contract.** The scripts keep
  calling the same generic CLI verbs; only where the scripts themselves live
  changes.
- **Not a reconciliation with Worktree Manager's earlier, fledgling
  Picker-launch prototype.** That code is retired scaffolding; the migrated
  agent-worktrees implementation is authoritative and unmodified in substance.
