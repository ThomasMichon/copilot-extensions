---
name: worktree
description: >
  Worktree isolation system — lifecycle, finalization, cleanup, commit/push
  policy, and cross-worktree safety. Use this skill when managing worktrees,
  checking worktree state, finalizing, cleaning up stale worktrees, or
  understanding the worktree-per-session model.
  Trigger phrases include:
  - 'worktree'
  - 'worktrees'
  - 'finalize'
  - 'mark complete'
  - 'cleanup'
  - 'clean up'
  - 'clean worktrees'
  - 'stale worktrees'
  - 'orphan worktrees'
---

# Worktree Skill

This system uses **git worktrees** to isolate concurrent Copilot CLI
sessions. Each session creates or resumes a worktree — a lightweight copy
of the repo with its own branch, working directory, and index.

## Am I in a Worktree?

Check the branch name:

```powershell
$branch = git rev-parse --abbrev-ref HEAD
if ($branch -like 'worktree/*') { "In worktree: $branch" }
```

If on the default branch or another non-`worktree/` branch, you're in the
anchor repo (base-repo mode).

## Committing and Pushing

### Push Policy

**Never run a bare `git push` from a worktree branch.** A bare push
creates a `worktree/*` branch on the remote, which should never exist.
Worktree branches are local-only — all pushes to the remote default branch
go through the finalization flow (rebase → ff-merge → push).

**Do not auto-push.** Pushing only happens in two cases:

1. **Worktree finalization** — the standard squash → rebase → ff-merge →
   push flow.
2. **The user explicitly says "push"** — this means
   `git push origin HEAD:<default-branch>`. Always push to the remote
   default branch; never to another remote or branch unless the user
   specifies one.

Committing freely to the worktree branch is encouraged (see below), but
commits stay local until finalization or an explicit push.

### In a Worktree

**Commit regularly** to the worktree branch during work — worktree
branches are disposable, so committing is always safe. Atomic commits
with descriptive messages; don't let changes pile up unstaged. Commits
stay on the `worktree/{id}` branch until finalization.

**Only commit work belonging to this worktree.** Each worktree is an
isolated workspace for a specific task or set of tasks. Do not stage or
commit files from unrelated work that happens to be present.

### Finalization Merge Strategy

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

## Quick Reference

All commands route through the project binstub (`$WORKTREE_PROJECT`), which
ensures operations are scoped to the correct project. Inside a session,
`$WORKTREE_PROJECT` is always set.

| Action | Command |
|--------|---------|
| All worktree operations | `$WORKTREE_PROJECT --help` |
| Set title (while active) | `$WORKTREE_PROJECT mark-complete --title "desc" --title-only` |
| Mark complete (triggers finalize) | `$WORKTREE_PROJECT mark-complete` |
| Finalize a worktree | `$WORKTREE_PROJECT finalize` (auto-detects from branch, or pass ID) |
| Show worktree git status | `$WORKTREE_PROJECT status` |
| List worktrees for cleanup | `$WORKTREE_PROJECT cleanup` |
| Clean completed worktrees | `$WORKTREE_PROJECT cleanup --clean` |
| Also clean unused worktrees | `$WORKTREE_PROJECT cleanup --clean --include-unused` |

## Cleanup Procedure

When the user asks to clean up worktrees:

1. **Run default cleanup** — `$WORKTREE_PROJECT cleanup --clean` removes
   only `completed` worktrees (those whose changes are already merged via
   squash-merge) and `gone` worktrees (path no longer exists).
   - For `gone` worktrees, the branch is only deleted if its content is
     verified to be on the default branch (commit ancestry or blob
     comparison). If unmerged content is detected, the worktree is skipped
     with a warning.
   - Cleanup acquires the finalization lock to prevent races with
     post-exit finalization running in another session.
   - After cleanup, `git worktree prune` runs automatically to remove
     stale worktree entries.
2. **Report unused count** — the script reports how many `unused` worktrees
   it preserved. These have no commits but may contain planning,
   conversation history, or uncommitted work.
3. **Ask the user** whether to also purge unused worktrees. If yes, run
   `$WORKTREE_PROJECT cleanup --clean --include-unused`.

Never auto-purge unused worktrees without asking — a worktree may appear
"unused" if the session involved only questions, planning, or conversation
with no commits yet.

## Worktree States

| Status | Meaning |
|--------|---------|
| `active` | In use — live Copilot session detected |
| `wip` | Has uncommitted or unmerged work, no live session |
| `dirty` | Uncommitted changes in working tree |
| `unused` | No commits on branch, no live session |
| `completed` | All content merged to default branch, safe to clean |
| `gone` | Worktree directory missing |
| `orphan` | No merge base with upstream |
| `finalized` | Merged to default branch, worktree removed |

## Worktree Titles

Titles appear in the picker for easier identification. Resolution order:

1. **Explicit title** — from the `title` field in worktree YAML. Once set
   (by `$WORKTREE_PROJECT mark-complete`), this wins.
2. **Session summary** — auto-derived from the most recent Copilot CLI
   session summary for the worktree path.
3. **None** — just the worktree ID and age.

```powershell
# Set title without marking complete (worktree stays active)
$env:WORKTREE_PROJECT mark-complete --title "Fix auth regression" --title-only

# Set title and mark complete (triggers finalization on exit)
$env:WORKTREE_PROJECT mark-complete --title "Fix auth regression"
```

## Cross-Worktree Safety

**CRITICAL: Never modify a sibling worktree with an active session.**
Read-only inspection is always safe; any mutating git operation requires
explicit user authorization.

When diagnosing worktree state across the fleet:

1. **Read-only inspection is always safe** — `git -C <path> log`,
   `status --porcelain`, `rev-parse`, `merge-base` queries are fine.
2. **Any mutating git operation on a sibling requires explicit user
   authorization** — rebase, reset, checkout, stash push/pop, cherry-pick,
   clean, etc. Ask first, even if the fix looks trivial.
3. **If the user authorizes work on a sibling**, confirm which worktree
   and what operation before proceeding.

## Active Worktree Safety

Worktrees with a live Copilot session always show as **active** regardless
of their git state. Even if the branch appears fully merged, an active
session means:

- **Cleanup will skip it** — never removes directories or branches for
  active worktrees.
- **Finalization defers destruction** — rebase, merge, and push proceed
  normally, but the worktree directory and branch are preserved.
- **Status shows `active`** — never `completed`, `unused`, or `wip` while
  a session is running.

## Session Detection

The picker shows 🟢 on worktrees with live Copilot CLI sessions. This
is detected by scanning `~/.copilot/session-state/` — no hooks or
external state needed. Dead PIDs are filtered automatically.

## Lifecycle

```
agent-worktrees / launcher
    │
    ▼
Arrow-key picker (always shown)
    ├─ Active worktrees → Resume (increment resume_count)
    ├─ ✨ New worktree → git worktree add + permission clone
    └─ 📂 Base repo → work directly in anchor (no isolation)
    │
    ▼
Copilot CLI session
    ├─ Copilot exits → session stays alive (supports /restart)
    ├─ Sign off → mark-complete → exit shell → finalize
    └─ Detach → session preserved, rejoin later
```
