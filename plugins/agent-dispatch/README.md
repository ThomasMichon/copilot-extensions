# agent-dispatch

A **portable agent task-queue + per-host coordinator**. It lets multiple
Copilot CLI agents (worktree sessions, bridged sub-agents, scheduled/reactive
producers) coordinate work through a single, low-latency authority -- instead of
racing each other through `origin/master` pushes or needing a dedicated user
account per agent.

> **Status: early.** This ships the queue **engine** (`agent_dispatch.queue`),
> the per-host **coordinator daemon** (`agent-dispatch serve`), and the
> **`agent-dispatch` CLI**. The installer + marketplace registration (making it
> deployable as a managed service) and SSE/agent-bridge integration land in
> subsequent slices, so it is not yet a marketplace-installed runtime.

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
agent-dispatch claim worker-1 --capability logger      # atomically leases one eligible task
agent-dispatch start  <id> worker-1
agent-dispatch complete <id> worker-1 --result-ref pr/123
agent-dispatch list --status queued
agent-dispatch recover                                 # requeue expired-lease tasks
```

Configuration (all optional): `AGENT_DISPATCH_HOST`, `AGENT_DISPATCH_PORT`,
`AGENT_DISPATCH_DB`, `AGENT_DISPATCH_TOKEN` (bearer auth), and
`AGENT_DISPATCH_URL` (the base URL the CLI talks to -- point it at a remote
coordinator on a shared network).
