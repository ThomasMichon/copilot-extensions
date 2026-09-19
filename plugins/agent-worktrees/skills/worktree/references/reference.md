# Worktree — Reference

Detailed, occasional-use material split out of `SKILL.md` (the entrypoint)
to keep the entrypoint lean. Load this file only when a section below is
actually needed.

## Payload Command Resolution

The agent-worktrees session command catalog supplies an exact `argv[0]`
owned by this plugin payload. Replace `<agent-worktrees catalog argv[0]>` in
direct runtime operations with that raw path. Quote the path at each shell
call site; if assigning it to a variable, store the raw path without
embedded quote characters and invoke the variable quoted. Never paste an
absolute path unquoted, search `PATH`, or substitute a same-named command
from another payload. Project binstubs and commands explicitly labeled as
management boundaries remain distinct attributable entry points. In
PowerShell, invoke the catalog path as `& "<agent-worktrees catalog argv[0]>" <args>`.

The payload command provisions its runtime on first use and works without
the interactive launcher. If session-start hooks did not publish the
catalog, enumerate installed agent-worktrees payloads and fail unless
exactly one exists. Invoke that payload's `bin/payload/agent-worktrees` on
POSIX or `bin\payload\agent-worktrees.cmd` on Windows directly; never choose
the first match from multiple marketplaces or stamp a global wrapper just
to recover an in-session command.

## Cross-Machine Inspection

Use the project binstub's **agent-worktrees commands** to inspect
worktrees on another machine. Do not discover them by listing guessed
checkout directories or by reconstructing paths from naming conventions.

Operators often identify a worktree by only its four-character display
suffix (for example, `0541`). Treat that as a lookup key, **not** as a
complete worktree or Copilot session id:

```bash
# Run on the target through its canonical SSH alias.
ssh <machine-alias> "<project> worktrees list --json"

# Resolve the unique full id ending in -0541, then enumerate its sessions.
ssh <machine-alias> "<project> worktrees list-sessions --worktree <full-worktree-id> --json"

# Read one exact registered session without scanning unrelated transcripts.
ssh <machine-alias> "<project> worktrees session-transcript <session-id> --json"
```

Always pass the resolved **full worktree id** to follow-up commands; some
surfaces display or accept a four-character suffix, but support is not
uniform and suffixes can be ambiguous. Once `list-sessions` supplies exact
Copilot session ids, an explicitly requested deep diagnosis may inspect
only their `~/.copilot/session-state/<session-id>/events.jsonl` files or
keyed rows in `~/.copilot/session-store.db`. Enumerate first; never begin
with a recursive state-root or filesystem sweep.

## Binstub and Project-Registration Notes

Project binstubs pin the payload that created them and carry an ownership
receipt. A different payload cannot silently overwrite one; deliberate
ownership transfer uses the current payload command's
`reconcile-binstubs --transfer <project>` operation.

> **`register` (adopt) is the exception — cwd is the only *implicit* locator.**
> Because a project binstub / `--project <name>` resolves an *already-adopted*
> project, those levers don't exist for the repo you're about to adopt.
> `<agent-worktrees catalog argv[0]> register <name>` therefore takes the repo **path from cwd** (the
> git root of the current directory → its anchor) unless you name one explicitly;
> `<name>` is only the project **label**. So run `register` **from inside the
> target repo's checkout**, or pass `--repo-dir <path>` (or use `repos add <name>
> <path>`). Running `register <name>` from a *different* repo silently adopts
> *that* repo's path under `<name>`.

## Finalization Merge Strategy

When a worktree is marked complete, finalization merges it back to the
default branch. The merge strategy preserves **linear history** with
exactly **one commit per worktree**:

1. **Pre-squash** all worktree commits into a single commit on the
   worktree branch (uses `git reset --soft` to merge-base, then
   re-commits). A backup ref is saved for rollback on failure.
2. **Rebase** the single squashed commit onto the remote default branch
3. **Fast-forward merge** into the local default branch

**Standard merge commits are never used.** The result is always a linear
history with one squashed commit per worktree. No two-parent merge nodes,
no multi-commit replays, no extraneous files from other branches.

### What This Means for Agents

- **Commit normally** during work — individual commits help track progress,
  but finalization squashes them into one commit for the default branch.
- **Don't worry about merge conflicts** — pre-squashing reduces rebase
  conflicts to a single resolution. If rebase still fails, original
  commits are restored from the backup ref.
- **Don't manually merge to the default branch** — finalization handles
  this automatically when the worktree is marked complete.
- **Don't stage unrelated files** — if the working tree has changes from
  other sessions or stale state, only stage and commit files relevant to
  the current task.

### In Base-Repo Mode

Commits go directly to the current branch with no finalization flow.
Follow the repo's normal commit policy.

## Worktree Titles

Titles appear in the picker for easier identification. Resolution order:

1. **Explicit title** — from the `title` field in worktree YAML. Once set
   (via `<agent-worktrees catalog argv[0]> push-changes --title`), this wins.
2. **Session summary** — auto-derived from the most recent Copilot CLI
   session summary for the worktree path.
3. **None** — just the worktree ID and age.

```powershell
# Set title without pushing (worktree stays active)
& "<agent-worktrees catalog argv[0]>" push-changes --title "Fix auth regression" --title-only

# Push changes and set title
& "<agent-worktrees catalog argv[0]>" push-changes --title "Fix auth regression"
```

## Active Worktree Safety

Worktrees with a live Copilot session always show as **active** regardless
of their git state. Even if the branch appears fully merged, an active
session means:

- **Cleanup will skip it** — never removes directories or branches for
  active worktrees.
- **Finalization defers destruction** — validation, permission merge, and
  tracking update proceed normally, but the worktree directory and branch
  are intentionally preserved. This is expected, not a failure: `finalize`
  guarantees the work is on the default branch; it does not delete an active worktree
  in git or remove its folder. Cleanup handles that once the worktree is
  idle.
- **Status shows `active`** — never `completed`, `unused`, or `wip` while
  a session is running.

## Session Detection

The picker shows 🟢 on worktrees with live Copilot CLI sessions. Liveness is a
union of signals: tracked session locks under `~/.copilot/session-state/`, live
`wt-<id>` tmux/psmux sessions, cached `mux_live` / `bound_live` hints, and
bridge-owned session locks. Dead PIDs are filtered automatically, and stale
locks can be reclaimed with the `reclaim` flow.

### Diagnosing an unretired handoff predecessor pane

A worktree that accumulates extra mux panes after a series of handoffs (a
predecessor whose successor was spawned but whose own retirement was never
confirmed) is a handoff-lifecycle bug, not something to fix by hand. Run
`agent-worktrees handoffs-check --worktree-id <id>` (read-only) or add
`--execute` to retire what it finds. The read-only report does not itself
confirm the pane is still alive -- it lists every unretired candidate it
finds; `--execute` runs the same live-check choreography the resident
status monitor uses for its own automatic sweep, and is what actually
resolves whether a listed candidate was real. `--all` checks
every tracked worktree in the current project. See
`docs/architecture.md § Handoff cutover lifecycle` for the full 13-stage
model this diagnoses against. **Never manually kill a
pane/process** to work around a suspected stuck handoff -- use this tool.
