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


CONTAINERS_INIT = "plugins/agent-containers/scripts/init.sh"
CONTAINERS_BLOCK_START = "# The bootstrap interpreter may be older than the package floor; the runtime"
CONTAINERS_BLOCK_END = "# End of runtime Python selection."


def _run_containers_selection(
    tmp_path: Path, *, python_ok: bool, find_ok: bool, install_ok: bool, have_uv: bool,
) -> tuple[str, str]:
    bash = BASH
    if bash is None:
        pytest.skip("bash is unavailable")
    text = (ROOT / CONTAINERS_INIT).read_text(encoding="utf-8")
    start = text.index(CONTAINERS_BLOCK_START)
    block = text[start:text.index(CONTAINERS_BLOCK_END, start)]
    log = tmp_path / "calls.log"
    python = tmp_path / "bootstrap-python"
    python.write_text(
        f"#!/bin/sh\nexit {0 if python_ok else 1}\n", encoding="utf-8", newline="\n",
    )
    python.chmod(0o755)
    state = tmp_path / "installed"
    driver = (
        f'_fail() {{ echo "fail $*" >> "{log.as_posix()}"; }}\n'
        f'_ok() {{ echo "ok $*" >> "{log.as_posix()}"; }}\n'
        "uv() {\n"
        f'  echo "uv $*" >> "{log.as_posix()}"\n'
        f'  if [ "$2" = install ]; then [ {0 if install_ok else 1} -eq 0 ] && : > "{state.as_posix()}"; return; fi\n'
        f'  if [ {0 if find_ok else 1} -eq 0 ] || [ -e "{state.as_posix()}" ]; then echo /managed/python3.11; return 0; fi\n'
        "  return 1\n}\n"
        f'PYTHON_CMD="{python.as_posix()}"\nHAVE_UV={1 if have_uv else 0}\n'
        + block
        + 'echo "selected $PYTHON_CMD"\n'
    )
    result = subprocess.run(
        [bash, "-c", driver], capture_output=True, text=True, timeout=60, check=False,
    )
    calls = log.read_text(encoding="utf-8") if log.exists() else ""
    return calls + result.stdout + f"exit {result.returncode}\n", python.as_posix()


@pytest.mark.parametrize("find_ok", [True, False], ids=["found", "installed"])
def test_containers_init_selects_managed_floor_for_a_python310_caller(
    tmp_path: Path, find_ok: bool,
) -> None:
    output, python = _run_containers_selection(
        tmp_path, python_ok=False, find_ok=find_ok, install_ok=True, have_uv=True,
    )
    assert "selected /managed/python3.11" in output
    assert ("uv python install 3.11" in output) is (not find_ok)
    assert "exit 0" in output


def test_containers_init_retains_a_compatible_caller(tmp_path: Path) -> None:
    output, python = _run_containers_selection(
        tmp_path, python_ok=True, find_ok=True, install_ok=True, have_uv=True,
    )
    assert f"selected {python}" in output
    assert "uv " not in output


@pytest.mark.parametrize("have_uv", [True, False], ids=["uv-cannot-provide", "no-uv"])
def test_containers_init_refuses_python310_without_managed_floor(
    tmp_path: Path, have_uv: bool,
) -> None:
    output, _ = _run_containers_selection(
        tmp_path, python_ok=False, find_ok=False, install_ok=False, have_uv=have_uv,
    )
    assert "fail Python 3.11+ is required" in output
    assert "selected" not in output
    assert "exit 1" in output


def test_containers_powershell_init_selects_the_managed_floor() -> None:
    text = (ROOT / "plugins/agent-containers/scripts/init.ps1").read_text(encoding="utf-8")
    assert "& $pythonCmd -c 'import sys; sys.exit(sys.version_info < (3, 11))'" in text
    assert "& uv python find 3.11" in text and "& uv python install 3.11" in text
    assert "Python 3.11+ is required" in text
