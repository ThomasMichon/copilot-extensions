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


def _validate_explicit_context() -> None:
    """Raise a clear diagnostic for a genuinely foreign/invalid explicit
    context. This is a UX nicety only -- the actual root/slot selection
    always comes from ``agent_plugin_runtime.resolve_installed_plugin_slot``,
    which applies the identical validated, policy-gated, namespaced-then-
    legacy fallback ``engine_client.installed_engine_command`` uses, so both
    the subprocess and in-process paths can never disagree about which
    install is available.
    """
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
    # Shares the exact namespaced-then-legacy slot selection
    # engine_client.installed_engine_command() uses (marker/fallback walk,
    # completion-marker requirement, validated receipts) so the Picker's
    # in-process import path and the CLI subprocess path can never disagree
    # about which install is available.
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
        "agent-procutil",
        "dropin-registry",
        "plugin-activation",
        "plugin-resolve",
        "config-migrate",
        "single-instance-lease",
    ):
        lib_source = libs_root / lib / "src"
        if lib_source.is_dir() and str(lib_source) not in sys.path:
            sys.path.insert(0, str(lib_source))
    return source


def engine_module(name: str) -> ModuleType:
    ensure_engine_runtime()
    return importlib.import_module(f"agent_worktrees.{name}")
