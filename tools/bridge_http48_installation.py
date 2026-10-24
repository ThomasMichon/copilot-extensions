"""Inventory the actual payload installation owner used by HTTP-48 probes."""

from __future__ import annotations

import ast
from pathlib import Path

from agent_bridge_contract_paths import safe
from bridge_http47_runtime import INSTALLATION_HELPER_PREFIX

ENTRY = INSTALLATION_HELPER_PREFIX + "installation_context.py"


def paths(root: Path) -> tuple[str, ...]:
    directory = safe(root, INSTALLATION_HELPER_PREFIX.rstrip("/"), directory=True)
    result = tuple(sorted(
        path.relative_to(root).as_posix() for path in directory.rglob("*.py")
        if "__pycache__" not in path.parts
    ))
    if ENTRY not in result:
        raise ValueError("HTTP-48 installation helper entrypoint is missing")
    for path in result:
        safe(root, path)
    return result


def required_sources(entry: bytes) -> frozenset[str]:
    """Derive fragment coverage from captured source, never a supplied manifest."""
    declarations = [
        node.value for node in ast.parse(entry.decode("utf-8")).body
        if isinstance(node, ast.Assign) and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "_FRAGMENT_FILES"
    ]
    if len(declarations) != 1:
        raise ValueError("HTTP-48 installation fragment declaration is missing or ambiguous")
    names = ast.literal_eval(declarations[0])
    if (not isinstance(names, tuple) or not names
            or any(not isinstance(name, str) or Path(name).name != name
                   or not name.startswith("_installation_context_") or not name.endswith(".py")
                   or "/" in name or "\\" in name for name in names)
            or len(set(names)) != len(names)):
        raise ValueError("HTTP-48 installation fragment declaration is invalid")
    return frozenset({ENTRY, *(INSTALLATION_HELPER_PREFIX + name for name in names)})


def closure_python(path: str) -> bool:
    return path.startswith(INSTALLATION_HELPER_PREFIX) and path.endswith(".py")
