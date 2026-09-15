"""Temporary compatibility boundary to the active agent-worktrees runtime."""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from types import ModuleType

from .. import agent_plugin_runtime

ENGINE_SOURCE_ENV = "WORKTREE_MANAGER_AGENT_WORKTREES_SRC"


class EngineRuntimeError(RuntimeError):
    """The production Picker's temporary engine compatibility layer is absent."""


def _context_runtime_root() -> Path | None:
    """The cell-scoped root named by an explicit ``COPILOT_EXTENSIONS_CONTEXT``.

    A context that names the wrong plugin, or is otherwise unreadable, is a
    genuine misconfiguration and raises. But a context that DOES name
    agent-worktrees is only trusted when the shared installation-mode policy
    (``agent_plugin_runtime.marketplace_cells_enabled`` -- the same vendored
    resolver every agent-* plugin's own bootstrap consults) says marketplace
    cells are actually enabled. This is the "same config" invariant: an
    explicit context alone must never be more authoritative here than it
    would be for agent-worktrees itself.
    """
    context = os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "").strip()
    if not context:
        return None
    pointer = Path(context).expanduser()
    if not pointer.is_absolute():
        raise EngineRuntimeError(
            "COPILOT_EXTENSIONS_CONTEXT must be an absolute install receipt"
        )
    try:
        install = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError) as error:
        raise EngineRuntimeError(
            "the selected agent-worktrees installation context is invalid"
        ) from error
    if not isinstance(install, dict) or install.get("pluginId") != "agent-worktrees":
        raise EngineRuntimeError(
            "the selected installation context does not own agent-worktrees"
        )
    if not agent_plugin_runtime.marketplace_cells_enabled():
        return None
    return pointer.parent


def _active_runtime_source() -> Path | None:
    root = _context_runtime_root() or agent_plugin_runtime.legacy_plugin_root(
        "agent-worktrees"
    )
    # Reuse the shared marker/fallback walk (current-version, then
    # last-known-good, then the newest remaining versions/*) instead of
    # reading only current-version -- so a stale/damaged current-version slot
    # here degrades exactly the way engine_client's resolver does, rather
    # than reporting no source at all when a good fallback slot exists.
    for slot in agent_plugin_runtime._runtime_candidates(root):
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
    source = Path(override) if override else (
        _active_runtime_source() if os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "").strip()
        else (_checkout_source() or _active_runtime_source())
    )
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
