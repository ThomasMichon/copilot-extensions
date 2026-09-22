# agent-pull-requests CLI reference

`agent-pull-requests` is a new, separate plugin for **cross-repo** PR
operations addressed directly by `--repo owner/repo`, even when the target repo
has no local worktree checkout.

## Status of this slice

This is the first runtime-backed step of the staged rollout from
`agent-worktrees`' existing PR verbs into a superset plugin:

- `agent-worktrees` is unchanged in this slice.
- Consumer/doc migration and deprecation are explicitly later steps.
- The only implemented end-to-end verb here is `status`.
- Marketplace install now deploys a real `~/.local/bin/agent-pull-requests`
  binstub backed by the plugin's own versioned runtime.

## Planned verb surface

```text
agent-pull-requests status --repo <owner/repo> --number <n> [--json]
agent-pull-requests create --repo <owner/repo> ...
agent-pull-requests merge --repo <owner/repo> --number <n> ...
agent-pull-requests wait --repo <owner/repo> --number <n> ...
```

## Implemented now: `status`

```text
agent-pull-requests status --repo <owner/repo> --number <n> [--json]
```

Reads one GitHub pull request and reports:

- PR state
- mergeable state
- review decision
- draft bit
- title and URL

Current implementation details:

1. It is GitHub-only in this slice.
2. It shells out via `agent-worktrees repos gh <owner/repo> -- ...` to reuse
   the existing account/token plumbing instead of inventing a second auth path.
3. An explicit `owner/repo` target works **without** a local checkout, but the
   account-selection behavior still comes from `agent-worktrees`:
   - registered repos can use pinned account data;
   - unregistered repos fall back to owner-based resolution;
   - org-owned repos whose actual `gh` login differs from the owner may still
     warn and use ambient auth until this plugin grows its own lighter-weight
     resolver.

## Not implemented yet

- `create`
- `merge`
- `wait`
- non-GitHub providers
- worktree-tracking integration (`set-pr`, active-PR selection, reconcile)

Those are intentionally left for later parity slices so this first step proves
the standalone plugin shape without changing `agent-worktrees`.
