---
name: codespaces-lifecycle
description: >
  GitHub Codespaces operations -- SSH into codespaces, list/delete/status,
  credential relay monitoring, and agent-bridge registration. Use this skill
  for day-to-day codespace management.
  Trigger phrases include:
  - 'codespace'
  - 'codespace ssh'
  - 'ssh into codespace'
  - 'list codespaces'
  - 'delete codespace'
  - 'codespace status'
  - 'credential relay'
  - 'relay status'
  - 'bridge register codespace'
  - 'codespace agent'
---

# Codespaces Lifecycle

Day-to-day operations for GitHub Codespaces via agent-codespaces. For
first-time setup and config changes, see the `codespaces-setup` skill.

## SSH into a CodeSpace

```bash
# Interactive SSH session (with credential relay tunnel)
agent-codespaces ssh <codespace-name>

# Run a command and return output
agent-codespaces ssh <codespace-name> --remote-cmd "ls -la"

# Structured stdio for agent-bridge transport
agent-codespaces ssh <codespace-name> --stdio --remote-cmd "copilot --acp --stdio"

# Skip credential relay tunnel setup
agent-codespaces ssh <codespace-name> --no-relay
```

SSH connections go through ssh-manager for multiplexing. The credential
relay port is automatically forwarded via SSH `-R` unless `--no-relay`
is specified.

## Listing and Status

```bash
# List all active codespaces
agent-codespaces list
agent-codespaces list --json

# Service status overview (adopted repos, config, tool availability)
agent-codespaces status

# Show version
agent-codespaces version
```

## Creating and Deleting

```bash
# Delete a codespace
agent-codespaces delete <codespace-name>
agent-codespaces delete <codespace-name> --force
```

CodeSpace creation uses `gh codespace create` with defaults from
`codespaces.yaml`. Per-repo overrides (machine type, location) apply
automatically based on the target repository.

## Credential Relay

The credential relay is a TCP server (default port 9847) that runs on
the host machine. CodeSpaces connect to it via SSH reverse port
forwarding. It proxies credential requests to local credential stores.

### How It Works

1. Host runs the relay server on `127.0.0.1:9847`
2. SSH connection includes `-R 9847:localhost:9847`
3. CodeSpace sends git-credential-protocol requests to `localhost:9847`
4. Relay routes to matching source (GCM, gh-auth, az-login)
5. Response flows back through the tunnel

### Available Sources

| Source | Action | What It Does |
|--------|--------|-------------|
| `git-credential` | `get`/`store`/`erase` | Proxies to local Git Credential Manager |
| `gh-auth` | `get-github-token` | Returns `gh auth token` output |
| `az-login` | `get-azure-token` | Returns Azure access tokens (opt-in) |

### Policy Enforcement

All requests pass through a policy gate before reaching any source:
- **Action allowlist** -- only recognized actions are accepted
- **Host allowlist** -- fnmatch-style patterns per source
- **Resource allowlist** -- exact-match for Azure resources (az-login)

Requests that fail policy checks are rejected before reaching the
credential store.

## Agent-Bridge Integration

Register active CodeSpaces as dynamic agents with agent-bridge. Each
`Available` codespace becomes a `command`-type agent that spawns via
`agent-codespaces ssh --stdio`.

```bash
# Register codespace agents with agent-bridge
agent-codespaces bridge register
agent-codespaces bridge register --ttl 600
agent-codespaces bridge register --bridge-url http://127.0.0.1:9280

# Check registration status
agent-codespaces bridge status

# Remove codespace agents from agent-bridge
agent-codespaces bridge unregister
```

### TTL and Refresh

Registrations expire after the TTL (default: 300s). For long sessions,
either increase the TTL or re-register periodically. Expired
registrations are cleaned up by agent-bridge automatically.

### Agent Naming

CodeSpaces are registered as `cs-<codespace-name>` (lowercase, max 64
chars). Use `agent-bridge agents` to see them after registration.

## Troubleshooting

- **SSH hangs** -- check `gh codespace ssh --config -c <name>` works
  directly. Verify `gh auth status` is authenticated.
- **Credential relay not working** -- ensure relay port (9847) is not
  blocked. Check that `--no-relay` was not accidentally passed.
- **Bridge registration fails** -- verify agent-bridge is running
  (`agent-bridge status`) and the auth token exists at
  `~/.agent-bridge/auth_token`.
- **"gh CLI not found"** -- install from https://cli.github.com/
- **WSL credential slowness** -- first GCM call through PowerShell
  takes ~25s. Subsequent calls use the 300s cache.
