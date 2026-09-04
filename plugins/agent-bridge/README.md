# Agent Bridge

Persistent inter-agent communication service for Copilot CLI. One instance per
machine, providing session management, SSE event streaming, live-session
messaging, and agent subprocess spawning across local, SSH, CodeSpace, and
container venues.

Supports **Windows** and **Linux/WSL** (macOS planned).

## Responsibility boundary

agent-bridge owns **live cross-boundary agent communication**: starting or
resuming a persistent session, sending a turn, streaming its response, and
steering or taking over that live agent across repository, worktree, machine,
or venue boundaries. It does not own generic delegation policy or durable task
state. Use native Task sub-agents for bounded work inside the current session,
`delegation-guidance:delegating-work` for task decomposition, and
`agent-dispatch:agent-dispatch` for queued tasks, atomic claims, retries, and
supervision.

The current session-oriented controls and the target compact delegation
semantics are mapped in
[Delegated-agent contract](docs/delegation-contract.md). That baseline defines
logical identity, caller relationships, attention reasons, and message
idempotency without changing the existing CLI, HTTP/SSE, ACP, or Session Host
contracts.

Agent-facing sessions receive an exact payload-local command through the
session command catalog. That command resolves and, when necessary, provisions
the runtime from its own payload without searching `PATH` for another
marketplace's same-named plugin. On Windows the catalog publishes the native
`.cmd` entry so prompt bodies sent through stdin remain intact.

The legacy global wrappers remain explicit compatibility and management
boundaries for callers that do not inherit session catalogs: daemon and service
launchers, picker pivots, remote commands, provider manifests and provider
process boundaries, elevated launchers, and out-of-session management callers.
Catalog adoption does not make those callers payload-aware; they retain their
current commands until an attributable launcher contract reaches each surface.

## How It Works

Agent Bridge runs as a local HTTP service on an OS-assigned loopback port by
default. The daemon advertises its live endpoint in `~/.agent-bridge/active.json`
and the CLI discovers it there, so callers should use `agent-bridge status`
rather than hardcoding a port. Multiple Copilot CLI sessions can start, stop,
resume, and observe conversations with agents running locally, over SSH, or via
optional namespace providers such as `codespace:` and `container:`.

agent-bridge is a **standalone persistent daemon** -- enabling only this plugin
is enough for its core service, static/local agents, WebSocket ACP surface, and
live-session registry. Sibling plugins compose opportunistically: when a sibling
drops a provider manifest into `~/.agent-bridge/providers.d/`, its namespace
(for example `codespace:` or `container:`) appears; when it also exposes a
credential-relay profile, agent-bridge folds those sources into its relay. If a
sibling is absent, only that sibling's namespace/relay feature is absent.
Provider discovery is desired-set based: deleting, invalidating, or losing the
command target of a manifest withdraws that dynamic namespace without a daemon
restart. Bad entries warn but do not block valid peers. `agent-bridge doctor`
lists every provider finding with the exact entry, target, and cleanup or
re-registration remedy.

## Streaming & the delivery cursor

When a host agent delegates work with `send`, it gets a **continuous, low-noise
feed** of the remote agent's progress -- not a silent block that looks "stuck".

- **`send`** streams the remote turn live by default, then returns when the turn
  settles.
- **`wait`** streams the in-flight turn to completion.
- **`read`** resumes the feed from where the host last left off, or does
  random-access historical reads.
- **`start`** does not stream (no conversation yet).

### Collapsed feed (default)

To avoid polluting the host agent's context, the feed is **collapsed**:

- **agent messages** stream in full (the signal);
- **chain-of-thought** collapses to a single `▸ thinking…` marker per burst;
- **tool calls** collapse to one line: `▸ running: <title> … done`.

Expand on demand (rarely needed) with `--expand`:

```bash
agent-bridge read <session> --expand thoughts   # show full reasoning
agent-bridge read <session> --expand tools       # show tool output
agent-bridge read <session> --expand all
```

### Delivery cursor (exactly-once feed)

Each caller has a per-session **delivery cursor**. Commands stream from the
cursor and **ack only after the content is flushed** to the host, so the cursor
advances on *confirmed delivery* -- never on server-side production. This gives
one contiguous, gap-free, duplicate-free stream:

- Killing the consumer mid-stream (Ctrl-C / SIGKILL / terminal close) leaves the
  cursor where it was; the next `read` resumes **exactly** where the host left
  off -- nothing skipped.
