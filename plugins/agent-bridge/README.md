# Agent Bridge

Persistent inter-agent communication service for Copilot CLI. One instance
per machine, serving all Copilot CLI sessions with session management,
SSE event streaming, and agent subprocess spawning.

## Quick Start

```bash
# Install
pip install -e plugins/agent-bridge

# Start the service
agent-bridge start

# Check status
agent-bridge status
```

## What It Does

Agent Bridge runs as a local HTTP service (`localhost:9280`) that manages
agent conversations on your behalf:

- **Session lifecycle** -- start, stop, resume, and end agent sessions
- **Event streaming** -- SSE streams with durable event IDs for
  reconnect-safe consumption
- **Subprocess management** -- spawns `copilot --acp --stdio` processes
  and manages their lifecycle
- **Persistence** -- SQLite-backed session state survives service restarts

## API

All endpoints require `Authorization: Bearer <token>` (except `/health`).
The token is generated on first run and stored in `~/.agent-bridge/auth.yaml`.

### Session Management

```
POST   /api/v1/sessions                  # Start new session
GET    /api/v1/sessions                  # List sessions
GET    /api/v1/sessions/{id}             # Get session info
POST   /api/v1/sessions/{id}/turns       # Submit prompt
GET    /api/v1/sessions/{id}/events      # SSE event stream
POST   /api/v1/sessions/{id}/stop        # Stop (preserve state)
POST   /api/v1/sessions/{id}/resume      # Resume stopped session
DELETE /api/v1/sessions/{id}             # End (full cleanup)
```

### Health

```
GET    /health                           # Service health check
```

## Configuration

```yaml
# ~/.agent-bridge/config.yaml
port: 9280
bind: 127.0.0.1
db_path: ~/.agent-bridge/sessions.db
log_level: info

topologies:
  my-project:
    machines_yaml: /path/to/machines.yaml
    agents_config: /path/to/agents.json
```

## Architecture

```
Copilot CLI sessions (multiple)
  |
  |  HTTP (localhost:9280)
  v
+--------------------------------------------+
|  agent-bridge (persistent, one per machine) |
|  +--------------------------------------+  |
|  |  Session Manager                     |  |
|  |  - Lifecycle (start/stop/resume/end) |  |
|  |  - Turn tracking + event log         |  |
|  |  - SQLite persistence                |  |
|  +--------------------------------------+  |
|  |  Transport Layer                     |  |
|  |  - Local stdio spawn (Phase 1)      |  |
|  |  - SSH spawn (Phase 2)              |  |
|  +--------------------------------------+  |
+--------------------------------------------+
```

## Phases

- **Phase 1** (current): Service scaffold, local sessions, SQLite, SSE
- **Phase 2**: SSH transport, machine topology, connection pooling
- **Phase 3**: CLI tools, Copilot CLI integration, MCP shim
- **Phase 4**: Consumer migration (upstream agents, agent-worktrees)

## License

MIT
