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

## Connecting to CodeSpaces

All CodeSpace interaction should go through **agent-bridge**, not raw SSH.
CodeSpace agents are discovered automatically via the agent-codespaces
namespace resolver — any CodeSpace (running or stopped) is addressable
as `codespace:<name>`.

### Agent-Bridge CLI

| Command | Purpose |
|---------|---------|
| `agent-bridge agents` | List all available agents (local + codespace) |
| `agent-bridge send codespace:<name> "<prompt>"` | Start a new session (blocks until turn completes) |
| `agent-bridge send <session-id> "<prompt>"` | Send follow-up prompt on existing session |
| `agent-bridge send --no-wait <target> "<prompt>"` | Send without waiting — returns session ID immediately |
| `agent-bridge wait <session-id>` | Block until current turn completes |
| `agent-bridge sessions` | List all sessions with status |
| `agent-bridge sessions --status idle` | List sessions ready for follow-up |
| `agent-bridge stop <session-id>` | Pause session (preserves state for resume) |
| `agent-bridge resume <session-id>` | Resume a stopped session |
| `agent-bridge end <session-id>` | End and clean up session |

### Sync pattern (default — recommended for interactive use)

`agent-bridge send` blocks until the turn completes. Use when you need
the result before continuing.

```
powershell(command: 'agent-bridge send "codespace:<name>" "<prompt>"', initial_wait: 120)
```

### Async pattern (for long-running tasks)

Use `--no-wait` to dispatch and continue working.

```
powershell(mode: "async", command: 'agent-bridge send --no-wait "codespace:<name>" "<prompt>"')
# ... continue working ...
# [system_notification: shell completed]
powershell(command: 'agent-bridge wait <session-id>', initial_wait: 300)
```

### Multi-turn sessions

Sessions are persistent. After the first `send` creates a session, send
follow-ups using the session ID:

```bash
agent-bridge send "codespace:<name>" "Research the auth module"
# → Session abc123-def (keen-river) created

agent-bridge send abc123-def "Now implement the changes"
# → [response]

agent-bridge end abc123-def
```

### Startup and Shutdown Behavior

- **Shutdown CodeSpaces auto-start** when the bridge connects. Startup
  takes 60–120 s; the SSH layer retries automatically (up to ~180 s).
- **Do NOT pre-start CodeSpaces with manual SSH** — the bridge handles
  startup end-to-end.
- **Concurrency limit:** Max 4 active CodeSpaces. Check before creating:
  `gh codespace list --json name,state -q '[.[] | select(.state == "Available")] | length'`
- **Throttling:** 30 seconds between create/resume operations.

## SSH (Diagnostic Only)

SSH is for diagnostics and one-off commands, **not routine dispatch**.
If you find yourself using SSH for dispatch or status checks, diagnose
the bridge connection instead.

> **Always use `agent-codespaces ssh`**, not bare `gh codespace ssh`.
> Raw `gh codespace ssh` bypasses ssh-manager and can conflict with
> managed connections — duplicate ControlMaster sockets, missed
> credential relay tunnels, and orphan SSH processes.

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

## Listing and Status

```bash
agent-codespaces list
agent-codespaces list --json
agent-codespaces status
agent-codespaces version
```

## Creating and Deleting

```bash
# Create a CodeSpace on a repo + run on_create provisioning from codespaces.yaml
agent-codespaces create <owner/repo>
agent-codespaces create <owner/repo> --branch <branch> --display-name <name>
agent-codespaces create <owner/repo> --no-wait        # don't wait / skip provisioning

agent-codespaces delete <codespace-name>
agent-codespaces delete <codespace-name> --force

# Remove stale local state (orphaned SSH configs, ControlMaster sockets)
agent-codespaces cleanup
agent-codespaces cleanup --dry-run
```

CodeSpace creation uses `gh codespace create` with defaults from
`codespaces.yaml`. Per-repo overrides (machine type, location) apply
automatically based on the target repository.

## Syncing Dotfiles on CodeSpaces

Use `agent-codespaces ssh` to pull latest:
```bash
agent-codespaces ssh <name> --remote-cmd "cd /workspaces/.codespaces/.persistedshare/dotfiles && git pull origin main && bash install.sh"
```

If credential relay isn't active, pass the token via `--remote-cmd`:
```bash
token=$(gh auth token)
agent-codespaces ssh <name> --no-relay --remote-cmd "cd /workspaces/.codespaces/.persistedshare/dotfiles && git pull https://x-access-token:${token}@github.com/<user>/dotfiles.git main"
```

### Fresh clone (when .git is missing or corrupted)

```bash
token=$(gh auth token)
agent-codespaces ssh <name> --no-relay --remote-cmd "rm -rf /workspaces/.codespaces/.persistedshare/dotfiles && git clone https://x-access-token:${token}@github.com/<user>/dotfiles.git /workspaces/.codespaces/.persistedshare/dotfiles"
agent-codespaces ssh <name> --no-relay --remote-cmd "bash /workspaces/.codespaces/.persistedshare/dotfiles/install.sh"
```

> **Do NOT use `tar` or `git archive` pipes** to sync dotfiles. They
> destroy `.git` state, introduce CRLF from Windows, and leave stale
> files from renames/deletes. Always maintain a proper git clone.
>
> **Always use `agent-codespaces ssh`**, not bare `gh codespace ssh`.
> The latter bypasses ssh-manager and can conflict with managed
> connections (ControlMaster sockets, credential relay tunnels).

## Credential Relay

The credential relay is a TCP server (default port 9857) that runs on
the host machine. CodeSpaces connect to it via SSH reverse port
forwarding. It proxies credential requests to local credential stores.

### How It Works

1. Host runs the relay server on `127.0.0.1:9857`
2. SSH connection includes `-R 9857:localhost:9857`
3. CodeSpace sends git-credential-protocol requests to `localhost:9857`
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

## Agent-Bridge Integration

Register active CodeSpaces as dynamic agents with agent-bridge. Both
Available and Shutdown CodeSpaces are included (Shutdown ones auto-start
on connection).

```bash
agent-codespaces bridge register
agent-codespaces bridge register --ttl 600
agent-codespaces bridge status
agent-codespaces bridge unregister
```

### TTL and Refresh

Registrations expire after the TTL (default: 300s). For long sessions,
either increase the TTL or re-register periodically.

### Agent Naming

CodeSpaces are registered as `cs-<codespace-name>` (lowercase, max 64
chars). Use `agent-bridge agents` to see them after registration.

## Troubleshooting

- **SSH hangs** -- test with `agent-codespaces ssh <name> --remote-cmd "echo ok" --no-relay`.
  If that works, check credential relay. If it doesn't, verify
  `gh auth status` is authenticated.
- **Bridge connection fails** -- the bridge auto-starts Shutdown
  CodeSpaces and retries SSH (up to ~180 s). If it still fails, try
  `agent-codespaces ssh <name> --remote-cmd "echo ok" --no-relay`.
  Check `agent-bridge status` and `~/.agent-bridge/agent-bridge-err.log`.
- **Session fails on start** -- check `~/.agent-bridge/agent-bridge-err.log`.
  Common cause: wrong `ssh_user` in `codespaces.yaml`.
- **Credential relay not working** -- ensure relay port (9857) is not
  blocked. Check that `--no-relay` was not accidentally passed.
- **Quota exceeded** -- `gh codespace start` returns HTTP 400 "too many
  codespaces running". Stop idle CodeSpaces first, then retry.
- **"gh CLI not found"** -- install from https://cli.github.com/
- **WSL credential slowness** -- first GCM call through PowerShell
  takes ~25s. Subsequent calls use the 300s cache.
