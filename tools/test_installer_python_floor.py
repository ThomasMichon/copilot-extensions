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


def _run_containers_construction(
    tmp_path: Path, *, python_ok: bool, have_uv: bool, construct_ok: bool = True,
) -> str:
    bash = BASH
    if bash is None:
        pytest.skip("bash is unavailable")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("runtime-construction.sh", "installer-bootstrap.sh"):
        shutil.copy2(ROOT / "plugins/agent-containers/scripts" / name, scripts)
    shutil.copy2(ROOT / "libs/installer-engine/bootstrap-uv.sh", scripts)
    (scripts / "installer-instance-python-version.txt").write_text("cpython@3.11.18\n")
    log = tmp_path / "calls.log"
    python = tmp_path / "python"
    python.write_text(
        f'#!/bin/sh\necho "ambient Python" >> "{log.as_posix()}"\n'
        f"exit {0 if python_ok else 1}\n", encoding="utf-8", newline="\n",
    )
    python.chmod(0o755)
    uv = tmp_path / "uv"
    if have_uv:
        uv.write_text(
            "#!/bin/sh\n"
            f'echo "uv $*" >> "{log.as_posix()}"\n'
            f'echo "selectors $UV_PYTHON,$VIRTUAL_ENV,$UV_NO_MANAGED_PYTHON" >> "{log.as_posix()}"\n'
            f"exit {0 if construct_ok else 23}\n", encoding="utf-8", newline="\n",
        )
        uv.chmod(0o755)
    result = subprocess.run(
        [bash, str(scripts / "runtime-construction.sh"), str(uv), str(tmp_path / "venv")],
        capture_output=True, text=True, timeout=60, check=False,
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ.get("PATH", ""),
             "UV_PYTHON": str(python), "VIRTUAL_ENV": "foreign", "UV_NO_MANAGED_PYTHON": "1"},
    )
    calls = log.read_text(encoding="utf-8") if log.exists() else ""
    return calls + result.stdout + f"exit {result.returncode}\n"


@pytest.mark.parametrize("python_ok", [True, False], ids=["usable-ambient", "broken-ambient"])
def test_containers_construction_selects_the_managed_floor_independently(
    tmp_path: Path, python_ok: bool,
) -> None:
    output = _run_containers_construction(
        tmp_path, python_ok=python_ok, have_uv=True,
    )
    assert f"uv venv --python 3.11 --managed-python {tmp_path / 'venv'}" in output
    assert "ambient Python" not in output
    assert "selectors ,," in output
    assert "exit 0" in output


@pytest.mark.parametrize("have_uv", [True, False], ids=["uv-cannot-provide", "no-uv"])
def test_containers_construction_refuses_without_managed_floor(
    tmp_path: Path, have_uv: bool,
) -> None:
    output = _run_containers_construction(
        tmp_path, python_ok=True, have_uv=have_uv, construct_ok=False,
    )
    assert "ambient Python" not in output
    assert f"exit {23 if have_uv else 127}" in output


def test_containers_powershell_init_selects_the_managed_floor() -> None:
    text = (ROOT / "plugins/agent-containers/scripts/runtime-construction.ps1").read_text(encoding="utf-8")
    assert "New-SignedVenv -VenvDir $VenvDir" in text
    assert "-PythonVersion '3.11' -MinimumSignedBaseVersion ([version]'3.11')" in text
    assert "-WithoutPip" in text
