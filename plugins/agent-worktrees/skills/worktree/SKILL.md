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

> **Before you start — use the payload-local session command.**
> The agent-worktrees session command catalog supplies an exact `argv` prefix
> owned by this plugin payload. Replace
> `<agent-worktrees catalog argv prefix>` in direct runtime operations below with
> its shell-ready rendering: quote each prefix element separately and prepend
> `&` in PowerShell. Never join or re-parse the prefix, search `PATH`, or
> substitute a same-named command from another payload.
> Project binstubs and commands explicitly labeled as
> management boundaries remain distinct attributable entry points. In
> PowerShell, invoke the catalog prefix as
> `<agent-worktrees catalog argv prefix> <args>`.
> Cross-plugin `<agent-codespaces catalog argv prefix>` examples use that plugin's
> exact catalog prefix under the same rendering rule.
>
> The payload command provisions its runtime on first use and works without
> the interactive launcher. If session-start hooks did not publish the
> catalog, enumerate installed agent-worktrees payloads and fail unless
> exactly one exists. Invoke that payload's
> `bin/payload/agent-worktrees` on POSIX or
> `bin\payload\agent-worktrees.cmd` on Windows directly; never choose the first
> match from multiple marketplaces or stamp a global wrapper just to recover
> an in-session command.

This system uses **git worktrees** to isolate concurrent Copilot CLI
sessions. Each session creates or resumes a worktree — a lightweight copy
of the repo with its own branch, working directory, and index.

## The Worktree Owns the Objective; Sessions Are Relay Legs

A worktree's active objective can span many Copilot sessions, context windows,
phases, commits, and pull requests. Session succession is not worktree
completion.

- Consuming a handoff starts a new relay leg; it is not evidence that the
  worktree is done. Re-read the founding request, continuing objective,
  successor work roster, and any cited effort or issue.
- When `effort-focus show --json` reports a valid open binding, the cited effort
  README is the canonical objective and completion gate. A handoff should carry
  its pointer, the next slice, and only the immediate relay delta rather than
  copying the effort's request, plan, or journal.
- Keep driving every actionable next phase already permitted by that objective,
  as far as the current context and available work allow. Do not wait for the
  user to restate or reauthorize known work merely because a phase or PR landed.
- A single session may consume a handoff, drive many additional slices or
  phases, and hand off again when context pressure returns. Carry the same
  objective and remaining roster forward.
- Explicit user scope boundaries, approval gates, and safety confirmations
  still apply. Relentless continuation means pursuing authorized work, not
  bypassing decisions that belong to the user.

Before finalizing, re-check the parent objective rather than the most recent
milestone. A consumed or completed handoff task, a completed phase, a clean git
status, or a merged PR is not proof that the worktree has no remaining work.
An effort-bound worktree remains responsible until the effort is `Done`, every
Plan and Validation Plan checkbox is resolved, deferred or blocked work is
transferred with the checked form
`Deferred to \`<tracked objective>\`: ...` or
`Blocked; transferred to \`<tracked objective>\`: ...`, required review/merge
gates have passed, and the effort is archived and released.

## Am I in a Worktree?

Check the branch name:

```powershell
$branch = git rev-parse --abbrev-ref HEAD
if ($branch -like 'worktree/*') { "In worktree: $branch" }
```

If on the default branch or another non-`worktree/` branch, you're in the
anchor repo (base-repo mode).

## ⛔ Always Use the Payload Command

**All direct worktree lifecycle operations MUST use the catalog's exact
payload command.** Never call `python -m worktree_manager`, `python -m
agent_worktrees`, or any other Python invocation directly. Never attempt
to replicate finalization with raw git commands. The session catalog makes the owning payload command available inside a
worktree session.

```
# CORRECT -- always use the payload command
<agent-worktrees catalog argv prefix> push-changes --title "Fix auth regression"
<agent-worktrees catalog argv prefix> finalize
<agent-worktrees catalog argv prefix> status
<agent-worktrees catalog argv prefix> cleanup --clean

# WRONG -- never do any of these
python -m worktree_manager mark-complete ...
python -m agent_worktrees push-changes ...
$env:PYTHONPATH = "..."; python -m worktree_manager ...
git rebase && git checkout master && git merge ...
```

