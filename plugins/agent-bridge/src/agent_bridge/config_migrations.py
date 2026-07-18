"""agent-bridge config-schema migration wiring.

Plugin-side adapter over the vendored ``config_migrate`` library (see
``libs/config-migrate``). agent-bridge's machine-local ``config.yaml`` lives at
``$AGENT_BRIDGE_CONFIG_DIR/config.yaml`` (default ``~/.agent-bridge/config.yaml``).

Note the two *distinct* migration systems agent-bridge now has, which do not
overlap:

* **Schema** migration (this module): versions the on-disk *shape* of
  ``config.yaml`` via an explicit ``schema_version`` marker + ordered
  ``vN->vN+1`` transforms. The marker is a real field on
  :class:`~agent_bridge.models.ServiceConfig` so it round-trips through
  ``model_dump``/``save_config``.
* **Value** migration (``config.migrate_config``): one-time *semantic* default
  flips (e.g. Session-Hosts-on), guarded by marker files under ``.migrations``.

``auth.yaml`` (the bearer token) and ``sessions.db`` (already versioned at
``SCHEMA_VERSION=13``) are **out of scope** here.

Two call sites, one registry:

* the loader (``config.load_config``) calls ``migrate_loaded`` on the parsed
  document (lazy, never-raises) before constructing ``ServiceConfig``;
* the installer / ``config migrate`` CLI calls ``run_migrations`` once (eager)
  to stamp/upgrade the file on disk.

Baseline: registered at **v1** with no migrators -- the first migrate merely
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


SCHEMA_CONFIG = "agent-bridge/config"

#: Current on-disk schema version for ``config.yaml``. Must equal the
#: ``schema_version`` field default on :class:`ServiceConfig`. Bump both together
#: (and add a ``vN->vN+1`` migrator + prior-version fixture) when the shape
#: first changes.
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
        from .config import config_dir

        config_path = config_dir() / "config.yaml"
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
