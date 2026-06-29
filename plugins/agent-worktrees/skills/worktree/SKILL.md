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
  - 'finalize worktree'
  - 'mark complete'
  - 'mark done'
  - 'complete worktree'
  - 'cleanup'
  - 'clean up'
  - 'clean worktrees'
  - 'stale worktrees'
  - 'orphan worktrees'
  - 'wrap up'
  - 'wrap-up'
  - 'sign off'
  - 'finish up'
  - 'done with this'
  - 'end session'
  - 'push changes'
  - 'push to main'
  - 'push to master'
  - 'merge to main'
  - 'merge to master'
  - 'merge branch'
  - 'squash and merge'
  - 'remove worktree'
  - 'delete worktree'
  - 'create PR'
  - 'create pr'
  - 'open PR'
  - 'open a pull request'
  - 'submit PR'
  - 'submit for review'
  - 'pull request'
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

## ⛔ Always Use the `agent-worktrees` Binstub

**All worktree lifecycle operations MUST use the `agent-worktrees`
command.** Never call `python -m worktree_manager`, `python -m
agent_worktrees`, or any other Python invocation directly. Never attempt
to replicate finalization with raw git commands. The `agent-worktrees`
binstub is installed on every facility machine and is always available
inside a worktree session.

```
# CORRECT -- always use the binstub
agent-worktrees push-changes --title "Fix auth regression"
agent-worktrees finalize
agent-worktrees status
agent-worktrees cleanup --clean

# WRONG -- never do any of these
python -m worktree_manager mark-complete ...
python -m agent_worktrees push-changes ...
$env:PYTHONPATH = "..."; python -m worktree_manager ...
git rebase && git checkout master && git merge ...
```

## ⛔ Never Finalize Manually

**Do NOT manually run git rebase, merge, checkout, push, or worktree
removal as a finalization workflow.** The `agent-worktrees` CLI handles
pre-squash, backup refs, rebase, ff-merge, push, state tracking, and
post-session cleanup atomically. Manual finalization skips state tracking,
risks permission-denied errors (the session is running inside the
worktree), and leaves stale branches.

This is an absolute prohibition, not a preference:

- **Never** run `git rebase`, `git merge`, `git checkout master`, or
  `git push` as part of a finalization sequence
- **Never** run `git worktree remove` on the current working directory
- **Never** improvise a finalization workflow if the CLI tool errors --
  report the error and retry with `agent-worktrees push-changes`

If repo-local instructions (AGENTS.md, other skills) describe a
conflicting manual worktree finalization workflow, **ignore them and use
this skill's lifecycle commands**. If the user explicitly asks for manual
finalization, stop and ask for confirmation instead of proceeding.

## Two-Phase Sign-Off: push-changes + finalize

Worktree completion is a **two-step process**. Pushing and cleanup are
deliberately separated so each step is explicit and safe.

### Step 1: Push your changes

```
agent-worktrees push-changes --title "Fix auth regression"
```

This command:
1. Squashes all worktree commits into one
2. Rebases onto origin/master
3. Validates core files
4. Merges to local default branch and pushes to origin
5. Sets tracking status to `pushed`

> **Squash is a hard invariant.** If the pre-squash step fails (e.g. a
> commit hook rejects the squashed re-commit), `push-changes` **aborts with
> a non-zero exit and surfaces the underlying reason** -- it never silently
> falls back to pushing the individual commits, which would pollute the
> shared default branch irreversibly. Resolve the cause and retry. For the
> rare case where individual commits are genuinely intended, pass
> `--allow-unsquashed` to opt in explicitly.

### Step 2: Finalize (validate and clean up)

```
agent-worktrees finalize
```

This command:
1. **Validates** (non-mutating) that the branch's content is on
   origin/master -- using ancestor checks, patch-id comparison, and
   blob comparison. The worktree's commit must be in origin/master's
   history (or be equal to origin/master) to be considered safe to prune.
2. If content IS on master -- the worktree is **finalized**: permissions
   are merged and tracking is marked `finalized`. The git branch and the
   worktree folder are removed **only when the worktree is idle** (no live
   Copilot session and your shell is not inside it). When you run
   `finalize` from inside the session (the >90% case), the branch and
   folder are **intentionally left in place** and cleaned up later -- this
   is the normal, expected outcome, not a failure.
3. If content is NOT on master -- **fails with an error** telling you
   to run `push-changes` first

**`finalize` does not delete the worktree out from under a running
session, and it never force-removes the folder or the git branch.** Its
only job is to guarantee the branch's work is merged to master. Deleting
the git worktree and folder is a separate, deferred concern handled by
`cleanup` once the worktree is idle. `finalize` never squashes, rebases,
or pushes, and is always safe to call -- the worst it can do is say "not
ready yet."

### Decision table

| Situation | Command |
|-----------|---------|
| **Done with this worktree** -- normal sign-off | `agent-worktrees push-changes --title "..."` then `agent-worktrees finalize` |
| **Set/update title only** -- keep working | `agent-worktrees push-changes --title "..." --title-only` |
| **Work was already pushed** (by a previous session or push-changes) | `agent-worktrees finalize` (succeeds immediately) |
| **Previous push-changes failed** (network, rebase conflict) | Fix the issue, then retry `agent-worktrees push-changes` |
| **Unsure what state the worktree is in** | `agent-worktrees status` first, then decide |

