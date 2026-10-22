"""Native launchers are observers/submitters, not plan-command executors."""

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
@pytest.mark.parametrize("mode", [
    "detach", "wait", "no-update", "no-reconcile", "failed", "old-engine", "no-project",
])
def test_native_launch_submits_fixed_phases_and_preserves_progress(tmp_path, shell, mode):
    if shell == "bash" and os.name != "posix":
        pytest.skip("POSIX native launcher")
    if shell != "bash" and (os.name != "nt" or not shutil.which(shell)):
        pytest.skip("Windows native launcher")
    root = Path(__file__).resolve().parents[1]
    package = tmp_path / "agent_worktrees"
    package.mkdir()
    (package / "__init__.py").write_text("")
    calls = tmp_path / "calls.jsonl"
    (package / "__main__.py").write_text(
        "import json,sys\nfrom pathlib import Path\n"
        f"path=Path({str(calls)!r})\n"
        "with path.open('a') as stream: stream.write(json.dumps(sys.argv[1:])+'\\n')\n"
        "args=sys.argv[1:]\n"
        f"if {mode!r}!='no-project':\n"
        " assert args[:2]==['--project','fixture-project']; args=args[2:]\n"
        "assert args[:2] in [['launch-update','bootstrap'],['launch-update','staged'],['launch-update','reconcile']]\n"
        f"if {mode!r}=='old-engine':\n"
        " print('unrecognized command: launch-update',file=sys.stderr); sys.exit(2)\n"
        "print('native-progress',file=sys.stderr,flush=True)\n"
        f"sys.exit(7 if {mode!r}=='failed' else 0)\n",
    )
    home = tmp_path / "home"
    stage = home / ".agent-worktrees" / "updater-status.json"
    stage.parent.mkdir(parents=True)
    stage.write_text(json.dumps(dict(stage_done=True, plugin_changed=True)))
    log = tmp_path / "setup.log"
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), PYTHONPATH=str(tmp_path))
    env.pop("WORKTREE_NO_RECONCILE", None)
    env.pop("WORKTREE_BLOCKING_INSTALL", None)
    if mode == "wait":
        env["WORKTREE_BLOCKING_INSTALL"] = "1"
    if mode == "no-reconcile":
        env["WORKTREE_NO_RECONCILE"] = "1"
    python = getattr(sys, "_base_executable", sys.executable)
    if shell != "bash":
        source = (root / "bin" / "launch-session.ps1").read_text(encoding="utf-8")

        def function(name):
            start = source.index(f"function {name} {{")
            return source[start:source.index("\n}\n", start) + 3]

        script = "\n".join([
            "$ErrorActionPreference='Stop'; $PSNativeCommandUseErrorActionPreference=$true",
            f"$VenvPython='{python}'",
            "$script:LaunchTimer=[Diagnostics.Stopwatch]::StartNew()",
            f"$script:SetupLog='{log}'; $script:ShowLaunchStatus=$false",
            f"$script:UpdateApplied=$false; $script:LaunchProject='{'' if mode == 'no-project' else 'fixture-project'}'",
            f"$noUpdate=${'true' if mode == 'no-update' else 'false'}",
            *(function(name) for name in (
                "Format-SetupLine", "Write-SetupLog", "Write-SetupStatus",
                "Invoke-SetupCommand", "Invoke-UpdateApply",
            )),
            f"function Resolve-RuntimePython {{ '{python}' }}",
            "Invoke-UpdateApply -WithReconcile",
            "Invoke-UpdateApply -WithReconcile",
            "if($ErrorActionPreference -ne 'Stop' -or -not $PSNativeCommandUseErrorActionPreference){throw 'preference leaked'}",
        ])
        fixture = tmp_path / "launch.ps1"
        fixture.write_text(script, encoding="utf-8-sig")
        command = [shell, "-NoProfile", "-File", str(fixture)]
    else:
        source = (root / "bin" / "launch-session.sh").read_text()

        def function(name):
            start = source.index(f"{name}() {{")
            return source[start:source.index("\n}\n", start) + 3]

        functions = source[source.index("setup_log() {"):source.index("\n# Write header")]
        env["PYTHONPATH"] += os.pathsep + str(root.parent / "libs" / "update-telemetry" / "src")
        script = "\n".join([
            "set -euo pipefail",
            f"PYTHON={shlex.quote(python)}; LAUNCH_PROJECT={shlex.quote('' if mode == 'no-project' else 'fixture-project')}",
            f"_NO_UPDATE={'1' if mode == 'no-update' else '0'}; _UPDATE_APPLIED=''; _STAGE_PID=''",
            f"SETUP_LOG={shlex.quote(str(log))}; _SETUP_LOG_DIR={shlex.quote(str(tmp_path))}",
            functions, function("_run_launch_request"), function("invoke_update_apply"),
            f"resolve_runtime_python() {{ printf '%s\\n' {shlex.quote(python)}; }}",
            "invoke_update_apply 1", "invoke_update_apply 1",
        ])
        fixture = tmp_path / "launch.sh"
        fixture.write_text(script)
        command = ["/bin/bash", str(fixture)]
    result = subprocess.run(
        command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "" and result.stderr == ""
    observed = [
        json.loads(line)[0 if mode == "no-project" else 2:]
        for line in calls.read_text().splitlines()
    ]
    if mode == "no-update":
        assert observed == [["launch-update", "reconcile"]]
    else:
        assert observed[0] == ["launch-update", "bootstrap", "--defer-worktrees"]
        expected_stage = ["launch-update", "staged", "--status", str(stage)]
        if mode != "wait":
            expected_stage += ["--detach"]
        assert observed[-1] == expected_stage
        assert (["launch-update", "reconcile"] in observed) is (mode != "no-reconcile")
        assert len(observed) == (2 if mode == "no-reconcile" else 3)
    report = log.read_text(encoding="utf-8-sig")
    if mode == "old-engine":
        assert "unrecognized command: launch-update" in report
        assert "failed (exit 2)" in report
        assert all(call[0] == "launch-update" for call in observed)
    else:
        assert "native-progress" in report
        assert ("failed (exit 7)" if mode == "failed" else "done (exit 0)") in report
    assert "install.ps1" not in report and "install.sh" not in report
