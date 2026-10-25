"""Prepare engine repair without importing the damaged engine application."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path

from .runtime_source import ResolvedSource
from .runtime_source_prepare import prepare, preparer
from .self_install import _runtime_installer, default_root

PIN_ENV = "COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN"


def prepare_engine(resolved: ResolvedSource) -> tuple[Path, str]:
    record = prepare(
        preparer(), resolved, "agent-worktrees",
        default_root().absolute() / "runtime-sources/prepared",
    )
    payload = Path(record["payload"])
    declaration = ast.parse(
        (payload / "scripts/versioned_runtime_source.py").read_text(encoding="utf-8"),
    )
    if not any(
        isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "SOURCE_PIN_ENV"
                for target in node.targets)
        and isinstance(node.value, ast.Constant) and node.value.value == PIN_ENV
        for node in declaration.body
    ):
        raise ValueError("Prepared engine installer does not support transaction source pinning")
    installer = payload / "scripts" / ("install.ps1" if os.name == "nt" else "install.sh")
    if "AGENT_WORKTREES_TARGETED_UPDATE" not in installer.read_text(encoding="utf-8"):
        raise ValueError("Prepared engine installer does not support runtime-only repair")
    return payload, json.dumps(record["identity"], sort_keys=True)


def verify_engine(root: Path, pin: str | None) -> None:
    """Require the native lifecycle to have selected the complete pinned slot."""
    if pin is None:
        raise ValueError("Prepared engine repair has no transaction source pin")
    identity = json.loads(pin)
    version = identity["runtime_version"]
    manifest_path = root / "deploy-manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("Prepared engine repair has no ordinary deployment receipt")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    runtime = _runtime_installer().runtime
    selector = root / "current-version"
    slot = runtime.version_dir(root, version)
    if (
        not isinstance(manifest, dict) or manifest.get("service") != "agent-worktrees"
        or any(manifest.get(key) != value for key, value in identity.items())
        or selector.is_symlink() or not selector.is_file()
        or selector.read_text(encoding="utf-8").strip() != version
        or slot.is_symlink() or not slot.is_dir()
        or not isinstance(manifest.get("venv"), str) or Path(manifest["venv"]) != slot
        or not runtime.is_complete(root, version)
    ):
        raise ValueError("Engine repair did not select the complete pinned service source")
