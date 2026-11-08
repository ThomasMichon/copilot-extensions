# Recovering a dirty or stale anchor

An anchor is a project's registered primary checkout. Worktree creation starts
from the fetched remote default branch and fast-forwards the anchor only when it
is clean and on that branch; a dirty, detached, off-default, ahead or diverged
anchor is never rewritten, so it silently goes stale. This procedure restores
an anchor without losing anyone's work. Never do new work in an anchor --
create a managed worktree with `agent-worktrees -p <name> create --json`.

## 1. Diagnose (read-only)

Use `git --no-optional-locks -C <anchor> ...` so inspection does not refresh
the index.

- **Upstream ref.** `git rev-parse --abbrev-ref <default>@{upstream}` names
  `<remote>/<default>`. It must match the project's configured `remote`
  (default `origin`) and `default_branch`; if they disagree, or the branch has
  no upstream, stop and ask the operator rather than guessing. Call it
  `<upstream>` below.
- **Branch state.** `git status --short --branch` shows the checked-out branch
  (or detached HEAD), ahead/behind counts, staged and unstaged entries, and
  untracked (`??`) files.
- **Local commits.** `git log --oneline <upstream>..HEAD` lists commits that
  exist only in the anchor; `git log --oneline HEAD..<upstream>` shows how far
  it is behind. Compare against a fresh fetch when one is permitted; otherwise
  state that the comparison uses the last fetched ref.
- **Content changes.** Check unstaged (`git diff --ignore-cr-at-eol --stat`),
  staged (`git diff --cached --ignore-cr-at-eol --stat`) and untracked files
  separately. Treat modifications as line-ending-only noise only when both
  diffs are empty **and** no untracked files exist while `git status` still
  reports modified paths.

## 2. Preserve real work

Before changing the anchor, move anything that is not line-ending-only noise
into a managed worktree:

- **Local commits** (ahead, diverged, or on a detached HEAD): create a managed
  worktree and `git cherry-pick` them there; worktrees of one clone share the
  object store. Confirm each commit is now reachable from a branch other than
  the anchor's default branch (`git branch --contains <sha>`).
- **Uncommitted content**: write `git diff --binary HEAD` (staged and unstaged)
  to a scratch patch, copy untracked files, apply both in the new worktree and
  commit there. Never use `git stash`: the stash is shared by every worktree of
  the clone.
- **A non-default branch** checked out in the anchor is already a named ref;
  switching away loses nothing, but report it so its owner can adopt it.

If another live session may own the anchor's changes, or their purpose is
unclear, ask the operator before moving anything. Discarding unpreserved work
always requires operator approval.

## 3. Restore

When an anchor write guard blocks these commands, acquire its time-bounded
allowance for this single repair (for example
`agent-worktrees repos allow-edits <name> --reason "<why>" --minutes 10`).

- **Line-ending-only noise:** restore the affected paths
  (`git restore -- <paths>`) and record that no content was discarded.
- **Detached or off-default, now clean:** `git switch <default>`.
- **Behind only:** `git merge --ff-only <upstream>`.
- **Ahead or diverged:** a fast-forward cannot fix this -- it reports "Already
  up to date" or refuses. After step 2 has verified every local commit is
  preserved elsewhere, realigning the default branch to `<upstream>`
  (`git reset --hard <upstream>` while on it) removes the anchor's copy of
  those commits and requires operator approval.

Finish by confirming `git status --short --branch` reports
`## <default>...<upstream>` with no ahead/behind counts and no entries.
