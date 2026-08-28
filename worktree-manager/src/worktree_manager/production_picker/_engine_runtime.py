"""Temporary compatibility boundary to the active agent-worktrees runtime."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from types import ModuleType

ENGINE_SOURCE_ENV = "WORKTREE_MANAGER_AGENT_WORKTREES_SRC"


class EngineRuntimeError(RuntimeError):
    """The production Picker's temporary engine compatibility layer is absent."""


def _active_runtime_source() -> Path | None:
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    root = home / ".agent-worktrees"
    marker = root / "current-version"
    try:
        version = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not version:
        return None
    slot = root / "versions" / version
    for candidate in (
        slot / "Lib" / "site-packages",
        slot / "lib" / "python3.13" / "site-packages",
        slot / "lib" / "python3.12" / "site-packages",
        slot / "lib" / "python3.11" / "site-packages",
        slot / "lib" / "python3.10" / "site-packages",
    ):
        if (candidate / "agent_worktrees").is_dir():
            return candidate
    return None


def _checkout_source() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "plugins" / "agent-worktrees" / "src"
        if (candidate / "agent_worktrees").is_dir():
            return candidate
    return None


def ensure_engine_runtime() -> Path:
    """Make the attributable engine package importable for compatibility calls."""
    override = os.environ.get(ENGINE_SOURCE_ENV)
    source = Path(override) if override else (_checkout_source() or _active_runtime_source())
    if source is None or not (source / "agent_worktrees").is_dir():
        raise EngineRuntimeError(
            "the production Picker needs an installed agent-worktrees runtime; "
            "run `worktree-manager setup --apply`"
        )
    source_text = str(source)
    if source_text not in sys.path:
        sys.path.insert(0, source_text)
    plugin_root = source.parent
    libs_root = plugin_root / "libs"
    for lib in (
        "agent-procutil",
        "dropin-registry",
        "plugin-activation",
        "plugin-resolve",
        "config-migrate",
    ):
        lib_source = libs_root / lib / "src"
        if lib_source.is_dir() and str(lib_source) not in sys.path:
            sys.path.insert(0, str(lib_source))
    return source


def engine_module(name: str) -> ModuleType:
    ensure_engine_runtime()
    return importlib.import_module(f"agent_worktrees.{name}")
