# Pattern: local-endpoint-discovery

**Serves:** *Vision plugin-services* §Behaviors/`collision-free-endpoints`,
`endpoint-discovered-not-assumed`, `local-first-exposure`.
**Exemplars:** agent-dispatch, agent-bridge, agent-vault.

## Problem

A plugin service needs a local endpoint its clients (its own CLI, sibling
plugins, agents on the box) can reach. The naïve approach — **pin a fixed
loopback TCP port** and hardcode it in both server and clients — is where the
suite has repeatedly bled:

- **Sibling contention.** Two services (or two versions) both want a "well-known"
  port; the second loses.
- **The WSL/Windows shared namespace.** Under WSL mirrored networking, a WSL
  guest and its Windows host share one `127.0.0.1`. A guest service and a host
  service on the same fixed port collide even though they're "different machines."
- **Phantom reservations.** The OS network stack (ephemeral ranges, Hyper-V/HNS
  reservations) can hold a fixed port with *no visible listener*, so a bind fails
  for reasons a client can't see.

Papering over this with a **hand-maintained port table** plus a per-platform
**"+1" offset** (host `N`, guest `N+1`) is the anti-pattern: it is manual
deconfliction that doesn't generalize past two environments and turns "add a
service" into a coordination problem.

## Standard approach

**Discover the endpoint; don't assume it.** In preference order:

1. **Prefer an OS-native local endpoint over a TCP port.** A single-host service's
   default endpoint should be a name in a namespace the service *owns* — a Unix
   domain socket (`~/.agent-<x>/…`) or a Windows named pipe — not a loopback TCP
   port. This sidesteps the port lottery, the shared-namespace collision, and the
   phantom-reservation class entirely, and adds free permission scoping.
2. **If TCP is required, bind an OS-assigned port and advertise it.** Bind `:0`,
   let the OS pick a free port, then **write the resolved endpoint to the
   service's own runtime state** (an endpoint file under `~/.agent-<x>/`). No
   fixed constant to collide.
3. **Resolve, in the client, from the service — not a hardcoded constant.**
   Resolution order: an explicit operator override (env) → the service's
   advertised runtime endpoint → a documented default. A service that moved or
   rebound is still reached with no client edit.

**Local-first, opt-in wider.** Default to machine-local reach; exposing a service
beyond the host is an explicit, deliberate act, never the default.

**Fail loud.** If the endpoint can't be claimed, report the *literal* cause (what
actually holds the address), not a masked "service unavailable."

## The rendezvous file (a.k.a. port-mapping file)

The **rendezvous file** is the discovery seam: a small, well-known, machine-local
file a service **writes when it binds** and every client **reads to find it**. It
is the durable, cross-tool convention that replaces a hardcoded constant — the
same shape the wider ecosystem calls a "port-mapping" or "lock/endpoint" file
(e.g. a dev server's `.port`, a browser's `DevToolsActivePort`).

**Location — a name derived from identity, not a searched-for guess.** The file
lives at a fixed *relative* path inside the service's own runtime dir, so any
client computes the path from the service name with no lookup:

```
~/.agent-<x>/run/endpoint.json
```

**Contents — the transport, not just a port.** Record enough that a client can
*connect* and *trust it's current*, and keep every value ASCII/JSON-parseable:

```json
{
  "schema": 1,
  "transport": "unix" | "pipe" | "tcp",
  "endpoint": "/home/u/.agent-x/run/x.sock" | "\\\\.\\pipe\\agent-x" | "127.0.0.1:52731",
  "pid": 48213,
  "started_at": "2026-07-16T22:41:09Z"
}
```

- `transport` names the mechanism (see [service-transport](service-transport.md))
  so a client picks the right connector instead of assuming TCP.
- `endpoint` is the concrete address for that transport.
- `pid` + `started_at` let a client detect a **stale** file (no such process, or
  the socket no longer accepts) and fail loud rather than dial a ghost.

**Write/read discipline.**

- **Write atomically, on every bind.** Write a temp file in the same dir and
  `rename()` it over the target, so a reader never sees a half-written record.
  Rewrite it each start (the port/PID change); the newest bind wins.
- **Own the cleanup, tolerate the crash.** Remove the file on graceful shutdown;
  a client must still treat a *present-but-stale* file (dead `pid`, refused
  connection) as "not running," because a crash skips cleanup.
- **Never require a central registry.** Each service owns *its own* file under
  *its own* runtime dir. There is no shared directory a service must register
  with — discovery stays peer-wise and à-la-carte (no single coordinator to
  install or keep alive).

## Rationale

Endpoints become collision-free **by construction** rather than by a human
arbitrating addresses — which is the vision's invariant. Discovery decouples
"where a service is" from "what a client hardcodes," so relocation, the WSL
boundary, and OS reservations stop being client-visible failures.

## Migration note

Plugins still on a fixed loopback port (and the Windows/WSL "+1" convention in
`architecture.md`'s ports table) are the **legacy** shape. New services adopt
discovery from the start; existing ones migrate toward it. The ports table is a
transitional registry, not the target state — the target is that no such table is
needed.

## See Also

- Transport choice (which channel to expose in the first place):
  [service-transport](service-transport.md)
- Intent: [`visions/plugin-services/`](../../visions/plugin-services/README.md)
- Hub: [`docs/patterns/`](README.md) · Reality: [`architecture.md`](../architecture.md) (ports table)
