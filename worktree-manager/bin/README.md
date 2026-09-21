# Worktree Manager launcher scripts

These launcher/wrapper scripts are the **canonical, single implementation** of
interactive Mux (TMux/PSMux) session launch and reattach for the copilot-extensions
harness, per the [`session-hosting`](../../visions/session-hosting/README.md)
vision and [Phase 3b Slice 2](../../efforts/active/worktree-manager-control-plane/phase-3b-mux-relocation.md)
of the `worktree-manager-control-plane` effort.

- `launch-session.ps1` / `launch-session.sh` / `launch-session.cmd` — resolve a
  JSON launch plan from `agent-worktrees resolve`, execute it natively
  (mux session creation/attach, profile selection, seed injection, handoff
  cutover argv), then call `agent-worktrees post-exit` for finalization.
- `pane-wrapper.sh` / `pane-wrapper.ps1` — wrap the actual mux pane command for
  graceful exit-code handling, initial-prompt injection, and AHP token handoff.
- `session-options.ps1` / `session-options.sh` — per-session status bar +
  behaviors that `launch-session.ps1`/`.sh` stamp onto each mux session.
  `launch-session.ps1` dot-sources `session-options.ps1` via a
  `$PSScriptRoot`-relative path (`session-options.sh` instead uses a fixed
  `~/.agent-worktrees/bin/` path, unaffected by relocation), so the Windows
  script must ship as a sibling of the relocated launcher — omitting it left
  Worktree Manager-launched sessions with a silently unconfigured psmux status
  bar (the failure is swallowed, not a launch error).
- `apply-mux-keybinds.ps1` / `apply-mux-keybinds.sh` — opt-in, server-global
  mux tuning (keystroke passthrough + `escape-time`), shipped alongside
  `session-options.*` for parity with agent-worktrees' own deployment; run by
  the user or a machine-restore flow, never automatically.
- `psmux-passthrough.conf` — the keystroke-passthrough fragment
  `session-options.ps1` resolves by the same `$PSScriptRoot`-relative path.
- `psmux-path.ps1` — Windows psmux binary discovery/compatibility helper,
  also dot-sourced from `launch-session.ps1` by a `$PSScriptRoot`-relative
  path; ships as a sibling for the same reason as `session-options.ps1`.

**Migrated verbatim from `plugins/agent-worktrees/bin/`,
`plugins/agent-worktrees/terminal/`, and `plugins/agent-worktrees/scripts/`**
(proven, tested implementation). Phase 3b Sub-slice 2a Step 2's cutover is
complete: this is now the **one true copy** of the interactive mux launch
scripts. `agent-worktrees`'s `cmd_launch` resolves this installed location
live and execs into it; when this Manager is absent or unusable it falls back
to a small, direct non-mux Copilot invocation instead of a second, in-plugin
implementation of the mux launcher. The in-plugin scripts and their deploy
steps were deleted from `plugins/agent-worktrees/` in the same cutover.

Deployed automatically: `self_install.py`'s `_copy_payload` copies the whole
`worktree-manager/` payload directory (this one included) into each versioned
install slot — no separate packaging step is needed for this directory.
