# work-coalescing-singleton -- fold many callers onto one warm daemon

Service-neutral primitives that make "many short-lived callers of the same
cheap, idempotent work share one warm, refcounted daemon instead of each
paying full price" a reusable shape, not a per-plugin reinvention. Generalizes
`agent-worktrees`' `hook_ipc.py` (its `session-lifecycle-v1` resident status
monitor listener), which proved the design in production for one request kind.

- **Distribution:** `agent-work-coalescing-singleton` (the `agent-` prefix
  avoids PyPI dependency-confusion; never published to an index -- consumers
  install it from a local path).
- **Import module:** `work_coalescing_singleton`
- **Runtime deps:** none (pure stdlib).

Realizes the `work-coalescing-singleton` behavior of the plugin-services
vision (see
[`docs/patterns/work-coalescing-singleton.md`](../../docs/patterns/work-coalescing-singleton.md)
for the full design: the wire protocol, timeout budgets, and validation
scenarios this library implements).

## What's inside

### `server` -- the daemon side

`CoalescingServer` is a dynamic-port, loopback-only, token-authed TCP server
(the same shape as `hook_ipc.HookIpcServer`) that adds:

- **Request coalescing** on `(kind, key)`: a request that arrives while an
  identical one is already executing joins it instead of starting a second
  execution. Distinct keys always execute independently (never coalesced
  across identity) -- coalescing is a cache-of-one-in-flight-execution, not a
  scheduler; bounding concurrency across distinct keys is the caller's
  `compute` callback's own responsibility.
- **Explicit ref-counted subscribers** (`subscribe` / `release`, plus an
  implicit `touch` on every fire-and-forget request) with a bounded **linger**
  timer that only starts once the last subscriber releases, and cancels if a
  new subscriber arrives before it fires.
- **A liveness reaper**: a subscriber that never sends an explicit `release`
  (crashed, killed) is dropped once its last-seen timestamp exceeds
  `subscriber_ttl`, so it cannot pin the daemon alive forever.
- **`on_idle`**: invoked once, off the reaper/timer thread, when the linger
  expires with zero subscribers -- the consumer's cue to stop serving and
  exit.

```python
from work_coalescing_singleton import CoalescingServer

def compute(kind: str, payload: dict) -> dict:
    ...  # the actual (cheap, idempotent) work, keyed by the caller's `key`

server = CoalescingServer(compute, linger_seconds=5.0, on_idle=lambda: os._exit(0))
server.start()
publish_rendezvous(server.rendezvous())  # {"transport", "endpoint", "token", "generation"}
```

### `client` -- the caller side

Pure functions over a resolved `(host, port, token)` endpoint -- this module
never resolves rendezvous or boots a daemon itself; that stays the consumer's
own discovery/boot logic (each plugin already has one).

- `subscribe` / `release` -- explicit ref-count in/out.
- `request(..., request_deadline_s=...)` -- one coalesced request; raises
  `DaemonUnavailable` on timeout, malformed response, or an explicit
  `fallback` reply from the server (its own coalesced execution missed the
  requester's deadline).
- `call_with_fallback(...)` -- the full boot-wait + request + inline-fallback
  sequence in one call: dial an existing daemon, else invoke the caller's
  `boot` and re-poll `dial` up to `boot_wait_s`, else run the caller's
  `fallback` -- **never raises past this call**, satisfying the
  always-optional invariant regardless of which phase failed.

```python
from work_coalescing_singleton import client

result = client.call_with_fallback(
    dial=my_rendezvous_dial,      # () -> (host, port, token) | None
    boot=my_boot_daemon,          # () -> None, best-effort
    boot_wait_s=5.0,
    kind="classify",
    key=project_key,
    payload={"project": project},
    request_deadline_s=2.0,
    fallback=lambda: direct_classify(project),
)
```

## Deliberately NOT in this library

- **Rendezvous / endpoint discovery.** Each consumer already has its own
  (agent-worktrees' session-state file, agent-mcp's own installation-cell
  layout); this library only speaks the wire protocol once an endpoint is
  known.
- **The `single-instance-lease`** that guards against two daemons booting for
  the same cell -- a separate vendored library, composed by the consumer
  around its own boot path.
- **Bounding concurrency across distinct keys** (the "queued, bounded
  concurrency" half of the vision behavior) -- that is inside the consumer's
  `compute` callback, since the right bound is service-specific (a classify
  pass vs. an MCP server connection have nothing in common there).

## Testing

```
cd libs/work-coalescing-singleton
python -m pytest
```

## Vendoring

Like the other shared libs (`single-instance-lease`, `zdd`, `ssh-manager`,
...), once a second consumer adopts this library it is vendored per consuming
plugin at `plugins/<plugin>/libs/work-coalescing-singleton/`. Every copy's
`src/` tree and version must stay byte-identical (enforced by
`tools/check-vendored-libs-sync.py`).
