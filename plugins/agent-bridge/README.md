# Agent Bridge

Persistent inter-agent communication service for Copilot CLI. One
instance per machine, providing session management, SSE event streaming,
and agent subprocess spawning across local and remote machines.

Supports **Windows** and **Linux/WSL** (macOS planned).

## How It Works

Agent Bridge runs as a local HTTP service (`localhost:9280`) that manages
agent conversations on your behalf. Multiple Copilot CLI sessions can
start, stop, and resume conversations with agents running on any
configured machine via local subprocess or SSH transport.

Unlike agent-worktrees (a per-session plugin), agent-bridge is a
**persistent daemon** -- it runs continuously and survives session
restarts.

## Getting Started

See [Getting Started](docs/getting-started.md) for install, configuration,
and service startup.

## Docs

| Document | Description |
|----------|-------------|
| [Getting Started](docs/getting-started.md) | Install, configure, start the service |
| [Architecture](docs/architecture.md) | Service design, API reference, deployment |
| [Machine Configuration](docs/machine-config.md) | Topology setup -- machines.yaml, agents config |

## Skills

| Skill | Description |
|-------|-------------|
| `agent-bridge` | CLI control plane -- sessions, agents, machines, config |
| `copilot-extensions-setup` | Install and adopt (shared with agent-worktrees) |

## Platforms

| Platform | Service manager | Auto-start |
|----------|----------------|------------|
| Windows | Scheduled task | At-logon (15s delay) |
| Linux/WSL | systemd user unit | Enabled |
| macOS | Planned | -- |
