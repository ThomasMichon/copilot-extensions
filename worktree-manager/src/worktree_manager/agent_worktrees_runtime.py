"""Temporary compatibility helpers for non-Picker agent-worktrees imports.

Production Picker runtime call sites now stay on the public engine-client seam,
but a small number of Worktree Manager-owned helpers and cross-surface tests
still import selected agent-worktrees modules directly. Keep that bootstrap in
one top-level module so ``worktree_manager.production_picker`` itself stays free
of direct ``agent_worktrees`` imports.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from types import ModuleType

from . import agent_plugin_runtime

ENGINE_SOURCE_ENV = "WORKTREE_MANAGER_AGENT_WORKTREES_SRC"


class EngineRuntimeError(RuntimeError):
    """The temporary agent-worktrees compatibility layer is absent."""


def _validate_explicit_context() -> None:
    context = os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "").strip()
    if not context:
        return
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


def _active_runtime_source() -> Path | None:
    _validate_explicit_context()
    slot = agent_plugin_runtime.resolve_installed_plugin_slot("agent-worktrees")
    if slot is None:
        return None
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
        "plugin-resolve",
        "config-migrate",
        "single-instance-lease",
        "lazy-cli-dispatch",
        "work-coalescing-singleton",
        "dropin-registry",
        "plugin-activation",
    ):
        lib_source = libs_root / lib / "src"
        if not lib_source.is_dir():
            repo_root = plugin_root.parent.parent
            if (repo_root / "libs").is_dir() and (repo_root / "plugins").is_dir():
                canonical_source = repo_root / "libs" / lib / "src"
                if canonical_source.is_dir():
                    lib_source = canonical_source
        if lib_source.is_dir() and str(lib_source) not in sys.path:
            sys.path.insert(0, str(lib_source))
    return source


def engine_module(name: str) -> ModuleType:
    ensure_engine_runtime()
    module = importlib.import_module(f"agent_worktrees.{name}")
    if name == "__main__":
        loader = getattr(module, "_load_full_command_surface", None)
        if callable(loader):
            loader()
    return module
