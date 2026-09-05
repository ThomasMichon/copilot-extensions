# Agent-Bridge CLI Command Reference

Use the exact `argv[0]` from the agent-bridge session command catalog for
interactive bridge operations in this reference. Replace
`<agent-bridge catalog argv[0]>` with that path and never search `PATH`.
Commands labeled as service, deployment, provider, or elevated management
boundaries remain literal global-wrapper invocations.

Full command catalog for the `agent-bridge` CLI. See [SKILL.md](../SKILL.md)
for the overview, when to use the bridge vs internal sub-agents, and common
patterns. All commands connect to the local agent-bridge HTTP API; the service
must be running (the `start` management operation).

`agent-bridge` is a logical command name throughout this reference. Replace each
leading token with the exact `argv[0]` from the agent-bridge session command
catalog; never resolve it through ambient `PATH`.

## Contents
- List Available Agents / Machines
- Send a Prompt to an Agent (sync / async, sessions, timeouts)
- Session management
- Config (adopt / show)
- Service control

---
## CLI Commands

All commands connect to the local agent-bridge HTTP API. The service must
be running (the `start` management operation) for client commands to work.

### List Available Agents

```bash
<agent-bridge catalog argv[0]> agents
<agent-bridge catalog argv[0]> --json agents
<agent-bridge catalog argv[0]> --project <repo> agents
<agent-bridge catalog argv[0]> agents --all-projects
```

Shows all registered agents from the topology config (name, type, host,
spawnable status).

### List Machines

```bash
<agent-bridge catalog argv[0]> machines
<agent-bridge catalog argv[0]> --json machines
<agent-bridge catalog argv[0]> --project <repo> machines
<agent-bridge catalog argv[0]> machines --all-projects
```

`agents` and `machines` infer their project from CWD. Top-level `--project`
selects a different project; `--all-projects` shows the fleet-wide catalog. A
neutral CWD with no adopted project also falls back to the fleet-wide view.
For compatibility with machine consumers, bare `--json` listing is fleet-wide;
combine `--json` with explicit top-level `--project` for filtered structured
output.
Invalid topology profiles are reported on stderr after valid partial results and
make the command exit with status 2.

Shows all machines in the topology with SSH readiness and environment
details.

### Send a Prompt to an Agent

```bash
# Reuse this caller's session for the agent (resumes it if stopped),
# or start one if none exists, then send a prompt (streams response)
<agent-bridge catalog argv[0]> send <agent-name> "your prompt here"

# Send to a specific existing session
<agent-bridge catalog argv[0]> send <session-id> "follow-up prompt"

# Fire-and-forget (don't wait for response)
<agent-bridge catalog argv[0]> send <agent-name> "do this" --no-wait

# Multi-line / quote-heavy prompt: read it from a file (or '-' for stdin) so it
# never transits the shell's argv (avoids PowerShell mangling a prompt at the
# first embedded double-quote). Mutually exclusive with the positional prompt.
<agent-bridge catalog argv[0]> send <agent-name> --prompt-file ./dispatch.md
Get-Content ./dispatch.md | & "<agent-bridge catalog argv[0]>" send <agent-name> --prompt-file -

# Deliver INTO a live interactive session (human-attached), attributed and
# answerable -- routes to the message queue, not an ACP turn. The receiver
# replies with `<agent-bridge catalog argv[0]> send <reply-to> "..."`.
<agent-bridge catalog argv[0]> send <live-session-id> "message body"
<agent-bridge catalog argv[0]> send <live-session-id> "msg" --from "reviewer@example-host" --reply-to <my-session-id>
# Durable producers pass a stable key so retries return the original message id.
<agent-bridge catalog argv[0]> send <live-session-id> "wake" --no-wait --idempotency-key wake:example:1
```

`send` auto-detects whether the target is an agent name, a bridge-owned session
ID, or a **live interactive session** (delivered as an attributed
`<agent-message>` envelope). See
[agent-messages.md](agent-messages.md) for the receive/reply convention.

When given an **agent name**, it never starts a *fresh* session on top of an
existing one: it reuses this caller's session for that agent — keyed by
`(agent, caller)`, where the caller is the current worktree
(`agent-worktrees get worktree-dir`, or `--caller`) — <!-- marketplace-isolation: allow agent-worktrees-management -->
and
**resumes it if stopped**. Only when this caller has no session for the agent
is a new one started. Output streams in real-time: response text, thought
blocks, and tool call summaries.

> **There is no `send --new`.** It was removed because it silently reused a
> pre-existing (often stopped, stale) session instead of creating a fresh one.
> To force a brand-new session, use the payload-local `create` operation below.

### Create a Fresh Session

