# agent-dispatch

A **portable agent task-queue + per-host coordinator**. It lets multiple
Copilot CLI agents (worktree sessions, bridged sub-agents, scheduled/reactive
producers) coordinate work through a single, low-latency authority -- instead of
racing each other through `origin/master` pushes or needing a dedicated user
account per agent.

> **Status: early but installable.** This ships the queue **engine**
> (`agent_dispatch.queue`), the per-host **coordinator daemon**
> (`agent-dispatch serve`), the **`agent-dispatch` CLI**, **local MCP tools**
> (`agent-dispatch mcp`), a **lifecycle installer** (marketplace-registered;
> `scripts/install.sh` / `scripts/install.ps1` --
> `install|update|status|start|stop|uninstall` -- deploy a venv + binstub +
> deploy manifest), an **SSE event stream** (`GET /events` /
> `agent-dispatch watch`), and **agent-bridge spawn** (`create --spawn`). On its
> deploy machines the coordinator installs by default and auto-starts as a
> service on both platforms: a **systemd user unit** (Linux) and a **Windows
> Scheduled Task**.

## Install

`agent-dispatch` is a `machine-gated` plugin: the agent-worktrees launch-time
reconciler installs/updates its runtime automatically on the machines listed in
the control repo's gate manifest (`external-repos.yaml` `deploy_machines`), and
the facility path `aperture-labs services agent-dispatch <action>` drives it too
-- the same model as agent-bridge. To install/manage it directly:

```bash
# via the marketplace (once published):
copilot plugin install agent-dispatch@copilot-extensions
# then deploy the runtime (venv + binstub + coordinator service + picker pivot):
bash "$(copilot plugin path agent-dispatch)/scripts/install.sh" install    # Linux/WSL/macOS
# Windows:  pwsh -File <plugin>\scripts\install.ps1 -Action install
```

`scripts/install.{sh,ps1}` is a lifecycle manager --
`install | update | status | start | stop | uninstall` (`init.{sh,ps1}` is a
thin alias for `install`). `install`/`update` create `~/.agent-dispatch/.venv`,
an `agent-dispatch` binstub in `~/.local/bin`, a schema-3 deploy manifest, the
**"Tasks" picker pivot** (see below), and -- unless `--no-service` (`-NoService`)
-- the coordinator service (a per-host local coordinator, matching agent-bridge).
`update` is downgrade-guarded (a stale checkout won't silently roll back a newer
deployed runtime; override with `--force`).

### Worktree-picker "Tasks" pivot

The installer drops a pivot manifest at
`~/.agent-worktrees/pivots/agent-dispatch.json` so the agent-worktrees Textual
picker grows a **Tasks** pivot (between Worktrees and Maintenance). It lists this
machine's `proposed` tasks (via `agent-dispatch inbox`, grouped by target
worktree, handoffs badged) and Enter opens a per-task action sub-menu (kick to a
bridge agent / abandon). The seam is a filesystem manifest registry, not a Python
import -- the plugins live in separate venvs -- so a stale or absent picker simply
ignores it. Source: `pivots/agent-dispatch.json`.

### Running the coordinator as a service

On the host that *is* a coordinator, the service is installed by default
(`install`/`update` above). To manage it explicitly, or install only the client
on a machine that points at a remote coordinator:

```bash
# Linux/WSL -- a systemd user unit (installed by default; --no-service to skip):
bash "$(copilot plugin path agent-dispatch)/scripts/install.sh" status   # or start | stop
systemctl --user status agent-dispatch          # manage it directly
# edit ~/.agent-dispatch/service.env (host/port/token), then: systemctl --user restart agent-dispatch
```

```powershell
# Windows -- a Scheduled Task (starts at logon; installed by default):
pwsh -File <plugin>\scripts\install.ps1 -Action status   # or start | stop
Get-ScheduledTask -TaskName agent-dispatch | Get-ScheduledTaskInfo   # manage it
# edit %USERPROFILE%\.agent-dispatch\service.env, then: Start-ScheduledTask -TaskName agent-dispatch
```

