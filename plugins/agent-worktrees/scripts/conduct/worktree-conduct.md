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
  while work is owed, or clear it with `--resolved` once nothing is left. If the
  worktree's *focus* changed (not just its state), also refresh the headline
  title: `agent-worktrees status --title "<short headline>"` -- the title is the
  Picker's label, so keep it describing what this worktree is *now* about.
- **End of a substantive turn?** Before you hand control back, refresh the
  one-line summary so the Picker reflects the latest state:
  `agent-worktrees status --summary "<where things stand>"` (with
  `--follow-up`/`--resolved` as appropriate). This is the highest-signal status
  the Picker has -- keep it current rather than leaving it for finalize.

> **You'll get a reminder.** A `postToolUse` hook watches for drift and, after
> ~25 tool calls or ~20 minutes without a disposition write, injects a one-line
> nudge to run `status --summary`/`--title`. It resets when you write one (and on
> finalize). Treat the nudge as a cue to reflect -- update if the focus/state
> moved, ignore it if nothing consequential changed. (Silence it for a session
> with `AGENT_WORKTREES_NUDGE=off`; tune via `AGENT_WORKTREES_NUDGE_CALLS` /
> `AGENT_WORKTREES_NUDGE_MINUTES`.)

The summary is one line; latest wins. Flag conservatively but honestly: an
unflagged worktree reads as *resolved and safe to prune*.

> **Every disposition write is remembered.** Each `status --summary`/`--title`/
> `--follow-up`/`--resolved` also appends a durable entry to the worktree's
> disposition-history sidecar (`~/.<project>/worktrees/<id>.history.jsonl`). To
> grok *what a worktree has been doing* -- its focus shifts and what got done --
> read the trajectory with `agent-worktrees status --history` (`--limit N`,
> `--json`). It lives and dies with the tracking record. This is why keeping the
> summary current pays off twice: it feeds the Picker **and** leaves a legible
> trail for the next agent.

> `finalize` also **seals a fallback identity** -- it backfills the worktree's
> title and session registry from session-state so a pruned worktree is never
> left "(untitled)". That is only a safety net for sessions that never asserted
> anything; your `status --summary` is the real signal, so don't rely on the
> seal in place of keeping the disposition honest.
