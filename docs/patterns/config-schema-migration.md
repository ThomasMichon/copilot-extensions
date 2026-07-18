# Pattern: config-schema-migration

**Serves:** *Vision plugin-services* §Features/`install-adopt-boundary`,
§Behaviors/`install-leaves-repos-unaltered` (install/update *may* migrate
machine-local config **schema**).
**Exemplars:** agent-worktrees (`~/.agent-worktrees/{config,repos,projects}.yaml`),
agent-codespaces (`~/.agent-codespaces/adopted-repos.yaml`).
**Primitive:** the vendored [`libs/config-migrate`](../../libs/config-migrate/README.md)
library (dist `agent-config-migrate`, module `config_migrate`).

## Problem

Plugins accumulate persisted config/state schemas. SQLite stores and
`deploy-manifest.json` already version and migrate cleanly (a `schema_version`
column / key + ordered migration steps). The **machine-local YAML configs did
not** — upgrading a YAML shape meant a local agent noticing drift and
hand-editing: brittle, unrepeatable, and invisible to review. A plugin needs a
way to evolve its machine-local config shape **deterministically**, on the
`install`/`update` side the [install-vs-adopt boundary](install-vs-adopt-boundary.md)
already sanctions ("install/update *may* migrate machine-local config schema").

## Standard approach

**Give each machine-local config an explicit `schema_version` and a scripted,
migrate-by-rewrite upgrade path.** Use the shared `config_migrate` primitive,
vendored per [à-la-carte independence](a-la-carte-independence.md) (each plugin
carries its own copy under `plugins/<p>/libs/config-migrate` and installs it into
its own venv, like `ssh-manager`).

**Migrate-by-rewrite, not a legacy-tolerant loader.** The on-disk config converges
to the current shape; the loader targets *one* shape. All legacy knowledge is
confined to ordered `vN -> vN+1` migrators (the single source of shape-change
truth) — the loader never grows independent legacy-handling code that expands
without bound.

**One registry, two call sites:**

| Call | Where | Effect |
|------|-------|--------|
| `migrate_doc(doc, schema_id, reg)` | the plugin's config **loader**, on read | migrates the parsed doc **in memory** so a still-old file loads at the current shape *before* `update` has rewritten it (the read-before-migrate window) |
| `migrate_file(path, schema_id, reg)` | the plugin's **install/update** flow, once | **persists** the migrated file atomically (temp + rename) with a `.bak` backup |

**Scope is machine-local only.** This pattern migrates a plugin's *machine-local*
YAML (`~/.agent-*/…`). **Repo-committed** config (`machines.yaml`,
`codespaces.yaml`, in-repo `.agent-worktrees/*`, checked-in `*.mcp.yaml`) is
**warn-only** on install/update — its migration is an `adopt` concern (see the
[install-vs-adopt boundary](install-vs-adopt-boundary.md)). SQLite and
`deploy-manifest.json` already solve their own versioning and are untouched.

**Baseline first, real migrator later.** Register a schema at **v1** with *no*
migrators; the first migrate merely stamps `schema_version: 1` (a no-shape-change
stamp is inserted textually so comments survive; a real transform reserializes).
The framework is thus *in place before it's needed* — the first `vN -> vN+1`
migrator lands only when a shape actually evolves.

**Model-driven configs carry the version as a field.** When a plugin's config is
a typed model (pydantic / dataclass), add `schema_version` as a **model field**
(or have the plugin's *save* function stamp it) so it round-trips through a
`model_dump`/reserialize save rather than being dropped. Raw-dict configs need no
such step.

## Safety properties (the primitive guarantees)

- **Idempotent** — a second run over a current file is a no-op.
- **Atomic** — an interrupted `migrate_file` leaves the original intact.
- **Backed up** — the pre-migration file is copied to `<name>.bak`.
- **Fail-closed on newer** — a file newer than the registry's current version
  raises rather than lossily downgrading; the caller surfaces "update the plugin."

## The backward-compat invariant

Migrate-by-rewrite only works if old configs can actually *reach* the current
shape. That is a **binding contract**, not a hope (see the invariant in the
[patterns hub](README.md)): keep the `vN -> vN+1` chain unbroken for at least the
**last version or two** (the supported migration window), enforced by checked-in
**prior-version fixtures** (`v_{cur-1}`, `v_{cur-2}`) that must migrate cleanly to
current *and* load. A change that breaks a fixture fails CI — so accidental
backward-incompatibility cannot land; a genuinely breaking change is a deliberate,
fixture-updating act that bumps across the break and gives out-of-window configs a
clear fail-closed message.

## Rationale

The suite already versions its *structured* stores (SQLite, deploy-manifests); the
YAML configs were the gap. Migrate-by-rewrite keeps the loader simple (one shape)
while the install-vs-adopt boundary keeps the power safe (machine-local only). The
fixture-guarded invariant turns "don't break old configs" from a hope into a CI
gate — the same move that makes the SQLite migrations trustworthy.

## See Also

- Primitive: [`libs/config-migrate/README.md`](../../libs/config-migrate/README.md)
- Boundary: [`install-vs-adopt-boundary.md`](install-vs-adopt-boundary.md) —
  which verb may migrate what.
- Vendoring: [`a-la-carte-independence.md`](a-la-carte-independence.md) — each
  plugin carries its own copy of the shared lib.
- Intent: [`visions/plugin-services/`](../../visions/plugin-services/README.md).
