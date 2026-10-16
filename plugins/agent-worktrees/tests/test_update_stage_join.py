"""The existing native launchers consume staging refusal/contender results."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("shell", ["pwsh", "powershell", "bash"])
@pytest.mark.parametrize("state", ["missing", "error", "legacy-locked", "completed"])
def test_launcher_join_retries_unusable_stage_once(tmp_path, shell, state):
    if shell == "bash" and os.name != "posix":
        pytest.skip("Native POSIX launcher contract")
    if shell != "bash" and (os.name != "nt" or not shutil.which(shell)):
        pytest.skip("Native Windows launcher contract")
    root = Path(__file__).resolve().parents[3] / "worktree-manager" / "bin"
    home = tmp_path / "home"
    runtime = home / ".agent-worktrees"
    runtime.mkdir(parents=True)
    status = runtime / "updater-status.json"
    initial = {
        "error": {"stage_done": False, "stage_error": "Update-stage lock unavailable"},
        "legacy-locked": {"stage_done": True, "skipped": "locked"},
        "completed": {"stage_done": True, "plugin_changed": True},
    }.get(state)
    if initial is not None:
        status.write_text(json.dumps(initial), encoding="utf-8")
    counter = tmp_path / "attempts"
    package = tmp_path / "agent_worktrees"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    # A contender leaves the owner's shared status untouched, even on retry.
    (package / "__main__.py").write_text(
        "import pathlib,sys\n"
        f"counter=pathlib.Path({str(counter)!r})\n"
        "assert sys.argv[1:] == ['stage-update']\n"
        "counter.write_text(counter.read_text() + '.' if counter.exists() else '.')\n"
        f"raise SystemExit({1 if state == 'error' else 0})\n",
        encoding="utf-8",
    )
    python = getattr(sys, "_base_executable", sys.executable)
    env = {
        **os.environ, "HOME": str(home), "USERPROFILE": str(home),
        "PYTHONPATH": str(tmp_path),
    }
    if shell == "bash":
        source = (root / "launch-session.sh").read_text(encoding="utf-8")
        start = source.index("invoke_update_apply() {")
        end = source.index('        if [[ -n "$runtime_apply_blocked" ]]', start)
        fixture = tmp_path / "join.sh"
        fixture.write_text(
            "set -euo pipefail\n"
            "setup_status() { :; }; setup_log() { :; }; _start_setup_progress() { :; }\n"
            f"PYTHON={shlex.quote(python)}\n"
            "_NO_UPDATE=0; _UPDATE_APPLIED=''; _STAGE_PID=''\n"
            + source[start:end] + "    fi\n}\n"
            "invoke_update_apply 0\ninvoke_update_apply 0\n",
            encoding="utf-8",
        )
        command = ["/bin/bash", str(fixture)]
    else:
        source = (root / "launch-session.ps1").read_text(encoding="utf-8")
        start = source.index("function Invoke-UpdateApply {")
        end = source.index("        if ($status -and $status.runtime_apply_blocked)", start)
        fixture = tmp_path / "join.ps1"
        fixture.write_text(
            "$ErrorActionPreference='Stop'\n"
            "function Write-SetupStatus {}\n"
            f"$VenvPython='{python}'; $noUpdate=$false; $script:UpdateApplied=$false\n"
            + source[start:end] + "    }\n}\n"
            "Invoke-UpdateApply\nInvoke-UpdateApply\n",
            encoding="utf-8-sig",
        )
        command = [shell, "-NoProfile", "-File", str(fixture)]
    result = subprocess.run(
        command, env=env, cwd=tmp_path, capture_output=True, text=True, timeout=15,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (counter.read_text() if counter.exists() else "") == (
        "" if state == "completed" else "."
    )
    assert (json.loads(status.read_text()) if status.exists() else None) == initial
