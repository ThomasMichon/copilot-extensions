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

Each plugin has its own version triplet. Bump only the files for the
plugin you changed:

**agent-worktrees:**

| File | Field | Purpose |
|------|-------|---------|
| `plugins/agent-worktrees/plugin.json` | `version` | Copilot CLI reads this to detect updates via `copilot plugin update` |
| `plugins/agent-worktrees/pyproject.toml` | `version` under `[project]` | Python package version at runtime; shown in `--version` output |
| `.github/plugin/marketplace.json` | `metadata.version` AND `plugins[0].version` | Marketplace catalog; Copilot CLI reads this from GitHub to check for updates |

**agent-bridge:**

| File | Field | Purpose |
|------|-------|---------|
| `plugins/agent-bridge/plugin.json` | `version` | Plugin version for marketplace detection |
| `plugins/agent-bridge/pyproject.toml` | `version` under `[project]` | Python package version; shown in `agent-bridge version` output |
| `.github/plugin/marketplace.json` | `plugins[1].version` | Marketplace catalog entry for agent-bridge |

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
not from your project monorepo. Your project repo may contain a
parallel `worktree-manager` service that shares code but deploys
independently.

### The Deployment Pipeline

Changes follow this exact sequence — no shortcuts:

1. **Commit** changes in `plugins/agent-worktrees/`
2. **Bump the version** in all three files (see "Where the version lives")
3. **Push** to `main` on GitHub: `git push origin main`
4. **Update on each machine** via `agent-worktrees update`
   (over SSH for remote machines)

The update command runs `copilot plugin update` to pull the latest
plugin from the marketplace, then executes the platform-specific
installer which deploys the package, regenerates `_build_info.py`
with the real commit hash, and refreshes instruction files.

### What NOT to Do

**Never copy source files directly into the deployed runtime directory
(`~/.agent-worktrees/lib/`).** This bypasses:

- Version tracking (`_build_info.py` won't reflect the real version)
- The installer's own setup steps (venv sync, wrapper generation,
  instruction file deployment, post-install hooks)
- Other machines — they won't get the update
- Rollback safety — there's no commit to revert to

If you need to test a change locally before pushing, use the installer
from the local checkout:

```powershell
# Windows — from the copilot-extensions checkout
cd plugins\agent-worktrees
.\scripts\install.ps1 update
```

```bash
# Linux/WSL — from the copilot-extensions checkout
cd plugins/agent-worktrees
./scripts/install.sh update
```

This runs the real installer against the local source, so the full
pipeline executes (build info, venv, wrappers, instructions) — just
from a local commit instead of a pushed one.

## Deploying Agent Bridge

Agent Bridge is a persistent HTTP service (not a per-session plugin).
It deploys via the **aperture-labs service framework**, not the Copilot
CLI marketplace update flow.

### The Deployment Pipeline

1. **Commit** changes in `plugins/agent-bridge/`
2. **Bump the version** in all three files (see "Where the version lives")
3. **Push** to `main` on GitHub: `git push origin main`
4. **Update on each machine** via `aperture-labs services agent-bridge update`

The aperture-labs installer resolves the local checkout via `~/.git-repos`,
installs agent-bridge into a venv, deploys layered config, and restarts
the service.

### Platform-Specific Deployment

| Platform | Installer | Service manager | Install location |
|----------|-----------|----------------|-----------------|
| Linux/WSL | `install.sh` | systemd | `/opt/agent-bridge/` |
| Windows | `install.ps1` | Scheduled task + PID file | `~/.agent-bridge/` |
| macOS | Planned | -- | -- |

### Local Testing

```powershell
# Windows
pwsh -File plugins\agent-bridge\scripts\install.ps1 install
```

```bash
# Linux/WSL
bash plugins/agent-bridge/scripts/install.sh install
```

### Keeping worktree-manager in sync

When fixing bugs or adding features that apply to both codebases:

1. Apply the fix in **both** `copilot-extensions` (agent-worktrees) and
   your project repo (worktree-manager)
2. Push copilot-extensions to GitHub
3. Push your project repo to its origin

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