## Cross-machine inspection -- enumerate first, then resolve suffixes

Use the project binstub's **agent-worktrees commands** to inspect worktrees on
another machine. Do not discover them by listing guessed checkout directories or
by reconstructing paths from naming conventions.

Operators often identify a worktree by only its four-character display suffix
(for example, `0541`). Treat that as a lookup key, **not** as a complete
worktree or Copilot session id:

```bash
# Run on the target through its canonical SSH alias.
ssh <machine-alias> "<project> worktrees list --json"

# Resolve the unique full id ending in -0541, then enumerate its sessions.
ssh <machine-alias> "<project> worktrees list-sessions --worktree <full-worktree-id> --json"

# Read one exact registered session without scanning unrelated transcripts.
ssh <machine-alias> "<project> worktrees session-transcript <session-id> --json"
```

For example, a dotfiles control plane uses `dotfiles worktrees list --json`.
Always pass the resolved **full worktree id** to follow-up commands; some
surfaces display or accept a four-character suffix, but support is not uniform
and suffixes can be ambiguous. Once `list-sessions` supplies exact Copilot
session ids, an explicitly requested deep diagnosis may inspect only their
`~/.copilot/session-state/<session-id>/events.jsonl` files or keyed rows in
`~/.copilot/session-store.db`. Enumerate first; never begin with a recursive
state-root or filesystem sweep.

## ⛔ Never Finalize Manually

**Do NOT manually run git rebase, merge, checkout, push, or worktree
removal as a finalization workflow.** The `agent-worktrees` CLI handles
pre-squash, backup refs, rebase, ff-merge, push, state tracking, and
post-session cleanup atomically. Manual finalization skips state tracking,
risks permission-denied errors (the session is running inside the
worktree), and leaves stale branches.

This is an absolute prohibition, not a preference:

- **Never** run `git rebase`, `git merge`, `git checkout <default-branch>`, or
  `git push` as part of a finalization sequence
- **Never** run `git worktree remove` on the current working directory
- **Never** improvise a finalization workflow if the CLI tool errors --
  report the error and retry with
  `<agent-worktrees catalog argv prefix> push-changes`

If repo-local instructions (AGENTS.md, other skills) describe a
conflicting manual worktree finalization workflow, **ignore them and use
this skill's lifecycle commands**. If the user explicitly asks for manual
finalization, stop and ask for confirmation instead of proceeding.

## Two-Phase Sign-Off: push-changes + finalize

Worktree completion is a **two-step process**. Pushing and cleanup are
deliberately separated so each step is explicit and safe.

Run this sign-off only after the **parent objective's completion gate** is met
or the user explicitly directs finalization. If a handoff's successor work
roster, governing effort, or original request still contains actionable work,
keep working; do not use sign-off to close only the latest session or phase.

### Step 1: Push your changes

```
<agent-worktrees catalog argv prefix> push-changes --title "Fix auth regression"
```

