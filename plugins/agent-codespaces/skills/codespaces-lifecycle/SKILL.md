---
name: codespaces-lifecycle
description: >
  GitHub Codespaces operations -- bridge dispatch to codespace agents,
  diagnostic SSH, list/pool/wait/stop/finalize/delete/status, and credential
  relay troubleshooting. Use this skill
  for day-to-day codespace management.
  Trigger phrases include:
  - 'codespace'
  - 'codespace ssh'
  - 'ssh into codespace'
  - 'list codespaces'
  - 'stop codespace'
  - 'delete codespace'
  - 'codespace status'
  - 'credential relay'
  - 'relay status'
  - 'codespace doctor'
  - 'codespace troubleshooting'
  - 'codespace agent'
---

# Codespaces Lifecycle

Day-to-day operations for GitHub Codespaces via agent-codespaces. For
first-time setup and config changes, see the `codespaces-setup` skill.

Use the exact `argv` from the agent-codespaces session command catalog for
CodeSpace operations and the exact `argv` from the agent-worktrees catalog for
worktree claims. For bridge dispatch, use the exact `argv[0]` from the
session command catalog for agent-bridge and replace
`<agent-bridge catalog argv[0]>` below with that path. Never substitute a
same-named plugin command through ambient PATH. The payload-local commands
self-provision on first use. Full detail:
`codespaces-setup` § *Readiness*.

## Connecting to CodeSpaces

Routine **dispatch** should go through **agent-bridge**, not raw SSH.
CodeSpace agents are discovered **automatically** via the agent-codespaces
namespace resolver — **no manual registration is needed past installation**
(see *Agent-Bridge Integration* below). Any CodeSpace (running or stopped) is
addressable as `codespace:<name>`, by either its **raw** name or its **friendly**
(display) name. The `codespace:` prefix is optional — a bare name resolves too,
and constrains nothing; use the prefix to force CodeSpace-only resolution. A
bare name that collides with another agent makes the bridge **balk** and list
the candidates.

```bash
<agent-bridge catalog argv[0]> send codespace:my-feature-branch "<prompt>"
<agent-bridge catalog argv[0]> send my-feature-branch "<prompt>"
<agent-bridge catalog argv[0]> send codespace:my-feature-branch-7qv4rv "..."
```

### Agent-Bridge CLI

| Command | Purpose |
|---------|---------|
| `<agent-bridge catalog argv[0]> agents` | List all available agents (local + codespace) |
| `<agent-bridge catalog argv[0]> send codespace:<name> "<prompt>"` | Start a new session (blocks until turn completes) |
| `<agent-bridge catalog argv[0]> send <session-id> "<prompt>"` | Send follow-up prompt on existing session |
| `<agent-bridge catalog argv[0]> send --no-wait <target> "<prompt>"` | Deliberate fire-and-forget — returns a session ID without attaching to its feed |
| `<agent-bridge catalog argv[0]> wait <session-id>` | Block until current turn completes |
| `<agent-bridge catalog argv[0]> sessions` | List all sessions with status |
| `<agent-bridge catalog argv[0]> sessions --status idle` | List sessions ready for follow-up |
| `<agent-bridge catalog argv[0]> stop <session-id>` | Pause session (preserves state for resume) |
| `<agent-bridge catalog argv[0]> resume <session-id>` | Resume a stopped session |
| `<agent-bridge catalog argv[0]> end <session-id>` | End and clean up session |

### Sync pattern (default — recommended for interactive use)

The payload-local `send` operation blocks until the turn completes.
Use when you need the result before continuing.

```
powershell(command: '& "<agent-bridge catalog argv[0]>" send "codespace:<name>" "<prompt>"', initial_wait: 120)
```

### Long-running interactive work

Keep the default attached stream when the operator expects to see progress.
Long runtime alone is **not** a reason to add `--no-wait`: the bridge collapses
thoughts and tools into a low-noise live feed and emits liveness markers during
quiet tool calls.

```
powershell(command: '& "<agent-bridge catalog argv[0]>" send "codespace:<name>" "<prompt>"', initial_wait: 300)
```

If the outer tool runner backgrounds the still-running command after its
initial wait, keep following that same tool session rather than declaring the
dispatch complete.

### Detached pattern (fire-and-forget only)

Use `--no-wait` only when the caller intentionally does not need the result or
live progress. It exits immediately; no background command remains to produce a
completion notification, and the remote feed accumulates unread until a caller
attaches to it.

