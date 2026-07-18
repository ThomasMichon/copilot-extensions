"""agent-containers config-schema migration wiring.

Plugin-side adapter over the vendored ``config_migrate`` library (see
``libs/config-migrate``). agent-containers' ``containers.yaml`` is looked up
env -> cwd -> ``~/.agent-containers/`` and is **read-only** (never written by the
plugin). Migration is scoped to the **machine-local** copy
(``~/.agent-containers/containers.yaml``) only; a ``containers.yaml`` found in a
repo / cwd is repo-committed and is an ``adopt`` concern -- the eager path never
rewrites it (the lazy loader may still migrate it *in memory* on read, which
never persists).

Two call sites, one registry (mirroring the agent-worktrees / agent-codespaces
exemplars):

* the loader (``config.load_config``) calls ``migrate_loaded`` on the parsed
  document (lazy, never-raises) so a still-old config reads at the current shape;
* the installer (``init``) calls ``run_migrations`` once (eager) to stamp/upgrade
  the machine-local file on disk.

Baseline (R3): registered at **v1** with no migrators -- the first migrate merely
stamps ``schema_version: 1``. Import-guarded: absent lib => safe no-op.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:  # pragma: no cover - exercised via the installed venv
    from config_migrate import ManagedFile, SchemaRegistry, migrate_doc
    from config_migrate import run as _run

    _AVAILABLE = True
except ImportError:  # pragma: no cover - lib is vendored at install time
    _AVAILABLE = False


SCHEMA_CONTAINERS = "agent-containers/containers"
CONTAINERS_VERSION = 1


def _build_registry() -> Any:
    reg = SchemaRegistry()
    reg.register(SCHEMA_CONTAINERS, current_version=CONTAINERS_VERSION)
    return reg


REGISTRY = _build_registry() if _AVAILABLE else None


def available() -> bool:
    """True when the vendored ``config_migrate`` library is importable."""
    return _AVAILABLE


def current_version() -> int:
    return CONTAINERS_VERSION


def managed_files(runtime_config: Path) -> list[Any]:
    """The machine-local config files under migration management.

    Only the machine-local ``~/.agent-containers/containers.yaml`` -- never a
    repo/cwd copy (adopt-only).
    """
    if not _AVAILABLE:
        return []
    return [ManagedFile(Path(runtime_config), SCHEMA_CONTAINERS)]


def run_migrations(runtime_config: Path | None = None) -> list[Any]:
    """Eager path: migrate the machine-local containers.yaml in place (idempotent)."""
    if not _AVAILABLE:
        return []
    if runtime_config is None:
        from . import config

        runtime_config = config.RUNTIME_DIR / config.CONFIG_FILENAME
    return _run(managed_files(Path(runtime_config)), REGISTRY)


def migrate_loaded(doc: dict[str, Any], schema_id: str = SCHEMA_CONTAINERS) -> dict[str, Any]:
    """Lazy path: migrate a parsed config document in memory on read (never raises)."""
    if not _AVAILABLE or not isinstance(doc, dict):
        return doc
    try:
        new_doc, _changed = migrate_doc(doc, schema_id, REGISTRY)
        return new_doc
    except Exception:
        return doc


def summarize(results: list[Any]) -> str:
    if not results:
        return "config-migrate: nothing to migrate"
    lines = [r.summary() for r in results]
    changed = sum(1 for r in results if getattr(r, "changed", False))
    skipped = sum(1 for r in results if getattr(r, "skipped", False))
    lines.append(f"config-migrate: {changed} migrated, {skipped} skipped, {len(results)} total")
    return "\n".join(lines)


__all__ = [
    "CONTAINERS_VERSION",
    "REGISTRY",
    "SCHEMA_CONTAINERS",
    "available",
    "current_version",
    "managed_files",
    "migrate_loaded",
    "run_migrations",
    "summarize",
]
