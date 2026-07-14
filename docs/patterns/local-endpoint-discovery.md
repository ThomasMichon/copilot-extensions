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

- Intent: [`visions/plugin-services/`](../../visions/plugin-services/README.md)
- Hub: [`docs/patterns/`](README.md) · Reality: [`architecture.md`](../architecture.md) (ports table)
