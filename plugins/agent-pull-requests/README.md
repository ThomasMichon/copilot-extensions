# agent-pull-requests

Cross-repository pull-request commands for Copilot CLI sessions that need to
act on `owner/repo` targets **without** requiring a local checkout of that repo.

This plugin is the first slice of the staged extraction from
`agent-worktrees`' worktree-bound PR commands into a separate,
API-first surface. In this scaffold slice:

- `agent-worktrees` remains unchanged and fully supported.
- `agent-pull-requests status --repo <owner/repo> --number <n>` is implemented.
- `create`, `merge`, and `wait` are reserved as planned verbs and currently
  return a clear "not implemented yet" error.

## Installation

Marketplace install deploys a real runtime:

```bash
copilot plugin install agent-pull-requests@copilot-extensions
agent-pull-requests status --repo <owner/repo> --number <n>
```

The installer builds an immutable versioned venv under
`~/.agent-pull-requests/versions/<version>/`, deploys a real
`~/.local/bin/agent-pull-requests` binstub, and self-reconciles that runtime on
session start the same way the other Python runtime plugins do.

## Current constraint

The current GitHub implementation still shells out through:

`agent-worktrees repos gh <owner/repo> -- gh api ...`

That means this plugin currently depends on an available `agent-worktrees`
runtime plus its GitHub account-resolution logic. An explicit `owner/repo`
target does **not** require a local checkout, but unregistered repos still
rely on `agent-worktrees`' owner-to-login mapping or owner fallback, so an
org-owned repo whose GitHub login differs from the org may fall back to ambient
`gh` authentication until a dedicated account-resolution layer lands here.

See [docs/cli-reference.md](docs/cli-reference.md) for the planned surface.
