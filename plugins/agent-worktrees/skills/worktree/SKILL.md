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

> **Always use the payload command.** Replace `<agent-worktrees catalog argv[0]>`
> below with the exact `argv[0]` supplied by the session catalog. Quote it at
> each shell call site. See [references/reference.md](references/reference.md)
> § Payload Command Resolution for the enumeration fallback and quoting detail
> when session-start hooks didn't publish the catalog.

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
to replicate finalization with raw git commands.

```
# CORRECT -- always use the payload command
<agent-worktrees catalog argv[0]> push-changes --title "Fix auth regression"
<agent-worktrees catalog argv[0]> finalize
<agent-worktrees catalog argv[0]> status
<agent-worktrees catalog argv[0]> cleanup --clean

# WRONG -- never do any of these
python -m worktree_manager mark-complete ...
python -m agent_worktrees push-changes ...
git rebase && git checkout master && git merge ...
```

Cross-machine worktree inspection uses the project binstub's
**agent-worktrees commands**, never guessed checkout paths or reconstructed
worktree naming conventions — see
[references/reference.md](references/reference.md) § Cross-Machine Inspection
for the enumerate-then-resolve procedure.

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
  `<agent-worktrees catalog argv[0]> push-changes`

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
<agent-worktrees catalog argv[0]> push-changes --title "Fix auth regression"
```

This squashes worktree commits into one, rebases onto the upstream default
branch, validates core files, merges to local default branch, and pushes to
origin.

> **Squash is a hard invariant.** If the pre-squash step fails, `push-changes`
> **aborts with a non-zero exit and surfaces the underlying reason** -- it
> never silently falls back to pushing the individual commits, which would
> pollute the shared default branch irreversibly. Resolve the cause and
> retry. For the rare case where individual commits are genuinely intended,
> pass `--allow-unsquashed` to opt in explicitly.

### Step 2: Finalize (validate and clean up)

```
<agent-worktrees catalog argv[0]> finalize
```

Validates (non-mutating) that the branch's content is on the upstream
default branch. If it is, the worktree is **finalized** (permissions merged,
tracking marked `finalized`); the git branch and folder are removed **only
when the worktree is idle** (no live session). Finalizing from inside the
session (the >90% case) intentionally **leaves the branch/folder in
place** for later cleanup -- this is the normal, expected outcome, not a
failure. If content is NOT on the default branch, it fails and tells you to
run `push-changes` first.

**`finalize` never deletes a worktree out from under a running session,
force-removes a folder or branch, squashes, rebases, or pushes.** Its only
job is to guarantee the branch's work is merged to the default branch --
always safe to call.

### Decision table

| Situation | Command |
|-----------|---------|
| **Done with this worktree** -- normal sign-off | `<agent-worktrees catalog argv[0]> push-changes --title "..."` then `<agent-worktrees catalog argv[0]> finalize` |
| **Handoff consumed or phase/PR landed, but the parent objective has actionable work** | Keep driving the next roster item; do **not** finalize |
| **Set/update title only** -- keep working | `<agent-worktrees catalog argv[0]> push-changes --title "..." --title-only` |
| **Work was already pushed** (by a previous session or push-changes) | `<agent-worktrees catalog argv[0]> finalize` (succeeds immediately) |
| **Previous push-changes failed** (network, rebase conflict) | Fix the issue, then retry `<agent-worktrees catalog argv[0]> push-changes` |
| **Unsure what state the worktree is in** | `<agent-worktrees catalog argv[0]> status` first, then decide |

### Make a successful `finalize` the last thing you do

When the user asks you to finalize/wrap up/sign off, or you judge your
assigned goal/effort/task complete, drive `finalize` to a **successful
result** as the last action before ending your turn. `finalize` is a
**fail-fast assertion**, not a gate you route around: it re-validates
non-mutating checks and names the *specific* blocker on failure. Read the
blocker, clean it up directly (resolve the cited obligation, retry
`push-changes`, settle the named claim), then call `finalize` again --
repeat until a clean pass or a genuine operator-only blocker. **Never
force-release a claim and never improvise a hand-off** to make `finalize`
pass -- a claim only moves to a successor when the user explicitly names
one; absent that, close the obligation yourself.

A `finalize` that reports content already on the default branch **is** the
successful result even when the branch/folder were left in place because the
session is still live -- that is normal, not a failure to loop on.

### Finalize is gated on outbound resource obligations

`finalize` holds a worktree **accountable** for what it allocated. If this
worktree still owns **unsettled** outbound resources -- a cross-repo
worktree, a borrowed CodeSpace/container, or a bridge session it brought
into being -- the **obligation gate blocks finalize by default**, refusing
*before* any destructive step. **Resolve each named obligation through its
own lifecycle -- never bypass the gate and never force-release a claim.**
**Never run an unfiltered `claims cleanup --apply`** (with no worktree-id
selector) to clear a blocker -- it acts on the **entire orphanage**,
including unrelated agents' resources, not just this worktree's; any
`claims cleanup --apply` must be scoped to the specific
`<source-worktree-id>` and only after reviewing its selective dry-run.
Only after the operator explicitly names a recipient may an unclosable
child be re-homed via `finalize --abandon --handoff-to <recipient>` (refused
without `--handoff-to`); creating-agent cleanup remains the default. See
[references/obligations.md](references/obligations.md) for the itemized
per-resource-kind resolution procedure (cross-repo worktrees, CodeSpaces,
bridge sessions, crashed holders, out-of-band resources you must journal
yourself) and the claims-ledger commands.

## PR Workflow (PR mode)

Some repos opt into a **pull-request workflow** instead of direct-push
finalization (config `pr.enabled: true`). **Check the target repo's flow
before signing off -- it is not the same everywhere:**

```
<agent-worktrees catalog argv[0]> get pr-profile      # direct | pr-human-merge | pr-agent-merge | pr-self-merge
<agent-worktrees catalog argv[0]> get pr-enabled      # "true" or "false"
<agent-worktrees catalog argv[0]> get pr-required     # "true" -> direct-to-default-branch is blocked
```

- **`direct`** -- no PR flow; `finalize` lands to the default branch.
- **`pr-human-merge`** -- a **human** approves + merges: `create-pr` / `pr-watch` / `pr-status` / `pr-complete`; **`pr-merge` does not apply**.
- **`pr-agent-merge`** -- after approval the author runs `pr-merge` to signal consent and the review gate merges.
- **`pr-self-merge`** -- the submitter merges directly once checks/reviews allow. On GitHub, never approve your own PR. Use `pr-merge <pr> --now` when ready; bare `pr-merge` refuses in this profile.

The verbs are **self-describing** and refuse (naming the reason) when they
don't apply to the current profile -- believe them; never hand-merge or
escalate past a refusal. **An opened PR is final by default** (land
everything first, or open as `--draft`), and **driving the PR through to
actual merge is the default conduct for every profile**. Full profile
detail, config resolution, `create-pr` mechanics, draft PRs, and multiple
PRs per worktree: [references/pr-workflow.md](references/pr-workflow.md).

## Committing and Pushing

**Never run a bare `git push` from a worktree branch.** A bare push
creates a `worktree/*` branch on the remote, which should never exist.

**Do not auto-push.** Pushing only happens via worktree finalization, or
when the user explicitly says "push" (`git push origin HEAD:<default-branch>`
-- always the default branch, never another remote/branch unless specified).

**Commit regularly** to the worktree branch -- worktree branches are
disposable, commits stay local until finalization. **Only commit work
belonging to this worktree** -- do not stage or commit unrelated files that
happen to be present.

Finalization squashes all worktree commits into **one** commit, rebases
onto the default branch, and fast-forward merges -- **standard merge
commits are never used**. See [references/reference.md](references/reference.md)
§ Finalization Merge Strategy for the exact squash/rebase/ff-merge
mechanics and what that means for conflict resolution.

## Quick Reference

Context resolves **the way git does — from the current directory**: the
target worktree and its anchor repo are discovered from CWD, not ambient
env vars or branch names. Before `create-pr`, prepare a human-readable body
with the target repository's required sections (Intent/Changes/Validation);
hidden source metadata never substitutes for reviewable intent.

| Action | Command |
|--------|---------|
| **Push changes to the default branch** (normal sign-off step 1) | `<agent-worktrees catalog argv[0]> push-changes --title "desc"` |
| **Finalize** (validate + clean up, step 2) | `<agent-worktrees catalog argv[0]> finalize` |
| **PR mode: create + push a feature branch** | `<agent-worktrees catalog argv[0]> create-pr --title "desc" --body-file <path>` |
| **PR mode: record PR metadata** (after sub-agent opens it) | `<agent-worktrees catalog argv[0]> set-pr --url URL --number N` |
| **PR mode: show tracked PR state** | `<agent-worktrees catalog argv[0]> pr-status` |
| **Check the target repo's PR flow** | `<agent-worktrees catalog argv[0]> get pr-profile` |
| Set/update title only | `<agent-worktrees catalog argv[0]> push-changes --title "desc" --title-only` |
| Show worktree git status | `<agent-worktrees catalog argv[0]> status` |
| List worktrees for cleanup | `<agent-worktrees catalog argv[0]> cleanup` |
| Clean completed worktrees | `<agent-worktrees catalog argv[0]> cleanup --clean` |
| Also clean unused worktrees | `<agent-worktrees catalog argv[0]> cleanup --clean --include-unused` |
| Help | `<agent-worktrees catalog argv[0]> --help` |

See [references/reference.md](references/reference.md) § Binstub and
Project-Registration Notes for `register`'s cwd-is-the-only-implicit-locator
exception and project-binstub ownership details.

## Cleanup Procedure

When the user asks to clean up worktrees:

1. **Run default cleanup** -- `<agent-worktrees catalog argv[0]> cleanup --clean`
   removes only `completed` worktrees (merged) and `gone` worktrees (path
   missing, branch content verified merged first).
2. **Report unused count** -- worktrees with no commits but possibly
   planning/conversation history are preserved and reported.
3. **Ask the user** whether to also purge unused worktrees --
   `cleanup --clean --include-unused`. **Never auto-purge unused worktrees
   without asking** -- a worktree may look "unused" if the session involved
   only questions or planning with no commits yet.

**Never blanket-`--force` a batch of dirty worktrees.** `cleanup` refuses a
worktree with uncommitted changes *on purpose* -- that refusal protects real,
unlanded work. Treating "dirty" as a synonym for "safe to force" throws away
the one signal that distinguishes real work from noise, and one worktree's
noise doesn't prove the same of another's. **For every worktree reported
`dirty`, resolve it individually** -- read its actual diff (including staged
and untracked paths), land real work through the repo's own flow (PR or
direct, per `pr-profile`), and discard only confirmed noise file-by-file
(never a bare `git clean -fd`). **Before actually discarding or force-clearing
anything, read [references/cleanup-details.md](references/cleanup-details.md)**
for the exact per-path discard commands (staged vs. untracked differ), the
fresh-safety-revalidation guarantees non-forced cleanup provides, and the
narrow, single-worktree `--force` exception -- the policy above tells you
*what* to do; that reference has the *exact commands* so you don't improvise
syntax.

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

## Cross-Worktree Safety

**CRITICAL: Never modify a sibling worktree with an active session.**
Read-only inspection (`git -C <path> log/status/rev-parse/merge-base`) is
always safe; **any mutating git operation on a sibling requires explicit
user authorization** -- ask first, even if the fix looks trivial, and
confirm which worktree and what operation before proceeding.

Worktrees with a live Copilot session always show as **active** regardless
of git state -- cleanup skips them and finalize defers destruction (this is
expected, not a failure). See [references/reference.md](references/reference.md)
§ Active Worktree Safety and § Session Detection for the full liveness-signal
model, titles, and diagnosing an unretired handoff predecessor pane (**never
manually kill a pane/process** to work around a suspected stuck handoff --
use `handoffs-check` instead).

## Resource Leases (atomic, cross-machine, same-harness)

The payload-local `lease` operation is the harness's one atomic primitive
for exclusive, cross-machine access to a scarce shared resource (a
CodeSpace, cross-repo worktree, container, bridge session):

```bash
<agent-worktrees catalog argv[0]> lease acquire <kind> <key> --holder <ref> [--ttl N]
<agent-worktrees catalog argv[0]> lease release <kind> <key> --token <oid>
<agent-worktrees catalog argv[0]> lease inspect <kind> <key>
```

See [references/leases.md](references/leases.md) for the full primitive
(fencing tokens, renew, the two-tier consumer model, degrade-safe behavior)
-- most agents only need `acquire`/`release`/`inspect`.

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

## See Also

- [references/pr-workflow.md](references/pr-workflow.md) -- full PR-mode reference
- [references/obligations.md](references/obligations.md) -- finalize's outbound-resource obligation gate
- [references/cleanup-details.md](references/cleanup-details.md) -- per-worktree dirty resolution and cleanup safety guarantees
- [references/leases.md](references/leases.md) -- the resource-lease primitive
- [references/reference.md](references/reference.md) -- payload-command resolution, cross-machine inspection, finalization merge mechanics, session detection, titles
