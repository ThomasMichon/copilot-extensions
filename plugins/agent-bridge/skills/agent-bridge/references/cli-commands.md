# Agent-Bridge CLI Command Reference

Full command catalog for the `agent-bridge` CLI. See [SKILL.md](../SKILL.md)
for the overview, when to use the bridge vs internal sub-agents, and common
patterns. All commands connect to the local agent-bridge HTTP API; the service
must be running (`agent-bridge start`).

## Contents
- List Available Agents / Machines
- Send a Prompt to an Agent (sync / async, sessions, timeouts)
- Session management
- Config (adopt / show)
- Service control

---
## CLI Commands

All commands connect to the local agent-bridge HTTP API. The service must
be running (`agent-bridge start`) for client commands to work.

### List Available Agents

```bash
agent-bridge agents
agent-bridge agents --json
```

Shows all registered agents from the topology config (name, type, host,
spawnable status).

### List Machines

```bash
agent-bridge machines
agent-bridge machines --json
```

Shows all machines in the topology with SSH readiness and environment
details.

### Send a Prompt to an Agent

```bash
# Reuse this caller's session for the agent (resumes it if stopped),
# or start one if none exists, then send a prompt (streams response)
agent-bridge send <agent-name> "your prompt here"

# Send to a specific existing session
agent-bridge send <session-id> "follow-up prompt"

# Fire-and-forget (don't wait for response)
agent-bridge send <agent-name> "do this" --no-wait

# Deliver INTO a live interactive session (human-attached), attributed and
# answerable -- routes to the message queue, not an ACP turn. The receiver
# replies with `agent-bridge send <reply-to> "..."`.
agent-bridge send <live-session-id> "message body"
agent-bridge send <live-session-id> "msg" --from "reviewer@lambda-core" --reply-to <my-session-id>
```

`send` auto-detects whether the target is an agent name, a bridge-owned session
ID, or a **live interactive session** (delivered as an attributed
`<agent-message>` envelope). See
[agent-messages.md](agent-messages.md) for the receive/reply convention.

When given an **agent name**, it never starts a *fresh* session on top of an
existing one: it reuses this caller's session for that agent — keyed by
`(agent, caller)`, where the caller is the current worktree
(`agent-worktrees get worktree-dir`, or `--caller`) — and
**resumes it if stopped**. Only when this caller has no session for the agent
is a new one started. Output streams in real-time: response text, thought
blocks, and tool call summaries.

> **There is no `send --new`.** It was removed because it silently reused a
> pre-existing (often stopped, stale) session instead of creating a fresh one.
> To force a brand-new session, use `agent-bridge create` (below).

### Create a Fresh Session

```bash
# Force a brand-new session for an agent (no reuse)
agent-bridge create <agent-name>

# ...and send a first prompt in one step
agent-bridge create <agent-name> "your first prompt"
```

`create` always spawns a fresh session, bypassing caller reuse. For agents
that allow only **one session at a time** — CodeSpaces share a single
checkout — `create` **refuses** if a session already exists rather than
silently latching onto it, and tells you to end the existing one first:

```bash
agent-bridge end <existing-session-id>   # free the CodeSpace
agent-bridge create <agent-name> "..."   # then start clean
```

### Choosing send vs create — check for an outstanding session first

Before dispatching work to an agent, **check whether it already has a
session and whether that session's state is relevant to the work**:

```bash
agent-bridge sessions          # is there a session for this agent/caller?
agent-bridge session-usage <session-id>   # how full is its context?
```

- **Relevant & healthy** (same effort, context well under ~70%) → `send`
  to continue it (it resumes automatically if stopped).
- **Stale / unrelated / context-heavy** (different effort, near the context
  limit, or known-bad state) → `agent-bridge end <session-id>` then
  `agent-bridge create` for a clean start.

`send` is the safe default (it reuses/resumes); reach for `create` only when
you have decided the existing session must be discarded.

### Session Management

```bash
# List all sessions (includes CONTEXT column showing usage %)
agent-bridge sessions
agent-bridge sessions --status idle

# Check context window usage for a session
agent-bridge session-usage <session-id>

# Compact one-screen status: state, in-flight tool + elapsed, and how far
# behind your delivery cursor is (head/acked) -- without dumping the feed.
agent-bridge status <session-id>
agent-bridge status <session-id> --steps 5   # also show the last 5 collapsed steps

# Wait for a running session's current turn
agent-bridge wait <session-id>

# Stop a session (preserves state for resume)
agent-bridge stop <session-id>

# Resume a stopped session
agent-bridge resume <session-id>

# End a session (full cleanup)
agent-bridge end <session-id>

# Garbage-collect aged terminal/disconnected sessions + compact the DB.
# Runs automatically (startup + periodic sweep); this forces it on demand.
agent-bridge gc
```

### Service Control

Use the `service` subcommands to control the long-running daemon. These
delegate to the platform service manager (Windows scheduled task / Linux
systemd user unit) that the installer registered, so they control the **same**
instance that auto-starts at logon -- and they fall back to a detached spawn if
no service manager is registered.

