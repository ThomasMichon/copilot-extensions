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
- **Resource obligations** -- a borrowed CodeSpace is an accountable
  **obligation** on the borrowing worktree: `ssh` journals an `active`
  `codespace` claim onto its ledger, a clean disconnect settles it to `at-rest`
  and **mirrors that disposition onto the shared exclusion lease** (cross-machine
  visible), so the worktree's `agent-worktrees finalize` is gated until the box
  is safe. See [`docs/resource-obligations.md`](docs/resource-obligations.md) and
  the `borrowing-codespaces` skill.
- **Session context map** -- a `sessionStart` hook injects a brief
  `additionalContext` map of the repos delegated to CodeSpaces (derived from
  `agent-worktrees related list`), so every session knows which repos have no
  local checkout and must be worked via a CodeSpace

## Configuration

**Most repos need no config at all.** agent-codespaces works out of the box on
standard GitHub CodeSpaces by **convention**:

- machine `largePremiumLinux`, location `EastUs`
- in-CodeSpace checkout at `/workspaces/<repo-basename>`
- credential relay serving `github.com` **and** Azure DevOps (via the host Git
  Credential Manager)

So `agent-codespaces create <your-org>/<standard-repo>` just works -- no file to
author.

Add a **supplementary** config only when a repo deviates from convention (a
split CodeSpaces-vs-product repo, a pinned devcontainer, an ADO host, a
provision hook). It lives in the **adopting repo**, in the canonical in-repo
location aligned with the sibling `agent-*` plugins:

```
<repo>/.agent-codespaces/config.yaml
```

Scaffold and adopt it in one step from inside the repo:

```bash
agent-codespaces config init      # writes .agent-codespaces/config.yaml (+ auto-adopts)
```

Running a command inside a repo that carries the file **auto-discovers** it (no
manual `config adopt`); adoption persists it for the detached daemon and for
extra/multi-repo setups. A **legacy** repo-root `codespaces.yaml` is still read
as a fallback -- relocate it with `agent-codespaces config migrate`.

```yaml
# .agent-codespaces/config.yaml -- SUPPLEMENTARY, in-repo. Add ONLY what
# deviates from convention; everything omitted is derived.
repos:
  org/my-app-codespaces:
    workspace_repo: my-app          # split repo -> agents land in /workspaces/my-app
    machine_type: largePremiumLinux256gb
    devcontainer_path: .devcontainer/devcontainer.json   # pin if repo ships >1

credentials:
  ado_host: my-org.visualstudio.com   # only for bare ADO get-access-token
```

> The service reads config live from the repo -- no generated intermediate
> config. All org/account/URL values live in **your** repo, never in the plugin.

## CLI

```bash
agent-codespaces ssh <name>           # SSH into a CodeSpace
agent-codespaces ssh --stdio <name>   # Structured SSH for agent-bridge
agent-codespaces list                 # List active CodeSpaces
agent-codespaces create <owner/repo>  # Create a CodeSpace + run provisioning
agent-codespaces delete <name>        # Delete a CodeSpace (--force to skip prompt)
agent-codespaces config init          # Scaffold .agent-codespaces/config.yaml (+ auto-adopt)
agent-codespaces config adopt         # Register a repo's config for the daemon
agent-codespaces config migrate       # Relocate legacy codespaces.yaml -> .agent-codespaces/config.yaml
agent-codespaces config show          # Show resolved config
agent-codespaces config validate      # Validate resolved config
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

Machine type and location default by convention (`largePremiumLinux` / `EastUs`)
and can be overridden per-repo in `.agent-codespaces/config.yaml`. After the
CodeSpace is Available, any `on_create` provisioning hooks from that config run
automatically.

### `bridge` options

> **Usually unnecessary.** Once agent-codespaces is installed, agent-bridge
> auto-registers the live `codespace:` namespace resolver, so CodeSpaces are
> addressable as `codespace:<name>` (raw or friendly) with no registration.
> `bridge register` only POSTs a static `cs-<name>` snapshot (with a TTL) for
> HTTP consumers that prefer a pre-registered provider list; it is optional and
> superseded by the resolver.

```bash
agent-codespaces bridge register   [--ttl 300] [--bridge-url <url>]
agent-codespaces bridge refresh    [--ttl 300] [--bridge-url <url>]
agent-codespaces bridge status     [--bridge-url <url>]
agent-codespaces bridge unregister [--bridge-url <url>]
```

> **Linux/WSL:** the bridge defaults to port **9281**, but these commands
> default `--bridge-url` to `http://127.0.0.1:9280`. On Linux/WSL pass
> `--bridge-url http://127.0.0.1:9281` explicitly.

## Multi-account gh (per-repo identity)

Host-side `gh` operations (`gh codespace list/create/delete/stop/ssh`, `gh api`,
and the `gh codespace ssh --config` fetch) run under the `gh` account that can
access the **target repo's org** — not whatever account is active in the `gh`
keyring. With two accounts backing different orgs (e.g. `ThomasMichon` for
`github/*` and `tmichon_microsoft` for `odsp-microsoft/*`), the active-account
default would hide or `403`/`404` the other org's CodeSpaces entirely.