- A service restart mid-workflow is survivable: the client reconnects and
  resumes from the acked cursor (state lives in SQLite).
- Random-access reads (`read --range A:B`, `read --event N`) are the **only**
  way to re-read consumed content and never move the cursor.

The caller identity keying the cursor comes from `--caller`, else the current
worktree (`agent-worktrees get worktree-dir`), else a shared per-session default.

### Remote Bridge carrier operations

Remote consumers use the local Agent Bridge daemon rather than constructing SSH
commands or learning carrier details. The initial surface is intentionally
narrow and read-oriented:

```bash
agent-bridge remote status example-host 11111111-1111-1111-1111-111111111111 \
  --caller-id supervisor.lane-a --json
agent-bridge remote live-session example-host 22222222-2222-2222-2222-222222222222 \
  --json
agent-bridge remote events example-host 11111111-1111-1111-1111-111111111111 \
  --caller-id supervisor.lane-a --json
```

`host` is a topology machine key for a single-environment machine, or an exact
SSH alias. Multi-environment machines require the alias so Windows, WSL, and
Linux Bridge daemons cannot be confused. Session arguments are exact
hosting-Bridge IDs. Status, live-session resolution, event IDs, event names,
payloads, and timestamps come unchanged from the hosting Bridge. The local
daemon owns one shared reconnecting carrier per normalized SSH identity and
does not create another session or event ledger.

Every event consumer supplies its own stable `caller_id`. Remote delivery uses
that identity's hosting-Bridge cursor and acknowledges only after local output
has been flushed. Carrier reconnect starts from the hosting Bridge's durable
acknowledged cursor; a local daemon cutover is likewise recoverable because a
replacement daemon reuses the same remote cursor authority. Event-log rebuild,
cursor invalidation, and non-contiguous replay produce a cursor-neutral
`bridge_control` envelope with `action=full_reconcile`; they are never reported
as a quiet empty stream. The HTTP equivalents live below
`/api/v1/remote/{host}/...` and require the ordinary local bearer token.

### Bounded result snapshots

`result` is the cursor-neutral answer to "what did this delegate produce?":

```bash
agent-bridge result <session-or-worktree>
agent-bridge result <session-or-worktree> --json
agent-bridge result <session-or-worktree> --position <opaque-position>
agent-bridge result <session-or-worktree> --expand <opaque-detail-ref>
```

The fixed-size response combines current lifecycle/attention state, bounded
active-work and pending-input fields, the latest completed turn, and a collapsed
increment of recent work. Reasoning and verbose tool output stay out of the
ordinary result path; each projected event and completed turn carries an opaque
detail reference for explicit expansion. Bridge-owned sessions recover durable
turns and event positions. Represented interactive sessions use the same shape
but explicitly report process-lifetime retention, read-only requests, and
unavailable durable recovery.

Positions are bridge-issued and must not be parsed or compared across targets.
They do not read or advance a delivery cursor. A resync rebuild invalidates an
older position explicitly instead of reusing its event number for different
history. A represented process replacement uses a new session ID and therefore
a new position scope; PID-mismatched re-registration is rejected. Empty logs
return no position until the first event establishes an observable origin.
Result detail expansion never returns reasoning or nested-agent events.

### Attention waits

Bare `wait` keeps its historical meaning: stream the current turn until it
settles. Explicit attention flags opt into the cursor-neutral attention
contract:

```bash
agent-bridge wait <session> --attention input_required
agent-bridge wait <session> --attention failed --attention stopped
agent-bridge wait <session> --all-attention
agent-bridge wait <session> --all-attention --json
agent-bridge wait <session> --all-attention --position <opaque-position>
```

The selected stable reasons are `turn_complete`, `turn_cancelled`, `failed`,
`input_required`, `permission_required`, `unreachable`, `policy_required`,
`contract_changed`, `stopped`, and `ended`. `ended` is reserved but does not
settle in the current production lifecycle because deliberate retirement
deletes the session and its history; callers that need a replayable terminal
boundary should select `stopped` until terminal-retention ownership lands.

Human mode retains one SSE delivery loop, renders and acknowledges through the
settlement boundary, then exits. JSON mode uses only the authenticated bounded
attention request and never reads or advances a delivery cursor. A service
restart is retried from the opaque attention position. A deliberate handoff
continues on a compatible successor; explicit protocol rejection settles as
`contract_changed`, while inconclusive discovery/reachability remains
recoverable. Timeout returns `settled: false` with the latest position rather
than inventing a timeout reason.

