"""Regression coverage for the POSIX installer's systemd start fallback."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_SH = _PLUGIN_ROOT / "scripts" / "install.sh"
_BASH = shutil.which("bash")


def _plugin_version() -> str:
    text = (_PLUGIN_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "could not read the plugin version from pyproject.toml"
    return match.group(1)


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
    install_dir = home / ".agent-bridge"
    unit_dir = home / ".config" / "systemd" / "user"
    fake_bin.mkdir(parents=True)
    unit_dir.mkdir(parents=True)
    (unit_dir / "agent-bridge.service").write_text("[Service]\n", encoding="utf-8")

    # Versioned-slot layout, as `activate --no-link` leaves it: a
    # `current-version` marker and a completed slot, and NO `venv` link. The
    # installer must resolve the interpreter through the deployed resolver.
    version = _plugin_version()
    slot_bin = install_dir / "versions" / version / "bin"
    slot_bin.mkdir(parents=True)
    (install_dir / "versions" / version / ".install-complete.json").write_text(
        json.dumps(
            {"version": version, "completed_at": "1970-01-01T00:00:00Z", "pid": 1},
            separators=(", ", ": "),
        )
        + "\n",
        encoding="utf-8",
    )
    (install_dir / "current-version").write_text(version + "\n", encoding="utf-8")
    resolver_dir = install_dir / "bin"
    resolver_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        _PLUGIN_ROOT / "scripts" / "resolve-runtime.sh",
        resolver_dir / "resolve-runtime.sh",
    )

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
    # Stands in for the slot interpreter; the installer invokes it as
    # `python -m agent_bridge start`.
    _executable(
        slot_bin / "python",
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
