"""Regression coverage for `Get-SignedBasePython` in the shared installer engine: probing `py -3.13`/`-3.12`/`-3.11`/`-3.10` must not abort
provisioning when a candidate minor version simply isn't installed.

The `py` launcher writes "No suitable Python runtime found" to stderr for any
version it doesn't have, and the script sets $ErrorActionPreference='Stop'
at its top level -- under that preference, writing to the native stderr
stream becomes a terminating error even with a `2>$null` redirect (the same
gotcha already worked around in `Ensure-UvIndex`, a few functions above this
one). Before this fix, a machine lacking Python 3.13 (a very common case --
3.13 is deliberately probed first as the newest, so nearly every host misses
at least the first candidate) would abort the whole `provision` run instead
of just skipping that candidate and falling through to an installed version
such as 3.12."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
_INSTALL_PS1 = PLUGIN.parents[1] / "libs" / "installer-engine" / "installer-engine.ps1"


def _extract_ps1_function(name: str) -> str:
    install_ps1 = _INSTALL_PS1.read_text(encoding="utf-8")
    rest = install_ps1.split(f"function {name}", 1)[1]
    # Each of these functions is closed by a `}` at column 0 (the file's
    # top-level function-closing convention).
    body = rest.split("\n}\n", 1)[0]
    return f"function {name}{body}\n}}"


def _run_harness(
    tmp_path: Path, shell: str, py_stub_body: str, *, py_dir: Path | None = None
) -> subprocess.CompletedProcess:
    exe = shutil.which(shell)
    if not exe:
        pytest.skip(f"{shell} is not installed")
    harness = tmp_path / f"harness-{shell.replace('.exe', '')}.ps1"
    path_prefix = ""
    if py_dir is not None:
        py_dir_ps = str(py_dir).replace("\\", "\\\\")
        path_prefix = f"$env:PATH = '{py_dir_ps}' + [IO.Path]::PathSeparator + $env:PATH\n"
    harness.write_text(
        f"""
$ErrorActionPreference = 'Stop'
$env:OS = 'Windows_NT'
{path_prefix}
{py_stub_body}

{_extract_ps1_function("Invoke-NativeCapture")}
{_extract_ps1_function("Get-SignedBasePython")}

$result = Get-SignedBasePython
Write-Host "RESULT:$result"
""",
        encoding="utf-8",
    )
    return subprocess.run(
        [exe, "-NoProfile", "-File", str(harness)],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )


def _write_py_stub(py_dir: Path, signed_exe: Path | None) -> None:
    """A real *native executable* stub for `py`, not a PowerShell function.

    This matters: the actual `py` launcher writing to stderr and exiting
    non-zero is what PowerShell (under $ErrorActionPreference='Stop') turns
    into a terminating NativeCommandError even past a `2>$null` redirect -- a
    PowerShell *function* stub that calls `[Console]::Error.WriteLine()`
    does not reproduce that native-command error-record behavior, so the
    stub must be a real .cmd on PATH to catch a regression here.
    """
    py_dir.mkdir(parents=True, exist_ok=True)
    if signed_exe is not None:
        lines = [
            "@echo off",
            'if "%1"=="-3.12" (',
            f'  echo {signed_exe}',
            "  exit /b 0",
            ")",
            "echo No suitable Python runtime found 1>&2",
            "exit /b 103",
        ]
    else:
        lines = [
            "@echo off",
            "echo No suitable Python runtime found 1>&2",
            "exit /b 103",
        ]
    (py_dir / "py.cmd").write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")


@pytest.mark.skipif(
    os.name != "nt",
    reason="the py.cmd stub is a native Windows batch file; PowerShell Core's "
    "cross-platform Get-Command does not do Windows-style PATHEXT-less "
    "resolution off Windows, so on a non-Windows host it can never find "
    "(or execute) the stub regardless of which pwsh/powershell binary runs "
    "the harness -- spoofing $env:OS in the harness only bypasses "
    "install.ps1's own OS guard, it cannot make cmd-file execution work",
)
@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh"])
def test_missing_newest_candidate_does_not_abort_probe(tmp_path: Path, shell: str) -> None:
    """Only 3.12 is "installed"; 3.13 must fail-and-skip, not throw."""
    signed_exe = tmp_path / "python312.exe"
    signed_exe.write_text("stub", encoding="utf-8")
    py_dir = tmp_path / "py-stub"
    _write_py_stub(py_dir, signed_exe)
    stub = """
function Get-AuthenticodeSignature {
    param([string]$Path)
    [pscustomobject]@{ Status = 'Valid' }
}
"""
    result = _run_harness(tmp_path, shell, stub, py_dir=py_dir)
    assert result.stdout.strip() == f"RESULT:{signed_exe}"


@pytest.mark.skipif(
    os.name != "nt",
    reason="the py.cmd stub is a native Windows batch file; see the skip "
    "reason on test_missing_newest_candidate_does_not_abort_probe above",
)
@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh"])
def test_no_candidate_installed_returns_null_not_throw(tmp_path: Path, shell: str) -> None:
    """Every probed version is missing: the function returns $null cleanly."""
    py_dir = tmp_path / "py-stub"
    _write_py_stub(py_dir, None)
    stub = """
function Get-AuthenticodeSignature {
    param([string]$Path)
    [pscustomobject]@{ Status = 'Valid' }
}
"""
    result = _run_harness(tmp_path, shell, stub, py_dir=py_dir)
    assert "RESULT:" in result.stdout
    assert "RESULT:python" not in result.stdout
    line = next(line for line in result.stdout.splitlines() if line.startswith("RESULT:"))
    assert line == "RESULT:"