The authenticated API is
`GET /api/v1/sessions/{session-or-worktree}/attention` with repeatable `reason`,
optional `position`, and bounded `timeout_seconds` query parameters. A live
manual permission can be resolved through
`POST /api/v1/sessions/{session}/permission` using the correlated `request_id`
and one advertised `option_id`.

### Phased timeouts

`send` distinguishes phases so a slow codespace cold-start is not mistaken for a
hung turn. Configure in `~/.agent-bridge/config.yaml`:

```yaml
timeouts:
  codespace_boot: 300   # waiting for a Shutdown codespace to boot
  ssh_connect: 120      # establishing SSH (patient: wake-on-LAN / ProxyJump)
  session_start: 240    # ACP handshake (client start + initialize)
  session_new: 1200     # cold ACP session/new (large-workspace + skills load)
  command: 1800         # a single turn/command to complete
```

### Remote Session Host authority

A remote Session Host is the durable owner of its Copilot child. Each running
host publishes a mode-0600 record under the remote user's mode-0700
`~/.agent-bridge/session-hosts/` catalogue. The record names the bridge session,
host/child PIDs and process-start identities, host port, protocol/build version,
working directory, and credential-relay reverse forwards. It also carries the
private ATTACH nonce; diagnostics must redact that field.

After a frontend restart, agent-bridge first reattaches from its local HostIndex.
If that row was lost but the session DB survived, it inspects the far-side
record for an already-running CodeSpace, validates boot/PID identity, rebuilds
the ACP and relay forwards, and adopts the same child. A transport failure is
inconclusive and blocks duplicate spawn; only confirmed host death permits
pruning, with an explicit process-group reap if the owned child survived.
Startup inspection never wakes a stopped CodeSpace; explicit resume may wake
and revalidate it.

For a stopped trusted-container session, resume first re-resolves the provider's
current serving generation. If credential relay is enabled, it replaces the
session's reverse-forward against the daemon's current live relay and proves the
container-side loopback listener accepts before ACP is marked ready or a queued
prompt is delivered. Relay setup or verification failure aborts the resume
explicitly; relay-disabled fleets keep the auth-light path.

### Session retention & garbage collection

`sessions.db` is a *relay log* of cross-agent turns/events -- not the canonical
Copilot session history (that lives in each target's `~/.copilot/session-state`
and is archived separately). Left unbounded it grows monotonically: SQLite never
shrinks the file, so a large dispatch can leave **tens of GB** of freelist pages
behind even after the session ends.

The daemon garbage-collects automatically: it prunes the relay metadata for
**terminal** sessions (`ended`/`failed`/`stopped`) older than the retention
window, then VACUUMs to return freed pages to the OS. GC runs on **startup**, on
a periodic **sweep**, and on demand via `agent-bridge gc`. Live sessions (and any
with a still-running client) are never touched. Configure in
`~/.agent-bridge/config.yaml`:

```yaml
retention:
  enabled: true
  max_age_hours: 168       # prune terminal sessions older than this (7 days)
  statuses: [ended, failed, stopped]
  vacuum: true             # compact the DB after pruning
  vacuum_min_free_mb: 128  # only VACUUM when freelist exceeds this
  sweep_interval_hours: 12 # background sweep cadence (0 = startup + manual only)
```

## Connection pipeline & diagnostics

Bringing up a remote agent passes through seven distinct stages, each with its
own patience/fail-fast profile. agent-bridge records a `connect_checkpoint`
event (`started` / `reached` / `failed`, with `elapsed_ms`) at every stage —
into both the daemon log and the session's event feed — so a failure says
*exactly* which stage broke and whether a retry could help, instead of an opaque
"agent died, trying a new session".

| # | Stage | Behavior |
|---|-------|----------|
| 1 | connect-bridge | CLI → service. Transient on restart → short **grace + retry** (client side). |
| 2 | bridge-to-sshmgr | In-process hand-off. Reliable → **fail fast**. |
| 3 | ssh-to-target | ssh-manager → SSH. Boot / wake-on-LAN / ProxyJump → **patient retry** to `ssh_connect` deadline, then a staged retryable failure. |
| 4 | target-auth-env | Auth relay + env on target. Dead relay → **instant fail** (not retryable). |
| 5 | target-binstub | Binstub / folder present. **Instant fail** if missing. |
| 6 | worktree | Create/resume worktree. Failures **propagate**, no retries. |
| 7 | launch-acp | Launch Copilot ACP: handshake bounded by `session_start`, the cold `session/new` bounded by the larger `session_new`, then fail fast. |

