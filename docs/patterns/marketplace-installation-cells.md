# Pattern: marketplace-installation-cells

> **Scope:** private runtime infrastructure for service/runtime-bearing core
> `agent-*` plugins shipped by the `copilot-extensions` marketplace. It is not a
> general plugin pattern. Payload-only plugins, non-`agent-*` plugins,
> downstream marketplaces, and consumer harnesses must not create, validate,
> expose, or depend on installation cells.

**Serves:** *Vision plugin-services/installation-cells*
§Features/`marketplace-scoped-runtime-and-state`,
`source-neutral-installation-home`, `independent-lifecycle`,
`cell-scoped-project-adoption`, `cell-local-invocation`,
`attributable-agent-capabilities`, `provenance-safe-transition`; and all
corresponding Behaviors.
**Exemplars:** Agent Machines is the command-only operative exemplar. Phase 3
of the
[marketplace-scoped-installations effort](../../efforts/active/marketplace-scoped-installations/README.md)
still requires a service-bearing exemplar.

## Problem

Plugin name is not installation identity. Two independent marketplaces can ship
the same plugin name and version, while the current runtime layout gives both
copies the same mutable roots, global commands, service identities, endpoints,
provider registries, and project-adoption records. The second installer can
therefore overwrite, attach to, reconcile, or uninstall the first.

The same ambiguity appears after installation. A hook starts from an attributable
payload, but a reconciler, service supervisor, generated project command, remote
worker, or repair command may start outside that payload. Ambient `PATH`, current
working directory, and a marketplace display name are not durable provenance.

## Standard approach

**Treat one globally distinguishing marketplace source as an installation cell,
then qualify every plugin-owned machine-local resource by that cell.**

The durable user-level home is source-neutral:

```text
~/.copilot-extensions/
  marketplaces/
    <marketplace-id>/
      namespace.json
      plugins/
        <plugin-id>/
          install.json
          versions/
          snapshots/
          state/
          run/
          logs/
      repos/
        <stable-repo-id>/
          identity.json
          <plugin-id>/
```

The concrete root is an install-contract decision, not identity itself. A caller
selects a cell by validated installation context and then derives paths from that
context; it never identifies a cell by taking the nearest similarly named
directory.

## Identity contracts

### Marketplace provenance

`marketplace-id` is derived from a normalized, globally distinguishing source
identity. A configured marketplace key may form its readable prefix, but a
publisher-chosen display name is not unique enough. The normalized source
fingerprint distinguishes repositories, forks, local-directory marketplaces,
and other independently managed sources that reuse the same short name.

The mapping from normalized source to `marketplace-id` is persisted in
`namespace.json`. Updates may move or replace a payload cache without changing
the installation identity. If two source descriptions normalize ambiguously,
management fails and requires an explicit identity rather than merging them.

### Plugin installation identity

A plugin installation is selected by:

```text
(marketplace-id, plugin-id)
```

Runtime version is a slot inside that installation, not a new installation.
Process or service instances may add an instance identifier, but an instance
never drops the marketplace and plugin components.

### Repository identity

Machine-local project adoption uses a normalized remote identity plus a
collision-resistant suffix. Repository basename is display metadata only. Each
adopting cell may own state for the same repository identity without sharing
worktree, session, lease, or generated-command state with another cell.

## Installation context

Every operative process receives one immutable context containing at least:

- normalized marketplace source and `marketplace-id`;
- `plugin-id` and, when applicable, instance identity;
- payload, runtime, state, run, log, cache, endpoint, provider, and repository
  roots;
- the ownership receipt or receipt location used to validate those roots.

Context resolution follows this precedence:

1. explicit context supplied by an installer, reconciler, dispatch, repair, or
   other management caller;
2. provenance attached to a staged payload;
3. the installed marketplace payload boundary;
4. the nearest directory-marketplace catalog;
5. a canonical local marketplace checkout used for development;
6. otherwise fail as ambiguous.

Payload-originated hooks and servers may use runtime-provided
`COPILOT_PLUGIN_ROOT` to locate the immutable payload. Surfaces that receive
`COPILOT_PLUGIN_DATA` / `PLUGIN_DATA` may use that directory for mutable state
only when the host contract proves it is qualified by globally distinguishing
marketplace provenance. The variable's presence alone is not installation
identity, and surfaces that do not receive it still require the same explicit
context.

Child processes receive a freshly constructed context. Launchers replace
conflicting legacy root variables rather than inheriting them, and remote
execution serializes the identity explicitly instead of reconstructing
`~/.agent-*` paths at the destination.

## Ownership receipts

Every mutable or singleton artifact carries attributable ownership:

- `namespace.json` binds the cell to normalized marketplace provenance;
- `install.json` binds one plugin installation to its cell, payload, runtime
  slots, and lifecycle artifacts;
- project-command receipts bind a global project entry point to marketplace,
  repository, plugin, payload, and runtime identity;
- endpoint and provider records name both producer identity and intended
  consumer identity;
- service, task, unit, lease, mutex, pipe/socket, coalescing, and cleanup
  records include cell identity.

Install, repair, migration, and uninstall validate the receipt immediately
before mutation. Missing or mismatched ownership fails closed. A cleanup routine
never infers ownership from a familiar filename or a missing original payload.

