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
provision hook). It lives in the **adopting repo**, in the canonical
`.copilot-extensions/<plugin>/` namespace:

```
<repo>/.copilot-extensions/agent-codespaces/config.yaml
```

Scaffold and adopt it in one step from inside the repo:

```bash
agent-codespaces config init      # writes .copilot-extensions/agent-codespaces/config.yaml (+ auto-adopts)
```

On Windows, the noninteractive workspace-discovery command used by `config init`
runs with console-window suppression.

Running a command inside a repo that carries the file **auto-discovers** it (no
manual `config adopt`); adoption persists it for the detached daemon and for
extra/multi-repo setups. Legacy `.agent-codespaces/config.yaml` and repo-root
`codespaces.yaml` are still read as fallbacks -- relocate them with
`agent-codespaces config migrate`.

```yaml
# .copilot-extensions/agent-codespaces/config.yaml -- SUPPLEMENTARY, in-repo. Add ONLY what
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

### Repo provenance & the active-plugin config seam

A repo's venue policy does **not** have to live in an adopted control-plane repo.
A plugin can ship the venue's **repo provenance** with itself and make it
discoverable with **no control-plane repo**. Two convention-discovered seams are
honored here:

- **Config declaration (`codespaceConfig`).** The active plugin's `plugin.json`
  names one string path relative to its payload root, for example
  `"codespaceConfig": "references/agent-codespaces/config.yaml"`.
  agent-codespaces resolves effectively active plugins, reads the declaration
  from the identity-verified root, rejects path escapes and non-regular or
  malformed targets, and parses the file as the normal supplementary config
  shape. No `sessionStart` hook or user-level pointer is required.
- **Hygiene and compatibility.** Legacy and operator-owned `config.d` inputs
  remain supported and independently diagnosed. A pointer for an identity with
  a valid active declaration is reported as superseded and cannot override or
  reject the declaration. Invalid, disabled, missing, duplicate, or transient
  contributions are isolated from peers; indeterminate reads retain only their
  own last-known contribution. Runtime warnings are bounded and deduplicated;
  `agent-codespaces doctor` (or `doctor --json`) reports exhaustive findings and
  precise remediation without deleting any entry.
- **Precedence.** `load_merged_config` consumes provider configs at the **lowest
  precedence** — adopted-repo/cwd config always wins. Active plugin declarations
  precede compatibility `config.d` inputs.
- **Repo provenance (`workspace_repo`).** The provider config's
  `repos.<vessel>.workspace_repo: <product>` is what makes
  `effective_acp_command_for(<vessel>)` launch the agent in `/workspaces/<product>`
  (the product checkout) rather than the vessel folder, and what
  `resolved_workspace_folder_for` publishes as the dispatched agent's ACP
  `session/new` cwd.
- **In-venue plugins (`codespacePlugins`).** The harness plugin's `plugin.json`
  also declares which plugins to inject **into** the CodeSpace on connect (the
  `<product>-agent`), scoped by `forWorkspaceRepo` (see `codespace_plugins.py`).
  A source backed by a **remote** marketplace is registered + pre-installed into
  the CodeSpace's user settings; a source backed by the harness repo's own
  **local** (`.ai`/`directory`) marketplace can't be installed on an
  egress-restricted CodeSpace (its repo-relative `path` doesn't exist there), so
  its host payload is **staged** (tar+base64 copy) and folded into the `--acp`
  launch as `--plugin-dir` -- the same lane the related-repo plugins use.

Authoring a `<repo>-harness` plugin that uses these seams is the
`authoring-harness-plugins` skill (`customizing-copilot`) and the pattern
[`docs/patterns/codespace-repo-provenance.md`](../../docs/patterns/codespace-repo-provenance.md).
The reference implementation is `example-web-harness` (example-marketplace).

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
agent-codespaces config init          # Scaffold .copilot-extensions/agent-codespaces/config.yaml (+ auto-adopt)
agent-codespaces config adopt         # Register a repo's config for the daemon
agent-codespaces config migrate       # Relocate legacy config -> .copilot-extensions/agent-codespaces/config.yaml
agent-codespaces config show          # Show resolved config
agent-codespaces config validate      # Validate resolved config
agent-codespaces cleanup              # Remove stale local state (SSH configs, sockets)
agent-codespaces doctor               # Check gh auth + config-provider hygiene
agent-codespaces doctor --json        # Exhaustive structured auth/config report
agent-codespaces status               # Runtime/config/gh/ssh overview
agent-codespaces version              # Show version
```

