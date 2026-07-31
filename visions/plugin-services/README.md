# Plugin Service Model — Vision

- **Subject:** The copilot-extensions plugin **service model** — how
  installer-deployed plugin runtimes expose, coordinate, and are reached as
  local services on a user's machine.
- **Scope:** branch (links per-plugin child visions as they are authored)
- **Status:** Active
- **Last revised:** 2026-07-30
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

### immutable-versioned-runtime
A runtime install is **immutable**: once a version's venv is built it is never
edited in place. A new version is installed **beside** the old one (its own
directory), and the active version is selected **atomically** (a `current`
junction/symlink swap), never by mutating a shared venv. Switching versions —
forward or a rollback — is a selection, not a rewrite; a running service keeps
serving its own immutable files until it is cut over or retired, and two live
versions are reconciled by drain-and-cutover + shared routing/port state rather
than by racing to overwrite one install. This makes a concurrent-update
corruption (two installers mutating one venv → duplicate/broken daemons)
impossible by construction, and rollback a swap rather than a rebuild. The
immutable, versioned, junction-selected install is the service's **executable
logic**; its **durable state** (databases, indexes, queues, anything the service
must not lose) lives in a **separate location that a version swap never touches**,
so cutting over or rolling back the runtime is safe by construction. Replacing a
running version is governed by *zero-downtime-cutover* below.

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

### self-provisioning-runtime
**Enabling** a runtime plugin is the whole action a user takes — its runtime
then **provisions itself**. Whenever a session starts on a machine where the
plugin is enabled (and, for a machine-scoped runtime, *permitted*), the runtime is
installed if absent and **reconciled to match its enabled payload version** if it
has drifted — with **no manual install step** and no dependence on one *particular*
sibling being the launcher. Provisioning is **idempotent, version-keyed** (a no-op
once already matched), **throttled**, **gated** to the machines a runtime belongs
on, and **opt-out-able**, and it never blocks or slows a session that is already
current. "Enabled" is the user's whole intent; "installed, running, and
version-matched" is the model's job — the same self-healing that keeps a runtime
immutable-versioned (above) also brings a *missing* runtime into existence.

### graceful-composition
When multiple services are present they discover and use one another's optional
capabilities without a mandatory central broker and without user-authored wiring.
Cooperation is opportunistic, not obligatory.

### version-skew-tolerant-contracts
Because every plugin updates on its **own schedule**, the parties to a live
interaction may be at **different versions** at the same moment — a client newer
than the service it calls, a service newer than a peer it discovers, a running
daemon lagging its own installed payload. The model treats this **version skew as
a normal steady state, not a fault**: every live communication contract between
services — and between a service and its own clients — is **explicitly versioned
and backwards-compatible within a declared tolerance**, so composition keeps
working across a support window of versions without a lockstep upgrade. This is
the runtime-communication counterpart to the install contract's **config-schema
migration**: just as a newer runtime reads and migrates older on-disk config
rather than demanding a matching writer, a newer endpoint understands older
callers — and an older endpoint tolerates a newer caller's additive requests —
rather than demanding a matched peer. Convergence (reconcile, drain-and-cutover)
merely *reduces* skew when convenient; it is never a **precondition for
correctness**. The suite is correct **while** skewed.

### uniform-deploy-contract
All service-bearing plugins share one deploy/update/version footprint (the
install contract), so a user — or an automated fleet — reasons about, audits, and
upgrades every plugin service the same way.

### install-adopt-boundary
Two lifecycle verbs, two scopes, never crossed. **Install/update** touches only
**machine-local** content — it deploys and updates a plugin's own runtime and
local config, and may migrate that config's *schema*, but it never changes the
user's chosen *behaviors* and never alters a **repo** (its committed config or its
git hooks). **Register/adopt** is the only verb that mutates a repo: it takes the
user's preferences and wires the repo up — writing repo and machine-local config
and injecting/validating in-repo git hooks. Because you only *adopt* a repo you
own, that mutating power is confined to owned repos by construction; a repo you
merely contribute to is never adopted, so its git is never touched.

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

### interoperate-across-version-skew
Two composed services at different versions still interoperate within their
contract's declared tolerance: a newer caller reaches an older endpoint, and a
newer endpoint serves an older caller, each **degrading a version-gated feature
gracefully** — negotiating shared capability instead of assuming a matched build
— rather than erroring. This extends *degrade-gracefully* from an **absent** peer
to a **present-but-skewed** one. A contract evolves only by **tolerant, additive
change**: unknown fields are ignored, existing fields keep their meaning, and a
breaking change carries a **deprecation window** across which both the old and the
new form are honored — so an endpoint, its discovery handshake, and its wire
protocol each advertise a version and a supported range, never a single required
match. A version mismatch that falls **outside** the tolerance
*fail-loud-on-endpoint-error*'s the real version gap, never silently misbehaves.

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

