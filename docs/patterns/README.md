# Architecture Patterns

The **prescriptive design layer** for copilot-extensions: *how we build plugins
and plugin services here*, as reusable patterns — distinct from `architecture.md`
(what the suite *is*, as-is) and from `visions/` (what a subject *should
ultimately be*, intent). Patterns are the connective tissue: a pattern is the
established, reusable **how** that *realizes* a vision's **what**.

Read this hub as a **map**: it states the shapes, the design principles, and the
binding invariants, then links to focused pattern docs for the deep dives. It is
the copilot-extensions analogue of a facility "service-architecture" guide.

## The layered model

| Layer | Question | Home |
|-------|----------|------|
| **Vision** | what should this *ultimately* be? | [`visions/`](../../visions/README.md) |
| **Patterns** (this) | how do we *build* it here, reusably? | `docs/patterns/` |
| **Architecture** | how does it *actually* work now? | [`architecture.md`](../architecture.md), per-plugin `docs/` |
| **Contribution** | how do I land a change correctly? | [`AGENTS.md`](../../AGENTS.md), `CONTRIBUTING.md`, the harness skills |

A pattern **serves a vision** (name which) and is **embodied by exemplar
plugins** (name them). When you add or change a pattern, keep it intent-agnostic
about the specific plugin — a pattern is a suite-wide convention, not a plugin's
private design.

## Plugin shapes

Choose the simplest shape that fits; don't impose structure a plugin doesn't need.

| Shape | What it is | Examples |
|-------|-----------|----------|
| **Payload-only** | Skills / hooks / a session extension; enabling the plugin is the whole install — no runtime | efforts, visions, context-handoff, customizing-copilot, harness-* |
| **Runtime CLI** | venv + `~/.local/bin` binstub, invoked on demand; no daemon | agent-mcp, agent-containers |
| **Runtime service** | Runtime CLI **plus** a long-lived local service under platform-native supervision | agent-bridge, agent-dispatch, agent-vault |
| **Resolver-import** | A plugin whose package is *imported into a sibling service's venv* to add a namespace resolver, rather than running its own daemon | agent-codespaces / agent-containers (imported by agent-bridge) |

## Design principles

0. **Architectural change reconciles to the vision.** Before a change that adds
   or alters architecture or behavior, reconcile it to the relevant vision
   (`AGENTS.md` § Visions): it either **closes** a stated vision item (cite it),
   **extends** the vision (revise the vision first), or is **below-altitude** (no
   vision governs it — say so and proceed). Never silently introduce
   architectural intent that contradicts or bypasses a vision. Guide, not gate —
   proportionate to the change.
1. **À la carte first.** Every plugin is independently installable. Never assume a
   sibling is installed or running, and never require shared machine-wide plumbing
   (proxy, tunnel, registry, central coordinator). A lone install is first-class.
2. **Compose gracefully.** When siblings *are* present, discover and use their
   optional capabilities without a mandatory central broker and without the user
   hand-wiring them. A missing sibling degrades a feature, never the whole plugin.
3. **The runtime is the unit, not the checkout.** A plugin runs from its installed
   runtime (`~/.agent-*` venv + binstub), deployed by its own installer per the
   install contract. Nothing at run time depends on a git checkout of this repo.
4. **Right-size the surface.** Payload-only < runtime CLI < runtime service.
   Don't add a daemon, a port, or a resolver a plugin doesn't need.
5. **Cross-platform parity is a feature.** A plugin behaves the same on Windows
   and Linux/WSL; platform differences are handled at the edges (installer,
   binstub, supervision), never leaked into behavior.
6. **Fail loud on the real cause.** A service that can't bind or reach its endpoint
   surfaces the literal cause; it does not mask the symptom or silently degrade.
7. **One canonical CLI per plugin.** A plugin owns exactly one binstub; a sibling
   that imports its package must not re-point that binstub (avoids version skew).

## Design invariants (binding contracts)

Invariants are **must-always-hold contracts between a vision and the code** — the
narrow set of properties a change may never quietly break. They are the enforceable
core of the principles above; a reviewer checks a change against these.

- **No shared-infrastructure dependency.** A plugin service is installable and
  reachable using only what its *own* installer deployed. It must never *require*
  an external reverse proxy, tunnel, mesh, load balancer, or service registry.
  (Serves *Vision plugin-services §Non-Goals/no-shared-infrastructure-dependency*.)
- **Endpoints are collision-free by construction.** Two plugin services — and the
  same service across the Windows/WSL boundary — never contend for one address by
  design, not by a human maintaining a fixed-port table or applying per-platform
  offsets. (Serves *§Behaviors/collision-free-endpoints*.)
- **Endpoints are discovered, not assumed.** A client resolves a service's current
  endpoint from the service's own runtime state; there is no ambient constant a
  mismatch can silently break. (Serves *§Behaviors/endpoint-discovered-not-assumed*.)
