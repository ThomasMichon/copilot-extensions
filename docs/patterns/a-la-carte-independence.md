# Pattern: a-la-carte-independence

**Serves:** *Vision plugin-services* §Features/`a-la-carte-installability`,
`graceful-composition`, `self-contained-runtime`; §Behaviors/`standalone-reachability`,
`degrade-gracefully`; §Non-Goals/`no-mandatory-central-coordinator`.
**Exemplars:** agent-mcp (standalone), agent-bridge ↔ agent-codespaces /
agent-containers (provider-manifest registry).

## Problem

Each plugin is installed from the marketplace **independently**. A user picks any
subset. A plugin therefore cannot assume a particular sibling is installed, that a
particular service is running, or that any shared machine-wide plumbing exists — and
yet, when several plugins *are* present, they should cooperate without the user
hand-wiring them.

## Standard approach

**Standalone-first.** A plugin's core function works with only what its own
installer deployed. A single-plugin install is a supported, first-class
configuration — not a degraded one. Reaching a plugin never depends on an external
proxy/tunnel/registry (an **invariant**, see the hub).

**Graceful composition.** Optional cross-plugin capabilities light up **when the
peer is present** and stay dark otherwise. A missing sibling degrades a *feature*,
never the whole plugin. Composition is opportunistic and peer-wise — there is **no
mandatory central coordinator** every plugin depends on.

**The provider-manifest sub-pattern.** When one plugin extends another's service
rather than running its own daemon, it registers as a **namespace provider** through
a filesystem **manifest registry** — it does **not** import its package into the
host service's venv. Each provider drops a small JSON manifest into the host
service's registry dir (e.g. `~/.agent-bridge/providers.d/<name>.json`) from its own
`sessionStart` hook, declaring the `<prefix>:` namespace it serves and an
**absolute** binstub command; the host daemon scans that dir on demand and drives
the provider's binstub **over a process boundary**
(`<command> namespace-list` / `namespace-resolve …`). agent-bridge sources the
`codespace:` / `container:` namespaces (and the codespaces credential relay) this
way. Why a manifest and not an import: the daemon runs from its own isolated
versioned venv where a provider package is neither importable nor on `PATH`, so an
absolute-command manifest is the only seam that survives. Two rules keep it clean:

- **One canonical CLI per plugin.** The provider keeps ownership of its own binstub
  and runtime; the host service **must not re-point** it. Register the *binstub via a
  manifest*, never re-point it.
- **The host degrades if the provider is absent.** A provider is optional: a missing
  or malformed manifest is skipped with a warning and discovery never raises, so a
  peer's absence darkens only that namespace, never the host daemon. The sweep
  reconciles the current desired set rather than retaining providers it saw once,
  and the host's doctor command identifies stale entries and exact cleanup. The
  suite-wide warning, provenance, reconciliation, and doctor rules are the
  [`drop-in-registry-hygiene`](drop-in-registry-hygiene.md) pattern.

**No cross-plugin reach-around.** A plugin talks to a sibling through the sibling's
declared surface (its CLI, its service endpoint, its resolver), never by poking the
sibling's runtime files or assuming its internal layout.

**Optional session-context composition.** `context-injection` is an optional
coordinator, not a prerequisite for any contributor. Each context-producing
plugin retains a payload-relative standalone path. Its producer wrapper uses
that path until the repository proves the exact compatible
`context-injection@copilot-extensions` authority; after proof, the producer
joins the pair-key rendezvous and emits `{}`, while only the authority emits the
aggregate. Missing, incompatible, ambiguous, or inactive coordination restores
standalone behavior. Direct bootstrap and reconciliation side effects never run
through the coordinator.

## Rationale

À-la-carte independence is what lets the marketplace be a *menu* rather than a
bundle. Standalone-first guarantees any single choice works; graceful composition
makes the whole feel coherent when fully installed — without a central authority
whose absence would break everyone.

## See Also

- Intent: [`visions/plugin-services/`](../../visions/plugin-services/README.md)
- Hub: [`docs/patterns/`](README.md) · Reality: [`architecture.md`](../architecture.md)
  (communication paths, provider-manifest registry)
- Hygiene contract:
  [`drop-in-registry-hygiene.md`](drop-in-registry-hygiene.md)
