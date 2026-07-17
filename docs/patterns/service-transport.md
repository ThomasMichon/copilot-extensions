# Pattern: service-transport

**Serves:** *Vision plugin-services* §Behaviors/`minimal-network-exposure`,
`collision-free-endpoints`, `local-first-exposure`, `standalone-reachability`.
**Exemplars:** agent-vault (Unix socket / named pipe), agent-bridge (SSH
reverse-tunnel credential relay; OS-picked forward ports), agent-dispatch.

## Problem

A service and its callers — its own CLI, sibling plugins, agents and tools on
the box — need a channel. The habitual default is to **pin a loopback TCP port**
and hardcode it on both ends. That is the wrong default *even bound to
`127.0.0.1`*: a loopback port is still a **global, OS-arbitrated resource** on a
namespace the service does not own, so it collides with siblings, collides across
the shared `127.0.0.1` a Windows host shares with its WSL guest, and can be held
by an OS reservation (Hyper-V/WinNAT excluded ranges) that refuses the bind with
*no visible listener*. Reserving a specific port does not escape this class — it
*is* this class.

Two questions are often conflated; keep them apart:

- **Transport** — *what kind of channel* a service exposes. **This pattern.**
- **Discovery** — *where that channel currently is*, resolved without a
  hardcoded constant. Owned by
  [local-endpoint-discovery](local-endpoint-discovery.md).

Pick the transport here; advertise and resolve it there.

## Standard approach — the transport ladder

Choose the **highest rung** that covers the reach you actually need. Descend only
when a rung is genuinely unavailable, and say why.

### 1. No channel at all — in-process or stdio

If a caller can reach the capability as a **library import** or a **child process
over stdio**, there is no endpoint to bind, discover, or secure. This is how MCP
servers are wrapped and how the resolver-import shape composes
([a-la-carte-independence](a-la-carte-independence.md)). Zero ports, zero
rendezvous, nothing to collide. Prefer it for CLI→capability calls and for
sibling composition that doesn't need a long-lived socket.

### 2. An OS-native local endpoint the service owns — the default for a daemon

A long-lived service's default endpoint is a **name in a namespace the service
owns**, which opens **no network port**:

- **Linux / WSL:** a **Unix domain socket** under the service's own runtime dir
  (`~/.agent-<x>/run/<x>.sock`). ASGI servers bind it natively (`uvicorn --uds`,
  `hypercorn`), and `httpx` / `aiohttp` reach it with a UDS transport.
  Filesystem permissions on the socket give free, per-user access scoping.
- **Windows:** a **named pipe** (`\\.\pipe\agent-<x>`) with an explicit DACL —
  the idiomatic Windows-local endpoint, with OS-level ACLs (see the caveat for
  the async-HTTP shim it needs).

This rung is immune, by construction, to the port lottery, the host/WSL shared
loopback, and phantom reservations. A WSL guest is itself Linux, so a
guest-resident service uses a UDS natively — the host/guest collision only ever
arises for *TCP*, and this rung avoids TCP.

### 3. An OS-assigned loopback port + rendezvous — only when a socket is unavoidable

Some dependency can only speak TCP (see the caveat). Then bind **`127.0.0.1:0`**,
let the OS hand out a free ephemeral port, and **advertise the resolved port
through the rendezvous file** — never a fixed constant. See
[local-endpoint-discovery](local-endpoint-discovery.md) for the file's schema and
the client resolution order. The port is dynamic and discovered; it stays
local-only. This is strictly a fallback from rung 2, not a peer of it.

### 4. A tunnel over an already-trusted transport — crossing a host or trust boundary

When the caller is on the *other side* of a boundary — a different host, or the
host↔WSL-guest split where a shared loopback would collide — **do not open a new
inbound port.** Forward the service's *local* endpoint across a channel that is
already authenticated and is already the machine's one externally-reachable
surface:

