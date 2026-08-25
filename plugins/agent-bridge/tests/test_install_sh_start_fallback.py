"""Regression coverage for the POSIX installer's systemd start fallback."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_SH = _PLUGIN_ROOT / "scripts" / "install.sh"
_BASH = shutil.which("bash")


def _executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


@pytest.mark.skipif(
    _BASH is None or os.name == "nt",
    reason="a POSIX bash environment is not available",
)
def test_failed_systemd_start_reaches_direct_fallback(tmp_path: Path) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    runtime_bin = home / ".agent-bridge" / "venv" / "bin"
    unit_dir = home / ".config" / "systemd" / "user"
    fake_bin.mkdir(parents=True)
    runtime_bin.mkdir(parents=True)
    unit_dir.mkdir(parents=True)
    (unit_dir / "agent-bridge.service").write_text("[Service]\n", encoding="utf-8")

    _executable(
        fake_bin / "systemctl",
        "#!/bin/sh\n"
        "case \"$*\" in\n"
        "  *' start '*) exit 1 ;;\n"
        "  *' is-active '*) exit 1 ;;\n"
        "esac\n"
        "exit 0\n",
    )
    _executable(fake_bin / "curl", "#!/bin/sh\nexit 0\n")
    _executable(
        runtime_bin / "agent-bridge",
        "#!/bin/sh\n"
        "trap 'exit 0' TERM INT\n"
        "sleep 30\n",
    )

    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    result = subprocess.run(
        [_BASH, str(_INSTALL_SH), "start"],
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
        check=False,
    )

    pid_file = home / ".agent-bridge" / "agent-bridge.pid"
    try:
        assert result.returncode == 0, result.stderr
        assert "falling back to direct start" in result.stderr
        assert "agent-bridge started" in result.stdout
        assert pid_file.is_file()
    finally:
        if pid_file.is_file():
            os.kill(int(pid_file.read_text().strip()), signal.SIGTERM)