```
powershell(command: '& "<agent-bridge catalog argv[0]>" send --no-wait "codespace:<name>" "<prompt>"')
# Capture the returned session ID.
powershell(command: '& "<agent-bridge catalog argv[0]>" read <session-id>', initial_wait: 300)
# `<agent-bridge catalog argv[0]> wait <session-id>` is also valid when only the current turn matters.
```

Do not end the host turn with only "implementation is running" when the result
is part of the current request. Either keep the original `send` attached, or
immediately follow with `read`/`wait`. A genuinely detached dispatch needs an
explicit later monitoring plan.

### Multi-turn sessions

Sessions are persistent. After the first `send` creates a session, send
follow-ups using the session ID:

```bash
<agent-bridge catalog argv[0]> send "codespace:<name>" "Research the auth module"
# → Session abc123-def (keen-river) created

<agent-bridge catalog argv[0]> send abc123-def "Now implement the changes"
# → [response]

<agent-bridge catalog argv[0]> end abc123-def
```

### Startup and Shutdown Behavior

- **Shutdown CodeSpaces auto-start** when the bridge connects. Startup
  takes 60–120 s; the SSH layer retries automatically (up to ~180 s).
- **Transient dev-tunnel resets are retried** ("An existing connection was
  forcibly closed", `error getting tunnel`, RPC `Unavailable`, ssh exit 255):
  ssh-manager backs off and retries the `gh codespace ssh --config` fetch, the
  connect, and every idempotent remote step (`exec_with_retry`: probes,
  staging, settings merges, installs); a detached launch or `--stop` retries
  its connection once. A failure that survives those retries is real -- read
  its stderr rather than re-running blindly.
- **Do NOT pre-start CodeSpaces with manual SSH** — the bridge handles
  startup end-to-end.
- **Pool pressure:** the catalog command's `create` action consults the pool planner before
  spending another box: it prefers reusing a suitable idle CodeSpace and refuses
  over-budget creates unless `--force-create` is passed. Inspect with
  `<agent-codespaces catalog argv[0]> pool` or preflight with
  `<agent-codespaces catalog argv[0]> allocate <repo>`.

### Exclusive control: claim + cross-harness fence

A CodeSpace is fronted by a single bridge, so `agent-codespaces` takes an
**exclusive, worktree-keyed claim** on connect (`ssh --effort` / `claim`): a
host-local **L1** lock plus an atomic cross-machine **L2** Git-ref lease
(`<agent-worktrees catalog argv[0]> lease`, the same-harness authority) — a live claim on another
machine raises `[BUSY]`/`ClaimConflict` (take over with `--force-claim`). On top,
a **cross-harness fence** reads a lockfile inside the CodeSpace (`~/.agent-lease`)
and **refuses** the connect if a *foreign harness* holds it (the seam the
same-harness ref store cannot see). All degrade-safe — a missing store / identity
never blocks. See `borrowing-codespaces` for the full lease + fence model.

## CLI-mode sessions (`copilot`)

`<agent-codespaces catalog argv[0]> copilot <name>` delivers a real interactive Copilot CLI
session, running in a tmux session inside the CodeSpace, into **this**
terminal (it reserves the CLI-mode slot on the host agent-bridge, forwards the
credential relay and the host bridge port, and runs the venue's agent-worktrees
`copilot` verb there). Re-running it re-attaches to the same session.

An **orchestrating agent** uses the detached form instead -- nothing takes over
its terminal:

```bash
<agent-codespaces catalog argv[0]> copilot <name> --detach --seed-file task.md \
  --driver orchestrator --copilot-arg=--no-ask-user     # prints a JSON handle
<agent-codespaces catalog argv[0]> copilot <name> --detach --seed-file task.md \
  --ref-file ./trace.har --ref-file ./session.md        # + reference files for the worker
<agent-codespaces catalog argv[0]> copilot <name> --detach --dry-run   # resolved plan, no side effects
<agent-codespaces catalog argv[0]> copilot <name> --stop               # verified stop, then release
```

Launched with `--effort <owner>`, the attach (`copilot <name>`) and `--stop`
commands must pass the same `--effort`: they connect, and the CodeSpace claim
refuses a different owner as busy. The launch's JSON `commands` already carry it.

`--detach` runs the same dispatch-grade venue preparation as an agent-bridge
dispatch (relay helpers, dotfiles/harness, CodeSpace-scoped plugins staged into
the session via `--plugin-dir`, repo hooks, auth checks), then from the product
checkout adopts it as an anchor-only `agent-worktrees` project if needed,
records that folder in Copilot's `trustedFolders` (nobody is there to answer the
first-run trust dialog), and launches the session with the venue's agent-worktrees
`embody` verb.
`--ref-file` (repeatable; a file or a folder, up to 256 MiB per call) copies
reference material into `~/.agent-bridge/refs/<batch>/` on the venue -- outside
the product checkout, so it is never committed -- over the same egress-free
stdin lane as plugin staging, and tells the worker the exact paths: in its seed
for a new session, or as a message when the session is already running (so
re-running the same `--detach` command with `--ref-file` hands a running
worker more files). The caller passes only paths; it never reads the files.
`--reverse-forward VENUE_PORT:HOST_PORT` (repeatable) asks the Connection Owner
to keep the CodeSpace's `127.0.0.1:VENUE_PORT` forwarded to this host's
`127.0.0.1:HOST_PORT` for as long as the session lives -- for example a host
browser started with `--remote-debugging-port=<HOST_PORT>` exposed as the
venue's DevTools endpoint on 9222. It binds loopback only on the venue, but
anything running there can then drive that host process: forward only what the
worker should control. A rejoin without the flag keeps the session's forwards.
The launch reports `reverse_forwards_ready` per venue port (it checks each one
accepts a connection); `false` usually means a Connection Owner that predates
this option is still running -- it exits once it holds nothing.
A multi-line or long seed is written to `~/.agent-bridge/seeds/` on the venue and
seeded as a one-line pointer (tmux-typed input must be a single line). It
succeeds only once the session is registered with the host bridge (and its seed
submitted); a failure reports the screen (`pane_tail`) and stops what it started.
The **Connection Owner** keeps the
credential relay and the host-bridge forward alive while its mux session exists
(checked from the host every couple of minutes, only after the
launch confirmed the session; never by waking a stopped CodeSpace), up to a 24h
cap, and both forwards follow a host bridge restart onto its new port. Observe and steer it through agent-bridge
(`live-sessions resolve`, `result`, `send`, `ui`). A detached launch keeps the
CodeSpace claim active; `--stop` settles it like any finished connection and
deregisters the stopped session from the host bridge at once. A CodeSpace that
is already `Shutdown` is never booted for `--stop`: its session is gone, so it
only releases and deregisters (`already_shutdown`), and the claim is settled by
the ordinary release/retire step.

