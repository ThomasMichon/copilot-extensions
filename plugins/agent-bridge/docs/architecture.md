# Agent Bridge -- Architecture

## Service Design

Agent Bridge runs as a persistent HTTP service on `localhost:9280`. It
manages agent conversations across multiple Copilot CLI sessions,
spawning agent subprocesses locally or via SSH.

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
|  |  - SQLite persistence (WAL mode)     |  |
|  +--------------------------------------+  |
|  |  Transport Layer                     |  |
|  |  - Local stdio spawn                 |  |
|  |  - SSH spawn (remote machines)       |  |
|  +--------------------------------------+  |
+--------------------------------------------+
```

### Key Components

| Module | File | Purpose |
|--------|------|---------|
| FastAPI app | `app.py` | HTTP server, routing, auth middleware |
| Session manager | `session_manager.py` | Session lifecycle, turn tracking |
| Transport | `transport.py` | Local + SSH subprocess spawning |
| ACP agent | `acp_agent.py` | Upstream ACP agent interface (stdio mode) |
| ACP client | `acp_client.py` | Downstream ACP client (subprocess comms) |
| Events | `events.py` | SSE event log with durable IDs |
| Config | `config.py` | Config loading, topology management |
| Client | `client.py` | HTTP client for CLI commands |
| CLI | `__main__.py` | Command-line interface |

## Credential Relay

Agent-bridge starts a credential relay server during its FastAPI
lifespan in `app.py` by instantiating agent-codespaces'
`CredentialRelayServer`. The relay listens on port `9857` and proxies
requests to the local Git Credential Manager via agent-codespaces'
credential source integration.

For SSH-spawned agents, the transport layer reads per-machine
`auth.hooks` from `machines.yaml` and converts them into SSH reverse port
forwards plus environment variable exports. This makes the local relay
available inside remote agent sessions without separate relay setup.

The relay speaks the git credential protocol over TCP and supports the
standard `get`, `store`, and `erase` actions plus `get-access-token`,
which returns a raw ADO PAT for callers that need an access token.

## HTTP API

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
GET    /health                           # Service health (no auth required)
```

### Session States

```
STARTING --> IDLE <--> RUNNING --> STOPPED --> ENDED
                                     |
                                     +--> ENDED
```

- **STARTING** -- subprocess launching
- **IDLE** -- waiting for prompts
- **RUNNING** -- processing a turn
- **STOPPED** -- paused, state preserved
- **ENDED** -- cleanup complete

## ACP Agent Mode

Agent-bridge can also run as a stdio ACP agent (not HTTP):

```bash
agent-bridge agent --agent my-agent
```

This presents agent-bridge as an ACP-compatible agent for chat UIs that
connect via ACP protocol directly. The bridge routes prompts to the named
downstream agent.

## Deployment

### Platform-Specific Service Management

| Platform | Service manager | Install location | Config |
|----------|----------------|-----------------|--------|
| Windows | Scheduled task + PID | `~/.agent-bridge/` | At-logon, 15s delay |
| Linux/WSL | systemd user unit | `~/.agent-bridge/` | `~/.config/systemd/user/` |
| macOS | Planned | -- | -- |

### Installer Actions

| Action | Description |
|--------|-------------|
| `install` | Full deploy: venv, package, binstub, service, manifest |
| `update` | Reinstall package, restart if running |
| `start` | Start the service |
| `stop` | Stop the service |
| `status` | Show service status |
| `uninstall` | Remove service (`--remove-config` for config too) |

### Deploy Manifest

The installer writes `~/.agent-bridge/deploy-manifest.json` tracking:
- Schema version, installer type (plugin vs legacy)
- Source commit, branch, timestamp
- Plugin directory path

## Persistence

- **Sessions:** SQLite database at `~/.agent-bridge/sessions.db` (WAL mode)
- **Config:** YAML at `~/.agent-bridge/config.yaml`
- **Auth:** Bearer token at `~/.agent-bridge/auth.yaml`
- **Logs:** Structured logging to stderr (captured by service manager)

## Development Phases

- **Phase 1** (complete): Service scaffold, local sessions, SQLite, SSE
- **Phase 2** (complete): SSH transport, machine topology, connection pooling
- **Phase 3** (in progress): CLI tools, Copilot CLI integration, namespace resolvers
- **Phase 4**: Consumer migration (upstream agents, agent-worktrees)

## Namespace Resolvers

Agent-bridge supports **namespace resolvers** for prefixed agent names
(e.g. `codespace:my-cs`, `admin:local-agent`). When a colon appears in
an agent name, the prefix is looked up in the namespace registry and
resolution is delegated to the matching resolver.

### Architecture

```
agent name: "codespace:my-cs"
              |          |
              v          v
         prefix       bare name
              |
              v
    NamespaceResolver (ABC)
    +-- CodespaceResolver    (agent-codespaces package)
    +-- AdminResolver        (built-in)
```

### Registered Resolvers

| Prefix | Resolver | Source | Description |
|--------|----------|--------|-------------|
| `codespace:` | `CodespaceResolver` | `agent-codespaces` package | Queries `gh codespace list`, builds SpawnTargets via `agent-codespaces ssh --stdio` |
| `admin:` | `AdminResolver` | Built-in (`admin_resolver.py`) | Wraps local agents in elevation (gsudo / sudo -A) |

### NamespaceResolver Interface

```python
class NamespaceResolver(ABC):
    @property
    def prefix(self) -> str: ...
    async def resolve(self, name: str) -> SpawnTarget: ...
    async def list(self) -> list[NamespaceAgentInfo]: ...
    async def ensure_ready(self, name: str) -> None: ...  # optional
```

### Registration

Resolvers are auto-discovered and registered at startup by
`_register_namespace_resolvers()` in `agent_registry.py`. Import
failures are gracefully skipped (resolvers are optional extensions).

The installer installs sibling plugin packages (e.g. `agent-codespaces`)
into the agent-bridge venv to make their resolvers importable.
