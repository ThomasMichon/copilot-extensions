# Plugin Service Model — Vision

- **Subject:** The copilot-extensions plugin **service model** — how
  installer-deployed plugin runtimes expose, coordinate, and are reached as
  local services on a user's machine.
- **Scope:** branch (links cross-cutting and per-plugin child visions)
- **Status:** Active
- **Last revised:** 2026-09-04
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

- **Plugin runtime** — the self-contained versioned runtime and invocation
  surface a runtime plugin's own installer deploys inside its marketplace
  installation cell, per the shared **install contract**. The installed runtime,
  not a git checkout or an ambient command with the same name, is what executes.
- **Drop-in contribution registry** — a consumer-owned `*.d` directory through
  which independently-installed plugins contribute manifests, pointers, or
  config fragments without importing across runtimes or editing one shared file.
  Presence is only a discovery candidate: the consumer validates provenance,
  current eligibility, and the referenced target before activating it.
- **Service-bearing plugin** — a plugin whose runtime includes a **long-lived
  local service** (an always-on daemon), as distinct from an on-demand CLI or a
  payload-only (skills/extension) plugin.
- **Local endpoint** — the machine-local address at which a running service is
  reached by its clients (its own CLI, other plugins, agents on the box). The
  vision constrains the *guarantees* of this endpoint, not its mechanism.
- **Endpoint discovery (rendezvous)** — how a client finds the *current*
  endpoint of a service without a human-managed constant. Discovery is the seam
  that makes endpoints collision-free and relocatable.
- **Lifecycle supervision** — the platform-native mechanism that realizes a
  service's declared availability contract, from user-session auto-run through
  restart-on-failure and pre-login operation.
- **Lifecycle tier** — the least-privileged supervision class that satisfies a
  service's actual availability needs: user-mode ensure/auto-run, scheduled
  activation, an installed system service, or a container whose orchestrator
  owns lifecycle. Moving upward is an explicit escalation, not the default.
- **Stable lifecycle launcher** — the durable invocation boundary registered
  with a supervisor once. It remains at one stable location while resolving the
  currently selected immutable runtime generation behind that boundary.
- **Single-instance lease** — the host-local claim that makes "**one active
  daemon per service per host**" an *asserted, repairable* property rather than a
  hope: a process becomes the active endpoint only by holding the lease, and a
  process that cannot acquire it stands down instead of racing. Ownership is
  liveness-reconciled, so a lease held by a dead process is reclaimable and a
  live owner is never displaced by accident. It is the seam that makes cutover
  reaping complete and the coalescing tier safe.
- **Work-coalescing singleton** — an *optional* service tier that folds many
  callers' identical, cheap, idempotent work onto a single warm, refcounted
  daemon (consolidating the warm runtime and shared upstream, never the callers'
  isolated state), instead of each caller spawning its own worker. A convenience
  over the always-correct inline path, never a dependency.
- **Install contract** — the uniform deploy/version/footprint agreement every
  runtime plugin follows, so services deploy, update, and are audited the same
  way. See [`docs/install-contract.md`](../../docs/install-contract.md).
- **Marketplace-scoped installation** — the ownership boundary that lets
  independently versioned marketplaces ship same-named plugin ecosystems to one
  host without contending for runtime, state, lifecycle, adoption, discovery, or
  invocation resources. See the
  [Marketplace Installation Cells](installation-cells/README.md) child vision.
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
A service's declared availability is realized by the host's native lifecycle
facility through one coherent cross-platform contract. A user-session service
starts with that user; a managed user service adds restart and logout-survival
where declared; a system service adds pre-login or system-identity availability.
For standalone host installation this supervision is the plugin's **own** — a
plugin brings up and keeps alive **its own daemon** with **no
installer/configurator control-plane in the loop**. An optional control-plane
may observe and true-up daemons across the set, but a daemon's standalone
existence and liveness never depend on it running.

