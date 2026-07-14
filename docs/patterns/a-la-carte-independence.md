# Pattern: a-la-carte-independence

**Serves:** *Vision plugin-services* §Features/`a-la-carte-installability`,
`graceful-composition`, `self-contained-runtime`; §Behaviors/`standalone-reachability`,
`degrade-gracefully`; §Non-Goals/`no-mandatory-central-coordinator`.
**Exemplars:** agent-mcp (standalone), agent-bridge ↔ agent-codespaces /
agent-containers (resolver-import).

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

**The resolver-import sub-pattern.** When one plugin extends another's service
rather than running its own daemon, the extension **package is imported into the
host service's venv** to contribute a namespace resolver (e.g. agent-bridge imports
the `agent_codespaces` / `agent_containers` packages for the `codespace:` /
`container:` resolvers and the credential relay). Two rules keep this from creating
skew:

- **One canonical CLI per plugin.** The imported plugin keeps ownership of its own
  binstub and runtime; the host service **must not re-point** it. Import the
  *package*, not the *binstub*.
- **The importer degrades if the peer package is absent.** The resolver is an
  optional capability of the host service, not a hard dependency.

**No cross-plugin reach-around.** A plugin talks to a sibling through the sibling's
declared surface (its CLI, its service endpoint, its resolver), never by poking the
sibling's runtime files or assuming its internal layout.

## Rationale

À-la-carte independence is what lets the marketplace be a *menu* rather than a
bundle. Standalone-first guarantees any single choice works; graceful composition
makes the whole feel coherent when fully installed — without a central authority
whose absence would break everyone.

## See Also

- Intent: [`visions/plugin-services/`](../../visions/plugin-services/README.md)
- Hub: [`docs/patterns/`](README.md) · Reality: [`architecture.md`](../architecture.md)
  (communication paths, resolver imports)
