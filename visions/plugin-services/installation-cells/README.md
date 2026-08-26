# Marketplace Installation Cells — Vision

- **Subject:** Marketplace installation cells — the identity and isolation
  boundary for plugins that may share a name while arriving from independent
  marketplaces.
- **Scope:** leaf
- **Status:** Active
- **Last revised:** 2026-08-25
- **Reality docs:** [`docs/architecture.md`](../../../docs/architecture.md) ·
  [`docs/install-contract.md`](../../../docs/install-contract.md)

## Purpose & Intent

A plugin installation is identified by both its plugin identity and its
marketplace provenance. The plugin name alone is never enough to claim
machine-local resources.

Independent marketplaces should be able to ship independently versioned copies
of the same plugin ecosystem to one user account on one host. Each copy should
install, run, compose, update, roll back, adopt projects, and uninstall as if the
other copies did not exist. Coexistence is structural: no operator arbitrates
paths, commands, services, locks, endpoints, or state ownership by hand.

## Concepts & Components

- **Marketplace installation identity** — the stable identity formed from a
  plugin's marketplace provenance and plugin identity. It survives changes in
  working directory, launch path, process environment, and active runtime
  version.
- **Installation cell** — the marketplace-scoped ownership boundary containing
  that marketplace's plugin runtimes, mutable and durable state, lifecycle and
  discovery artifacts, and machine-local project-adoption state.
- **Cell-local invocation surface** — the callable surface through which a
  plugin's skills, hooks, services, and peers reach the runtime in their own
  installation cell without claiming a machine-global command name.
- **Same-cell composition** — optional cooperation among sibling plugins that
  share one marketplace installation identity. A peer in another installation
  cell is a separate ecosystem, not an interchangeable sibling.

## Features

### marketplace-scoped-runtime-and-state
Every runtime, configuration store, durable data store, cache, registry,
discovery record, and lifecycle artifact that an installation owns belongs to
its marketplace installation cell. Two same-named plugin copies never share
writable machine-local state by accident.

### independent-lifecycle
Each installation can provision, start, update, roll back, repair, and uninstall
without changing the executable selection, running services, state, or
availability of another marketplace's installation.

### cell-scoped-project-adoption
Machine-local state created when a project is registered or adopted belongs to
the adopting installation cell. The same project identity can be known to
multiple marketplaces without their worktree, session, lease, or generated
invocation state colliding.

### cell-local-invocation
Every operational path has a stable way to invoke the intended installation
without depending on ownership of a bare, machine-global command name. This
includes calls originating from skills, hooks, service supervisors, generated
project entry points, sibling plugins, and remote execution.

### attributable-agent-capabilities
Agent-facing skills, agents, hooks, and tool servers remain attributable to the
installation cell that supplied them. When same-named capabilities from more
than one cell are available, the intended provider is legible and explicitly
selectable rather than resolved by load order.

### provenance-safe-transition
An existing unscoped installation can become marketplace-scoped only when its
ownership is attributable to that marketplace. Ambiguous legacy state is
preserved, remains operable, and is surfaced for deliberate resolution; one
marketplace never claims or rewrites another installation's state as a
convenience migration.

## Behaviors

### coexistence-by-construction
Installing the same plugin name from two marketplaces creates two complete,
non-contending installations. Their paths, process identities, lifecycle
claims, endpoints, discovery records, and generated launch surfaces are
distinct by construction rather than by deployment convention.

### same-cell-composition
A plugin resolves optional siblings within its own marketplace installation
cell. Missing same-cell peers degrade gracefully; a same-named peer from another
marketplace is never captured through ambient command lookup or a shared
registry.

### provenance-carried-end-to-end
An operation inherits installation identity from the artifact that initiated
it: the payload that supplied a skill or hook, the runtime that spawned a peer
call, or the adoption record that addresses a project. Identity is carried
through every process and remote boundary; a machine-global "active
marketplace" setting is never a correctness dependency.

### ambient-activation-is-owner-gated
A hook, guard, reconciler, or other host-triggered entry point acts only when
its installation cell owns the current context. Non-owning cells stand down
without error and without provisioning, mutating state, emitting duplicate
policy, or affecting the owning cell.

