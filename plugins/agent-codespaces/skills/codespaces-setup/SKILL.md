---
name: codespaces-setup
description: >
  GitHub Codespaces setup and adoption -- create codespaces.yaml, adopt repos,
  configure credential relay sources, and validate config. Use this skill for
  first-time setup or config changes, not day-to-day operations.
  Trigger phrases include:
  - 'codespace setup'
  - 'codespace config'
  - 'adopt codespace'
  - 'codespaces.yaml'
  - 'configure codespace'
  - 'credential relay setup'
  - 'az-login relay'
  - 'codespace credentials'
---

# Codespaces Setup

One-time setup and configuration management for agent-codespaces. For
day-to-day operations (SSH, listing, bridge), see the `codespaces-lifecycle`
skill.

## Prerequisites

- **gh CLI** -- installed and authenticated (`gh auth login`)
- **ssh-manager** -- installed via the copilot-extensions plugin
- **agent-bridge** (optional) -- only needed for bridge provider features

## Adoption Workflow

### 1. Create `codespaces.yaml` in your repo

```yaml
# codespaces.yaml -- CodeSpace defaults and credential relay config
defaults:
  machine_type: largePremiumLinux     # gh codespace machine type
  location: EastUs                     # Azure region
  ssh_user: vscode                     # SSH user (match CodeSpace user)
  workspace_folder: /workspaces/odsp-web  # repo root on CodeSpace
  # dotfiles_repo: user/dotfiles      # Optional dotfiles repo

credentials:
  relay_port: 9847                     # TCP port for credential relay
  sources:
    git-credential:
      enabled: true
      allowed_hosts:
        - "github.com"
        - "*.github.com"
        - "dev.azure.com"
        - "*.visualstudio.com"
    gh-auth:
      enabled: true
      allowed_hosts:
        - "github.com"
    # az-login:                        # Azure token relay (DISABLED by default)
    #   enabled: false
    #   allowed_resources:
    #     - "https://management.azure.com/"

repos:
  org/my-repo:
    machine_type: largePremiumLinux256gb
    location: EastUs
```

### 2. Adopt the repo

```bash
cd /path/to/your/repo
agent-codespaces config adopt
```

This registers the repo path in `~/.agent-codespaces/adopted-repos.yaml`.
The service reads `codespaces.yaml` live from adopted repos on every
operation -- no generated intermediate config.

### 3. Validate

```bash
agent-codespaces config validate
agent-codespaces config show
```

## Config Reference

### `defaults`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `machine_type` | string | `largePremiumLinux` | Default VM size for `gh codespace create` |
| `location` | string | `EastUs` | Default Azure region |
| `dotfiles_repo` | string | -- | Dotfiles repo for CodeSpace provisioning |
| `ssh_user` | string | `vscode` | SSH user on CodeSpaces |
| `workspace_folder` | string | -- | Workspace root on CodeSpace (e.g., `/workspaces/odsp-web`). Used to `cd` before launching Copilot, preventing CWD race conditions during cold starts. |
| `acp_command` | string | -- | Explicit override for the remote agent command. If omitted, built automatically from `workspace_folder`. |

#### `workspace_folder`

The absolute path to the repo checkout on the CodeSpace. When set, the
remote agent command becomes `cd <workspace_folder> && copilot --acp --stdio`,
which ensures Copilot starts in the correct directory even when a
cold-started CodeSpace's workspace volume hasn't been mounted by the time
the SSH login profile runs.

```yaml
defaults:
  workspace_folder: /workspaces/odsp-web
```

**Why this matters:** CodeSpace profile scripts (`/etc/profile.d/codespaces.sh`)
run `cd $WORKING_DIRECTORY` during login shell init, but during cold starts
from Shutdown state, the workspace volume may not be ready when SSH first
connects. Without `workspace_folder`, Copilot can start in `/home/vscode`
instead of the repo root, causing "not in a git repository" errors.

#### `acp_command` (advanced)

Explicit override for the entire remote command. If set, this takes
priority over `workspace_folder`. Use only when you need a completely
custom entry point:

```yaml
defaults:
  # acp_command: "/workspaces/my-wrapper.sh"    # custom wrapper
  # acp_command: "copilot --acp --stdio"         # bare (no cd prefix)
```

### `credentials`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `relay_port` | int | `9847` | TCP port for credential relay server |
| `sources` | dict | -- | Pluggable credential source configs |

### Credential Sources

#### `git-credential`

Proxies requests to local Git Credential Manager. Handles standard git
credential actions (`get`/`store`/`erase`). On WSL, routes through
PowerShell to reach Windows-side GCM. Includes credential caching
(300s TTL) and request coalescing.

```yaml
git-credential:
  enabled: true
  allowed_hosts:
    - "github.com"
    - "*.github.com"      # fnmatch-style globbing
    - "dev.azure.com"
```

#### `gh-auth`

Returns GitHub auth tokens via `gh auth token`. Handles the
`get-github-token` action only.

```yaml
gh-auth:
  enabled: true
  allowed_hosts:
    - "github.com"
```

#### `az-login`

Returns Azure access tokens via `az account get-access-token`. Handles
the `get-azure-token` action only. **Disabled by default** -- this is a
high-trust operation.

```yaml
az-login:
  enabled: false          # Must be explicitly enabled
  allowed_resources:      # Exact-match allowlist (required when enabled)
    - "https://management.azure.com/"
    - "https://graph.microsoft.com/"
```

**Security implications:** Enabling this grants the CodeSpace access
equivalent to the host machine's current `az login` session for the
listed resources. Tokens are bearer credentials with broad cloud control
potential. Use only with trusted CodeSpaces and narrow resource scopes.

- Tokens are cached until 5 minutes before expiry
- Token values are never logged (only resource/tenant metadata)
- Requests for unlisted resources are denied with a clear error
- Requires `az login` on the host running the relay

### `repos`

Per-target-repo overrides. Keys are `org/repo` identifiers:

```yaml
repos:
  org/my-repo:
    machine_type: largePremiumLinux256gb   # Override default
    location: WestUs2                      # Override default
```

## Multi-Repo Adoption

Multiple repos can be adopted. Config merges in memory:
- **Defaults:** first adopted repo wins
- **Credential sources:** union across repos (hosts are merged)
- **Target repos:** first definition wins on key conflicts

## CLI Reference

```bash
agent-codespaces config adopt       # Register current repo
agent-codespaces config show        # Show resolved config
agent-codespaces config validate    # Validate config
```

## Troubleshooting

- **"No codespaces.yaml found"** -- Create the file in repo root first
- **"Already adopted"** -- Repo is already registered, check with `config show`
- **Config warnings about empty allowed_hosts** -- Add host patterns or
  disable the source
