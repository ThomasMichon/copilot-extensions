"""Installed preparation and health for canonical Manager runtime construction."""

from __future__ import annotations

import importlib
import json
import os
import stat
import sys
from collections.abc import Callable
from importlib.metadata import distribution
from os import PathLike
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from ._installation_context_base import path_is_within
else:
    from ._installation_context import path_is_within

# The complete helper set this release's retained versioned_runtime.py loads.
# It matches the canonical engine and retain_helpers() bundle.
RETENTION_HELPERS = (
    "runtime_launcher.py", "versioned_runtime.py", "versioned_runtime_leases.py",
    "versioned_runtime_source.py", "versioned_runtime_identity.py",
    "versioned_runtime_fingerprint.py", "versioned_runtime_construction.py",
    "versioned_runtime_publication.py", "versioned_runtime_cache.py",
    "daemon_instance_runtime.pyz",
)


def _native_path(path: Path) -> Path:
    value = os.path.abspath(path)
    if not value.startswith("\\\\?\\"):
        value = "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value
    return Path(value)


def _complete_retention_bootstrap(slot: Path, source: Path) -> None:
    """Add helpers an older installed engine did not retain for this candidate.

    A self-update runs the installed Manager's engine, which writes only the
    helper names it knew. This candidate copies its own missing helpers from
    the same source that engine reads, before the inventory is sealed.
    """
    bootstrap = slot / ".runtime-bootstrap"
    if os.name == "nt":
        bootstrap, source = _native_path(bootstrap), _native_path(source)
    try:
        info = bootstrap.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise RuntimeError("Manager retention bootstrap must be an ordinary directory")
    for name in RETENTION_HELPERS:
        if os.path.lexists(bootstrap / name):
            continue
        try:
            info = (source / name).lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise RuntimeError("Manager retention helper source must be an ordinary file")
        content = (source / name).read_bytes()
        with (bootstrap / name).open("xb") as stream:
            stream.write(content)


def _completed_progress(phase: str) -> Callable[[], None]:
    units = 0

    def completed() -> None:
        nonlocal units
        units += 1
        if units == 1 or units % 128 == 0:
            if units > 1:
                print(file=sys.stderr)
            print(
                f"Manager preparation progress: phase={phase} completed-units={units}",
                file=sys.stderr, flush=True,
            )
        else:
            print(".", end="", file=sys.stderr, flush=True)

    return completed


def prepare_runtime_candidate(data: dict[str, Any]) -> None:
    import worktree_manager

    environment = Path(sys.prefix).resolve()
    slot = environment.parent if environment.name == ".venv" else environment
    if not path_is_within(Path(worktree_manager.__file__), slot):
        raise RuntimeError("Manager preparation must run from the installed candidate")
    from .self_install_payload import _copy_payload

    if not (slot / "application.json").is_file():
        # Retained build metadata may rewrite editable dependencies. The engine
        # separately pins the original prepared source before and after health.
        source = Path(data.get("source_payload", data["frozen_plugin"]))
        _copy_payload(
            source, slot / "payload",
            mark_complete=not (source / "runtime-source.json").exists(),
            on_progress=_completed_progress("payload-copy"),
        )
    from .self_install import _expected_binstub_contents, _expected_control_plane_provider_manifest
    from .self_install import _runtime_installer

    _complete_retention_bootstrap(
        slot, slot / "runtime" if (slot / "application.json").is_file()
        else Path(data["frozen_plugin"]) / "scripts",
    )
    cast(Callable[[], ModuleType], _runtime_installer)()
    uninstall = importlib.import_module("runtime_uninstall")

    uninstall.seal_inventory(
        slot, frontdoors=_expected_binstub_contents(
            application_slot=slot if (slot / "application.json").is_file() else None,
        ),
        provider=_expected_control_plane_provider_manifest(root=slot.parent.parent),
        on_progress=_completed_progress("inventory"),
    )
    # The driver shares diagnostic output with the line-delimited bootstrap receipt.
    print(file=sys.stderr, flush=True)


def verify_runtime_candidate() -> None:
    import worktree_manager

    environment = Path(sys.prefix).resolve()
    slot = environment.parent if environment.name == ".venv" else environment
    if not path_is_within(Path(worktree_manager.__file__), slot):
        raise RuntimeError("Manager import is outside its constructed runtime")
    installed = distribution("copilot-extensions-worktree-manager")
    if not path_is_within(Path(cast("str | PathLike[str]", installed.locate_file(""))), slot):
        raise RuntimeError("Manager distribution is outside its constructed runtime")
    from .self_install import payload_version

    if not (slot / "application.json").is_file() and (
        payload_version(slot / "payload") != worktree_manager.__version__
    ):
        raise RuntimeError("Manager payload and installed package versions differ")
    if not (slot / "application.json").is_file() and (
        slot / "payload" / "runtime-source.json"
    ).exists():
        tomllib = importlib.import_module("tomllib")
        from .self_install import _runtime_installer

        project = tomllib.loads((slot / "payload" / "pyproject.toml").read_text(encoding="utf-8"))
        installer = cast(Callable[[], ModuleType], _runtime_installer)()
        installer.runtime_source.read_source_identity(
            slot / "payload", project["project"]["version"], plugin="worktree-manager",
        )
    origin = json.loads(installed.read_text("direct_url.json") or "{}")
    if origin.get("dir_info", {}).get("editable", False):
        raise RuntimeError("Manager construction must install a non-editable package")
    assets = Path(worktree_manager.__file__).resolve().parent / "_runtime_assets"
    if (slot / "application.json").is_file() and not all(
        (assets / path).is_file() for path in (
            "scripts/runtime_install.py", "scripts/runtime_launcher.py",
            "references/control-plane-provider.json", "references/uninstall-contract.json",
        )
    ):
        raise RuntimeError("Manager wheel is missing its operational assets")
    for name in RETENTION_HELPERS:
        if not (slot / ".runtime-bootstrap" / name).is_file():
            raise RuntimeError(f"Manager retention bootstrap is missing {name}")
    # Import the real CLI closure before completion, without running a command.
    from . import __main__  # noqa: F401
