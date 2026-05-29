# Contributing to Copilot Extensions

## Release & Versioning

### Marketplace architecture

This repo is a **Copilot CLI plugin marketplace** — a GitHub-hosted
registry of plugins that machines install via `copilot plugin marketplace
add ThomasMichon/copilot-extensions`. The marketplace catalog lives at
`.github/plugin/marketplace.json` and lists every plugin with its current
version. The Copilot CLI reads this file to determine available updates.

### Version scheme

Agent Worktrees follows [PEP 440](https://peps.python.org/pep-0440/)
compatible versioning:

```
MAJOR.MINOR.PATCH[-devN]
```

- **Patch** bumps (`1.0.1 -> 1.0.2`) — bug fixes, small improvements,
  new skills/docs that don't change runtime behavior.
- **Minor** bumps (`1.0.x -> 1.1.0`) — new features, behavioral changes,
  new CLI subcommands. **Only when the maintainer decides.**
- **Major** bumps (`1.x -> 2.0`) — breaking changes. **Only when the
  maintainer decides.**

### Default: bump patch with `-devN`

When committing changes that warrant a version bump, use the **patch**
level with a `-devN` suffix:

```
1.0.1 -> 1.0.2-dev1 -> 1.0.2-dev2 -> ... -> 1.0.2 (release)
```

Do **not** bump minor or major versions unless explicitly instructed.

### Where the version lives (ALL THREE must be bumped together)

| File | Field | Purpose |
|------|-------|---------|
| `plugins/agent-worktrees/plugin.json` | `version` | Copilot CLI reads this to detect updates via `copilot plugin update` |
| `plugins/agent-worktrees/pyproject.toml` | `version` under `[project]` | Python package version at runtime; shown in `--version` output |
| `.github/plugin/marketplace.json` | `metadata.version` AND `plugins[0].version` | Marketplace catalog; Copilot CLI reads this from GitHub to check for updates |

**All three files must be bumped together in the same commit.** If any
file is out of sync:

- Stale `plugin.json` — `copilot plugin update` reports "already at
  latest" even when new code is available.
- Stale `marketplace.json` — the marketplace registry shows the old
  version; machines checking for updates won't see the new version.
- Stale `pyproject.toml` — runtime `--version` output is wrong.

### When to bump

- After a set of changes is committed and ready to push.
- Before pushing to GitHub — the push is the "release."
- One bump per push is fine; don't bump on every commit.

## Deploying Agent Worktrees

Agent Worktrees is deployed from the `copilot-extensions` GitHub repo,
not from the aperture-labs monorepo. The aperture-labs repo contains a
parallel `worktree-manager` service that shares code but deploys
independently.

### Push flow

1. Make changes in `plugins/agent-worktrees/`
2. Bump the version in **both** `pyproject.toml` and `plugin.json`
   (patch + `-devN`)
3. Commit with a descriptive message
4. Push to `main` on GitHub: `git push origin main`
5. Machines pick up the update via `copilot plugin update` or manual
   `git pull`

### Keeping worktree-manager in sync

When fixing bugs or adding features that apply to both codebases:

1. Apply the fix in **both** `copilot-extensions` (agent-worktrees) and
   `aperture-labs` (worktree-manager)
2. Push copilot-extensions to GitHub
3. Push aperture-labs to origin (Gitea)

The two codebases are forked — they share structure and much of the code,
but are not automatically synchronized.

## Code Style

- Python 3.10+, type hints encouraged
- No external linter configured yet — keep code clean and consistent
  with existing style
- Docstrings for public functions

## Commit Messages

- Descriptive, imperative mood: "Fix Unicode crash on cp1252 consoles"
- Reference Gitea issue numbers where applicable: "Fix #372: …"
- Include `Co-authored-by` trailer for Copilot-assisted commits