Requires the venue's `agent-worktrees` and `agent-bridge` plugins to support
`embody --bridge-scope-id/--copilot-arg`; an older venue fails closed with an
"update agent-worktrees on the CodeSpace" error.

## SSH (Diagnostic Only)

SSH is for diagnostics and one-off commands, **not routine dispatch**.
If you find yourself using SSH for dispatch or status checks, diagnose
the bridge connection instead.

> **Never SSH a CodeSpace that has an active dispatch.** The catalog command's `ssh` action
> shares the same ssh-manager ControlMaster socket as the dispatch's connection;
> a concurrent diagnostic SSH can tear that down and **collapse the running
> session**. To answer "is it making progress?", read the bridge feed and get
> durable state (branch HEAD / pushed / PR) from the **source of truth** (the git
> remote / PR API) — not by shelling into the CodeSpace. Reserve host SSH for a
> CodeSpace whose dispatch is **stopped/idle**.

> **CodeSpace dispatch sessions are now resilient to the failures that used to
> collapse them ~every 10–15 min.** The main culprit — the ACP stdio relay
> giving up after 30 s of a quiet (output-buffered) remote tool call — is fixed,
> and a bridge **daemon restart is survived** (a streaming `send`/`read`/`wait`
> reconnects from its delivery cursor, and the next `send` auto-resumes the
> session). Genuine drops are now rare but still possible (a CodeSpace idle
> timeout, a network partition). Long jobs should still be **idempotent** and
> **push early and often**, and you can **resume on drop**
> (`<agent-bridge catalog argv[0]> end <sid>` →
> `<agent-bridge catalog argv[0]> create …`).
> See the agent-bridge
> skill's *Dispatching Long Autonomous Work* flow.

> **Always use the agent-codespaces catalog command's `ssh` action**, not bare
> `gh codespace ssh`.
> Raw `gh codespace ssh` bypasses ssh-manager and can conflict with
> managed connections — duplicate ControlMaster sockets, missed
> credential relay tunnels, and orphan SSH processes.

