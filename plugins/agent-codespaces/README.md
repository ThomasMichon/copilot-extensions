# agent-codespaces

GitHub Codespaces lifecycle management, SSH transport, and credential relay
for Copilot CLI.

## Overview

A copilot-extensions plugin that provides:

- **SSH transport** -- multiplexed SSH connections to CodeSpaces via
  ssh-manager, wrapping `gh codespace ssh --config`
- **Lifecycle management** -- list/pool, create/reuse, wait, stop, finalize,
  prune/delete, and status for CodeSpaces
- **Credential relay** -- contribute the CodeSpace relay profile to the
  agent-bridge-owned relay, then expose it to the CodeSpace over SSH reverse
  forwards (git credentials through host GCM; optional Azure tokens through
  `az-login`)
- **Agent-bridge provider** -- when agent-bridge is installed, a session-start
  hook drops a `providers.d` manifest so `codespace:<name>` agents resolve live
  over the agent-codespaces CLI boundary
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
  Credential Manager) when the agent-bridge relay is running

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

### Repo provenance & the config-provider seam (harness plugins)

A repo's venue policy does **not** have to live in an adopted control-plane repo.
A **`<repo>-harness`** plugin can ship the venue's **repo provenance** with itself
and make it discoverable with **no control-plane repo** — the odsp-web-style
golden path. Two convention-discovered seams, both honored here:

- **Config-provider drop-in (`config.d`).** A harness plugin ships a supplementary
  `.agent-codespaces/config.yaml` under its own `references/` and, from a
  `sessionStart` hook, drops a one-line **pointer** to it into
  `~/.agent-codespaces/config.d/<name>.conf`. `discover_dropin_configs()` reads each
  pointer and `load_merged_config` merges the referenced config at the **lowest
  precedence** — a provider default any adopted-repo/cwd config still overrides,
  with no copy to drift and no writeback into any repo.
- **Repo provenance (`workspace_repo`).** The provider config's
  `repos.<vessel>.workspace_repo: <product>` is what makes
  `effective_acp_command_for(<vessel>)` launch the agent in `/workspaces/<product>`
  (the product checkout) rather than the vessel folder, and what
  `resolved_workspace_folder_for` publishes as the dispatched agent's ACP
  `session/new` cwd (dotfiles#1274).
- **In-venue plugins (`codespacePlugins`).** The harness plugin's `plugin.json`
  also declares which plugins to inject **into** the CodeSpace on connect (the
  `<product>-agent`), scoped by `forWorkspaceRepo` (see `codespace_plugins.py`).

Authoring a `<repo>-harness` plugin that uses these seams is the
`authoring-harness-plugins` skill (`customizing-copilot`) and the pattern
[`docs/patterns/codespace-repo-provenance.md`](../../docs/patterns/codespace-repo-provenance.md).
The reference implementation is `odsp-web-harness` (dev-tmichon).

## CLI

agent-codespaces is a standalone CLI/binstub. Listing, creating, deleting,
waiting, stopping, and diagnostic SSH do not require registering the current
repo as an agent-worktrees harness. The bridge namespace and shared credential
relay are optional sibling composition: if agent-bridge is absent or stopped,
`codespace:` dispatch and relay-backed auth stay dark, but the CLI remains
usable (use `--no-relay` for relay-free diagnostics).

```bash
agent-codespaces ssh <name>           # SSH into a CodeSpace
agent-codespaces ssh --stdio <name>   # Structured SSH for agent-bridge
agent-codespaces list                 # List active CodeSpaces
agent-codespaces pool                 # Pool view: disposition + core budget
agent-codespaces allocate <owner/repo> # Reuse/create/recycle/pressure decision
agent-codespaces create <owner/repo>  # Create, guarded by reuse/budget checks
agent-codespaces wait <name>          # Patiently wait for Available
agent-codespaces stop <name>          # Recover sessions, then stop (preserve)
agent-codespaces finalize <name>      # Recover, stop, mark recovered/reusable
agent-codespaces finalize <name> --delete  # Recover, verify off-box safety, delete
agent-codespaces verify <name>        # Publish git-cleanliness safety verdict
agent-codespaces delete <name>        # Delete a CodeSpace (--force to skip prompt)
agent-codespaces config init          # Scaffold .agent-codespaces/config.yaml (+ auto-adopt)
agent-codespaces config adopt         # Register a repo's config for the daemon
agent-codespaces config migrate       # Relocate legacy codespaces.yaml -> .agent-codespaces/config.yaml
agent-codespaces config show          # Show resolved config
agent-codespaces config validate      # Validate resolved config
agent-codespaces cleanup              # Remove stale local state (SSH configs, sockets)
agent-codespaces doctor               # Check gh auth + codespace scope
agent-codespaces status               # Runtime/config/gh/ssh overview
agent-codespaces version              # Show version
```

There are also bridge-facing seams (`namespace-list`, `namespace-resolve`,
`namespace-target-repo`, `namespace-ensure-ready`, `relay-profile`,
`relay-launch-env`, `provision-command`, `acp-model-flags`). They are invoked by
agent-bridge and are not the normal human/operator surface.

### `create` options

```bash
agent-codespaces create <owner/repo> \
  --branch <branch> \           # branch to create on (default: repo default)
  --display-name <name> \       # CodeSpace display name
  --devcontainer-path <path> \  # only needed to override multi-devcontainer resolution
  --timeout 300 \               # seconds to wait for Available (default 300)
  --force-create \              # bypass reuse-before-create / core-budget guard
  --no-wait                     # don't wait / skip provisioning
```

Machine type and location default by convention (`largePremiumLinux` / `EastUs`)
and can be overridden per-repo in `.agent-codespaces/config.yaml`. After the
CodeSpace is Available, any `on_create` provisioning hooks from that config run
automatically. Without `--force-create`, `create` first consults the pool
planner: it reuses a suitable idle CodeSpace or refuses when the configured core
budget is already under pressure.

### Agent-bridge integration (automatic)

Once agent-codespaces is installed, its sessionStart hook drops a
namespace-provider manifest into `~/.agent-bridge/providers.d/`. agent-bridge
discovers it there and registers the live `codespace:` namespace resolver, so
CodeSpaces are addressable as `codespace:<name>` (raw or friendly) — listed and
resolved live, with no expiry, including newly-created ones. There is **no
`bridge register` step**; installing the plugin is all that's needed.

The current bridge integration is process-boundary first, not PATH/import
coupled: the manifest carries the absolute agent-codespaces binstub, and
agent-bridge invokes `namespace-*` commands to list/resolve targets. The
credential-relay and Session Host helper paths similarly prefer CLI seams
(`relay-profile`, `relay-launch-env`, `provision-command`) with in-process import
fallbacks only when the bridge venv happens to vendor the package. This follows
the repo's à-la-carte independence pattern: the agent-codespaces CLI owns its
runtime; agent-bridge only lights up optional dispatch/relay features.

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
(`*.visualstudio.com`, `dev.azure.com`) credentials. The relay server is owned
by agent-bridge; agent-codespaces contributes the CodeSpace policy/profile and
sets up the SSH reverse-forward on connect.

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
cd plugins\agent-codespaces
uv venv .venv
uv pip install --python .venv\Scripts\python.exe -e ".[dev]"
python ..\..\tools\run-plugin-tests.py agent-codespaces --guards
```
