"""POSIX installers construct runtimes at the shared requires-python floor."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
GIT_BASH = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git/bin/bash.exe"
BASH = str(GIT_BASH) if os.name == "nt" and GIT_BASH.is_file() else shutil.which("bash")
REQUEST = re.compile(r"""new_signed_venv\s+\S+\s+\S+\s+["']?(\d+)\.(\d+)["']?""")


def test_posix_installers_request_at_least_the_python311_floor() -> None:
    sources = sorted(ROOT.glob("plugins/*/scripts/install.sh")) + sorted(
        ROOT.glob("plugins/*/scripts/cell_install.py")
    )
    requests: list[tuple[str, tuple[int, int]]] = []
    for path in sources:
        for match in REQUEST.finditer(path.read_text(encoding="utf-8")):
            requests.append((path.relative_to(ROOT).as_posix(), (int(match[1]), int(match[2]))))
    assert requests, "expected POSIX installers to construct signed venvs"
    assert [entry for entry in requests if entry[1] < (3, 11)] == []


RUNTIME_BLOCK_START = "    # The bootstrap interpreter may be older than the package floor;"
RUNTIME_BLOCK_END = '    if [[ ! -x "$VENV_PYTHON" ]]; then'
INIT_SCRIPTS = ("plugins/agent-machines/scripts/init.sh", "plugins/agent-mcp/scripts/init.sh")


def _runtime_block(relative: str) -> str:
    text = (ROOT / relative).read_text(encoding="utf-8")
    start = text.index(RUNTIME_BLOCK_START)
    return text[start:text.index(RUNTIME_BLOCK_END, start)]


def _run_runtime_block(tmp_path: Path, relative: str, *, python_ok: bool, have_uv: bool) -> tuple[str, str]:
    bash = BASH
    if bash is None:
        pytest.skip("bash is unavailable")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    python = bin_dir / "bootstrap-python"
    python.write_text(
        "#!/bin/sh\n"
        f'echo "python $*" >> "{log.as_posix()}"\n'
        f'if [ "$1" = "-c" ]; then exit {0 if python_ok else 1}; fi\n',
        encoding="utf-8", newline="\n",
    )
    python.chmod(0o755)
    driver = (
        "_step() { :; }\n_versioned_slot_clean() { :; }\n"
        f'_fail() {{ echo "fail $*" >> "{log.as_posix()}"; }}\n'
        f'uv() {{ echo "uv $*" >> "{log.as_posix()}"; }}\nPYTHON_CMD="{python.as_posix()}"\n'
        f'VENV_DIR="{(tmp_path / "venv").as_posix()}"\nHAVE_UV={1 if have_uv else 0}\n'
        + _runtime_block(relative)
    )
    result = subprocess.run(
        [bash, "-c", driver], capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stderr
    return log.read_text(encoding="utf-8") if log.exists() else "", python.as_posix()


@pytest.mark.parametrize("relative", INIT_SCRIPTS)
def test_init_runtime_uses_managed_floor_for_a_python310_caller(tmp_path: Path, relative: str) -> None:
    calls, python = _run_runtime_block(tmp_path, relative, python_ok=False, have_uv=True)
    assert f"uv venv {(tmp_path / 'venv').as_posix()} --python 3.11 --allow-existing" in calls
    assert "python -m venv" not in calls


@pytest.mark.parametrize("relative", INIT_SCRIPTS)
def test_init_runtime_keeps_a_compatible_caller_selection(tmp_path: Path, relative: str) -> None:
    calls, python = _run_runtime_block(tmp_path, relative, python_ok=True, have_uv=True)
    assert f"--python {python} --allow-existing" in calls


@pytest.mark.parametrize("relative", INIT_SCRIPTS)
def test_init_runtime_refuses_python310_without_uv(tmp_path: Path, relative: str) -> None:
    calls, _ = _run_runtime_block(tmp_path, relative, python_ok=False, have_uv=False)
    assert "fail Python 3.11+ is required" in calls
    assert "python -m venv" not in calls


def test_powershell_init_requests_the_floor_for_uv_and_fallback_venvs() -> None:
    text = (ROOT / "plugins/agent-machines/scripts/init.ps1").read_text(encoding="utf-8")
    assert "sys.exit(sys.version_info < (3, 11))" in text
    assert "& uv venv $VenvDir --python $uvPythonRequest --allow-existing" in text
    assert "& $pythonCmd -m venv" not in text
