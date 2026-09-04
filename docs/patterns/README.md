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
| **Runtime CLI** | Target: installation-cell runtime + payload-local shim, invoked on demand; legacy implementations still use a global binstub during migration | agent-mcp, agent-containers (migration targets) |
| **Runtime service** | Runtime CLI **plus** a long-lived local service under platform-native supervision | agent-bridge, agent-dispatch, agent-vault |
| **Namespace-provider** | A plugin that registers a namespace with a sibling service via a filesystem **manifest** (its binstub driven over a process boundary), rather than running its own daemon | agent-codespaces / agent-containers (providers to agent-bridge) |
| **Managed companion capability** | An explicitly configured optional heavyweight capability whose attributed runtime declaration is materialized only by an already-running trusted supervisor | agent-index through agent-dispatch (planned integration) |

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
3. **The installation is the unit, not the checkout or plugin name.** A plugin
   runs from the runtime owned by its marketplace installation cell, deployed by
   its own installer per the install contract. Nothing at run time depends on a
   git checkout of this repo, and a bare plugin name never selects mutable state.
4. **Right-size the surface.** Payload-only < runtime CLI < runtime service.
   Don't add a daemon, a port, or a resolver a plugin doesn't need.
5. **Cross-platform parity is a feature.** A plugin behaves the same on Windows
   and Linux/WSL; platform differences are handled at the edges (installer,
   binstub, supervision), never leaked into behavior.
6. **Fail loud on the real cause.** A service that can't bind or reach its endpoint
   surfaces the literal cause; it does not mask the symptom or silently degrade.
7. **One command owner, one attributable invocation surface.** A plugin owns
   each logical command it implements and carries the canonical payload-local
   platform shims for it. Siblings refer to the logical command and consume the
   owning plugin's session glossary; they never re-point its command, embed its
   path, or rediscover it through `PATH`.
8. **Sweep safely; doctor explicitly.** A consumer-owned `*.d` registry processes
   each contribution independently, warns without aborting valid peers, and
   derives live state only from an authoritative current snapshot; an unreadable
   registry preserves last-known state rather than masquerading as empty.
   Cleanup belongs to the consumer's report-first doctor command, never a
   destructive startup sweep.

## Design invariants (binding contracts)

Invariants are **must-always-hold contracts between a vision and the code** — the
narrow set of properties a change may never quietly break. They are the enforceable
core of the principles above; a reviewer checks a change against these.

- **No shared-infrastructure dependency by default.** A plugin service is
  installable and reachable using only what its *own* installer deployed. It
  must never *require* an external reverse proxy, tunnel, mesh, load balancer,
  or service registry. The sole explicit exception is a
  [`managed-companion-runtime`](managed-companion-runtime.md): an optional
  heavyweight capability that is inert without its declared trusted supervisor
  and never masquerades as the plugin's ordinary standalone runtime. (Serves
  *Vision plugin-services §Non-Goals/no-shared-infrastructure-dependency* and
  §Features/`delegated-heavy-companion-runtime`.)
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
- **Marketplace provenance qualifies every installation-owned artifact.** Plugin
  name and version alone never select a runtime, mutable state, service, endpoint,
  provider, project-adoption record, or lifecycle claim. An operation inherits a
  validated installation context from its attributable payload/runtime or receives
  an explicit management context; missing or mismatched provenance fails closed.
  (Serves *Vision plugin-services/installation-cells*; see
  [`marketplace-installation-cells.md`](marketplace-installation-cells.md).)
