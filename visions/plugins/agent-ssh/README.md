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

The layer's **product** is a set of **SSH profiles keyed by machine name** — the
interface the rest of the fabric already speaks. The ground and coordination
layers (and an operator's own machine-aware tooling and skills) *consume* a
machine's profile **by name** to reach it; agent-ssh is the *producer* that makes
those profiles exist, wires each to the right transport, and keeps it honest. So
long as a machine's profile exists and is truthful, the consumers already work —
agent-ssh's job is to **guarantee that precondition**, not to re-plumb the
consumers. (This is exactly why a machine-name-aware SSH *skill* and this
provisioning *plugin* are two halves of one concept: one consumes the profiles,
the other produces them.)

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
known-good baseline; own **key material** (lifecycle below) and **host-key
pinning**; and keep each
machine's `~/.ssh/config` mesh entries **derived from the registry**, not
hand-maintained. This is the common floor every transport module rides on.

### Key lifecycle (mint · store · distribute)
The layer owns the **full lifecycle of SSH key material** — **minting** client
and host keys, **storing** them securely, and **distributing** the public
keys / host-key pins the mesh needs so a freshly-adopted machine trusts and is
trusted by its peers. Storage and distribution **prefer the fabric's trust
layer** where present, **falling back to a durable, operator-owned store** so key
material survives a machine rebuild and propagates without manual copy-paste.
Private keys stay under their owner's control; only public halves and host-key
pins are distributed.

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

A module conforms to a stable **transport-provider contract** — the core owns the
contract (and SSH-profile creation + validation); a module supplies only its
transport-specific pieces. Because of that, **modules need not live in this
plugin**: a transport may be shipped by a separate provider plugin — even from a
different marketplace, maintained by a different owner — that registers against the
contract. Each module writes **only its own** managed profile fragment, so several
transports **coexist on one client** (a machine reached over one transport, its
peer over another) without any module owning the whole SSH config.

### Firewall posture (tunnel-first)
Open and verify **only** what a chosen transport actually requires, with a
standing **tunnel-first** recommendation: prefer a zero-inbound tunnel over
opening a listening port, and treat direct/inbound as the deliberate exception.

### Mesh health & reachability assertion
Verify — on demand and/or continuously — that a machine advertised as reachable
**actually is**, and keep the "is this machine reachable?" bit honest. A machine
whose transport has decayed is demoted from the reachable set so higher layers
stop routing to a dead path; a repaired machine is restored.

### Mesh introspection & derived roster
Reaching a machine is only the *floor*; the fabric also needs to know **what a
reachable machine offers** — which repos are checked out on it and where, and
which fabric runtimes it already hosts — so the layers above can address the
right agent without a second, hand-maintained map. agent-ssh can **introspect a
reachable target live**: shell in over the very transport it provisioned and
read, *by convention*, what the machine itself already declares — its own
per-machine **repo registry** (the checkouts and their locations, and which back
an agent), whether the coordination and worktree runtimes are installed, and the
by-convention config that names each repo's purpose. From connectivity **×** each
machine's **live** repo registry, the fabric's addressable **agent roster is
derived**, not separately authored: a machine's own checkouts *are* the list of
agents reachable on it. Introspection is **report-first** — it surfaces what a
target actually has before anything is written — and **adoption is the explicit
follow-on** that records the finding into the mesh. The machine remains the
single owning store of *its own* locations; the fabric reads them at query time
rather than copying them into a central list that will drift.

## Features

### provisioned-not-assumed-transport
The SSH mesh is something agent-ssh **provisions and maintains**, not something
it assumes a human wired. Installing OpenSSH, minting/pinning keys, standing up a
transport, and repairing a decayed one are first-class capabilities of the layer.

### live-machine-introspection
Invoked locally against a reachable target, agent-ssh **explores** it over SSH
and reports, **by convention**, what the machine offers the fabric: the repos it
has checked out and **where** (from the machine's own repo registry, the source
of truth for its locations), which of those **back an agent**, whether the
fabric's coordination and worktree runtimes are installed, and the in-repo config
that names each repo's purpose. Exploration is **read-only by default**; recording
a finding into the mesh is an explicit, separate **adopt** step — so probing a
machine never silently mutates the registry.

### derived-agent-roster
The fabric's addressable agent roster is **derived** from *connectivity* × *each
machine's live repo-checkout locations* × *in-repo repo→purpose config* — not
maintained as a separate, hand-authored list that a consumer must be told out of
band. A machine's own checked-out, agent-backing repos **are** the agents
reachable on it; the roster falls out of the two things that are already true
(the machine is reachable, and it has repo X checked out at path P) plus the
repo's declared purpose. This removes the class of failure where a machine is
fully reachable and set up, yet unaddressable because a separate roster binding
was never wired.

### declared-mesh-adoption
An operator **declares** the machines and roles of one mesh; *adopting* a machine
provisions its role and records how it is reached, so a new machine joins the
mesh by declaration rather than by a manual, per-box ritual.

### pluggable-transport-modules
Reachability is delivered by **interchangeable transport modules** (direct,
tunnel-based providers, real-user interactive reach) behind one contract. Adding
or swapping a transport is installing a module; the core and the layers above are
unchanged. A module may even be an **out-of-repo provider plugin** — a different
owner, a different marketplace — that registers against the contract; the core need
not ship every transport.

### transport-provider-contract
The core's durable **product** is the **transport-provider contract** plus
SSH-profile **creation + validation**; a concrete transport is a **conforming
provider**. This lets the set of transports grow — and be owned and shipped
**independently** — without changing the core or its consumers, while every provider
still yields the same machine-name-keyed profile behind the one reachability
contract.

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
has a single declarative source and stays consistent across machines. These
**per-machine profiles, keyed by machine name, are the contract** the fabric's
consumers rely on: producing and maintaining them so a machine is reachable
**by its name** is agent-ssh's core deliverable.

### managed-key-lifecycle
SSH key material is **minted, stored, and distributed** by the layer, not
hand-managed: adopting a machine provisions its keys and propagates the public
keys / host-key pins the mesh needs, with **secure storage** (the trust layer,
or a durable fallback) so keys survive rebuilds and reach every machine that
needs them — while private halves never leave their owner's control.

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
layers (coordination, dispatch, venue providers) **read and route over** it —
reaching a machine through its **derived per-machine profile, by name** — and do
not persist a competing copy of who-is-reachable-how, nor re-derive connectivity
themselves. This is the fabric's *derive-don't-duplicate* rule applied to
transport.

### locations-live-from-the-machine
A machine's actual repo-checkout locations are read from **that machine's own
repo registry at query time** — the machine is the source of truth for where its
own checkouts live. The fabric does **not** copy those paths into a central store
that drifts; in-repo config may declare an *expected* location, but the mesh's
**live** answer wins. This is *derive-don't-duplicate* applied to repo locations:
the same rule that keeps reachability honest keeps the derived roster honest.

### explore-before-adopt
Introspecting a target is **read-only first**: exploration reports what a machine
actually offers the fabric before anything is written. Turning a finding into a
recorded mesh/registry entry is an explicit **adopt** step the operator (or an
automation) invokes deliberately — so discovery never silently mutates the
declared mesh, and a probe of an untrusted or transient target leaves no residue.

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
  agent-bridge / agent-dispatch, not here. The derived roster is a case of this
  seam, not an exception to it: agent-ssh surfaces the **inputs** a roster is
  derived from (reachability, each machine's live repo-checkout locations, the
  in-repo repo→purpose config) and the introspection to gather them; the
  coordination layer **derives its addressable roster** from those inputs and
  owns the agents themselves. agent-ssh does not create, message, or manage an
  agent.
- **Not a venue provider.** Provisioning a CodeSpace or a container and
  presenting its agent to the fabric is the venue providers' territory; agent-ssh
  wires **machine-to-machine SSH reachability**, the substrate a machine venue
  may in turn ride on.
- **Not an account-per-agent or identity provider.** It authenticates the
  operator's real identity through an existing IdP; it does not mint SSH
  *accounts* per agent. It **does** own the SSH **key lifecycle** (mint / store /
  distribute), but leans on the fabric's **trust layer** for secret *storage*
  rather than inventing its own vault.
- **Not required to ship every transport in-repo.** The core owns the
  transport-provider **contract** and SSH-profile creation + validation; concrete
  transports may live in **separate provider plugins** — audience-appropriate
  marketplaces, independent owners — that register against the contract. The core is
  not the sole home of transport implementations.
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
- **2026-07-22** — Sharpened the **producer/consumer seam** and added the **key
  lifecycle**. Made explicit that the layer's *product* is **SSH profiles keyed
  by machine name** — the interface consumers (the ground/coordination layers and
  a machine-name-aware SSH *skill*) already speak — so a machine-aware *skill*
  (consumer) and this provisioning *plugin* (producer) are two halves of one
  concept. Added `mint · store · distribute` key-material ownership, preferring
  the trust layer for secret storage with a durable operator-owned fallback.
  Mined from operator clarification.
- **2026-07-22** — Made the **transport-provider extension point** explicit: the
  core owns the contract + SSH-profile creation/validation, and transports may be
  **out-of-repo provider plugins** (different owners/marketplaces) registering
  against it — added §Features/`transport-provider-contract`, extended
  `pluggable-transport-modules`, the Transport-modules concept, and a Non-Goal.
  Mined from the operator's multi-part split (upstream core + a per-audience
  transport plugin each) and validated against a staged draft proving fragment
  coexistence on one client.
- **2026-07-22** — Extended past *reach* into **knowing what a reachable machine
  offers**: added the **Mesh introspection & derived roster** concept, the
  `live-machine-introspection` and `derived-agent-roster` features, and the
  `locations-live-from-the-machine` and `explore-before-adopt` behaviors. Intent
  mined from the observation that the fabric's coordination layer needed a
  separately hand-maintained binding to know which repos back agents on which
  machines — so a machine could be fully reachable and provisioned yet
  unaddressable because that roster map was never wired. Grounds the roster in two
  facts already true (the machine is reachable; it has a repo checked out that
  backs an agent) plus the repo's declared purpose, and reads each machine's own
  repo registry **live** for locations rather than duplicating them. Extends the
  existing `derive-dont-duplicate` rule from reachability to repo-locations, and
  `declared-mesh-adoption` with a discover-then-adopt path. Confirmed design
  posture (wrap existing connectivity; live-query locations; report-then-adopt).
