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

**Migrated verbatim from `plugins/agent-worktrees/bin/`** (proven, tested
implementation; the still-shipping copy there remains the resolved target of
`cmd_launch` until Phase 3b Slice 2's cutover step repoints it — see the linked
plan for the ordered steps and the "clean cutover" invariant: this becomes the
one true copy, not a second implementation living alongside an old
Worktree-Manager-native prototype).

Deployed automatically: `self_install.py`'s `_copy_payload` copies the whole
`worktree-manager/` payload directory (this one included) into each versioned
install slot — no separate packaging step is needed for this directory.
