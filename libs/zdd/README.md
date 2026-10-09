# zdd -- zero-downtime cutover library

Shared active/passive redeploy primitives for Copilot CLI plugins and multi-machine
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

A **watchdog counterpart**, `reap_stale_active`, complements the client-side
self-heal above for a process that polls on its own schedule rather than only
reading the table per-request: it retires an `active` that names a dead pid/port
(promoting a live `previous`, or clearing the table), and separately heals a
table that has **no `active` claim at all** -- the shape a clean shutdown
(`clear_if_owner`) leaves behind when no successor ever publishes itself, which
otherwise strands every consumer indefinitely. Both promotions require the
candidate `previous` to have both a live listener *and* a positive recorded
pid confirmed alive, reducing (though not eliminating -- pid reuse races
remain a residual, narrower risk) the chance an unrelated service that later
reuses the same port gets mistaken for the real daemon.

Key API: `Endpoint`, `read_active_endpoint`, `publish_active`,
`clear_if_owner`, `reap_stale_active`, `routing_table_path`.

Publications for a live PID also record its `process_start_time`, establishing
the process identity when the route is written rather than sampling a potentially
reused PID during later adoption. The three publication APIs accept an optional
spawn-time `process_start_time` baseline and refuse a mismatched or unverifiable
baseline before changing the route. Active/previous endpoints and guarded
publication preserve and compare this optional token.

Legacy routes without the field remain readable. If identity cannot be read,
ordinary publication logs a warning and retains the existing routing behavior;
an identity-bound supervisor must reject that unverified route, not substitute
a fresh token. This field does not itself implement a singleton manager, provide
Windows handle custody, or change listener-based client fallback.

### `zdd.cutover` -- the cutover orchestrator

`CutoverOrchestrator` drives one active/passive cutover: spawn the new daemon on
a fresh port, health-gate it, flip the routing table, drain the old daemon, then
retire it. The sequence is reversible up to an explicit commit point, with
rollback and commit-forward (if the old endpoint is unreachable, it commits to
the healthy new one rather than stranding clients).

### `zdd.diagnostics` -- daemon-health audit + repair

`audit_daemon_health()` and `apply_daemon_health()` let a consumer surface the
field-proven abnormal cutover states uniformly through its own doctor/health
command: duplicate resident daemons, stranded old survivors from an aborted
cutover, never-promoted abandoned passives, and stale superseded generations
that should already have self-retired. Destructive repairs are gated on the
same two rules everywhere: first confirm a validated live owner from the
consumer's lock/routing state, then terminate only through an identity-bound
OS handle tied to the target process's start time.

### `zdd.singleton_manager` -- Linux supervision across cutover and exec

`SingletonManager` claims Linux subreaper ownership before spawning, validates
successors against publication-time identity and ancestry, and watches them
through immutable pidfds rather than numeric-PID liveness. It holds a private
state-directory lease and persists the watched identity, manager identity,
boot ID, and bounded discovery phase in `manager.json`.
Run it in a dedicated manager process: its entire descendant tree, including
helper processes, belongs to this lifecycle. Do not embed it in an unrelated
long-lived process whose other children must survive manager exit.

Supply distinct `config_dir` and `manager_state_dir` paths and a `spawn`
callback returning a process with `pid` and `poll()`. A `resolve_update`
callback may return a new absolute executable/argv when its version marker
changes, or `None` otherwise. The manager polls it while the daemon is alive;
successful exec preserves the manager PID and lease. The new image must enter
the same manager API with the same paths; it recovers the validated watched
process or pending discovery state without invoking `spawn` again.

The spawn callback must preserve manager ancestry and use the adopter's
platform-aware launch primitive. Fresh bootstrap refuses a live incumbent
without same-manager recovery state. On a real crash or an in-process error, cleanup freezes the owned
tree to a bounded fixed point and signals only held descendant pidfds; the
caller must propagate `ManagerResult.exit_code` to the service manager.
This is the manager's service outcome, not transparent child-status forwarding:
even a zero-status child exit without a verified successor means the continuously
supervised service has disappeared and yields a nonzero manager status. Intentional
service stop targets the manager/unit itself, not just its current daemon child.
State/exec/containment failures raise explicitly rather than continuing with
weaker guarantees.

This implementation requires Linux subreaper and pidfd support (provided through
Python or libc). Windows and macOS native backends are explicitly unsupported.
For abrupt manager death, the launcher must retain systemd's
`KillMode=control-group`; no in-process cleanup can run after SIGKILL.
Consumer launch wiring, real systemd validation, Windows ownership/handoff,
and platform deployment are tracked by the
[implementation effort](../../efforts/active/versioned-singleton-manager/README.md).

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

## Vendoring

On `dev`, consumers may reference `libs/zdd` canonically via
`[tool.uv.sources]` with `editable = true` instead of carrying a local
`plugins/<plugin>/libs/zdd` copy. Promotion materializes that canonical
reference back into a real local `libs/zdd/` tree for shipped `main` payloads,
so development stays DRY without weakening the self-contained release payload.

## Development

```bash
uv pip install -e ".[dev]"
pytest
ruff check .
```

`zdd` is pure stdlib and imports nothing from any consuming plugin -- keep it
that way.
