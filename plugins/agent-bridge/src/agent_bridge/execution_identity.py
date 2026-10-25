"""Validate optional Bridge identity without requiring a source project."""

from __future__ import annotations

from pathlib import Path


class ExecutionIdentityError(ValueError):
    """The daemon cannot establish an unambiguous local execution binding."""


def source_repository(path: Path) -> Path | None:
    """Locate a source checkout, retaining its stable anchor across detach."""
    from .config import _canonical_repo_root

    path = path.expanduser().resolve()
    if not path.is_dir():
        return None
    for root in (path, *path.parents):
        if (root / ".git").exists():
            return _canonical_repo_root(root)
        if (root / "HEAD").is_file() and (root / "objects").is_dir() and (root / "config").is_file():
            return root
    return None


def resolve_execution_space(cfg, machines: dict) -> str | None:
    """Validate an explicit selector; otherwise use ordinary topology detection."""
    from . import agent_registry

    selected = getattr(cfg, "local_execution_space", None)
    if not machines or selected is None:
        return None
    try:
        agent_registry._detect_local_machine(machines, selected)
    except ValueError as exc:
        raise ExecutionIdentityError("Bridge selector is invalid: " + str(exc)) from exc
    return selected