```bash
# Force a brand-new session for an agent (no reuse)
<agent-bridge catalog argv[0]> create <agent-name>

# ...and send a first prompt in one step
<agent-bridge catalog argv[0]> create <agent-name> "your first prompt"

# ...or read the first prompt from a file (or '-' for stdin) -- robust for
# multi-line / quote-heavy dispatch prompts (no argv mangling)
<agent-bridge catalog argv[0]> create <agent-name> --prompt-file ./dispatch.md --no-wait

# Orchestration seam: atomically capture the exact session this create owns
<agent-bridge catalog argv[0]> create <agent-name> --prompt-file ./dispatch.md \
  --session-id-file ./created-session-id
```

`--session-id-file` is written immediately after the fresh session is created
and before the first prompt is streamed. Use it when a caller must bind later
status, model, or result reads to this exact create rather than selecting a
same-agent session from a broad listing.

`create` always spawns a fresh session, bypassing caller reuse. For agents
that allow only **one session at a time** — CodeSpaces share a single
checkout — `create` **refuses** if a session already exists rather than
silently latching onto it, and tells you to end the existing one first:

```bash
<agent-bridge catalog argv[0]> end <existing-session-id>   # free the CodeSpace
<agent-bridge catalog argv[0]> create <agent-name> "..."   # then start clean
```

### Choosing send vs create — check for an outstanding session first

Before dispatching work to an agent, **check whether it already has a
session and whether that session's state is relevant to the work**:

```bash
<agent-bridge catalog argv[0]> sessions          # is there a session for this agent/caller?
<agent-bridge catalog argv[0]> session-usage <session-id>   # how full is its context?
```

- **Relevant & healthy** (same effort, context well under ~70%) → `send`
  to continue it. Idle sessions continue normally. A session that was stopped
  *mid-turn* can return `Operation cancelled by user` + an empty `end_turn` on
  the **first** reattached turn (a stale host cancel draining, not a broken
  resume) — just `send` again and it continues.
- **Stale / unrelated / context-heavy** (different effort, near the context
  limit, or known-bad state) →
  `<agent-bridge catalog argv[0]> end <session-id>` then the payload-local
  `create` operation for a clean start.

`send` is the safe default; it reuses/resumes and drains a stale cancel after
one turn. Reach for `create` only when you have decided the existing session must
be discarded (or the cancel signature *persists* across sends). See the
*Resume on drop* section of the skill for the cause.

### Session Management

```bash
# List all sessions (includes CONTEXT column showing usage %)
<agent-bridge catalog argv[0]> sessions
<agent-bridge catalog argv[0]> sessions --status idle

# Check context window usage for a session
<agent-bridge catalog argv[0]> session-usage <session-id>

# Compact one-screen status: state, in-flight tool + elapsed, and how far
# behind your delivery cursor is (head/acked) -- without dumping the feed.
<agent-bridge catalog argv[0]> status <session-id>
<agent-bridge catalog argv[0]> status <session-id> --steps 5   # also show the last 5 collapsed steps

# Wait for a running session's current turn
<agent-bridge catalog argv[0]> wait <session-id>

# Stop a session (preserves state for resume)
<agent-bridge catalog argv[0]> stop <session-id>

# Resume a stopped session -- or load/take-over a worktree by handle.
# The target may be an owned ACP session id OR a worktree handle. If it is a
# worktree handle whose interactive CLI has stopped (e.g. after a reboot), the
# worktree is loaded as a fresh owned session -- a dormant worktree is just a
# note. If a *live* interactive CLI still holds the worktree, resume refuses
# (break-glass); stop that CLI first, then re-run with --force to take it over.
<agent-bridge catalog argv[0]> resume <session-id|worktree-handle>
<agent-bridge catalog argv[0]> resume <worktree-handle> --force   # affirmative take-over

# End a session (full cleanup)
<agent-bridge catalog argv[0]> end <session-id>

# Garbage-collect aged terminal/disconnected sessions + compact the DB.
# Runs automatically (startup + periodic sweep); this forces it on demand.
<agent-bridge catalog argv[0]> gc
```

### Service Control

Use the `service` subcommands to control the long-running daemon. These
delegate to the platform service manager (Windows scheduled task / Linux
systemd user unit) that the installer registered, so they control the **same**
instance that auto-starts at logon -- and they fall back to a detached spawn if
no service manager is registered.

```bash
agent-bridge service start      # start the daemon (no-op if already running) -- marketplace-isolation: allow service-management
agent-bridge service stop       # stop the daemon (kills the worker + releases the port) -- marketplace-isolation: allow service-management
agent-bridge service restart    # stop, wait for the port to release, start -- marketplace-isolation: allow service-management
agent-bridge service status     # running state + bound port + PID -- marketplace-isolation: allow service-management
```

> **Note:** the payload-local `stop <session-id>` operation stops a *session*,
> not the service. For the daemon, use the literal management command
> `agent-bridge service stop`. <!-- marketplace-isolation: allow service-management -->

