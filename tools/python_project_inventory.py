"""Canonical Python projects and runtime-root selections, never vendor copies."""
from __future__ import annotations

import os
from pathlib import Path

from standalone_consumers import STANDALONE_CONSUMERS

OPTIONAL_CORE = {"agent-dispatch": ("mcp",), "agent-containers": ("provider-exec",)}
PORTABLE_EXTRA = 'sys_platform != "win32" or platform_machine != "ARM64"'


def manifests(root: Path) -> list[Path]:
    paths = set(root.glob("*/pyproject.toml"))
    paths.update(root.glob("libs/*/pyproject.toml"))
    for directory in ("plugins", "services"):
        paths.update(
            p for p in (root / directory).rglob("pyproject.toml")
            if not {"libs", ".venv", "node_modules", "__pycache__"} & set(p.relative_to(root).parts)
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


def runtime_roots(root: Path, catalog: dict) -> dict[str, list[tuple[str, str, frozenset[str]]]]:
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
    return groups
