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

The caller identity keying the cursor comes from `--caller`, else `$WORKTREE_ID`,
else a shared per-session default.

### Phased timeouts

`send` distinguishes phases so a slow codespace cold-start is not mistaken for a
hung turn. Configure in `~/.agent-bridge/config.yaml`:

```yaml
timeouts:
  codespace_boot: 180   # waiting for a Shutdown codespace to boot
  ssh_connect: 120      # establishing SSH (patient: wake-on-LAN / ProxyJump)
  session_start: 60     # freshly spawned session to become idle
  command: 1800         # a single turn/command to complete
```

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
| 7 | launch-acp | Launch Copilot ACP. Should be fast; bounded by `session_start`, then fail fast. |

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
