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
agent-codespaces config adopt         # Register repo for config
agent-codespaces config show          # Show resolved config
agent-codespaces status               # Service + relay + tunnel state
```

## Development

```bash
cd plugins/agent-codespaces
pip install -e ".[dev]" -e "../../libs/ssh-manager[dev]"
pytest tests/
```
