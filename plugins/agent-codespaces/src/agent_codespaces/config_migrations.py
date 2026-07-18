"""agent-codespaces config-schema migration wiring.

Plugin-side adapter over the vendored ``config_migrate`` library (see
``libs/config-migrate``). agent-codespaces keeps almost all configuration in
**adopting repos** (`codespaces.yaml`) -- that is repo-committed, so migrating
its schema is an ``adopt`` concern, never install/update. The only
**machine-local** persisted YAML is the adoption manifest
``~/.agent-codespaces/adopted-repos.yaml`` (a list of adopted repo paths); that
is what this module versions.

Two call sites, one registry (mirroring the agent-worktrees exemplar):

* the loader (``config.load_adopted_repos``) calls ``migrate_loaded`` on read
  (lazy, never-raises) so a still-old manifest loads at the current shape before
  ``install``/``update`` has rewritten it;
* the installer calls ``run_migrations`` once (eager) to stamp/upgrade the
  manifest on disk, and ``config.save_adopted_repos`` stamps the current version
  so the marker round-trips through a normal save.

Baseline (R1): registered at **v1** with no migrators -- the first migrate
merely stamps ``schema_version: 1``. The whole module is import-guarded: if the
vendored library is absent, every entry point degrades to a safe no-op.
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


# Stable schema id for the machine-local adoption manifest.
SCHEMA_ADOPTED_REPOS = "agent-codespaces/adopted-repos"

# Current on-disk version for the adoption manifest. Bump + add a vN->vN+1
# migrator (and a prior-version fixture) here when its shape first changes.
ADOPTED_REPOS_VERSION = 1


def _build_registry() -> Any:
    reg = SchemaRegistry()
    reg.register(SCHEMA_ADOPTED_REPOS, current_version=ADOPTED_REPOS_VERSION)
    return reg


REGISTRY = _build_registry() if _AVAILABLE else None


def available() -> bool:
    """True when the vendored ``config_migrate`` library is importable."""
    return _AVAILABLE


def current_version() -> int:
    """Current schema version stamped onto the adoption manifest."""
    return ADOPTED_REPOS_VERSION


def managed_files(adopted_repos_file: Path) -> list[Any]:
    """The machine-local config files under migration management."""
    if not _AVAILABLE:
        return []
    return [ManagedFile(Path(adopted_repos_file), SCHEMA_ADOPTED_REPOS)]


def run_migrations(adopted_repos_file: Path | None = None) -> list[Any]:
    """Eager path: migrate the machine-local manifest in place (idempotent)."""
    if not _AVAILABLE:
        return []
    if adopted_repos_file is None:
        from . import config

        adopted_repos_file = config.ADOPTED_REPOS_FILE
    return _run(managed_files(Path(adopted_repos_file)), REGISTRY)


def migrate_loaded(doc: dict[str, Any], schema_id: str = SCHEMA_ADOPTED_REPOS) -> dict[str, Any]:
    """Lazy path: migrate a parsed config document in memory on read.

    Never raises and never persists: on any problem it returns the input
    document unchanged, so config loading can never be broken by migration.
    """
    if not _AVAILABLE or not isinstance(doc, dict):
        return doc
    try:
        new_doc, _changed = migrate_doc(doc, schema_id, REGISTRY)
        return new_doc
    except Exception:
        return doc


def summarize(results: list[Any]) -> str:
    """One-line-per-file human summary for the CLI/installer."""
    if not results:
        return "config-migrate: nothing to migrate"
    lines = [r.summary() for r in results]
    changed = sum(1 for r in results if getattr(r, "changed", False))
    skipped = sum(1 for r in results if getattr(r, "skipped", False))
    lines.append(f"config-migrate: {changed} migrated, {skipped} skipped, {len(results)} total")
    return "\n".join(lines)


__all__ = [
    "ADOPTED_REPOS_VERSION",
    "REGISTRY",
    "SCHEMA_ADOPTED_REPOS",
    "available",
    "current_version",
    "managed_files",
    "migrate_loaded",
    "run_migrations",
    "summarize",
]