### least-privilege-lifecycle-tier
Every service uses the **lowest lifecycle tier that satisfies its availability
contract**. User-mode ensure/auto-run is the default because starting and
keeping the process healthy needs no elevation; scheduled activation is an
opt-in next step when login/startup triggers are required; an installed system
service is reserved for system-wide or pre-login availability; and a container
is used only when an external orchestrator explicitly owns lifecycle. The same
platform facility may realize different tiers when its privilege and
availability properties differ. Installation does not escalate merely because
a more privileged mechanism is available, and routine updates never introduce a
new privilege boundary. Container management is a consumer-selected deployment
of the same portable service contract, not a dependency of the plugin's
standalone host installation.

### self-provisioning-runtime
**Enabling** a runtime plugin is the whole action a user takes — its runtime
then **provisions itself**, driven by the plugin's **own** installer and **no
particular sibling, launcher, or control-plane**. Provisioning is reached by a
**layered, launcher-independent bootstrap**, so it fires in whatever the host
offers: (1) the plugin's **binstub provisions on first use** — the broadest path,
needing only a shell and the binstub on disk, so the runtime comes up the first
time an agent (or a human) *calls the tool*, even in a confined host (the Copilot
app, a cloud agent) where no launch-wrapper and no session hooks exist; (2) where
session-start hooks fire, a cheap **stamp** lays the binstub ahead of first use as
an optimization; (3) each plugin's **skills open with a fail-closed readiness
check** that, if the runtime is absent, tells the agent the one command to bring
it up — so the reach extends anywhere the plugin's skills load. Whenever a session
starts on a machine where the plugin is enabled (and, for a machine-scoped
runtime, *permitted*), an absent runtime is installed and a drifted one is
**reconciled to match its enabled payload version** — with **no manual install
step**. Because agents start sessions **out of order, concurrently, and ad-hoc**,
provisioning is **idempotent, version-keyed** (a no-op once already matched),
**throttled**, **lock-serialized** against concurrent first-callers, and **gated**
to the machines a runtime belongs on — and it **never blocks or slows** a session
already current. It is also **self-sufficient in acquiring its own toolchain**:
where the venv/package manager it needs is missing, it obtains a private copy
rather than dead-ending. "Enabled" is the user's whole intent; "installed,
running, and version-matched" is the model's job — the same self-healing that
keeps a runtime immutable-versioned (above) also brings a *missing* runtime into
existence. None of this depends on the optional installer/configurator
control-plane being present: it is a **convenience that can update the whole set
and true-up alignment**, but a plugin **provisions and reconciles itself — and
supervises its own daemon (see *platform-native-lifecycle*) — with that app
entirely absent**.

### delegated-heavy-companion-runtime
The self-provisioning contract has one narrow exception: an explicitly
configured **optional companion capability** whose dependency footprint is too
heavy or invasive for ordinary plugin first-use and session-start paths may
delegate runtime materialization to an already-running trusted supervisor. The
plugin contributes an attributed declarative contract; it does not contribute
an arbitrary installer command, package-manager flags, credentials, or physical
runtime placement. The supervisor alone builds, validates, atomically publishes,
selects, rolls back, and retires immutable companion runtime generations.

This exception never becomes an ambient dependency of the plugin. Without
explicit capability configuration and the owning supervisor, the contribution
is inert and the capability is honestly unavailable; an agent-facing command
cannot install it as a fallback. The plugin's remaining lightweight surfaces
continue to work independently where meaningful. A normal runtime plugin or
service still follows `self-provisioning-runtime`; delegation is an explicit
capability boundary, not a way to centralize routine plugin installation.

### graceful-composition
When multiple services are present they discover and use one another's optional
capabilities without a mandatory central broker and without user-authored wiring.
Cooperation is opportunistic, not obligatory. The same rule governs a **heavy,
invasive capability the optional control-plane provides** — for example **terminal
multiplexing**: a plugin (e.g. the worktree tools, or agent-bridge when hosting
sessions) **detects mux support and uses it when present, and runs non-muxed when
it is absent**, rather than carrying the heavyweight multiplexer itself. The
capability lives where it belongs (the Worktree Manager); the plugins consume it if
it is there and degrade cleanly if it is not.

