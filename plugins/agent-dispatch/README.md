# agent-dispatch

A **portable agent task-queue + per-host coordinator**. It lets multiple
Copilot CLI agents (worktree sessions, bridged sub-agents, scheduled/reactive
producers) coordinate work through a single, low-latency authority -- instead of
racing each other through `origin/master` pushes or needing a dedicated user
account per agent.

> **Status: early but installable.** This ships the queue **engine**
> (`agent_dispatch.queue`), the per-host **coordinator daemon**
> (`agent-dispatch serve`), the **`agent-dispatch` CLI**, an **installer**
> (marketplace-registered; `scripts/init.sh` / `scripts/init.ps1` deploy a venv +
> binstub + deploy manifest), an **SSE event stream** (`GET /events` /
> `agent-dispatch watch`), and **agent-bridge spawn** (`create --spawn`). Still to
> come: facility service auto-start (systemd unit / scheduled task) — for now the
> coordinator is launched with `agent-dispatch serve`.

## Install

```bash
# via the marketplace (once published):
copilot plugin install agent-dispatch@copilot-extensions
# then deploy the runtime (venv + binstub + manifest):
bash "$(copilot plugin path agent-dispatch)/scripts/init.sh"   # Linux/WSL/macOS
# Windows:  pwsh -File <plugin>\scripts\init.ps1
```

The installer creates `~/.agent-dispatch/.venv`, an `agent-dispatch` binstub in
`~/.local/bin`, and a schema-3 deploy manifest. Re-run with `--force` to repair.

### Running the coordinator as a service

On the host that *is* the coordinator, add `--service` (Linux/WSL) to install a
**systemd user unit** that runs `agent-dispatch serve` and restarts on failure:

```bash
bash "$(copilot plugin path agent-dispatch)/scripts/init.sh" --service
systemctl --user status agent-dispatch          # manage it
# edit ~/.agent-dispatch/service.env (host/port/token), then: systemctl --user restart agent-dispatch
```

A machine that is only a *client* of a remote coordinator omits `--service` and
just points `AGENT_DISPATCH_URL` at the coordinator host. (A Windows scheduled-task
equivalent for `init.ps1` is a follow-up; on Windows run `agent-dispatch serve`.)

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
- Lease expiry returns a held task to **queued** (a dead worker's task resurfaces).

### Routing: `requires` (hard) vs `affinity` (soft)

- **`requires`** -- a set of capability tokens (e.g. `logger`, `review`) or an
  identity pin (`agent:review-bot`). A task is claimable only when `requires` is
  a subset of the worker's advertised capabilities. This is how the same
  capability on two machines gives **cooperative, redundant** coverage: first
  writer wins; a dead worker's lease expires and the other reclaims.
- **`affinity`** -- soft preferences (preferred agent/worktree) that order
  candidates but never exclude.

### Dedup & scheduling

- `dedup_key` (unique) makes `create` idempotent -- a duplicate returns the
  existing task, so agents can browse/`find` before ideating.
- `not_before` defers a task until a wall-clock time (scheduled creation).

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
agent-dispatch claim                     # lease my assigned/eligible task (identity auto-resolved)
agent-dispatch start  <id>  <owner>
agent-dispatch complete <id> <owner> --result-ref pr/123
agent-dispatch list --status queued
agent-dispatch recover                                 # requeue expired-lease tasks
agent-dispatch watch                                   # stream task events (SSE) as JSON lines
```

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

Configuration (all optional): `AGENT_DISPATCH_HOST`, `AGENT_DISPATCH_PORT`,
`AGENT_DISPATCH_DB`, `AGENT_DISPATCH_TOKEN` (bearer auth), and
`AGENT_DISPATCH_URL` (the base URL the CLI talks to -- point it at a remote
coordinator on a shared network).
