# agent-pull-requests

Cross-repository pull-request commands for Copilot CLI sessions that need to
act on `owner/repo` targets **without** requiring a local checkout of that repo.

This plugin is the extraction from `agent-worktrees`' worktree-bound PR
commands into a separate, API-first surface:

- `agent-worktrees` remains unchanged and fully supported.
- `status`, `create`, `merge`, and `wait` are all implemented.

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

## Verbs

```text
agent-pull-requests status --repo <owner/repo> --number <n> [--json]
agent-pull-requests create --repo <owner/repo> --head <branch> [--base <branch>] --title <t> [--body <b>] [--draft] [--json]
agent-pull-requests merge  --repo <owner/repo> --number <n> [--squash|--merge|--rebase] [--auto] [--delete-branch] [--json]
agent-pull-requests wait   --repo <owner/repo> --number <n> [--interval <s>] [--timeout <s>] [--json]
```

`create` and `merge` shell out to `gh pr create`/`gh pr merge` with an explicit
`--repo`/`--head` pair so no local checkout or branch context is required;
`wait` polls `status` until the pull request reaches `MERGED` or `CLOSED`, or
the timeout elapses (exit code `3`).

## Current constraint

The current GitHub implementation still shells out through:

`agent-worktrees repos gh <owner/repo> -- gh ...`

That means this plugin currently depends on an available `agent-worktrees`
runtime plus its GitHub account-resolution logic. An explicit `owner/repo`
target does **not** require a local checkout, but unregistered repos still
rely on `agent-worktrees`' owner-to-login mapping or owner fallback, so an
org-owned repo whose GitHub login differs from the owner may still fall back
to ambient `gh` authentication until a dedicated account-resolution layer
lands here.

See [docs/cli-reference.md](docs/cli-reference.md) for full verb documentation.
