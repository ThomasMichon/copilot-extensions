"""Validated paths for agent-worktrees' coupled machine-local registries."""

from __future__ import annotations

import importlib.util
import os
import platform
import sys
from pathlib import Path
from types import ModuleType


def _legacy_root() -> Path:
    override = os.environ.get("AGENT_HOME", "").strip()
    if override:
        return Path(override) / ".agent-worktrees"  # marketplace-isolation: allow legacy-default registry root
    if platform.system() == "Windows":
        home = Path(os.environ.get("USERPROFILE") or Path.home())
    else:
        home = Path.home()
    return home / ".agent-worktrees"  # marketplace-isolation: allow legacy-default registry root


def _payload_root() -> Path:
    for key in ("AGENT_WORKTREES_PAYLOAD_ROOT", "COPILOT_PLUGIN_ROOT"):
        raw = os.environ.get(key, "").strip()
        if raw:
            return Path(raw)
    return Path(__file__).resolve().parents[2]


def _helper() -> ModuleType:
    helper = _payload_root() / "scripts" / "registry_root.py"
    if not helper.is_file():
        raise ValueError("agent-worktrees registry-root helper is unavailable")
    spec = importlib.util.spec_from_file_location(
        "_agent_worktrees_registry_root", helper
    )
    if spec is None or spec.loader is None:
        raise ValueError("agent-worktrees registry-root helper is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except (ImportError, OSError, SyntaxError) as error:
        sys.modules.pop(spec.name, None)
        raise ValueError(
            "agent-worktrees registry-root helper cannot be loaded"
        ) from error
    return module


def registry_root(legacy_root: Path | None = None) -> Path:
    """Return the only root authorized to hold config/projects/repos YAML."""
    legacy = legacy_root or _legacy_root()
    if not os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "").strip():
        return legacy
    return _helper().resolve_registry_root(
        legacy_root=legacy,
        payload_root=_payload_root(),
        environment=os.environ,
    )


def installation_context() -> dict | None:
    """Return the validated explicit context, or ``None`` in legacy mode."""
    if not os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "").strip():
        return None
    return _helper().resolve_registry_context(
        payload_root=_payload_root(),
        environment=os.environ,
    )


def registry_path(filename: str, *, legacy_root: Path | None = None) -> Path:
    """Return one file beneath the validated coupled-registry root."""
    return registry_root(legacy_root) / filename