- The owner→login mapping is owned by **agent-worktrees** (its `repos.yaml`
  `account_map` + `accounts.yaml` catalog). agent-codespaces shells out to
  `agent-worktrees repos account-for <owner/name>` (loose coupling — separate
  venvs) and mints a per-account `GH_TOKEN` for each `gh` subprocess.
- **Cross-account discovery:** `gh codespace list` only returns the active
  account's CodeSpaces, so `list` (and status/resolve) enumerate under **every**
  mapped account plus the ambient one and merge, tagging each CodeSpace with its
  owning account. Per-CodeSpace ops (stop/delete/ssh) then pin `gh` to that
  account.
- **Auth preflight** verifies each mapped account is logged in with the
  `codespace` scope, surfacing the account's recorded `accounts.yaml` login flow
  as the remedy.
- **Fully additive:** with no `account_map` configured, everything collapses to
  a single ambient `gh` call — today's behavior.

### Authenticating an account over SSH (device-code flows)

Setting up a second account on a remote box — `gh auth login` / `gh auth refresh
-s codespace`, and likewise `az login` / `devtunnel user login` — runs an
**interactive device-code flow** that polls for a minute-plus while a human
authorizes in a browser. **Do not run it as a foreground command over SSH.** A
Windows SSH session is a **network logon** whose session (and its entire child
process tree) is torn down the moment the connection drops — and a
`Start-Process … -WindowStyle Hidden` child launched from that SSH shell is
*still* parented to it, so it dies too. Any tunnel blip (acute on dtssh, and on
hibernate-prone cloud dev boxes) kills the poller and the code silently expires
(`context deadline exceeded`).

Run the auth under **Task Scheduler**, which owns the process in a session that
outlives the SSH connection:

```powershell
# over ssh: write a runner, register+run a one-shot task, redirect output to a file
Set-Content $env:USERPROFILE\ghauth.ps1 'gh auth refresh -h github.com -s codespace *> "$env:USERPROFILE\ghauth.out"'
schtasks /Create /TN ghauth /TR "pwsh -NoProfile -File $env:USERPROFILE\ghauth.ps1" /SC ONCE /ST 00:00 /F
schtasks /Run /TN ghauth
# then, over FRESH ssh connections, poll the file for the device code + completion:
#   Get-Content $env:USERPROFILE\ghauth.out
# clean up: schtasks /Delete /TN ghauth /F ; Remove-Item $env:USERPROFILE\ghauth.ps1,$env:USERPROFILE\ghauth.out
```

Surface the device code from the output file, have the human authorize it (in an
**incognito** window signed in as the **target** account — otherwise the code
authorizes whatever account the browser is already on), then poll the same file
for `✓ Authentication complete`. Note `gh auth refresh` targets the **active**
account (no `-u/--user` on many `gh` builds), so `gh auth switch --user <login>`
first and restore afterward.

## Credential relay: fail-fast & auth verification

The relay forwards git-credential requests from a CodeSpace back to the host
over the SSH tunnel, resolving them through the host's Git Credential Manager
(GCM) — which serves **both** GitHub (`github.com`) and Azure DevOps
(`*.visualstudio.com`, `dev.azure.com`) credentials.

To avoid the failure mode where a missing/expired credential causes a CodeSpace
`git fetch` to hang indefinitely on `git credential fill`:

- **Host GCM runs non-interactively** (`GIT_TERMINAL_PROMPT=0`,
  `GCM_INTERACTIVE=never`), so it errors fast instead of blocking on a prompt.
- **The relay replies `quit=1`** when a git `get`/`fill` request can't be
  resolved, which makes git in the CodeSpace abort immediately
  (`fatal: credential helper ... told us to quit`) rather than dropping to an
  interactive prompt. CodeSpace SSH sessions also export `GIT_TERMINAL_PROMPT=0`.
- **On connect, remote-domain auth is verified up front:** the workspace's
  `git remote -v` domains are probed against the host credential store, and any
  domain lacking local auth is reported as a `[WARN]` so it can be fixed
  (`az login` / GCM sign-in) before work begins, rather than discovered
  mid-fetch.

## Local identifier guard

This is a **public** repo, so internal org/account/repo names and personal
aliases must never land in it. The generated `.agent-codespaces/config.yaml`
scaffold is checked for such leaks by `tests/test_config_init.py`, and the whole
working tree by [`tools/check-no-internal-identifiers.py`](../../tools/check-no-internal-identifiers.py)
(wire it up as a git `pre-push` hook).

A denylist that *named* those identifiers would itself leak them, so it is
**never stored in the repo**. Both guards read it privately from:

1. env `COPILOT_EXTENSIONS_FORBIDDEN_IDS` (comma-separated), and
2. `~/.agent-codespaces/forbidden-identifiers.txt` (one per line; blank lines
   and `#` comments ignored).

With neither configured (a fresh clone / CI) the identifier check is a no-op, so
the guards are safe to ship. Populate one of the sources on your own machine —
e.g.:

```text
# ~/.agent-codespaces/forbidden-identifiers.txt
my-internal-org
my-internal-repo
my-alias
```

Matching is case-insensitive (substring). The host file lives in `$HOME`, outside
any repo, so it is never committed.

## Development

```bash
cd plugins/agent-codespaces
pip install -e ".[dev]" -e "../../libs/ssh-manager[dev]"
pytest tests/
```
