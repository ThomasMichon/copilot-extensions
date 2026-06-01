# Agent Worktrees

Copilot CLI plugin for worktree-isolated sessions. Every Copilot CLI
session gets its own git worktree -- no branch conflicts, no stale state,
no stepping on parallel sessions.

## How It Works

Agent Worktrees has two layers:

- **Plugin** (skills, hooks) -- loaded into every Copilot CLI session
- **Runtime** (Python CLI) -- manages worktrees, launches sessions,
  handles finalization

The plugin installs via the Copilot CLI marketplace. The runtime installs
separately via init scripts and provides the `agent-worktrees` CLI and
per-project binstubs.

## Getting Started

See [Getting Started](docs/getting-started.md) for install, repo
adoption, and session launch.

## Docs

| Document | Description |
|----------|-------------|
| [Getting Started](docs/getting-started.md) | Install, adopt a repo, launch sessions |
| [Architecture](docs/architecture.md) | Plugin/runtime layers, installed layout, session lifecycle |
| [CLI Reference](docs/cli-reference.md) | Commands, installer actions, config format |

## Skills

| Skill | Description |
|-------|-------------|
| `worktree` | Worktree lifecycle -- creation, finalization, cleanup, safety rules |
| `service-lifecycle` | Service installer patterns -- deploy, update, status |
| `copilot-extensions-setup` | Install and adopt for both plugins |
| `agent-worktrees-wsl-provision` | Provision the current project in WSL |
| `agent-worktrees-repos` | Repos registry -- known repos and source roots |
| `create-setup-script` | Generate repo-specific session setup scripts |
| `agent-ssh` | SSH transport helpers |

## Hooks

| Hook | Trigger | What it does |
|------|---------|--------------|
| `sessionStart` | Every session | Verifies runtime is installed; prints setup hint if not |

## Platforms

| Platform | Installer | Terminal integration |
|----------|-----------|---------------------|
| Windows | `install.ps1` | Windows Terminal fragments, psmux |
| Linux/WSL | `install.sh` | tmux, Tabby profiles |
| macOS | Planned | -- |
