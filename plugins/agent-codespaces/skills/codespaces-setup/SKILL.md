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

- **gh CLI** -- installed **and authenticated with the `codespace` scope**:
  ```bash
  gh auth login
  gh auth refresh -h github.com -s codespace   # default login scopes omit this
  ```
  Without the `codespace` scope, CodeSpace operations fail with
  `HTTP 403 ... needs the "codespace" scope`. `agent-codespaces config init`
  runs a preflight that flags this with the exact fix.
- **ssh-manager** -- installed via the copilot-extensions plugin
- **agent-bridge** (optional) -- only needed for bridge provider features

## Adoption Workflow

### 1. Create `codespaces.yaml` in your repo

**Fastest path -- scaffold it from your existing CodeSpaces:**

```bash
cd /path/to/your/repo
agent-codespaces config init
```

`config init` runs `gh codespace list` and, if you already have at least one
CodeSpace, derives sensible defaults into a new `codespaces.yaml`:

- **`machine_type`** -- the most common machine across your CodeSpaces.
- **`repos:` entry** -- the CodeSpaces repository.
- **`workspace_folder`** -- discovered from a live (Available) CodeSpace by
  reading `$WORKING_DIRECTORY` over SSH. If no CodeSpace is Available it is
  left as a clearly-marked **TODO** rather than guessed -- the CodeSpaces repo
  name is often **not** the checkout path (e.g. a `*-codespaces` repo whose
  workspace is `/workspaces/<app>`).

Flags: `--from-codespace <name>` (derive from a specific one), `--force`
(overwrite), `--adopt` (register immediately after writing). With no
CodeSpaces, it writes a generic template to fill in.

> All org/account/URL values live in **your** repo's `codespaces.yaml`, derived
> at runtime from your own `gh` account -- never hardcoded in the plugin.

**Or author it by hand:** copy the full annotated example,
[`references/codespaces.yaml`](references/codespaces.yaml), into your repo root
and adapt it. Its shape at a glance:

```yaml
# codespaces.yaml -- CodeSpace defaults and credential relay config
defaults:
  machine_type: largePremiumLinux     # gh codespace machine type
  location: EastUs                     # Azure region
  ssh_user: vscode                     # SSH user (match CodeSpace user)

credentials:
  relay_port: 9857                     # TCP port for credential relay
  sources:
    git-credential: { enabled: true, allowed_hosts: ["github.com", "*.visualstudio.com"] }
    gh-auth:        { enabled: true, allowed_hosts: ["github.com"] }

repos:
  <your-org>/<your-repo>-codespaces:
    machine_type: largePremiumLinux256gb
    workspace_repo: <your-repo>        # -> agents launch in /workspaces/<your-repo>
```

See [`references/codespaces.yaml`](references/codespaces.yaml) for every field
(credential sources, per-repo provisioning, `workspace_folder` overrides) with
inline comments. The per-field reference is in **Config Reference** below.

### 1b. Declare your dotfiles repo (account-wide, one-time)

GitHub Codespaces clones **one** dotfiles repo — chosen once for your **whole
account** at <https://github.com/settings/codespaces> — into *every* CodeSpace
(via the post-start script, at `/workspaces/.codespaces/.persistedshare/dotfiles`).
It is **not** per-repo configurable, and **GitHub exposes no API to read which
repo you picked**, so agent-codespaces can't auto-discover it. Declare it **once**
so connect-time housekeeping (dotfiles sync-forward, auth re-shim) knows where
your dotfiles live:

```yaml
defaults:
  dotfiles_repo: <your-user>/dotfiles    # the repo set at github.com/settings/codespaces
```

- The value must match your **account** dotfiles setting (commonly
  `<your-user>/dotfiles`, but any single repo you configured). This field only
  **records** that choice — setting it here does **not** change your GitHub
  account setting (do that in the web UI).
