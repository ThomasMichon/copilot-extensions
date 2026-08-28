from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest


_LIB = Path(__file__).resolve().parents[1] / "lib"


def _powershell() -> str:
    for name in ("pwsh", "powershell.exe", "powershell"):
        executable = shutil.which(name)
        if executable is not None:
            return executable
    pytest.skip("PowerShell is not available")


def _assert_command_preserves_plugin_dir(
    tmp_path: Path,
    command: str,
    plugin_dir: str,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake_copilot = bin_dir / "copilot"
    fake_copilot.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$@\"\n",
        encoding="utf-8",
    )
    fake_copilot.chmod(0o755)
    marker = tmp_path / "injected"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
    }

    result = subprocess.run(
        ["bash", "-c", command],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "--acp",
        "--stdio",
        "--allow-all-tools",
        "--plugin-dir",
        plugin_dir,
    ]
    assert not marker.exists()


def test_bash_acp_command_quotes_plugin_dirs(tmp_path: Path):
    marker = tmp_path / "injected"
    plugin_dir = f"/plugins/with spaces; touch {marker}; $(touch {marker})"
    helper = _LIB / "acp-command.sh"
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"source {shlex.quote(str(helper))}; "
            'printf "%s\\n" "$TEST_PLUGIN_DIR" | clean_room_build_acp_command',
        ],
        env={**os.environ, "TEST_PLUGIN_DIR": plugin_dir},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    _assert_command_preserves_plugin_dir(tmp_path, result.stdout, plugin_dir)


def test_powershell_acp_command_quotes_plugin_dirs(tmp_path: Path):
    marker = tmp_path / "injected"
    plugin_dir = f"/plugins/with spaces; touch {marker}; $(touch {marker})"
    helper = str(_LIB / "acp-command.ps1").replace("'", "''")
    result = subprocess.run(
        [
            _powershell(),
            "-NoLogo",
            "-NoProfile",
            "-Command",
            f". '{helper}'; "
            "New-CleanRoomAcpCommand -PluginDirs @($env:TEST_PLUGIN_DIR)",
        ],
        env={**os.environ, "TEST_PLUGIN_DIR": plugin_dir},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    _assert_command_preserves_plugin_dir(
        tmp_path,
        result.stdout.strip(),
        plugin_dir,
    )
