# Copilot Extensions -- Development Guide

Source of truth for the copilot-extensions Copilot CLI plugins. The **canonical
plugin roster** lives in [`.github/plugin/marketplace.json`](.github/plugin/marketplace.json)
(mirrored, with descriptions, in the [README](README.md) and
[`docs/architecture.md`](docs/architecture.md)). All ship from this repo via the
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

## Plugins and Lifecycles

The suite spans many plugins. The **canonical plugin list, the runtime-vs-
payload split, and the per-plugin lifecycle tables** live in
[`docs/architecture.md`](docs/architecture.md) (and the
[README](README.md) plugin table) — derived from
[`.github/plugin/marketplace.json`](.github/plugin/marketplace.json), which is
the single source of truth. **Don't re-enumerate the plugin roster here** — that
duplicate is exactly what drifts. All binstubs live in `~/.local/bin/`.

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

### Coordinating Across Control Repos

This repo is public and may be driven from **multiple downstream/control repos**
at once. Two rules keep them from colliding and keep private context off the
public face:

- **Claim work with a GitHub issue** before starting a stretch -- search open
  issues first, then take or comment on one. It's the shared token other drivers
  and outside contributors can see.
- **Keep every public artifact generic.** Commits, issues, and docs are
  world-readable -- write them self-contained, with no downstream-private names,
  systems, or context. The proprietary "why" stays in the driver's own private
  planning, which links to the public issue.

Pushes to `main` are single-writer: rebase before pushing and re-check your
version bump in case a concurrent push already consumed it.

### Version Bump -- Required Before Every Push

**Every push to `main` must include a version bump** for each plugin you
changed. The marketplace detects updates by comparing versions; skip the bump
and machines report "already at latest" and silently ignore your change.

For **each plugin `<p>` you touched**, bump these **in the same commit**:

| File | Field | When |
|------|-------|------|
| `plugins/<p>/plugin.json` | `version` | always |
| `plugins/<p>/pyproject.toml` | `version` under `[project]` | runtime plugins only (payload-only plugins have no `pyproject.toml`) |
| `.github/plugin/marketplace.json` | the `version` on `<p>`'s entry in `plugins[]` (find it **by name**, not a hardcoded index) | always |

Two extra rules for the marketplace catalog:

- **agent-worktrees** additionally bumps `metadata.version` (the catalog's own
  version).
- **Adding a new plugin** appends a `plugins[]` entry **and** bumps
  `metadata.version`.

Default bump: **patch with a `-devN` suffix** (e.g., `1.3.1` -> `1.3.2-dev1`).
Do not bump minor or major unless the maintainer explicitly requests it.
See `CONTRIBUTING.md` for the full versioning scheme. (The
`tools/check-docs-consistency.py` guard keeps the plugin lists/counts in the
docs honest; run it before pushing doc changes.)

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
