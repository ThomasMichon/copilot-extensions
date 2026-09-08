# zdd -- zero-downtime cutover library

Shared active/passive redeploy primitives for Copilot CLI plugins and facility
services. Extracted from agent-bridge (which proved the design in production) so
multiple consumers reuse one implementation instead of reinventing it.

- **Distribution:** `agent-zdd` (the `agent-` prefix avoids PyPI
  dependency-confusion; the package is never published to an index -- consumers
  install it from a local path or a pinned Git ref).
- **Import module:** `zdd`

## What's inside

### `zdd.routing` -- the routing table (no proxy)

A file-based, client-read routing table (`active.json`) that decouples *which
port is live* from static config. Short-lived clients re-read it every
invocation; the daemon publishes its endpoint on startup and flips
`active`/`previous` atomically on cutover. Readers self-heal: a dead `active`
falls back to `previous`, then to the caller's static config.

Why a table rather than a front proxy: a proxy holding a stable port is itself a
long-lived process you must update, which re-introduces the very downtime it was
meant to remove (and demands socket hand-off between proxy generations --
hardest on Windows). A file has no process to update.

Key API: `Endpoint`, `read_active_endpoint`, `publish_active`,
`clear_if_owner`, `routing_table_path`.

### `zdd.cutover` -- the cutover orchestrator

`CutoverOrchestrator` drives one active/passive cutover: spawn the new daemon on
a fresh port, health-gate it, flip the routing table, drain the old daemon, then
retire it. The sequence is reversible up to an explicit commit point, with
rollback and commit-forward (if the old endpoint is unreachable, it commits to
the healthy new one rather than stranding clients).

## Consumer contract

Every side-effecting collaborator is **injected**, so a consuming service stays
in control of its own process/health/drain semantics:

```python
from zdd.cutover import CutoverOrchestrator

orch = CutoverOrchestrator(
    config_dir,                        # where active.json lives
    bind="127.0.0.1", version="1.2.3",
    spawn_passive=lambda port: ...,    # start the new slot -> handle(.pid/.terminate/.poll)
    health_check=lambda host, port: ...,   # probe the new slot's readiness -> bool
    make_client=lambda base_url: ...,  # -> client exposing drain/undrain/shutdown[/adopt_relay]
    pick_free_port=lambda: ...,        # -> int
)
result = orch.run(health_timeout=60, drain_timeout=300, force=False)
```

The consumer additionally implements its own drain endpoint + semantics (when is
it safe to retire the old daemon?) and an **edge adapter** that makes its clients
follow the table -- short-lived clients re-read `active.json` directly; a service
behind a fixed external port (e.g. reached through a reverse tunnel) instead has
a hop watch the table and re-point at the live port.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

`zdd` is pure stdlib and imports nothing from any consuming plugin -- keep it
that way.
