"""agent-logger config-schema migration wiring.

Plugin-side adapter over the vendored ``config_migrate`` library (see
``libs/config-migrate``). agent-logger's ``config.yaml`` lives at
``$AGENT_LOGGER_HOME/config.yaml`` (default ``~/.agent-logger/config.yaml``); it
is **user-authored** and read-only (the plugin never writes it). It is
**machine-local**, so versioning + migration is fully in scope here.

Two call sites, one registry (mirroring the other adopters):

* the loader (``config._load_user_config``) calls ``migrate_loaded`` on the
  parsed document (lazy, never-raises) so a still-old config reads at the current
  shape before ``install``/``update`` has rewritten it;
* the installer calls ``run_migrations`` once (eager) to stamp/upgrade the file
  on disk.

Baseline (R4): registered at **v1** with no migrators -- the first migrate merely
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


SCHEMA_CONFIG = "agent-logger/config"
CONFIG_VERSION = 1


def _build_registry() -> Any:
    reg = SchemaRegistry()
    reg.register(SCHEMA_CONFIG, current_version=CONFIG_VERSION)
    return reg


REGISTRY = _build_registry() if _AVAILABLE else None


def available() -> bool:
    """True when the vendored ``config_migrate`` library is importable."""
    return _AVAILABLE


def current_version() -> int:
    return CONFIG_VERSION


def managed_files(config_path: Path) -> list[Any]:
    """The machine-local config file(s) under migration management."""
    if not _AVAILABLE:
        return []
    return [ManagedFile(Path(config_path), SCHEMA_CONFIG)]


def run_migrations(config_path: Path | None = None) -> list[Any]:
    """Eager path: migrate the machine-local config.yaml in place (idempotent)."""
    if not _AVAILABLE:
        return []
    if config_path is None:
        from . import config

        config_path = config.home_dir() / "config.yaml"
    return _run(managed_files(Path(config_path)), REGISTRY)


def migrate_loaded(doc: dict[str, Any], schema_id: str = SCHEMA_CONFIG) -> dict[str, Any]:
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
    "CONFIG_VERSION",
    "REGISTRY",
    "SCHEMA_CONFIG",
    "available",
    "current_version",
    "managed_files",
    "migrate_loaded",
    "run_migrations",
    "summarize",
]
