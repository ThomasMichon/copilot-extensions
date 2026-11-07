"""Identity of the running interpreter, independent of mutable selectors."""

import sys
from pathlib import Path

import worktree_manager


def running_slot() -> Path | None:
    prefix = Path(sys.prefix).resolve()
    if prefix.name == ".venv":
        prefix = prefix.parent
    return prefix if prefix.parent.name == "versions" else None


def running_generation() -> str | None:
    slot = running_slot()
    return slot.name if slot is not None else None


def running_application_slot() -> Path | None:
    slot = running_slot()
    return slot if slot is not None and (slot / "application.json").is_file() else None


def running_payload_dir() -> Path:
    """Resolve the running payload without consulting an installer or selector."""
    package = Path(worktree_manager.__file__).resolve()
    slot = running_slot()
    retained = (slot if slot is not None else Path(sys.prefix)) / "payload"
    if package.is_relative_to(Path(sys.prefix).resolve()) and (retained / "pyproject.toml").is_file():
        return retained
    return package.parents[2]


def runtime_scripts(package: Path, payload_dir) -> Path:
    """Locate retained runtime resources or their source-layout counterpart."""
    resources = package / "_runtime_assets" / "scripts"
    if resources.is_dir():
        return resources
    return payload_dir() / "scripts"


def route_version() -> str:
    from . import __version__

    return running_generation() or __version__
