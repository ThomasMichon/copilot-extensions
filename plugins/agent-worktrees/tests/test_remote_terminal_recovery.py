"""The actual legacy preprocessors must not bypass a packaged target launch."""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess

import pytest

from agent_worktrees import remote_terminal_launch

BIN = Path(__file__).resolve().parents[3] / "worktree-manager" / "bin"


def _block(filename: str, start: str, end: str) -> str:
    text = (BIN / filename).read_text(encoding="utf-8-sig")
    return start + text.split(start, 1)[1].split(end, 1)[0]


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell unavailable")
@pytest.mark.parametrize("flag", ["--recovery", "-Recovery", "recovery"])
def test_powershell_preprocessor_fast_path_is_rejected_before_handoff(flag: str) -> None:
    parser = _block(
        "launch-session.ps1", "$FilteredArgs = @()",
        "Write-SetupLog 'launch-session.ps1 starting'",
    )
    script = (
        "function Write-SetupLog { param($Message,$Level) }\n"
        f"$CopilotArgs=@('--new','{flag}','--seed=literal')\n"
        "$script:LaunchProject=$null\n" + parser + "\nWrite-Output ([int]$RecoveryMode)\n"
    )
    executable = shutil.which("pwsh")
    assert executable is not None
    result = subprocess.run(
        [executable, "-NoProfile", "-Command", script],
        capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    assert result.returncode == 0 and result.stdout.strip() == "1", result.stderr
    with pytest.raises(ValueError, match="Recovery is unsupported"):
        remote_terminal_launch.plan("example", "target", "pwsh", ["--new", flag], seed="literal")


@pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None, reason="Native POSIX Bash required")
@pytest.mark.parametrize("flag", ["--recovery", "-Recovery", "recovery"])
def test_posix_preprocessor_fast_path_is_rejected_before_handoff(flag: str) -> None:
    parser = _block(
        "launch-session.sh", 'LAUNCH_PROJECT=""',
        "setup_log INFO 'launch-session.sh starting'",
    )
    executable = shutil.which("bash")
    assert executable is not None
    script = (
        f"set -- --new {shlex.quote(flag)} --seed=literal\nsetup_log() {{ :; }}\n"
        + parser + '\nprintf "%s" "$RECOVERY_MODE"\n'
    )
    result = subprocess.run([executable, "-c", script], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0 and result.stdout.strip() == "1", result.stderr
    with pytest.raises(ValueError, match="Recovery is unsupported"):
        remote_terminal_launch.plan("example", "target", "bash", ["--new", flag], seed="literal")
