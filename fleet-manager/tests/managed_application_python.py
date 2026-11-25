"""Private managed interpreter prerequisite for real application fixtures."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig

import pytest


@pytest.fixture(scope="session")
def managed_application_python(tmp_path_factory: pytest.TempPathFactory) -> Path:
    uv = shutil.which("uv")
    assert uv is not None, "native application fixtures require the runner's uv"
    root = tmp_path_factory.mktemp("mp")
    home = root / "home"
    home.mkdir()
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith("UV_") and key not in {"VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME"}
    }
    environment.update(
        HOME=str(home), USERPROFILE=str(home),
        UV_PYTHON_INSTALL_DIR=str(root / "python"), UV_CACHE_DIR=str(root / "cache"),
    )
    if os.name == "nt":
        environment.update(HOMEDRIVE=home.drive, HOMEPATH=str(home)[len(home.drive):])
    version = ".".join(map(str, sys.version_info[:3]))
    request = version
    if os.name == "nt":
        architecture = {
            "win32": "x86", "win-amd64": "x86_64", "win-arm64": "aarch64",
        }[sysconfig.get_platform()]
        request = f"cpython-{version}-windows-{architecture}-none"
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW
    result = subprocess.run(
        [uv, "python", "install", "--no-bin", "--no-registry", request],
        env=environment, capture_output=True, text=True, timeout=30,
        creationflags=flags,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return root / "python"
