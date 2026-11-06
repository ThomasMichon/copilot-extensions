"""Read-only delegated selection evidence, not executing-writer admission."""

from __future__ import annotations

import re
import stat
from pathlib import Path

from . import session_lifecycle_cli as lifecycle


def _slot_generation(root: Path, python: Path | None) -> str | None:
    if python is None or not python.is_absolute():
        return None
    environment = python.parent.parent
    slot = environment.parent if environment.name == ".venv" else environment
    expected = root.resolve() / "versions" / slot.name
    if environment.name == ".venv":
        expected /= ".venv"
    if (
        slot.parent != root / "versions" or slot.name == "dev"
        or python.parent.name not in {"bin", "Scripts"}
        or python.name not in {"python", "python.exe"}
        or python.parent.resolve() != expected / python.parent.name
        or not python.is_file()
    ):
        return None
    bootstrap = slot / ".runtime-bootstrap"
    path = bootstrap / "runtime-generation"
    try:
        if bootstrap.is_symlink() or bootstrap.resolve() != slot.resolve() / bootstrap.name:
            return None
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 64:
            return None
        with path.open("rb") as stream:
            raw = stream.read(65)
        if len(raw) > 64:
            return None
        value = raw.decode("ascii").strip()
    except (OSError, UnicodeError):
        return None
    return value if re.fullmatch(r"[0-9a-f]{32}", value) else None


def observe() -> dict:
    """Recheck the same owner selection around its immutable-slot metadata."""
    unknown = {"installation_root": None, "runtime_generation": None}
    selection = lifecycle._agent_worktrees_runtime_selection(allow_path_fallback=False)
    if selection is None or selection[1] is None:
        return unknown
    _prefix, root, python = selection
    context = lifecycle._agent_bridge_installation_context()
    if python is None and context:
        try:
            python = lifecycle._peer_launch.observe_peer_python(
                "agent-bridge", lifecycle._agent_bridge_owner_root(),
                context, "agent-worktrees",
            )
        except (OSError, ValueError, ImportError):
            return unknown
    generation = _slot_generation(root, python)
    if (
        lifecycle._agent_worktrees_runtime_selection(allow_path_fallback=False) != selection
        or _slot_generation(root, python) != generation
    ):
        return unknown
    if context and python is not None:
        try:
            if lifecycle._peer_launch.observe_peer_python(
                "agent-bridge", lifecycle._agent_bridge_owner_root(),
                context, "agent-worktrees",
            ) != python:
                return unknown
        except (OSError, ValueError, ImportError):
            return unknown
    return {"installation_root": str(root), "runtime_generation": generation}
