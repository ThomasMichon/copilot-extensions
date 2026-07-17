# Worktree Lifecycle & Change Management

The single, browsable walkthrough of a worktree's life — from creation through
landing its change to cleanup — and how the two landing paths (direct-push and
pull-request) work. This is the **map**. For the exact operator steps inside a
Copilot session, use the **`worktree` skill**; for the deep PR reference (head
schemes, provider delegation, recovery), see
[`skills/worktree/references/pr-workflow.md`](../skills/worktree/references/pr-workflow.md);
for every config key, see [config-reference.md](config-reference.md); for the
full verb catalog, see [cli-reference.md](cli-reference.md).

> All commands use the `agent-worktrees` binstub (or a project binstub /
> `--project <name>`). Never call the Python modules or raw `git worktree`
> directly — the binstub owns squash, rebase, push, and prune. Context resolves
> **from the current directory**, the way git does.

## The lifecycle at a glance

```
                      ┌─────────────────────────────────────────────┐
   create / resolve   │                                             │
  ───────────────────▶│  ACTIVE  ──commit──▶  WIP  ──────────────┐  │
   (picker or          │  (live session)      (ahead, not landed) │  │
    programmatic)      └───────────────────────────────────────┐  │  │
                                                                │  │  │
                                   ┌────────────────────────────┘  │  │
                                   ▼                                │  │
                         ┌──────── landing ────────┐                │  │
                         │                          │               │  │
              direct-push│                          │PR mode        │  │
                         ▼                          ▼               │  │
                 push-changes              create-pr → review        │  │
                      │                    → pr-merge (consent)       │  │
                      │                    → merged                   │  │
                      ▼                          │                    │  │
                   MERGED  ◀──────────────────────┘                   │  │
                      │                                               │  │
                      ▼                                               │  │
              finalize / pr-complete ──▶ COMPLETED ──cleanup──▶ FINALIZED/pruned
                      │                                               ▲  ▲
                      └────────── resume until pruned ────────────────┘  │
                                   (detached PR? recover) ───────────────┘
```

**Committed ≠ merged ≠ deployed.** A commit lives only on the worktree branch;
**merged** (via the default-branch landing) makes the change shared and *primes*
deployment; **deployed** means a running system reflects it (a separate install
step for runtime plugins). Don't call a merged change "deployed."

## Worktree states

The tracking state (seen in `list` / the picker) and its status-bar block:

| Tracking state | Bar block | Meaning |
|----------------|-----------|---------|
| `active` | — | Live Copilot session detected |
| `dirty` | `DIRTY` (red) | Uncommitted changes in the working tree |
| `wip` | `WIP` (amber) | Clean; ahead with commits not yet on upstream |
| `unused` | `UNUSED` (grey) | Clean; no commits **and** no conversation since the fork point |
| `convo` | `CONVO` (teal) | Clean; no commits, but the session held conversation turns (`💬N`) |
| `pushed` | — | Changes pushed to the default branch, awaiting finalization |
| `completed` | `FINAL` (green) | All content landed on the default branch; safe to clean |
| `finalized` | — | Landed and the worktree removed |
| `gone` | — | Worktree directory missing |
| `orphan` | `ORPHAN` (magenta) | No merge base with upstream |

`unused` vs `convo` is why cleanup never auto-purges a commit-less worktree: it
may hold planning or conversation. See
[cli-reference.md § status-segment](cli-reference.md) for the bar detail.

## 1. Create

| Way | Command | Use when |
|-----|---------|----------|
| Interactive | `my-project` (bare binstub) or `agent-worktrees resolve` | A human at a terminal picks/creates a worktree and launches a session (the **Picker**). |
| Muxed launch | `agent-worktrees resolve --new` | Create **and** launch a multiplexed interactive session (refused without a TTY). |
| Programmatic | `agent-worktrees create [--json]` | An agent or daemon needs a worktree path with **no launch and no mux** — prints id + directory. |

A fresh worktree branches from the up-to-date default branch into a
`<anchor>.worktrees/<id>` sibling folder. Until it has commits or a live
session it lists as `unused`.

## 2. Active — work and commit

Commit freely on the worktree branch; commits are cheap and isolated, and the
branch is never shared until you land it. Idle worktrees are kept aligned with
the default branch **fast-forward only** (never rebased or discarded) — a clean,
strictly-behind worktree auto-fast-forwards on resume (`--no-fast-forward` or
`auto_fast_forward: false` to disable). A worktree that is ahead or diverged is
left untouched.

## 3. Landing the change

There are two landing paths. Which one applies is a **repo config choice**
(`pr.enabled` / `pr.required`), not a per-session decision. Check it first:

```bash
agent-worktrees get pr-profile     # direct | pr-human-merge | pr-agent-merge
agent-worktrees get pr-required    # "true" ⇒ direct-to-default-branch is blocked
```

### 3a. Direct-push (no PR) — two-phase sign-off

```bash
agent-worktrees push-changes --title "concise description"   # squash → rebase → push to default branch
agent-worktrees finalize                                      # validate content is upstream, then prune when idle
```

`push-changes` squashes the worktree's commits, rebases onto the current default
branch, and pushes. `finalize` verifies the content actually landed before
removing the worktree/branch — and defers the prune while a session is still
live. Never hand-run `git merge`/`push`/`worktree remove`.

### 3b. PR mode — the `pr-*` command family

When the repo is PR-gated, sign-off becomes **create-pr → review → merge →
reconcile**, and `push-changes` targets the *feature* branch, never the default
branch. Three profiles decide which verbs apply:

- **`pr-human-merge`** — PR-gated, a human approves + merges. Use `create-pr`,
  `pr-watch`, `pr-status`, `pr-complete`. `pr-merge` **does not apply** (no
  consent label bound).
