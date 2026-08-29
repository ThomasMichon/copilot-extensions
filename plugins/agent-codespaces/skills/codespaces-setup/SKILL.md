---
name: codespaces-setup
description: >
  GitHub Codespaces setup and adoption -- work out of the box on standard
  CodeSpaces, or add supplementary .agent-codespaces/config.yaml for repos that
  deviate (split CodeSpaces-vs-product repos, pinned devcontainers, ADO hosts,
  provision hooks). Use for first-time setup or config changes, not day-to-day
  operations.
  Trigger phrases include:
  - 'codespace setup'
  - 'codespace config'
  - 'adopt codespace'
  - 'agent-codespaces config'
  - 'configure codespace'
  - 'credential relay setup'
  - 'az-login relay'
  - 'codespace credentials'
---

# Codespaces Setup

One-time setup and configuration for agent-codespaces. For day-to-day
operations (SSH, listing, bridge), see the `codespaces-lifecycle` skill.

Use the exact `argv` from the agent-codespaces session command catalog for
CodeSpace operations and the exact `argv` from the agent-worktrees catalog for
related-repo management. For dispatch, use the exact `argv` prefix from the
session command catalog for agent-bridge and replace
`<agent-bridge catalog argv prefix>` below with its shell-ready rendering. Append the arguments
shown below; never substitute a same-named plugin command found through `PATH`.

## Readiness — agent-codespaces provisions its own runtime (standalone)

This plugin is **self-provisioning and standalone**: it needs no
worktree manager or session launcher.

In an agent session, invoke the exact `argv` from the session command catalog.
The payload-local command provisions its runtime on first use (vendors `uv` if
missing and builds the venv), printing a `::agent-provisioning::` line and
progress. This can take ~30–120s; let it finish and do not kill it.

If a host loads the skill but does not inject a session command catalog, do not
fall back to ambient `PATH` or scan every installed marketplace. Select the
payload explicitly through that host's plugin management surface, then invoke
its payload-local command or stamp a management binstub from that chosen
payload:

- Windows:
  `pwsh -NoProfile -ExecutionPolicy Bypass -File "<explicit-payload-path>\scripts\install.ps1" stamp`
- POSIX:
  `bash "<explicit-payload-path>/scripts/install.sh" stamp`

If a call reports a provisioning failure (for example, `uv is required…` or a
network/TLS error), surface the exact message and stop. Do not improvise a
toolchain install. On a governed box, provide the internal index
(`UV_DEFAULT_INDEX=<pip index-url>`) or install `uv`, then retry.

This is agent-codespaces' own runtime readiness. Its vendored Python libraries
(`ssh-manager`, `credential-relay`, `config-migrate`, `plugin-resolve`) are
installed into that runtime automatically; the external prerequisites below are
only host tools/sibling services it cannot self-install.

## Most repos need no config -- start here

The runtime works **out of the box** on standard GitHub CodeSpaces by
**convention**:

- machine `largePremiumLinux`, location `EastUs`
- in-CodeSpace checkout at `/workspaces/<repo-basename>`
- credential relay serving `github.com` **and** Azure DevOps (through the host
  Git Credential Manager) when the optional agent-bridge relay is running;
  relay-free lifecycle/diagnostic commands still work without it

So for a repo whose CodeSpaces match convention, there is **nothing to
configure**:

```bash
<agent-codespaces catalog argv prefix> create <your-org>/<standard-repo>
<agent-codespaces catalog argv prefix> doctor
<agent-bridge catalog argv prefix> send codespace:<name> "<task>"
```

Add config **only** when a repo deviates from convention. The rest of this skill
is about that supplementary config.

## Prerequisites

- **gh CLI** -- installed **and authenticated with the `codespace` scope**:
  ```bash
  gh auth login
  gh auth refresh -h github.com -s codespace   # default login scopes omit this
  ```
  Without the `codespace` scope, CodeSpace operations fail with
  `HTTP 403 ... needs the "codespace" scope`.
  `<agent-codespaces catalog argv prefix> doctor` checks
  the ambient account and any mapped accounts and prints the exact remedy.
- **agent-bridge** (optional sibling) -- needed for `codespace:<name>`
  dispatch and for the managed host credential relay. The agent-codespaces
  CLI/binstub itself remains standalone; lifecycle commands and relay-free
  diagnostic SSH (`--no-relay`) still work without a bridge daemon.

## When you DO need config -- `.agent-codespaces/config.yaml`

Supplementary config lives **in the adopting repo**, in the canonical in-repo
location aligned with the sibling `agent-*` plugins (e.g.
`.agent-worktrees/config.yaml`):

