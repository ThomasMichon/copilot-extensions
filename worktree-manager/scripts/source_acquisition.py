"""Qualify Manager's bootstrap source without an installed application."""
from __future__ import annotations

import importlib
from pathlib import Path
import shutil
import sys
import tomllib

import installer_engine as engine
import versioned_runtime_source as runtime_source


def manager_payload(payload: Path, root: Path) -> Path:
    from worktree_manager.self_install import manager_ref, manager_repo, payload_version

    root = root.resolve()
    if manager_ref(root) != "dev":
        return payload
    receipt = payload / runtime_source.SOURCE_FILE
    if receipt.exists() or receipt.is_symlink():
        if runtime_source.read_source_identity(
            payload, payload_version(payload), plugin="worktree-manager",
        ) is None:
            raise ValueError("Manager prepared source receipt disappeared before validation")
        return payload
    if shutil.which("git") is None:
        raise ValueError("Manager merged-Dev acquisition requires git; no unqualified tarball fallback")
    for package in engine._package_roots(payload):
        project = tomllib.loads((package / "pyproject.toml").read_text(encoding="utf-8"))
        if project["project"]["name"] == "agent-procutil":
            source = package / "src"
            sys.path.insert(0, str(source))
            try:
                module = importlib.import_module("agent_procutil")
            finally:
                sys.path.pop(0)
            origin = module.__file__
            if not isinstance(origin, str) or Path(origin).resolve() != (
                source / "agent_procutil/__init__.py"
            ):
                raise RuntimeError("Manager bootstrap imported a different process helper source")
            break
    else:
        raise ValueError("Manager bootstrap requires its declared local process helper")
    from worktree_manager.runtime_source_self_update import fetch
    from worktree_manager.source_config import absolute_local_source

    return fetch(absolute_local_source(manager_repo(root)), root)
