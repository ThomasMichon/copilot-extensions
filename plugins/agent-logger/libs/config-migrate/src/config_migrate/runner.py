"""Runner: migrate a set of managed config files against a registry.

The *discovery* of which files are managed (by convention from an install root)
is deliberately left to the consuming plugin -- only the plugin knows its own
config layout. The runner takes an explicit list of ``ManagedFile`` entries and
applies ``migrate_file`` to each, collecting results and never letting one
file's failure abort the rest (a fail-closed file is reported, not raised).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .core import MigrationError, MigrationResult, migrate_file
from .registry import SchemaRegistry


@dataclass(frozen=True)
class ManagedFile:
    """A config file under migration management: its path and its schema_id."""

    path: Path
    schema_id: str


def run(
    files: Iterable[ManagedFile],
    registry: SchemaRegistry,
    *,
    backup: bool = True,
) -> list[MigrationResult]:
    """Migrate each managed file, returning a result per file.

    A per-file ``MigrationError`` (malformed YAML, newer-than-current) is
    captured as a skipped result with the reason rather than aborting the batch
    -- one unmigratable file must not block the others. Programming errors
    (unregistered schema) still propagate.
    """
    results: list[MigrationResult] = []
    for mf in files:
        try:
            results.append(migrate_file(mf.path, mf.schema_id, registry, backup=backup))
        except MigrationError as exc:
            results.append(
                MigrationResult(
                    path=Path(mf.path),
                    schema_id=mf.schema_id,
                    changed=False,
                    from_version=registry.current_version(mf.schema_id),
                    to_version=registry.current_version(mf.schema_id),
                    skipped=True,
                    reason=str(exc),
                )
            )
    return results
