# config-migrate

By-convention YAML **config schema versioning + scripted migration** for Copilot
CLI plugins. A small, dependency-light library (`pyyaml` only) that plugins
**vendor** into their own venvs (like `ssh-manager` / `zdd`) to give their
persisted YAML configs explicit versions and a committed, repeatable upgrade
path.

> Distribution name: **`agent-config-migrate`** (namespaced to avoid
> dependency-confusion). Import module: **`config_migrate`**.

## Why

Plugin configs have accumulated schemas. SQLite stores and `deploy-manifest.json`
already version and migrate cleanly; the **YAML configs do not**. Upgrading a
YAML shape has meant a local agent noticing drift and hand-editing -- brittle,
unrepeatable, and invisible to review. This library replaces that with **explicit
`schema_version` markers** and **ordered `vN->vN+1` migrators**, applied on the
`install`/`update` (machine-local) side.

This is the machine-local-schema half of the **install-vs-adopt boundary**:
`install`/`update` *may migrate machine-local config schema* but never mutate
repo-committed config (that is an `adopt` concern).

## Model -- migrate-by-rewrite, two call sites, one registry

All legacy knowledge lives in the `vN->vN+1` migrators (the single source of
shape-change truth). Two call sites reuse the same registry:

| Call | Where | Effect |
|------|-------|--------|
| `migrate_doc(doc, schema_id, reg)` | the config **loader** (lazy, on read) | migrates **in memory** so a still-old file read *before* `update` runs still loads at the current shape |
| `migrate_file(path, schema_id, reg)` | `install` / `update` (eager) | **persists** the migrated file atomically (temp + rename) with a `.bak` backup |

Because the loader reuses the migrators, it never grows independent
legacy-handling code; because `install`/`update` rewrites the file, the on-disk
config converges to one shape.

## Safety properties

- **Idempotent** -- a second run over a current file is a no-op.
- **Atomic** -- an interrupted `migrate_file` leaves the original intact.
- **Backed up** -- the pre-migration file is copied to `<name>.bak`.
- **Fail-closed on newer** -- a file newer than the registry's `current_version`
  raises `NewerThanCurrentError` rather than lossily downgrading.
- **Formatting-preserving baseline stamp** -- when a migration only *adds the
  marker* (no shape change), the marker is inserted textually so comments and
  hand-formatting survive; a real `vN->vN+1` transform reserializes (the rewrite
  it already implies).

## Usage

```python
from config_migrate import SchemaRegistry, ManagedFile, migrate_doc, run

reg = SchemaRegistry()

# Baseline: register a schema at v1 with no migrators. The first migrate stamps
# schema_version: 1 onto an unmarked file (proving the pipeline, zero behavior
# change). The first *real* migrator lands only when the shape first evolves:
#
#   def _v1_to_v2(doc: dict) -> dict:
#       doc["renamed"] = doc.pop("old_name", None)
#       return doc
#   reg.register("myplugin/config", current_version=2, migrators={1: _v1_to_v2})
reg.register("myplugin/config", current_version=1)

# Eager path (install/update): persist migrations for the managed files.
results = run(
    [ManagedFile(install_root / "config.yaml", "myplugin/config")],
    reg,
)

# Lazy path (loader): migrate in memory on read.
doc, changed = migrate_doc(loaded_dict, "myplugin/config", reg)
```

## Backward-compatibility invariant

Migrate-by-rewrite only works if old configs can actually *reach* the current
shape. Keep the `vN->vN+1` chain unbroken for at least the **last version or
two** (the supported migration window). Enforce it with checked-in
**prior-version fixtures** that must migrate cleanly to current and load -- a
change that breaks a fixture fails CI, so accidental incompatibility cannot
land. A genuinely breaking change is a deliberate, fixture-updating act.

## Testing

```bash
uv run --with pytest --with pyyaml pytest        # from libs/config-migrate/
```
