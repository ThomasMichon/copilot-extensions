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
