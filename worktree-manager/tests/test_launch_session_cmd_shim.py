"""Guards for launch-session.cmd, the thin cmd.exe shim used only for
--stdio/--acp (interactive launches skip straight to launch-session.ps1).

Migrated from plugins/agent-worktrees/tests/test_first_install_bootstrap.py
as part of the Phase 3b Sub-slice 2a Step 2 cutover (efforts/active/worktree-
manager-control-plane/phase-3b-mux-relocation.md): Worktree Manager's bin/ is
now the sole copy of the interactive mux launch scripts, launch-session.cmd
included.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]


def test_launch_session_cmd_preserves_windows_powershell_fallback() -> None:
    cmd = (PLUGIN / "bin" / "launch-session.cmd").read_text(encoding="utf-8")

    assert "%SystemRoot%\\System32\\where.exe" in cmd
    assert "%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" in cmd
    assert '"%_PSHOST%"' in cmd


@pytest.mark.skipif(os.name != "nt", reason="Windows cmd regression")
def test_launch_session_cmd_survives_overlong_path(tmp_path: Path) -> None:
    cmd = tmp_path / "launch-session.cmd"
    shutil.copyfile(PLUGIN / "bin" / "launch-session.cmd", cmd)
    runtime_bin = tmp_path / ".agent-worktrees" / "bin"
    runtime_bin.mkdir(parents=True)
    (runtime_bin / "launch-session.ps1").write_text(
        "Write-Output ($args -join '|')\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["USERPROFILE"] = str(tmp_path)
    env["PATH"] = ";".join([r"C:\missing"] * 1000)

    proc = subprocess.run(
        [os.environ["ComSpec"], "/d", "/c", str(cmd), "path-overflow"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "path-overflow"