### self-auditing-drop-in-composition
Cross-plugin `*.d` registries are **safe to sweep and easy to clean**. A routine
consumer sweep treats each entry independently: a malformed manifest, missing
target, disabled or uninstalled contributor, ambiguous identity, or unavailable
command makes only that contribution inert; every valid peer still loads. The
consumer emits a bounded, actionable warning that identifies the entry, target,
and reason, while long-running services deduplicate or rate-limit repeats so one
stale file—or a directory full of stale files—cannot flood logs. A scan that
cannot authoritatively enumerate the registry is **indeterminate**, never
misreported as an empty registry that withdraws healthy last-known contributions.
Removing or deactivating a contribution is a
desired-set change, not a restart requirement: live state sourced from an entry
is withdrawn when the entry ceases to be valid.

Each consumer also owns a **doctor** surface that audits its contribution
registry without activating entries. Doctor distinguishes malformed, missing,
unauthorized, ambiguous, duplicate, and legacy/unattributed entries; reports the
exact file and target; and recommends the narrow cleanup or re-registration
command. Routine sweeps never delete user state. Doctor is report-only by
default, and may auto-fix only an entry whose managed ownership is proven by a
consumer-issued registration receipt and whose file identity is revalidated
immediately before unlinking.

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
git hooks). Repository mutation happens only through an explicit,
ownership-aware repo-integration command and the repository's normal
contribution flow; it is never an incidental install/update side effect.
**Register/adopt** commands declare integration intent, but their write scope is
part of each command's contract: a repo-bootstrap command may write repo and
machine-local wiring, while a projection command may only read published repo
state and update user-level configuration. The verb name alone never grants
repo-write authority.

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
Reaching it never depends on an external proxy, tunnel, mesh, registry, **or the
optional installer/configurator control-plane** being installed, configured, or
running.

### degrade-gracefully
Absent an optional peer or coordinator, a service still performs its own local
function; optional cross-service features simply stay dark until the peer is
present. A missing sibling degrades a feature, never the whole service.

### stale-drop-ins-are-inert-and-legible
A stale cross-plugin drop-in never breaks its consumer and never silently stays
active. Discovery validates entries one at a time, continues past every bad
entry, and rebuilds the live desired set from what is valid **now** rather than
additively retaining what was valid once. A failed/partial registry enumeration
is not a valid desired-set snapshot: the consumer retains its last-known set,
warns, and retries rather than turning uncertainty into mass removal. Invalid
contributions are visible
through warnings and doctor findings with stable reason codes, so cruft can be
removed deliberately instead of accumulating invisibly. Operational resilience
and hygiene are complementary: the sweep stays available; doctor makes the
degraded edge legible and removable.

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
repo-altering effect is explicit, ownership-aware, and travels through the
repository's normal contribution flow; it is never a side effect of deployment.

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

### register-once-cutover-on-update
A lifecycle supervisor is bound **once** to a stable launcher and remains
unchanged across ordinary version updates. The launcher resolves the selected
immutable runtime generation dynamically; an update installs the new generation
beside the old one and uses *zero-downtime-cutover* to move work before retiring
the predecessor. A routine version bump therefore requires neither
re-registration nor renewed elevation, and changing runtime configuration does
not rewrite the supervisor definition.

### payload-remains-replaceable
The marketplace payload remains replaceable while services and launchers are
running. No long-lived **service, daemon, installer, or service launcher** may
retain the payload directory as its working directory or depend on mutable files
there for steady-state service execution. Runtime processes operate from their
installed generation and durable state locations, so refreshing or replacing
the payload cannot be blocked by a process the previous payload launched.
Copilot CLI's own session-scoped loading of skills, hooks, and extensions from
the payload remains outside this service-runtime guarantee.

