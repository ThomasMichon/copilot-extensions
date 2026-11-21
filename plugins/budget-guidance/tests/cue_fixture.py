"""Prepare only the native assets and existing managed prerequisite for cue tests."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import NoReturn

from agent_procutil import no_window_kwargs

PLUGIN = Path(__file__).resolve().parents[1]


def _unavailable(message: str) -> NoReturn:
    import pytest

    pytest.skip(message)


def prepare_cue_assets(payload: Path) -> None:
    scripts = payload / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    for name, owner in (
        ("bootstrap-uv.ps1", "installer-engine"),
        ("versioned_runtime_native.ps1", "versioned-runtime"),
        ("bootstrap_uv_capture.py", "installer-engine"),
        ("daemon_instance_runtime.pyz", "agent-procutil"),
    ):
        source = PLUGIN / "scripts" / name
        if not source.is_file():
            source = PLUGIN.parents[1] / "libs" / owner / name
        if source.resolve() != (scripts / name).resolve():
            shutil.copy2(source, scripts / name)
    version = json.loads((PLUGIN / "payload-invocation.json").read_bytes())["bootstrapPython"]
    (scripts / "bootstrap-python-version.txt").write_text(f"cpython@{version}\n")


def managed_cue_environment() -> dict[str, str]:
    uv = shutil.which("uv")
    if uv is None:
        _unavailable("cue wrapper requires already-prepared uv")
    environment = {
        key: value for key, value in os.environ.items()
        if key.upper() not in {
            "UV_PYTHON", "UV_PYTHON_PREFERENCE", "UV_MANAGED_PYTHON",
            "UV_NO_MANAGED_PYTHON", "VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONHOME",
            "PYTHONPATH", "UV_WORKING_DIR", "UV_WORKING_DIRECTORY", "UV_ISOLATED",
            "UV_ENV_FILE", "UV_PROJECT", "UV_FROZEN", "UV_LOCKED", "UV_NO_SYNC",
        }
    }
    environment.update(UV_OFFLINE="1", UV_PYTHON_DOWNLOADS="never")
    storage = subprocess.run(
        [uv, "python", "dir"], env=environment, capture_output=True, text=True,
        timeout=10, **no_window_kwargs(),
    )
    assert storage.returncode == 0, storage.stderr
    environment["UV_PYTHON_INSTALL_DIR"] = storage.stdout.strip()
    request = json.loads((PLUGIN / "payload-invocation.json").read_bytes())["bootstrapPython"]
    selected = subprocess.run(
        [uv, "python", "find", "--no-project", "--system", "--managed-python",
         "--no-python-downloads", f"cpython@{request}"],
        env=environment, capture_output=True, text=True, timeout=10, **no_window_kwargs(),
    )
    if selected.returncode:
        _unavailable("cue wrapper's declared managed interpreter is not prepared")
    environment["COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON"] = selected.stdout.strip()
    return environment