- **`pr-agent-merge`** — an auto-merge consent label is bound: after approval the
  author runs `pr-merge` to signal consent and the review gate merges. The full
  family applies.

The verbs are self-describing — `pr-status` prints the active `flow:` and
`pr-merge` refuses (naming the reason + next step) where it doesn't apply.
Believe them; never hand-merge past a verb that says it doesn't apply.

| Verb | Role in the loop |
|------|------------------|
| `create-pr [--title][--body/--body-file][--draft][--new]` | Squash + publish the PR head branch and open the PR. `--draft` opens it not-ready-for-review. |
| `pr-ready` | Move a draft PR **out of draft** (request review). |
| `set-pr --url URL --number N` | Record PR metadata when a sub-agent/provider opened the PR out of band. |
| `pr-status` | Tracked PR metadata + live verdict / conflict / merge state; flags pull-forward when merged. |
| `pr-watch wait <repo> <pr>` | Block until the PR moves (approved / changes_requested / conflict / mergeable / merged / closed) and wake the caller with a race-proof cursor. |
| `pr-merge <repo> <pr>` | Signal **merge consent** on an approved PR (applies the bound `automerge_label`); the gate merges when satisfied. |
| `pr-complete` | Reconcile the worktree after its PR merged — fast-forward past the squash-merge (or rebase), dropping the local commits the squash already absorbed. |

**Merge consent is a deliberate, post-approval act.** Opening a PR invites
*review*; only after an approval does the author run `pr-merge` to authorize the
*merge*. "Please review" is never silently "ship it."

**An opened PR is final by default.** Land everything *before* `create-pr` — a
late push races the merge. If you need to keep iterating, open it as a
`--draft` and run `pr-ready` when it's genuinely ready for review.

A typical PR-mode loop:

```bash
agent-worktrees create-pr --title "add the thing"      # squash + open PR
agent-worktrees pr-watch wait owner/name 123 --json     # sleep until a review lands
# ...on approval...
agent-worktrees pr-merge owner/name 123                  # signal consent → gate merges
agent-worktrees pr-watch wait owner/name 123 --until merged --json
agent-worktrees pr-complete                              # reconcile the worktree forward
```

See [`references/pr-workflow.md`](../skills/worktree/references/pr-workflow.md)
for head-scheme/branch topology, provider delegation, comment-thread handling,
and the disposition modes.

### Held and follow-up PRs

- **Held / draft.** A PR you're not ready to have reviewed is a **draft**
  (`create-pr --draft`); `pr-ready` releases it. A repo may also bind
  `hold_labels` (e.g. a "needs-rebase" or explicit block) that keep consent from
  applying — `pr-status` surfaces them.
- **Follow-up PRs.** A merged PR that missed a piece doesn't get reopened —
  land a **follow-up** PR. Short review cycles + a cheap second PR beat one giant
  anxiety-preserved PR.

### Serial vs parallel PRs

- **Serial (default, recommended).** From one worktree, open a PR, see it
  through, then open the next. Sequential PRs avoid tangled local branch state.
- **Parallel.** Multiple in-flight PRs are supported (`create-pr --new` forces a
  fresh head branch), but only when each PR leaves the default branch green on
  its own. Prefer independent worktrees for genuinely parallel work.

The details of both — including how a `--new` PR picks its head ref — are in
[`references/pr-workflow.md` § Multiple PRs per worktree](../skills/worktree/references/pr-workflow.md).

## 4. Finalize, prune, and resume

`finalize` marks a landed worktree **prune-eligible**; the actual removal
happens once the session is idle, or explicitly via `cleanup`:

```bash
agent-worktrees cleanup --clean                    # remove completed + gone worktrees
agent-worktrees cleanup --clean --include-unused   # also purge commit-less worktrees (asks first)
```

Cleanup never auto-purges an `unused`/`convo` worktree — a commit-less worktree
may still hold planning or conversation. For a `gone` worktree the branch is
deleted only when its content is verified on the default branch.

**Finalized is not terminal.** Until it is pruned, a finalized worktree still
appears in the picker and can be resumed to carry follow-up work — open a fresh
PR for the new change. If a PR-mode worktree was already torn down (the `detach`
disposition), recover it via
[`references/pr-workflow.md` § Recovering a PR after teardown](../skills/worktree/references/pr-workflow.md).
When in doubt, just `create` a fresh worktree and continue there.

## Command map

| Stage | Direct-push | PR mode |
|-------|-------------|---------|
| Create | `resolve` / `create [--json]` / `resolve --new` | same |
| Work | commit on the worktree branch | same |
| Land | `push-changes --title` | `create-pr` → `pr-watch` → `pr-merge` → `pr-complete` |
| Draft/hold | — | `create-pr --draft` → `pr-ready` |
| Inspect | `status` | `pr-status` |
| Clean up | `finalize` → `cleanup` | `finalize` → `cleanup` |

## See also

- [Getting Started](getting-started.md) — install, register, first session.
- [The Worktree Picker](picker.md) — the interactive launcher the lifecycle
  starts from (screen, navigation, resume/create/clean/sync).
- [Multiplexed Sessions](mux.md) — why a launched session runs in tmux/psmux
  (persistence, detach/rejoin) and when to skip the mux.
- [CLI Reference](cli-reference.md) — every subcommand and flag.
- [Configuration Reference](config-reference.md) — the `pr:` block
  (`enabled` / `required` / `provider` / `strategy` / `automerge_label` /
  `hold_labels` / …) and where it resolves (machine-local vs in-repo).
- [`worktree` skill](../skills/worktree/SKILL.md) — the in-session operator flow
  and its [PR-workflow reference](../skills/worktree/references/pr-workflow.md).
- [Architecture](architecture.md) — worktree internals and the picker.