## Invocation and composition

Generic plugin shims are checked-in payload-local commands. A skill, hook, agent,
or peer invokes the shim supplied by its own payload, and the shim dispatches
through that payload's installation context to the selected immutable runtime
slot. It never finds a same-named sibling through `PATH`.

Long-lived runtime callers use an installation-local canonical launcher after
validating the same context. Session-start hooks emit a command catalog when an
agent-facing surface cannot interpolate its payload root directly.

Machine-global command space is reserved for attributable project entry points.
A generated project command records and pins its owning cell. Another cell must
choose a distinct entry point or perform an explicit ownership transfer; it may
not replace the wrapper last-writer-wins.

Same-cell sibling composition uses explicit provider and consumer identities.
Cross-cell composition is a separate opt-in contract and always names the target
cell. A missing same-cell peer degrades gracefully; a same-named cross-cell peer
is never a fallback.

Session-context aggregation is one explicit cross-cell contract. Repository
adoption names the exact source-qualified `context-injection` authority, and
each contributor declaration identifies its owning plugin and payload-relative
commands. The authority reconstructs a fresh child environment with the
contributor's validated payload roots instead of inheriting its own. Before
authority proof, each producer invokes its own payload-relative contributor;
it never scans for a same-named coordinator or contributor in another cell.

## Lifecycle and migration

- Namespaced operation is user-local and default-off. A shared user config
  expresses desired legacy/namespaced mode globally or for an exact
  marketplace/plugin; repository config and payloads cannot override it.
- Desired mode is not actual ownership. A namespaced activation receipt pins the
  authoritative runtime root after a new install or migration. Removing the
  flag never silently reactivates legacy state, and enabling it never creates a
  parallel empty runtime beside an existing legacy footprint.
- Provision, update, rollback, repair, supervision, reconciliation, and
  uninstall operate on one validated installation identity.
- One cell-root provisioning lock serializes each complete operative build and
  cutover transaction, not only the short receipt publications within it.
  Snapshot copy is staged in an owned temporary sibling and atomically published;
  recovery removes only a marker-proven, still-unproven directory created by
  that publisher. Successful cutover republishes the active-runtime deploy
  manifest before the adapter reports completion. The manifest keeps the latest
  reconciled payload provenance separate from the selected runtime slot, so an
  explicit historical rollback survives bootstrap while a later payload update
  still reconciles. Failed compare-and-swap leaves the manifest unchanged.
- Legacy unqualified state has no trustworthy owner. Migration requires the
  operator to name the destination cell and writes an ownership receipt.
- New and legacy state found together are reported; registries are never merged
  silently.
- Legacy hooks, services, reconcilers, scheduled work, and leases become
  quiescent only after the destination cell proves ownership and health.
- An orphaned cell remains attributable and inert when its marketplace payload
  disappears. Another marketplace cannot activate or adopt it by name.
- Uninstall removes only artifacts whose receipts match the uninstalling cell.
- A user-local maintenance marker can quiesce hooks, reconciliation,
  provisioning, service ensure/start, scheduled work, and dispatch while
  preserving read-only doctor and explicitly authorized repair surfaces.

Policy and maintenance resolve from the canonical OS user profile, not an
ordinary `HOME`, durable-home override, or repository directory. Windows, native
POSIX, and WSL receipts do not cross-validate. Migration holds both the legacy
plugin lock/lease and cell install lock, and publishes a legacy-side ownership
tombstone with the activation generation. Long-running writers revalidate
maintenance, ownership, and receipt generations before mutation.

The stable schemas, resolver status precedence, and effective-mode table are
defined by the
[install contract](../install-contract.md#installation-mode-governance).
The active effort's
[installation-mode governance](../../efforts/active/marketplace-scoped-installations/installation-mode-governance.md)
retains rollout rationale and acceptance only.

## Transition rule

This pattern is normative before the rollout is complete. Existing
`~/.agent-*`, `~/.local/bin/agent-*`, and unqualified service artifacts remain
legacy reality until their owning phase migrates them. New installation
plumbing must accept installation context now; it must not add another
unqualified root or singleton that later phases need to discover and unwind.

`tools/check-marketplace-isolation.py` inventories those transitional surfaces.
It is report-only while producers migrate; `--verbose` and `--json` expose
categorized file/line findings, and `--strict` becomes the enforcement gate only
after the baseline reaches zero. A compatibility exception must carry an inline
`marketplace-isolation: allow <reason>` marker so the reason remains auditable.

## See Also

- Intent:
  [`visions/plugin-services/installation-cells`](../../visions/plugin-services/installation-cells/README.md)
- Runtime deployment: [`install-contract.md`](../install-contract.md)
- Configuration ownership: [`configuration.md`](../configuration.md)
- Related patterns:
  [`a-la-carte-independence`](a-la-carte-independence.md) ·
  [`install-vs-adopt-boundary`](install-vs-adopt-boundary.md) ·
  [`project-scoped-invocation`](project-scoped-invocation.md) ·
  [`uniform-runtime-resolution`](uniform-runtime-resolution.md) ·
  [`drop-in-registry-hygiene`](drop-in-registry-hygiene.md)
