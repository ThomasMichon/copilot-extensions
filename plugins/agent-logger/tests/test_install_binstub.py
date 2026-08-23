"""POSIX installer regressions for the agent-logger binstub."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_SH = _PLUGIN_ROOT / "scripts" / "install.sh"


@pytest.mark.skipif(os.name == "nt", reason="POSIX installer behavior")
def test_stamp_replaces_dangling_legacy_binstub(tmp_path: Path) -> None:
    home = tmp_path / "home"
    local_bin = home / ".local" / "bin"
    local_bin.mkdir(parents=True)
    binstub = local_bin / "agent-logger"
    binstub.symlink_to(home / ".agent-logger" / ".venv" / "bin" / "agent-logger")

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "COPILOT_PLUGIN_INSTALL_STAGED": "1",
        }
    )
    result = subprocess.run(
        ["bash", str(_INSTALL_SH), "stamp"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert binstub.is_file()
    assert not binstub.is_symlink()
    assert "agent-logger binstub -- self-provisioning" in binstub.read_text(
        encoding="utf-8"
    )
    assert "stamped-version: No such file or directory" not in result.stderr
