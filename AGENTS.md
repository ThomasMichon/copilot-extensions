# Copilot Extensions -- Development Guide

Source of truth for the **agent-worktrees**, **agent-bridge**, and
**agent-codespaces** Copilot CLI plugins. All ship from this repo via the
Copilot CLI marketplace.

---

## Repository Structure

```
copilot-extensions/
  plugins/
    agent-worktrees/           # Worktree lifecycle plugin
      bin/                     # Binstubs (agent-worktrees, launch-session)
      docs/                    # Plugin documentation
      scripts/                 # Installers (init.ps1/sh, install.ps1/sh)
      skills/                  # Plugin-provided skills
      src/agent_worktrees/     # Python source
      terminal/                # Terminal config (tmux, tabby, psmux)
      hooks.json               # Plugin hooks
      plugin.json              # Plugin manifest
      pyproject.toml           # Python project config
    agent-bridge/              # Inter-agent communication plugin
      skills/                  # Plugin-provided skills
      src/agent_bridge/        # Python source
      tests/                   # Test suite
      docs/                    # Architecture, machine-config, getting-started
      libs/ssh-manager/        # Vendored SSH multiplexing library
      plugin.json              # Plugin manifest
      pyproject.toml           # Python project config
    agent-codespaces/          # GitHub Codespaces lifecycle + credential relay
      scripts/                 # Installers (init.ps1/sh, install.ps1/sh)
      skills/                  # codespaces-setup, codespaces-lifecycle
      src/agent_codespaces/    # Python source (CLI, relay, bridge provider)
      tests/                   # Test suite
      plugin.json              # Plugin manifest
      pyproject.toml           # Python project config
  docs/
    architecture.md            # Repo-level architecture overview
    plans/                     # Rollout + validation plans
  .github/
    plugin/
      marketplace.json         # Marketplace catalog (versions live here too)
  CONTRIBUTING.md              # Full versioning and release docs
```

---

## Three Plugins, Three Lifecycles

| Aspect | agent-worktrees | agent-bridge | agent-codespaces |
|--------|----------------|-------------|------------------|
| Type | Session plugin (hooks, skills) | Persistent HTTP service (9280 Win / 9281 WSL) | CLI + credential relay (9857) |
| Lifecycle | Per-session via Copilot CLI | Per-machine daemon (systemd / scheduled task) | On-demand CLI; relay runs in the bridge process |
| Runtime dir | `~/.agent-worktrees/` | `~/.agent-bridge/` | `~/.agent-codespaces/` |
| Binstub | `~/.local/bin/agent-worktrees[.cmd]` | `~/.local/bin/agent-bridge[.cmd]` | `~/.local/bin/agent-codespaces[.cmd]` |
| Test suite | -- | `plugins/agent-bridge/tests/` (pytest) | `plugins/agent-codespaces/tests/` (pytest) |

> The agent-bridge installer also imports the `agent_codespaces` package into
> its venv (for the `codespace:` resolver + relay) but does **not** own the
> `agent-codespaces` binstub — that belongs to `~/.agent-codespaces`.

---

## Contribution Rules

### Branch and Push

We own this repo -- branch directly, no fork or PR required. Use
descriptive branch names.

### Version Bump -- Required Before Every Push

**Every push to `main` must include a version bump.** The marketplace
detects updates by comparing versions. If you don't bump, machines will
report "already at latest" and silently skip the update.

Bump these files **in the same commit**, immediately before pushing:

**agent-worktrees:**

| File | Field(s) |
|------|----------|
| `plugins/agent-worktrees/plugin.json` | `version` |
| `plugins/agent-worktrees/pyproject.toml` | `version` under `[project]` |
| `.github/plugin/marketplace.json` | `metadata.version` AND `plugins[0].version` |

**agent-bridge:**

| File | Field(s) |
|------|----------|
| `plugins/agent-bridge/plugin.json` | `version` |
| `plugins/agent-bridge/pyproject.toml` | `version` under `[project]` |
| `.github/plugin/marketplace.json` | `plugins[1].version` |

**agent-codespaces:**

| File | Field(s) |
|------|----------|
| `plugins/agent-codespaces/plugin.json` | `version` |
| `plugins/agent-codespaces/pyproject.toml` | `version` under `[project]` |
| `.github/plugin/marketplace.json` | `plugins[2].version` |

