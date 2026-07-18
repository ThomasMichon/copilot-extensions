"""agent-worktrees config-schema migration wiring.

This is the plugin-side adapter over the vendored ``config_migrate`` library
(see ``libs/config-migrate``). It:

* registers agent-worktrees' machine-local YAML schemas
  (``agent-worktrees/{config,repos,projects}``) and their ``vN->vN+1`` migrators,
* enumerates the managed files by convention from the discoverable install root
  (``~/.agent-worktrees/``) for the **eager** install/update path
  (``run_migrations``), and
* exposes ``migrate_loaded`` for the **lazy** loader path -- a never-raising
  wrapper the config readers apply on read so a still-old file loads at the
  current shape *before* ``update`` has rewritten it.

Scope is **machine-local only** (the install-vs-adopt boundary): the runtime
root's ``config.yaml`` / ``repos.yaml`` / ``projects.yaml``. Repo-committed YAML
(``machines.yaml``, in-repo ``.agent-worktrees/*``) is an ``adopt`` concern and
is deliberately NOT migrated here.

Baseline (B4): each schema is registered at **v1** with no migrators, so the
first migrate merely *stamps* ``schema_version: 1`` onto an unmarked file --
proving the pipeline end-to-end with zero behavior change. The first real
``vN->vN+1`` migrator lands only when a shape actually evolves.

The whole module is import-guarded: if the vendored library is not present in
the venv (e.g. running from a source checkout that never ran the installer),
every entry point degrades to a safe no-op rather than breaking config loading.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:  # pragma: no cover - exercised via the installed venv
    from config_migrate import (
        ManagedFile,
        SchemaRegistry,
        migrate_doc,
    )
    from config_migrate import run as _run

    _AVAILABLE = True
except ImportError:  # pragma: no cover - lib is vendored at install time
    _AVAILABLE = False


# Stable schema ids for agent-worktrees' machine-local YAML configs.
SCHEMA_CONFIG = "agent-worktrees/config"
SCHEMA_REPOS = "agent-worktrees/repos"
SCHEMA_PROJECTS = "agent-worktrees/projects"


def _build_registry() -> Any:
    """Register agent-worktrees' machine-local schemas at their current versions.

    All three start at **v1** (baseline). When a shape first changes, bump that
    schema's ``current_version`` here and add the ``vN->vN+1`` migrator -- and a
    prior-version fixture in the tests (the backward-compat invariant).
    """
    reg = SchemaRegistry()
    reg.register(SCHEMA_CONFIG, current_version=1)
    reg.register(SCHEMA_REPOS, current_version=1)
    reg.register(SCHEMA_PROJECTS, current_version=1)
    return reg


REGISTRY = _build_registry() if _AVAILABLE else None


def available() -> bool:
    """True when the vendored ``config_migrate`` library is importable."""
    return _AVAILABLE


def managed_files(install_dir: Path) -> list[Any]:
    """The machine-local config files under migration management.

    Enumerated by convention from the discoverable install root. Missing files
    are fine -- the runner skips them.
    """
    if not _AVAILABLE:
        return []
    return [
        ManagedFile(install_dir / "config.yaml", SCHEMA_CONFIG),
        ManagedFile(install_dir / "repos.yaml", SCHEMA_REPOS),
        ManagedFile(install_dir / "projects.yaml", SCHEMA_PROJECTS),
    ]


def run_migrations(install_dir: Path | None = None) -> list[Any]:
    """Eager path: migrate the managed machine-local files in place.

    Called once from the install/update flow. Idempotent and atomic per file;
    a per-file problem (malformed YAML, newer-than-current) is captured in the
    returned results, not raised. Returns an empty list if the library is
    unavailable.
    """
    if not _AVAILABLE:
        return []
    if install_dir is None:
        from . import config

        install_dir = config.install_dir()
    return _run(managed_files(Path(install_dir)), REGISTRY)


def migrate_loaded(doc: dict[str, Any], schema_id: str) -> dict[str, Any]:
    """Lazy path: migrate a parsed config document in memory on read.

    Never raises and never persists: on any problem (unavailable library,
    unknown schema, a file newer than this build) it returns the input document
    unchanged, so config loading -- on the critical path of every command --
    can never be broken by migration. The eager install/update path is where
    problems surface loudly.
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
    "REGISTRY",
    "SCHEMA_CONFIG",
    "SCHEMA_PROJECTS",
    "SCHEMA_REPOS",
    "available",
    "managed_files",
    "migrate_loaded",
    "run_migrations",
    "summarize",
]