- **Runtime installs are immutable and versioned.** An installer never mutates a
  runtime venv in place. A new version is built into its **own** directory beside
  the old one and the active version is published by an **atomic** `current-version`
  **marker file** (on Windows there is no junction at all — a reparse point was
  blocked by RedirectionGuard/WinError 448 on managed devices; POSIX keeps a
  `venv`/`.venv` symlink into the active slot, but the marker is authoritative); a
  running service keeps serving its own immutable files until it is cut over or
  retired, and rollback is a marker rewrite,
  not a rebuild. Concurrent live versions are reconciled by drain-and-cutover +
  shared routing/port state, never by racing to overwrite one install — so a
  concurrent-update corruption (duplicate/broken daemons) cannot happen by
  construction.   Realized by the versioned-runtime primitive (`versioned_runtime.py`), whose one
  canonical source is `libs/versioned-runtime/versioned_runtime.py` (vendored
  byte-identically into each plugin's `scripts/` by
  `tools/sync-versioned-runtime.py`),
  it is the **default** for every Python runtime and **enforced** by
  `tools/check-install-contract.py` in CI (a runtime that skips the layout fails);
  it is **always versioned** on both OSes -- the `AGENT_<NAME>_VERSIONED` /
  `COPILOT_EXT_NO_VERSIONED` opt-out and the legacy in-place-venv fork are retired.
  (Serves *Vision plugin-services §Behaviors/immutable-versioned-runtime*; tracked
  in dotfiles #581.)
- **A version bump ships the change.** Every plugin change bumps its version in the
  same commit (see `CONTRIBUTING.md`); an un-bumped push is silently ignored.
- **Enabling a runtime provisions it.** A runtime plugin that a repo/session
  **enables** is installed, started, and kept **version-matched to its enabled
  payload automatically at session start** — idempotent, version-keyed, throttled,
  and **gated to the machines it belongs on** — with **no manual install step**.
  A user's only action is enabling; provisioning and version reconciliation are the
  model's job and must never require hand-running an installer, nor depend on one
  *particular* sibling being the session launcher. (Serves *Vision plugin-services
  §Features/self-provisioning-runtime*; the convenience realization is `runtimeScope` +
  `agent-worktrees reconcile-plugins`, see
  [`../install-contract.md`](../install-contract.md) § "Automatic reconciliation at
  launch" — but that path must not be a *dependency*: the sibling-independent,
  confined-env realization (self-provisioning binstub + `stamp` + skill readiness
  self-check) is [`runtime-self-provisioning.md`](runtime-self-provisioning.md).)
  An explicitly configured optional
  [`managed-companion-runtime`](managed-companion-runtime.md) is the narrow
  exception: enabling the lightweight plugin does not provision that heavyweight
  capability, and only its already-running trusted supervisor may do so.
- **Every runtime command is payload-attributable.** Every runtime-bearing
  marketplace `agent-*` plugin declares all agent-facing commands in
  `payload-invocation.json`, commits the generated POSIX/PowerShell/CMD shims,
  and wires both-platform bootstrap and command-glossary hooks through
  `COPILOT_PLUGIN_ROOT`. Static prose names logical commands, never another
  plugin's direct path; missing or ambiguous ownership never falls through to
  `PATH`. Enforced by
  `libs/payload-invocation/tests/test_agent_plugin_coverage.py`. See
  [`runtime-agent-plugin.md`](runtime-agent-plugin.md).
- **Enabled machine-gated runtimes are never silently omitted.** Each enabled
  `runtimeScope: machine-gated` plugin publishes a bounded, plugin-owned
  installer/readiness module manifest or an explicit decline. Discovery
  qualifies module identity by validated marketplace installation provenance,
  resolves only payload-local scripts or declared payload commands, validates
  the complete dependency graph before mutation, and leaves execution and
  whole-run policy to an optional consumer. The plugin remains independently
  installable without that consumer. See
  [`installer-readiness-modules.md`](installer-readiness-modules.md).
- **Initial command context stays static.** A session command glossary contains
  exact attributable invocation data and, at most, stable bounded pivots such
  as machine/repository names. It never snapshots worktrees, sessions, leases,
  health, or live agents; fast-changing resources are queried through the mapped
  command at the point of use.
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
- **Handoff takeover is successor-acknowledged and predecessor-preserving.**
  Persist the baton before launch; candidate startup never moves the head;
  explicit successor consumption checkpoints and acknowledges before
  succession/head/title changes; predecessor retirement is last and requires
  the originally observed process creation identity. A failed or unacknowledged
  successor leaves the predecessor and stored baton recoverable. (Serves
  *Vision agent-fabric §Features/delegate-and-hand-off*; see
  [`context-handoff-lifecycle.md`](context-handoff-lifecycle.md).)
- **A handoff seed is a locator, never the baton.** The full-fidelity
  continuation has exactly one durable home. The startup seed is bounded,
  single-line, and carries only a task lead, the canonical consume action, and
  one short opaque recovery locator. It contains no executable source, shell
  command, quote-sensitive recovery text, or installed path, so terminal
  rendering, shell quoting, and process argv never become the storage channel. (Serves *Vision agent-fabric
  §Behaviors/context-pressure-is-a-continuity-signal*; see
  [`context-handoff-lifecycle.md`](context-handoff-lifecycle.md).)
- **Cross-plugin payload argv remains data.** A payload-only orchestrator
  resolves a sibling command only inside its own provenance-checked marketplace
  installation cell. If it must enter the sibling's runtime directly, it uses
  that payload's authoritative runtime resolver, then launches exact argv
  without ambient `PATH` choosing the payload/runtime, shell source
  interpolation of user-controlled arguments, shell-to-native re-serialization,
  cwd/PYTHONPATH import shadowing, or locale-dependent text encoding. First-use
  provisioning has a separate installation-sized timeout.
  (Serves *Vision plugin-services/installation-cells* and
  *agent-fabric/delegate-and-hand-off*; see
  [`context-handoff-lifecycle.md`](context-handoff-lifecycle.md).)
- **Durable coordination identity precedes ownership.** When a project requires
  an external state root, every claim/lease/resource producer resolves the
  qualified owner's versioned coordination readiness before its first mutation
  or external side effect. Known compatible rejection fails closed; teardown
  and read-only inspection remain available; optional peers degrade only when
  absent or incompatible. See
  [`state-root-coordination.md`](state-root-coordination.md). (Serves *Vision
  agent-fabric* resource claims, leasing, and accountability.)
- **Lifecycle logging is durable, fail-silent, parity-equal, and joinable.**
  High-level worktree/session lifecycle events go through the single
  `activity.log_event` writer (or its `activity-log` binstub) into the durable,
  self-pruning **activity log** — never a hand-formatted line, and never a write
  that can raise into the flow it observes. The Windows and POSIX launchers emit
  the **same** marks at the same points (a one-platform mark is a parity defect),
  each flow stage that can fail emits symmetric **start + end** marks, and one
  launch flow is tied together by a **`launch_id`** stamped on every record so a
  trace is reconstructable across process boundaries rather than guessed by
  timestamp. Verbose per-launch detail is quarantined to the ephemeral setup-log
  tier. (Serves *Vision plugins/agent-worktrees* — owns the event log + lifecycle
  hooks; see [`lifecycle-activity-logging.md`](lifecycle-activity-logging.md).)
- **Session discovery never sweeps the state root.** Linking a worktree to its
  Copilot session(s) resolves subfolders of the session-state root by **exact
  session id** (via the worktree session registry), never by enumerating the
  directory. A full sweep is quarantined to one explicit, user/agent-initiated
  **backfill/recovery** verb; normal operation (`list`, status, finalize, resume)
  must never iterate the state root — otherwise per-worktree enrichment degrades to
  O(worktrees × total sessions). (Serves *Vision picker §Features/programmatic-parity*,
  *§Behaviors/live-not-snapshot*; see
  [`session-state-access.md`](session-state-access.md).)
- **Drop-in registries are non-blocking and doctorable.** A malformed, missing,
  stale, disabled, or ambiguous `*.d` contribution can disable only itself:
  every other valid entry still loads, the live desired set forgets entries that
  are no longer valid after an authoritative scan, an indeterminate scan keeps
  last-known state, and the consumer emits aggregate-bounded warnings with stable
  reasons. The consumer's doctor surface reports exact
  entry/target/remediation; routine discovery never deletes state, and auto-fix
  is allowed only with a consumer-issued ownership receipt plus an immediate
  file-identity recheck. (Serves *Vision plugin-services
  §Features/self-auditing-drop-in-composition* and
  §Behaviors/stale-drop-ins-are-inert-and-legible*; see
  [`drop-in-registry-hygiene.md`](drop-in-registry-hygiene.md).)

## Patterns

Focused deep-dives (each states: the problem, the standard approach, the rationale,
the exemplars, and the vision it serves):

| Pattern | Concern |
|---------|---------|
| [runtime-agent-plugin](runtime-agent-plugin.md) | The complete “add an `agent-*` plugin” path: choose the smallest runtime shape, implement the cross-platform install contract, generate payload-local commands, wire attributable bootstrap/glossary hooks, write skills against logical commands, and add service/provider ownership without dynamic initial-context snapshots |
| [context-injection](context-injection.md) | How repositories, plugins, skills, and operator policy retain clear ownership while plugin-owned ambient guidance is injected as a concise, gated `sessionStart` context kernel with fail-open behavior, static safety fallback, cross-platform parity, and non-executing budget inventory |
| [context-handoff-lifecycle](context-handoff-lifecycle.md) | How a stored continuation becomes a successor-owned session without losing prompt fidelity, moving the head early, trusting a reused PID, depending on effort/knowledge state, or crossing an ambient/lossy command boundary |
| [local-endpoint-discovery](local-endpoint-discovery.md) | How a service exposes a discoverable, collision-free, local-first endpoint — the anti-static-port pattern, incl. the rendezvous / port-mapping file |
| [service-transport](service-transport.md) | Which channel a service exposes — the transport ladder (stdio → OS-native socket/pipe → OS-assigned loopback → tunnel) and the named-pipe/UDS reality |
| [service-lifecycle-supervision](service-lifecycle-supervision.md) | Least-privilege lifecycle tiers (user-mode ensure → scheduled activation → system service → container), stable register-once launchers, and cutover-safe updates |
| [install-vs-adopt-boundary](install-vs-adopt-boundary.md) | Which lifecycle verb may mutate what — `install`/`update` is machine-local (schema-migrate + warn), while each explicit `register`/`adopt` command owns only its documented repo and/or user-state integration scope |
| [config-schema-migration](config-schema-migration.md) | How a machine-local YAML config gains an explicit `schema_version` + scripted `vN -> vN+1` migrate-by-rewrite (the vendored `config-migrate` primitive), applied lazily on read + eagerly on install/update, with a fixture-guarded backward-compat window |
| [a-la-carte-independence](a-la-carte-independence.md) | Standalone-first plugins that compose gracefully, incl. the provider-manifest registry pattern |
| [state-root-coordination](state-root-coordination.md) | How claim/lease/resource producers resolve a qualified owner's versioned durable coordination identity before side effects, while teardown stays available and optional peers degrade by compatible contract |
| [drop-in-registry-hygiene](drop-in-registry-hygiene.md) | How cross-plugin `*.d` registries keep routine sweeps non-blocking while making malformed, missing, disabled, ambiguous, duplicate, and stale contributions visible and safely cleanable through consumer-owned doctor commands |
| [runtime-self-provisioning](runtime-self-provisioning.md) | How a plugin provisions its own runtime with no manual step and **no dependency on a sibling launcher** — the layered bootstrap (self-provisioning binstub → sessionStart auto-stamp → skill-driven readiness self-check) + toolchain self-acquisition (vendored uv, pip-index bridge), reaching confined envs (Copilot app, cloud agent) |
| [installer-readiness-modules](installer-readiness-modules.md) | How enabled machine-gated plugins publish attributable installer/readiness modules (or an explicit decline), how discovery joins settings to installation-cell provenance without cache/PATH assumptions, and how strict graph validation produces an execution-free deterministic plan |
| [cross-platform-parity](cross-platform-parity.md) | One behavior across Windows and Linux/WSL: shells, UTF-8, the WSL/Windows boundary, binstubs |
| [windows-background-process-launch](windows-background-process-launch.md) | How Windows background children and daemons remain invisible from consoleless parents without depending on Default Terminal behavior |
| [project-scoped-invocation](project-scoped-invocation.md) | Reach any layer against an explicitly named project (`--project`), CWD-independently, and the per-project `<repo>` binstub as a uniform `<repo> <layer> …` dispatcher over the agent-* fleet |
| [marketplace-installation-cells](marketplace-installation-cells.md) | Qualify runtime, state, lifecycle, invocation, composition, project adoption, migration, and uninstall by globally distinguishing marketplace provenance so same-named plugin suites coexist safely |
| [durable-vs-versioned-runtime](durable-vs-versioned-runtime.md) | When a plugin carries an expensive, warm, stateful runtime (heavy stack + loaded model) that must outlive routine service cutovers: a durable runtime + warm daemon on its own lifecycle, decoupled from the swappable versioned runtime, config-resolved + capability-matched per host |
| [uniform-runtime-resolution](uniform-runtime-resolution.md) | Exactly one way to resolve+spawn a versioned runtime's interpreter — marker → `last-known-good` → newest complete slot, junction-free, never a `venv`/`.venv` link, never a PATH python — reachable identically from a binstub, a service unit, a hook, and an agent, so no two callers ever bind different slots |
| [graceful-daemon-cutover](graceful-daemon-cutover.md) | How a long-lived local service updates its version **without killing in-flight, non-resumable work** — and **the installer drives the cutover automatically** (no externally-driven `deploy` command): the shared `zdd` active/passive primitive (routing-table flip + drain at a safe cutover point + breadcrumb recovery), the consumer contract each daemon implements, and per-plugin adoption (agent-bridge reference; agent-index service+engine; agent-dispatch repossession; agent-vault connection-owner) |
| [session-state-access](session-state-access.md) | How worktree↔session discovery stays O(worktrees) at any history size: resolve session-state by exact id via the registry, quarantine the unbounded directory sweep to one explicit user/agent-initiated backfill/recovery verb |
| [lifecycle-activity-logging](lifecycle-activity-logging.md) | How worktree/session lifecycle events stay a reconstructable trace: the durable-coarse activity log vs. the ephemeral-verbose setup log, one fail-silent writer, cross-platform parity, symmetric start/end marks, and a per-launch `launch_id` correlation key |
| [codespace-repo-provenance](codespace-repo-provenance.md) | How a provider plugin defines a CodeSpace venue's **repo provenance** (vessel→product `workspace_repo` → the agent's `/workspaces/<product>` checkout + ACP cwd) and its in-venue plugins, via agent-codespaces' two convention-discovered manifest fields — `codespaceConfig` and `codespacePlugins` — so the golden path works with **no control-plane repo or user-level pointer** |

The **runtime deploy contract** (venv + binstub + manifest, `uv`, marketplace-vs-
runtime split) is its own established pattern doc:
[`install-contract.md`](../install-contract.md).

## See Also

- Intent: [`visions/plugin-services/`](../../visions/plugin-services/README.md) —
  the plugin service model these patterns realize.
- Reality: [`architecture.md`](../architecture.md) — the as-is topology, ports,
  and install map.
- Contribution: [`AGENTS.md`](../../AGENTS.md), `CONTRIBUTING.md`, and the
  `copilot-extensions-harness:contributing-to-copilot-extensions` harness skill (routes design work here).