### stable-installation-identity
The chosen installation cell is explicit and stable across shell environments,
working directories, service restarts, runtime cutovers, Windows/WSL boundaries,
and remote invocation. Ambient `PATH` order does not decide which installation
owns an operation.

### singleton-claims-are-cell-scoped
Single-instance leases, supervised service identities, coalescing daemons, and
other exclusivity claims mean one active owner per service **per installation
cell** per host. Work and warmth may coalesce within a cell, but never across
cells with independent state or provenance.

### failure-and-removal-containment
A failed install, corrupt runtime, stopped service, update, rollback, repair, or
uninstall affects only its owning installation cell. Cleanup requires
installation-specific ownership evidence and never sweeps another cell's
artifacts.

### ownership-is-legible
Commands, services, state, logs, diagnostics, and doctor surfaces identify the
installation cell that owns them. All cells present on a host are enumerable
without activating them, so an operator can diagnose overlap and migration
state without guessing from paths or process names.

### repository-boundary-preserved
Marketplace scoping applies to machine-local runtime and adoption state. A
project's committed configuration remains repository-owned and is not
duplicated or rewritten by install/update merely because multiple marketplaces
consume it; any marketplace-specific repository behavior requires an explicit
adoption decision. Singleton in-repo integration surfaces have attributable,
explicit ownership or an intentionally composable form; a second adopter never
wins by silently overwriting the first.

### cross-platform-equivalence
The isolation guarantees are the same on Windows, Linux, and WSL. Platform
differences in filesystem layout, command wrappers, service supervision, and
local transports do not weaken or change installation identity.

## Non-Goals / Boundaries

- **Not a filesystem or command-naming specification.** This vision does not
  mandate a particular home-directory layout, command prefix, service-unit
  spelling, or manifest schema. Those mechanics belong to the install contract
  and patterns.
- **No required global aliases.** A marketplace may offer optional
  human-facing aliases, but bare machine-global command ownership is never a
  correctness dependency or the identity of an installation.
- **No cross-marketplace federation.** Installation cells are isolated by
  default. Deliberate interoperability between marketplaces is a separate,
  explicit contract, not implicit sibling discovery.
- **One cell per installed marketplace identity.** Multiple immutable runtime
  versions from one marketplace are slots inside one installation cell, not
  independent cells. Parallel cells represent independently installed
  marketplace identities.
- **No mandatory central registry.** Isolation does not require one shared
  daemon or mutable global registry that every plugin must consult.
- **No automatic fork of committed project configuration.** This model
  separates machine-local adoption state; it does not silently create parallel
  copies of repository-owned configuration.

## See Also

- Parent vision: [Plugin Service Model](../README.md)
- Child visions: none (leaf)
- Reality docs: [`docs/architecture.md`](../../../docs/architecture.md) ·
  [`docs/install-contract.md`](../../../docs/install-contract.md)
- Patterns:
  [`a-la-carte-independence`](../../../docs/patterns/a-la-carte-independence.md) ·
  [`project-scoped-invocation`](../../../docs/patterns/project-scoped-invocation.md) ·
  [`install-vs-adopt-boundary`](../../../docs/patterns/install-vs-adopt-boundary.md) ·
  [`runtime-self-provisioning`](../../../docs/patterns/runtime-self-provisioning.md) ·
  [`uniform-runtime-resolution`](../../../docs/patterns/uniform-runtime-resolution.md) ·
  [`local-endpoint-discovery`](../../../docs/patterns/local-endpoint-discovery.md) ·
  [`drop-in-registry-hygiene`](../../../docs/patterns/drop-in-registry-hygiene.md) ·
  [`cross-platform-parity`](../../../docs/patterns/cross-platform-parity.md)

## Provenance

- **2026-08-25** — Authored from the requirement that independently versioned
  marketplaces can ship same-named plugin ecosystems to one host without
  contending for runtime, state, lifecycle, discovery, adoption, or invocation
  ownership. Tracked by
  [ThomasMichon/copilot-extensions#1096](https://github.com/ThomasMichon/copilot-extensions/issues/1096).
