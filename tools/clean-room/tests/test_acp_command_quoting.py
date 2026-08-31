from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest


_LIB = Path(__file__).resolve().parents[1] / "lib"


def _bash_path(path: Path) -> str:
    resolved = path.resolve()
    if os.name != "nt":
        return resolved.as_posix()
    relative = resolved.relative_to(resolved.anchor).as_posix()
    return f"/mnt/{resolved.drive[0].lower()}/{relative}"


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
        newline="\n",
    )
    fake_copilot.chmod(0o755)
    marker = tmp_path / "injected"
    result = subprocess.run(
        ["bash"],
        input=(
            f"export PATH={shlex.quote(_bash_path(bin_dir))}:$PATH\n"
            + command
            + "\n"
        ).encode("utf-8"),
        env=os.environ.copy(),
        capture_output=True,
        check=False,
    )

    stderr = result.stderr.decode("utf-8", errors="replace")
    stdout = result.stdout.decode("utf-8", errors="replace")
    assert result.returncode == 0, stderr
    assert stdout.splitlines() == [
        "--acp",
        "--stdio",
        "--allow-all-tools",
        "--plugin-dir",
        plugin_dir,
    ]
    assert not marker.exists()


def test_bash_acp_command_quotes_plugin_dirs(tmp_path: Path):
    marker = _bash_path(tmp_path / "injected")
    plugin_dir = f"/plugins/with spaces; touch {marker}; $(touch {marker})"
    helper = _LIB / "acp-command.sh"
    result = subprocess.run(
        ["bash"],
        input=(
            f"export TEST_PLUGIN_DIR={shlex.quote(plugin_dir)}\n"
            f"source {shlex.quote(_bash_path(helper))}\n"
            'printf "%s\\n" "$TEST_PLUGIN_DIR" | clean_room_build_acp_command\n'
        ).encode("utf-8"),
        env=os.environ.copy(),
        capture_output=True,
        check=False,
    )

    stderr = result.stderr.decode("utf-8", errors="replace")
    stdout = result.stdout.decode("utf-8", errors="replace")
    assert result.returncode == 0, stderr
    _assert_command_preserves_plugin_dir(tmp_path, stdout, plugin_dir)


def test_powershell_acp_command_quotes_plugin_dirs(tmp_path: Path):
    marker = _bash_path(tmp_path / "injected")
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
