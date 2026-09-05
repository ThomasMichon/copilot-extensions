# Phase 3b, Slice 2 — Relocate Mux launch/reattach mechanics out of agent-worktrees

- **Parent effort:** [`README.md`](README.md) § Phase 3b
- **Tracks:** [#2062](https://github.com/ThomasMichon/copilot-extensions/issues/2062)
- **Governing vision:** [`visions/session-hosting`](../../../visions/session-hosting/README.md)
  Concepts/*Session-host provider*; the matching Non-Goal in
  [`visions/plugins/agent-worktrees`](../../../visions/plugins/agent-worktrees/README.md).
- **Depends on:** [`phase-3b-ahp-relocation.md`](phase-3b-ahp-relocation.md)
  (Slice 1) proving the pattern of a relocated provider calling agent-worktrees
  only through its `--json` subprocess CLI. This slice does **not** wait for
  Slice 1's every step, but reuses its generic `execution-leg` verbs once they
  exist (see below).
- **Status:** Planned — not started.

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

## Recommendation: do not attempt this as one slice

Given the size (3,500+ lines across three platforms) and that `cmd_remux`
mixes generic liveness detection (which should stay) with a launch action
(which should move), this document proposes **two further sub-slices** rather
than one big relocation:

### Sub-slice 2a — Move launcher scripts + repoint resolution (no logic change)

1. Copy `launch-session.{sh,ps1,cmd}` and `pane-wrapper.{sh,ps1}` verbatim
   into `worktree-manager/` (e.g. `worktree-manager/bin/`). Zero logic
   changes — this is a location move only.
2. Change `agent-worktrees`'s `cmd_launch` path resolution: instead of
   resolving `<agent-worktrees install dir>/bin/launch-session.*`, resolve
   `<worktree-manager install dir>/bin/launch-session.*` (mirroring the
   existing `_active_runtime_source()`-style version-marker lookup pattern
   already used by `worktree-manager/production_picker/_engine_runtime.py`,
   just in the opposite direction). Keep a fallback to the **legacy
   in-plugin location** for one deprecation window so a mixed-version
   install (new agent-worktrees, old Worktree Manager without the scripts
   yet) still launches — mirroring the existing legacy-location fallback
   `cmd_launch` already has for `~/.{project}/bin/launch-session.sh`.
3. The scripts' own calls into `agent-worktrees resolve` / `agent-worktrees
   post-exit` are **unchanged** — still the same CLI subprocess boundary,
   now just invoked from a different installed location.
4. Land as: one agent-worktrees PR (path resolution + fallback, version
   bump) and one worktree-manager PR (script files + install-manifest
   entries so they get deployed, version bump). Test by running the existing
   `tests/test_ahp_launcher_contract.py`-style launcher contract tests
   against the relocated scripts (update their asserted script path, not
   their assertions about launcher behavior).

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

1. Copy launcher/wrapper scripts into `worktree-manager/`, unchanged.
   Land as its own PR (worktree-manager version bump, no behavior change —
   files not yet referenced by anything).
2. Add the new-location resolution + legacy fallback to `cmd_launch` in
   agent-worktrees. Land as its own PR (agent-worktrees version bump).
   At this point both locations work; nothing is deleted.
3. Deploy Worktree Manager's installer to actually ship the relocated
   scripts (install-manifest entry, `check-install-contract.py` coverage).
4. Split `cmd_remux` per Sub-slice 2b; land the detection-query verb in
   agent-worktrees first (additive), then the Worktree-Manager-driven action
   second.
5. Remove the legacy in-plugin script copies and the fallback path from
   `cmd_launch` once telemetry/a deprecation window confirms no install still
   needs it.
6. Update `test_remux.py`, `test_ahp_launcher_contract.py`, and any launcher-
   path-asserting test to the new location; add Worktree Manager tests for
   the relocated scripts and the new remux action.

## Validation

- Existing launcher contract tests continue to pass unmodified in spirit
  (only the resolved script path changes).
- A worktree with no Worktree Manager installed still launches via the
  legacy fallback location, unchanged, until step 5 retires it.
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
