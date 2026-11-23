"""Fixture-owned selection without installing an interpreter or changing host state."""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import sys

import pytest


@pytest.fixture(autouse=True)
def prepared_controller_uv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "prepared-uv"
    directory.mkdir()
    if os.name == "nt":
        return directory
    bash = shutil.which("bash")
    assert bash is not None
    uv = directory / "uv"
    uv.write_text(
        f"#!{bash}\n"
        'if [[ -n "${CONTROLLER_UV_TRACE:-}" ]]; then printf "%s\\n" "$*" >> "$CONTROLLER_UV_TRACE"; fi\n'
        '[[ -z "${UV_PYTHON+x}${UV_PYTHON_PREFERENCE+x}${UV_MANAGED_PYTHON+x}'
        '${UV_NO_MANAGED_PYTHON+x}${VIRTUAL_ENV+x}${CONDA_PREFIX+x}${PYTHONHOME+x}${PYTHONPATH+x}'
        '${UV_WORKING_DIR+x}${UV_WORKING_DIRECTORY+x}${UV_ISOLATED+x}${UV_ENV_FILE+x}'
        '${UV_PROJECT+x}${UV_FROZEN+x}${UV_LOCKED+x}${UV_NO_SYNC+x}" ]] || exit 96\n'
        'case "$*" in\n'
        '  --version) printf "uv 0.8.0\\n" ;;\n'
        '  "python find --no-project --system --managed-python --no-python-downloads cpython@3.11.18")\n'
        f"    printf '%s\\n' {shlex.quote(sys.executable)} ;;\n"
        '  *) exit 97 ;;\n'
        'esac\n',
        encoding="utf-8",
    )
    uv.chmod(0o755)
    monkeypatch.setenv("PATH", str(directory) + os.pathsep + os.environ.get("PATH", ""))
    return directory