Both read an editable `service.env` (host/port/db/token) beside the runtime. A
client-only machine installs with `--no-service` (`-NoService`) and points
`AGENT_DISPATCH_URL` at the coordinator host.

## Why

A queue needs an **atomic leased claim** to be a correct coordinator: two agents
must never both "win" the same task, and a crashed agent must not hold work
forever. Git and issue trackers give neither cheaply. `agent-dispatch` provides
that primitive as a single-writer SQLite (WAL) queue, reachable over HTTP --
loopback on a lone dev box, one designated host in a facility. Same code, one
config switch.

## The engine (`agent_dispatch.queue`)

```python
from agent_dispatch import TaskQueue

q = TaskQueue("~/.agent-dispatch/tasks.db")

# Producer: enqueue a task (or propose a draft that isn't claimable yet)
t = q.create("Add narration track", prompt="segment 42", requires=["logger"])

# Consumer: a worker advertises capabilities and atomically leases one task
task = q.claim_one("worker-1", capabilities=["logger"])
if task:
    q.start(task.id, "worker-1")
    # ... do the work ...
    q.complete(task.id, "worker-1", result_ref="pr/123")

# Crash recovery: return any expired-lease task to the queue
q.recover_expired_leases()
```

### State model

```
proposed -> queued -> claimed -> started -> completed        (terminal)
                ^         |          |
                +-- decline/yield ---+
                ^
                +-- lease expiry (internal requeue, attempts++)
   (any non-terminal) --------------------------> abandoned   (terminal, permission-gated)
```

- **proposed** -- written but not yet claimable (a draft handoff / undecided idea).
- **queued** -- claimable.
- **claimed** -- leased by a worker (may evaluate before committing).
- **started** -- under active implementation.
- **completed** / **abandoned** -- terminal (abandon requires permission).
- Lease expiry returns a held task to **queued** (a dead worker's task
  resurfaces). The coordinator runs this recovery sweep automatically every
  `AGENT_DISPATCH_SWEEP_INTERVAL` seconds (default 60; `0` disables); `recover`
  forces one on demand.

### Routing: `requires` (hard) vs `affinity` (soft)

- **`requires`** -- a set of capability tokens (e.g. `logger`, `review`) or an
  identity pin (`agent:review-bot`). A task is claimable only when `requires` is
  a subset of the worker's advertised capabilities. This is how the same
  capability on two machines gives **cooperative, redundant** coverage: first
  writer wins; a dead worker's lease expires and the other reclaims.
- **`affinity`** -- soft preferences (preferred agent/worktree) that order
  candidates but never exclude.

### Payloads (inline + content-addressed blobs)

