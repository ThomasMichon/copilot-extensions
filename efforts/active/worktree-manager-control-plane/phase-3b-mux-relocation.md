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
  verbatim; deployment mechanism proven). Repointing `cmd_launch` (the actual
  cutover) is next.

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
2. **Step 2 (the cutover):** in the same PR:
   - Change `agent-worktrees`'s `cmd_launch` to resolve
     `<worktree-manager install dir>/bin/launch-session.*` (mirroring the
     version-marker lookup pattern `worktree_manager/production_picker/
     _engine_runtime.py` already uses in the opposite direction).
   - Add the **direct, non-mux fallback**: when no usable Worktree Manager is
     found (health-probed, same pattern as the bare-invocation Picker seam),
     `cmd_launch` execs Copilot directly in the worktree instead of a mux
     pane. This is new, small code — not a retained copy of the old scripts.
   - **Delete** `plugins/agent-worktrees/bin/launch-session.{sh,ps1,cmd}` and
     `pane-wrapper.{sh,ps1}` from agent-worktrees in this same PR.
   - The scripts' own calls into `agent-worktrees resolve` / `agent-worktrees
     post-exit` are unchanged — still the same CLI subprocess boundary, now
     invoked from a different installed location.
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

1. Split `cmd_remux`/`_perform_remux` into: **detection** (is the owner
   process unreachable/ambiguous? — stays in agent-worktrees as an
   observation query, reusing `reclaim.py` unchanged) and **action** (adopt
   into tmux via `reptyr`, or the Windows reclaim-then-relaunch-through-the-
   normal-launcher step — moves to Worktree Manager, which now owns "the
   normal mux launcher" per Sub-slice 2a).
2. agent-worktrees exposes the detection half as a `--json` query (e.g.
   `agent-worktrees mux-owner-status --worktree-id <id>`) that Worktree
   Manager calls before deciding to reclaim; the actual reclaim/relaunch
   sequence is driven by Worktree Manager against the relocated launcher.
3. `reclaim_one` (used by both Picker "Reclaim" and remux) **stays in
   agent-worktrees unchanged** — it is a generic process-termination
   operation over `reclaim.py`'s process-table observation, not a launch
   mechanic, and the Picker's plain "Reclaim" action (no relaunch) has
   nothing to do with the Mux launcher.
4. Preserve every existing safety invariant from #1478/#1491 verbatim: refuse
   an existing live mux, refuse an ambiguous owner, never reap the calling
   process's own subtree.
5. Same clean-cutover rule: `cmd_remux`'s relaunch action moves to Worktree
   Manager and is deleted from agent-worktrees in the same PR that adds the
   detection-query verb's consumer — no parallel remux implementations.

## What stays in agent-worktrees (not relocated, ever)

- `sessions.py`'s `has_mux_session`, `has_mux_session_named`, `LiveVerdict`,
  `verify_worktree_active` — live liveness **observation**, feeding the
  aggregate-status reducer. This is the vision's *provider-observation-
  ingestion* feature, not launch mechanics.
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
2. Add the health-probed new-location resolution **and** the direct non-mux
   fallback to `cmd_launch`; delete the old in-plugin scripts. One PR, one
   cutover — validated against the full Picker golden/screenshot suite,
   both plugins' test suites, and the `agent-worktrees-solo` /
   `worktree-manager-bootstrap` clean-room scenarios.
3. Author (or extend) a combined clean-room scenario proving an actual
   interactive mux launch end-to-end across both installed plugins, if not
   already done as part of step 2.
4. Split `cmd_remux` per Sub-slice 2b; land the detection-query verb in
   agent-worktrees, then cut the relaunch action over to Worktree Manager and
   delete it from agent-worktrees in the same PR (same clean-cutover rule).
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

