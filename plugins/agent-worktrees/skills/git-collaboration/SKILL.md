---
name: git-collaboration
description: >
  Multi-agent git collaboration on a worktree: pull a worktree forward onto an
  updated default branch and build on top of a just-merged PR, share a durable
  feature branch across several agents, and ff-merge a delegate's slice into that
  feature branch -- with a clear boundary between git that must go through the
  agent-worktrees wrappers and the everyday git you run directly. This is the
  "branches" executor binding for the efforts planning system: an effort
  coordinating multiple agents over one feature branch is driven from here. Use
  whenever you continue work after a PR merges, coordinate more than one agent
  on a branch, or decide whether a git command is safe to run raw. Trigger
  phrases include:
  - 'pull forward'
  - 'build on top of the merged PR'
  - 'sync the worktree to master'
  - 'shared feature branch'
  - 'multi-agent branch'
  - 'merge my slice into the feature branch'
  - 'can I just run git'
  - 'is it safe to git push'
  - 'coordinate agents on one branch'
  - 'host-only PR'
---

# Git Collaboration

This skill covers the **git collaboration flows** that sit *below* the high-level
sign-off flow (`push-changes` / `create-pr` / `finalize`, owned by the
**`worktree`** skill) and *above* raw git. Three flows live here:

1. **Pull forward / build on top** -- after a PR merges, advance the worktree
   onto the updated default branch and keep working on top of it.
2. **Shared feature branch** -- a durable branch several agents commit to, with
   one host that owns PRs.
3. **The boundary** -- which git you should just run directly, and which steps
   have a turn-key helper.

**The flows are the point; the commands are turn-key helpers.** Each
`agent-worktrees git ...` verb just wraps a short git sequence you *could* run by
hand -- it exists so the common path is one safe step that can't silently break a
shared invariant. Reach for the helper when a flow below calls for it; otherwise
use plain git.

> **Don't fully wrap git.** Wrapping every git command destroys the
> intuitiveness of the tool everyone already knows. The helpers exist *only* for
> the steps that protect a shared invariant raw git would silently break.
> Everything else is plain git -- and this skill says so explicitly, so you don't
> reach for a helper that isn't needed.

## The boundary -- plain git vs. turn-key helper vs. forbidden

| Operation | Do it via | Why |
|-----------|-----------|-----|
| `status`, `log`, `diff`, `show`, `branch -v` | **plain git** | read-only inspection; no shared state |
| `add`, `commit`, `restore`, `stash`, local `switch`, `rebase -i` **on your own worktree branch** | **plain git** | local history; disposable until it lands |
| `fetch` | **plain git** | read-only; updates remote-tracking refs only |
| Advance the worktree onto the merged default ("pull forward") | helper: `agent-worktrees git sync` | wraps fetch + rebase; drops squash-merged commits without losing local work |
| Create / update / push a **shared** feature branch | helper: `agent-worktrees git feature-branch ...` | wraps create + ff + push; a real remote branch many agents build on |
| Merge a delegate's slice into the shared feature branch | helper: `agent-worktrees git merge-to-feature ...` | wraps rebase + ff + push; must be **ff-only** (no two-parent nodes) |
| Push to the remote **default** branch / open a PR | flow: `push-changes` * `create-pr` * `finalize` | the lifecycle/sign-off flow (see the `worktree` skill) |
| Bare `git push` of a `worktree/*` branch | **forbidden** | `worktree/*` refs must never reach the remote |
| Manual merge to the default branch | **forbidden** | breaks linear, one-commit-per-worktree history -- use `finalize` |
| A **delegate** opening or merging a **PR** | **forbidden** | only the **host** opens PRs from the shared branch (below) |
| Force-push of shared history (default or shared feature branch) | **forbidden** | rewrites history other agents have built on |

Rule of thumb: **if a parser, a remote, or another agent will consume the
result, reach for the helper; if only you and your local branch see it, just run
git.** The helper never does anything you couldn't do by hand -- it just does the
invariant-bearing steps the same way every time.

## Pull forward -- build on top of a merged PR

After a PR merges (especially a **squash** merge), the worktree branch still
carries the now-upstreamed commits. To keep working, advance onto the new
default and stack new work on top -- **do not** start a fresh worktree.

**This is the standard, automatic-by-convention move the moment a PR lands** --
not an optional cleanup. As soon as you confirm the merge, pull forward.

**`pr-status` confirms the merge and tells you to do it.** `agent-worktrees
pr-status` reconciles the active PR against the provider, so a PR merged
externally (e.g. via the `auto-merge` label) reports `state: merged` rather than
a stale `open`. When it has landed and the worktree is not yet on top of the
updated default branch, it flags `pull_forward_recommended: true` with a
`next_action`. Treat that flag as a directive.