### single-instance-lease
At most **one live daemon owns a given service within one marketplace
installation cell on a host at a time**, and that ownership is **explicit and
reclaimable**. A service acquires a cell-scoped host-local lease before it
becomes the active endpoint; a process that cannot acquire it **stands down**
rather than racing an incumbent. Ownership is **liveness-reconciled, not
timer-guessed**: a lease held by a dead process is reclaimable, and a still-live
owner is never displaced by accident. This makes "one active per service per
installation cell per host" a property the system can **assert and repair** —
so a cutover reconciles the **full set** against the lease, retiring every
predecessor it replaces *and* every stray that a plain restart would otherwise
strand, and no drained-but-live daemon lingers holding a port or memory. The
mechanism (a lock file, a named mutex, an OS-native single-instance guard) is
spec-level; the guarantee is not.

### work-coalescing-singleton
Where many callers would otherwise each spawn a short-lived worker for the **same
cheap, idempotent work**, the suite **may** fold that work onto a single warm,
**refcounted** daemon instead. Identical requests arriving close together
**coalesce** into one execution rather than fanning out into N redundant
processes; **distinct side-effecting** work is **queued with bounded concurrency
and backpressure**, never unbounded-forked. The daemon lives **only while at
least one consumer needs it** and **idle-exits** when the last releases — it is
**warmth, not truth**: an accelerator over a durable store whose loss costs only
warmth. This tier is **always optional**: a lone caller, or any caller that
cannot reach the daemon, does the work **inline and correct with no daemon at
all**. It generalizes two capabilities the model already sanctions — an optional
resident tracker and the optional multiplexer of *graceful-composition* — into
one shape: **consolidate the warm runtime and shared upstream, never the callers'
isolated state, and only across callers that share the same identity and
credentials.** Guarded by the *single-instance-lease*, cut over by
*zero-downtime-cutover*, discovered by rendezvous.

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
- Child visions:
  [Marketplace Installation Cells](installation-cells/README.md) · per-plugin
  service visions live under `visions/plugins/<name>/`
- Reality docs: [`docs/architecture.md`](../../docs/architecture.md) (install
  topology, the ports table, communication paths) ·
  [`docs/install-contract.md`](../../docs/install-contract.md) · per-plugin
  `docs/architecture.md`

## Provenance

- **2026-09-03** — Added the **lifecycle tier** and **stable lifecycle launcher**
  concepts, the **least-privilege-lifecycle-tier** feature, and the
  **register-once-cutover-on-update** and **payload-remains-replaceable**
  behaviors. Mined from repeated Windows update failures in which long-lived
  plugin processes retained the marketplace payload as their working directory,
  plus the existing stable-launcher and graceful-cutover mechanisms. The intent
  generalizes the fixes tracked by #621, #622, and #1550: select the least
  privileged sufficient supervisor, register its stable boundary once, move
  versions behind it without renewed elevation, and keep the distributable
  payload disposable.
- **2026-08-25** — Added the
  [Marketplace Installation Cells](installation-cells/README.md) child vision.
  It generalizes the requirement that independently versioned marketplaces can
  ship same-named plugin ecosystems to one host without sharing runtime, state,
  lifecycle, discovery, adoption, or invocation ownership.
- **2026-08-24** — Added the **drop-in contribution registry** concept,
  **self-auditing-drop-in-composition** feature, and
  **stale-drop-ins-are-inert-and-legible** behavior. Mined from a suite-wide
  audit of `providers.d`, `config.d`, Picker pivots, managed SSH fragments, and
  the designed dispatch registrar: consumers generally avoided hard failure,
  but several skipped missing targets silently, retained live state
  additively, restored installed-but-disabled contributions, or had no doctor
  path to identify cruft. The intent separates routine availability from
  hygiene: per-entry failures warn but never abort a sweep; only an authoritative
  scan drives the live desired set; consumer-owned doctor commands report exact
  stale entries and recommend narrow cleanup, while auto-fix requires a
  consumer-issued ownership receipt and pre-unlink identity recheck. Realized by
  the `drop-in-registry-hygiene` pattern and tracked by
  ThomasMichon/copilot-extensions#1043.
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

