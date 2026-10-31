"""Validated paths for agent-worktrees' coupled machine-local registries."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType


def _legacy_root() -> Path:
    override = os.environ.get("AGENT_HOME", "").strip()
    if override:
        return Path(override) / ".agent-worktrees"  # marketplace-isolation: allow legacy-default registry root
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
    root = Path(__file__).resolve().parents[2]
    if "COPILOT_EXTENSIONS_CONTEXT" in os.environ or (root / "plugin.json").is_file():
        return root
    # A stamped payload-dir can name a single-plugin snapshot, not its marketplace.
    manifest_path = _legacy_root() / "deploy-manifest.json"
    if not manifest_path.exists():
        return root
    if manifest_path.is_symlink():
        raise ValueError("agent-worktrees deploy manifest is a symlink")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if not isinstance(manifest, dict) or manifest.get("service") != "agent-worktrees":
        raise ValueError("agent-worktrees deploy manifest belongs to another plugin")
    source = manifest.get("source")
    payload = source.get("path") if isinstance(source, dict) else None
    if not isinstance(payload, str) or not Path(payload).is_absolute():
        raise ValueError("agent-worktrees deployed payload path must be absolute")
    return Path(payload)


def _helper() -> ModuleType:
    bootstrap = Path(sys.prefix) / ".runtime-bootstrap"
    if bootstrap.is_symlink():
        raise ValueError("agent-worktrees installed registry bootstrap is a symlink")
    helper = (
        bootstrap / "registry_root.py" if bootstrap.is_dir()
        else _payload_root() / "scripts" / "registry_root.py"
    )
    if not helper.is_file() or helper.is_symlink():
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
    """Report this original decision, not complete acquisition by its helper."""
    from dropin_registry import ScanAuthority

    from . import config_acquisition as acquisition

    try:
        if not os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "").strip():
            context = None
        else:
            acquisition.opaque_source("installation-context-helper")
            context = _helper().resolve_registry_context(
                payload_root=_payload_root(),
                environment=os.environ,
            )
    except Exception as error:
        acquisition.failed(
            acquisition.current_observations(), "installation-context", "installation-context",
            "installation-context-unavailable", type(error).__name__,
        )
        raise
    acquisition.observe(
        acquisition.current_observations(), "installation-context", "installation-context", ScanAuthority.COMPLETE,
    )
    return context


_INSTALLATION_CONTEXT_OWNER = installation_context


def installation_context_call(callback):
    """Keep the selected callback opaque when it is not the original owner."""
    from . import config_acquisition as acquisition

    try:
        return callback()
    finally:
        if callback is not _INSTALLATION_CONTEXT_OWNER:
            acquisition.failed(
                acquisition.current_observations(), "installation-context", "installation-context",
                "owner-coverage-unavailable", "selected installation-context callback is not the original owner",
            )


def registry_path(filename: str, *, legacy_root: Path | None = None) -> Path:
    """Return one file beneath the validated coupled-registry root."""
    return registry_root(legacy_root) / filename