```bash
agent-bridge service start      # start the daemon (no-op if already running)
agent-bridge service stop       # stop the daemon (kills the worker + releases the port)
agent-bridge service restart    # stop, wait for the port to release, start
agent-bridge service status     # running state + bound port + PID
```

> **Note:** plain `agent-bridge stop <session-id>` stops a *session*, not the
> service. For the daemon, always use `agent-bridge service stop`.

> **Windows headless (run whether logged on or not):** by default the Windows
> daemon runs from an *at-logon* scheduled task, so it only runs while a user is
> interactively signed in. For an always-on machine reached over SSH/RDP with no
> persistent session, (re)install with `install.ps1 install -NonInteractive`
> (or `AGENT_BRIDGE_NONINTERACTIVE=1`) to register a **boot-triggered S4U** task
> instead -- opt-in, preserved across updates. See the `copilot-extensions-setup`
> skill.

`agent-bridge start` (no `service`) runs the server in the **foreground** -- it
is the entry point the service manager invokes, and is useful for debugging.
Add `--port` / `--bind` to override the platform default (9280 Windows / 9281
Linux/WSL).

```bash
# Foreground (debugging) -- blocks the terminal
agent-bridge start
agent-bridge start --port 9280 --bind 127.0.0.1

# Health check (also shows the bound URL)
agent-bridge status

# Version
agent-bridge version
```

### Zero-Downtime Redeploy (routing table + drain + cutover)

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
agent-bridge drain --timeout 300
agent-bridge undrain                 # release the gate (rollback)

# Active/passive cutover: spawn the new daemon on a free port, flip the routing
# table, drain + retire the old one. Rolls back on any pre-commit failure.
# Writes a durable breadcrumb (cutover.json) so an aborted cutover is traceable
# and its stranded survivor can be undrained (#1756).
agent-bridge deploy --drain-timeout 300 [--force]
agent-bridge deploy --recover        # only heal a prior aborted cutover, then exit
```

The installer `update` path drains before stopping by default (no hard-killed
turns up to `AGENT_BRIDGE_DRAIN_TIMEOUT`). Full zero-downtime cutover is opt-in
while service-manager reconciliation is validated:

```bash
AGENT_BRIDGE_ZERO_DOWNTIME=1 aperture-labs services agent-bridge update
```

> A passive instance (`agent-bridge start --passive`) does not self-publish the
> routing table or bind the credential relay (9857) -- the deploy orchestrator
> flips the table after a health check and calls `/api/v1/relay/adopt` once the
> old daemon releases the relay port. The port-keyed singleton lock lets the
> active and passive daemons coexist on one config dir during the overlap.



```bash
# Run as an ACP agent on stdio (for chat UIs / upstream ACP clients)
agent-bridge agent --agent my-agent
```

Presents agent-bridge as an ACP-compatible agent. Upstream ACP clients
connect via stdio and the bridge routes prompts to the named downstream
agent. Used by chat interfaces that speak ACP natively.

### Elevated Agents (Windows)

Some local agents must run **elevated** (admin) -- e.g. an enlistment-based
`base_repo` agent that needs admin plus a build environment. Such a project is
flagged once, at adoption time:

```bash
agent-worktrees register <Project> --base-repo --elevated   # writes elevated: true
```

After that, **just send to it by its bare name** -- no special prefix:

```bash
agent-bridge send <Project> "do the elevated work"
```

The (non-elevated) primary daemon cannot spawn an elevated Copilot directly, so
for a flagged agent it transparently:

1. **auto-ensures an elevated sub-daemon** -- a second agent-bridge on loopback
   `127.0.0.1:9281`, run elevated via a persistent `/RL HIGHEST` scheduled task,
   isolated under `<config>/elevated/`; and
2. **relays** the session to it over ACP-over-WebSocket (`agent-bridge
   acp-connect ws://127.0.0.1:9281/acp/<Project>`). Because the whole sub-daemon
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
agent-bridge elevated start          # ensure up (headless once the task exists)
agent-bridge elevated status         # port / up / task-registered / agents
agent-bridge elevated stop           # stop now, headless (keeps task for restart)
agent-bridge elevated stop --deregister  # full teardown: delete the task (one UAC)
```

> **Security (v1):** the sub-daemon is loopback-only and bearer-token gated, but
> the token is in a user-readable file, so any same-user process could drive the
> elevated agent. Acceptable on a single-user dev box; hardening is tracked
> separately.

### Config Management

```bash
# Show current config
agent-bridge config show
agent-bridge config show --json

# Add/update a topology profile for a repo
agent-bridge config adopt --repo /path/to/repo --profile facility

# Remove a topology profile
agent-bridge config remove my-profile

# Validate config (checks file paths, topology completeness)
agent-bridge config validate
```

For first-time setup, see the `copilot-extensions-setup` skill. For
detailed topology configuration, see `plugins/agent-bridge/docs/machine-config.md`.