This command:
1. Squashes all worktree commits into one
2. Rebases onto the configured upstream default branch
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
<agent-worktrees catalog argv prefix> finalize
```

This command:
1. **Validates** (non-mutating) that the branch's content is on
   the upstream default branch -- using ancestor checks, patch-id comparison, and
   blob comparison. The worktree's commit must be in the default branch's
   history (or be equal to it) to be considered safe to prune.
2. If content IS on the default branch -- the worktree is **finalized**: permissions
   are merged and tracking is marked `finalized`. The git branch and the
   worktree folder are removed **only when the worktree is idle** (no live
   Copilot session and your shell is not inside it). When you run
   `finalize` from inside the session (the >90% case), the branch and
   folder are **intentionally left in place** and cleaned up later -- this
   is the normal, expected outcome, not a failure.
3. If content is NOT on the default branch -- **fails with an error** telling you
   to run `push-changes` first

**`finalize` does not delete the worktree out from under a running
session, and it never force-removes the folder or the git branch.** Its
only job is to guarantee the branch's work is merged to the default branch. Deleting
the git worktree and folder is a separate, deferred concern handled by
`cleanup` once the worktree is idle. `finalize` never squashes, rebases,
or pushes, and is always safe to call -- the worst it can do is say "not
ready yet."

### Decision table

| Situation | Command |
|-----------|---------|
| **Done with this worktree** -- normal sign-off | `<agent-worktrees catalog argv prefix> push-changes --title "..."` then `<agent-worktrees catalog argv prefix> finalize` |
| **Handoff consumed or phase/PR landed, but the parent objective has actionable work** | Keep driving the next roster item; do **not** finalize |
| **Set/update title only** -- keep working | `<agent-worktrees catalog argv prefix> push-changes --title "..." --title-only` |
| **Work was already pushed** (by a previous session or push-changes) | `<agent-worktrees catalog argv prefix> finalize` (succeeds immediately) |
| **Previous push-changes failed** (network, rebase conflict) | Fix the issue, then retry `<agent-worktrees catalog argv prefix> push-changes` |
| **Unsure what state the worktree is in** | `<agent-worktrees catalog argv prefix> status` first, then decide |

### Finalize is gated on outbound resource obligations

`finalize` holds a worktree **accountable** for what it allocated. If this
worktree still owns **unsettled** outbound resources -- a cross-repo worktree, a
borrowed CodeSpace/container, or a bridge session it brought into being -- the
**obligation gate blocks finalize by default** (`AGENT_WORKTREES_OBLIGATION_GATE`
is `block`), refusing *before* any destructive step so the worktree stays intact.
The error lists each unsettled obligation. Resolve it -- don't bypass:

- **A cross-repo worktree you created** -- finalize *it* first; its finalize
  flips this worktree's claim to `at-rest` automatically (no manual step).
- **A borrowed CodeSpace/container** -- merge or move its work off-box, then
  disconnect (the disconnect hook stamps it `at-rest` **and mirrors that onto the
  shared lease**, so the settle is visible cross-machine), or run
  `<agent-codespaces catalog argv prefix> finalize <name>`.
- **A bridge session** -- drive its worktree to final.
- **A crashed/gone holder that never settled** --
  `<agent-worktrees catalog argv prefix> claims sweep`
  (dry-run) then `--apply` explicitly reclaims provably-gone-and-safe
  obligations. Finalize never auto-reclaims creator ownership. A stale *codespace*
  obligation -- including one owned on a different machine -- is reclaimed by
  reading the disposition mirror off the shared lease, so a clean disconnect
  anywhere unblocks it.
- **Genuinely cannot close the children yourself** -- ownership still stays with
  the creating agent. Do **not** choose a handoff unilaterally: ask the operator.
  Only after the operator explicitly names another recipient/flow may you run
  `<agent-worktrees catalog argv prefix> finalize --abandon --handoff-to <recipient-or-flow>`.
  `--abandon` without `--handoff-to` is refused. The command re-homes the
  obligations to a durable orphanage with that recipient recorded; it never
  drops them. **Creating-agent cleanup is the default; affirmative handoff is the
  only exception.** The creating agent remains responsible until the named flow
  accepts the transfer. Immediately:
  1. save the finalizing worktree id printed in the orphan entries;
  2. run `<agent-worktrees catalog argv prefix> claims cleanup <source-worktree-id>` as a dry-run;
  3. investigate each selected resource (child git/PR state, CodeSpace work,
     active sessions), then finalize/settle it through its owning lifecycle;
  4. use `<agent-worktrees catalog argv prefix> claims cleanup <source-worktree-id> --apply` only for
     the selected resources you intend to reclaim, and rerun the selective
     dry-run until it reports no matches.

  Never run unfiltered `claims cleanup --apply` merely to clear your blocker:
  with no selector it acts on the **entire orphanage**, including unrelated
  agents' resources. Do not report the parent fully closed while its selected
  orphan entries remain; if the named recipient cannot accept them, return to
  the operator rather than inventing a different flow.

Inspect the ledger any time with
`<agent-worktrees catalog argv prefix> claims show`. Creator
ownership is invariant: `AGENT_WORKTREES_OBLIGATION_GATE=warn|off` does not
permit releasing unsettled resources without the affirmative handoff above.

#### Resources you create **out-of-band** aren't auto-journaled — claim them by hand

Auto-journaling only covers resources created through the blessed paths: a
worktree via `<agent-worktrees catalog argv prefix> create`/bridge dispatch, and a
CodeSpace via `<agent-codespaces catalog argv prefix> ssh`. Anything you bring into being **another way** is invisible
to the finalize gate unless you journal it yourself — so `finalize` would let this
worktree vanish while that work is still open. Journal it as a claim on **this**
worktree, and settle it when it's done:

```
# You opened a cross-repo / ADO PR out-of-band (e.g. an example-web PR created with
# the AZ CLI / ADO REST / gh, NOT the payload-local create-pr operation):
<agent-worktrees catalog argv prefix> claims add pr <pr-url-or-id> \
  --owner-ref "$(<agent-worktrees catalog argv prefix> get owner-ref)"
