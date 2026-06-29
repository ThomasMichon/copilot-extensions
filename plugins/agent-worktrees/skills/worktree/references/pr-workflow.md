# Worktree PR Workflow (PR mode)

Full reference for signing off a worktree through a **pull request** instead of
direct-push finalization. See [SKILL.md](../SKILL.md) for the overview, the
two-phase `push-changes` + `finalize` flow (direct mode), and the safety
rules.

## Contents
- Detecting PR mode + where PR config lives (machine-local vs in-repo)
- `create-pr` (auto-open, attribution marker, labels)
- Dispositions: keep-alive vs detach
- Held PRs (`--hold` / `pr-ready`)
- Multiple PRs from one worktree
- Recovery

---
## PR Workflow (PR mode)

Some repos opt into a **pull-request workflow** instead of direct-push
finalization. A repo is in PR mode when its config sets `pr.enabled: true`.
Check before signing off:

```
agent-worktrees get pr-enabled      # "true" or "false"
agent-worktrees get pr-required     # "true" -> direct-to-master is blocked
agent-worktrees get pr-provider     # gitea | github | azure-devops (empty in direct mode)
```

In **direct mode** (the default), use the two-phase `push-changes` +
`finalize` flow above. In **PR mode**, the flow becomes
`create-pr -> [delegate PR creation] -> finalize`, and `push-changes` targets
the *feature* branch instead of master.

### Where PR config lives (machine-local vs in-repo)

The `pr` block may come from two places:

- **Machine-local** `~/.{project}/config.yaml` under `repos.<name>.pr` --
  the default location, per-machine.
- **In-repo** `<repo-root>/.agent-worktrees.yaml` (committed) under a top-level
  `pr:` block -- **repo-level policy shared across every machine**. When this
  file provides a `pr` block it **overrides** the machine-local one entirely.

Put PR *policy* (enabled/required/provider) in the in-repo file when it should
be identical everywhere -- it then needs no per-machine replication. A
malformed or absent in-repo file safely falls back to machine-local. Either
way, query the effective values with `agent-worktrees get pr-*`.

### `pr.enabled` vs `pr.required` -- available vs mandatory

These are two distinct switches:

- **`pr.enabled: true`** makes the PR path *available*. The mode is **opt-in
  per worktree**: `push-changes`/`finalize` only take the PR path once a PR
  record exists (you ran `create-pr`). A worktree that never runs `create-pr`
  still finalizes **direct-to-master**.
- **`pr.required: true`** makes the PR path *mandatory* (it implies
  `enabled`). The direct-to-master path is **refused**: `push-changes` will
  not push to the default branch, and `finalize` will not prune a worktree
  with unmerged work. The **only** way to land work is `create-pr` -> open PR
  -> merge. There is no local bypass — when `pr-required` is `true`, every
  worktree goes through a PR.

If `agent-worktrees get pr-required` returns `true`, **do not** attempt a
direct `push-changes`/`finalize` for unmerged work — it will be refused. Go
straight to the end-to-end PR loop below.

### End-to-end PR loop (when PRs are required)

The normal, expected flow for a worktree with work to land:

1. **`create-pr`** — squash + push the feature branch (Step 1 below).
2. **Open the PR** via the provider sub-agent (Step 2). Add the provider's
   **auto-merge** affordance if the work should merge automatically once the
   review gate is satisfied.
3. **`set-pr`** — record the PR URL/number (Step 3).
4. **Wait for review.** The PR goes through the repo's review gate (e.g. the
   facility's automated reviewer). Poll the PR via the provider sub-agent for
   review state and comments.
5. **Address feedback** in the **same** worktree (keep-alive disposition):
   edit -> commit on the feature branch -> `push-changes` updates the PR
   branch (never master). Note: new commits **dismiss stale approvals**, so
   re-request / await review again.
6. **Repeat 4–5** until the PR is **approved and merged upstream**. With
   auto-merge set, merge happens automatically on approval; otherwise a human
   merges.
7. **Finalize.** Once the feature branch is safely pushed you *may* `finalize`
   at any point — finalize is decoupled from merge (see below). Choose the
   disposition deliberately (keep-alive to babysit review, detach to let it
   ride).