```
<repo>/.agent-codespaces/config.yaml
```

It carries **only** the CodeSpace-specific bits convention can't derive. The
common cases:

- a **split** CodeSpaces-vs-product repo (`org/app-codespaces` hosting a
  `/workspaces/app` checkout) -> `workspace_repo`
- a repo that ships **multiple devcontainers** -> `devcontainer_path` (else
  headless `create` prompts and hangs)
- a bare Azure DevOps `get-access-token` host -> `credentials.ado_host`
- repo-specific **provision** hooks

### 1. Scaffold + adopt (one step)

From inside the repo:

```bash
cd /path/to/your/repo
<agent-codespaces catalog argv prefix> config init
```

`config init`:

- writes a **supplementary-only** `.agent-codespaces/config.yaml` (deriving what
  it can from your existing CodeSpaces via `gh codespace list`), and
- **auto-adopts** the repo (registers its path in
  `~/.agent-codespaces/adopted-repos.yaml`) so the detached agent-bridge daemon
  reads it too. No separate `config adopt` step.

If your repo already matches convention, `config init` will tell you so and the
file it writes is safe to delete.

**Or author it by hand:** copy the annotated example,
[`references/config.yaml`](references/config.yaml), to
`.agent-codespaces/config.yaml` and adapt. Then run
`<agent-codespaces catalog argv prefix> config adopt`.

> **Auto-discovery.** Running any `agent-codespaces` command *inside* a repo that
> carries `.agent-codespaces/config.yaml` picks it up automatically -- adoption
> only persists it for the daemon and for extra/multi-repo setups.

### 2. Migrate a legacy `codespaces.yaml`

A repo-root `codespaces.yaml` (the former location) is still read as a
back-compat fallback. Relocate it to the canonical location:

```bash
cd /path/to/your/repo
<agent-codespaces catalog argv prefix> config migrate
```

Adoption is unaffected (the manifest tracks the repo root, not the file). Commit
the move.

### 3. Declare your dotfiles repo (account-wide, one-time)

GitHub Codespaces clones **one** dotfiles repo -- chosen once for your **whole
account** at <https://github.com/settings/codespaces> -- into *every* CodeSpace
(at `/workspaces/.codespaces/.persistedshare/dotfiles`). It is **not** per-repo,
and **GitHub exposes no API to read which repo you picked**, so declare it once
under `defaults` so connect-time housekeeping (dotfiles sync-forward, auth
re-shim) knows where it lives:

```yaml
defaults:
  dotfiles_repo: <your-user>/dotfiles    # the repo set at github.com/settings/codespaces
```

- This field only **records** the choice -- it does not change your GitHub
  account setting (do that in the web UI).
- The repo should contain an **`install.sh`** at its root. Connect-time
  housekeeping runs `bash install.sh` after syncing the repo forward:
  ```bash
  gh api repos/<your-user>/dotfiles/contents/install.sh --jq .name   # expect: install.sh
  ```

#### Control-plane repo == or != dotfiles repo

If the repo you adopt for CodeSpaces config (your **control plane**) **is** your
account dotfiles repo, you're done -- `dotfiles_repo` just names it. If they
**differ**, make the relationship explicit so cross-repo flows can find and
update the account dotfiles repo:

1. Link it as a related repo (see the `agent-worktrees:agent-worktrees-related` skill):
   ```bash
   <agent-worktrees catalog argv prefix> related add <your-user>/dotfiles --role tooling \
     --summary "Account dotfiles repo cloned into every CodeSpace; hosts install.sh." \
     --delegate none
   ```
2. Scaffold a `repo-<dotfiles>` skill in your control plane describing how to
   update that repo (branch/PR conventions, what `install.sh` does, how to test
   a change on a CodeSpace).

### 4. (Optional) Declare a separate control-plane *harness* repo

`dotfiles_repo` is the GitHub-dotfiles **shim**. It is **distinct** from your
control-plane **harness** -- the repo that carries your effort / vision / planning
state. If you've split them, name the harness separately:

```yaml
defaults:
  harness_repo: <your-org>/<harness>     # the repo carrying your effort/vision state
```

- **Opt-in / default OFF.** With `harness_repo` **unset**, no harness is placed
  on a venue -- the local control-plane agent manages effort updates and the
  on-venue agent works the product repo directly.
- When **set**, connect-time housekeeping clones/ff-syncs the harness onto the
  venue at **`/workspaces/<basename>`** (no `install.sh` -- it's referenced, not
  installed). A parked feature branch / dirty tree is never touched.
