"""Prepared-only inputs for inert Worktrees command admission controls."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys

import pytest

PLUGIN = Path(__file__).resolve().parents[2]
REPO = PLUGIN.parents[1]
HEADLESS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def bash_host() -> str | None:
    if os.name != "nt":
        return shutil.which("bash")
    git = shutil.which("git")
    if git is None:
        return None
    root = Path(git).resolve().parents[1]
    return next((
        str(path) for path in (root / "bin/bash.exe", root / "usr/bin/bash.exe")
        if path.is_file()
    ), None)


def snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*") if path.is_file()
    }


@pytest.fixture(scope="module")
def prepared_python() -> tuple[Path, Path, Path]:
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("This acquisition-free control requires prepared uv")
    selected = subprocess.run(
        [uv, "python", "find", "--no-project", "--system", "--managed-python",
         "--no-python-downloads", "cpython@3.10.20"],
        capture_output=True, text=True, timeout=15, creationflags=HEADLESS,
    )
    if selected.returncode:
        pytest.skip("This acquisition-free control requires prepared CPython 3.10.20")
    store = subprocess.check_output(
        [uv, "python", "dir"], text=True, timeout=15, creationflags=HEADLESS,
    ).strip()
    return Path(uv), Path(selected.stdout.strip()).resolve(), Path(store)


@pytest.fixture(scope="module")
def packaged_command(tmp_path_factory: pytest.TempPathFactory) -> Path:
    payload = tmp_path_factory.mktemp("worktrees-command-package") / "agent-worktrees"
    shutil.copytree(PLUGIN / "scripts", payload / "scripts")
    shutil.copytree(PLUGIN / "bin", payload / "bin")
    shutil.copy2(
        REPO / "worktree-manager/bin/terminal-launch.ps1", payload / "bin/terminal-launch.ps1",
    )
    for name in ("plugin.json", "payload-invocation.json", "pyproject.toml", "readiness-isolation.json"):
        shutil.copy2(PLUGIN / name, payload / name)
    previous = sys.path[:]
    try:
        owner = runpy.run_path(str(REPO / "tools/materialize_payload_invocation.py"))
        changes = owner["materialize"](payload, canonical_root=REPO, source_consumer=PLUGIN)
    finally:
        sys.path[:] = previous
    assert changes and not any(line.startswith("SKIP") for line in changes), changes
    return payload


def prepare_application(uv: Path, application: Path, slot: Path, env: dict[str, str]) -> Path:
    result = subprocess.run(
        [str(uv), "venv", "--no-project", "--no-python-downloads",
         "--python", str(application), str(slot)],
        env=env, capture_output=True, text=True, timeout=20, creationflags=HEADLESS,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