> **Windows headless (run whether logged on or not):** by default the Windows
> daemon runs from an *at-logon* scheduled task, so it only runs while a user is
> interactively signed in. For an always-on machine reached over SSH/RDP with no
> persistent session, (re)install with `install.ps1 install -NonInteractive`
> (or `AGENT_BRIDGE_NONINTERACTIVE=1`) to register a **boot-triggered S4U** task
> instead. A working headless registration is preserved across updates. If a
> requested start remains at `267011` (`SCHED_S_TASK_HAS_NOT_RUN`), the likely
> cause is an S4U token-acquisition failure; an update without the explicit
> non-interactive opt-in recovers to the default interactive `AtLogOn` task.
> See the `agent-worktrees:copilot-extensions-setup` skill.

The literal management command `agent-bridge start` (no `service`) runs the <!-- marketplace-isolation: allow service-management -->
server in the **foreground**.
It
is the entry point the service manager invokes, and is useful for debugging.
By default the daemon binds an **OS-assigned ephemeral** loopback port and
advertises it via the routing table (`active.json`); add `--port` / `--bind`
only to pin a fixed port (e.g. for debugging).

```bash
# Foreground (debugging) -- blocks the terminal
agent-bridge start # marketplace-isolation: allow service-management
agent-bridge start --port 9280 --bind 127.0.0.1 # pin a fixed port (default is dynamic) -- marketplace-isolation: allow service-management

# Health check (also shows the bound URL)
<agent-bridge catalog argv[0]> status

# Version
<agent-bridge catalog argv[0]> version
```

### Remote Venue Parity Acceptance

`parity` creates an isolated bridge session, runs a redacted quality/auth probe,
stops and resumes the session, verifies ACP continuity plus a live resumed
child, completes another turn, and ends the session. (An explicit idle stop may
reap and recreate the child; same-child PID is reserved for the frontend-loss
recovery scenario.) Target identity and
expected workspace/capability are always caller-supplied; no product repo or
credential endpoint is hardcoded.

```bash
# Baseline cwd + repo-local capability + same-child reattach
<agent-bridge catalog argv[0]> parity container:example-1 \
  --expect-workspace /workspaces/example-web \
  --expect-capability example-local-skill

# Add credential-consumer checks. Values are captured privately; JSON contains
# booleans only, never tokens or helper output.
<agent-bridge catalog argv[0]> parity container:example-1 \
  --expect-workspace /workspaces/example-web \
  --auth \
  --ado-url https://example.visualstudio.com/Project/_git/repo \
  --azure-scope https://storage.azure.com/.default \
  --json

# Narrow no-regression smoke against an explicitly chosen idle CodeSpace.
<agent-bridge catalog argv[0]> parity codespace:example-codespace \
  --expect-workspace /workspaces/example-web \
  --expect-capability example-local-skill
```

The command refuses any one-session venue that already has a bridge session
rather than taking it over. Use a dedicated target with no existing session.
`--keep-session` is diagnostic break glass; normal runs always clean up in
`finally` (and retained sessions keep their redacted probe event log).

### Graceful Redeploy (routing table + drain + installer-driven cutover)

A redeploy no longer has to hard-kill live work. Clients resolve the daemon
through a **routing table** (`~/.agent-bridge/active.json`) instead of the
static config port: `BridgeClient.from_config()` reads the table first and falls
back to `config.yaml` when it is absent (so the table is inert until a daemon
publishes it). This lets a new daemon come up on a fresh port, the table flip to
it, and the old daemon retire -- with no client ever dialing a dead port.

```bash
# Stop accepting new sessions/turns and wait for in-flight work to settle
# (the busy oracle: streaming turns + active background sub-agents). Bounded by
# --timeout; --force proceeds anyway at timeout. Exit 0 = clean, 2 = still busy.
# Teardown (stop/end) stays permitted while draining (#1755). Set/clear is
# logged; /health exposes a drain{} block; a watchdog auto-releases a stuck
# drain after ~15min so an aborted cutover self-heals (#1757).
agent-bridge drain --timeout 300 # marketplace-isolation: allow deployment-management
agent-bridge undrain # release the gate (rollback) -- marketplace-isolation: allow deployment-management

# Active/passive cutover is an INTERNAL installer seam -- NOT an operator
# command. Do NOT run the bridge deploy seam to ship a build. The canonical
# deploy path is the normal plugin update flow: refresh the payload
# (`copilot plugin update agent-bridge`) and let the plugin's installer reconcile
# cut the daemon over (`scripts/install.sh update`, via the host's plugin-update
# integration or the sessionStart hook). Keep `plugin.json` / `pyproject.toml`
# versions in lockstep (the marketplace keys off `plugin.json`). `deploy` remains
# exposed only for installer internals and recovery:
agent-bridge deploy --recover # heal a prior aborted cutover, then exit -- marketplace-isolation: allow deployment-management
```