```bash
# Interactive SSH session (with credential relay tunnel)
<agent-codespaces catalog argv[0]> ssh <codespace-name>

# Run a command and return output
<agent-codespaces catalog argv[0]> ssh <codespace-name> --remote-cmd "ls -la"

# Structured stdio for agent-bridge transport
<agent-codespaces catalog argv[0]> ssh <codespace-name> --stdio --remote-cmd "copilot --acp --stdio"

# Skip credential relay tunnel setup
<agent-codespaces catalog argv[0]> ssh <codespace-name> --no-relay
```

## Listing and Status

```bash
<agent-codespaces catalog argv[0]> list
<agent-codespaces catalog argv[0]> list --json
<agent-codespaces catalog argv[0]> pool
<agent-codespaces catalog argv[0]> pool --json
<agent-codespaces catalog argv[0]> allocate <owner/repo> --json
<agent-codespaces catalog argv[0]> status
<agent-codespaces catalog argv[0]> doctor
<agent-codespaces catalog argv[0]> version
```

## Creating and Deleting

```bash
# Create a CodeSpace on a repo + run on_create provisioning from config
<agent-codespaces catalog argv[0]> create <owner/repo>
<agent-codespaces catalog argv[0]> create <owner/repo> --branch <branch> --display-name <name>
<agent-codespaces catalog argv[0]> create <owner/repo> --devcontainer-path .devcontainer/devcontainer.json
<agent-codespaces catalog argv[0]> create <owner/repo> --force-create
<agent-codespaces catalog argv[0]> create <owner/repo> --no-wait

<agent-codespaces catalog argv[0]> delete <codespace-name>
<agent-codespaces catalog argv[0]> delete <codespace-name> --no-sync

# Remove stale local state (orphaned SSH configs, ControlMaster sockets)
<agent-codespaces catalog argv[0]> cleanup
<agent-codespaces catalog argv[0]> cleanup --dry-run
```

CodeSpace creation uses `gh codespace create` with defaults by convention
(`largePremiumLinux`/`EastUs`); per-repo overrides from
`.copilot-extensions/agent-codespaces/config.yaml` (machine type, location) apply automatically
based on the target repository.

## Finalize — graceful close-out with session recovery

Before a CodeSpace is destroyed, its Copilot session history (`~/.copilot`
session-state) should be recovered — a deleted CodeSpace's transcripts are
gone forever. `finalize` pulls the session-state off the CodeSpace and lands
it in the agent-logger storage hub (under `.codespaces/<name>/`), reusing
agent-logger's `session-sync push` management entry point. <!-- marketplace-isolation: allow logger-management -->
Only the `session-state` tree and the
`session-store.db` index are pulled — never credentials, keys, or settings.

```bash
# Recover sessions, stop the CodeSpace, and mark it recovered/reusable
<agent-codespaces catalog argv[0]> finalize <codespace-name>

# Recover sessions, require a fresh off-box-safety verdict, then delete
<agent-codespaces catalog argv[0]> verify <codespace-name>
<agent-codespaces catalog argv[0]> finalize <codespace-name> --delete
```

Plain `finalize` is the preserve path: it recovers Copilot session-state, stops
the CodeSpace (idempotent if already `Shutdown`), marks it `recovered`, and
releases the borrow so the box can be reused later. It does **not** delete.

`finalize --delete` is the destructive path. It first checks the no-SSH
`codespace-clean` beacon; if safety is unknown, run
`<agent-codespaces catalog argv[0]> verify <name>` to SSH-probe git cleanliness
and publish a fresh verdict, then retry.
It also refuses deletion after failed session recovery unless `--force` is
explicitly supplied.

> 🛑 **If `finalize --delete` refuses, diagnose — don't bypass.** Common causes:
> unknown/dirty off-box safety (`verify` or push/settle the work), a
> still-booting CodeSpace, or an SSH/relay hiccup. For a genuinely unrecoverable
> CodeSpace, deletion is break-glass:
> `<agent-codespaces catalog argv[0]> finalize <name> --delete --force` or
> `<agent-codespaces catalog argv[0]> delete <name> --force --no-sync`.

`delete` also runs recovery automatically as a **best-effort pre-delete hook**
(skip with `--no-sync`); unlike `finalize --delete`, it does not gate on
recovery or the cleanliness beacon. Prefer `finalize --delete` for normal
retirement, and reserve `delete` for deliberate break-glass cleanup.