- The plugin only *materializes* the repo; the *interop* (telling the on-venue
  agent that an effort lives in the harness) is a skill concern.

### 5. Validate

```bash
<agent-codespaces catalog argv prefix> config validate
<agent-codespaces catalog argv prefix> config show
```

## Config Reference

Config is read live from the repo (canonical `.agent-codespaces/config.yaml`, or
legacy `codespaces.yaml`) -- no generated intermediate file.

### `defaults`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `machine_type` | string | `largePremiumLinux` | Default VM size for `gh codespace create` |
| `location` | string | `EastUs` | Default Azure region |
| `dotfiles_repo` | string | -- | Your **account-wide** dotfiles repo (the single repo GitHub clones into every CodeSpace; set at `github.com/settings/codespaces`). Records the choice so connect-time housekeeping finds it -- GitHub has no API to read it. Not per-repo. |
| `harness_repo` | string | -- | Optional control-plane **harness** repo (effort/vision state), **distinct** from `dotfiles_repo`. When set, cloned/ff-synced to `/workspaces/<basename>` on connect -- no `install.sh`. **Unset = OFF**. |
| `ssh_user` | string | `vscode` | SSH user on CodeSpaces |
| `devcontainer_path` | string | `.devcontainer/devcontainer.json` | Fallback devcontainer config, used **only** when a repo exposes more than one discoverable `devcontainer.json` (otherwise `gh codespace create` prompts and hard-fails headless). Override per-repo or per-create (`--devcontainer-path`). |
| `workspace_folder` | string | -- | **Global** workspace root applied to every CodeSpace. Prefer per-repo `repos.<repo>.workspace_repo`/`workspace_folder` when you adopt more than one CodeSpaces repo. |
| `acp_command` | string | -- | Explicit override for the remote agent command. If omitted, built automatically from the resolved workspace folder. |

#### `workspace_folder`

The absolute path to the repo checkout on the CodeSpace. When set, the remote
agent command becomes `cd <workspace_folder> && copilot --acp --stdio`, which
ensures Copilot starts in the right directory even when a cold-started
CodeSpace's workspace volume isn't mounted by the time the SSH login profile
runs. Without it, convention resolves the folder on the CodeSpace at launch
(`$CODESPACE_VSCODE_FOLDER` -> `$WORKING_DIRECTORY` -> `$VM_REPO_PATH`) so a
session still lands in the checkout rather than `/home/vscode`.

#### `acp_command` (advanced)

Explicit override for the entire remote command; takes priority over
`workspace_folder`. Use only for a completely custom entry point:

```yaml
defaults:
  # acp_command: "/workspaces/my-wrapper.sh"     # custom wrapper
  # acp_command: "copilot --acp --stdio"          # bare (no cd prefix)
```

### `credentials`

The credential relay forwards git-credential requests from a CodeSpace back to
the host's Git Credential Manager, which serves **both** GitHub and Azure DevOps
-- **on by default, no config required**. Configure this block only for the
extras below.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `relay_port` | int | `0` (dynamic) | TCP port for the relay. `0` binds an OS-assigned ephemeral port (recommended). A positive value pins a fixed port. |
| `ado_host` | string | -- | Default Azure DevOps host (e.g. `<your-org>.visualstudio.com`) for bare `get-access-token` requests that carry no host (npm/nuget via ado-auth-helper). Unset = such requests are rejected. Also settable via the `CODESPACES_ADO_HOST` env var on the relay host. |
| `sources` | dict | -- | Optional per-source overrides (see below). |

### Credential Sources

The **git-credential** source is always active (serving github.com + ADO via
GCM). The `sources:` block is only for the optional cases below.

#### `az-login`

Returns Azure access tokens via `az account get-access-token`. The CodeSpace
relay allows the public Azure DevOps REST resource and Azure Storage by default
(so a headless agent can mint ADO REST and dev-deploy blob tokens). Add any
other resources explicitly and narrowly:

```yaml
credentials:
  sources:
    az-login:
      enabled: true
      allowed_resources:      # exact-match allowlist for ADDITIONAL resources
        - "https://graph.microsoft.com/"
```

**Security implications:** enabling this grants the CodeSpace access equivalent
to the host's current `az login` session for the listed resources. Tokens are
bearer credentials -- use only with trusted CodeSpaces and narrow scopes. Tokens
are cached until 5 min before expiry, never logged; unlisted resources are
denied; requires `az login` on the relay host.

### `repos`