- The repo should contain an **`install.sh`** at its root that performs your
  CodeSpace setup (symlink skills, install relay shims, etc.). Connect-time
  housekeeping runs `bash install.sh` after syncing the repo forward. Verify it
  exists:
  ```bash
  gh api repos/<your-user>/dotfiles/contents/install.sh --jq .name   # expect: install.sh
  ```

#### Control-plane repo == or != dotfiles repo

If the repo you adopt for CodeSpaces config (your **control plane**) **is** your
account dotfiles repo (a common setup), you're done — `dotfiles_repo` just names
it.

If they **differ** (control plane is e.g. `org/my-harness`, account dotfiles is
`<your-user>/dotfiles`), make the relationship explicit so cross-repo flows can
find and update the dotfiles repo as a good citizen:

1. **Link it as a related repo** from your control plane (see the
   `agent-worktrees-related` skill):
   ```bash
   agent-worktrees related add <your-user>/dotfiles --role tooling \
     --summary "Account dotfiles repo cloned into every CodeSpace; hosts install.sh." \
     --delegate none
   ```
2. **Scaffold a `repo-<dotfiles>` skill** in your control plane describing how to
   update that dotfiles repo (its branch/PR conventions, what `install.sh` does,
   how to test a change on a CodeSpace), so future sessions edit it knowingly
   rather than guessing.

### 1c. (Optional) Declare a separate control-plane *harness* repo

`dotfiles_repo` above is the GitHub-dotfiles **shim** (the account-wide repo with
`install.sh`, cloned to `/workspaces/.codespaces/.persistedshare/dotfiles`). It is
**distinct** from your control-plane **harness** — the repo that carries your
effort / vision / planning state. Historically the two were the same repo, so the
dotfiles clone doubled as the harness; if you've split them (a renamed harness +
a minimal dotfiles shim), name the harness separately:

```yaml
defaults:
  harness_repo: <your-org>/<harness>     # the repo carrying your effort/vision state
```

- **Opt-in / default OFF.** With `harness_repo` **unset** (the default), **no
  harness is placed on a venue** — the *local* control-plane agent manages effort
  updates, and the on-venue agent works the product repo directly (no extra
  checkout, no game-of-telephone).
- When **set**, connect-time housekeeping clones/ff-syncs the harness onto the
  venue at **`/workspaces/<basename>`** — the standard repo-layout convention,
  same as any named repo (no bespoke harness path) — over the credential relay.
  Unlike the dotfiles shim, **no `install.sh` is run** — the harness is
  referenced in place, not installed. A parked feature branch / dirty tree is
  never touched.
- **The plugin only *materializes* the repo; the *interop* is a skill concern.**
  Telling an on-venue repo agent that an effort lives in the harness — which repo
  it is, that it's at `/workspaces/<harness>`, and how to reference/update it (or,
  when the harness is OFF, that the host relays effort context and owns updates)
  — is handled by your control-plane's own skills, not by this config.
- This is purely additive: it does **not** change the dotfiles bootstrap, and
  both can be set independently.

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
| `dotfiles_repo` | string | -- | Your **account-wide** dotfiles repo (the single repo GitHub clones into every CodeSpace; set at `github.com/settings/codespaces`). Records the choice so connect-time housekeeping finds it — GitHub has no API to read it. See "Declare your dotfiles repo" above. Not per-repo. |
| `harness_repo` | string | -- | Optional control-plane **harness** repo (effort/vision state), **distinct** from `dotfiles_repo`. When set, cloned/ff-synced to `/workspaces/<basename>` (the standard repo-layout convention) on connect — no `install.sh`. **Unset = OFF** → no on-venue harness; the local agent owns effort updates. On-venue interop is a skill concern (see "Declare a separate control-plane harness repo" above). |
| `ssh_user` | string | `vscode` | SSH user on CodeSpaces |
| `devcontainer_path` | string | `.devcontainer/devcontainer.json` | Fallback devcontainer config, used **only** when a repo exposes more than one discoverable `devcontainer.json` (otherwise `gh codespace create` prompts and hard-fails headless). Single-devcontainer repos are unaffected — the flag is passed only when there are multiple. Override per-repo (`repos.<repo>.devcontainer_path`) or per-create (`--devcontainer-path`). |
| `workspace_folder` | string | -- | **Global** workspace root applied to every CodeSpace (e.g., `/workspaces/<your-repo>`). Used to `cd` before launching Copilot, preventing CWD race conditions during cold starts. When you adopt more than one CodeSpaces repo, prefer per-repo `repos.<repo>.workspace_repo`/`workspace_folder` instead (see `repos`). |
| `acp_command` | string | -- | Explicit override for the remote agent command. If omitted, built automatically from the resolved workspace folder. |

