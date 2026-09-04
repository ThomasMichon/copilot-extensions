from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]
INSTALLER = PLUGIN / "scripts" / "install.ps1"
PWSH = shutil.which("pwsh")

pytestmark = [
    pytest.mark.guard,
    pytest.mark.skipif(
        os.name != "nt" or PWSH is None,
        reason="Windows PowerShell installer coverage",
    ),
]


def _function_source(source: str, name: str, next_marker: str) -> str:
    start_marker = f"function {name} {{"
    assert source.count(start_marker) == 1, f"missing unique {start_marker!r}"
    assert next_marker in source, f"missing delimiter {next_marker!r}"

    start = source.index(start_marker)
    end = source.index(next_marker, start + len(start_marker))
    assert end > start, f"{next_marker!r} does not follow {start_marker!r}"
    return source[start:end]


def test_package_install_falls_back_when_resolved_uv_cannot_launch(
    tmp_path: Path,
) -> None:
    installer = INSTALLER.read_text(encoding="utf-8")
    install_package = _function_source(
        installer,
        "Install-AgentSshPackage",
        "\n$PluginDir =",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "uv.exe").write_bytes(b"not a Windows executable")
    marker = tmp_path / "pip-fallback-ran"
    fake_python = fake_bin / "python.cmd"
    fake_python.write_text(
        f'@echo fallback>"{marker}"\n@exit /b 0\n',
        encoding="ascii",
    )

    def ps_quote(value: str) -> str:
        return value.replace("'", "''")

    script = tmp_path / "fallback.ps1"
    script.write_text(
        "\n".join(
            [
                "$ErrorActionPreference = 'Stop'",
                "function Write-Step { param([string]$Msg) Write-Host $Msg }",
                install_package,
                (
                    "$ok = Install-AgentSshPackage "
                    f"-Python '{ps_quote(str(fake_python))}' "
                    f"-Source '{ps_quote(str(tmp_path))}'"
                ),
                "if (-not $ok) { throw 'fallback install failed' }",
            ]
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
    }

    proc = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(script)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "falling back to python -m pip" in proc.stdout
    assert marker.is_file()
