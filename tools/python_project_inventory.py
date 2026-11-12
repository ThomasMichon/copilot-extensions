"""Canonical Python projects and runtime-root selections, never vendor copies."""
from __future__ import annotations

import os
from pathlib import Path

from standalone_consumers import STANDALONE_CONSUMERS

OPTIONAL_CORE = {"agent-dispatch": ("mcp",), "agent-containers": ("provider-exec",)}
PORTABLE_EXTRA = 'sys_platform != "win32" or platform_machine != "ARM64"'


def manifests(root: Path) -> list[Path]:
    from application_sources import declared_projects

    paths = set(root.glob("*/pyproject.toml"))
    paths.update(root.glob("libs/*/pyproject.toml"))
    for directory in ("plugins", "services"):
        paths.update(
            p for p in (root / directory).rglob("pyproject.toml")
            if not {"libs", ".venv", "node_modules", "__pycache__"} & set(p.relative_to(root).parts)
        )
    paths.update(
        contained_path(directory / "pyproject.toml", root)
        for directory in declared_projects(root).values()
    )
    return sorted(paths)


def contained_path(path: Path, root: Path) -> Path:
    lexical = Path(os.path.abspath(path))
    if not lexical.is_relative_to(root):
        raise ValueError("source path escapes the repository")
    current = lexical
    while current != root:
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            raise ValueError("repository source path traverses a symlink or junction")
        current = current.parent
    resolved = lexical.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("resolved source path escapes the repository")
    return resolved


def runtime_roots(
    root: Path, catalog: dict, *, root_names: tuple[str, ...] | None = None,
) -> dict[str, list[tuple[str, str, frozenset[str]]]]:
    """Share the root/extra/condition selection with the release-lock input owner."""
    groups = {name: [] for name in ("core", "index-service", "index-engine")}
    special = {
        Path("services") / "index": "index-service",
        Path("plugins") / "agent-index" / "server": "index-engine",
    }
    standalone = {Path(value) for value in STANDALONE_CONSUMERS}
    for name, (directory, _data) in sorted(catalog.items()):
        # Catalog readers may retain either the manifest or its project directory.
        relative = (directory.parent if directory.name == "pyproject.toml" else directory).relative_to(root)
        if relative in special:
            group = special[relative]
        elif relative in standalone or (len(relative.parts) == 2 and relative.parts[0] == "plugins"):
            group = "core"
        else:
            continue
        groups[group].append((name, "", frozenset()))
        extras = ("native",) if group == "index-service" else OPTIONAL_CORE.get(name, ())
        for extra in extras:
            conditions = frozenset({PORTABLE_EXTRA}) if group == "core" else frozenset()
            groups[group].append((name, extra, conditions))
    if root_names is not None:
        available = {name for roots in groups.values() for name, _extra, _conditions in roots}
        if (
            not isinstance(root_names, (tuple, list)) or not root_names
            or any(not isinstance(name, str) or name not in available for name in root_names)
            or len(set(root_names)) != len(root_names)
        ):
            raise ValueError("root_names must select unique canonical runtime roots")
        groups = {
            group: [entry for entry in roots if entry[0] in root_names]
            for group, roots in groups.items()
        }
    return groups
