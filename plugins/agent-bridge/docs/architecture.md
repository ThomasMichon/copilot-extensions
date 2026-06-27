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
| Single-instance guard | `singleton.py` | OS-level lock: one daemon per config dir |
| Elevated sub-daemon | `elevated.py` | Windows admin sub-daemon launcher (port 9281) |
| CLI | `__main__.py` | Command-line interface |

## Single-Instance Guard

At most **one daemon may run per config dir**. On startup (`_cmd_start` in
`__main__.py`), before binding any port, the daemon takes an OS-level
**exclusive, non-blocking** lock on `<config_dir>/agent-bridge.lock`
(`singleton.py`). A second `agent-bridge start` for the same config dir refuses
cleanly and exits instead of spawning a duplicate daemon -- duplicate daemons
otherwise accumulate as zombies that re-bind the service/relay ports and defeat
restarts.

The lock is an OS byte-range lock (`fcntl.flock` on POSIX,
`msvcrt.locking` on Windows), so the kernel **releases it automatically when the
holder dies** (graceful exit, crash, kill, or power loss) -- there is never a
stale lock to detect or reclaim. It is keyed on the **config dir**, not the
plugin/venv folder: the primary daemon (`~/.agent-bridge`) and the Windows
elevated sub-daemon (`~/.agent-bridge/elevated`) have distinct config dirs, so
each gets its own single instance while two *primaries* can never coexist.

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

**Single owner of port 9857.** Only the **primary** daemon hosts the relay. The
Windows elevated sub-daemon sets `enable_credential_relay: false` in its seeded
config (`elevated.py` -> `_seed_config`), so it never re-binds -- and thus never
evicts -- the primary's relay; local elevated agents reuse the primary's relay on
the same host. The `enable_credential_relay` config flag (default `true`) gates
relay startup in the `app.py` lifespan.

## HTTP API

All endpoints require `Authorization: Bearer <token>` (except `/health`).
The token is generated on first run and stored in `~/.agent-bridge/auth.yaml`.

### Session Management

```
POST   /api/v1/sessions                  # Start new session
GET    /api/v1/sessions                  # List sessions
GET    /api/v1/sessions/{id}             # Get session info
POST   /api/v1/sessions/{id}/turns       # Submit prompt
GET    /api/v1/sessions/{id}/events      # SSE event stream (resume from cursor)
GET    /api/v1/sessions/{id}/events/range # Random-access read by event id range
GET    /api/v1/sessions/{id}/cursor      # Read caller's delivery cursor
POST   /api/v1/sessions/{id}/cursor      # Ack delivery (advance cursor)
POST   /api/v1/sessions/{id}/stop        # Stop (preserve state)
POST   /api/v1/sessions/{id}/resume      # Resume stopped session
DELETE /api/v1/sessions/{id}             # End (full cleanup)
```

