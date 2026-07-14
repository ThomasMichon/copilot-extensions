# Worktree PR Workflow (PR mode)

Full reference for signing off a worktree through a **pull request** instead of
direct-push finalization. See [SKILL.md](../SKILL.md) for the overview, the
two-phase `push-changes` + `finalize` flow (direct mode), and the safety
rules.

## Contents
- Check the target repo's PR flow first (profiles + verb applicability)
- Detecting PR mode + where PR config lives (machine-local vs in-repo)
- `create-pr` (auto-open, attribution marker, labels)
- Dispositions: keep-alive vs detach
- Draft PRs (`--draft` / `pr-ready`)
- Multiple PRs from one worktree
- Recovery

---
## PR Workflow (PR mode)

Some repos opt into a **pull-request workflow** instead of direct-push
finalization. A repo is in PR mode when its config sets `pr.enabled: true`.

### Check the target repo's PR flow FIRST -- it is not the same everywhere

**Never assume a PR flow; read the target repo's config before you drive one.**
Different repos land work differently, and the `pr-*` verbs apply to different
subsets. Query the repo's **flow profile** up front:

```
agent-worktrees get pr-profile      # direct | pr-human-merge | pr-agent-merge
agent-worktrees get pr-enabled      # "true" or "false"
agent-worktrees get pr-required     # "true" -> direct-to-master is blocked
agent-worktrees get pr-provider     # gitea | github | azure-devops (empty in direct mode)
```

The three profiles (derived purely from config -- provider-generic, no network):

| Profile | Config shape | How work lands | Verbs that apply |
|---------|--------------|----------------|------------------|
| **`direct`** | `pr.enabled: false` | `finalize` lands to the default branch | *(none -- no PR flow)* |
| **`pr-human-merge`** | enabled, **no** `automerge_label` | PR-gated; a **human** approves + merges | `create-pr`, `pr-watch`, `pr-status`, `pr-complete` -- **not `pr-merge`** |
| **`pr-agent-merge`** | enabled + an `automerge_label` bound | PR-gated; the author **signals merge consent** after approval; the review gate merges | the full `pr-*` family, including `pr-merge` |

**Applicability is self-describing.** `pr-status` prints the profile (`flow:`
line + a `flow` block in `--json`), and `pr-merge` **refuses** on a repo whose
profile is not `pr-agent-merge`, naming the reason and pointing at the right
process (human merge vs a stale anchor). So when a verb reports it does not
apply, believe it and follow its pointer -- do **not** hand-merge, escalate to
an admin, or invent a flow.

- On a **`pr-human-merge`** repo: open the PR (`create-pr`), address review with
  `pr-watch`, then **a human approves and merges** -- consult the repo's
  `CONTRIBUTING` / related narrative for who merges. `pr-merge` does not apply.
- On a **`pr-agent-merge`** repo (e.g. an auto-reviewer + auto-merge repo where
  review typically lands in minutes): after approval, `pr-merge` signals consent
  and the gate merges. This is the flow the sections below assume.