The flow is plain git you could run by hand:

```
git fetch origin && git rebase origin/<default>     # the manual flow
```

The turn-key helper does exactly that, plus the guards:

```
agent-worktrees git sync                            # the helper
```

`sync` fetches the remote and rebases the worktree branch onto
`<remote>/<default>`, **dropping commits that were squash-merged upstream**
(they reappear as a single commit on the default branch; git skips them as
already-applied) while preserving any genuinely-new local commits. It runs
**mid-flight** -- it does *not* finalize, prune, or push. A dirty tree or a true
rebase conflict stops it with a clear message instead of guessing; on a conflict
the rebase auto-aborts so the branch is left untouched -- resolve by hand, then
re-run. Because the PR squashed your work into one commit, conflicts are
uncommon.

This is the **review-gate continuation** for efforts: submit the effort PR ->
it's reviewed + merged -> confirm via `pr-status` -> `git sync` -> build Phase
work on top.

## Iterating on an open PR (the merge-only hold)

Use a hold when you want the reviewer to keep commenting on the PR, but you do
not want the fast auto-merge path to land it while you are still iterating:

```
agent-worktrees create-pr --hold
# ...address feedback locally...
agent-worktrees push-changes
agent-worktrees pr-ready
```

`create-pr --hold` opens the PR with `do-not-merge`: the Intelligence Dampener
still reviews it (unlike a draft/`wip`), but the merge gate refuses to merge it.
Each `push-changes` update is re-reviewed and remains held. `pr-ready` removes
the hold label and releases the active PR for merge.

## Shared feature branch -- many agents, one branch

When several agents collaborate on one effort over a single branch:

> **"Agent" here means an agent-bridge agent, not a Copilot sub-agent.** Multi-
> agent coordination in this skill is **always** via **agent-bridge** -- each
> delegate is a *separate Copilot CLI session* (local or over SSH) with **its own
> worktree** that can commit, push, and ff-merge on the shared branch. Copilot's
> in-process sub-agents (the Task tool) are **not** delegates here: they share the
> host's context, have no worktree or branch of their own, and cannot participate
> in a shared-branch handoff. If a "delegate" can't `git commit` in its own
> checkout, it's the wrong mechanism -- dispatch through agent-bridge.

1. **Host** drafts the effort and gets it reviewed/approved (the effort PR).
2. **Host** creates and pushes the shared feature branch:
   ```
   agent-worktrees git feature-branch <name> --push
   ```
3. Each **delegate** syncs to the branch, completes its assigned section, commits
   on the branch, writes back its slice of the effort README, then ff-merges its
   work into the shared branch:
   ```
   agent-worktrees git feature-branch <name> --sync   # pull the branch forward
   # ...do the work, commit...
   agent-worktrees git merge-to-feature <name>        # ff-only handoff
   ```
4. **Host** syncs forward from its side as delegates land slices, and -- when
   coordination is done -- ensures its local branch matches the shared branch and
   **submits the PR(s)**.

### Host-only PRs

**Only the host opens PRs** for a shared feature branch. Delegates **do**
ff-push their slices to the shared branch (that is the handoff -- `merge-to-feature`
ff-pushes by default, so the host can sync forward and see the work). What a
delegate must never do is **open or merge a PR** from the shared branch, or
**force-push** it. One PR owner -- the host -- keeps review and merge coherent;
many delegates fast-forward the branch underneath it.

### When you don't need a shared branch

If the work is **well-componentized** and each piece leaves the default branch
green on its own, skip the shared branch: let each delegate use its own worktree
and open its **own** PR. The host watches remote PR state to sequence
follow-ups. Use a shared feature branch only when the slices are interdependent
and must integrate before any of them can merge.

## Relationship to other skills

- **`worktree`** -- the high-level lifecycle (`push-changes` / `create-pr` /
  `finalize`) and push policy. This skill is the layer beneath it; the boundary
  table above reconciles the two.
- **`planning-efforts`** -- efforts bind their **"branches" participant** to this
  skill. Multi-agent coordination over a feature branch is planned and journaled
  in the effort README's `## Coordination` section; the mechanics live here.
- **`agent-bridge`** -- how the host dispatches a slice to a delegate agent, and
  **the only** multi-agent coordination mechanism this skill uses. A delegate is
  an agent-bridge session with its own worktree -- never a Copilot in-process
  sub-agent. This skill owns the git; agent-bridge owns the conversation.
