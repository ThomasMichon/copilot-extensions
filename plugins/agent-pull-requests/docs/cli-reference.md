# agent-pull-requests CLI reference

`agent-pull-requests` is a new, separate plugin for **cross-repo** PR
operations addressed directly by `--repo owner/repo`, even when the target repo
has no local worktree checkout.

## Status of this slice

This is the runtime-backed extraction from `agent-worktrees`' existing PR
verbs into a superset plugin:

- `agent-worktrees` is unchanged in this slice.
- Consumer/doc migration and deprecation are explicitly later steps.
- All four verbs (`status`, `create`, `merge`, `wait`) are implemented.
- Marketplace install deploys a real `~/.local/bin/agent-pull-requests`
  binstub backed by the plugin's own versioned runtime.

## Verb surface

```text
agent-pull-requests status --repo <owner/repo> --number <n> [--json]
agent-pull-requests create --repo <owner/repo> --head <branch> [--base <branch>] --title <t> [--body <b>] [--draft] [--json]
agent-pull-requests merge  --repo <owner/repo> --number <n> [--squash|--merge|--rebase] [--auto] [--delete-branch] [--json]
agent-pull-requests wait   --repo <owner/repo> --number <n> [--interval <s>] [--timeout <s>] [--json]
```

## `status`

```text
agent-pull-requests status --repo <owner/repo> --number <n> [--json]
```

Reads one GitHub pull request and reports:

- PR state
- mergeable state
- review decision
- draft bit
- title and URL

## `create`

```text
agent-pull-requests create --repo <owner/repo> --head <branch> [--base <branch>] --title <t> [--body <b>] [--draft] [--json]
```

Opens a pull request via `gh pr create --repo <owner/repo> --head <branch> ...`.
`--head` must already be pushed to the target repo (or a fork per `gh`'s
`<user>:<branch>` syntax); no local checkout is created or required. `--base`
defaults to the repo's default branch when omitted. Returns the created PR's
repo/number/url (parsed from `gh`'s printed PR URL).

## `merge`

```text
agent-pull-requests merge --repo <owner/repo> --number <n> [--squash|--merge|--rebase] [--auto] [--delete-branch] [--json]
```

Merges an existing pull request via `gh pr merge`. Defaults to `--squash`;
`--auto` enables auto-merge instead of merging immediately; `--delete-branch`
removes the head branch after a successful merge.

## `wait`

```text
agent-pull-requests wait --repo <owner/repo> --number <n> [--interval <s>] [--timeout <s>] [--json]
```

Polls `status` (default 15s interval, 600s timeout) until the pull request
reaches a terminal state (`MERGED` or `CLOSED`). Exit codes: `0` merged,
`1` closed without merging (or a status error), `3` timed out while still
open.

## Shared implementation details

1. GitHub-only in this slice.
2. All verbs shell out via `agent-worktrees repos gh <owner/repo> -- ...` to
   reuse the existing account/token plumbing instead of inventing a second
   auth path.
3. An explicit `owner/repo` target works **without** a local checkout, but
   the account-selection behavior still comes from `agent-worktrees`:
   - registered repos can use pinned account data;
   - unregistered repos fall back to owner-based resolution;
   - org-owned repos whose actual `gh` login differs from the owner may still
     warn and use ambient auth until this plugin grows its own lighter-weight
     resolver.

## Not implemented yet

- non-GitHub providers
- worktree-tracking integration (`set-pr`, active-PR selection, reconcile)

Those remain later parity/migration slices, kept separate so this plugin's
standalone shape stays independent of `agent-worktrees`' own worktree-bound
PR flows.
