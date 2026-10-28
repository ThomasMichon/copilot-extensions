"""Explicit source closure for the opt-in SDK visibility fixture."""

from __future__ import annotations

import importlib
from pathlib import Path
import sys

from sdk_desktop_observer import ObservationBlocked


PACKAGE_FILES = (
    "__init__.py", "_async.py", "_caller_owned.py", "_daemon_instance_child.py",
    "_daemon_instance_cli.py", "_daemon_instance_clock.py", "_daemon_instance_identity.py",
    "_daemon_instance_linux.py", "_daemon_instance_native.py", "_daemon_instance_owner.py",
    "_daemon_instance_store.py", "_darwin_caller_owned.py", "_interactive_groups.py",
    "_interactive_session.py", "_interactive_terminal.py", "_interpreter.py",
    "_linux_pidfd.py", "_shell_caller_owned.py", "_windows_caller_owned.py",
    "_windows_parent_job.py", "daemon.py", "daemon_broker.py", "daemon_instances.py",
    "diagnostics.py", "interactive.py", "metrics.py", "metrics_accounting.py",
    "native_child.py", "owned_process.py", "startup.py", "streaming.py", "wsl.py",
)
FRAGMENT_FILES = (
    "base", "files", "source", "lock_recovery", "receipts", "snapshot",
    "runtime_slot_ownership", "runtime_slot_completion", "resolution", "guest_environment",
    "activation", "legacy_attribution", "legacy_transition", "legacy_retirement",
    "maintenance", "mode_cli", "deployment", "publication", "powershell",
)
SDK_FILES = (
    *(f"libs/agent-procutil/src/agent_procutil/{name}" for name in PACKAGE_FILES),
    *(f"libs/installation-context/_installation_context_{name}.py" for name in FRAGMENT_FILES),
    "libs/installation-context/installation_context.py",
    "libs/installation-context/installation-context.ps1",
    "libs/installation-context/_installation_context_worker.py",
    "libs/installation-context/daemon_instance_runtime.pyz",
    "libs/agent-procutil/tests/test_shell_caller_owned_native.py",
    "libs/agent-procutil/tests/test_caller_owned_native.py",
    "libs/agent-procutil/tests/caller_owned_fixture.py",
)


def require_sdk_sources(sdk: Path, sources: list[dict]) -> None:
    """Bind the required fixture, SDK fragments and import package to one root."""
    if not sdk.is_absolute() or sdk.resolve() != sdk:
        raise ObservationBlocked("SDK source root is missing or redirected")
    supplied = {Path(item["path"]) for item in sources}
    if not {sdk / name for name in SDK_FILES} <= supplied:
        raise ObservationBlocked("SDK source closure is incomplete or bound to another root")


def bind_sdk_import(sdk: Path) -> Path:
    """Select the verified source before pytest can import an ambient package."""
    if any(name == "agent_procutil" or name.startswith("agent_procutil.") for name in sys.modules):
        raise ObservationBlocked("SDK worker already imported an unbound procutil package")
    source = sdk / "libs/agent-procutil/src"
    sys.path.insert(0, str(source))
    importlib.invalidate_caches()
    package = importlib.import_module("agent_procutil")
    expected = source / "agent_procutil/__init__.py"
    if Path(package.__file__).resolve() != expected.resolve():
        raise ObservationBlocked("SDK worker imported procutil outside its verified source")
    return expected
