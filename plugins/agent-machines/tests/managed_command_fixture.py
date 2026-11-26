"""Prepared-only prerequisites for inert Machines dispatcher controls."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parents[1]
HEADLESS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


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
    result = subprocess.run(
        [uv, "python", "find", "--no-project", "--system", "--managed-python",
         "--no-python-downloads", "cpython@3.10.20"],
        capture_output=True, text=True, timeout=15, creationflags=HEADLESS,
    )
    if result.returncode:
        pytest.skip("This acquisition-free control requires prepared CPython 3.10.20")
    store = subprocess.check_output(
        [uv, "python", "dir"], text=True, timeout=15, creationflags=HEADLESS,
    ).strip()
    return Path(uv), Path(result.stdout.strip()).resolve(), Path(store)


@pytest.fixture(scope="module")
def packaged_command(tmp_path_factory: pytest.TempPathFactory) -> Path:
    payload = tmp_path_factory.mktemp("machines-command-package") / "agent-machines"
    shutil.copytree(PLUGIN / "scripts", payload / "scripts")
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


def admission_support(payload: Path, packaged: Path) -> None:
    scripts = payload / "scripts"
    shutil.copytree(packaged / "scripts/_bootstrap_python", scripts / "_bootstrap_python")
    shutil.copy2(
        REPO / "libs/agent-procutil/src/agent_procutil/native_capture.sh",
        scripts / "native_capture.sh",
    )


def prepare_application(uv: Path, application: Path, slot: Path, env: dict[str, str]) -> Path:
    built = subprocess.run(
        [str(uv), "venv", "--no-project", "--no-python-downloads",
         "--python", str(application), str(slot)],
        env=env, capture_output=True, text=True, timeout=20, creationflags=HEADLESS,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    return slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