- **2026-08-12** — Substantially strengthened **self-provisioning-runtime** and
  reaffirmed the plugins' **independence from the optional control-plane**. The
  original 2026-07-30 realization still pinned provisioning to *session start via
  the agent-worktrees launcher*; this pass generalizes it to a **layered,
  launcher-independent bootstrap** — (1) the plugin's **binstub provisions on first
  use** (the broadest path; reaches the Copilot app / cloud agent where no
  launch-wrapper or session hook exists), (2) a session-start **stamp** as an
  optimization where hooks fire, (3) a **fail-closed skill readiness check** that
  guides the agent to bring the runtime up. Made explicit that agents start
  sessions **out of order, concurrently, and ad-hoc**, so provisioning is
  idempotent, version-keyed, throttled, and **lock-serialized**, and that a plugin
  **self-acquires its own toolchain** when the venv/package manager is absent.
  Extended **platform-native-lifecycle** and **standalone-reachability** to state
  that a plugin **supervises its own daemon and is reachable with the
  installer/configurator control-plane entirely absent** — that app is an optional
  convenience (whole-set update + cross-plugin alignment), never a dependency.
  Reciprocal to the installer vision's broadening into the optional worktree/agent
  control-plane. Mined from the operator's direction during, and the empirical
  results of, the plugin self-provisioning rollout (all binstub/service-installing
  agent-\* runtimes made self-provisioning + clean-room-validated).

- **2026-08-13** — Extended **graceful-composition** to cover a **heavy, invasive
  capability the optional control-plane (the Worktree Manager) provides** — namely
  **terminal multiplexing**: a plugin (the worktree tools, or agent-bridge when
  hosting sessions) **detects mux support and uses it when present, running
  non-muxed when absent**, rather than carrying the multiplexer itself. Keeps the
  heavyweight dependency out of the lightweight plugins while letting muxed sessions
  light up when the Manager is installed. Mined from the operator's Phase-6 DQ9
  decision (mux is Manager-owned; plugins detect-and-fall-back).

- **2026-08-18** — Added the **single-instance-lease** and
  **work-coalescing-singleton** behaviors. Mined from a process-management audit
  of the suite under its now-normal operating point: hosts running **many
  concurrent worktree sessions** (on the order of 5–10) with **frequent mid-flight
  plugin updates**, where each new session launch may re-run a service's start and
  trigger a reinstall. The audit found the model already prescribes clean cutover
  and optional multiplexing, but lacked two things. (a) An explicit **"one active
  per service per host" lease** to make cutover reaping *complete*: repeated
  same-version cutovers and plain restarts were observed **stranding drained-but-
  live passive daemons** that keep holding a port and memory, because a cutover
  retires only the single predecessor it replaces, not every stray. (b) A **named
  work-coalescing service tier** unifying the already-stated worktree resident-
  tracker and the graceful-composition multiplexer, so that many identical cheap
  sweeps (a status refresh) and per-session, per-server transport bridges
  **consolidate onto one warm, refcounted, idle-exiting daemon** instead of
  fanning out one process per caller — the dominant source of process-count and
  memory growth under concurrency. Both are intent-level: the lease is a shared
  primitive beside `zdd`/rendezvous, and the coalescing tier stays strictly
  optional with an always-correct inline fallback. Realized by the
  *plugin-process-hygiene* effort.

- **2026-09-04** — Added **delegated-heavy-companion-runtime** as a narrow
  exception to ordinary plugin self-provisioning. An explicitly configured
  optional capability may keep heavyweight dependencies out of plugin and
  session paths by contributing a declarative runtime contract to an
  already-running trusted supervisor. The supervisor owns package installation,
  immutable publication, rollback, and retention; the plugin has no fallback
  installer authority and remains inert when the capability or supervisor is
  absent. This preserves the default independent-plugin contract while giving
  genuinely heavyweight companions one explicit ownership boundary.
