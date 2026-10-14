from __future__ import annotations

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


def test_both_installers_use_the_persistent_profile_coordinator() -> None:
    powershell = (PLUGIN / "scripts" / "init.ps1").read_text(encoding="utf-8")
    posix = (PLUGIN / "scripts" / "init.sh").read_text(encoding="utf-8")

    assert "'runtime_profile.py'" in powershell
    assert "'--profile', $Profile" in powershell
    assert 'runtime_profile.py" --root "$INSTALL_DIR"' in posix
    assert '--profile "$PROFILE"' in posix
    assert "[provider-exec]" not in powershell
    assert "[provider-exec]" not in posix
    assert "falling back to the base package" not in powershell + posix


@pytest.mark.guard
def test_profile_coordinator_declares_every_workspace_dependency() -> None:
    source = (PLUGIN / "scripts" / "runtime_profile.py").read_text(encoding="utf-8")
    project = tomllib.loads((PLUGIN / "pyproject.toml").read_text(encoding="utf-8"))
    for dependency in project["tool"]["uv"]["sources"]:
        path = project["tool"]["uv"]["sources"][dependency]["path"]
        assert f'"{Path(path).name}"' in source


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