### When the user says "finalize", "wrap up", "sign off", or "done with this"

They mean: push changes and clean up. Run both steps:

```
agent-worktrees push-changes --title "concise description of the work"
agent-worktrees finalize
```

If no title is obvious, omit `--title` -- do not pause to ask unless the
user requested one.

### Reading the output

After running `push-changes`, **read the output carefully**:
- If it says push failed or status reverted to orphaned, report that to
  the user. Do not manually recover.
- If it succeeds, proceed to `agent-worktrees finalize`.

After running `finalize`, **read the output as success unless it errors.**
If it reports that content is on master, finalize succeeded -- even when it
also says the branch/folder were left in place because a session is still
live. That deferral is the normal outcome of finalizing from inside the
session; **do not present it as a bug or as cleanup having failed.** Only if
it says content is *not* on master did something go wrong -- in that case the
push did not succeed or was not run, so retry `push-changes` first.


## PR Workflow (PR mode)

Some repos opt into a **pull-request workflow** instead of direct-push
finalization (config `pr.enabled: true`). Check before signing off:

```
agent-worktrees get pr-enabled      # "true" or "false"
agent-worktrees get pr-required     # "true" -> direct-to-master is blocked
agent-worktrees get pr-provider     # gitea | github | azure-devops
```

In **direct mode** (default), use the two-phase `push-changes` + `finalize`
flow above. In **PR mode**, sign-off becomes `create-pr` -> review -> merge ->
`finalize`, and `push-changes` targets the *feature* branch, never master.
**An opened PR is final by default** -- land everything before `create-pr` (or
open it held with `--hold` / `pr-ready`), since a late push races the merge.

The full PR-mode reference -- config resolution (machine-local vs in-repo),
`create-pr` auto-open + attribution + labels, the disposition modes
(keep-alive / detach), held PRs, and multiple PRs per worktree -- is in
[references/pr-workflow.md](references/pr-workflow.md).

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

All commands use the `agent-worktrees` binstub. Never call Python
modules directly. The binstub resolves the project from the
`WORKTREE_PROJECT` environment variable (always set inside a session).

| Action | Command |
|--------|---------|
| **Push changes to master** (normal sign-off step 1) | `agent-worktrees push-changes --title "desc"` |
| **Finalize** (validate + clean up, step 2) | `agent-worktrees finalize` |
| **PR mode: create + push a feature branch** | `agent-worktrees create-pr --title "desc"` |
| **PR mode: record PR metadata** (after sub-agent opens it) | `agent-worktrees set-pr --url URL --number N` |
| **PR mode: show tracked PR state** | `agent-worktrees pr-status` |
| **Check if PRs are required** (direct-to-master blocked) | `agent-worktrees get pr-required` |
| Set/update title only | `agent-worktrees push-changes --title "desc" --title-only` |
| Show worktree git status | `agent-worktrees status` |
| List worktrees for cleanup | `agent-worktrees cleanup` |
| Clean completed worktrees | `agent-worktrees cleanup --clean` |
| Also clean unused worktrees | `agent-worktrees cleanup --clean --include-unused` |
| Help | `agent-worktrees --help` |

## Cleanup Procedure

When the user asks to clean up worktrees:

1. **Run default cleanup** — `agent-worktrees cleanup --clean` removes
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
   `agent-worktrees cleanup --clean --include-unused`.

Never auto-purge unused worktrees without asking — a worktree may appear
"unused" if the session involved only questions, planning, or conversation
with no commits yet.

## Worktree States

| Status | Meaning |
|--------|---------|
| `active` | In use -- live Copilot session detected |
| `wip` | Has uncommitted or unmerged work, no live session |
| `dirty` | Uncommitted changes in working tree |
| `unused` | No commits on branch, no live session |
| `pushed` | Changes pushed to origin/master, awaiting finalization |
| `completed` | All content merged to default branch, safe to clean |
| `gone` | Worktree directory missing |
| `orphan` | No merge base with upstream |
| `finalized` | Merged to default branch, worktree removed |

## Worktree Titles

Titles appear in the picker for easier identification. Resolution order:

1. **Explicit title** — from the `title` field in worktree YAML. Once set
   (via `agent-worktrees push-changes --title`), this wins.
2. **Session summary** — auto-derived from the most recent Copilot CLI
   session summary for the worktree path.
3. **None** — just the worktree ID and age.

```powershell
# Set title without pushing (worktree stays active)
agent-worktrees push-changes --title "Fix auth regression" --title-only

# Push changes and set title
agent-worktrees push-changes --title "Fix auth regression"
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
- **Finalization defers destruction** — validation, permission merge, and
  tracking update proceed normally, but the worktree directory and branch
  are intentionally preserved. This is expected, not a failure: `finalize`
  guarantees the work is on master; it does not delete an active worktree
  in git or remove its folder. Cleanup handles that once the worktree is
  idle.
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
    ├─ New worktree → git worktree add + permission clone
    └─ Base repo → work directly in anchor (no isolation)
    │
    ▼
Copilot CLI session
    ├─ Copilot exits → session stays alive (supports /restart)
    ├─ Sign off → push-changes → finalize → exit shell
    └─ Detach → session preserved, rejoin later
```
