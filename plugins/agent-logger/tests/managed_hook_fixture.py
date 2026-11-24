"""Acquisition-free prerequisites shared by native Logger hook controls."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys

import pytest

if sys.platform == "win32":
    HEADLESS = subprocess.CREATE_NO_WINDOW
else:
    HEADLESS = 0


@pytest.fixture(scope="module")
def prepared_helper() -> tuple[Path, Path, Path]:
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("This acquisition-free control requires prepared uv")
    selected = subprocess.run(
        [uv, "python", "find", "--no-project", "--system", "--managed-python",
         "--no-python-downloads", "cpython@3.11.18"],
        capture_output=True, text=True, timeout=15, creationflags=HEADLESS,
    )
    if selected.returncode:
        pytest.skip("This acquisition-free control requires prepared CPython 3.11.18")
    root = subprocess.check_output(
        [uv, "python", "dir"], text=True, timeout=15, creationflags=HEADLESS,
    ).strip()
    return Path(uv), Path(selected.stdout.strip()).resolve(), Path(root)
