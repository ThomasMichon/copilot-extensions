---
name: agent-bridge
description: >
  Agent-bridge control plane -- manage inter-agent sessions, send prompts
  to remote agents, and check session status via CLI commands.
  Trigger phrases include: - 'agent-bridge' - 'remote agent' - 'send to agent' - 'agent session' - 'bridge session' - 'inter-agent' - 'cross-machine agent'
---

# Agent-Bridge Control Plane

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
