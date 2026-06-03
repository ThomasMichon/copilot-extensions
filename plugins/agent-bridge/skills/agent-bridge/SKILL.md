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
  - 'talk to borealis'
  - 'talk to wheatley'
  - 'talk to lambda-core'
  - 'bridge to'
  - 'cross-machine'
  - 'facility agent'
  - 'external agent'
  - 'inter-agent'
  - 'relay to'
  - 'send to borealis'
  - 'send to wheatley'
  - 'send to lambda-core'
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
"communicate with" a known facility machine or agent name (wheatley,
borealis, lambda-core, borealis-wsl, lambda-core-wsl, etc.), **ALWAYS
use `agent-bridge send <agent-name> "prompt"`**. Never use the Task
tool for cross-machine communication -- it cannot reach other machines.

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
# List all sessions
agent-bridge sessions
agent-bridge sessions --status idle

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

```bash
# Start the service (uses platform default port: 9280 Windows, 9281 Linux/WSL)
agent-bridge start
agent-bridge start --port 9280 --bind 127.0.0.1  # explicit override

# Check service health
agent-bridge status

# Print version
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
# See all active sessions
agent-bridge sessions

# Get JSON for programmatic use
agent-bridge sessions --json
```

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
   aperture-labs worktrees cleanup
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
ssh <machine-alias> "aperture-labs worktrees cleanup"
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
