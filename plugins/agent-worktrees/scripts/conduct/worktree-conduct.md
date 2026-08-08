# Worktree status conduct

Keep the Worktree Picker honest about where the operator's attention is owed.
Only *you* know whether a worktree is truly done or still has follow-ups -- git
and process state cannot tell. Annotate THIS worktree's disposition with
`agent-worktrees status`:

- **Finalizing for real?** Make `agent-worktrees finalize` the **last** thing you
  do -- do not re-open work after it, and do not flag follow-ups. A one-line
  "all done" (optionally offering more) is fine; the worktree is prune-able.
- **Stopping with work left?** Run
  `agent-worktrees status --follow-up --summary "<what's left>"`. Leftover
  temporary state, or an external-repo change not yet pushed / merged / deployed,
  **counts as a follow-up**.
- **Just answered a question?** If nothing consequential started, leave it
  (it stays `CONVO`). If the conversation began a plan or changed state, flag it:
  `agent-worktrees status --follow-up --summary "<what's underway>"`.
- **Direction changed or you learned more?** Periodically re-summarize:
  `agent-worktrees status --summary "<current focus>"` -- add/keep `--follow-up`
  while work is owed, or clear it with `--resolved` once nothing is left.

The summary is one line; latest wins. Flag conservatively but honestly: an
unflagged worktree reads as *resolved and safe to prune*.