# ...later, when that PR merges or closes:
<agent-worktrees catalog argv prefix> claims settle <pr-url-or-id>     # or: claims release <pr-url-or-id> --remove
```

The gate is **kind-agnostic** — a `pr` (or `codespace`/`container`/`workdir`)
claim blocks finalize exactly like a worktree claim, so this keeps you honest
about unfinished cross-repo work. But the reclaim **sweep spares `pr`-kind
claims** (it can't prove an arbitrary PR safe), so a `pr` claim is **manual to
settle** — there is no auto-reclaim. Kinds: `worktree|codespace|container|ssh|
workdir|pr`. *(Auto-journaling `pr` claims + settle-on-merge is tracked in
example-operator/dotfiles#1351.)*

### When the user says "finalize", "wrap up", "sign off", or "done with this"

They mean: push changes and clean up. Run both steps:

```
<agent-worktrees catalog argv prefix> push-changes --title "concise description of the work"
<agent-worktrees catalog argv prefix> finalize
```

If no title is obvious, omit `--title` -- do not pause to ask unless the
user requested one.

### Reading the output

After running `push-changes`, **read the output carefully**:
- If it says push failed or status reverted to orphaned, report that to
  the user. Do not manually recover.
- If it succeeds, proceed to
  `<agent-worktrees catalog argv prefix> finalize`.

After running `finalize`, **read the output as success unless it errors.**
If it reports that content is on the default branch, finalize succeeded -- even when it
also says the branch/folder were left in place because a session is still
live. That deferral is the normal outcome of finalizing from inside the
session; **do not present it as a bug or as cleanup having failed.** Only if
it says content is *not* on the default branch did something go wrong -- in that case the
push did not succeed or was not run, so retry `push-changes` first.


## PR Workflow (PR mode)

Some repos opt into a **pull-request workflow** instead of direct-push
finalization (config `pr.enabled: true`). **Check the target repo's flow
before signing off -- it is not the same everywhere:**

```
<agent-worktrees catalog argv prefix> get pr-profile      # direct | pr-human-merge | pr-agent-merge | pr-self-merge
<agent-worktrees catalog argv prefix> get pr-enabled      # "true" or "false"
<agent-worktrees catalog argv prefix> get pr-required     # "true" -> direct-to-default-branch is blocked
<agent-worktrees catalog argv prefix> get pr-provider     # gitea | github | azure-devops
```

The **profile** tells you how the repo lands work and which `pr-*` verbs apply:

- **`direct`** -- no PR flow; `finalize` lands to the default branch.
- **`pr-human-merge`** -- PR-gated, but a **human** approves + merges: use
  `create-pr` / `pr-watch` / `pr-status` / `pr-complete`; **`pr-merge` does not
  apply** (there is no consent label to signal).
- **`pr-agent-merge`** -- PR-gated with an auto-merge consent label bound: after
  approval the author runs `pr-merge` to signal consent and the review gate
  merges. The full `pr-*` family applies.
- **`pr-self-merge`** -- PR-gated and the submitter is authorized to merge
  directly: use `create-pr` / `pr-watch` / `pr-status`, then
  `pr-merge <pr> --now` when the PR is ready. Bare `pr-merge` deliberately
  refuses in this profile.

The verbs are **self-describing**: `pr-status` prints the `flow:` profile, and
`pr-merge` refuses (naming the reason + the right next step) on a repo where it
does not apply. Believe them -- never hand-merge or escalate past a verb that
says it does not apply.

In **direct mode**, use the two-phase `push-changes` + `finalize` flow above.
In **PR mode**, sign-off becomes `create-pr` -> review -> merge ->
`pr-complete`/`finalize`, and `push-changes` targets the *feature* branch, never
the default branch.
**An opened PR is final by default** -- land everything before `create-pr` (or
open it as a draft with `--draft`, then `pr-ready` when ready for review), since
a late push races the merge.

The full PR-mode reference -- profiles + verb applicability, config resolution
(machine-local vs in-repo), `create-pr` auto-open + attribution + labels, the
disposition modes (keep-alive / detach), draft PRs, and multiple PRs per
worktree -- is in [references/pr-workflow.md](references/pr-workflow.md).

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

Direct runtime commands use the session catalog's exact payload command;
project binstubs remain attributable project entry points. Never call Python
modules directly. Context resolves **the way git does — from the current
directory**: the target worktree and its anchor repo are discovered from CWD
(not from ambient environment variables or branch names). A project binstub
(or `--project <name>`) names a specific project, which means *operate as if
CWD were that project's anchor repo* — so you can act on another repo's
worktrees from anywhere without env-var contamination.

Project binstubs pin the payload that created them and carry an ownership
receipt. A different payload cannot silently overwrite one; deliberate
ownership transfer uses the current payload command's
`reconcile-binstubs --transfer <project>` operation.

> **`register` (adopt) is the exception — cwd is the only *implicit* locator.**
> Because a project binstub / `--project <name>` resolves an *already-adopted*
> project, those levers don't exist for the repo you're about to adopt.
> `<agent-worktrees catalog argv prefix> register <name>` therefore takes the repo **path from cwd** (the
> git root of the current directory → its anchor) unless you name one explicitly;
> `<name>` is only the project **label**. So run `register` **from inside the
> target repo's checkout**, or pass `--repo-dir <path>` (or use `repos add <name>
> <path>`). Running `register <name>` from a *different* repo silently adopts
> *that* repo's path under `<name>`.

| Action | Command |
|--------|---------|
| **Push changes to the default branch** (normal sign-off step 1) | `<agent-worktrees catalog argv prefix> push-changes --title "desc"` |
| **Finalize** (validate + clean up, step 2) | `<agent-worktrees catalog argv prefix> finalize` |
| **PR mode: create + push a feature branch** | `<agent-worktrees catalog argv prefix> create-pr --title "desc"` |
| **PR mode: record PR metadata** (after sub-agent opens it) | `<agent-worktrees catalog argv prefix> set-pr --url URL --number N` |
| **PR mode: show tracked PR state** (reconciles vs. provider; flags pull-forward when merged) | `<agent-worktrees catalog argv prefix> pr-status` |
| **Check the target repo's PR flow** (direct / human-merge / agent-merge / self-merge) | `<agent-worktrees catalog argv prefix> get pr-profile` |
| **Check if PRs are required** (direct-to-default-branch blocked) | `<agent-worktrees catalog argv prefix> get pr-required` |
| Set/update title only | `<agent-worktrees catalog argv prefix> push-changes --title "desc" --title-only` |
| Show worktree git status | `<agent-worktrees catalog argv prefix> status` |
| List worktrees for cleanup | `<agent-worktrees catalog argv prefix> cleanup` |
| Clean completed worktrees | `<agent-worktrees catalog argv prefix> cleanup --clean` |
| Also clean unused worktrees | `<agent-worktrees catalog argv prefix> cleanup --clean --include-unused` |
| Help | `<agent-worktrees catalog argv prefix> --help` |

## Cleanup Procedure

When the user asks to clean up worktrees:

1. **Run default cleanup** —
   `<agent-worktrees catalog argv prefix> cleanup --clean` removes
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
   `<agent-worktrees catalog argv prefix> cleanup --clean --include-unused`.

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
| `pushed` | Changes pushed to the upstream default branch, awaiting finalization |
| `completed` | All content merged to default branch, safe to clean |
| `gone` | Worktree directory missing |
| `orphan` | No merge base with upstream |
| `finalized` | Merged to default branch, worktree removed |

## Worktree Titles

Titles appear in the picker for easier identification. Resolution order:

1. **Explicit title** — from the `title` field in worktree YAML. Once set
   (via `<agent-worktrees catalog argv prefix> push-changes --title`), this wins.
2. **Session summary** — auto-derived from the most recent Copilot CLI
   session summary for the worktree path.
3. **None** — just the worktree ID and age.

```powershell
# Set title without pushing (worktree stays active)
<agent-worktrees catalog argv prefix> push-changes --title "Fix auth regression" --title-only

