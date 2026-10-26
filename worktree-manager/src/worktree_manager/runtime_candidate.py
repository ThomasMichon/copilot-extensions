"""Installed preparation and health for canonical Manager runtime construction."""

from __future__ import annotations

import json
import sys
from importlib.metadata import distribution
from pathlib import Path


def prepare_runtime_candidate(data: dict) -> None:
    import worktree_manager

    slot = Path(sys.prefix).resolve()
    if not Path(worktree_manager.__file__).resolve().is_relative_to(slot):
        raise RuntimeError("Manager preparation must run from the installed candidate")
    source = Path(data["frozen_plugin"])
    from .self_install_payload import _copy_payload

    _copy_payload(
        source, slot / "payload",
        mark_complete=not (source / "runtime-source.json").exists(),
    )
    from .self_install import _expected_binstub_contents, _expected_control_plane_provider_manifest
    from .self_install import _runtime_installer

    _runtime_installer()
    from runtime_uninstall import seal_inventory

    seal_inventory(
        slot, frontdoors=_expected_binstub_contents(),
        provider=_expected_control_plane_provider_manifest(root=slot.parent.parent),
    )


def verify_runtime_candidate() -> None:
    import worktree_manager

    slot = Path(sys.prefix).resolve()
    if not Path(worktree_manager.__file__).resolve().is_relative_to(slot):
        raise RuntimeError("Manager import is outside its constructed runtime")
    installed = distribution("copilot-extensions-worktree-manager")
    if not Path(installed.locate_file("")).resolve().is_relative_to(slot):
        raise RuntimeError("Manager distribution is outside its constructed runtime")
    from .self_install import payload_version

    if payload_version(slot / "payload") != worktree_manager.__version__:
        raise RuntimeError("Manager payload and installed package versions differ")
    if (slot / "payload" / "runtime-source.json").exists():
        import tomllib
        from .self_install import _runtime_installer

        project = tomllib.loads((slot / "payload" / "pyproject.toml").read_text(encoding="utf-8"))
        _runtime_installer().runtime_source.read_source_identity(
            slot / "payload", project["project"]["version"], plugin="worktree-manager",
        )
    origin = json.loads(installed.read_text("direct_url.json") or "{}")
    if origin.get("dir_info", {}).get("editable", False):
        raise RuntimeError("Manager construction must install a non-editable package")
    for name in (
        "runtime_launcher.py", "versioned_runtime.py", "versioned_runtime_leases.py",
        "versioned_runtime_source.py", "versioned_runtime_construction.py",
        "versioned_runtime_publication.py",
    ):
        if not (slot / ".runtime-bootstrap" / name).is_file():
            raise RuntimeError(f"Manager retention bootstrap is missing {name}")
    # Import the real CLI closure before completion, without running a command.
    from . import __main__  # noqa: F401
