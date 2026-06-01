# Agent Bridge -- Machine Configuration

This guide covers how to configure the machine topology that agent-bridge
uses to discover and connect to agents across your infrastructure.

Agent-bridge and agent-worktrees both consume **the same `machines.yaml`
file** -- agent-worktrees uses it for terminal profiles and SSH sessions;
agent-bridge uses it for agent subprocess spawning and SSH transport.

## Overview

Agent-bridge topology is configured via **profiles** in
`~/.agent-bridge/config.yaml`. Each profile points to two files in your
project repo:

| File | Purpose |
|------|---------|
| `machines.yaml` | Machine inventory -- hostnames, SSH environments, platforms |
| `acp-agents.json` | Agent definitions -- which agents run on which machines |

## Quick Setup

### Auto-adopt from a repo

```bash
agent-bridge config adopt --repo /path/to/repo --profile my-project
```

This searches for `machines.yaml` and `acp-agents.json` at conventional
paths and creates a topology profile.

### Verify

```bash
agent-bridge config show       # show config with resolved paths
agent-bridge config validate   # check file existence and structure
agent-bridge machines          # list discovered machines
agent-bridge agents            # list available agents
```

---

## machines.yaml Format

`machines.yaml` defines the machines in your infrastructure. Each machine
has a name, hostname, platform, and optional SSH environment.

```yaml
machines:
  workstation:
    hostname: my-workstation
    platform: windows
    ssh_environment: powershell
    roles:
      - development
      - compilation

  server:
    hostname: my-server
    platform: linux
    ssh_environment: bash
    roles:
      - services
      - deployment

  server-wsl:
    hostname: my-workstation
    platform: wsl
    ssh_environment: bash
    wsl_distro: Ubuntu
    roles:
      - development
```

### Machine Fields

| Field | Required | Description |
|-------|----------|-------------|
| `hostname` | Yes | Network hostname or SSH alias |
| `platform` | Yes | `windows`, `linux`, or `wsl` |
| `ssh_environment` | No | `bash` or `powershell` (default: inferred from platform) |
| `wsl_distro` | No | WSL distribution name (only for `platform: wsl`) |
| `roles` | No | List of role tags for documentation |

### Platform Values

- **`windows`** -- native Windows, SSH lands in PowerShell
- **`linux`** -- native Linux or remote Linux server
- **`wsl`** -- Windows Subsystem for Linux on a Windows host

### SSH Aliases

Machine `hostname` values should match SSH aliases configured in your
`~/.ssh/config`. Agent-bridge uses `ssh <hostname>` to connect, so the
alias must resolve to the correct host, user, port, and key.

### File Locations

`config adopt` searches these paths (first match wins):

1. `{repo}/machines.yaml`
2. `{repo}/config/machines.yaml`
3. `{repo}/.github/machines.yaml`

---

## acp-agents.json Format

`acp-agents.json` defines the agents available across your machines.
Each agent has a name, type, host machine, and optional configuration.

```json
[
  {
    "name": "workstation-wsl",
    "type": "copilot-cli",
    "host": "workstation-wsl",
    "description": "Copilot CLI agent on workstation WSL",
    "spawnable": true
  },
  {
    "name": "server",
    "type": "copilot-cli",
    "host": "server",
    "description": "Copilot CLI agent on the server",
    "spawnable": true,
    "spawn_command": ["copilot", "--acp", "--stdio"]
  }
]
```

### Agent Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Unique agent identifier (used in CLI: `agent-bridge send <name>`) |
| `type` | Yes | Agent type (`copilot-cli` for Copilot CLI agents) |
| `host` | Yes | Machine name from `machines.yaml` |
| `description` | No | Human-readable description |
| `spawnable` | No | Whether agent-bridge can spawn this agent (default: `true`) |
| `spawn_command` | No | Custom spawn command (default: `["copilot", "--acp", "--stdio"]`) |

### Agent Names

Agent names are used in CLI commands:

```bash
agent-bridge send workstation-wsl "Check disk space"
agent-bridge send server "Run the test suite"
```

### File Locations

`config adopt` searches these paths (first match wins):

