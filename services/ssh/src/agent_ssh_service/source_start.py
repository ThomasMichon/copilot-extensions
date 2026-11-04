"""Strict installed Source receiver; local lifecycle authority remains separate."""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
from pathlib import Path
import stat
import sys
import time
from collections.abc import Callable
from types import ModuleType
from typing import Any, cast

from fleet_manager.private_state import private_directory, read_private_file

from .config import ServiceConfig, absolute, check, decode

ROOT_ENV = "AGENT_SSH_SERVICE_HOME"
POLICY_ENV = "AGENT_SSH_SERVICE_CONFIG"


def load_provider(slot: Path) -> ModuleType:
    """Load only this physical application's co-delivered canonical provider."""
    path = slot / "install.py"
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise RuntimeError("installed-Source-provider-unsupported") from None
    check(stat.S_ISREG(info.st_mode) and not path.is_symlink()
          and not getattr(info, "st_file_attributes", 0) & 0x400,
          "invalid-installed-Source-provider")
    spec = importlib.util.spec_from_file_location("_ssh_installed_source_provider", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("installed-Source-provider-unsupported")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def selected_source() -> Any:
    """Check local custody before invoking the installed association reader."""
    prefix = absolute(os.path.abspath(sys.prefix))
    check(prefix.name == ".venv", "installed-nested-application-required")
    slot = prefix.parent
    check(slot.parent.name == "versions", "installed-numbered-application-required")
    root = slot.parent.parent
    selected = os.environ.get(ROOT_ENV)
    check(bool(selected), "explicit-installation-root-required")
    requested = absolute(selected)
    check(requested.is_dir() and requested.samefile(root), "installation-root-mismatch")
    declared_slot = requested / "versions" / slot.name
    check(declared_slot.is_dir() and declared_slot.samefile(slot), "installation-slot-mismatch")
    # Preserve the admitted root's receipt spelling, not a native executable alias.
    slot = declared_slot
    private_directory(requested)
    provider = load_provider(slot)
    qualify = getattr(provider, "qualify_installed_application", None)
    if not callable(qualify):
        raise RuntimeError("installed-Source-provider-unsupported")
    return qualify(
        slot, component="ssh-service", command="agent-ssh-service",
        entry_point="agent_ssh_service.__main__:main", deadline=time.monotonic() + 120,
    )


def selected_policy(path: Path, binding: Any) -> ServiceConfig:
    """Read an explicit private policy without adopting or repairing state."""
    path = absolute(str(path))
    config = ServiceConfig.from_dict(decode(read_private_file(path, 1024 * 1024)))
    root = binding.root.resolve()
    data = absolute(config.to_dict()["data_dir"])
    check(not path.is_relative_to(root), "configuration-must-be-outside-runtime")
    check(not data.is_relative_to(root) and not root.is_relative_to(data),
          "service-data-must-be-outside-runtime")
    if data.exists():
        private_directory(data)
    return config


def selection_reader(binding: Any) -> Callable[[], str | None]:
    provider = load_provider(binding.slot)
    runtime, _ = provider._resources(binding.slot)
    return lambda: cast(str | None, runtime.current_version(binding.root))


def ensure_selected(binding: Any) -> None:
    check(selection_reader(binding)() == binding.slot.name,
          "installed-generation-no-longer-selected")


def dispatch(argv: list[str]) -> int:
    """Receive only declared Source/local operations; legacy foreground is separate."""
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("operation", choices=("__source-start", "__local-serve", "__local-stop"))
    policy = os.environ.get(POLICY_ENV)
    parser.add_argument("--config", type=Path, default=Path(policy) if policy else None,
                        required=not bool(policy))
    arguments = parser.parse_args(argv)
    binding = selected_source()
    config = selected_policy(arguments.config, binding)
    from . import local_service

    if arguments.operation == "__source-start":
        result = local_service.start(binding, arguments.config, config)
        check(result.get("state") == "ready", "local-service-readiness-unconfirmed")
        return 0
    if arguments.operation == "__local-stop":
        local_service.stop(binding, config)
        return 0
    return asyncio.run(local_service.serve(binding, config))