# Push changes and set title
<agent-worktrees catalog argv prefix> push-changes --title "Fix auth regression"
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
locks can be reclaimed with the `reclaim` flow above.

## Resource Leases (atomic, cross-machine, same-harness)

The payload-local `lease` operation is the harness's **one atomic primitive** for exclusive,
cross-machine access to any scarce shared resource — a CodeSpace, a cross-repo
worktree, a container, a bridge session — so two agents on two machines never
collide. It is **ref shenanigans only** in the harness's own repo: hidden
`refs/agent-worktrees/leases/v1/<kind>/<key>` refs updated by atomic
compare-and-swap (`--force-with-lease`), no branches, no commits, no service, no
new credential. Each transition appends a synthetic empty-tree metadata commit
whose **OID is the fencing token**; release appends a **tombstone** (ABA-safe);
every read strictly validates linear history.

```bash
<agent-worktrees catalog argv prefix> lease acquire <kind> <key> --holder <ref> [--ttl N]  # atomic CAS
<agent-worktrees catalog argv prefix> lease renew   <kind> <key> --token <oid> [--ttl N]   # keep the grip
<agent-worktrees catalog argv prefix> lease release <kind> <key> --token <oid>             # tombstone
<agent-worktrees catalog argv prefix> lease inspect <kind> <key>                           # current record
<agent-worktrees catalog argv prefix> lease list [--kind <kind>]                           # fabric-wide view
```

- **Holder** = the qualified **ClaimRef** (`machine/project/worktree_id[#session]`),
  from `<agent-worktrees catalog argv prefix> get owner-ref` — directly resolvable for stale-takeover.
- **Store origin** = the resolved lease store repo, from
  `<agent-worktrees catalog argv prefix> get lease-origin` (the `AGENT_WORKTREES_LEASE_ORIGIN` override,
  else the bound control-plane/knowledge repo's origin, else the project's default
  remote). Every agent of one harness resolves the **same** origin — so
  coordination is **same-harness-scoped by construction**. Pin
  `AGENT_WORKTREES_LEASE_ORIGIN` to the control-plane (dotfiles) remote so agents
  in *every* project coordinate through one store.
- **Two-tier consumers.** `agent-codespaces` uses this as the cross-machine **L2**
  authority behind its host-local **L1** claim (see `agent-codespaces:borrowing-codespaces`); a
  live claim on another machine raises a `ClaimConflict` naming the remote holder.
- **Degrade-safe.** Only a definitive lease conflict (exit 3) blocks; a missing
  origin / binstub / token degrades to best-effort, never a hard failure.

## Lifecycle

```
payload command / launcher
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
