"""Native launchers submit once without adopting legacy shared staging status."""

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
@pytest.mark.parametrize("state", ["missing", "error", "legacy-locked", "in-progress", "completed"])
def test_launcher_submits_once_without_adopting_legacy_stage_status(tmp_path, shell, state):
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
        "in-progress": {"stage_done": False, "ts": 1.0},
        "completed": {"stage_done": True, "plugin_changed": True},
    }.get(state)
    if initial is not None:
        status.write_text(json.dumps(initial), encoding="utf-8")
    original_status = status.read_bytes() if status.exists() else None
    counter = tmp_path / "attempts"
    package = tmp_path / "agent_worktrees"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "import pathlib,sys\n"
        f"counter=pathlib.Path({str(counter)!r})\n"
        "assert sys.argv[1:] == ['update', '--auto', '--wait', '--skip-modules']\n"
        "counter.write_text(counter.read_text() + '.' if counter.exists() else '.')\n"
        f"raise SystemExit({1 if state == 'error' else 0})\n",
        encoding="utf-8",
    )
    python = getattr(sys, "_base_executable", sys.executable)
    env = {
        **os.environ, "HOME": str(home), "USERPROFILE": str(home),
        "PYTHONPATH": str(tmp_path),
        "WORKTREE_BLOCKING_INSTALL": "1",
    }
    # Target admission and opt-in have separate policy controls.
    if shell == "bash":
        source = (root / "launch-session.sh").read_text(encoding="utf-8")
        start = source.index("invoke_update_apply() {")
        end = source.index("\n# ", start)
        fixture = tmp_path / "join.sh"
        fixture.write_text(
            "set -euo pipefail\n"
            "setup_log() { :; }; _start_setup_progress() { :; }; _stop_setup_progress() { :; }\n"
            "_setup_progress_begin() { :; }; _setup_progress_end() { :; }\n"
            'resolve_runtime_python() { printf "%s\\n" "$PYTHON"; }\n'
            'admit_launch_target() { [[ "$1" == "$_LAUNCH_POLICY_PATH" && "$2" == maintenance ]]; }\n'
            "automatic_maintenance_enabled() { return 0; }\n"
            "_LAUNCH_ADMITTED=1; _LAUNCH_POLICY_PATH=synthetic-target\n"
            f"PYTHON={shlex.quote(python)}\n"
            "_NO_UPDATE=0; _UPDATE_APPLIED=''; LAUNCH_PROJECT=''\n"
            + source[start:end]
            + "\ninvoke_update_apply 0\ninvoke_update_apply 0\n",
            encoding="utf-8",
        )
        command = ["/bin/bash", str(fixture)]
    else:
        source = (root / "launch-session.ps1").read_text(encoding="utf-8")
        start = source.index("function Invoke-UpdateApply {")
        end = source.index("\n# ", start)
        fixture = tmp_path / "join.ps1"
        fixture.write_text(
            "$ErrorActionPreference='Stop'\n"
            "function Assert-LaunchTarget { param($Path, $Operation)\n"
            "  if ($Path -ne $script:LaunchPolicyPath -or $Operation -ne 'maintenance') "
            "{ throw 'Wrong target admission' }\n}\n"
            "function Test-AutomaticMaintenance { return $true }\n"
            "function Resolve-RuntimePython { return $script:VenvPython }\n"
            "function Invoke-SetupCommand { param($CommandArgv, $Scope, $Label)\n"
            "  if ($Scope -ne 'launch/maintenance') { throw 'Wrong request scope' }\n"
            "  $program=$CommandArgv[0]; $arguments=@($CommandArgv | Select-Object -Skip 1)\n"
            "  & $program @arguments\n"
            "  return $LASTEXITCODE\n}\n"
            "$script:LaunchAdmitted=$true; $script:LaunchPolicyPath='synthetic-target'\n"
            f"$VenvPython='{python}'; $noUpdate=$false; $script:UpdateApplied=$false\n"
            "$script:LaunchProject=''\n"
            + source[start:end]
            + "\nInvoke-UpdateApply\nInvoke-UpdateApply\n",
            encoding="utf-8-sig",
        )
        command = [shell, "-NoProfile", "-File", str(fixture)]
    result = subprocess.run(
        command, env=env, cwd=tmp_path, capture_output=True, text=True, timeout=15,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert counter.read_text() == "."
    assert (status.read_bytes() if status.exists() else None) == original_status
