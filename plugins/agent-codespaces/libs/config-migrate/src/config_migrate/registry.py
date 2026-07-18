"""Schema registry: schema_id -> current version + ordered vN->vN+1 migrators.

A ``SchemaRegistry`` is the single source of shape-change truth for a set of
managed config files. Each managed schema declares:

* a stable ``schema_id`` (e.g. ``"agent-worktrees/config"``),
* a monotonic integer ``current_version`` (matching the small-int convention
  used by ``deploy-manifest`` v3 / agent-bridge ``sessions.db`` v13), and
* an ordered set of pure ``vN -> vN+1`` transforms (``migrators``), each keyed
  by the *source* version ``N`` it upgrades.

The migrators compose: to bring a document from version ``v`` to
``current_version`` the runner applies ``migrators[v]``, ``migrators[v+1]``,
... ``migrators[current-1]`` in order. All legacy knowledge lives here, in the
migrators -- the loader gains no independent legacy-handling code that grows
without bound (see the module docstring in ``core``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

# A migrator is a pure transform of a parsed config document: dict-in, dict-out.
# It upgrades a document from version N to version N+1. It must not mutate its
# input in place (the runner deep-copies defensively, but purity keeps migrators
# testable and composable).
Migrator = Callable[[dict], dict]


class SchemaError(Exception):
    """Base class for schema-registry problems."""


class UnknownSchemaError(SchemaError):
    """Raised when a schema_id has not been registered."""


class MigratorGapError(SchemaError):
    """Raised when the vN->vN+1 migrator chain has a hole below current_version."""


@dataclass(frozen=True)
class SchemaSpec:
    """The registered contract for one managed schema.

    Attributes:
        schema_id: Stable identity, e.g. ``"agent-worktrees/config"``.
        current_version: The version the on-disk config should converge to.
        baseline_version: The version an *unmarked* file is assumed to be at
            (default 1). Absence of the marker means "as old as the baseline",
            never "unknown".
        migrators: Mapping of source version ``N`` -> ``vN->vN+1`` transform.
            Must cover every ``N`` in ``[baseline_version, current_version)``.
    """

    schema_id: str
    current_version: int
    baseline_version: int = 1
    migrators: dict[int, Migrator] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.current_version < 1:
            raise SchemaError(
                f"{self.schema_id}: current_version must be >= 1, got {self.current_version}"
            )
        if self.baseline_version < 1 or self.baseline_version > self.current_version:
            raise SchemaError(
                f"{self.schema_id}: baseline_version {self.baseline_version} out of range "
                f"[1, {self.current_version}]"
            )
        # Every step from baseline up to current must have a migrator.
        missing = [
            n
            for n in range(self.baseline_version, self.current_version)
            if n not in self.migrators
        ]
        if missing:
            raise MigratorGapError(
                f"{self.schema_id}: missing vN->vN+1 migrator(s) for version(s) {missing}"
            )


class SchemaRegistry:
    """A collection of ``SchemaSpec`` keyed by ``schema_id``."""

    def __init__(self) -> None:
        self._specs: dict[str, SchemaSpec] = {}

    def register(
        self,
        schema_id: str,
        current_version: int,
        migrators: dict[int, Migrator] | None = None,
        *,
        baseline_version: int = 1,
    ) -> SchemaSpec:
        """Register (or replace) a schema and return its ``SchemaSpec``."""
        spec = SchemaSpec(
            schema_id=schema_id,
            current_version=current_version,
            baseline_version=baseline_version,
            migrators=dict(migrators or {}),
        )
        self._specs[schema_id] = spec
        return spec

    def get(self, schema_id: str) -> SchemaSpec:
        try:
            return self._specs[schema_id]
        except KeyError as exc:
            raise UnknownSchemaError(f"unregistered schema_id: {schema_id!r}") from exc

    def current_version(self, schema_id: str) -> int:
        return self.get(schema_id).current_version

    def __contains__(self, schema_id: object) -> bool:
        return schema_id in self._specs

    def schema_ids(self) -> list[str]:
        return list(self._specs)