- If `pr-merge` reports "no automerge_label" on a repo you **expected** to be
  `pr-agent-merge`, suspect a **stale anchor** first (the binding landed on the
  default branch but this checkout hasn't pulled it): refresh the anchor
  (`git sync` on the anchor / the project's update command) and retry -- do not
  fall back to a hand-merge.

### "Request auto-complete" -- the same shape across providers (incl. Azure DevOps)

`pr-merge` is **"request auto-complete of this PR"**. *How* the provider honors
that is an implementation detail, so ADO is the **same `pr-agent-merge` shape**
as gitea/github -- not a special case:

| Provider | How `pr-merge` requests auto-complete | Consent marker in a snapshot |
|----------|----------------------------------------|-------------------------------|
| gitea / github | applies the `automerge_label` (the review gate then merges) | the real label on the PR |
| azure-devops | sets **native** auto-complete (`az repos pr update --auto-complete` with `squash` / `delete_source_branch` / `bypass_policy`) -- no label | the synthetic `auto-complete` marker, present once auto-complete is set |

So an **ADO repo** (e.g. `dev.tmichon`) binds `automerge_label: auto-complete`
(the abstract consent-marker name) and uses the full family. Extra ADO knobs:

- `approval_required: false` -- **self-complete**: eligible when simply *not*
  changes-requested (we own the merge; no approval vote needed). A
  `CHANGES_REQUESTED` review still blocks -- address it, then re-run.
- `bypass_policy: true` -- complete **past** a branch policy that never
  auto-satisfies for our own PRs (e.g. a central governance status policy);
  otherwise ADO auto-complete would wait forever.

The natural "wait for the auto-review, then complete" loop is
`pr-watch` (blocks until the reviewer weighs in / mergeability settles) →
`pr-merge` (requests auto-complete once eligible) → `pr-complete` (post-merge
reconcile).

**`pr-watch` tells you when to run `pr-merge`.** Its result payload carries a
`merge` block derived from the same verdict/consent classifier `pr-status` uses:
`merge.needs_consent` (true = the PR is approved and unblocked but the
merge-consent label is not applied yet — **you** must apply it via `pr-merge`;
it will not merge on its own), `merge.consent_action` (`apply` | `already` |
`skip`), `merge.clear_to_merge`, and `merge.reason`. So an `approved` transition
is a *review* signal, not the finish line: when `needs_consent` is true, run
`pr-merge` and re-arm the watch. On a `pr-human-merge` repo (no consent label
bound) the block degrades to a verdict/merge-state readout with no action.

### Comment threads -- first-class, every provider

Review **comment threads** are a first-class capability (`pr-status --threads`
lists them; `--resolve-threads` marks the active ones resolved). Azure DevOps
maps threads cleanly (REST; AAD or PAT auth); gitea/github carry more-irritating
details (gitea has no programmatic conversation-resolve -- read-only there;
github threads use GraphQL and resolve all active threads at once). The
feedback loop: `pr-status --threads` (read) → fix in the worktree →
`push-changes` → `pr-status --resolve-threads` → `pr-merge`.

Check before signing off:

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

### Head scheme + branch topology (PR mode)

**Invariant (both schemes): a worktree is always checked out on its own
`worktree/{id}` branch, and it always lands on the squashed commit.**
`create-pr` squashes the worktree's commits in place on `worktree/{id}`, rebases
onto upstream, and leaves HEAD there at that squashed commit — it is **never
reset off it** (#1804). `worktree/{id}` sits one commit ahead of master while
the PR is open; a later `git sync` (or the finalize reconcile) realigns it clean
on merge.

`pr.head_scheme` selects **only how the PR head is published** — its name +
push mechanism — never the local worktree state:

**`refspec` (default, #1815/#1899).** Push `worktree/{id}`'s squashed commit
*directly* to a disposable PR head ref via a refspec — no local feature branch:

```
origin/master  <—  worktree/{id}  ——push——>  origin/pr/{slug}-{suffix}
  (upstream)       (the only local branch;     (the PR head; deleted on merge)
                    sits ahead while open)
```

The head ref (`pr/{slug}-{suffix}` by default; templated via `pr.head_pattern`,
e.g. `user/{username}/{slug}-{suffix}`) is ephemeral and provider-deleted on
merge. Requires the repo's pre-push hook to allow the mediated
`worktree/{id} → pr/{slug}` push (a hook that blocks `worktree/*` by ref name
must honor `AGENT_WORKTREES_PR_PUSH=1`). A parallel `--new` PR auto-falls-back
to a snapshot ref (one worktree branch hosts only one live refspec PR).

**`snapshot` (legacy/compatible).** Copies the squashed commit onto a separate
local `feature/{slug}-{suffix}` branch and pushes *that* — **no reset, no
checkout dance** (HEAD stays on `worktree/{id}`, which keeps the squashed
commit):

```
origin/master  <—  worktree/{id}  ——snapshot——>  feature/{slug}-{suffix}
  (upstream)       (keeps the squashed             (the pushed PR head)
                    commit, sits ahead)
```

Snapshot needs no pre-push-hook cooperation, so it is the safe opt-out
(`head_scheme: snapshot`) for a repo whose hook still blocks the refspec push.
Set `head_scheme` per repo to choose; the facility default is `refspec`.

> **`feature/` is reclaimed under refspec.** Under the refspec scheme the per-PR
> head lives in the `pr/` namespace, freeing `feature/<name>` for its other
> meaning — a **coordinated multi-agent shared branch** (see the
> `git-collaboration` skill). Don't conflate the two.

### Step 1: `create-pr`

```
agent-worktrees create-pr --title "Concise PR title"
```

Squashes the worktree's commits into one and rebases onto upstream, leaving HEAD
on `worktree/{id}` at the squashed commit (both schemes — it is never reset off
it, #1804). Under the default **refspec** scheme it pushes `worktree/{id}`
straight to the PR head ref (`pr/{slug}`) — no local feature branch. Under
**snapshot** it instead copies the squashed commit onto a local `feature/{slug}`
branch and pushes that (no reset, no checkout dance). Either way HEAD never
leaves `worktree/{id}`. Records `pr.state` and prints the branch, base/head SHAs, and provider.
Add `--json`
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

### Draft PRs (`--draft` / `pr-ready`)

Open a PR as a **draft** when you want it visible (a shareable URL, or a place to
iterate with `push-changes`) *before* inviting review:

```
agent-worktrees create-pr --draft --title "..."   # opens as a DRAFT
# ... iterate: edit -> commit -> push-changes ...
agent-worktrees pr-ready                            # draft -> ready-for-review
```

`--draft` uses the provider's **native** not-ready-for-review state, and
`pr-ready` clears it. *How* a provider encodes a draft is an implementation
detail the CLI hides:

| Provider | Draft encoding | `pr-ready` |
|----------|----------------|------------|
| `github` | native draft flag (`gh pr create --draft`) | `gh pr ready` |
| `gitea` | a `WIP:` title prefix (Gitea ≤ 1.26 has no draft boolean; the API's `draft` field is derived from it) | strips the WIP prefix by editing the title |
| `azure-devops` | not supported here | reports unsupported |

**`pr-ready` is an un-draft verb, not a merge signal.** It performs exactly one
transition — **draft → ready-for-review** — and states it explicitly. It does
**not** grant merge consent; that stays with `pr-merge` (a separate,
post-approval transition). `pr-ready` **errors** when the PR is not a draft (a
no-op never reports success). Whether an auto-reviewer reviews a draft depends on
the review backend — many skip drafts until the `ready_for_review` transition
that `pr-ready` triggers, so un-drafting is what invites review.

> **Deprecated `--hold`.** `create-pr --hold` is retained as an alias for
> `--draft`. The old model opened a PR carrying a `do-not-merge` label (a
> merge-only hold); that is retired in favour of native draft state. For a legacy
> PR still carrying a `do-not-merge` label, `pr-ready` removes it as a
> backward-compat transition. Prefer `--draft`.

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

  **After a PR merges, immediately pull the worktree forward -- this is the
  standard, expected post-merge move, not an optional cleanup.** The moment you
  confirm your PR landed, rebase the worktree branch onto the updated default
  branch with:

  ```
  agent-worktrees git sync
  ```

  It drops the just-merged (squashed) commits as already-applied and keeps any
  newer local work, so you continue *on top of* the merge rather than starting a
  fresh worktree. See the **`git-collaboration`** skill.

  **Confirming the merge is built into `pr-status`.** `agent-worktrees pr-status`
  reconciles the active PR against the provider before reporting, so a PR merged
  externally (e.g. via the `auto-merge` label, bypassing `finalize`/`pr-watch`)
  shows `state: merged` instead of a stale `open` -- this is your authoritative
  "did my PR land?" check. When it has landed **and** the worktree is not yet on
  top of the updated default branch, `pr-status` flags it for you:

  ```
  "pull_forward_recommended": true,
  "pull_forward_command": "agent-worktrees git sync",
  "next_action": "Active PR #N is merged. Pull this worktree forward: ..."
  ```

  Treat `pull_forward_recommended` as a directive: run `git sync` straight away.
  Because the PR squashes your work into a single commit, the rebase usually
  reconciles cleanly; if it does hit a conflict the rebase auto-aborts and tells
  you to resolve it by hand -- do so, then re-run. (If the worktree is dirty,
  commit or stash first, as the `next_action` note will say.)

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

To address feedback in the **same** worktree: edit, commit on `worktree/{id}`,
then update the PR branch with:

```
agent-worktrees push-changes
```

In PR mode `push-changes` updates the PR head, never master. Feedback commits
ride on `worktree/{id}` (create-pr leaves HEAD there); `push-changes` rebases
`worktree/{id}` onto master and then publishes per scheme — under **refspec**
(default) it force-with-lease pushes `worktree/{id}` to the PR head ref
(`pr/{slug}`); under **snapshot** it snapshots the `feature/{slug}` branch to the
new tip and force-with-lease pushes that. Either way HEAD stays on
`worktree/{id}` — just commit there and run `push-changes`. (A worktree still
checked out on a legacy feature branch is accepted too and pushed as-is.) It
does not create a PR; it updates the existing one.

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

