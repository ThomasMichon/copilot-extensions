# Worktree status conduct

`agent-worktrees status` is the authoritative Worktree Picker disposition.

- Work remains: write `--follow-up --summary "<what remains>"`. Nothing remains:
  write `--resolved --summary "<result>"`.
- On meaningful focus or direction changes, update both `--title "<headline>"`
  and `--summary "<current focus>"`.
- End each substantive turn with the current summary and accurate
  `--follow-up`/`--resolved` state.
- Leftover temp state, unpushed external-repo work, and merged-but-undeployed
  changes are follow-up; a finalized/completed worktree without `--follow-up`
  is resolved and safe to prune.
- Run `agent-worktrees finalize` last; do not resume work after finalizing.
- A status nudge is a reflective cue, not a mandatory write. There is no status
  heartbeat or timer: ignore the nudge when focus and state have not changed.
- Disposition history remains available through
  `agent-worktrees status --history`.

For mechanics, load `agent-worktrees:worktree` or run
`agent-worktrees status --help`; use `status --history --help` for history.