> **Closing out a CodeSpace settles the borrowing worktree's obligation.** A
> borrowed CodeSpace is an `active` `codespace` claim on the borrowing worktree's
> ledger (`resource-obligation-settlement`) that blocks *its* worktree
> finalization. A clean **disconnect** stamps the claim `at-rest` and mirrors
> that onto the CodeSpace's shared lease (cross-machine visible); the
> payload-local `delete` / `finalize --delete` operations release the box
> entirely. So drive a borrowed
> CodeSpace to a clean state and disconnect (or delete it) **before** finalizing
> the worktree that borrowed it — otherwise its finalize blocks on the unsettled
> obligation. If a settle was missed (a crash, or a bridge-driven box), the
> agent-worktrees reclaim sweep reads the lease mirror and settles the stale claim
> automatically. See the `borrowing-codespaces` skill for the obligation model.

> Requires the **agent-logger** plugin to be installed and enabled from the same
> marketplace (providing its payload-local `session-sync` command). If it isn't
> available, recovery reports a clear error and (for plain `delete`) deletion
> still proceeds.

## Stop — pause-and-keep (preserve, don't delete)

When an effort is **paused but not done** (e.g. waiting on an external gate — a
feed publish, a redeploy, a review), release the compute but **keep** the
CodeSpace so it resumes later. `stop` is the pause-and-keep counterpart to
`finalize --delete`: it recovers session-state (same hook as `finalize`) and
then shuts the CodeSpace down gracefully via `gh codespace stop`. It **never
deletes**, and a stopped CodeSpace **boots again on the next connect** (no
explicit start needed).

```bash
# Recover sessions, then gracefully stop (preserve for later resume)
<agent-codespaces catalog argv[0]> stop <codespace-name>
<agent-codespaces catalog argv[0]> stop <codespace-name> --no-sync
```

Unlike `finalize --delete`, a failed pre-stop recovery does **not** block the
stop — stopping is non-destructive, so the sessions stay on the preserved
CodeSpace and can be recovered on a later connect. `stop` is **idempotent**: a
no-op if the CodeSpace is already `Shutdown`.

Never use a bare `gh codespace stop` — it bypasses the session-recovery hook.

## Syncing Dotfiles on CodeSpaces

Use the catalog command's `ssh` action to pull latest:
```bash
<agent-codespaces catalog argv[0]> ssh <name> --remote-cmd "cd /workspaces/.codespaces/.persistedshare/dotfiles && git pull origin main && bash install.sh"
```

If credential relay isn't active, pass the token via `--remote-cmd`:
```bash
token=$(gh auth token)
<agent-codespaces catalog argv[0]> ssh <name> --no-relay --remote-cmd "cd /workspaces/.codespaces/.persistedshare/dotfiles && git pull https://x-access-token:${token}@github.com/<user>/dotfiles.git main"
```

### Fresh clone (when .git is missing or corrupted)

```bash
token=$(gh auth token)
<agent-codespaces catalog argv[0]> ssh <name> --no-relay --remote-cmd "rm -rf /workspaces/.codespaces/.persistedshare/dotfiles && git clone https://x-access-token:${token}@github.com/<user>/dotfiles.git /workspaces/.codespaces/.persistedshare/dotfiles"
<agent-codespaces catalog argv[0]> ssh <name> --no-relay --remote-cmd "bash /workspaces/.codespaces/.persistedshare/dotfiles/install.sh"
```

> **Do NOT use `tar` or `git archive` pipes** to sync dotfiles. They
> destroy `.git` state, introduce CRLF from Windows, and leave stale
> files from renames/deletes. Always maintain a proper git clone.
>
> **Always use the agent-codespaces catalog command's `ssh` action**, not bare
> `gh codespace ssh`.
> The latter bypasses ssh-manager and can conflict with managed
> connections (ControlMaster sockets, credential relay tunnels).

## Credential Relay

The credential relay is a host-side TCP server owned by the agent-bridge daemon.
Its port is dynamic by default (`credentials.relay_port: 0`): the daemon
publishes the live port, and agent-codespaces follows that when creating the SSH
reverse-forward. A positive `credentials.relay_port` pins a fixed port; `9857`
is only a last-resort compatibility fallback when no live/pinned port is known.
It proxies credential requests to local credential stores.

