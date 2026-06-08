# copilot-extensions

A [Copilot CLI](https://docs.github.com/copilot/how-tos/use-copilot-agents/use-copilot-cli)
plugin marketplace for developer workflow automation.

## Plugins

| Plugin | Type | Description |
|--------|------|-------------|
| [agent-worktrees](plugins/agent-worktrees/) | Session tool | Worktree isolation for concurrent Copilot CLI sessions |
| [agent-bridge](plugins/agent-bridge/) | Persistent service | Inter-agent communication, SSH transport, machine mesh |
| [agent-codespaces](plugins/agent-codespaces/) | Session tool + CLI | GitHub Codespaces lifecycle, SSH transport, credential relay |

**Which do I need?**

- **agent-worktrees** -- every machine that runs Copilot CLI sessions.
  Gives each session its own git worktree. Install this first.
- **agent-bridge** -- machines that need to talk to agents on other
  machines. Runs as an always-on HTTP service (port 9280). Requires
  agent-worktrees.
- **agent-codespaces** -- machines that create, manage, or SSH into
  GitHub Codespaces. Provides the `codespace:<name>` namespace resolver
  for agent-bridge routing, credential relay (git-credential, gh-auth,
  az-login), and SSH multiplexing via ssh-manager. Requires
  agent-worktrees; integrates with agent-bridge for inter-agent
  communication.

All plugins ship from this repo and install via the Copilot CLI
marketplace. All support **Windows** and **Linux/WSL** (macOS planned).

## Prerequisites

- **Copilot CLI** (`copilot` command on PATH)
- **Python 3.10+**
- **Git 2.15+**
- **uv** (bootstrapped automatically by init scripts if missing)

## Quick Start

### 1. Register the marketplace (one-time)

```bash
copilot plugin marketplace add ThomasMichon/copilot-extensions
```

### 2. Install the plugins

```bash
copilot plugin install agent-worktrees@copilot-extensions
```

Agent-codespaces does not yet have marketplace installer scripts. Install
from the local checkout instead:

```bash
cd plugins/agent-codespaces
pip install -e ".[dev]" -e "../../libs/ssh-manager[dev]"
```

### 3. Bootstrap the runtime

Start a Copilot CLI session and say:

> *"set up agent-worktrees"* -- or -- *"set up agent-bridge"*

The `copilot-extensions-setup` skill handles init, repo adoption, and
topology wiring interactively. See the per-plugin docs for details:

- [Agent Worktrees -- Getting Started](plugins/agent-worktrees/docs/getting-started.md)
- [Agent Bridge -- Getting Started](plugins/agent-bridge/docs/getting-started.md)

## Updating

```bash
# Update the plugin from the marketplace
copilot plugin update agent-worktrees@copilot-extensions

# Or use the built-in update command (plugin + runtime in one step)
agent-worktrees update
```

Agent-worktrees also auto-updates on each session launch via the
`launch-session` wrapper.

## Documentation

### Agent Worktrees

| Document | Description |
|----------|-------------|
| [README](plugins/agent-worktrees/README.md) | Plugin overview |
| [Getting Started](plugins/agent-worktrees/docs/getting-started.md) | Install, adopt a repo, launch sessions |
| [Architecture](plugins/agent-worktrees/docs/architecture.md) | Plugin/runtime layers, installed layout, session lifecycle |
| [CLI Reference](plugins/agent-worktrees/docs/cli-reference.md) | Commands, installer actions, config format |

### Agent Bridge

| Document | Description |
|----------|-------------|
| [README](plugins/agent-bridge/README.md) | Plugin overview |
| [Getting Started](plugins/agent-bridge/docs/getting-started.md) | Install, configure, start the service |
| [Architecture](plugins/agent-bridge/docs/architecture.md) | Service design, API reference, deployment |
| [Machine Configuration](plugins/agent-bridge/docs/machine-config.md) | Topology setup -- machines.yaml, acp-agents.json |

### Agent Codespaces

| Document | Description |
|----------|-------------|
| [README](plugins/agent-codespaces/README.md) | Plugin overview, CLI reference, config format |
| [codespaces-setup](plugins/agent-codespaces/skills/codespaces-setup/SKILL.md) | First-time setup, adoption, credential relay config |
| [codespaces-lifecycle](plugins/agent-codespaces/skills/codespaces-lifecycle/SKILL.md) | Day-to-day operations -- SSH, listing, bridge integration |

### Repo-Level

| Document | Description |
|----------|-------------|
| [CONTRIBUTING](CONTRIBUTING.md) | Versioning, release workflow, deployment pipeline |

## License

[MIT](LICENSE)
