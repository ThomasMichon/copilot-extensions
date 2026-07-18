"""config_migrate -- by-convention YAML config schema versioning + migration.

A small, dependency-light primitive shared (vendored) by Copilot CLI plugins to
give their persisted YAML configs **explicit schema versions** and a
**scripted, migrate-by-rewrite** upgrade path -- replacing ad-hoc "a local agent
notices drift and hand-edits" with committed ``vN->vN+1`` migrators.

Public API::

    from config_migrate import (
        SchemaRegistry, migrate_doc, migrate_file, run, ManagedFile,
        MigrationResult, NewerThanCurrentError, MigrationError, SCHEMA_VERSION_KEY,
    )

Two call sites, one registry:

* ``migrate_doc(doc, schema_id, registry)`` -- in-memory (the loader's lazy path).
* ``migrate_file(path, schema_id, registry)`` -- atomic on-disk rewrite (the
  install/update eager path).

See ``core`` for the safety model (idempotent / atomic / backed-up /
fail-closed-on-newer) and ``registry`` for the schema contract.
"""

from __future__ import annotations

from .core import (
    SCHEMA_VERSION_KEY,
    MigrationError,
    MigrationResult,
    NewerThanCurrentError,
    migrate_doc,
    migrate_file,
    read_version,
)
from .registry import (
    Migrator,
    MigratorGapError,
    SchemaError,
    SchemaRegistry,
    SchemaSpec,
    UnknownSchemaError,
)
from .runner import ManagedFile, run

__version__ = "0.1.0-dev2"

__all__ = [
    "SCHEMA_VERSION_KEY",
    "ManagedFile",
    "MigrationError",
    "MigrationResult",
    "Migrator",
    "MigratorGapError",
    "NewerThanCurrentError",
    "SchemaError",
    "SchemaRegistry",
    "SchemaSpec",
    "UnknownSchemaError",
    "__version__",
    "migrate_doc",
    "migrate_file",
    "read_version",
    "run",
]
