# Agent Bridge -- Architecture

## Service Design

Agent Bridge runs as a persistent HTTP service on a loopback port. By default
it binds an **OS-assigned ephemeral** port and advertises the actual port
through its routing table (`active.json`), so nothing well-known is reserved
(dotfiles #694); clients discover it (`agent-bridge status` prints it). It
manages agent conversations across multiple Copilot CLI sessions,
spawning agent subprocesses locally or via SSH.

```
Copilot CLI sessions (multiple)
  |
  |  HTTP (loopback, discovered port)
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
| SSH carrier | `carrier.py` + vendored `ssh-manager` | One bounded, reconnecting framed stdio carrier per normalized SSH connection identity |
| ACP agent | `acp_agent.py` | Upstream ACP agent interface (stdio mode) |
| ACP client | `acp_client.py` | Downstream ACP client (subprocess comms) |
| Events | `events.py` | SSE event log with durable IDs; content-free session, conversation, and tool-call telemetry reduction. Owned and represented sources are labeled; represented turn completion supplies its terminal idle boundary. |
| Config | `config.py` | Config loading, topology management |
| Client | `client.py` | HTTP client for CLI commands |
| Single-instance guard | `singleton.py` | OS-level lock: one daemon per config dir |
| Elevated sub-daemon | `elevated.py` | Windows admin sub-daemon launcher (ephemeral loopback port, discovered via its routing table) |
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

Agent-bridge owns and hosts the shared credential relay during its FastAPI
lifespan (`app.py`). It builds a `credential_relay.RelayBuilder` and lets
optional provider plugins contribute sources/policy through `relay-profile`
CLI seams (`agent_registry.py` -> `register_credential_sources`). That process
boundary is the primary path: sibling runtimes keep ownership of their own
binstubs and venvs, and a sibling fix reaches the bridge without vendoring the
sibling into the bridge venv. A legacy in-process `register_relay` import remains
only as a degrade-safe fallback.

If no provider contributes any sources, the relay is disabled and the bridge
still starts. If sources exist, the relay binds the provider-requested port
(`0` means an OS-assigned loopback port) or the relay library's default fallback,
then publishes the live port through `relay_state` so transports discover the
actual endpoint.

For SSH-spawned agents, the transport layer reads per-machine
`auth.hooks` from `machines.yaml` and converts them into SSH reverse port
forwards plus environment variable exports. This makes the local relay
available inside remote agent sessions without separate relay setup.

Stopped trusted-container sessions have a stricter resume boundary. The bridge
re-resolves the provider target, replaces any surviving reverse-forward against
the current live relay port, and verifies the container-side loopback listener
before ACP readiness or prompt delivery. A relay-enabled resume fails explicitly
when that readiness cannot be proven; fleets with relay disabled skip the gate.
This resume contract is separate from broader relay port-stability policy.

The relay speaks the git credential protocol over TCP and supports the standard
`get`/`fill`, `store`/`approve`, and `erase`/`reject` shapes plus token actions
such as `get-github-token`, `get-azure-token`, and `get-access-token`; provider
profiles decide which sources and token gates are enabled.

**Single owner of the credential relay.** Only the **primary** daemon hosts the relay. The
Windows elevated sub-daemon sets `enable_credential_relay: false` in its seeded
config (`elevated.py` -> `_seed_config`), so it never re-binds -- and thus never
evicts -- the primary's relay; local elevated agents reuse the primary's relay on
the same host. The `enable_credential_relay` config flag (default `true`) gates
relay startup in the `app.py` lifespan.

## Persistent SSH Carrier Foundation

Agent Bridge owns a persistent SSH carrier pool through the same
`ssh-manager.ConnectionManager` that owns ordinary SSH connections. A carrier
is keyed by the complete normalized SSH connection identity, not a display
alias, and opens exactly one long-lived `agent-bridge carrier --stdio` process
through `open_stdio_channel`. This gives native Windows and POSIX hosts the same
application-level multiplexing contract without depending on ControlMaster.

The versioned protocol uses bounded, length-prefixed frames for hello, request,
response, event, heartbeat, cancellation, and error envelopes. It supports
concurrent request IDs and replayable subscriptions, detects heartbeat and
subscription staleness, reconnects with bounded backoff, bounds output queues,
retires after an idle interval with no logical clients, and closes stdin before
reaping the SSH process tree.

The operation contract proxies exact session status, session-or-worktree live
resolution, cursor-based events, session create/stop/end, and represented live
message delivery. The remote carrier endpoint authenticates to its host-local
Bridge through the existing discovered HTTP endpoint and bearer-token flow,
then calls the same session, live-message, event, and cursor authorities used by
direct clients. Mutating requests are version-gated separately from the original
read/event contract and return structured results instead of CLI preambles. It
does not import Agent Dispatch, copy session state, or maintain a second event
log.

The caller supplies a required stable `caller_id`; no anonymous/default cursor
is used for remote subscriptions. Actual event IDs, names, payloads, and
timestamps are forwarded unchanged. The local proxy acknowledges the hosting
Bridge only after its own authenticated API/CLI consumer accepts delivery, so a
carrier reconnect reopens from the durable hosting cursor. A replacement local
daemon can do the same after zero-downtime cutover. Event-log rebuilds persist a
per-caller invalidation marker, while continuity changes and non-contiguous
event IDs terminate the stream with an explicit `bridge_control` /
`full_reconcile` signal rather than an empty result. Unsupported operation
versions are rejected before a remote HTTP request or subscription is opened.
An aggregate `POST /api/v1/remote/events` surface accepts a bounded set of exact
host/session/caller identities and returns one SSE connection. Each logical
subscription retains its own hosting cursor and carrier lease, while events and
control signals carry their subscription identity in the envelope. Replacing
the set means replacing this one local stream; it never creates one local HTTP
connection per observed session. Carrier heartbeats and tool-progress envelopes
become SSE comments so ongoing remote activity keeps the aggregate connection
and its local consumer healthy without creating reconciliation wakes.
Agent Dispatch uses the authenticated local remote-operation API for fleet
create, status/activity, end, worktree resolution, and queued prompt delivery.
A raw SSH Bridge command remains only when the local daemon or required HTTP
generation is absent; a carrier operation failure never starts parallel direct
outreach.

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

GET    /api/v1/remote/{host}/sessions/{id}/status
GET    /api/v1/remote/{host}/live-sessions/{id}
GET    /api/v1/remote/{host}/sessions/{id}/events
POST   /api/v1/remote/events             # Multiplex several remote subscriptions
POST   /api/v1/remote/{host}/sessions/{id}/cursor
```

The SSE stream (`/events`) resumes from the caller's last-acked **delivery
cursor** when `after` is omitted and `caller_id` is supplied; pass an explicit
`?after=<id>` for a fixed start point. The cursor advances only via `POST
/cursor` acks (confirmed delivery), never from server-side production -- so an
ungraceful client death never skips output. `/events/range` is the only way to
re-read already-consumed content and never moves the cursor. See
[Streaming & the delivery cursor](../README.md#streaming--the-delivery-cursor)
in the README for the consumer model.

The `/remote` endpoints are authenticated local proxy surfaces. Clients name a
topology host and exact hosting-Bridge session, never SSH arguments. Each remote
event caller must provide a distinct stable `caller_id`; response headers expose
the accepted cursor and event-log continuity, and cursor acknowledgements carry
that continuity back. `bridge_control` SSE events have no durable event ID and
request full reconciliation after cursor invalidation or a replay gap.
The aggregate endpoint emits `bridge_event` envelopes containing the exact
`host`, `session_id`, `caller_id`, durable `event_id`, event name, continuity,
timestamp, and payload. Any subscription-level gap or carrier failure emits one
identified `bridge_control` envelope so the consumer can run a full
reconciliation pass, acknowledge the identified subscription's authoritative
head and continuity when supplied, and reconnect the whole set from durable
cursors. Initialization-time subscription failures use the same identified SSE
control envelope instead of changing the aggregate response shape.

### Health

```
GET    /health                           # Service health (no auth); includes aggregate ssh_carriers health/counts, never payloads or SSH details
```

### Admin / Deployment

```
POST   /api/v1/drain                     # Open the drain gate; wait for busy sessions to settle
POST   /api/v1/undrain                   # Release the drain gate (cutover rollback)
POST   /api/v1/shutdown                  # Clean daemon shutdown (retires its own routing-table entry)
POST   /api/v1/relay/adopt               # Bind/adopt the provider-configured credential relay on this daemon
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

These runtime states are not the complete caller-facing delegation model.
Logical delegate identity, caller attachment, transport liveness, and
caller-attention reasons are separate axes. Their current mapping and target
semantics are defined in
[Delegated-agent contract](delegation-contract.md).

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
| `stamp` | Fast first-install path: snapshot the payload and write the self-provisioning binstub; defers venv/service work to first use. |
| `provision` | First-use path invoked by the binstub when no runtime slot exists; equivalent to a full install from the stamped snapshot. When a runtime already exists, it reconciles only the Windows scheduled task without rebuilding the slot or restarting the daemon. A never-ran S4U task automatically enters the self-elevating repair path; routine `update` never rewrites it. |
| `install` | Full deploy: versioned venv slot, package/libs, binstub, service, manifest |
| `update` | Build/verify a new versioned slot, activate it, then perform installer-driven graceful cutover when a daemon is live; falls back to drain/stop/start on failure. |
| `start` | Start the service (`--passive` for a cutover spare -- see below) |
| `stop` | Stop the service |
| `status` | Show service status |
| `uninstall` | Remove service (`--remove-config` for config too) |

### Scheduled task: write-once bootstrap (decoupled from the runtime version)

On Windows the auto-start scheduled task is **write-once bootstrap
infrastructure**, deliberately decoupled from the runtime version so routine
updates rarely — in practice never — touch it:

- **The task's action is version-stable.** It launches the stable
  `~/.agent-bridge/start-agent-bridge.ps1` supervisor, which resolves the live
  runtime from the `current-version` marker at boot. A version cutover only
  rewrites the marker (and, if its content changed, the supervisor file) — both
  plain-file writes that need no elevation. The task's action/trigger/principal
  are byte-identical across every deploy.
- **`update` never rewrites an existing task.** The routine update path calls
  `Ensure-ScheduledTask`, which *creates* the task only when it is **absent**
  (first install / after a manual removal, in the default non-elevated
  interactive AtLogOn mode) and otherwise leaves it **entirely untouched** —
  adopting whatever mode it already has, with zero Task Scheduler writes. This is
  what keeps a routine `update` from needing elevation and from churning or
  breaking a working auto-start (an S4U/boot task can only be modified with
  elevation; a failed rewrite used to purge a healthy task).
- **Mode changes and repair are explicit and elevation-aware.** Switching
  interactive ⇄ headless S4U, or repairing a broken/never-ran task, happens only
  when an operator runs the self-elevating **`scripts/repair-scheduled-task.ps1`**
  (it raises its own UAC prompt, removes the stale task, and registers the clean
  interactive task — reusing the existing action verbatim, and deliberately *not*
  starting an elevated daemon) or `install.ps1 provision` — which invokes that
  repair automatically for a never-ran S4U task and otherwise performs ordinary
  task reconciliation without touching the runtime or daemon. This never happens
  silently during a version update. If a routine step ever does need such a
  change it leaves the existing task intact and prints the one command to run.
- **Meanwhile the daemon self-heals** regardless of the task: any daemon-touching
  CLI command boots a down daemon on demand (persistence-correct detached
  spawn), and `service`/restart fall back to a direct spawn when a task can't be
  run on demand. So auto-start-at-logon is a convenience, not a dependency.

### Deploy Manifest

The installer writes `~/.agent-bridge/deploy-manifest.json` tracking:
- Schema version, installer type (plugin vs legacy)
- Source commit, branch, timestamp
- Plugin directory path

### Restart Behavior and What Survives

The current process model uses a **Session Host** for owned ACP sessions. The
Session Host is the stable process that owns the `copilot --acp` child and its
stdio pipes; the agent-bridge daemon is a frontend that attaches to the host over
a reattachable loopback protocol (`session_host/`). On Windows the host breaks
away from the daemon's job; on POSIX it runs in its own session, so a daemon
restart does not inherently close the child's pipes.

- **Idle / stopped sessions survive transparently.** Session metadata, turns,
  events, and host connection data are persisted to SQLite/host state. On startup
  the daemon reattaches to compatible surviving Session Hosts; otherwise it can
  lazily resume from persisted Copilot state.
- **Active turns are preserved across frontend restarts when the Session Host
  survives.** A streaming `send`/`read`/`wait` reconnects through the routing
  table and resumes from the caller's acked delivery cursor. If a host is
  incompatible with the newly deployed frontend, version-mux leaves a live child
  stranded until it reaches its own stop rather than killing it mid-turn.
- **Drain is still the safe cutover boundary.** The installer-driven cutover
  opens the drain gate on the old daemon so no new work enters it, then retires
  it once busy sessions have settled (or force proceeds when explicitly told to).
- **Destructive recovery is executable and target-scoped.**
  `agent-bridge parity <container:...|codespace:...> --fault
  frontend-restart-hostindex-loss` stops the local frontend, removes only the
  harness-created session's local HostIndex row, restarts the frontend, and
  requires recovery from the far-side authority record to preserve the same
  Session Host PID, ACP child PID, and ACP session id through a second turn. The
  mode is explicit and refuses to run while another managed session is active.
  The companion `--fault relay-interruption` mode requires the auth probe,
  interrupts only that idle parity session's supervised credential-relay
  process, waits for the same owner handle to establish a replacement process,
  rejects duplicate ownership, and reruns the boolean-only credential consumers
  before the final continuity turn. The `--fault failed-acp-handshake` mode
  substitutes a deterministic JSON-RPC handshake rejection for one harness-only
  remote launch, then requires identity-checked Host/child death, removal of the
  far-side authority record, provider launch cleanup, local forward/relay/index/
  lock cleanup, and durable session removal before a normal launch may reacquire
  the same target. Inconclusive remote or ownership cleanup is fail-closed: the
  failed session and target ownership remain in place to block a duplicate.
  The container-only `--fault container-recreate` mode asks the provider to
  remove exactly the identity-checked trusted container instance and recreate
  its deterministic target name. The bridge transfers the existing target lock
  to the replacement launch without an unlocked window, treats the confirmed
  instance change as authoritative death of the old Host, and retires the old
  session/index/forward/relay only after the replacement reaches idle with a
  fresh Host, child, and ACP session. Provider, authority, or replacement-launch
  uncertainty keeps a durable failed owner and refuses a duplicate.

## Zero-Downtime Redeploy

A redeploy no longer has to hard-kill live work or strand clients on a dead
port. Three cooperating pieces make this work; all are **OS-agnostic and
app-level** (systemd and Windows Scheduled Tasks share almost no lifecycle
surface, so the drain/handoff logic does not live in the service manager). This
is the plugin's implementation of the repo-level
[`graceful-daemon-cutover`](../../../docs/patterns/graceful-daemon-cutover.md)
pattern.

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
releases the gate (used by cutover rollback).

**Teardown is never gated by the drain flag.** `stop`/`end` on a session stay
permitted while draining -- teardown is exactly the operation the drain waits
for, so gating it would self-deadlock a redeploy (the operator could not clear
the very sessions blocking the drain). The gate blocks only *new* work
(create/turn).

**Drain observability + bounded lifetime.** Opening and releasing the gate are
logged with a `source`/`reason`, and `/health` exposes a `drain` block (`since`,
`held_s`, `reason`, `source`, `auto_release_at`) whenever `draining` is true, so
a stuck drain is visible to monitoring without grepping logs. A drain has a
**bounded lifetime**: a watchdog WARNs on an interval while the gate is open and
**auto-releases** it after `SessionManager.DRAIN_AUTO_RELEASE_S` (default 900s)
if no cutover ever retires the daemon -- so an aborted cutover (or a diagnosis
session that is itself 503'd by the gate it is investigating) self-heals instead
of returning 503 forever.

### 3. Active/passive cutover (internal seam -- `agent-bridge deploy`, not an operator command)

> **Deploy via the normal plugin update flow, not this seam.** To ship an
> agent-bridge build, refresh the plugin payload (`copilot plugin update
> agent-bridge`) and let the plugin's own installer reconcile cut the daemon over
> (`scripts/install.sh update`, run by the host's plugin-update integration or the
> plugin's sessionStart hook). `agent-bridge deploy` is an **internal** cutover
> seam the installer drives; running it by hand is **not** a recommended operator
> mechanism. It stays exposed only for installer internals and `--recover`.
>
> Note the normal flow must reliably (a) restart the running daemon into the new
> version -- `copilot plugin update` refreshes the payload but does not itself
> restart the daemon -- and (b) key the plugin version off `plugin.json` (a
> `pyproject.toml`-only bump does not advance the marketplace catalog). Keep the
> two version fields in lockstep.

`agent-bridge deploy [--drain-timeout SECONDS] [--force]` is an internal seam
the installer invokes during `update`/activation when a live daemon exists. It
runs a reversible cutover (`zdd.cutover.CutoverOrchestrator`):

1. pick a free port and spawn the new daemon `--passive` (no self-route, no
   relay);
2. wait until it is healthy;
3. **flip the routing table** -> new `active`, old demoted to `previous`;
4. **drain** the old daemon (busy-oracle wait, optional `--force`);
5. **-- commit point --** shut the old daemon down (a clean exit; it
   `clear_if_owner`s only its own route entry);
6. best-effort: adopt the credential relay (ephemeral) on the new daemon.

Any failure **before** the commit point rolls back: re-publish the old endpoint
as active, undrain the old daemon, and terminate the freshly spawned passive. If
the route was already flipped and the old daemon is gone, the orchestrator
**commits forward** to the healthy new daemon rather than strand clients. The
[single-instance guard](#single-instance-guard) is **port-keyed** so an active
and a passive daemon can coexist on one config dir during the overlap (two starts
on the *same* port still collide).

**Durable breadcrumb + stale-cutover recovery.** The orchestrator runs in the
short-lived `agent-bridge deploy` process, separate from the daemons it drives.
It writes a durable **breadcrumb** (`<config_dir>/cutover.json`) *before* it
touches the old daemon's drain gate and advances its state at each phase
(`started` -> `flipped` -> `draining` -> `committed`/`rolled_back`), clearing it
on a clean cutover. If the deploy process dies mid-cutover, the breadcrumb is
left in a non-terminal state -- an attributable trace tying a drained survivor
to the cutover that drained it. `agent-bridge deploy` heals such a stale
breadcrumb on its next run (undraining the stranded survivor); `agent-bridge
deploy --recover` runs *only* that heal and exits. Combined with the drain
watchdog (#1757), a stranded survivor self-heals even if no deploy is re-run.

**Windows listener recovery.** A Proactor accept-socket failure can leave the
uvicorn process alive while its loopback listener no longer serves. The
independent health watchdog treats a sustained Windows failure as terminal
after a short bounded grace, schedules a detached replacement from the same
versioned runtime with the original `start` flags, and then hard-exits the
wedged frontend to release the singleton lock. Session Hosts remain independent
and the replacement frontend reattaches them from the durable ledger; recovery
does not wait for a later CLI command to notice the dead endpoint.

### Installer wiring (both platforms)

The installer `update` path on **both** Linux/WSL (`install.sh`) and Windows
(`install.ps1`) now uses graceful cutover by default whenever a live daemon is
running and the new versioned slot differs from the active slot:

- Build and verify the new slot while the old daemon keeps serving.
- Atomically activate the new slot (`current-version` / stable `venv` link).
- Invoke the internal `agent-bridge deploy` seam to start the new daemon
  passive, flip the routing table, drain the old daemon, and retire it.
- If cutover cannot run or fails, fall back to drain/stop/start. The drain grace
  is controlled by `AGENT_BRIDGE_DRAIN_TIMEOUT` (120s default on the classic
  path; 300s default for the deploy seam).

`AGENT_BRIDGE_ZERO_DOWNTIME` is still accepted by installers for compatibility
but no longer enables a separate mode.

**Redeploying the daemon that hosts your own driving session — prefer a detached
installer.** A Session Host lets the Copilot child survive a frontend restart,
but the installer is still changing the service that carries its own control
path and may fall back to classic drain/stop/start. For manual updates from a
bridge-hosted agent, run the installer detached and read progress from a logfile:

```bash
setsid bash -c 'bash plugins/agent-bridge/scripts/install.sh update \
  > ~/.agent-bridge/deploy.log 2>&1; echo "EXIT=$?" >> ~/.agent-bridge/deploy.log' \
  < /dev/null > /dev/null 2>&1 &
# then poll ~/.agent-bridge/deploy.log; the hosted session survives the cutover
# (Session-Host keeps the child alive) and the manifest shows the new version.
```

Sessions themselves survive the restart (the Session Host keeps the `copilot`
child alive across the daemon swap — see
[Restart Behavior](#restart-behavior-and-what-survives)); the detach only
protects the *installer* from being coupled to the very service it is updating.

**Verifying a redeploy: the manifest version is not proof the daemon runs it.**
The deploy manifest (`~/.agent-bridge/deploy-manifest.json`) and the versioned
runtime slot (`versions/<v>/`, published by the `current-version` marker) are written when
the *files* land — which can happen without the running **process** being
restarted (e.g. a marketplace auto-sync that swaps files but defers the
restart, or a `stop`/`start` where the old process had already exited). A daemon
launched *before* the file swap keeps executing the old binary while the manifest
already advertises the new version. To confirm a redeploy actually took effect,
check that the **process start time is later than the manifest `deployed_at`** —
not just that the manifest names the new version. If the running process predates
the deploy, restart it (`install.sh stop`/`start`, or the platform service verb)
so it re-execs the current venv slot.

### Session Host migration boundary

The old "pipe-owned-by-daemon" model has been replaced by Session Host: the
host owns the child and a reattachable frame stream, while the daemon frontend
can restart and reattach. That gives mid-turn survival for compatible host
protocol versions. The remaining boundary is **wire compatibility**: if a future
frontend cannot speak a surviving host's protocol, version-mux leaves that host
running until its child stops (or until an opt-in stale-host reap bound fires)
rather than killing the child mid-turn.

## Persistence

- **Sessions:** SQLite database at `~/.agent-bridge/sessions.db` (WAL mode)
- **Config:** YAML at `~/.agent-bridge/config.yaml`
- **Auth:** Bearer token at `~/.agent-bridge/auth.yaml`
- **Routing table:** `~/.agent-bridge/active.json` -- the client-facing
  active/previous endpoint table (see [Zero-Downtime Redeploy](#zero-downtime-redeploy));
  absent until a daemon publishes itself, atomically rewritten on each cutover.
- **Logs:** Structured logging to stderr (captured by service manager)

## Implemented Surfaces

- Persistent FastAPI daemon with SQLite-backed sessions/events and SSE delivery
  cursors.
- Session Host frontend/child split for restart-survivable ACP sessions.
- Local, SSH, elevated, and provider-backed namespace dispatch.
- Live interactive session registry, messaging, representation, and progress
  beats.
- Installer-managed versioned runtime, self-provisioning binstub, drain, and
  graceful cutover.

## Namespace Resolvers

Agent-bridge supports **namespace resolvers** for prefixed agent names
(e.g. `codespace:my-cs`, `admin:local-agent`). When a colon appears in
an agent name, the prefix is looked up in the namespace registry and
resolution is delegated to the matching resolver.

The core bridge does not require or vendor provider packages. External provider
plugins self-register by writing JSON manifests under
`~/.agent-bridge/providers.d/`; the daemon scans that directory at startup and
again on demand (throttled) and drives each provider's CLI over a process
boundary (`namespace-list`, `namespace-resolve`, `namespace-ensure-ready`,
`namespace-target-repo`). Missing or malformed provider manifests are skipped
with a warning, so a bad sibling never breaks daemon startup. The built-in
`admin:` resolver is registered in-process.

`namespace-resolve` may return a versioned, provider-owned `venue` object.
Agent-bridge preserves that object unchanged in the `SpawnTarget` and durable
session record so stable target/instance identity, trust posture, readiness, and
capabilities remain owned by the provider rather than reconstructed from a
spawn command. The legacy top-level `workspace_folder` / `security_profile`
fields are folded in only when the provider omits those compatibility keys.
Conflicting workspace identities are rejected; a trust-posture conflict can
only resolve toward `restricted` and marks the target unready. A provider that
successfully returns malformed venue metadata fails closed rather than falling
back to a different in-process target. CLI namespace providers are command
transports: a successful resolution must declare `type: command` and a non-empty
string argv, so provider data cannot redirect a restricted target into a local
or machine-SSH launch path. Defined venue fields are type-checked at the
boundary (including boolean readiness/posture flags and boolean capability
flags), while unknown additive metadata is preserved. Command argv rejects
embedded NUL bytes before persistence.

### Architecture

```
agent name: "codespace:my-cs"
              |          |
              v          v
         prefix       bare name
              |
              v
    NamespaceResolver (ABC)
    +-- CliNamespaceResolver (provider manifest: codespace/container/...)
    +-- AdminResolver        (built-in)
```

### Registered Resolvers

| Prefix | Resolver | Source | Description |
|--------|----------|--------|-------------|
| `codespace:` | `CliNamespaceResolver` | Provider manifest from `agent-codespaces` | Delegates list/resolve/ready checks to the `agent-codespaces` CLI. |
| `container:` | `CliNamespaceResolver` | Provider manifest from `agent-containers` | Delegates list/resolve/ready checks to the `agent-containers` CLI. |
| `admin:` | `AdminResolver` | Built-in (`admin_resolver.py`) | Wraps local agents in elevation (gsudo / sudo -A) |

### NamespaceResolver Interface

```python
class NamespaceResolver(ABC):
    @property
    def prefix(self) -> str: ...
    async def resolve(
        self, name: str, *,
        extra_plugins: list[PluginRef] = (),
        repo: str | None = None,
        repo_remote: str | None = None,
    ) -> SpawnTarget: ...
    async def list(self) -> list[NamespaceAgentInfo]: ...
    @property
    def bare_addressable(self) -> bool: ...
    async def ensure_ready(self, name: str) -> None: ...  # optional
    async def target_repo(self, name: str) -> str | None: ...  # optional
```

### Registration

Resolvers are auto-discovered and registered by `_register_namespace_resolvers()`
and `AgentResolver.refresh_provider_resolvers()` in `agent_registry.py`. Provider
manifests are additive and idempotent; a provider dropped after daemon start is
picked up on the next scan without a restart. The installer deliberately leaves
sibling plugin packages and binstubs to their own installers.
