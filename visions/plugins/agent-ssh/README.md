# agent-ssh — Vision

- **Subject:** The **connectivity layer** of the agent fabric — the plugin that
  *provisions and keeps real* the SSH mesh that cross-machine reach rides on,
  rather than merely borrowing whatever SSH profiles happen to already exist.
- **Scope:** leaf (a per-plugin vision under the [agent-fabric](../../agent-fabric/README.md) branch)
- **Status:** Draft
- **Last revised:** 2026-07-22
- **Reality docs:** [`docs/architecture.md`](../../../docs/architecture.md) ·
  the plugin's future `plugins/agent-ssh/docs/`

## Purpose & Intent

The fabric's higher layers all assume they can **reach** another machine over
SSH — the coordination layer sends to a remote agent, a venue provider fronts a
remote host, the ground layer shells out for remote interop. But today that
reach is only ever **borrowed**: the fabric *consumes* whatever SSH config,
keys, and tunnels a human happened to set up by hand. When the mesh isn't wired
— a fresh machine, a rotated key, a torn-down tunnel, a firewall that never
allowed inbound — cross-machine reach simply fails, silently, and the layer
above has no idea the transport underneath it was never real.

**agent-ssh** is the layer that owns making the SSH mesh **real and keeping it
real**. It provisions the SSH substrate (server and client) to a known-good
baseline, **adopts** a machine into a declared mesh, stands up the chosen
**transport** to reach it (a tunnel by default; a direct route only when
deliberately chosen), and continuously keeps a machine's advertised
reachability **honest** — so a machine marked reachable actually is, and the
layers above route on a truthful signal instead of a stale assumption.

The north star: an operator **declares** the machines they want in one mesh, and
agent-ssh makes every declared, in-scope machine actually reachable — installing
or repairing OpenSSH, minting and pinning keys, deriving each machine's SSH
config from the registry, standing up the right transport module, and asserting
liveness — so that "SSH is assumed" becomes "SSH is **provisioned, verified, and
maintained**." Where existing SSH profiles are already good, agent-ssh
**augments** them; it never fights a working setup.

## Concepts & Components

agent-ssh sits **beneath** the fabric's cross-machine capabilities as their
transport floor. It is composed of a small set of concerns, each replaceable
without disturbing the others:

### Machine registry & adoption
A declarative inventory of the machines in the mesh — each with a **role** (SSH
host, client, or both), an environment, and a **reachability declaration** (how
it is reached, and whether it currently is). *Adopting* a machine is the verb
that turns a registry entry into reality: register it, provision what its role
needs, record how it's reached, and hand higher layers a name they can address.
The registry is the **single owning store** of the mesh's reachability; higher
layers *derive* routing from it rather than keeping a second copy.

### SSH substrate (OpenSSH)
Install and manage the OpenSSH **server** and **client** on each machine to a
known-good baseline; own **key** material and **host-key pinning**; and keep each
machine's `~/.ssh/config` mesh entries **derived from the registry**, not
hand-maintained. This is the common floor every transport module rides on.

### Transport modules (pluggable, à la carte)
*How* a client actually reaches a host is a **module**, chosen per machine, so
adding connectivity is installing a module rather than rewiring the core:
- **Direct** — plain SSH to a routable address, only where the network permits
  inbound.
- **Tunnel transports** — reach a host through an identity-gated tunnel when
  direct inbound is blocked (NAT, corporate firewall, cloud-only identity).
  Concrete tunnel providers (e.g. a Dev-Tunnel-based transport, a
  Cloudflare-Tunnel-based transport) are **interchangeable modules** behind one
  reachability contract.
- **Real-user interactive reach** — land as the **actual user** in their own
  interactive session over a persistent tunnel (for attaching to live,
  human-owned sessions), distinct from a headless service transport.
A registry entry **names** which transport carries a machine; the layers above
address the machine the same way regardless.

### Firewall posture (tunnel-first)
Open and verify **only** what a chosen transport actually requires, with a
standing **tunnel-first** recommendation: prefer a zero-inbound tunnel over
opening a listening port, and treat direct/inbound as the deliberate exception.

### Mesh health & reachability assertion
Verify — on demand and/or continuously — that a machine advertised as reachable
**actually is**, and keep the "is this machine reachable?" bit honest. A machine
whose transport has decayed is demoted from the reachable set so higher layers
stop routing to a dead path; a repaired machine is restored.

## Features

### provisioned-not-assumed-transport
The SSH mesh is something agent-ssh **provisions and maintains**, not something
it assumes a human wired. Installing OpenSSH, minting/pinning keys, standing up a
transport, and repairing a decayed one are first-class capabilities of the layer.

### declared-mesh-adoption
An operator **declares** the machines and roles of one mesh; *adopting* a machine
provisions its role and records how it is reached, so a new machine joins the
mesh by declaration rather than by a manual, per-box ritual.

### pluggable-transport-modules
Reachability is delivered by **interchangeable transport modules** (direct,
tunnel-based providers, real-user interactive reach) behind one contract. Adding
or swapping a transport is installing a module; the core and the layers above are
unchanged.

### tunnel-first-connectivity
The layer **recommends and defaults to** zero-inbound tunnel transports, and
manages firewall posture to open only what a deliberately-chosen direct
transport truly requires — minimizing attack surface as the default, not an
afterthought.

