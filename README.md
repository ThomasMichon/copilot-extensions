# copilot-extensions

A [Copilot CLI](https://docs.github.com/copilot/how-tos/use-copilot-agents/use-copilot-cli)
plugin marketplace for developer workflow automation.

## Plugins

| Plugin | Version | Description |
|--------|---------|-------------|
| [agent-worktrees](plugins/agent-worktrees/) | 1.3.1 | Worktree isolation system for concurrent Copilot CLI sessions |

---

## Agent Worktrees

Every Copilot CLI session gets its own isolated git worktree -- no branch
conflicts, no stale state, no stepping on parallel sessions. When a
session is marked complete, the worktree is squash-merged back to the
default branch and cleaned up automatically.

> **Note:** This README documents the plugin marketplace and installation.
> The Python package (`agent-worktrees`) is the runtime layer, normally
> installed by the plugin's init/install scripts rather than directly via
> `pip`.

### Architecture

Agent Worktrees has two layers: a **Copilot CLI plugin** (skills, hooks,
session-start wiring) and a **Python runtime** (the CLI tool that
actually manages worktrees, services, and session lifecycle).

```
Plugin layer (Copilot CLI)              Runtime layer (Python CLI)
  plugin.json                             ~/.agent-worktrees/
  hooks.json  -- sessionStart hook          .venv/           Python venv (shared)
  skills/     -- 6 skills loaded              lib/agent_worktrees/  Python package
                 into every session           bin/             launch-session.*, bootstrap-check.*
                                              projects.yaml    registry of adopted repos

                                            ~/.{project}/      per-project config + state
                                              config.yaml      repos, machine, launch commands
                                              worktrees/       per-worktree tracking YAML

                                            ~/.local/bin/
                                              {project}        binstub (Windows: .cmd)
                                              agent-worktrees  CLI tool
```

The plugin installs via the Copilot CLI marketplace and provides skills
and hooks to every session. The runtime installs separately (via the
plugin's init scripts or the `install.ps1`/`install.sh` installer) and
provides the `agent-worktrees` CLI and per-project binstubs.

### Prerequisites

- **Python 3.10+** on PATH
- **Git 2.15+** (worktree support)
- **Copilot CLI** (`copilot` command available)
- **uv** (Python package installer) -- the `init` scripts bootstrap `uv`
  automatically if missing; the full `install` scripts expect it on PATH

### Supported Platforms

| Platform | Installer | Terminal integration |
|----------|-----------|---------------------|
| Windows (PowerShell) | `install.ps1` | Windows Terminal fragments, psmux |
| Linux / WSL (bash) | `install.sh` | tmux, Tabby profiles |

---

## Installation

There are two independent things to install: the **plugin** (into Copilot
CLI) and the **runtime** (onto the machine). Both are required for full
functionality.

### Step 1: Install the Copilot CLI Plugin

The plugin provides skills and a session-start hook to every Copilot CLI
session. Install it from the marketplace:

```bash
# Register the marketplace (one-time per machine)
copilot plugin marketplace add ThomasMichon/copilot-extensions

# Install the plugin
copilot plugin install agent-worktrees@copilot-extensions
```

Or install directly without registering the marketplace:

```bash
copilot plugin install ThomasMichon/copilot-extensions:plugins/agent-worktrees
```

**What this does:** copies the plugin files (skills, hooks, scripts) into
`~/.copilot/installed-plugins/`. No Python runtime is installed yet.

### Step 2: Bootstrap the Runtime

The runtime is the Python package, venv, and shell wrappers that power
the `agent-worktrees` CLI. Bootstrap it one of two ways:

#### Option A: Ask Copilot (recommended)

Start any Copilot CLI session and say:

> *"set up agent-worktrees"*

This invokes the `agent-worktrees-init` skill, which runs the init
script for your platform.

#### Option B: Run the init script directly

```powershell
# Windows
$pluginDir = "$env:USERPROFILE\.copilot\installed-plugins\copilot-extensions\agent-worktrees"
powershell -NoProfile -ExecutionPolicy Bypass -File "$pluginDir\scripts\init.ps1"
```

```bash
# Linux / WSL
plugin_dir="$HOME/.copilot/installed-plugins/copilot-extensions/agent-worktrees"
bash "$plugin_dir/scripts/init.sh"
```

**What this does:** creates `~/.agent-worktrees/` with a Python venv,
the `agent_worktrees` package, shell wrappers, and the `agent-worktrees`
binstub in `~/.local/bin/`.

### Step 3: Register a Project (Adopt a Repo)

Register a git repo so it gets its own worktree-isolated binstub:

```bash
cd /path/to/your/repo
agent-worktrees register my-project
```

Or ask Copilot from inside the repo:

> *"adopt this repo for worktree sessions"*

**What this does:** creates `~/.{project}/config.yaml`, a project
binstub in `~/.local/bin/{project}`, terminal profiles, and worktree
tracking state.

### Step 4: Launch a Session

```bash
my-project          # opens the worktree picker
```

The picker shows active worktrees and offers to create new ones or work
in the base repo. Each new worktree gets an isolated branch.

---

## Programmatic Installation (Outside Copilot)

For automated machine setup, CI, or scripted provisioning -- skip Copilot
entirely and use the installers directly.

### Full Install from a Repo Checkout

The installer must run from the **target project repo** (or receive an
explicit project name). Clone copilot-extensions for the installer
source, then run it from your project directory:

```powershell
# Windows -- clone the extension, then install from your project repo
git clone https://github.com/ThomasMichon/copilot-extensions.git C:\Src\copilot-extensions
cd C:\Src\my-project
C:\Src\copilot-extensions\plugins\agent-worktrees\scripts\install.ps1 install -ProjectName my-project
```

```bash
# Linux / WSL
git clone https://github.com/ThomasMichon/copilot-extensions.git ~/src/copilot-extensions
cd ~/src/my-project
bash ~/src/copilot-extensions/plugins/agent-worktrees/scripts/install.sh install --project-name my-project
```

Or split runtime install from project registration:

```bash
# Install runtime only (from anywhere)
cd ~/src/copilot-extensions/plugins/agent-worktrees
bash scripts/install.sh install

# Then register a project (from the project repo)
cd ~/src/my-project
agent-worktrees register my-project
```

### Installer Actions

The `install.ps1` and `install.sh` scripts support these lifecycle
actions:

| Action | Description |
|--------|-------------|
| `install` | Full deploy: runtime, binstub, config, terminal profiles, deploy manifest |
| `uninstall` | Remove runtime and binstub. Add `--remove-config` to also delete config/metadata |
| `status` | Check deployed runtime, config, PATH, worktrees, deploy provenance |
| `update` | Re-deploy runtime + binstub from repo source, refresh marketplace plugin |
| `update-config` | Regenerate config.yaml (use `--force` to overwrite existing) |
| `start` | N/A (not a daemon) |
| `stop` | N/A (not a daemon) |

### Installer Flags

| Flag | Platform | Description |
|------|----------|-------------|
| `-ProjectName` / `--project-name` | Both | Project name (auto-detected from repo if omitted) |
| `-Force` / `--force` | Both | Overwrite config without confirmation |
| `-RemoveConfig` / `--remove-config` | Both | On uninstall: also delete config and session metadata |
| `-Machine` / `--machine` | Windows | Machine name (auto-detected if omitted) |

### Remote Deployment via SSH

```bash
# Deploy to another machine
ssh my-machine "cd ~/src/copilot-extensions/plugins/agent-worktrees && bash scripts/install.sh update"
```

---

## Updating

### Plugin Updates (Copilot Marketplace)

When a project session launches via its binstub, the `launch-session`
wrapper runs `copilot plugin update agent-worktrees@copilot-extensions`
to sync the installed plugin files with the latest version on GitHub.
This applies to marketplace-installed plugins; direct installs are
detected but marketplace update is skipped.

Check for updates manually:

```bash
copilot plugin update agent-worktrees@copilot-extensions
```

### Runtime Updates

The runtime updates when you run the installer's `update` action from a
repo checkout that has newer code:

```powershell
# Windows
cd C:\Src\copilot-extensions\plugins\agent-worktrees
.\scripts\install.ps1 update
```

```bash
# Linux / WSL
cd ~/src/copilot-extensions/plugins/agent-worktrees
bash scripts/install.sh update
```

This re-deploys the Python package, refreshes shell wrappers, updates
terminal profiles, and writes a deploy manifest with provenance info
(commit, timestamp, source paths).

### Pre-Flight Auto-Update

The `launch-session` wrappers perform a pre-flight check on each
session launch. If the anchor repo has new commits that affect the
worktree manager or vault infrastructure, the launcher automatically
re-runs the installer before proceeding. This keeps deployed code
current without manual intervention.

The pre-flight update can be skipped with `--no-update` or by setting
`WORKTREE_NO_UPDATE=1`.

### Version Checking

```bash
agent-worktrees --version
```

All three version sources must agree:

| File | Purpose |
|------|---------|
| `plugin.json` | Copilot CLI marketplace version detection |
| `pyproject.toml` | Python runtime `--version` output |
| `.github/plugin/marketplace.json` | GitHub-hosted marketplace catalog |

See [CONTRIBUTING.md](CONTRIBUTING.md) for the versioning and release
workflow.

---

## What the Plugin Provides

### Skills

Skills are loaded automatically into every Copilot CLI session. They
give the agent knowledge about worktree workflows, service deployment,
and project setup.

| Skill | Description |
|-------|-------------|
| `worktree` | Worktree lifecycle -- creation, finalization, cleanup, commit policy, safety rules |
| `service-lifecycle` | Service installer patterns -- deploy, update, status, config drift detection |
| `agent-worktrees-init` | Bootstrap the shared runtime on a new machine |
| `agent-worktrees-adopt` | Adopt a repo -- create per-project config and binstubs |
| `agent-worktrees-wsl-provision` | Provision the current project in WSL from a Windows host |
| `create-setup-script` | Generate repo-specific session setup scripts |

### Hooks

| Hook | Trigger | What it does |
|------|---------|--------------|
| `sessionStart` | Every Copilot CLI session | Runs `bootstrap-check.{ps1,sh}` to verify the runtime is installed. Prints a setup hint if not. 15-second timeout. |

### Terminal Integration

| File | Platform | Description |
|------|----------|-------------|
| `psmux.conf` | Windows | psmux multiplexer config with opt-in prefix interception |
| `tmux.conf` | Linux/WSL | tmux config with the same opt-in philosophy |
| `tabby-aperture-labs.yaml` | Linux | Tabby terminal profile template, merged by the Linux installer |

The Windows installer also generates a **Windows Terminal fragment** at
`%LOCALAPPDATA%\Microsoft\Windows Terminal\Fragments\AgentWorktrees\`
with profiles for each registered project (local + remote SSH machines).

---

## Worktree Session Lifecycle

```
{project}                         # launch binstub
  |
  v
launch-session.{ps1,sh}           # pre-flight update, venv activation
  |
  v
agent-worktrees resolve           # Python: picker UI, worktree creation
  |                                 emits JSON launch plan, exits
  v
Setup script runs                  # tools/setup/setup.{ps1,sh} or config-driven
  |
  v
Copilot CLI session                # your work happens here
  |
  v
Post-exit checks                   # detect completion markers
  |
  +-- status: complete --> finalize (squash, rebase, ff-merge, push, cleanup)
  +-- status: active   --> preserve worktree for later resume
```

### Finalization Flow

When a session is marked complete (via the `worktree` skill or
`mark-worktree-complete`), the finalization flow:

1. Acquires a local lock
2. Fetches from origin
3. Squashes commits on the worktree branch
4. Rebases onto `origin/{default_branch}`
5. Fast-forward merges into local `{default_branch}`
6. Pushes to origin (with retry on rejection)
7. Removes the worktree directory and branch
8. Updates tracking YAML to `status: finalized`

On any failure, the worktree is preserved and marked `status: orphaned`.

### Recovery Mode

```bash
my-project -Recovery    # Windows
my-project recovery     # Linux/WSL or positional keyword
```

Skips vault credential loading for debugging broken bootstrap
infrastructure. Uses the anchor repo's setup script instead of the
worktree's copy.

---

## Installed Layout

After full installation and project registration:

```
~/.agent-worktrees/                 # Shared runtime (one per machine)
  .venv/                            #   Python virtual environment
  lib/agent_worktrees/              #   Python package
  bin/                              #   Shell wrappers
    launch-session.{ps1,cmd,sh}     #     Session launcher
    bootstrap-check.{ps1,sh}        #     Session-start health check
  projects.yaml                     #   Registry of adopted projects
  deploy-manifest.json              #   Provenance (commit, timestamp)
  aperture-science.ico              #   Icon for terminal profiles

~/.{project}/                       # Per-project config + state
  config.yaml                       #   Machine, repos, launch commands
  worktrees/                        #   Per-worktree tracking
    {worktree-id}.yaml              #     status: active|complete|finalized|orphaned

~/.local/bin/                       # Binstubs on PATH
  agent-worktrees{.cmd}             #   CLI tool
  {project}{.cmd}                   #   Project launcher (one per registered repo)
  cleanup-worktrees{.cmd}           #   Bulk worktree cleanup
  mark-worktree-complete{.cmd}      #   Mark worktree done / set title
```

### Config Reference

`~/.{project}/config.yaml`:

```yaml
srcroot: C:\Data\Src              # or ~/src on Linux
machine: my-machine
platform: windows                 # windows | wsl | linux
repo_name: my-project

repos:
  my-project:
    anchor: C:\Data\Src\my-project
    worktree_root: C:\Data\Src\.worktrees\my-project
    default_branch: main
    remote: origin
```

---

## CLI Reference

```bash
agent-worktrees <subcommand> [options]
```

### Session Lifecycle

| Subcommand | Description |
|------------|-------------|
| `resolve` | Interactive picker -- select or create a worktree, emit JSON launch plan |
| `create` | Create a new worktree non-interactively |
| `mark-complete` | Mark a worktree as complete (triggers finalization on exit) |
| `finalize` | Squash-merge a completed worktree back to the default branch |
| `cleanup` | List and remove orphaned or finalized worktrees |
| `status` | Show worktree git status |
| `list` | List worktrees from tracking records |
| `handoff` | Manage handoff prompt state on a worktree |

### Installation & Config

| Subcommand | Description |
|------------|-------------|
| `install` | Full deploy: runtime + project config + binstubs + terminal profiles |
| `register` | Register a new project (create config + binstub without full reinstall) |
| `uninstall` | Remove worktree manager |
| `update` | Re-deploy runtime from repo source |
| `install-status` | Show installation and deployment status |
| `deploy-instructions` | Deploy `machine.instructions.md` from `machines.yaml` |
| `get` | Query config values (e.g., `agent-worktrees get repo-dir`) |

### Services & Validation

| Subcommand | Description |
|------------|-------------|
| `services` | Service discovery, staleness checks, passthrough to service installers |
| `validate` | Validate core infrastructure files |
| `pre-launch` | Check bootstrap staleness (JSON output, used by launch wrappers) |

### Development

| Subcommand | Description |
|------------|-------------|
| `dev` | Dev venv and test runner |
| `--version` | Print installed version |

---

## Multiple Projects

Register multiple repos on the same machine. Each gets its own config
directory and binstub; the shared runtime is installed once:

```bash
agent-worktrees install --project-name my-app      # from ~/src/my-app
agent-worktrees install --project-name dotfiles     # from ~/src/dotfiles
```

Launch each independently:

```bash
my-app          # worktree picker for my-app
dotfiles        # worktree picker for dotfiles
```

---

## Further Reading

| Document | Description |
|----------|-------------|
| [Getting Started](plugins/agent-worktrees/docs/getting-started.md) | Anchor repo recommendations, setup scripts, session lifecycle |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Versioning, release workflow, marketplace architecture |

## License

[MIT](LICENSE)
