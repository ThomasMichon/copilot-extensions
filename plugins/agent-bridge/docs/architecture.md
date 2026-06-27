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

At most **one daemon may run per config dir + port**. On startup (`_cmd_start` in
`__main__.py`), before binding any port, the daemon takes an OS-level
**exclusive, non-blocking** lock (`singleton.py`). A second `agent-bridge start`
on the same port refuses cleanly and exits instead of spawning a duplicate
daemon -- duplicate daemons otherwise accumulate as zombies that re-bind the
service/relay ports and defeat restarts.

The lock is an OS byte-range lock (`fcntl.flock` on POSIX,
`msvcrt.locking` on Windows), so the kernel **releases it automatically when the
holder dies** (graceful exit, crash, kill, or power loss) -- there is never a
stale lock to detect or reclaim. It is keyed on the **config dir** and, for
callers that opt in, the **port**: the lock file is `<config_dir>/agent-bridge.lock`
by default, or `<config_dir>/agent-bridge.<port>.lock` when a port is supplied.
Port-keying lets an **active and a passive daemon coexist on one config dir**
(shared db/auth, different ports) during a [zero-downtime cutover](#zero-downtime-redeploy),
while two starts on the *same* port still collide. The primary daemon
(`~/.agent-bridge`) and the Windows elevated sub-daemon (`~/.agent-bridge/elevated`)
also have distinct config dirs, so each gets its own single instance.

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
GET    /health                           # Service health (no auth); reports {status, service, draining}
```

### Admin / Deployment

```
POST   /api/v1/drain                     # Open the drain gate; wait for busy sessions to settle
POST   /api/v1/undrain                   # Release the drain gate (cutover rollback)
POST   /api/v1/shutdown                  # Clean daemon shutdown (retires its own routing-table entry)
POST   /api/v1/relay/adopt               # Bind the credential relay (9857) on this daemon
POST   /api/v1/gc                        # Prune aged terminal/disconnected sessions
```

These back the zero-downtime redeploy flow -- see
[Zero-Downtime Redeploy](#zero-downtime-redeploy).

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
| `update` | **Drain-then-swap** by default (drain in-flight work, then reinstall + restart); opt-in **zero-downtime cutover** via `AGENT_BRIDGE_ZERO_DOWNTIME=1`. See [Zero-Downtime Redeploy](#zero-downtime-redeploy). |
| `start` | Start the service (`--passive` for a cutover spare -- see below) |
| `stop` | Stop the service |
| `status` | Show service status |
| `uninstall` | Remove service (`--remove-config` for config too) |

### Deploy Manifest

The installer writes `~/.agent-bridge/deploy-manifest.json` tracking:
- Schema version, installer type (plugin vs legacy)
- Source commit, branch, timestamp
- Plugin directory path

### Restart Behavior and What Survives

The daemon process model sets the floor for what a redeploy can preserve. **Each
session owns a live `copilot --acp` subprocess connected by stdin/stdout pipes to
the daemon.** The child leads its own process group, but the *pipes* are owned by
the daemon -- when the daemon exits the pipe breaks and the child dies. A live
pipe-connected subprocess cannot be serialized or handed to a successor process,
so a daemon exit inherently tears down every live child. Two things mitigate
this:

- **Idle / stopped sessions survive transparently.** Session metadata, turns,
  and events are persisted to SQLite, and on startup the daemon `_rehydrate()`s
  them: formerly-RUNNING sessions are marked STOPPED (resumable) and **lazily
  reattach** to their persisted ACP conversation via `load_session()` on next
  access. No work is lost for a parked session.
- **Active turns are drained, not killed (default path).** A plain `update`
  now **drains first** (see below): it opens the drain gate and waits for
  in-flight turns and active background sub-agents to settle before stopping the
  daemon, so an actively-streaming turn is no longer hard-killed up to the drain
  timeout. A *hard* kill (crash, `kill`, drain timeout without enough grace)
  still marks an in-flight turn `interrupted` -- history up to that point
  survives, the in-flight turn does not.

The remaining gap a plain drain-then-swap leaves is a brief **API-unavailable
window** while the old daemon stops and the new one binds the port. The
zero-downtime cutover path (opt-in) removes even that.

## Zero-Downtime Redeploy

A redeploy no longer has to hard-kill live work or strand clients on a dead
port. Three cooperating pieces make this work; all are **OS-agnostic and
app-level** (the effort's deliberate conclusion: systemd and Windows Scheduled
Tasks share almost no lifecycle surface, so the drain/handoff logic must not
live in the service manager). Design and validation are tracked in the
aperture-labs effort `agent-bridge-zero-downtime-deploy` (umbrella issue
[aperture-labs #1236](https://home.thomasmichon.com/gitea/tmichon/aperture-labs/issues/1236)).

### 1. Routing table (`<config_dir>/active.json`)

Clients resolve the daemon endpoint through a **routing table**
(`~/.agent-bridge/active.json`) instead of a static port, so a redeploy can
stand up a new daemon on a fresh port, flip the table atomically, and retire the
old daemon -- with no client ever dialing a dead port (`routing.py`).

- Records an `active` and (during an overlap) a `previous` endpoint, each with a
  monotonic `generation` counter. Writes are atomic (tmp + `os.replace`), so a
  concurrent reader never sees a torn file.
- **Backward compatible / self-healing.** When the table is absent the caller
  falls back to the static `config.yaml` port, so the table is inert until a
  daemon publishes itself. A reader that finds the `active` endpoint dead heals
  to `previous`, then to the config fallback (bounded by a 0.25s listener
  probe).
- A normal `start` self-publishes the table once it is listening; a
  `start --passive` instance stays **silent** (no self-route, no credential
  relay) until the cutover orchestrator promotes it.
- **Why a table, not a front proxy:** a proxy holding a stable port ships in the
  same plugin payload, so updating *it* reintroduces the very downtime it was
  meant to remove (and would need socket hand-off between proxy generations --
  the hardest-on-Windows part of a supervisor split). The table has no
  long-lived process to update: it is a file, re-read naturally by every
  short-lived CLI invocation.

### 2. Drain (the busy-oracle wait)

`agent-bridge drain [--timeout SECONDS] [--force]` (HTTP `POST /api/v1/drain`)
opens the **drain gate** -- the daemon immediately refuses *new* sessions and
*new* turns (`DaemonDrainingError`) -- then blocks until no session is **busy**,
bounded by `--timeout`. Busy is the dev57 **busy oracle**: a session that is
actively streaming a turn (RUNNING) **or** hosting active background sub-agents
(`has_active_background_tasks`). `--force` proceeds past the timeout, accepting
that the laggards are interrupted. `agent-bridge undrain` (`POST /api/v1/undrain`)
releases the gate (used by cutover rollback); `/health` reports `draining`.

### 3. Active/passive cutover (`agent-bridge deploy`)

`agent-bridge deploy [--drain-timeout SECONDS] [--force]` runs a reversible
cutover (`deploy.py`, `CutoverOrchestrator`):

1. pick a free port and spawn the new daemon `--passive` (no self-route, no
   relay);
2. wait until it is healthy;
3. **flip the routing table** -> new `active`, old demoted to `previous`;
4. **drain** the old daemon (busy-oracle wait, optional `--force`);
5. **-- commit point --** shut the old daemon down (a clean exit; it
   `clear_if_owner`s only its own route entry);
6. best-effort: adopt the credential relay (9857) on the new daemon.

Any failure **before** the commit point rolls back: re-publish the old endpoint
as active, undrain the old daemon, and terminate the freshly spawned passive. If
the route was already flipped and the old daemon is gone, the orchestrator
**commits forward** to the healthy new daemon rather than strand clients. The
[single-instance guard](#single-instance-guard) is **port-keyed** so an active
and a passive daemon can coexist on one config dir during the overlap (two starts
on the *same* port still collide).

### Installer wiring (both platforms)

The installer `update` path on **both** Linux/WSL (`install.sh`) and Windows
(`install.ps1`) chooses a strategy:

- **Default -- drain-then-swap:** drain in-flight work for a grace window
  (`AGENT_BRIDGE_DRAIN_TIMEOUT`, default 120s), then stop / reinstall / start.
  No active turn is hard-killed up to the drain timeout; a brief
  API-unavailable window remains.
- **Opt-in -- full cutover** (`AGENT_BRIDGE_ZERO_DOWNTIME=1`): leave the old
  daemon running, reinstall the venv, then `agent-bridge deploy` stands the new
  daemon up beside it and retires the old one -- **no** API-unavailable window
  and **no** hard-killed turns. **Experimental:** the survivor currently runs
  outside the service manager until service-manager reconciliation lands, so
  validate before relying on it; it falls back to stop/start on any failure.

### Still future: seamless mid-stream migration

Cutover **drains** in-flight turns (waits for them to finish on the old daemon)
rather than *migrating* a live, actively-streaming turn to the new daemon --
because a live pipe-connected `copilot --acp` child still cannot be handed to a
successor process (see [Restart Behavior](#restart-behavior-and-what-survives)).
Truly seamless mid-stream cutover would require splitting the daemon into a
stable **per-session supervisor** that owns the child over an **AF_UNIX socket**
and a **restartable frontend** that adopts supervisors on restart (and a rework
of the Windows kill-on-job-close so supervisors survive frontend exit). That
supervisor/broker split is **not implemented** and remains tracked in effort
[#1236](https://home.thomasmichon.com/gitea/tmichon/aperture-labs/issues/1236).

## Persistence

- **Sessions:** SQLite database at `~/.agent-bridge/sessions.db` (WAL mode)
- **Config:** YAML at `~/.agent-bridge/config.yaml`
- **Auth:** Bearer token at `~/.agent-bridge/auth.yaml`
- **Routing table:** `~/.agent-bridge/active.json` -- the client-facing
  active/previous endpoint table (see [Zero-Downtime Redeploy](#zero-downtime-redeploy));
  absent until a daemon publishes itself, atomically rewritten on each cutover.
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
