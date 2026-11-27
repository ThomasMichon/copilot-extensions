"""Canonical prepared-UV prerequisites for retained adopter tests."""
from __future__ import annotations

import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
from typing import Any

import pytest


def prepare_codespaces_admission(
    fixture: dict[str, Any], environment: dict[str, str], repo: Path,
) -> None:
    prepare_adopter_admission(fixture, environment, repo, "agent-codespaces")


def prepare_adopter_admission(
    fixture: dict[str, Any], environment: dict[str, str], repo: Path, plugin: str,
) -> None:
    uv = shutil.which("uv")
    if not uv:
        pytest.skip(f"{plugin} application admission requires prepared UV")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    request = f"cpython@{sys.version_info.major}.{sys.version_info.minor}"
    selected = subprocess.run(
        [uv, "python", "find", "--no-project", "--system", "--managed-python",
         "--no-python-downloads", request],
        capture_output=True, text=True, timeout=15, creationflags=flags,
    )
    if selected.returncode:
        pytest.skip(f"{plugin} fixture requires a prepared managed {request}")
    store = subprocess.check_output(
        [uv, "python", "dir"], text=True, timeout=15, creationflags=flags,
    ).strip()
    tools = fixture["home"] / "admission-tools"
    tools.mkdir()
    addressed_uv = tools / Path(uv).name
    shutil.copy2(uv, addressed_uv)
    fixture["managed_runtime_python"] = selected.stdout.strip()
    fixture["admission_uv"] = str(addressed_uv)
    environment.update(
        PATH=str(tools) + os.pathsep + environment["PATH"],
        UV_PYTHON_INSTALL_DIR=store, UV_PYTHON_DOWNLOADS="never",
    )
    source = repo / "plugins" / plugin
    payload = fixture["payload"]
    for name in ("payload-invocation.json", "pyproject.toml", "readiness-isolation.json"):
        if not (payload / name).is_file():
            shutil.copy2(source / name, payload / name)
    previous = sys.path[:]
    try:
        owner = runpy.run_path(str(repo / "tools/materialize_payload_invocation.py"))
        changes = owner["materialize"](payload, canonical_root=repo, source_consumer=source)
        assets = runpy.run_path(str(repo / "tools/materialize_installer_engine.py"))
        for origin, target in assets["derived_file_pairs"](
            repo, payload.parents[1], copied_consumer=(source, payload),
        ):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origin, target)
    finally:
        sys.path[:] = previous
    if plugin == "agent-worktrees":
        for name in (
            "agent-worktrees-update-entry.sh", "agent-worktrees-update-entry.ps1",
        ):
            shutil.copy2(source / "bin" / name, payload / "bin" / name)
        shutil.copy2(
            repo / "worktree-manager/bin/terminal-launch.ps1", payload / "bin/terminal-launch.ps1",
        )
    assert changes and not any(line.startswith("SKIP") for line in changes), changes