#### `workspace_folder`

The absolute path to the repo checkout on the CodeSpace. When set, the
remote agent command becomes `cd <workspace_folder> && copilot --acp --stdio`,
which ensures Copilot starts in the correct directory even when a
cold-started CodeSpace's workspace volume hasn't been mounted by the time
the SSH login profile runs.

```yaml
defaults:
  workspace_folder: /workspaces/<your-repo>
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
| `relay_port` | int | `9857` | TCP port for credential relay server |
| `ado_host` | string | -- | Default Azure DevOps host (e.g. `<your-org>.visualstudio.com`) for bare `get-access-token` requests that carry no host (npm/nuget via ado-auth-helper). Unset = such requests are rejected. Can also be set via the `CODESPACES_ADO_HOST` env var on the relay host. |
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

Per-target-repo overrides. Keys are `org/repo` identifiers -- **the CodeSpaces
repository** (the repo your CodeSpaces are created from):

```yaml
repos:
  org/my-repo:
    machine_type: largePremiumLinux256gb   # Override default
    location: WestUs2                      # Override default
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `machine_type` | string | from `defaults` | VM size for this repo's CodeSpaces |
| `location` | string | from `defaults` | Azure region for this repo's CodeSpaces |
| `workspace_repo` | string | -- | The **product repo** this CodeSpaces repo hosts. Records the directional "we consume CodeSpaces from here for repo X" link (mirrors agent-worktrees' *related repos*). The remote workspace folder derives from it as `/workspaces/<basename>`. |
| `workspace_folder` | string | from `workspace_repo`, then `defaults` | Explicit per-repo workspace root override. Use when the checkout path isn't `/workspaces/<basename(workspace_repo)>`. |
| `devcontainer_path` | string | from `defaults`, then canonical `.devcontainer/devcontainer.json` | Which devcontainer config `gh codespace create` builds from. Consulted **only** when the repo has multiple discoverable devcontainers (e.g. a local-Docker config alongside the CodeSpaces one); pins headless create to the right one. An agent can still override per-create with `agent-codespaces create --devcontainer-path`. |
| `provision` | map | -- | Repo-specific provision hooks (see Provisioning). |

#### Per-repo workspace folder (CodeSpaces repo ≠ checkout)

A CodeSpaces repo frequently differs from the product checkout it hosts:
`org/odsp-web-codespaces` serves a `/workspaces/odsp-web` checkout. Deriving the
folder from the CodeSpaces repo name would give the **wrong**
`/workspaces/odsp-web-codespaces`. Record the relationship once with
`workspace_repo`; agents launched for that CodeSpace (via agent-bridge or the
`codespace:` resolver) then land in the right directory:

```yaml
repos:
  odsp-microsoft/odsp-web-codespaces:
    machine_type: largePremiumLinux256gb
    workspace_repo: odsp-web        # -> agents launch in /workspaces/odsp-web
```

Resolution order for a CodeSpace's workspace folder (most specific wins):
`repos.<repo>.workspace_folder` > derived from `repos.<repo>.workspace_repo`
(`/workspaces/<basename>`) > global `defaults.workspace_folder` > the
remote-resolved fallback (`$CODESPACE_VSCODE_FOLDER`/`$VM_REPO_PATH`). Prefer a
per-repo `workspace_repo` over a global `defaults.workspace_folder` whenever you
adopt more than one CodeSpaces repo -- the global default applies to **every**
CodeSpace regardless of repo.

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