A task carries a Markdown `payload` (the graduated handoff's asset). Small
payloads live **inline** in the row; a payload over `blob_threshold` bytes
(default 4 KiB) is spilled to a **content-addressed blob** under
`~/.agent-dispatch/payloads/<sha256>.md`, and the row keeps only a `blob:<hash>`
ref -- so `list`/`find` stay lean and identical payloads dedupe to one file (no
external deps). `read_payload()` (engine) / `GET /tasks/{id}/payload` /
`agent-dispatch payload <id> [--raw]` resolve either form transparently; an
external `payload_ref` (e.g. `pr/123`) is left opaque for the caller.

### Dedup & scheduling

- `dedup_key` (unique) makes `create` idempotent -- a duplicate returns the
  existing task, so agents can browse/`find` before ideating.
- `not_before` defers a task until a wall-clock time (scheduled creation).

## Producers

The coordinator core only owns the queue -- it runs **no** scheduler and **no**
PR/alert logic. Anything that *creates* tasks is a **producer**: any client that
can POST. Two ship in-box (both driven by a declarative JSON spec, both talking
to the coordinator through the ordinary client):

### Scheduler / timer producer (`agent-dispatch schedule`)

Turns recurring task templates into deferred tasks. Each *tick* creates one task
per due occurrence, with `not_before` set to the occurrence time and a
deterministic `dedup_key` (`sched:<id>:<epoch>`) so re-ticks never double-create.

```jsonc
// schedules.json
{
  "default_repo": "example.com/acme/widget",   // lane fallback
  "schedules": [
    { "id": "hourly-sweep", "title": "Sweep service health", "interval_seconds": 3600 },
    { "id": "morning-digest", "title": "Morning digest", "at": ["09:00"],
      "require": ["logger"], "labels": ["scheduled"] }
  ]
}
```

A schedule uses **either** `interval_seconds` **or** `at` (a list of local
`"HH:MM"` times). Drive it one-shot from any external timer, or use the built-in
loop:

```bash
agent-dispatch schedule tick  schedules.json          # one pass (cron / systemd timer / manage_schedule)
agent-dispatch schedule serve schedules.json --interval 60   # built-in timer loop
```

### Reactive webhook producer (`agent-dispatch webhook`)

A small HTTP app that maps two generic, forge-neutral event shapes onto tasks:

- `POST /webhook/pr` -- a git-forge PR event; when **merged**, creates a
  follow-up task (`source=pr-webhook`, `origin_ref=pr/<n>`) in the lane derived
  from the payload's repository remote. Handles the shape GitHub and Gitea share.
- `POST /webhook/telemetry` -- a monitoring alert; a **firing** alert creates a
  remediation task (`source=telemetry`). Accepts an Alertmanager-style
  `{"alerts": [...]}` batch or a single flat alert object.

Every task carries a deterministic `dedup_key`, so a redelivered webhook doesn't
double-enqueue. Behavior (templates, base-branch/severity allowlists, an optional
inbound bearer token, the coordinator URL) is set in an optional JSON config:

```bash
agent-dispatch webhook --config webhook.json --host 127.0.0.1 --port 9331
```

## Development

```bash
cd plugins/agent-dispatch
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest
ruff check .
```

## Coordinator + CLI

Run the per-host coordinator (loopback by default), then drive it with the CLI:

```bash
agent-dispatch serve                     # binds 127.0.0.1:9330 (AGENT_DISPATCH_* to override)

# from any agent/producer (AGENT_DISPATCH_URL points at the coordinator):
agent-dispatch create "Add narration track" --require logger --dedup-key seg42
agent-dispatch worktree-status           # this worktree's inbox: tasks assigned to + owned by it
agent-dispatch inbox                      # machine-scoped, cross-lane pickable tasks (default: proposed)
agent-dispatch claim                     # lease my assigned/eligible task (identity auto-resolved)
agent-dispatch start  <id>  <owner>
agent-dispatch complete <id> <owner> --result-ref pr/123
agent-dispatch list --status queued
agent-dispatch recover                                 # requeue expired-lease tasks
agent-dispatch watch                                   # stream task events (SSE) as JSON lines
```

`inbox` complements the two lane-scoped reads: `worktree-status` is *this
worktree's* assigned/owned tasks, and `list` is scoped to the calling repo's
lane, but `inbox` spans **every** lane and returns the tasks *this machine* can
pick up — a matching `target_machine` plus machine-agnostic ones — defaulting to
the `proposed` state. Each entry carries `target_worktree`, `affinity`, `labels`
and `repo_name`, so a consumer (e.g. the worktree picker's task pivot) can group
by worktree and badge handoffs. The machine is resolved from the CWD via
`agent-worktrees`; pass `--machine <name>` to override.

### Worker identity

An agent's identity is the **`machine`/`worktree_id`** pair — the only durable
agent id the facility has. `claim` and `worktree-status` **resolve it from the
current directory** by delegating to `agent-worktrees` (the same CWD resolution
git uses), so an agent in its worktree just runs `agent-dispatch worktree-status`
/ `agent-dispatch claim` with no arguments. Claiming stamps that pair as the
task's `owner`, and **claim honors targeting**: an agent only leases tasks that
are untargeted or targeted at its own machine/worktree. Pass `--machine` /
`--worktree` to override the resolution (or where `agent-worktrees` is absent).

The coordinator publishes `task.created` / `.proposed` / `.approved` / `.claimed`
/ `.started` / `.yielded` / `.completed` / `.abandoned` / `.detached` events on
`GET /events` (Server-Sent Events) — the hook a subscriber (e.g. agent-bridge)
reacts to.

### Spawning a worker (agent-bridge)

`create --spawn` asks **agent-bridge** to spawn a worker agent that claims and
executes the task:

```bash
agent-dispatch create "Summarize PR 42" --require review --spawn            # managed (waits)
agent-dispatch create "Summarize PR 42" --spawn --spawn-agent task-worker --async  # fire-and-forget
```

The worker is instructed to claim the specific task by id
(`agent-dispatch claim <id> --task <task>`). If the `agent-bridge` CLI isn't on
PATH, `--spawn` **degrades gracefully** — the task is simply left queued for any
worker to claim, so agent-dispatch stays usable without a bridge.

## MCP tools (`agent-dispatch mcp`)

For agents that prefer **tools over a CLI**, `agent-dispatch mcp` runs a local
**stdio MCP server** — the per-agent interaction layer. It resolves the caller's
`machine`/`worktree` identity from the working directory (like the CLI) and
proxies each tool call to the coordinator, so `dispatch_claim` /
`dispatch_worktree_status` are auto-scoped to the agent's worktree with no
per-agent credential wiring. Requires the `mcp` extra
(`pip install 'agent-dispatch[mcp]'`).

Point a Copilot sub-agent (or any MCP client) at it:

```json
{
  "mcpServers": {
    "agent-dispatch": {
      "command": "agent-dispatch",
      "args": ["mcp"],
      "env": { "AGENT_DISPATCH_URL": "http://127.0.0.1:9330" }
    }
  }
}
```

It exposes the queue as tools: `dispatch_create` / `dispatch_find` /
`dispatch_list` / `dispatch_show` / `dispatch_events` / `dispatch_payload` /
`dispatch_worktree_status` / `dispatch_claim` / `dispatch_start` /
`dispatch_yield` / `dispatch_complete` / `dispatch_abandon` /
`dispatch_heartbeat` / `dispatch_approve` / `dispatch_detach` /
`dispatch_recover`. `dispatch_create` takes an inline `payload` the coordinator
spills to a blob when large.

### Two MCP surfaces

There are **two** ways to reach the tools — pick by where the client runs:

| Surface | Command / endpoint | Identity | Use when |
|---------|--------------------|----------|----------|
| **Local stdio shim** | `agent-dispatch mcp` | resolved from the caller's **CWD** (like the CLI) | the agent has `agent-dispatch` installed locally in its worktree |
| **Coordinator-hosted HTTP** | mounted at **`/mcp`** on the coordinator | `X-Agent-Machine` / `X-Agent-Worktree` **request headers** (or explicit tool args) | a remote MCP client (e.g. an `agent-mcp` bridge on another host) that can't resolve local identity |

Both expose the same 16 `dispatch_*` tools and publish the same `task.*` events;
they only differ in how identity is supplied. The coordinator mounts `/mcp`
automatically when the `mcp` extra is installed (pass `enable_mcp=False` to
`create_app` to suppress it); if a bearer token is configured it also guards the
`/mcp` mount. A remote client points at, e.g.,
`http://<coordinator-host>:9330/mcp` and sets the identity headers per agent.

Configuration (all optional): `AGENT_DISPATCH_HOST`, `AGENT_DISPATCH_PORT`,
`AGENT_DISPATCH_DB`, `AGENT_DISPATCH_TOKEN` (bearer auth),
`AGENT_DISPATCH_SWEEP_INTERVAL` (auto lease-recovery cadence in seconds; `0`
disables), and `AGENT_DISPATCH_URL` (the base URL the CLI talks to -- point it at
a remote coordinator on a shared network).
