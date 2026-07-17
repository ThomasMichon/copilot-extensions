# Plugin Service Model — Vision

- **Subject:** The copilot-extensions plugin **service model** — how
  installer-deployed plugin runtimes expose, coordinate, and are reached as
  local services on a user's machine.
- **Scope:** branch (links per-plugin child visions as they are authored)
- **Status:** Active
- **Last revised:** 2026-07-16
- **Reality docs:** [`docs/architecture.md`](../../docs/architecture.md) ·
  [`docs/install-contract.md`](../../docs/install-contract.md) · each plugin's
  `docs/architecture.md`

## Purpose & Intent

Each plugin in this suite is an **independently installable addon**. A user
picks whatever subset they want from the marketplace; that is the unit of
adoption, and the service model must honor it. The north star: **whatever you
install just works on its own, and whatever you install *together* composes —
with nothing in between assumed.**

Two forces are always in tension, and the model resolves them in the user's
favor:

- **À-la-carte independence.** No plugin may presume a sibling is installed, that
  a particular other service is running, or that any shared, machine-wide plumbing
  (a reverse proxy, a tunnel broker, a service registry, a central coordinator)
  exists. A lone install is a first-class configuration, not a degraded one.
- **Graceful composition.** When several plugins *are* present, they cooperate —
  discovering and reaching one another — without a mandatory central authority
  and without the user hand-wiring them together.

Success is a suite that feels coherent when fully installed, yet where every
service stands entirely on what its **own** installer deployed. The friction this
vision exists to abolish is the manual deconfliction of shared machine resources
— the hand-maintained port table, the per-platform "+1" workaround, the "which
service owns which address" bookkeeping — that turns adding or moving a service
into a coordination problem.

## Concepts & Components

- **Plugin runtime** — the self-contained venv + binstub a runtime plugin's own
  installer deploys under `~/.agent-*`, per the shared **install contract**. The
  runtime, not a git checkout, is what executes.
- **Service-bearing plugin** — a plugin whose runtime includes a **long-lived
  local service** (an always-on daemon), as distinct from an on-demand CLI or a
  payload-only (skills/extension) plugin.
- **Local endpoint** — the machine-local address at which a running service is
  reached by its clients (its own CLI, other plugins, agents on the box). The
  vision constrains the *guarantees* of this endpoint, not its mechanism.
- **Endpoint discovery (rendezvous)** — how a client finds the *current*
  endpoint of a service without a human-managed constant. Discovery is the seam
  that makes endpoints collision-free and relocatable.
- **Lifecycle supervision** — the platform-native mechanism that starts, keeps
  alive, and restarts a service (a per-user OS service), so a service's presence
  does not depend on an interactive session.
- **Install contract** — the uniform deploy/version/footprint agreement every
  runtime plugin follows, so services deploy, update, and are audited the same
  way. See [`docs/install-contract.md`](../../docs/install-contract.md).
- **Per-plugin visions** — concrete leaves under `visions/plugins/<name>/` refine
  this model for a specific service. Linked from here as they are authored.

## Features

### self-contained-runtime
Every runtime plugin owns a complete, standalone runtime (venv + binstub +
service) that its own installer deploys and updates. Nothing a service needs to
run is borrowed from a sibling plugin or from a git checkout of this repo.

### discoverable-local-endpoint
A client reaches a service by **resolving the service's current endpoint from
the service's own runtime state**, not by hardcoding a constant it must keep in
sync by hand. Installing, moving, or rebinding a service does not require editing
its clients.

### a-la-carte-installability
Any subset of plugins is a supported configuration. A single-plugin install is
fully functional; adding or removing a plugin never breaks an unrelated one and
never requires reconfiguring the survivors.

### platform-native-lifecycle
A service is supervised by the host OS's own per-user service facility, giving
auto-start, keep-alive, and restart-on-failure on every supported platform
(Windows and Linux/WSL) through one coherent contract.

### graceful-composition
When multiple services are present they discover and use one another's optional
capabilities without a mandatory central broker and without user-authored wiring.
Cooperation is opportunistic, not obligatory.

### uniform-deploy-contract
All service-bearing plugins share one deploy/update/version footprint (the
install contract), so a user — or an automated fleet — reasons about, audits, and
upgrades every plugin service the same way.

## Behaviors

### collision-free-endpoints
Two plugin services — and the *same* service running on both sides of a shared
network boundary (e.g. Windows and its WSL guest) — never contend for one
address. Deconfliction is **structural**, achieved by construction, never by a
human maintaining a registry of fixed ports or applying per-platform offsets. A
new service can be added, or an existing one relocated, without anyone arbitrating
addresses.