- **SSH port-forwarding** — `ssh -L` (pull a remote local endpoint to here) or
  `ssh -R` (push a local endpoint to a remote), including **streamlocal `-R`**
  to forward a **Unix socket** across the hop rather than a port. This is how the
  credential relay reaches a sandbox and how a remote client reaches a
  host-resident agent: the sensitive endpoint stays bound to loopback/UDS on its
  own side; only the SSH session crosses the boundary.
- **A userspace tunnel (Chisel-style)** — a TCP/HTTP-tunnel binary — when native
  SSH port-forwarding isn't available but an SSH/HTTPS channel is.

The forwarded local port on each end is **OS-assigned and discovered**, not
fixed. The result: the only listener a machine ever exposes beyond a service's
own namespace is **SSH** — and for the truly locked-down, even SSH can sit behind
a tunnel. This rung is always an **opt-in** boundary-crossing mechanism; per the
vision's standing non-goal, **no plugin ever *requires* a tunnel or a broker** to
be installed or reached on its own host.

## The named-pipe / UDS-with-Python caveat

The old reflex — "Python HTTP can't do sockets, so bind TCP" — is mostly a myth
now, but it has one real, platform-specific edge. Be precise:

- **Linux / WSL: UDS is first-class.** `uvicorn --uds`, `hypercorn`, and
  `httpx`/`aiohttp` UDS transports all work. There is no reason to bind loopback
  TCP for a Linux-side service.
- **Windows: `AF_UNIX` exists but async HTTP stacks don't use it.** Windows has
  shipped `AF_UNIX` since 1803 and Python can create such sockets, but
  `asyncio`'s Windows proactor loop has no UDS server support, so
  `uvicorn --uds` does **not** work there. The Windows-native answer is a
  **named pipe**: either a small pipe-server↔ASGI bridge, or a native
  `NamedPipeServerStream` sidecar (the shape agent-vault uses — a .NET/PowerShell
  pipe agent on Windows, a UDS daemon on Linux, one protocol over both).
- **Decision rule.** Rung 2 on **Linux/WSL → UDS** (native, one line). Rung 2 on
  **Windows → named pipe** (idiomatic, needs the small bridge/sidecar). Descend
  to **rung 3** (`127.0.0.1:0` + rendezvous) only when a Windows service's stack
  genuinely can't be given a pipe within the effort available — and treat that as
  a temporary state, not the target.

## Rationale

The ladder makes **"no new port"** the default and a network port an explicit,
*dynamic, discovered* exception. The collision / boundary / reservation failures
that a fixed loopback port invites become **structurally impossible** for the
common case (rungs 1–2) and **bounded and visible** for the rest (rung 3's port
is ephemeral and advertised; rung 4 crosses only over an already-trusted
surface). Keeping transport distinct from discovery lets a service change *how*
it is reached without any caller changing *how it finds it*.

## Migration note

Fixed-loopback services — and the Windows/WSL **"+1" offset** in
[`architecture.md`](../architecture.md)'s ports table — are the **legacy** shape.
Migrate downward: `127.0.0.1:0` + a rendezvous file is a cheap first step that
removes the client-visible constant with no transport rewrite; a UDS (Linux) or
named pipe (Windows) is the target for a daemon; an SSH/streamlocal tunnel
replaces any *cross-boundary* fixed port. The ports table is a transitional
registry, not the destination — the destination is that no such table is needed.

## See Also

- Discovery seam (the rendezvous / port-mapping file):
  [local-endpoint-discovery](local-endpoint-discovery.md)
- Composition (stdio / resolver-import):
  [a-la-carte-independence](a-la-carte-independence.md)
- Intent: [`visions/plugin-services/`](../../visions/plugin-services/README.md)
  (§Behaviors `minimal-network-exposure`, `collision-free-endpoints`,
  `local-first-exposure`, `standalone-reachability`)
- Hub: [`docs/patterns/`](README.md) · Reality:
  [`architecture.md`](../architecture.md) (ports table)
