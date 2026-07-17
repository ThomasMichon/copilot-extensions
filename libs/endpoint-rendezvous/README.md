# endpoint-rendezvous

A tiny, dependency-free library that gives a service-bearing Copilot CLI plugin a
**discoverable, collision-free local endpoint** — the shared building block for
moving services off fixed loopback TCP ports.

It implements the **rendezvous (port-mapping) file** convention from
`docs/patterns/local-endpoint-discovery.md` and the transport-selection guidance
in `docs/patterns/service-transport.md`, and it carries the **cutover fallback
ladder** an in-place migration off a fixed port needs.

## Why

Pinning a fixed loopback TCP port collides with sibling services, with the
`127.0.0.1` a Windows host shares with its WSL guest, and with OS reservations
(Hyper-V/WinNAT excluded ranges) that hold an address with no visible listener.
The fix is to **advertise** whatever endpoint a service actually bound and let
clients **discover** it, instead of hardcoding a constant on both ends.

## On-disk format

`<runtime_dir>/endpoint.json`:

```json
{
  "schema": 1,
  "transport": "unix",
  "endpoint": "/home/u/.agent-x/run/x.sock",
  "pid": 48213,
  "started_at": "2026-07-16T22:41:09Z"
}
```

`transport` is one of `unix` (Unix domain socket path), `pipe` (Windows named
pipe name), or `tcp` (`host:port`).

## Service side — advertise on bind

```python
from endpoint_rendezvous import write_endpoint, clear_endpoint, default_runtime_dir

run_dir = default_runtime_dir("agent-x")          # ~/.agent-x/run

# after binding an OS-assigned loopback port:
write_endpoint(run_dir, "tcp", f"127.0.0.1:{port}")
# or a Unix socket / named pipe:
write_endpoint(run_dir, "unix", str(sock_path))

# on graceful shutdown:
clear_endpoint(run_dir)
```

The write is **atomic** (temp file + `os.replace`), so a reader never sees a
half-written record. Call it on every bind — newest bind wins.

## Client side — resolve with a backwards-compatible fallback ladder

```python
from endpoint_rendezvous import resolve, connect_probe, default_runtime_dir

ep = resolve(
    default_runtime_dir("agent-x"),
    override=os.environ.get("AGENT_X_ENDPOINT"),   # explicit operator/env choice
    legacy="tcp:127.0.0.1:9847",                   # the old fixed constant
    probe=connect_probe,                           # optional liveness check
)
# ep.transport, ep.address, ep.source ("env" | "file" | "legacy")
```

Resolution order is **override → rendezvous file (if present and not stale) →
legacy constant**. A not-yet-migrated service (no file) is still reached via the
legacy default; a migrated one is discovered. If nothing resolves, `resolve`
raises `EndpointUnavailable` — it fails loud rather than masking the cause.

Staleness is decided by **evidence**: a recorded `pid` that is no longer alive,
or a supplied `probe` that reports connection refused. With neither signal it is
treated as live (the caller finds out on connect).

## Consumed by

Vendored into the venvs of the service-bearing plugins that migrate off fixed
ports (agent-vault, agent-dispatch, …), the same way `ssh-manager` is vendored.
Pure standard library — no runtime dependencies.

## Test

```
pytest        # from this directory
```