### install-leaves-repos-unaltered
Running install or update never changes a repo: no commit, no edit to the repo's
committed config, no git-hook injection. At most it migrates machine-local config
schema and *warns* about a stale or deprecated repo convention it observes. Any
repo-altering effect appears only after an explicit adopt / re-adopt.

### zero-downtime-cutover
Replacing a running service with a new version is **zero-downtime** for both the
**requests** it is serving and the **scheduled or background work** it owns. A
cutover **health-gates** the new version on a fresh endpoint, flips a
client-followed **routing record atomically**, then **drains** the old version —
letting in-flight requests finish and **handing off queued/scheduled work** to the
new version rather than dropping it or running it twice — before the old version
retires. The switch is **reversible up to a commit point**: roll back to the old
version, or commit forward to the healthy new one if the old endpoint is already
gone, rather than stranding clients. A version swap therefore never drops a
request, double-runs a scheduled job, or opens a window with no live service.
*How* the routing record and drain are implemented (a shared cutover primitive) is
spec-level, not fixed here.

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

- **2026-07-28** — Added the **immutable-versioned-runtime** behavior: a runtime
  install is never mutated in place; versions install side-by-side and the active
  one is selected by an atomic `current` junction/symlink swap, with concurrent
  live versions reconciled by drain-and-cutover rather than by overwriting a
  shared venv. Motivated by a class of failures where parallel installers mutating
  one venv spawned duplicate/broken daemons, and by a running daemon silently
  lagging its installed version. Realized by the versioned-runtime primitive
  (`versioned_runtime.py`); tracked in dotfiles #581 (structural successor to the
  #533 incremental lag fixes, root-cause fix for #123).

- **2026-07-29** — Added the **zero-downtime-cutover** behavior and sharpened
  **immutable-versioned-runtime** to separate the immutable, junction-selected
  *executable logic* from *durable state* that a version swap never touches.
  Generalizes the service model so replacing a running version is zero-downtime
  for both in-flight **requests** and the **scheduled/background work** a service
  owns — health-gate the new version, flip a client-followed routing record
  atomically, drain and hand off queued/scheduled work, then retire the old
  version; reversible up to a commit point. Mined from an operator directive to
  give service-bearing plugins ZDD/cutover for scheduled tasks and requests, with
  durable data (e.g. a search index) living outside the swappable runtime.
  Realized at intent level by the shared `zdd` cutover library (routing table +
  `CutoverOrchestrator`).

- **2026-07-29** — Added the **version-skew-tolerant-contracts** feature and the
  **interoperate-across-version-skew** behavior. Mined from an operator directive
  that plugin payloads now land **independently and asynchronously** — while one
  host was being brought to latest, the marketplace/anchor advanced through
  several dev builds within minutes from concurrent work, and a running daemon
  routinely lags its own installed payload (the whole #533 lineage). The
  conclusion: the service model must **allow** skew and be robust to it, not race
  it away. The convergence machinery (running-aware reconcile + drain-and-cutover
  from #533; immutable-versioned runtime from #581) is demoted from a correctness
  *precondition* to a convenience that merely reduces skew — correctness itself is
  moved into the **communication contracts**, extending the install contract's
  existing config-schema-migration discipline to **live communication**: every
  contract, endpoint, and protocol carries an explicit version and a
  backwards-compatible tolerance window (tolerant reader, additive evolution,
  deprecation windows, capability negotiation). Tracked in dotfiles #632 (the
  vision→reality delta: inventory and version the live wire contracts — HTTP,
  ACP-over-WS, the endpoint/rendezvous handshake, cross-plugin relay/provider
  calls, and the fabric coordination contract).

- **2026-07-30** — Added the **self-provisioning-runtime** feature: enabling a
  runtime plugin auto-installs and version-reconciles its runtime at session
  start, with no manual install step. Mined from a verification pass on the
  service model against reality. The launch-time payload+runtime reconciler
  (`runtimeScope` + `agent-worktrees reconcile-plugins`) already realizes *part*
  of this — but only via the **agent-worktrees worktree launcher** (so a session
  not launched through it, or a machine without agent-worktrees, never
  self-provisions), and a `machine-gated` runtime is **skipped** wherever a gate
  manifest is absent. The intent generalizes that partial mechanism: *any* enabled
  runtime self-provisions on *any* session start, gated only by where it is
  permitted. Tracked as the vision→reality delta in dotfiles (the
  *self-provisioning-runtime* effort). This pass also **reaffirmed** (did not
  change) **minimal-network-exposure** / **discoverable-local-endpoint**: the
  service-bearing plugins still bind **fixed, well-known default ports**
  (agent-bridge 9280/9281 + relay 9857, agent-dispatch 9847/9331) despite the
  rendezvous infrastructure already existing — the standing intent is OS-assigned
  ephemeral (or socket/pipe) endpoints advertised through discovery, never fixed;
  carved as the *dynamic-endpoints* delta in dotfiles.