**Rare opt-out — submit and detach without babysitting review.** An agent may,
when the operator approves, open the PR and immediately `finalize` (detach
disposition), leaving the open PR for asynchronous review + auto-merge rather
than waiting in-session. This still goes through a PR — it is **not** a
direct-to-master bypass. Use it sparingly: the default is to see the PR
through to merge. Never skip the PR entirely when `pr-required` is `true`.

### Branch topology (PR mode)

```
origin/master  <-  worktree/{id}  <-  feature/{slug}-{suffix}
  (upstream)       (local base,        (the PR branch: one squashed
                    tracks master)      work commit, pushed to remote)
```

`worktree/{id}` is a **local-only base** -- it is never pushed. The feature
branch carries the squashed work and is the only thing that reaches the remote.

### Step 1: `create-pr`

```
agent-worktrees create-pr --title "Concise PR title"
```

Squashes the worktree's commits into one, rebases onto upstream, creates the
feature branch off `worktree/{id}`, resets the worktree base to the upstream
tip, checks out the feature branch, and **pushes the feature branch**. Records
`pr.state` and prints the branch, base/head SHAs, and provider. Add `--json`
to capture the metadata, or `--branch NAME` to override the generated name.
Use `--repo owner/name` to target a different repo than the worktree's own,
and `--new` to force a brand-new PR even when one is already open (parallel
PRs). `create-pr` is idempotent -- safe to re-run.

A worktree can track **multiple PRs** over its life. When the active PR is
already **merged or closed**, `create-pr` automatically opens a *fresh* PR
(new branch off the current default-branch tip) instead of reusing the merged
branch -- so landing a second change from the same worktree just works. This
holds even when the prior PR was merged **externally** (e.g. via the provider
API + an auto-merge label, without `finalize`/`pr-watch` updating the local
record): `create-pr` reconciles the active PR's state against the provider
before choosing a branch, so a stale local `open` never causes a force-push
onto a merged branch. See *Multiple PRs per worktree* below.

**Auto-open (provider plugins).** When the repo config sets `pr.provider` with
credentials (`pr.api_base`, `pr.token_command`/`pr.token_env`) and
`pr.auto_open` is on, `create-pr` **opens the PR itself** right after the push
-- via the provider CLI (`curl` for Gitea, `gh` for GitHub, `az` for Azure
DevOps) -- embeds a hidden source-worktree attribution marker in the body, and
**auto-records** the url/number on the worktree (no manual `set-pr`). Useful
flags: `--no-open` (push only), `--no-attribution` (omit the marker),
`--body`/`--body-file`, `--repo owner/name`. If the provider call fails the
branch is still pushed, and the result carries `pr_open_error` so you can fall
back to Steps 2-3 below. A repo **without** provider credentials configured
uses the manual flow unchanged.

> **Trust the result -- do not open a second PR.** When `create-pr` returns
> `pr_opened: true` (or any `number`/`url`), the PR is already open and recorded
> -- **skip Steps 2-3 entirely**; opening another PR yourself produces a
> duplicate. This applies to re-runs too: a re-run on an already-pushed branch
> **surfaces the existing PR's number/url** (and opens a still-pending PR),
> rather than silently succeeding with no PR. Only fall back to Steps 2-3 when
> the result carries a `pr_open_error`, or when `pr.auto_open` is off / no
> provider creds are configured.

> **`pr_label_error` -- PR opened, but a label didn't stick.** When `create-pr`
> opens the PR but a configured label (e.g. `auto-merge` / `source:<machine>`)
> could not be applied, the result carries `pr_label_error` (the PR still
> exists -- do **not** open another). The label apply now retries transient
> failures, so this is rare; if it appears, re-apply the named label(s) via the
> provider sub-agent rather than re-creating the PR.

### Step 2: Delegate PR creation to the provider sub-agent

*(Manual fallback -- used only when `create-pr` did **not** open the PR: i.e.
the result carries a `pr_open_error`, `pr.auto_open` is off, or no provider
creds are configured. If `create-pr` already reported `pr_opened: true` /
a `number`, do not run this step -- the PR exists.)* The CLI does **not** call
any provider API in this path -- you do, via the matching sub-agent. Read the
provider and route accordingly:

