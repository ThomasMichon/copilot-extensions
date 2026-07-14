# Contributing to Copilot Extensions

## Release & Versioning

### Marketplace architecture

This repo is a **Copilot CLI plugin marketplace** — a GitHub-hosted
registry of plugins that machines install via `copilot plugin marketplace
add ThomasMichon/copilot-extensions`. The marketplace catalog lives at
`.github/plugin/marketplace.json` and lists every plugin with its current
version. The Copilot CLI reads this file to determine available updates.

> **`copilot plugin update` refreshes only the payload, not the runtime.**
> For plugins with a runtime (venv/binstubs/service — agent-worktrees,
> agent-bridge, agent-codespaces, agent-containers), `copilot plugin update`
> updates the cached source + skills but does **not** redeploy the runtime; that
> is a separate installer step run from the source folder (via the plugin's
> install skill). Pure skill/hook/agent plugins need no installer. See
> [docs/install-contract.md → Plugin update ≠ runtime install](docs/install-contract.md#plugin-update--runtime-install).

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
plugin you changed.

> **General rule (applies to every plugin, present and future).** For a plugin
> `<p>`: bump `plugins/<p>/plugin.json` (`version`), `plugins/<p>/pyproject.toml`
> (`[project].version`, runtime plugins only — payload-only plugins have none),
> and `<p>`'s entry in `.github/plugin/marketplace.json` (find it **by name**,
> not a hardcoded index). **agent-worktrees** additionally bumps
> `metadata.version`; **adding a new plugin** appends a `plugins[]` entry and
> bumps `metadata.version`. The per-plugin tables below are concrete examples for
> the original plugins — the same rule covers agent-logger, agent-dispatch,
> context-handoff, efforts, visions, customizing-copilot,
> harness-copilot-extensions, and anything added later.
>
> **Keep any in-package `__version__` in sync.** A runtime plugin that exposes a
> Python `__version__` (e.g. `agent-dispatch`'s `src/agent_dispatch/__init__.py`,
> surfaced by `--version` and the coordinator's `/health`) must bump it to match
> the `pyproject.toml` version in the **same** commit — it is a *fourth* file for
> that plugin, easy to miss because the marketplace doesn't read it. A stale
> `__version__` makes a correctly-deployed runtime misreport its own version.

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

**agent-codespaces:**

| File | Field | Purpose |
|------|-------|---------|
| `plugins/agent-codespaces/plugin.json` | `version` | Plugin version for marketplace detection |
| `plugins/agent-codespaces/pyproject.toml` | `version` under `[project]` | Python package version; shown in `agent-codespaces version` output |
| `.github/plugin/marketplace.json` | `plugins[2].version` | Marketplace catalog entry for agent-codespaces |

**agent-containers:**

| File | Field | Purpose |
|------|-------|---------|
| `plugins/agent-containers/plugin.json` | `version` | Plugin version for marketplace detection |
| `plugins/agent-containers/pyproject.toml` | `version` under `[project]` | Python package version; shown in `agent-containers version` output |
| `.github/plugin/marketplace.json` | `plugins[3].version` | Marketplace catalog entry for agent-containers |

**agent-mcp:**

| File | Field | Purpose |
|------|-------|---------|
| `plugins/agent-mcp/plugin.json` | `version` | Plugin version for marketplace detection |
| `plugins/agent-mcp/pyproject.toml` | `version` under `[project]` | Python package version; shown in `agent-mcp status` output |
| `.github/plugin/marketplace.json` | `plugins[4].version` | Marketplace catalog entry for agent-mcp |

**All version files for a plugin must be bumped together in the same commit.** If any
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
It deploys via its **own installer scripts** in
`plugins/agent-bridge/scripts/`, not the Copilot CLI marketplace update
flow.

### The Deployment Pipeline

1. **Commit** changes in `plugins/agent-bridge/`
2. **Bump the version** in all three files (see "Where the version lives")
3. **Push** to `main` on GitHub: `git push origin main`
4. **Update on each machine** via the installer (see below)

The installer resolves the local checkout via `~/.git-repos`, installs
agent-bridge into a venv, deploys layered config, and restarts the
service. Project binstubs (e.g. `my-project services agent-bridge
update`) can also dispatch to the installer.

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

## Deploying Agent Codespaces

Agent Codespaces is a session plugin with a CLI binstub. It provides the
`codespace:<name>` namespace resolver for agent-bridge and a standalone
`agent-codespaces` CLI for SSH transport, credential relay, and lifecycle
management.

### The Deployment Pipeline

1. **Commit** changes in `plugins/agent-codespaces/`
2. **Bump the version** in all three files (see "Where the version lives")
3. **Push** to `main` on GitHub: `git push origin main`
4. **Update on each machine** via the installer

### Install / Update

```powershell
# Windows -- from the copilot-extensions checkout
cd plugins\agent-codespaces
.\scripts\install.ps1 install    # first time
.\scripts\install.ps1 update    # subsequent updates
```

```bash
# Linux/WSL -- from the copilot-extensions checkout
cd plugins/agent-codespaces
bash scripts/install.sh install
bash scripts/install.sh update
```

The installer creates a venv at `~/.agent-codespaces/`, deploys the
package and ssh-manager dependency, and places a binstub in
`~/.local/bin/`.

### Bootstrap (init)

For first-time setup on a new machine, the `init` scripts handle
everything including prerequisite checks:

```powershell
# Windows
pwsh -File plugins\agent-codespaces\scripts\init.ps1
```

```bash
# Linux/WSL
bash plugins/agent-codespaces/scripts/init.sh
```

### Version Files

Bump all three files for agent-codespaces before pushing (same rule as
other plugins):

| File | Field |
|------|-------|
| `plugins/agent-codespaces/plugin.json` | `version` |
| `plugins/agent-codespaces/pyproject.toml` | `version` under `[project]` |
| `.github/plugin/marketplace.json` | `plugins[2].version` |

## Deploying Agent Containers

Agent Containers is a CLI plugin with an `~/.agent-containers` runtime. It
provides the `container:<name>` namespace resolver for agent-bridge (installed
as a sibling package into the bridge venv) and a standalone `agent-containers`
CLI for local Docker dev-container fleet and lease management.

### The Deployment Pipeline

1. **Commit** changes in `plugins/agent-containers/`
2. **Bump the version** in all three files (see "Where the version lives")
3. **Push** to `main` on GitHub: `git push origin main`
4. **Update on each machine** by re-running the init script

### Install / Update

The plugin ships only `init` scripts (no separate `install`); re-running `init`
with `--force` / `-Force` redeploys the runtime.

```powershell
# Windows -- from the copilot-extensions checkout
pwsh -File plugins\agent-containers\scripts\init.ps1            # first time
pwsh -File plugins\agent-containers\scripts\init.ps1 -Force     # redeploy
```

```bash
# Linux/WSL -- from the copilot-extensions checkout
bash plugins/agent-containers/scripts/init.sh                   # first time
bash plugins/agent-containers/scripts/init.sh --force           # redeploy
```

The init script creates a venv at `~/.agent-containers/` and places a binstub in
`~/.local/bin/`. So the bridge picks up the `container:` resolver, install
agent-containers **before** (re)running the agent-bridge installer.

## Deploying Agent MCP

Agent MCP is a standalone CLI plugin with an `~/.agent-mcp` runtime. Unlike the
other plugins it has **no** agent-bridge integration — an agent invokes the
`agent-mcp` binstub directly from its `mcp-servers` config to wrap an upstream
MCP server.

### The Deployment Pipeline

1. **Commit** changes in `plugins/agent-mcp/`
2. **Bump the version** in all three files (see "Where the version lives")
3. **Push** to `main` on GitHub: `git push origin main`
4. **Update on each machine** by re-running the init script

### Install / Update

Like agent-containers, agent-mcp ships only `init` scripts; re-run with
`--force` / `-Force` to redeploy.

```powershell
# Windows
pwsh -File plugins\agent-mcp\scripts\init.ps1            # first time
pwsh -File plugins\agent-mcp\scripts\init.ps1 -Force     # redeploy
```

```bash
# Linux/WSL
bash plugins/agent-mcp/scripts/init.sh                   # first time
bash plugins/agent-mcp/scripts/init.sh --force           # redeploy
```

The init script creates a venv at `~/.agent-mcp/` and places the `agent-mcp`
binstub in `~/.local/bin/`.

## Code Style

- Python 3.10+, type hints encouraged
- **Linter: [ruff](https://docs.astral.sh/ruff/).** Each plugin configures its
  own `[tool.ruff]` in `pyproject.toml`. Run the full pass with `ruff check .`
  (and `ruff format` for formatting). The repo carries pre-existing style debt,
  so the committed `pre-commit` hook lints only **staged** files and only the
  high-signal `F` (pyflakes) + `E9` (syntax) rule groups — fix those as you go.
- Docstrings for public functions

### Git Hooks

The repo ships git hooks under `tools/hooks/`:

- **`pre-commit`** — runs `ruff check --select F,E9` on staged Python files
  (unused imports/vars, undefined names, syntax errors).
- **`pre-push`** — runs `tools/check-install-contract.py` (the
  [install contract](docs/install-contract.md)) and
  `tools/check-no-internal-identifiers.py`.

They are **not active until wired** per clone (git does not auto-enable a
committed hooks dir):

```bash
git config core.hooksPath tools/hooks
```

Bypass in a pinch with `git commit/push --no-verify` (discouraged). The
install-contract check fails until every runtime plugin's installer conforms —
see the contract doc for the rules.

## Gotchas

### The mux status bar must never compute on the render path

**Rule: nothing in a tmux/psmux `status-left` / `status-right` may spawn a
process per render.** No `#(agent-worktrees …)`, no `#(cat …)`, no `#()` that
shells out. The bar may read only precomputed values — the `#{@aw_ctx}` /
`#{@aw_seg}` user options plus `%H:%M`-style strftime. A detached
`status-updater` watcher computes the segments **off** the render path and
pushes them in via `set-option`.

**Why (the regression this exists to prevent).** tmux runs `#()` jobs
asynchronously and caches them between `status-interval` ticks, so it *mostly*
hides the cost. **psmux repaints synchronously** — it re-runs every `#()` in the
status line on each repaint, in the render/keystroke path. A bar that shelled
out to the (Python, cold-starting) `agent-worktrees` CLI cost ~600 ms per
repaint there; under Copilot's high-framerate TUI that turned keystroke echo and
re-render to molasses on Windows (worse under the double-ConPTY stack), while a
no-mux session stayed snappy. The fix moved the compute into one common
`status-updater` watcher feeding `#{@aw_*}` vars. See the *Off the paint path*
section of
[`plugins/agent-worktrees/docs/cli-reference.md`](plugins/agent-worktrees/docs/cli-reference.md).

**If you touch `terminal/psmux.conf`, `terminal/session-options.sh`, or a
launcher status path:** keep the bar on `#{@aw_*}` vars; keep the compute in the
shared cross-platform `status-updater` watcher (do **not** re-introduce a
per-mux shell writer or a render-path `#()`); the guard tests in
`plugins/agent-worktrees/tests/test_terminal_decoupling.py` (assert no
`#(agent-worktrees` / `#(cat` in the bar) will fail if you regress. Verified
mechanisms: psmux 3.3.6 and tmux 3.4 both support session-scoped `set-option -t`
(isolated per session) and `#{@user-option}` expansion.

## Commit Messages

- Descriptive, imperative mood: "Fix Unicode crash on cp1252 consoles"
- Reference this repo's GitHub issue numbers where applicable: "Fix #372: …"
- Include `Co-authored-by` trailer for Copilot-assisted commits
