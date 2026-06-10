---
name: agent-bridge
description: >
  Agent-bridge control plane -- send prompts to agents on OTHER MACHINES
  via CLI commands. Use this for cross-machine communication, NOT the
  Task tool.
  Trigger phrases include:
  - 'agent-bridge'
  - 'agent-bridge send'
  - 'remote agent'
  - 'send to agent'
  - 'bridge to'
  - 'cross-machine'
  - 'external agent'
  - 'inter-agent'
  - 'relay to'
  - 'talk to <machine>'
  - 'send to <machine>'
---

# Agent-Bridge Control Plane

## Agent-Bridge vs Internal Sub-Agents -- READ THIS FIRST

**agent-bridge is NOT the Task tool.** They solve completely different
problems:

| | agent-bridge | Task tool (sub-agents) |
|---|---|---|
| **What** | Communicates with Copilot sessions on **other physical machines** | Spawns local background agents in **this session** |
| **How** | `agent-bridge send <agent> "prompt"` CLI command | `task` function call in your response |
| **Transport** | SSH to remote machines | Local subprocess |
| **Scope** | Cross-machine, cross-network | Same machine only |

**Rule:** When asked to "talk to", "send to", "relay to", or
"communicate with" a named machine or agent from the topology, **ALWAYS
use `agent-bridge send <agent-name> "prompt"`**. Never use the Task
tool for cross-machine communication -- it cannot reach other machines.

Run `agent-bridge agents` to see which agent names are available. If
your deployment includes a facility-specific adapter skill (e.g.
`facility-agent-bridge`), it will list the concrete machine and agent
names for your environment.

### Relay Chain Pattern

When relaying a message through multiple machines (A -> B -> C), each
hop uses `agent-bridge send` on **its own local bridge** to reach the
next machine. The chain is:

```
Machine A: agent-bridge send agent-on-B "relay this to C"
Machine B: agent-bridge send agent-on-C "the message"
```