| Provider | How to create the PR |
|----------|----------------------|
| `gitea` | Use the **gitea** sub-agent (Task tool, `agent_type: "gitea"`) to open a PR for the pushed feature branch into the default branch. |
| `github` | `gh pr create --head <feature-branch> --base <default-branch>` via the shell (or a GitHub sub-agent). |
| `azure-devops` | `az repos pr create --source-branch <feature-branch> --target-branch <default-branch>` via the shell. |

Enable auto-merge if the workflow calls for it -- that is a provider-side
action you request, not a CLI flag.

### Step 3: Record the PR metadata

After the sub-agent returns the PR URL and number:

```
agent-worktrees set-pr --url <URL> --number <N>
```

Inspect tracked PR state any time with `agent-worktrees pr-status [--json]`.
Add `--all` to list every tracked PR (serial/parallel), not just the active
one. When a worktree tracks several PRs, `set-pr` updates the **active** PR by
default; target a specific one with `--pr <number>` or
`--select-branch <branch>`.

### Multiple PRs per worktree (serial & parallel)

One worktree can track more than one PR -- recorded as a `prs:` list in the
tracking YAML, each entry self-describing (its own `state`, `branch`, target
`repo`, timestamps). The **active** PR (what no-selector commands target) is
the most recent non-terminal (open/creating) PR, or the most recent overall
when none are live.

- **Serial (the common case):** land a PR, then start the next change in the
  same worktree. Once the first PR is merged, just run `create-pr` again --
  it appends a fresh PR with a new branch and a current base, never reusing
  the merged branch. Works even when the prior PR was merged externally:
  `create-pr` reconciles the tracked PR's state against the provider first.

  **After a PR merges, pull the worktree forward and build on top of it.** Run
  `agent-worktrees git sync` to rebase the worktree branch onto the updated
  default branch -- it drops the just-merged (squashed) commits and keeps any
  newer local work, so you continue *on top of* the merge rather than starting a
  fresh worktree. See the **`git-collaboration`** skill.

> **The operator owns local worktrees -- an agent never creates one as a
> *continuation* of its own work.** Reusing the current worktree (sync forward,
> keep going) is the *only* way to advance serial work, the next stretch of an
> effort, a follow-up PR, or a finalized worktree. **Do NOT run
> `agent-worktrees create`** for any of these; from inside an agent session,
> create a worktree only when the **operator explicitly requests** it.
> - **Handoffs are in-place.** A context handoff continues in the **same
>   worktree** via a **new session** -- the handoff prompt must **never** tell
>   the next session to "create/build on a fresh worktree." (See the
>   **`context-handoff`** skill.)
> - **Cross-machine `agent-bridge` delegation is the exception that proves the
>   rule:** dispatching genuinely *parallel* work to another machine implicitly
>   provisions a remote worktree *bound to the host worktree* -- that is expected
>   and fine. The ban is only on new worktrees used as *continuations* of the
>   current line of work.
- **Parallel:** keep one PR open and open another from the same worktree with
  `create-pr --new`. Address a specific one with `push-changes` (from its
  feature branch) or `set-pr --pr <n>`.
- **Cleanup safety:** a worktree with any **open** PR is never reaped by
  cleanup, even if its current HEAD's content is already on master.

### Iterating on review feedback (keep-alive disposition)

To address feedback in the **same** worktree: edit, commit on the feature
branch, then update the PR branch with:

```
agent-worktrees push-changes
```

In PR mode `push-changes` runs the rebase chain (worktree base onto master,
feature onto the base) and force-with-lease pushes the **feature branch** --
never master. It does not create a PR; it updates the existing one.

### Finalizing a PR-mode worktree

```
agent-worktrees finalize
```

**Finalize is decoupled from merge.** A PR-mode worktree finalizes as soon as
its work is *safely upstream* -- the feature branch is pushed with no unpushed
commits. The PR does **not** need to be merged first. Finalize tears down the
worktree and removes the local branches but **leaves the remote feature branch
intact** (it backs the open PR). If there are unpushed commits, finalize blocks
and tells you to run `push-changes`.

### Recovering a PR after teardown (detach disposition)

If a finalized PR later needs more work, there is **no special resume
command**. Start the normal `create` workflow for a fresh worktree, then use
your provider git-ops skill to fetch the surviving remote feature branch and
re-establish the rebase chain. The CLI stays provider-agnostic; recovery is
ordinary git owned by you.