The SSE stream (`/events`) resumes from the caller's last-acked **delivery
cursor** when `after` is omitted and `caller_id` is supplied; pass an explicit
`?after=<id>` for a fixed start point. The cursor advances only via `POST
/cursor` acks (confirmed delivery), never from server-side production -- so an
ungraceful client death never skips output. `/events/range` is the only way to
re-read already-consumed content and never moves the cursor. See
[Streaming & the delivery cursor](#) in the README for the consumer model.

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

### Restart Behavior Today (and What Survives)

A version update is a **hard restart**: `update` reinstalls the package and
bounces the service (systemd `restart` on Linux/WSL; stop + at-logon relaunch on
Windows). The daemon process is replaced, which has three consequences:

- **Idle / stopped sessions survive transparently.** Session metadata, turns,
  and events are persisted to SQLite, and on startup the daemon `_rehydrate()`s
  them: formerly-RUNNING sessions are marked STOPPED (resumable) and **lazily
  reattach** to their persisted ACP conversation via `load_session()` on next
  access. No work is lost for a parked session.
- **Actively-streaming turns are lost.** A turn that is mid-flight when the
  daemon dies is marked `interrupted`; history up to that point survives, the
  in-flight turn does not.
- **The HTTP API is connection-refused for the swap window.** Callers
  (`send`/`wait`/`read`) see a dropped connection until the new daemon binds the
  port.

The core obstacle to doing better: **each session owns a live `copilot --acp`
subprocess connected by stdin/stdout pipes to the daemon.** The child leads its
own process group, but the *pipes* are owned by the daemon -- when the daemon
exits the pipe breaks and the child dies. A live pipe-connected subprocess
cannot be serialized or handed to a successor process, so a restart inherently
tears down every live child.

## Zero-Downtime Deployment (Roadmap)

> **Status: planned, not yet implemented.** The flow below is the *designed*
> evolution of the restart behavior above. The full plan, validation, and
> cross-platform adapters are tracked in the aperture-labs effort
> `agent-bridge-zero-downtime-deploy` (umbrella issue
> [aperture-labs #1236](https://home.thomasmichon.com/gitea/tmichon/aperture-labs/issues/1236)).

The goal is **active/passive deployment** with no observable loss to clients or
in-flight conversations. Because the OS service models diverge sharply
(systemd offers `ExecReload` / `Type=notify` / socket activation; Windows offers
none of these and *additionally* force-kills children on daemon exit via the
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` job object), the drain/handoff
orchestration is designed to live **in-process and OS-agnostic**, with thin
per-OS adapters only for socket handoff and the Windows job-breakaway. It builds
on a primitive already present in the daemon: the **busy oracle**
(`has_active_background_tasks` / `SessionBusyError`), which already knows when a
session is mid-work and must not be torn down.

The flow is delivered in three tiers of increasing completeness:

| Tier | Flow | Outcome |
|------|------|---------|
| **1 -- Graceful drain** | A `drain` command stops accepting new turns/sessions, **waits on the busy oracle** until live turns settle (bounded timeout + `force` override), checkpoints to SQLite, then exits. The listen socket is held across the swap (systemd socket activation on Linux; a small front proxy on Windows). | Not yet zero-downtime, but **no lost active turns**; clients see latency, not connection-refused. |
| **2 -- Active/passive failover** | A passive daemon starts on a **distinct config dir + port** (the config-dir singleton lock permits this -- see [Single-Instance Guard](#single-instance-guard)). A DB-ownership lease hands writes from old to new; the new daemon re-spawns children and `load_session()`s the drained sessions; a front proxy flips to the new port; the old daemon retires. Relayed cross-machine sessions (relay port) re-home without the remote caller noticing. | Idle sessions seamless across cutover; only a genuinely mid-stream turn is lost. |
| **3 -- Supervisor/broker split** | The daemon is split into a stable **per-session supervisor** that owns the `copilot --acp` child over an **AF_UNIX socket** (not a pipe), and a **restartable frontend** that serves the API and orchestrates. On restart the new frontend *adopts* the existing supervisors over their sockets. Requires reworking the Windows job object so supervisors **survive** frontend exit (own job / `CREATE_BREAKAWAY_FROM_JOB`). | **True zero-downtime**: children never die, turns never interrupt, even mid-stream. |

Each tier is validated with a "no work lost" proof on **both** platforms
(Linux/WSL systemd and Windows Scheduled Task + Job Object). Tier 1 is the
recommended first delivery -- it is cheap, reuses the busy oracle, and converts
"a redeploy kills active work" into "a redeploy waits for active work to
settle."

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
    +-- ContainerResolver    (agent-containers package)
    +-- AdminResolver        (built-in)
```

### Registered Resolvers

| Prefix | Resolver | Source | Description |
|--------|----------|--------|-------------|
| `codespace:` | `CodespaceResolver` | `agent-codespaces` package | Queries `gh codespace list`, builds SpawnTargets via `agent-codespaces ssh --stdio` |
| `container:` | `ContainerResolver` | `agent-containers` package | Queries `docker ps`, builds SpawnTargets via `docker exec -i` into local dev containers (GH_TOKEN forwarded by name) |
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