1. `{repo}/tools/mcp/acp-agents.json`
2. `{repo}/acp-agents.json`
3. `{repo}/config/acp-agents.json`

---

## Topology Profiles

Profiles live in `~/.agent-bridge/config.yaml` under the `topologies` key:

```yaml
port: 9280
bind: 127.0.0.1
log_level: info

topologies:
  my-project:
    machines_yaml: /home/user/src/my-project/machines.yaml
    agents_config: /home/user/src/my-project/tools/mcp/acp-agents.json

  another-project:
    machines_yaml: /home/user/src/other/machines.yaml
    agents_config: /home/user/src/other/acp-agents.json
```

### Path Conventions

- Use **forward slashes** on all platforms (including Windows) for config
  portability
- Paths are resolved relative to the config file location
- Tilde (`~`) expansion is supported

### Managing Profiles

```bash
# Add/update a profile
agent-bridge config adopt --repo /path/to/repo --profile my-project

# With explicit file paths
agent-bridge config adopt \
  --repo /path/to/repo \
  --profile my-project \
  --machines-yaml /custom/machines.yaml \
  --agents-config /custom/agents.json

# Remove a profile
agent-bridge config remove my-project

# Show all profiles
agent-bridge config show

# Validate file paths and structure
agent-bridge config validate
```

---

## Relationship to Agent-Worktrees

Both plugins consume `machines.yaml` but for different purposes:

| Aspect | agent-worktrees | agent-bridge |
|--------|----------------|-------------|
| **Reads** | `machines.yaml` | `machines.yaml` + `acp-agents.json` |
| **Uses for** | Terminal profiles, SSH session targets | Agent spawning, SSH transport |
| **Config location** | `~/.{project}/config.yaml` | `~/.agent-bridge/config.yaml` |
| **When configured** | During repo adoption (`register`) | During topology adoption (`config adopt`) |

The `copilot-extensions-setup` skill handles both in sequence: adopt the
repo for worktrees, then wire the topology for agent-bridge.

---

## Creating machines.yaml from Scratch

If your project doesn't have a `machines.yaml` yet, create one in your
repo root:

### Minimal (single machine)

```yaml
machines:
  local:
    hostname: localhost
    platform: linux
```

### Multi-machine with SSH

```yaml
machines:
  dev-workstation:
    hostname: dev-ws        # must match ~/.ssh/config alias
    platform: windows
    ssh_environment: powershell

  dev-wsl:
    hostname: dev-ws-wsl    # SSH alias for WSL on dev-ws
    platform: wsl
    ssh_environment: bash
    wsl_distro: Ubuntu

  build-server:
    hostname: build-srv     # SSH alias
    platform: linux
    ssh_environment: bash
```

Then create `acp-agents.json` alongside it:

```json
[
  {
    "name": "dev-wsl",
    "type": "copilot-cli",
    "host": "dev-wsl",
    "description": "Dev workstation WSL agent",
    "spawnable": true
  },
  {
    "name": "build-server",
    "type": "copilot-cli",
    "host": "build-server",
    "description": "Build server agent",
    "spawnable": true
  }
]
```

### Adopt into agent-bridge

```bash
agent-bridge config adopt --repo /path/to/repo --profile my-infra
agent-bridge config validate
agent-bridge machines
agent-bridge agents
```

---

## Troubleshooting

### "No machines found"

- Check `agent-bridge config show` for the topology profile paths
- Verify the files exist at those paths
- Run `agent-bridge config validate` for detailed diagnostics

### "Agent not found"

- Run `agent-bridge agents` to see available agents
- Check that the agent's `host` in `acp-agents.json` matches a machine
  name in `machines.yaml`

### SSH connection failures

- Verify SSH aliases work directly: `ssh <hostname> echo ok`
- Check `agent-bridge machines` for SSH readiness status
- Ensure SSH keys are configured for passwordless auth

### Config changes not taking effect

Restart agent-bridge after modifying config:

```bash
# Windows
agent-bridge stop; agent-bridge start

# Linux/WSL
systemctl --user restart agent-bridge.service
```
