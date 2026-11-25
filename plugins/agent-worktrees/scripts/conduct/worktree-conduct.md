# Worktree status conduct

`agent-worktrees status` is the authoritative Worktree Picker disposition.

- Work remains: write `--follow-up --summary "<what remains>"`. Nothing remains:
  write `--resolved --summary "<result>"`.
- `--title` names the worktree's persistent goal or standing role (e.g.
  "Backlog triager"), not the current task. Set it once that is clear,
  replacing an auto-derived first-message title; revise it when the charter,
  root goal or role changes, and recheck it on resume, restart or a new charter.
- `--activity` is the current focus: update it at each meaningful focus change,
  block or wait. `--summary` recaps progress.
- End each substantive turn with the current summary and accurate
  `--follow-up`/`--resolved` state.
- Leftover temp state, unpushed external-repo work, merged-but-undeployed
  changes, a held resource claim, or an open itemized follow-up (`agent-worktrees
  follow-ups add "<summary>" [--ref kind:value]`) all keep a worktree from
  reading as safe to prune -- a finalized/completed worktree with none of
  those is resolved and safe to prune, though paired-worktree and other holds may
  delay pruning.
- Run `agent-worktrees finalize` last -- but `finalized` is not terminal:
  resuming work afterward (a new commit, claim, or follow-up) is normal and
  safe, and reopens the worktree automatically. Never spawn a new worktree
  just because this one already finalized.
- A threshold-based status nudge is a reflective cue, not a mandatory heartbeat:
  ignore it when focus and state have not changed.
- Disposition history remains available through
  `agent-worktrees status --history`.

For mechanics, load `agent-worktrees:worktree` or run
`agent-worktrees status --help`; use `status --history --help` for history.
