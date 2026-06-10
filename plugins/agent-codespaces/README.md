# agent-codespaces

GitHub Codespaces lifecycle management, SSH transport, and credential relay
for Copilot CLI.

## Overview

A copilot-extensions plugin that provides:

- **SSH transport** -- multiplexed SSH connections to CodeSpaces via
  ssh-manager, wrapping `gh codespace ssh --config`
- **Lifecycle management** -- create, delete, list, and status for CodeSpaces
- **Credential relay** -- forward git credentials, GitHub tokens, and
  Azure tokens to CodeSpaces over SSH tunnels (pluggable sources:
  git-credential, gh-auth, az-login)
- **Agent-bridge provider** -- register CodeSpaces as dynamic agents in
  agent-bridge for inter-agent communication

## Configuration

All configuration lives in the **adopting repo** in a `codespaces.yaml` file.
The service reads config live from adopted repos -- no generated intermediate
config.

```yaml
# codespaces.yaml
defaults:
  machine_type: largePremiumLinux
  location: EastUs

credentials:
  sources:
    git-credential:
      enabled: true
      allowed_hosts:
        - "*.visualstudio.com"
        - "dev.azure.com"
    gh-auth:
      enabled: true
      allowed_hosts:
        - "github.com"
    # az-login:                        # Azure token relay (disabled by default)
    #   enabled: false
    #   allowed_resources:
    #     - "https://management.azure.com/"

repos:
  org/my-repo:
    machine_type: largePremiumLinux256gb
    location: EastUs
```

## CLI

```bash
agent-codespaces ssh <name>           # SSH into a CodeSpace
agent-codespaces ssh --stdio <name>   # Structured SSH for agent-bridge
agent-codespaces list                 # List active CodeSpaces
agent-codespaces create <owner/repo>  # Create a CodeSpace + run provisioning
agent-codespaces delete <name>        # Delete a CodeSpace (--force to skip prompt)
agent-codespaces config adopt         # Register repo for config
agent-codespaces config init          # Scaffold codespaces.yaml from your CodeSpaces
agent-codespaces config show          # Show resolved config
agent-codespaces bridge register      # Register CodeSpaces as bridge agents
agent-codespaces cleanup              # Remove stale local state (SSH configs, sockets)
agent-codespaces status               # Service + relay + tunnel state
agent-codespaces version              # Show version
```

### `create` options

```bash
agent-codespaces create <owner/repo> \
  --branch <branch> \           # branch to create on (default: repo default)
  --display-name <name> \       # CodeSpace display name
  --timeout 300 \               # seconds to wait for Available (default 300)
  --no-wait                     # don't wait / skip provisioning
```

Machine type and location come from `codespaces.yaml` (per-repo overrides
apply). After the CodeSpace is Available, `on_create` provisioning hooks from
`codespaces.yaml` run automatically.

### `bridge` options

```bash
agent-codespaces bridge register   [--ttl 300] [--bridge-url <url>]
agent-codespaces bridge refresh    [--ttl 300] [--bridge-url <url>]
agent-codespaces bridge status     [--bridge-url <url>]
agent-codespaces bridge unregister [--bridge-url <url>]
```

> **Linux/WSL:** the bridge defaults to port **9281**, but these commands
> default `--bridge-url` to `http://127.0.0.1:9280`. On Linux/WSL pass
> `--bridge-url http://127.0.0.1:9281` explicitly.

## Development

```bash
cd plugins/agent-codespaces
pip install -e ".[dev]" -e "../../libs/ssh-manager[dev]"
pytest tests/
```
