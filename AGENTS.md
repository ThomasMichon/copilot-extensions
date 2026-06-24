# Copilot Extensions -- Development Guide

Source of truth for the **agent-worktrees**, **agent-bridge**,
**agent-codespaces**, **agent-containers**, and **agent-mcp** Copilot CLI
plugins. All ship from this repo via the Copilot CLI marketplace.

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
    agent-containers/          # Local Docker dev-container fleet + container: resolver
      scripts/                 # Installer (init.ps1/sh)
      skills/                  # containers-fleet
      src/agent_containers/    # Python source (CLI, lease broker, resolver)
      tests/                   # Test suite
      plugin.json              # Plugin manifest
      pyproject.toml           # Python project config
    agent-mcp/                 # Swiss-army MCP bridge (wrap upstream MCP + auth + decorator stack)
      scripts/                 # Installer (init.ps1/sh)
      skills/                  # agent-mcp
      src/agent_mcp/           # Python source (bridge, pipeline, transports, auth, decorators)
      tests/                   # Test suite
      plugin.json              # Plugin manifest
      pyproject.toml           # Python project config
    efforts/                   # Efforts planning system (pure skill plugin -- no runtime)
      skills/                  # planning-efforts (+ references/, assets/), efforts-setup
      plugin.json              # Plugin manifest
  docs/
    architecture.md            # Repo-level architecture overview
    plans/                     # Rollout + validation plans
  .github/
    plugin/
      marketplace.json         # Marketplace catalog (versions live here too)
  CONTRIBUTING.md              # Full versioning and release docs
```

---

## Five Plugins, Five Lifecycles

| Plugin | Type | Lifecycle | Runtime dir | Binstub | Tests |
|--------|------|-----------|-------------|---------|-------|
| agent-worktrees | Session plugin (hooks, skills) | Per-session via Copilot CLI | `~/.agent-worktrees/` | `agent-worktrees[.cmd]` | -- |
| agent-bridge | Persistent HTTP service (9280 Win / 9281 WSL) | Per-machine daemon (systemd / scheduled task) | `~/.agent-bridge/` | `agent-bridge[.cmd]` | `plugins/agent-bridge/tests/` |
| agent-codespaces | CLI + credential relay (9857) | On-demand CLI; relay runs in the bridge process | `~/.agent-codespaces/` | `agent-codespaces[.cmd]` | `plugins/agent-codespaces/tests/` |
| agent-containers | CLI + `container:` resolver | On-demand CLI; resolver runs in the bridge process | `~/.agent-containers/` | `agent-containers[.cmd]` | `plugins/agent-containers/tests/` |
| agent-mcp | Standalone MCP bridge (stdio) | Spawned per-call by an agent's `mcp-servers` entry | `~/.agent-mcp/` | `agent-mcp[.cmd]` | `plugins/agent-mcp/tests/` |

All binstubs live in `~/.local/bin/`.

> The agent-bridge installer also imports the `agent_codespaces` **and**
> `agent_containers` packages into its venv (for the `codespace:` / `container:`
> resolvers + relay) but does **not** own their binstubs — those belong to
> `~/.agent-codespaces` and `~/.agent-containers` respectively. agent-mcp is
> standalone: it has no bridge resolver and is invoked directly from an agent's
> `mcp-servers` config.

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

**agent-containers:**

| File | Field(s) |
|------|----------|
| `plugins/agent-containers/plugin.json` | `version` |
| `plugins/agent-containers/pyproject.toml` | `version` under `[project]` |
| `.github/plugin/marketplace.json` | `plugins[3].version` |

**agent-mcp:**

| File | Field(s) |
|------|----------|
| `plugins/agent-mcp/plugin.json` | `version` |
| `plugins/agent-mcp/pyproject.toml` | `version` under `[project]` |
| `.github/plugin/marketplace.json` | `plugins[4].version` |

**efforts:** (pure skill plugin -- no `pyproject.toml`/runtime)

| File | Field(s) |
|------|----------|
| `plugins/efforts/plugin.json` | `version` |
| `.github/plugin/marketplace.json` | `plugins[7].version` |

Default bump: **patch with `-devN` suffix** (e.g., `1.3.1` -> `1.3.2-dev1`).
Do not bump minor or major unless the maintainer explicitly requests it.
See `CONTRIBUTING.md` for the full versioning scheme.

### Test Before Push

- **agent-bridge:** Run `pytest` from `plugins/agent-bridge/` before
  pushing. The test suite covers transport, sessions, config, and CLI.
- **agent-codespaces:** Run `pytest` from `plugins/agent-codespaces/` before
  pushing. Covers config, lifecycle, resolver, and the credential relay.
- **agent-containers:** Run `pytest` from `plugins/agent-containers/` before
  pushing. Covers config, lifecycle, the lease broker, and the resolver.
- **agent-mcp:** Run `pytest` from `plugins/agent-mcp/` before pushing. Covers
  config loading, auth injectors, transports, bridge framing, the decorator
  pipeline (filter/rename/defer/code-mode/storage), and an end-to-end stdio
  bridge run. The code-mode Node tests skip automatically when `node` is absent.
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

# agent-containers / agent-mcp -- re-run init (no separate installer)
cd plugins/agent-containers     # or plugins/agent-mcp
./scripts/init.sh --force       # Linux/WSL
.\scripts\init.ps1 -Force       # Windows
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
| agent-containers manifest | `plugins/agent-containers/plugin.json` |
| agent-mcp manifest | `plugins/agent-mcp/plugin.json` |
| agent-worktrees Python source | `plugins/agent-worktrees/src/agent_worktrees/` |
| agent-bridge Python source | `plugins/agent-bridge/src/agent_bridge/` |
| agent-codespaces Python source | `plugins/agent-codespaces/src/agent_codespaces/` |
| agent-containers Python source | `plugins/agent-containers/src/agent_containers/` |
| agent-mcp Python source | `plugins/agent-mcp/src/agent_mcp/` |
| agent-bridge tests | `plugins/agent-bridge/tests/` |
| agent-codespaces tests | `plugins/agent-codespaces/tests/` |
| agent-containers tests | `plugins/agent-containers/tests/` |
| agent-mcp tests | `plugins/agent-mcp/tests/` |
| Skills (agent-worktrees) | `plugins/agent-worktrees/skills/` |
| Skills (agent-bridge) | `plugins/agent-bridge/skills/` |
| Skills (agent-codespaces) | `plugins/agent-codespaces/skills/` |
| Skills (agent-containers) | `plugins/agent-containers/skills/` |
| Skills (agent-mcp) | `plugins/agent-mcp/skills/` |
| Hooks | `plugins/agent-worktrees/hooks.json` |
| Installers | `agent-worktrees`/`agent-codespaces`: `scripts/init.*` + `scripts/install.*`; `agent-bridge`: `scripts/install.*`; `agent-containers`/`agent-mcp`: `scripts/init.*` only |
| Repo architecture overview | `docs/architecture.md` |
