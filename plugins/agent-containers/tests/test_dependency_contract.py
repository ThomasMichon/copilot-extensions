from __future__ import annotations

import os
import subprocess
import tomllib
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]


def test_paramiko_is_an_optional_provider_exec_dependency() -> None:
    project = tomllib.loads(
        (PLUGIN / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]

    assert not any(dep.startswith("paramiko") for dep in project["dependencies"])
    assert project["optional-dependencies"]["provider-exec"] == ["paramiko>=4.0.0"]


def test_installers_fall_back_and_preserve_bounded_diagnostics() -> None:
    powershell = (PLUGIN / "scripts" / "init.ps1").read_text(encoding="utf-8")
    posix = (PLUGIN / "scripts" / "init.sh").read_text(encoding="utf-8")

    assert "${PluginDir}[provider-exec]" in powershell
    assert "function Write-Warn" in powershell
    assert "Get-Content -LiteralPath $logPath -Tail 40" in powershell
    assert "falling back to the base package" in powershell
    assert "${PLUGIN_DIR}[provider-exec]" in posix
    assert "_warn()" in posix
    assert 'tail -n 40 "$log_path"' in posix
    assert "falling back to the base package" in posix


def test_windows_management_binstub_resolves_powershell_without_path() -> None:
    powershell = (PLUGIN / "scripts" / "init.ps1").read_text(encoding="utf-8")

    assert r'%SystemRoot%\System32\where.exe" pwsh' in powershell
    assert (
        r"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
        in powershell
    )
    assert '"%_PSHOST%" -NoProfile -ExecutionPolicy Bypass' in powershell
    assert "else (powershell " not in powershell


def test_windows_installer_probes_docker_without_a_console() -> None:
    powershell = (PLUGIN / "scripts" / "init.ps1").read_text(encoding="utf-8")

    assert "$dockerStart.CreateNoWindow = $true" in powershell
    assert "$dockerStart.RedirectStandardOutput = $true" in powershell
    assert "$dockerStart.Arguments = '--version'" in powershell
    assert "& docker --version" not in powershell


@pytest.mark.skipif(os.name == "nt", reason="requires native POSIX bash semantics")
def test_posix_bounded_command_survives_errexit() -> None:
    source = (PLUGIN / "scripts" / "init.sh").read_text(encoding="utf-8")
    start = source.index("PACKAGE_STATUS=0\nPACKAGE_TAIL=''")
    end = source.index('\nif [[ "$HAVE_UV" -eq 1 ]]', start)
    helpers = source[start:end]
    script = (
        "set -euo pipefail\n"
        "TMPDIR=.\n"
        "_warn() { printf '%s\\n' \"$*\"; }\n"
        f"{helpers}\n"
        "run_bounded_package_command sh -c 'echo native-build-failed >&2; exit 7'\n"
        'test "$PACKAGE_STATUS" -eq 7\n'
        'case "$PACKAGE_TAIL" in *native-build-failed*) ;; *) exit 9 ;; esac\n'
    )

    completed = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