The installer `update` path performs graceful cutover automatically when a live
daemon is running and the new slot differs from the active slot; it falls back
to drain/stop/start only if cutover cannot run or fails. `AGENT_BRIDGE_ZERO_DOWNTIME`
is accepted for compatibility but is no longer an opt-in switch.

```bash
install.ps1 update    # Windows, from the plugin payload
install.sh update     # Linux/WSL, from the plugin payload
```

> A passive instance (`agent-bridge start --passive`) <!-- marketplace-isolation: allow deployment-management -->
> does not self-publish the
> routing table or bind the credential relay (ephemeral) -- the deploy orchestrator
> flips the table after a health check and calls `/api/v1/relay/adopt` once the
> old daemon releases the relay port. The port-keyed singleton lock lets the
> active and passive daemons coexist on one config dir during the overlap.



```bash
# Run as an ACP agent on stdio (for chat UIs / upstream ACP clients)
agent-bridge agent --agent my-agent # marketplace-isolation: allow provider-startup
```

Presents agent-bridge as an ACP-compatible agent. Upstream ACP clients
connect via stdio and the bridge routes prompts to the named downstream
agent. Used by chat interfaces that speak ACP natively.

### Elevated Agents (Windows)

Some local agents must run **elevated** (admin) -- e.g. an enlistment-based
`base_repo` agent that needs admin plus a build environment. Such a project is
flagged once, at adoption time:

```bash
agent-worktrees register <Project> --base-repo --elevated # marketplace-isolation: allow agent-worktrees-management
```

After that, **just send to it by its bare name** -- no special prefix:

```bash
<agent-bridge catalog argv[0]> send <Project> "do the elevated work"
```

The (non-elevated) primary daemon cannot spawn an elevated Copilot directly, so
for a flagged agent it transparently:

1. **auto-ensures an elevated sub-daemon** -- a second agent-bridge on an
   **OS-assigned ephemeral** loopback port (discovered via its own
   `<config>/elevated/active.json` routing table; dotfiles #694), run elevated
   via a persistent `/RL HIGHEST` scheduled task, isolated under
   `<config>/elevated/`; and
2. **relays** the session to it over ACP-over-WebSocket (the internal
   `acp-connect ws://127.0.0.1:<port>/acp/<Project>` operation, where `<port>` is the
   discovered elevated port). Because the whole sub-daemon
   is elevated, the agent it spawns is elevated too.

This only triggers on Windows when the primary is **not already elevated**; an
already-elevated daemon (and the sub-daemon itself) spawns the agent locally, so
there is no recursion.

**Headless after first use.** The scheduled task is consented **once** (a single
UAC prompt the first time it is registered); every cold start afterwards runs it
with `schtasks /run` -- **no UAC**. The sub-daemon also **auto-shuts-down** after
~10 min with no active sessions (so it does not linger), and the persistent task
restarts it headlessly on the next request. Manage it directly when needed:

```bash
agent-bridge elevated start # marketplace-isolation: allow elevated-management
agent-bridge elevated status # marketplace-isolation: allow elevated-management
agent-bridge elevated stop # marketplace-isolation: allow elevated-management
agent-bridge elevated stop --deregister # marketplace-isolation: allow elevated-management
```

> **Security (v1):** the sub-daemon is loopback-only and bearer-token gated, but
> the token is in a user-readable file, so any same-user process could drive the
> elevated agent. Acceptable on a single-user dev box; hardening is tracked
> separately.

### Config Management

These commands modify **user-level bridge state**, not repository content.
First edit and publish topology through the repository's normal worktree and
contribution flow; then adopt from the canonical checkout to project that
published state into `~/.agent-bridge/config.yaml`.

```bash
# Show current config
<agent-bridge catalog argv[0]> config show
<agent-bridge catalog argv[0]> --json config show

# Add/update a topology profile for a repo
<agent-bridge catalog argv[0]> config adopt --repo /path/to/repo --profile multi-machine system

# Remove a topology profile
<agent-bridge catalog argv[0]> config remove my-profile

# Validate config (checks file paths, topology completeness)
<agent-bridge catalog argv[0]> config validate
```

Explicit `--machines-yaml` and `--agents-config` arguments remain exact even
when `--repo` itself is canonicalized to the anchor. If such a path names a
temporary worktree, removing that worktree strands the profile; `config
validate` reports the missing file. Before re-adopting from canonical source
paths, back up the profile stanza in `~/.agent-bridge/config.yaml`, including
`default_copilot_args` and `default_env`; adoption replaces the profile and
those spawn defaults must be restored afterward.

For first-time setup, see the `agent-worktrees:copilot-extensions-setup` skill. For
detailed topology configuration, see `plugins/agent-bridge/docs/machine-config.md`.