### How It Works

1. agent-bridge runs the relay server on `127.0.0.1:<live-port>`
2. The catalog command's `ssh` action includes an SSH reverse-forward for that live port
3. CodeSpace sends git-credential-protocol requests to `localhost:<live-port>`
4. Relay routes to matching source (GCM / `git-credential`, plus `az-login` for
   allowed Azure resources)
5. Response flows back through the tunnel

### Available Sources

| Source | Action | What It Does |
|--------|--------|-------------|
| `git-credential` | `get`/`store`/`erase` | Proxies to local Git Credential Manager |
| `az-login` | `get-azure-token` | Returns Azure access tokens for the built-in ADO/Storage resources plus configured `allowed_resources` |

### Policy Enforcement

All requests pass through a policy gate before reaching any source:
- **Action allowlist** -- only recognized actions are accepted
- **Host allowlist** -- fnmatch-style patterns per source
- **Resource allowlist** -- exact-match for Azure resources (az-login)

## Agent-Bridge Integration

**No manual registration is required.** When agent-codespaces is installed, its
sessionStart hook drops a small **namespace-provider manifest** into
`~/.agent-bridge/providers.d/` (declaring the `codespace:` namespace and the
absolute path to the agent-codespaces binstub). agent-bridge discovers that
manifest there and registers the `codespace:` **namespace resolver**, driving
the provider over a process boundary. That resolver lists and resolves your
CodeSpaces **live** (via `gh codespace list`) on demand — so
the payload-local `agents` output shows them and
`<agent-bridge catalog argv[0]> send codespace:<name>` works immediately,
with no expiry, including newly-created CodeSpaces.

Because discovery is declarative (a dropped manifest carrying an absolute
command), it works even though the agent-bridge daemon runs from its own
isolated venv and does not need agent-codespaces importable or on `PATH`. There
is **no imperative `bridge register` step** — installing the plugin is all
that's needed.

The bridge-facing relay/session-host paths are likewise CLI-seam first:
The bridge calls the registered **management entry point** with
`relay-profile`, `relay-launch-env`, and `provision-command` when it needs the
CodeSpace relay policy or launch prelude. Session command catalogs do not
replace this provider/supervisor boundary; moving that launcher requires
installation-context ownership in a later phase.
In-process imports remain only as degrade-safe fallbacks when the bridge venv
happens to vendor the package; the agent-codespaces CLI/runtime is still owned
by agent-codespaces.

## Troubleshooting

- **SSH hangs** -- test with
  `<agent-codespaces catalog argv[0]> ssh <name> --remote-cmd "echo ok" --no-relay`.
  If that works, check credential relay. If it doesn't, verify
  `<agent-codespaces catalog argv[0]> doctor` / `gh auth status` is authenticated.
- **Bridge connection fails** -- the bridge auto-starts Shutdown
  CodeSpaces and retries SSH (up to ~180 s). If it still fails, try
  `<agent-codespaces catalog argv[0]> ssh <name> --remote-cmd "echo ok" --no-relay`.
  Check `<agent-bridge catalog argv[0]> status` and
  `~/.agent-bridge/agent-bridge-err.log`.
- **No `codespace:` targets** -- provider registration still uses the explicit
  management binstub, not the session catalog. If that binstub is missing,
  stamp it from the same explicitly selected payload shown in
  `codespaces-setup` § *Readiness*, then start a new session so the provider
  manifest is registered.
- **Session fails on start** -- check `~/.agent-bridge/agent-bridge-err.log`.
  Common cause: wrong `ssh_user` in
  `.copilot-extensions/agent-codespaces/config.yaml`.
- **Credential relay not working** -- check that `--no-relay` was not
  accidentally passed, then confirm agent-bridge's relay is up
  (`agent-bridge service restart` repairs the owner daemon). <!-- marketplace-isolation: allow service-management -->
  The client warns when the
  host relay is not listening before connect.
- **Quota exceeded** -- creating or connecting to a CodeSpace (a Shutdown one
  boots on connect) returns HTTP 400 "too many codespaces running" once the
  concurrently-running cap is hit.
  `<agent-codespaces catalog argv[0]> stop <name>` idle
  CodeSpaces first (preserves them), then retry.
- **"gh CLI not found"** -- install from https://cli.github.com/
- **WSL credential slowness** -- first GCM call through PowerShell
  takes ~25s. Subsequent calls use the 300s cache.
