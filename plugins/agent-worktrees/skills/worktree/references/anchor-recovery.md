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

- **Local commits** (ahead, diverged, or on a detached HEAD): first retain the
  originals under a recovery branch (`git branch recover/<anchor>-<date> HEAD`)
  so they stay reachable regardless of later steps. Then create a managed
  worktree and `git cherry-pick` them there; worktrees of one clone share the
  object store. Cherry-picking onto a newer base creates new commit IDs, so
  verify two things separately: the originals are reachable from the recovery
  branch (`git branch --contains <original-sha>`), and the managed worktree's
  replayed commits carry the same changes (read the `git range-diff` output or
  compare the full per-commit patches; `--stat` counts are not enough).
- **Uncommitted content**: export staged and unstaged changes as separate
  patches -- `git diff --cached --binary` (index vs HEAD) and `git diff --binary`
  (working tree vs index) -- because a single `git diff HEAD` hides index-only
  edits that the working tree has undone. Copy untracked files too. In the
  managed worktree, apply and commit the staged patch first, then apply and
  commit the unstaged patch, so both versions survive in history. Never use
  `git stash`: the stash is shared by every worktree of the clone.
- **A non-default branch** checked out in the anchor is already a named ref;
  switching away loses nothing, but report it so its owner can adopt it.

If another live session may own the anchor's changes, or their purpose is
unclear, ask the operator before moving anything. Discarding anything --
including confirmed line-ending-only noise -- always requires operator
approval. When an anchor write guard blocks a step
here or in step 3, acquire its time-bounded allowance for this single repair
(for example
`agent-worktrees repos allow-edits <name> --reason "<why>" --minutes 10`).

## 3. Restore

- **Line-ending-only noise:** with operator approval (as for any discard; see
  `cleanup-details.md`), restore both the index and working tree of the
  confirmed paths from HEAD (`git restore --staged --worktree -- <paths>`; a
  plain `git restore` leaves staged noise in place, as described in
  `cleanup-details.md`) and record that no content was discarded.
- **Detached or off-default, now clean:** `git switch <default>`.
- **Behind only:** `git merge --ff-only <upstream>`.
- **Ahead or diverged:** a fast-forward cannot fix this -- it reports "Already
  up to date" or refuses. After step 2 has retained the originals on a
  recovery branch and verified the replayed content, realigning the default
  branch to `<upstream>` (`git reset --hard <upstream>` while on it) requires
  operator approval. Report the recovery branch so its owner can delete it once
  the replayed work lands.

Finish by confirming `git status --short --branch` reports
`## <default>...<upstream>` with no ahead/behind counts and no entries.