Default bump: **patch with `-devN` suffix** (e.g., `1.3.1` -> `1.3.2-dev1`).
Do not bump minor or major unless the maintainer explicitly requests it.
See `CONTRIBUTING.md` for the full versioning scheme.

### Test Before Push

- **agent-bridge:** Run `pytest` from `plugins/agent-bridge/` before
  pushing. The test suite covers transport, sessions, config, and CLI.
- **agent-codespaces:** Run `pytest` from `plugins/agent-codespaces/` before
  pushing. Covers config, lifecycle, resolver, and the credential relay.
- **agent-worktrees:** No automated test suite yet. Verify worktree
  operations work end-to-end (create, finalize, cleanup).

### Deploy After Push

After pushing to `main`, update on each target machine:

```bash
# agent-worktrees -- via the update subcommand
agent-worktrees update

# agent-bridge -- via your project's service framework or the installer
# directly from the local checkout:
cd plugins/agent-bridge
./scripts/install.sh update    # Linux/WSL
.\scripts\install.ps1 update   # Windows

# agent-codespaces -- via its installer
cd plugins/agent-codespaces
./scripts/install.sh update    # Linux/WSL
.\scripts\install.ps1 update   # Windows
```

### Local Testing (Without Pushing)

Run the installer from the local checkout to deploy your uncommitted
changes through the real pipeline:

```powershell
# Windows -- agent-worktrees
cd plugins\agent-worktrees
.\scripts\install.ps1 update

# Windows -- agent-bridge
cd plugins\agent-bridge
.\scripts\install.ps1 update
```

```bash
# Linux/WSL -- agent-worktrees
cd plugins/agent-worktrees
./scripts/install.sh update

# Linux/WSL -- agent-bridge
cd plugins/agent-bridge
./scripts/install.sh update
```

---

## Code Standards

- **Python 3.10+**, type hints encouraged
- **uv** for all dependency operations -- never bare `pip`
- Docstrings for public functions
- Commit messages: imperative mood, descriptive
  ("Fix Unicode crash on cp1252 consoles")
- Include `Co-authored-by` trailer for Copilot-assisted commits

---

## What NOT to Do

- **Do not copy source files into the runtime directory**
  (`~/.agent-worktrees/lib/`, `~/.agent-bridge/venv/`). This bypasses
  version tracking, the installer pipeline, and leaves other machines
  on the old version. Always commit, bump, push, then update.
- **Do not push without a version bump.** Machines will silently ignore
  the update.
- **Do not edit installed plugin copies** under
  `~/.copilot/installed-plugins/`. The marketplace overwrites them on
  update. Fix the source here instead.
- **Do not mix up deployment paths.** agent-worktrees deploys via the
  marketplace + its own installer. agent-bridge deploys via its own
  installer (or a project service framework that wraps it). They are
  different pipelines.

---

## Key Files

| What | Where |
|------|-------|
| Marketplace catalog | `.github/plugin/marketplace.json` |
| agent-worktrees manifest | `plugins/agent-worktrees/plugin.json` |
| agent-bridge manifest | `plugins/agent-bridge/plugin.json` |
| agent-codespaces manifest | `plugins/agent-codespaces/plugin.json` |
| agent-worktrees Python source | `plugins/agent-worktrees/src/agent_worktrees/` |
| agent-bridge Python source | `plugins/agent-bridge/src/agent_bridge/` |
| agent-codespaces Python source | `plugins/agent-codespaces/src/agent_codespaces/` |
| agent-bridge tests | `plugins/agent-bridge/tests/` |
| agent-codespaces tests | `plugins/agent-codespaces/tests/` |
| Skills (agent-worktrees) | `plugins/agent-worktrees/skills/` |
| Skills (agent-bridge) | `plugins/agent-bridge/skills/` |
| Skills (agent-codespaces) | `plugins/agent-codespaces/skills/` |
| Hooks | `plugins/agent-worktrees/hooks.json` |
| Installers | `plugins/*/scripts/init.ps1, init.sh, install.ps1, install.sh` |
| Repo architecture overview | `docs/architecture.md` |