On failure, a `connect_failed` event carries `{stage, stage_name, retryable,
message}`. A host agent can surface the connection checkpoints with
`agent-bridge read <session> --expand all`.

### On-device breadcrumb

Just before the remote binstub runs, agent-bridge writes a timestamped
"reached-device" line (with the session id) to
`$AGENT_BRIDGE_CONNECT_LOG` (default `~/.agent-bridge/connect.log`) on the
target. If a launch hangs or fails opaquely, SSH into the target and check that
log to confirm the connection actually reached the device (and roughly when) —
distinguishing an unreachable host from an on-device failure.

## External ACP clients (WebSocket) & status UX

agent-bridge speaks the [Agent Client Protocol](https://agentclientprotocol.com/)
to its downstream agents, and re-exposes that surface so **any ACP client** can
drive a remote agent through the bridge — for example
[acp-ui](https://acp-ui.github.io/), a browser ACP chat client.

**Endpoints** (JSON-RPC 2.0 over a WebSocket, newline-delimited frames):

| URL | Target |
|-----|--------|
| `ws://<host>:<port>/acp/<agent>` | spawn a fresh session for a registered agent |
| `ws://<host>:<port>/acp/session/<session-id>` | *adopt* an already-running bridge session (observe/steer) — it is **not** stopped when the client disconnects |

Use the port printed by `agent-bridge status`, `agent-bridge token --verbose`,
or `/ui`; the default service port is dynamic unless pinned in config.

**Auth.** Browsers cannot set WebSocket headers, so the bridge token is carried
as a `bearer.<token>` WebSocket subprotocol (acp-ui's convention); a plain
`Authorization: Bearer <token>` header is also accepted for non-browser clients.
The server negotiates the `acp.v1` subprotocol. Print the token with
`agent-bridge token` (it lives in `~/.agent-bridge/auth.yaml`).

**Status UX.** `GET /ui` serves a dependency-free status page listing registered
agents and live sessions, each with a copyable ACP WebSocket URL to paste into
acp-ui. It calls the token-protected `/api/v1` endpoints (token entered once,
kept in `localStorage`), so no data is exposed without auth.

> Hosted acp-ui is served over HTTPS, which (per the browser mixed-content rule)
> can only dial `wss://` — not `ws://localhost`. For a local bridge, use the
> acp-ui desktop app / `npm run preview:web`, or expose the bridge via a `wss://`
> tunnel. acp-ui's "http (remote)" transport is not yet implemented upstream, so
> only the WebSocket transport is wired today.

> Enabled by the pure-Python `wsproto` dependency (uvicorn keeps its plain h11
> HTTP path; no native build, preserving win-arm64 support).

## Getting Started / Usage

Start with [Getting Started](docs/getting-started.md) for the standalone install
and first health check. Then use the [`agent-bridge` skill](skills/agent-bridge/SKILL.md)
and [CLI reference](skills/agent-bridge/references/cli-commands.md) for
day-to-day dispatch, live-session messaging, handoff, and service control.
Use [`agent-bridge-troubleshooting`](skills/agent-bridge-troubleshooting/SKILL.md)
for wedged dispatches, resume issues, relay/auth failures, and repair drills.

## Docs

| Document | Description |
|----------|-------------|
| [Getting Started](docs/getting-started.md) | Install, configure, start the service |
| [Architecture](docs/architecture.md) | Service design, API reference, deployment |
| [Delegated-agent contract](docs/delegation-contract.md) | Current-to-target lifecycle, identity, attention, and idempotency semantics |
| [Machine Configuration](docs/machine-config.md) | Topology setup -- machines.yaml, agents config |

## Skills

| Skill | Description |
|-------|-------------|
| `agent-bridge` | CLI control plane -- send/create/read/wait, sessions, live sessions, service/config |
| `agent-bridge-troubleshooting` | Diagnose and recover stuck dispatches, resume hangs, relay/auth failures, and split-brain |
| `agent-worktrees:copilot-extensions-setup` | Marketplace/runtime setup when a host needs guided installation |

## Platforms

| Platform | Service manager | Auto-start |
|----------|----------------|------------|
| Windows | Scheduled task | At-logon (15s delay) |
| Linux/WSL | systemd user unit | Enabled |
| macOS | Planned | -- |