### honest-reachability
A machine's advertised reachability is **kept truthful**: verified against the
live transport, demoted when a path decays, restored when it heals — so higher
layers never route on a stale "reachable" claim.

### derived-ssh-config
Each machine's SSH client configuration (host aliases, transport wiring, host-key
pinning) is **derived from the registry**, not hand-edited — so the mesh's wiring
has a single declarative source and stays consistent across machines.

### augments-existing-profiles
Where a working SSH profile already exists, agent-ssh **detects and augments**
it rather than clobbering it — a machine that is already reachable is adopted
as-is, and the layer adds only what is missing.

## Behaviors

### adopt-then-reach
A machine is **adopted** (registered, provisioned for its role, transport
recorded) before the fabric treats it as reachable. Reach is a *consequence* of
successful adoption, not an independent hope.

### reachability-is-asserted-and-verified
The reachable/unreachable state of a machine is **backed by verification**
against its actual transport, not by a hand-set flag that drifts. An unverified
or failed path defaults to **unreachable** (fail safe), so the fabric never
routes into a silent dead end.

### transport-is-a-detail
Which transport carries a machine (direct, one tunnel provider or another,
real-user reach) is an **implementation detail** below the reachability
contract. Higher layers address a machine by its mesh name; swapping its
transport does not change how they reach it.

### least-inbound
The layer prefers the **least inbound exposure** that achieves reach: a tunnel
over an open port, owner-scoped identity gating over broad access. Opening a
firewall port is a deliberate, justified exception — never the default path.

### derive-dont-duplicate
The registry is the **single owning store** of the mesh's reachability. Higher
layers (coordination, dispatch, venue providers) **read and route over** it;
they do not persist a competing copy of who-is-reachable-how. This is the
fabric's *derive-don't-duplicate* rule applied to transport.

### uses-real-identity
Reach is established under the operator's **real identity** and existing identity
provider — not a per-agent or per-mesh service account minted for SSH. Real-user
interactive reach lands as the actual user; headless reach still authenticates as
a real principal. (Where an SSO/IdP alone is insufficient, credentials are the
[agent-vault](../../agent-fabric/README.md) trust layer's concern, not this
layer's.)

## Non-Goals / Boundaries

- **Not the coordination / dispatch layer.** agent-ssh provides **transport** —
  the ability to *reach* a machine over SSH. Creating, messaging, delegating to,
  or recovering remote **agents** rides *on* this layer and belongs to
  agent-bridge / agent-dispatch, not here.
- **Not a venue provider.** Provisioning a CodeSpace or a container and
  presenting its agent to the fabric is the venue providers' territory; agent-ssh
  wires **machine-to-machine SSH reachability**, the substrate a machine venue
  may in turn ride on.
- **Not an account-per-agent or identity provider.** It authenticates the
  operator's real identity through an existing IdP; it does not mint SSH accounts
  per agent, and credential provision beyond SSO is the trust layer's job.
- **Not a general VPN / mesh-networking product.** It wires **SSH** reachability
  specifically, delegating the underlying tunnel technology to interchangeable
  transport modules — it is not a replacement for a network fabric.
- **Not the per-host service model.** *How* the layer's own runtime is deployed,
  supervised, and reached as a machine-local service is the
  [plugin-services](../../plugin-services/README.md) vision's territory; agent-ssh
  builds on that model rather than restating it.
- **Not a specification.** This vision fixes the layer's **role, concerns, and
  guarantees**, not the wiring — it pins no tunnel provider's API, no on-disk
  registry format, no `ssh_config` grammar, and no command surface. Binding detail
  of that kind lives in the reality docs and the plugin's own docs.

## See Also

- Parent vision: [agent-fabric](../../agent-fabric/README.md) — agent-ssh is the
  **connectivity/transport layer** the fabric's cross-machine reach rides on.
- Sibling layer visions: authored under `visions/plugins/<name>/` as they are
  written (e.g. a future `visions/plugins/agent-bridge/`).
- Related vision: [plugin-services](../../plugin-services/README.md) — the
  per-host service model agent-ssh's runtime deploys as.
- Reality docs: [`docs/architecture.md`](../../../docs/architecture.md) and the
  plugin's future `plugins/agent-ssh/docs/`.

## Provenance

<!-- OPTIONAL, non-authoritative. Dated notes on how this vision came to be. Not
     part of the should-be spec; not diffed for deltas. -->

- **2026-07-22** — Initial draft. Intent mined from the operator's observation
  that the fabric's cross-machine layers only ever *borrow* pre-existing SSH
  profiles (agent-worktrees/agent-bridge "support" SSH but assume it is already
  wired), leaving the mesh silently broken when it isn't. Split out **agent-ssh**
  as the dedicated connectivity layer that *provisions and maintains* the SSH
  mesh — OpenSSH substrate, machine adoption/registration, pluggable transport
  modules (direct, tunnel-based providers, real-user interactive reach), a
  tunnel-first firewall posture, and continuous reachability assertion. Placed as
  a leaf beneath agent-fabric by the *derive-don't-duplicate / single-owning-layer*
  rule: the registry is the one owning store of mesh reachability, over which the
  coordination, dispatch, and venue layers route.
