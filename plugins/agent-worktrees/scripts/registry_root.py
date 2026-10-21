#!/usr/bin/env python3
"""Resolve the agent-worktrees registry root without ambient-root fallback."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import platform
import sys
from pathlib import Path
from types import ModuleType
from typing import Mapping

_PLUGIN_ID = "agent-worktrees"
_PAYLOAD_ROOT_KEYS = ("AGENT_WORKTREES_PAYLOAD_ROOT", "COPILOT_PLUGIN_ROOT")


class RegistryRootError(ValueError):
    """The selected installation context cannot own registry access."""


def _legacy_home(environment: Mapping[str, str]) -> Path:
    override = environment.get("AGENT_HOME", "").strip()
    if override:
        return Path(override)
    if platform.system() == "Windows":
        return Path(environment.get("USERPROFILE") or Path.home())
    return Path(environment.get("HOME") or Path.home())


def _payload_root(
    environment: Mapping[str, str],
    explicit: str | os.PathLike[str] | None,
) -> Path:
    raw = explicit
    if raw is None:
        raw = next(
            (environment[key] for key in _PAYLOAD_ROOT_KEYS if environment.get(key)),
            None,
        )
    if raw is None:
        raw = Path(__file__).resolve().parents[1]
    root = Path(raw).expanduser()
    if not root.is_absolute() or not root.is_dir():
        raise RegistryRootError(
            "the owning agent-worktrees payload root is unavailable"
        )
    try:
        manifest = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError) as error:
        raise RegistryRootError(
            "the owning agent-worktrees payload manifest is invalid"
        ) from error
    if not isinstance(manifest, dict) or manifest.get("name") != _PLUGIN_ID:
        raise RegistryRootError(
            "the selected payload is not agent-worktrees"
        )
    return root.resolve()


def _load_context_helper(payload_root: Path) -> ModuleType:
    """Reuse content-verified code, never a previous context-validation result."""
    helper = (
        Path(__file__).resolve().parent
        / "installation-context"
        / "installation_context.py"
    )
    if not helper.is_file():
        raise RegistryRootError(
            "the owning payload has no installation-context validator"
        )
    module_name = "_agent_worktrees_registry_installation_context"
    try:
        sources = {
            path.name: path.read_bytes()
            for path in (helper, *sorted(helper.parent.glob("_installation_context_*.py")))
        }
    except OSError as error:
        raise RegistryRootError(
            "the installation-context validator cannot be read"
        ) from error
    fingerprint = tuple(
        (name, hashlib.sha256(content).digest()) for name, content in sources.items()
    )
    cached = sys.modules.get(module_name)
    if (
        cached is not None
        and getattr(cached, "__file__", None) == str(helper)
        and getattr(cached, "_registry_code_fingerprint", None) == fingerprint
    ):
        return cached
    spec = importlib.util.spec_from_file_location(module_name, helper)
    if spec is None or spec.loader is None:
        raise RegistryRootError(
            "the installation-context validator cannot be loaded"
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        exec(compile(sources[helper.name], str(helper), "exec"), module.__dict__)
    except Exception as error:
        sys.modules.pop(module_name, None)
        raise RegistryRootError(
            "the installation-context validator cannot be loaded"
        ) from error
    module._registry_code_fingerprint = fingerprint
    return module


def resolve_registry_root(
    *,
    legacy_root: str | os.PathLike[str] | None = None,
    payload_root: str | os.PathLike[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Return the legacy root or the explicitly validated cell plugin root."""
    environment = environment if environment is not None else os.environ
    context = environment.get("COPILOT_EXTENSIONS_CONTEXT", "").strip()
    if not context:
        return Path(legacy_root) if legacy_root is not None else (
            _legacy_home(environment) / ".agent-worktrees"
        )  # marketplace-isolation: allow legacy-default registry root

    return Path(
        resolve_registry_context(
            payload_root=payload_root,
            environment=environment,
        )["pluginRoot"]
    )


def resolve_registry_context(
    *,
    payload_root: str | os.PathLike[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict:
    """Return the validated explicit agent-worktrees installation context."""
    environment = environment if environment is not None else os.environ
    context = environment.get("COPILOT_EXTENSIONS_CONTEXT", "").strip()
    if not context:
        raise RegistryRootError("no explicit installation context is selected")

    pointer = Path(context).expanduser()
    if not pointer.is_absolute():
        raise RegistryRootError(
            "COPILOT_EXTENSIONS_CONTEXT must be an absolute path"
        )
    try:
        durable_home = pointer.parents[4]
    except IndexError as error:
        raise RegistryRootError(
            "COPILOT_EXTENSIONS_CONTEXT is outside the installation-cell layout"
        ) from error

    owner = _payload_root(environment, payload_root)
    helper = _load_context_helper(owner)
    try:
        resolved = helper.resolve_context(
            context=pointer,
            plugin_id=_PLUGIN_ID,
            payload_root=owner,
            durable_home=durable_home,
            environment=environment,
        )
        root = Path(resolved["pluginRoot"])
    except Exception as error:
        raise RegistryRootError(str(error)) from error
    if not root.is_absolute():
        raise RegistryRootError(
            "installation-context validation returned a relative plugin root"
        )
    return resolved


if __name__ == "__main__":
    try:
        print(resolve_registry_root())
    except RegistryRootError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2) from error