There are also bridge-facing seams (`namespace-list`, `namespace-resolve`,
`namespace-target-repo`, `namespace-ensure-ready`, `relay-profile`,
`relay-launch-env`, `provision-command`, `acp-model-flags`). They are invoked by
agent-bridge and are not the normal human/operator surface.

### Diagnostic command input

For a diagnostic command whose input exceeds local shell command-line limits,
send a file through the managed command channel instead of embedding its content:

```bash
agent-codespaces ssh example-space --effort /path/to/owner \
  --remote-cmd "node --input-type=module" --stdin-file preparation.mjs \
  --no-plugin-staging --require-relay --timeout 900 --connect-timeout 1200
```

`--stdin-file PATH` requires a non-stdio `--remote-cmd` or `--remote-cmd-file`.
The regular file is read and bounded to **16 MiB before claims or connections**.
Its bytes are sent unchanged, including BOMs, NULs, non-UTF-8 data, and line
endings; an empty file sends EOF. The input is a local snapshot, so later file
changes do not change the submitted bytes. Neither input nor file metadata is
rewritten. Interactive sessions, structured stdio, and missing/blank commands
are rejected. Existing command timeout, relay, fencing, ownership, exit-code and
cleanup behavior is unchanged. The usual minimal diagnostic provisioning default
still applies; this is not native execution hosting or an ACP fallback.

### Caller-owned interactive SSH

Use a local UTF-8 command file when a terminal owner needs a startup command,
especially across Windows shell quoting boundaries:

```bash
agent-codespaces ssh example-space \
  --interactive-command-file terminal-command.txt \
  --local-forward 8080:3000 \
  --reverse-forward 9000:9001 \
  --no-plugin-staging --require-relay
```

For example, `terminal-command.txt` can contain:

```bash
cd /workspaces/example-web &&
exec bash -il
```

- `--interactive-command-file PATH` reads UTF-8 (an optional UTF-8 BOM is
  accepted), without trimming whitespace or rewriting the file. Use LF line
  endings for remote Bash scripts. Missing, unreadable, invalid-UTF-8, blank, or
  NUL-containing payloads fail before claims or connections. The optional
  `--interactive-command COMMAND` string form is mutually exclusive with the
  file form.
- The command is a **trusted caller-owned shell program**, not an argument list.
  It travels as one SSH command argument through the existing `bash -l -c`
  wrapper, after the normal relay environment and arrival prelude. No ACP model
  or plugin flags are appended. SSH receives `-tt` to force a remote PTY, while
  local stdin/stdout/stderr remain attached to the caller's terminal. A command
  that should leave a shell open must explicitly launch one.
- Repeat `--local-forward LOCAL:REMOTE` to listen on host
  `127.0.0.1:LOCAL` and connect to CodeSpace `127.0.0.1:REMOTE`. Repeat
  `--reverse-forward REMOTE:LOCAL` for the opposite direction. Both fields must
  be decimal ports in `1..65535`; addresses, wildcards, sockets, and arbitrary
  SSH options are not accepted. Duplicate listeners within a direction are
  rejected, including differently formatted spellings of the same port.
  The same number in opposite directions is valid. A reverse listener cannot
  claim the credential relay's remote port while relay use is enabled.
- Forwarded sessions use `ExitOnForwardFailure=yes`,
  `ServerAliveInterval=30`, and `ServerAliveCountMax=3`. Failure to bind an SSH
  listener aborts the session; this does not promise that a destination service
  is listening. The remote SSH server must honor loopback binds (do not configure
  `GatewayPorts yes` to widen remote listeners).
- The new command and forward flags cannot combine with `--remote-cmd`,
  `--remote-cmd-file`, or `--stdio`. Forward flags can also accompany a plain
  interactive shell without a startup command. Existing diagnostic/ACP and
  unadorned interactive behavior remain unchanged.
- `--no-provision` and `--no-relay` retain their existing independent meanings.
  Neither disables claims, target locks, fences, or account pinning.
  `--no-provision` skips heavyweight provisioning **and plugin staging**; callers
  own any required remote tools and official plugin installation. No installer
  or copying behavior is added by this interface.
