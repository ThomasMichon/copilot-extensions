"""Prepared-application startup through Vault's existing generation owner."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys

from agent_procutil._daemon_instance_identity import runtime_slot_paths

from . import config, configuration


def _path_name(path: Path) -> Path:
    if os.name == "nt":
        name = str(path)
        if name.startswith("\\\\?\\UNC\\"):
            return Path("\\\\" + name[8:])
        if name.startswith("\\\\?\\"):
            return Path(name[4:])
    return path


def require_selected_slot() -> None:
    prefix = Path(sys.prefix)
    facts = runtime_slot_paths(prefix)
    if facts is None or prefix.name != ".venv":
        raise ValueError("startup requires its physical installed application")
    generation, slot_path, root_path = facts
    slot, root = Path(slot_path), Path(root_path)
    if (
        _path_name(prefix.resolve(strict=True)) != _path_name(prefix)
        or not Path(__file__).resolve().is_relative_to(prefix.resolve())
        or not root.samefile(config.home_dir())
        or (root / "current-version").read_text(encoding="utf-8-sig").strip() != generation
    ):
        raise ValueError("startup selection differs from the physical application")
    helper = slot / ".runtime-bootstrap" / "versioned_runtime.py"
    if _path_name(helper.resolve(strict=True)) != _path_name(helper):
        raise ValueError("application completion helper is redirected")
    spec = importlib.util.spec_from_file_location("_vault_source_completion", helper)
    if spec is None or spec.loader is None:
        raise ValueError("application completion helper is unavailable")
    runtime = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = runtime
    spec.loader.exec_module(runtime)
    complete = getattr(runtime, "is_complete", None)
    if not callable(complete) or not complete(root, generation):
        raise ValueError("application startup requires canonical complete construction")


def command(_args) -> int:
    from .service_generation import ensure_selected

    try:
        require_selected_slot()
        if not configuration.runtime_enabled():
            print("Agent Vault is inactive; runtime activation requires enabled=true.", file=sys.stderr)
            return 2
        if not ensure_selected():
            print("Error: selected Vault generation is not ready for credential work.", file=sys.stderr)
            return 1
        require_selected_slot()
        return 0
    except (OSError, ValueError, RuntimeError, SyntaxError) as error:
        print(
            f"Error: selected Vault application startup failed ({type(error).__name__}).",
            file=sys.stderr,
        )
        return 1