Each machine's bridge manages its own outbound connections. Do NOT
create all sessions from one machine (that's a star, not a chain).

Agent-bridge is the inter-agent communication service. It manages
persistent sessions with agents running on any configured machine
via local subprocess or SSH transport.

## Service Architecture

Each machine runs its own agent-bridge instance. The default port is
platform-specific: **9280 on Windows**, **9281 on Linux/WSL**. This avoids
TCP port collisions when both environments share the same host (WSL2 shares
the Windows TCP port space). The topology
is a mesh -- each instance manages outbound connections to other machines
via SSH. Sessions are persistent (SQLite-backed) and survive service
restarts.

Runs on **Windows** (scheduled task + PID file), **Linux/WSL** (systemd),
with macOS support planned.

**Installed as plugin:** Part of the `copilot-extensions` marketplace
plugin. Source code lives in the installed plugin directory at
`~/.copilot/installed-plugins/copilot-extensions/agent-bridge/`.

**Config lives at:** `~/.agent-bridge/config.yaml` (topology profiles
pointing to this repo's `machines.yaml` and `acp-agents.json`)

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
# Start a new session and send a prompt (streams response)
agent-bridge send <agent-name> "your prompt here"

# Send to an existing session
agent-bridge send <session-id> "follow-up prompt"

# Fire-and-forget (don't wait for response)
agent-bridge send <agent-name> "do this" --no-wait
```

The `send` command auto-detects whether the target is an agent name (starts
a new session) or a session ID (sends to existing session). Output streams
in real-time: response text, thought blocks, and tool call summaries.

### Session Management

```bash
# List all sessions (includes CONTEXT column showing usage %)
agent-bridge sessions
agent-bridge sessions --status idle

# Check context window usage for a session
agent-bridge session-usage <session-id>

# Wait for a running session's current turn
agent-bridge wait <session-id>

# Stop a session (preserves state for resume)
agent-bridge stop <session-id>

# Resume a stopped session
agent-bridge resume <session-id>

# End a session (full cleanup)
agent-bridge end <session-id>
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

### ACP Agent Mode

```bash
# Run as an ACP agent on stdio (for chat UIs / upstream ACP clients)
agent-bridge agent --agent my-agent
```

Presents agent-bridge as an ACP-compatible agent. Upstream ACP clients
connect via stdio and the bridge routes prompts to the named downstream
agent. Used by chat interfaces that speak ACP natively.

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

## Common Patterns

### Quick Remote Agent Interaction

```bash
# Ask a remote agent to check something
agent-bridge send server-wsl "Check disk space on /data"

# Ask another agent to run a command
agent-bridge send workstation-wsl "Run the test suite"
```

### Multi-Turn Conversation

```bash
# Start a session
agent-bridge send dev-wsl "Set up a new project" --no-wait

# Check sessions to get the ID
agent-bridge sessions --status running

# Send follow-up
agent-bridge send <session-id> "Now add the test framework"

# When done
agent-bridge end <session-id>
```

### Checking What's Running

```bash
# See all active sessions (CONTEXT column shows usage %)
agent-bridge sessions

# Get JSON for programmatic use
agent-bridge sessions --json
```

### Context Window Monitoring

The `CONTEXT` column in `agent-bridge sessions` shows token usage as a
fraction with percentage (e.g., `110k/200k (55%)`). Use this as a
progress indicator -- more tokens consumed generally means more work
completed.

For detailed usage on a specific session:

```bash
agent-bridge session-usage <session-id>
```

This shows the full usage snapshot: context size/used/percentage, model,
turn count, and a visual bar.

The REST API equivalent is `GET /api/v1/sessions/{id}/usage`.

### Context-Aware Handoff (Long-Running Sessions)

When managing a remote agent across many turns, the host agent should
monitor context usage and **proactively cycle the session** before the
remote agent exhausts its context window. This is the host's
responsibility -- the remote agent does not manage its own context
lifecycle.

**When to bail: ~70% context usage.** This leaves room for the handoff
prompt itself (which consumes context) and a safety margin before the
75%/90% warning thresholds fire.

**The handoff cycle:**

```bash
# 1. Check usage (do this every 2-3 turns on long-running sessions)
agent-bridge session-usage <session-id>

# 2. If context_pct >= 70, request a handoff from the remote agent
agent-bridge send <session-id> \
  "Your context window is filling up. Generate a continuation prompt
   for a fresh session to resume this work. Include:
   - Original objective
   - Progress so far (with file paths)
   - Remaining work
   - Key decisions and their rationale
   - Gotchas or failed approaches
   Keep it under 250 words. The new session will have full tool access."

# 3. Capture the response -- that IS the handoff payload

# 4. Stop the old session
agent-bridge stop <session-id>
# or: agent-bridge end <session-id>  (if no resume needed)

# 5. Start a new session with the handoff as the first prompt
agent-bridge send <agent-name> "Resume: <captured handoff payload>"
```

**Key points:**

- **No hooks or extensions required.** The host checks usage, makes the
  decision, sends the handoff request, and manages the session roll.
  The remote agent just answers a prompt.
- **The remote agent doesn't need to know** about context limits. It
  receives a normal prompt asking for a summary and responds normally.
- **Session roll preserves the worktree.** When starting the new session
  with the same agent name (and optionally the same `worktree_id` via
  the API), the new session lands in the same checkout with all prior
  commits available.
- **70% is the bail point, not 75%.** The 75% `context_warning` and
  90% `context_critical` SSE events are safety nets. If those fire,
  the handoff should already be in progress.

**Threshold reference:**

| Context % | Signal | Host action |
|-----------|--------|-------------|
| 0-50% | Normal | Continue sending work |
| 50-70% | Elevated | Monitor more frequently |
| 70% | **Bail point** | Request handoff, stop sending new work |
| 75% | `context_warning` SSE | Handoff should be in progress |
| 90% | `context_critical` SSE | Emergency -- do not send more prompts |

**Context % as a progress signal:** When listing sessions with
`agent-bridge sessions`, the CONTEXT column doubles as a rough progress
indicator. A session at 60% has done significant work. A session at 10%
is just getting started. Host agents can use this to prioritize which
sessions need attention, follow-up, or cycling.

## Agent Names

Agent names come from `acp-agents.json` in your project repo. Use
`agent-bridge agents` to list available agents.

Run `agent-bridge agents` to see the full list for your deployment.

## Remote Worktree Lifecycle

When agent-bridge spawns a session for an agent with `project` configured,
it creates a **new git worktree** on the target machine via
`agent-worktrees resolve --new`. The `agent-bridge end` command cleans up
the bridge session (subprocess, DB record) but does **not** finalize or
remove the spawned worktree. Without cleanup, these accumulate as orphaned
"unused" worktrees.

### Cleanup Responsibility

The **host agent** (the session that called `agent-bridge send`) is
responsible for cleaning up worktrees it caused to be created. During
the host session's wrap-up:

1. **End bridge sessions first.** Run `agent-bridge sessions` to find
   any active sessions. End each one with `agent-bridge end <id>`.

2. **Run worktree cleanup.** After ending bridge sessions, run:
   ```bash
   agent-worktrees worktrees cleanup
   ```
   This lists worktrees eligible for removal. The default (no flags)
   only removes worktrees that went through proper finalization --
   this is always safe to run with `--clean`.

3. **Report unused worktrees -- do not auto-purge.** The cleanup output
   may show "unused" worktrees (no commits, no uncommitted changes).
   Some of these may be bridge-spawned orphans; others may be
   intentional. **Do not run `--include-unused` automatically.**
   Instead, note any unused worktrees that appeared during this
   session's lifetime and ask the user whether to remove them.

4. **Proceed with host finalization.** After bridge cleanup, continue
   with the host session's own worktree finalization / sign-off flow.

### Remote (SSH) Agents

For worktrees spawned on a remote machine via SSH transport, cleanup
must run **on the target machine** where the worktree was created:

```bash
ssh <machine-alias> "agent-worktrees worktrees cleanup"
```

Use the same SSH alias that agent-bridge used for the session.

### Worktrees With Commits

If the remote agent made commits or has uncommitted changes, the
worktree is **not** unused -- it contains real work. Do not remove it.
Report the worktree path, branch, and status to the user for manual
review or normal worktree finalization.

### Future: Surgical Cleanup

Currently, worktree cleanup operates at the project level -- it cannot
distinguish bridge-spawned worktrees from user-created ones. A future
improvement will track the worktree ID in the bridge session metadata,
enabling targeted cleanup of only bridge-spawned orphans.

## Troubleshooting

- **"agent-bridge is not responding"** -- service isn't running. Start it
  with `agent-bridge start`.
- **"Agent not found"** -- check `agent-bridge agents` for available names.
  The topology config may not include the agent you're looking for.
- **Session stuck in RUNNING** -- the downstream agent may be waiting for
  permission or processing a long tool call. Check with
  `agent-bridge wait <session-id>` or `agent-bridge stop <session-id>`.
- **SSH connection failures** -- verify SSH aliases work:
  `ssh <machine-alias> echo ok`. Check `agent-bridge machines` for
  SSH readiness status.