Per-target-repo overrides. Keys are `org/repo` -- **the CodeSpaces repository**
(the repo your CodeSpaces are created from):

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `machine_type` | string | from `defaults` | VM size for this repo's CodeSpaces |
| `location` | string | from `defaults` | Azure region for this repo's CodeSpaces |
| `workspace_repo` | string | -- | The **product repo** this CodeSpaces repo hosts. Records the "we consume CodeSpaces from here for repo X" link (mirrors agent-worktrees' *related repos*). The remote workspace folder derives from it as `/workspaces/<basename>`. |
| `workspace_folder` | string | from `workspace_repo`, then `defaults` | Explicit per-repo workspace root override, when the checkout isn't `/workspaces/<basename(workspace_repo)>`. |
| `devcontainer_path` | string | from `defaults`, then canonical `.devcontainer/devcontainer.json` | Which devcontainer `gh codespace create` builds from. Consulted **only** when the repo has multiple discoverable devcontainers. |
| `provision` | map | -- | Repo-specific provision hooks (see Provisioning). |

#### Per-repo workspace folder (CodeSpaces repo != checkout)

A CodeSpaces repo frequently differs from the product checkout it hosts:
`org/example-web-codespaces` serves a `/workspaces/example-web` checkout.
Deriving the folder from the CodeSpaces repo name would give the **wrong**
`/workspaces/example-web-codespaces`. Record the relationship once with
`workspace_repo`; agents launched for that CodeSpace then land in the right dir:

```yaml
repos:
  example-org/example-web-codespaces:
    workspace_repo: example-web        # -> agents launch in /workspaces/example-web
```

Resolution order (most specific wins): `repos.<repo>.workspace_folder` > derived
from `repos.<repo>.workspace_repo` (`/workspaces/<basename>`) > global
`defaults.workspace_folder` > the remote-resolved fallback
(`$CODESPACE_VSCODE_FOLDER`/`$WORKING_DIRECTORY`/`$VM_REPO_PATH`).

### Provisioning

An adopting repo can deploy files and run hooks in the CodeSpace on connect:

```yaml
repos:
  org/app-codespaces:
    provision:
      files:
        - { src: tools/hook.sh, dest: ~/.bashrc.d/hook.sh }   # src is repo-relative
      on_connect:
        - "grep -qF hook.sh ~/.bashrc || echo '. ~/.bashrc.d/hook.sh' >> ~/.bashrc"
      # on_create:  runs once, after the CodeSpace is first Available
```

`provision.files.src` is resolved relative to the **repo root**, regardless of
whether the config lives at `.agent-codespaces/config.yaml` or the legacy
repo-root `codespaces.yaml`.

## Multi-Repo Adoption

Multiple repos can be adopted; config merges in memory:
- **Defaults:** first adopted repo wins
- **Credential sources:** union across repos (hosts merged)
- **Target repos:** first definition wins on key conflicts

## Session context map (additionalContext)

The plugin ships a `sessionStart` hook that injects a brief
`additionalContext` map of the repos **delegated to CodeSpaces** -- derived from
`<agent-worktrees catalog argv prefix> related list` (entries with
`delegate: agent-codespaces`) -- so
every session knows which repos have no local checkout and must be worked via a
CodeSpace. Nothing to configure; it emits nothing outside a managed project or
when no repo is CodeSpace-delegated.

## CLI Reference

```bash
<agent-codespaces catalog argv prefix> config init
<agent-codespaces catalog argv prefix> config adopt
<agent-codespaces catalog argv prefix> config migrate
<agent-codespaces catalog argv prefix> config show
<agent-codespaces catalog argv prefix> config validate
<agent-codespaces catalog argv prefix> doctor
```

## Troubleshooting

- **"No CodeSpace config found"** -- expected for a convention repo; only add a
  file when you need supplementary config.
- **`gh` missing or auth/scope wrong** -- run
  `<agent-codespaces catalog argv prefix> doctor`; it
  reports missing `gh`, unauthenticated accounts, or a missing `codespace` scope
  with the exact `gh auth ...` command to run.
- **No agent-bridge** -- setup is still valid. You lose `codespace:<name>`
  dispatch and the managed credential relay, but the catalog command's
  `list` / `create` actions and
  `<agent-codespaces catalog argv prefix> ssh --no-relay --remote-cmd "echo ok"`
  still diagnose
  CodeSpace reachability.
- **Headless `create` prompts/hangs** -- the repo ships >1 devcontainer; pin
  `devcontainer_path` (per-repo or per-create).
- **Agent lands in the wrong folder on a split repo** -- set `workspace_repo`.
- **Already adopted** -- the repo is registered; check with `config show`.
- **`config validate` warns about empty allowed_hosts** -- for an explicitly
  enabled `az-login`, add `allowed_resources` (or leave the source unset).