- `--no-plugin-staging` independently skips **all provider-controlled Copilot
  plugin delivery**: automatic CodeSpace-scoped registration/settings updates
  and remote-marketplace pre-installation, local-marketplace `codespacePlugins`
  host copying, and related-repo / explicit `--stage-plugin` payload copying.
  It does **not** skip relay/auth helpers, configured dotfiles or harness repo
  preparation, repo provision hooks, auth verification, or auth-cache warming.
  These helper scripts are provider-owned auth plumbing, not host plugin
  payloads. Caller-owned remote plugin installation remains explicit.
  User-configured repo hooks or dotfiles installers are still arbitrary programs;
  this flag does not rewrite or sandbox their behavior, nor remove existing
  remote plugin settings or payloads.
- `--require-relay` is an opt-in **launch admission** gate. It requires the host
  credential service to answer the relay protocol before claims; requires an
  owned live reverse-forward or a ready Connection Owner; requires a real
  protocol round trip through the exact remote loopback listener; and requires
  successful remote auth-helper setup. It repeats the remote probe after repo
  preparation, immediately before launching the interactive/diagnostic/ACP
  operation. Missing service, failed forwarding, owner readiness timeout,
  protocol-probe failure, or failed helper setup exits **69**, with normal
  owned-resource cleanup. Combining it with `--no-relay` is a usage error
  (exit **2**). With no opt-in, existing best-effort behavior is unchanged.
  Protocol probes request no credentials and use no auth-cache fallback: they
  establish service/tunnel readiness, not authorization for every remote host
  or resource. Existing auth verification and configured credential policy still
  apply. A later outage retains existing relay supervision/reconnection; this
  flag does not kill a terminal for a transient post-launch loss.
- Relay supervision and any connection-owner hold remain active throughout the
  interactive child. Owned claims and relay tenants are heartbeated every 30
  seconds; a claim heartbeat asserts its owner and rejects a replacement that
  appeared during distributed renewal. Refresh work is joined before cleanup.
  Direct and Connection Owner relay supervisors perform periodic remote
  protocol probes, in addition to process-death and host-port-rebinding checks.
  An uncertain probe transport does not tear down a potentially healthy relay;
  a definite bad protocol response triggers the shared supervisor's recovery.
  The managed command channel remains available through final cleanliness and
  obligation settlement, then disconnects. The child's exit status is returned unchanged. Cancellation
  and an explicit `--connect-timeout` stop only the owned process tree and release
  owned relay/lock resources. There is no implicit interactive timeout;
  `--timeout` remains the diagnostic-command deadline.

For a caller that wants host-backed credentials without plugin copying or ACP,
keep relay/repo preparation enabled and use the example above. Do not substitute
`--no-relay` or blanket `--no-provision` for plugin-delivery suppression.
The shared credential service must already be available. Using the resolved
agent-bridge command (not an unrelated same-named executable):

```text
agent-bridge service start
agent-bridge installer-readiness
```

The second command is read-only JSON; require exit 0 and
`{"schema":"copilot-extensions.module-readiness","version":1,
"module":"agent-bridge/runtime","state":"ready",...}`. This checks daemon
readiness and creates no ACP session; `--require-relay` separately verifies the
credential service through the CodeSpace tunnel. SSH does not implicitly start
the daemon. The optional `agent-codespaces owner --status` reports configuration
only, **not** live relay readiness; when a live enabled Owner is used, the SSH
operation places its hold, waits up to 30 seconds for readiness, and verifies the
remote protocol before proceeding.

This command is the transport/preparation primitive. For managed native Copilot,
use agent-bridge's `native start/attach/resume/status/stop` hosting surface, which
composes this provider's infrastructure with a survivable native execution host,
real native registration, and identity-bound retirement. Do not substitute a
foreground SSH process for that hosting contract. The provider's internal
`native-transport` channel owns preparation, relay, and forwarding—not the
remote Copilot process's lifetime. Interaction reservations prevent ACP/SSH
fallback from taking a native-owned venue, even when its registration is lost.

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
and can be overridden per-repo in `.copilot-extensions/agent-codespaces/config.yaml`. After the
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
The manifest is versioned and attributes its plugin source/root. If its binstub
or payload disappears, bridge leaves the namespace inactive, warns without
breaking other providers, and reports exact cleanup through
`agent-bridge doctor`.

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
`github/*` and `example-operator` for `example-org/*`), the active-account
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
aliases must never land in it. The generated
`.copilot-extensions/agent-codespaces/config.yaml`
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
