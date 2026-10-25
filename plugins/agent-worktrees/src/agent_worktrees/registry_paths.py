"""Validated paths for agent-worktrees' coupled machine-local registries."""

from __future__ import annotations

import importlib.util
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import os
import sys
from pathlib import Path
from types import ModuleType


_STATE_ROOT_REQUEST = ContextVar("_state_root_registry_request", default=None)
_REQUEST_ENV = (
    "COPILOT_EXTENSIONS_CONTEXT",
    "AGENT_WORKTREES_PAYLOAD_ROOT",
    "COPILOT_PLUGIN_ROOT",
    "AGENT_RT_ROOT",
    "AGENT_HOME",
    "HOME",
    "USERPROFILE",
)


class StateRootRequestRefused(ValueError):
    """A request's selected installation binding could not be preserved."""


def _request_key() -> tuple:
    return tuple(os.environ.get(name) for name in _REQUEST_ENV)


def _request_context() -> dict | None:
    request = _STATE_ROOT_REQUEST.get()
    if request is None:
        return None
    key, context = request
    if key != _request_key():
        raise StateRootRequestRefused("State-root request installation routing changed")
    return context


def _fresh_context() -> dict:
    return _helper().resolve_registry_context(
        payload_root=_payload_root(),
        environment=os.environ,
    )


def _validated_request_context() -> dict:
    try:
        return _fresh_context()
    except (OSError, ValueError, ImportError) as exc:
        raise StateRootRequestRefused("State-root request installation context refused") from exc


@contextmanager
def _state_root_request_scope():
    """Share one canonical binding only inside a guarded synchronous read."""
    if _STATE_ROOT_REQUEST.get() is not None:
        _request_context()
        yield
        return
    key = _request_key()
    context = (
        _validated_request_context()
        if os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "").strip()
        else None
    )
    token = _STATE_ROOT_REQUEST.set((key, context))
    try:
        yield
    finally:
        _STATE_ROOT_REQUEST.reset(token)
        if key != _request_key():
            raise StateRootRequestRefused("State-root request installation routing changed")
        # Re-read canonical authority before allowing a result to escape.
        # No validated value survives this request, including on failure.
        if context is not None and _validated_request_context() != context:
            raise StateRootRequestRefused("State-root request installation authority changed")


def _legacy_root() -> Path:
    override = os.environ.get("AGENT_HOME", "").strip()
    if override:
        return (
            Path(override) / ".agent-worktrees"
        )  # marketplace-isolation: allow legacy-default registry root
    if sys.platform == "win32":
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
    bootstrap = Path(sys.prefix) / ".runtime-bootstrap"
    if bootstrap.is_symlink():
        raise ValueError("agent-worktrees installed registry bootstrap is a symlink")
    helper = (
        bootstrap / "registry_root.py"
        if bootstrap.is_dir()
        else _payload_root() / "scripts" / "registry_root.py"
    )
    if not helper.is_file() or helper.is_symlink():
        raise ValueError("agent-worktrees registry-root helper is unavailable")
    spec = importlib.util.spec_from_file_location("_agent_worktrees_registry_root", helper)
    if spec is None or spec.loader is None:
        raise ValueError("agent-worktrees registry-root helper is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except (ImportError, OSError, SyntaxError) as error:
        sys.modules.pop(spec.name, None)
        raise ValueError("agent-worktrees registry-root helper cannot be loaded") from error
    return module


def registry_root(legacy_root: Path | None = None) -> Path:
    """Return the only root authorized to hold config/projects/repos YAML."""
    legacy = legacy_root or _legacy_root()
    context = _request_context()
    if not os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "").strip():
        return legacy
    if context is not None:
        return Path(context["pluginRoot"])
    return _helper().resolve_registry_root(
        legacy_root=legacy,
        payload_root=_payload_root(),
        environment=os.environ,
    )


def installation_context() -> dict | None:
    """Return the validated explicit context, or ``None`` in legacy mode."""
    context = _request_context()
    if not os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "").strip():
        return None
    if context is not None:
        return deepcopy(context)
    return _helper().resolve_registry_context(
        payload_root=_payload_root(),
        environment=os.environ,
    )


def registry_path(filename: str, *, legacy_root: Path | None = None) -> Path:
    """Return one file beneath the validated coupled-registry root."""
    return registry_root(legacy_root) / filename