- **Local-first exposure.** A service is machine-local by default; reaching beyond
  the host is an explicit opt-in. (Serves *§Behaviors/local-first-exposure*.)
- **Minimal network exposure.** A service prefers a transport that opens no
  network port at all (OS-native socket/pipe) over a loopback TCP port, *even one
  bound to `127.0.0.1`*; when a port is unavoidable it is OS-assigned and
  discovered, never fixed; a boundary is crossed by an opt-in tunnel over an
  already-trusted transport, never a new inbound port. (Serves
  *§Behaviors/minimal-network-exposure*.)
- **Deploy through the pipeline, never edit the deployed copy.** Source lives in
  the repo; changes reach a runtime only via the installer + version bump. Editing
  `~/.copilot/installed-plugins/…` or a runtime dir is forbidden.
- **A version bump ships the change.** Every plugin change bumps its version in the
  same commit (see `CONTRIBUTING.md`); an un-bumped push is silently ignored.
- **Repo mutation is an adopt-only power.** `install`/`update` act on
  **machine-local** state only — they may migrate local config *schema* and *warn*
  on a stale/deprecated repo convention, but never alter a repo's committed config
  or inject its git hooks, and never change user *behaviors*. Only `register`/`adopt`
  mutates a repo (taking user preferences); since you adopt only repos you own, the
  power is confined to owned repos by construction. (Serves *Vision plugin-services
  §Features/install-adopt-boundary*.)
- **Config schema is backward-migratable within a bounded window.** A machine-local
  config's schema evolves by **migrate-by-rewrite** (an explicit `schema_version` +
  ordered `vN -> vN+1` migrators applied on read and persisted on install/update),
  never by an unbounded legacy-tolerant loader. The `vN -> vN+1` chain stays
  unbroken for at least the last version or two, **enforced by checked-in
  prior-version fixtures** that must migrate to current and load — so accidental
  backward-incompatibility cannot land, and a breaking change is a deliberate,
  fixture-updating act. (Serves *Vision plugin-services §Behaviors/install-leaves-repos-unaltered*;
  see [`config-schema-migration.md`](config-schema-migration.md).)
- **Primitives below, orchestration above.** A lower fabric layer exposes a
  **mechanism** (e.g. "launch a session in a worktree"); the **policy** that
  composes it into a workflow (e.g. a handoff: continuation + claimable delegation
  record + successor cutover + verify + retire) lives in a **higher** layer — never
  baked into the primitive's owner. A layer must not draw a higher layer's
  orchestration concern inward. (Serves *Vision agent-fabric
  §Behaviors/handoff-orchestrated-above-primitives*.)

## Patterns

Focused deep-dives (each states: the problem, the standard approach, the rationale,
the exemplars, and the vision it serves):

| Pattern | Concern |
|---------|---------|
| [local-endpoint-discovery](local-endpoint-discovery.md) | How a service exposes a discoverable, collision-free, local-first endpoint — the anti-static-port pattern, incl. the rendezvous / port-mapping file |
| [service-transport](service-transport.md) | Which channel a service exposes — the transport ladder (stdio → OS-native socket/pipe → OS-assigned loopback → tunnel) and the named-pipe/UDS reality |
| [service-lifecycle-supervision](service-lifecycle-supervision.md) | Platform-native always-on supervision (Windows Scheduled Task / systemd user unit) and its lifecycle verbs |
| [install-vs-adopt-boundary](install-vs-adopt-boundary.md) | Which lifecycle verb may mutate what — `install`/`update` is machine-local (schema-migrate + warn), `register`/`adopt` is the only repo-mutating verb (repo config + git hooks), and ownership falls out of adoption |
| [config-schema-migration](config-schema-migration.md) | How a machine-local YAML config gains an explicit `schema_version` + scripted `vN -> vN+1` migrate-by-rewrite (the vendored `config-migrate` primitive), applied lazily on read + eagerly on install/update, with a fixture-guarded backward-compat window |
| [a-la-carte-independence](a-la-carte-independence.md) | Standalone-first plugins that compose gracefully, incl. the resolver-import pattern |
| [cross-platform-parity](cross-platform-parity.md) | One behavior across Windows and Linux/WSL: shells, UTF-8, the WSL/Windows boundary, binstubs |

The **runtime deploy contract** (venv + binstub + manifest, `uv`, marketplace-vs-
runtime split) is its own established pattern doc:
[`install-contract.md`](../install-contract.md).

## See Also

- Intent: [`visions/plugin-services/`](../../visions/plugin-services/README.md) —
  the plugin service model these patterns realize.
- Reality: [`architecture.md`](../architecture.md) — the as-is topology, ports,
  and install map.
- Contribution: [`AGENTS.md`](../../AGENTS.md), `CONTRIBUTING.md`, and the
  `contributing-to-copilot-extensions` harness skill (routes design work here).