### endpoint-discovered-not-assumed
A client always learns *where* a service currently is from the service itself,
so a service that binds a different address than last time is still reached with
no client change. There is no ambient, assumed constant that a mismatch can
silently break.

### standalone-reachability
A service is reachable using **only** what its own installer put on the machine.
Reaching it never depends on an external proxy, tunnel, mesh, or registry being
installed, configured, or running.

### degrade-gracefully
Absent an optional peer or coordinator, a service still performs its own local
function; optional cross-service features simply stay dark until the peer is
present. A missing sibling degrades a feature, never the whole service.

### local-first-exposure
A service is machine-local by default — reachable by processes on the same host
and no wider. Exposing a service beyond the local machine is an explicit,
opt-in act, never the default posture.

### minimal-network-exposure
A service prefers a transport that opens **no network port at all** — an
OS-native local endpoint (a Unix domain socket or a named pipe, in a namespace
the service owns) — over binding a loopback TCP port, *even one bound only to
`127.0.0.1`*. A network port is a last resort, not a default: when one is
genuinely required, it is an **OS-assigned ephemeral** port advertised through
discovery, never a fixed or well-known one. Crossing a host or trust boundary —
including the shared-loopback boundary between a host and its WSL guest — is done
by an explicit, opt-in **tunnel layered over an already-trusted transport**, so
the only surface a service ever exposes beyond its own namespace is the one the
operator deliberately chose. The steady-state ideal is that starting a service
adds **zero new listening ports** to the machine.

### fail-loud-on-endpoint-error
When a service cannot claim or reach its endpoint, it surfaces the **real,
literal cause** (what actually blocked the address) rather than masking it or
silently degrading — so the failure is diagnosable instead of mysterious.

## Non-Goals / Boundaries

- **No shared-infrastructure dependency.** The suite does **not** assume — and a
  plugin service **must not** require — an external reverse proxy, tunnel broker,
  service mesh, load balancer, or centralized service registry in order to be
  installed or reached. A downstream deployment *may* layer centralized routing
  **on top** (for example, a facility fronting these services with a tunnel and a
  reverse proxy for remote access or unified naming), but that is always a
  **consumer's** additive choice, never a prerequisite baked into the plugin.
- **Not a multi-host clustering / orchestration system.** This model governs
  **per-host, machine-local** services. Cross-host reach between agents is a
  separate transport concern owned by the mesh plugin, not this vision.
- **No mandatory central coordinator.** The suite does not require one always-on
  arbiter process that other plugins depend on; composition is peer-wise and
  optional.
- **Not an endpoint-mechanism specification.** This vision fixes the *guarantees*
  of a local endpoint (discoverable, collision-free, local-first, standalone) but
  deliberately does **not** pin the mechanism — a Unix domain socket, a named
  pipe, a rendezvous port file, or loopback TCP are all acceptable realizations.
  Binding detail of that kind belongs to reality docs or a future
  `specifications` layer, not here.

## See Also

- Parent vision: [visions index](../README.md)
- Child visions: none yet (per-plugin service visions will live under
  `visions/plugins/<name>/`)
- Reality docs: [`docs/architecture.md`](../../docs/architecture.md) (install
  topology, the ports table, communication paths) ·
  [`docs/install-contract.md`](../../docs/install-contract.md) · per-plugin
  `docs/architecture.md`

## Provenance

- **2026-07-13** — Initial authoring. Intent mined from the recurring
  static-port coordination pain across the service-bearing plugins (the
  hand-maintained loopback-port table in `docs/architecture.md` and the
  per-platform Windows/WSL offset convention), crystallized during an incident
  where a shared-loopback port collision between a Windows-host and a WSL-guest
  coordinator proved the "fixed port, deconflicted by hand" approach brittle.
  The vision generalizes the fix — *discoverable, collision-free, standalone
  local endpoints* — rather than pinning any one mechanism.
- **2026-07-16** — Extended with the **minimal-network-exposure** behavior.
  Sharpens the earlier local-first posture from "don't expose *beyond* the host
  by default" to "prefer to open **no network port at all**, even on loopback."
  Motivated by loopback-TCP binds colliding with, or being phantom-reserved by,
  standard OS components (Hyper-V/WinNAT excluded-port ranges, the WSL mirrored-
  networking shared `127.0.0.1`) — a class of failure a port *reservation* can't
  escape but an OS-native local endpoint sidesteps entirely. Sanctions
  tunnel-over-trusted-transport as the opt-in boundary-crossing mechanism,
  consistent with the standing non-goal that no such tunnel is ever *required*.
  Realized by the `service-transport` and `local-endpoint-discovery` patterns.
