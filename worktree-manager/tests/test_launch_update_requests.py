"""Native launchers are observers/submitters, not plan-command executors."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Win32 consoleless-parent control")
def test_policy_reads_do_not_show_windows_from_consoleless_parent(tmp_path):
    import mux_windows_observer as observer

    python = Path(getattr(sys, "_base_executable", sys.executable))
    pythonw = python.with_name("pythonw.exe")
    shell = shutil.which("pwsh")
    if not pythonw.is_file() or not shell:
        pytest.skip("native Python GUI interpreter and PowerShell required")
    root = Path(__file__).resolve().parents[1]
    source = (root / "bin" / "launch-session.ps1").read_text(encoding="utf-8")
    start = source.index("function Test-AutomaticMaintenance {")
    function = source[start:source.index("\n}\n", start) + 3].replace(
        "$PSScriptRoot", f"'{root / 'bin'}'",
    )
    home = tmp_path / "manager"
    home.mkdir()
    script = tmp_path / "policy.ps1"
    script.write_text("\n".join([
        "$ErrorActionPreference='Stop'",
        f"$VenvPython='{python}'",
        "function Write-SetupLog { param($Message, $Level) }",
        function,
        "if (-not (Test-AutomaticMaintenance)) { throw 'default policy refused' }",
        f"[IO.File]::WriteAllText('{home / 'config.toml'}', '[maintenance]' + \"`nautomatic = false\")",
        "Start-Sleep -Milliseconds 200",
        "if (Test-AutomaticMaintenance) { throw 'disabled policy admitted' }",
    ]), encoding="utf-8-sig")
    result = tmp_path / "result"
    parent = tmp_path / "parent.py"
    parent.write_text(
        "import subprocess\nfrom pathlib import Path\n"
        f"code=subprocess.call({[shell, '-NoProfile', '-File', str(script)]!r}, "
        "creationflags=subprocess.CREATE_NO_WINDOW, stdin=subprocess.DEVNULL, "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        f"Path({str(result)!r}).write_text(str(code))\n",
        encoding="utf-8",
    )
    env = dict(os.environ, WORKTREE_MANAGER_ROOT=str(home))
    proc = subprocess.Popen(
        [str(pythonw), str(parent)], env=env,
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    owned, visible, foreground = {proc.pid}, set(), set()
    try:
        deadline = time.monotonic() + 25
        while proc.poll() is None and time.monotonic() < deadline:
            owned.update(observer.descendants(proc.pid, observer.processes()))
            windows, active = observer.windows()
            visible.update(set(windows.values()) & owned)
            if active in owned:
                foreground.add(active)
            time.sleep(.025)
        assert proc.wait(timeout=2) == 0
        assert result.read_text() == "0"
        assert not visible and not foreground, (visible, foreground)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


@pytest.mark.parametrize("shell", ["pwsh", "powershell", "bash"])
@pytest.mark.parametrize("mode", [
    "detach", "wait", "no-update", "no-reconcile", "failed", "worker-refused", "old-engine",
    "no-project", "maintenance-off", "maintenance-invalid", "maintenance-unreadable",
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
    handoff = tmp_path / "handoff"
    (package / "__main__.py").write_text(
        "import json,sys\nfrom pathlib import Path\n"
        f"path=Path({str(calls)!r})\n"
        "with path.open('a') as stream: stream.write(json.dumps(sys.argv[1:])+'\\n')\n"
        "args=sys.argv[1:]\n"
        f"if {mode!r}!='no-project':\n"
        " assert args[:2]==['--project','fixture-project']; args=args[2:]\n"
        "assert args[:2] in [['launch-update','automatic'],['launch-update','bootstrap'],['launch-update','staged'],['launch-update','reconcile']]\n"
        f"if {mode!r}=='old-engine':\n"
        " print('unrecognized command: launch-update',file=sys.stderr); sys.exit(2)\n"
        "print('native-progress',file=sys.stderr,flush=True)\n"
        f"if {mode!r}=='worker-refused':\n"
        " print(json.dumps(dict(ok=False,reason='update-driver',message='worker launch failed: DaemonSpawnError'))); sys.exit(1)\n"
        f"sys.exit(7 if {mode!r}=='failed' else 0)\n",
    )
    home = tmp_path / "home"
    stage = home / ".agent-worktrees" / "updater-status.json"
    stage.parent.mkdir(parents=True)
    stage.write_text(json.dumps(dict(stage_done=True, plugin_changed=True)))
    log = tmp_path / "setup.log"
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), PYTHONPATH=str(tmp_path))
    policy_root = home / ".worktree-manager"
    policy_root.mkdir()
    env["WORKTREE_MANAGER_ROOT"] = str(policy_root)
    blocked = mode.startswith("maintenance-")
    if mode == "maintenance-off":
        (policy_root / "config.toml").write_text("[maintenance]\nautomatic = false")
    elif mode == "maintenance-invalid":
        (policy_root / "config.toml").write_text("[maintenance]\nautomatic = 'false'")
    elif mode == "maintenance-unreadable":
        (policy_root / "config.toml").mkdir()
    env.pop("WORKTREE_NO_RECONCILE", None)
    env.pop("WORKTREE_BLOCKING_INSTALL", None)
    if mode == "wait" or blocked:
        env["WORKTREE_BLOCKING_INSTALL"] = "1"
    if mode == "no-reconcile":
        env["WORKTREE_NO_RECONCILE"] = "1"
    python = getattr(sys, "_base_executable", sys.executable)
    if shell != "bash":
        source = (root / "bin" / "launch-session.ps1").read_text(encoding="utf-8")

        def function(name):
            start = source.index(f"function {name} {{")
            return source[start:source.index("\n}\n", start) + 3].replace(
                "$PSScriptRoot", f"'{root / 'bin'}'"
            )

        script = "\n".join([
            "$ErrorActionPreference='Stop'; $PSNativeCommandUseErrorActionPreference=$true",
            f"$VenvPython='{python}'",
            "$script:LaunchTimer=[Diagnostics.Stopwatch]::StartNew()",
            f"$script:SetupLog='{log}'; $script:ShowLaunchStatus=$false",
            f"$script:LaunchAdmitted=$true; $script:LaunchPolicyPath='{tmp_path}'",
            f"function Assert-LaunchTarget {{ param($Path,$Operation); if($Path -cne '{tmp_path}'){{throw 'wrong admission target'}} }}",
            f"$script:UpdateApplied=$false; $script:LaunchProject='{'' if mode == 'no-project' else 'fixture-project'}'",
            f"$noUpdate=${'true' if mode == 'no-update' else 'false'}",
            *(function(name) for name in (
                "Format-SetupLine", "Write-SetupLog", "Write-SetupStatus",
                "Test-AutomaticMaintenance", "Invoke-SetupCommand", "Invoke-UpdateApply",
                "Start-UpdateStage",
            )),
            "function Start-Job { throw 'policy admitted blocked background stage' }",
            "Start-UpdateStage" if blocked else "",
            f"function Resolve-RuntimePython {{ '{python}' }}",
            "Invoke-UpdateApply -WithReconcile",
            "Invoke-UpdateApply -WithReconcile",
            f"[IO.File]::WriteAllText('{handoff}','handoff')",
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
            f"_MANAGER_POLICY_DIR={shlex.quote(str(root / 'bin'))}",
            f"_NO_UPDATE={'1' if mode == 'no-update' else '0'}; _UPDATE_APPLIED=''; _STAGE_PID=''",
            f"SETUP_LOG={shlex.quote(str(log))}; _SETUP_LOG_DIR={shlex.quote(str(tmp_path))}",
            f"_LAUNCH_ADMITTED=1; _LAUNCH_POLICY_PATH={shlex.quote(str(tmp_path))}",
            f"admit_launch_target() {{ [[ \"$1\" == {shlex.quote(str(tmp_path))} ]]; }}",
            functions, function("automatic_maintenance_enabled"),
            function("_run_launch_request"), function("invoke_update_apply"),
            function("start_update_stage"),
            "start_update_stage" if blocked else "",
            f"resolve_runtime_python() {{ printf '%s\\n' {shlex.quote(python)}; }}",
            "invoke_update_apply 1", "invoke_update_apply 1",
            f"printf 'handoff' > {shlex.quote(str(handoff))}",
        ])
        fixture = tmp_path / "launch.sh"
        fixture.write_text(script)
        command = ["/bin/bash", str(fixture)]
    result = subprocess.run(
        command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert handoff.read_text() == "handoff"
    assert result.stdout == "" and result.stderr == ""
    observed = [
        json.loads(line)[0 if mode == "no-project" else 2:]
        for line in (calls.read_text().splitlines() if calls.exists() else [])
    ]
    if blocked:
        assert observed == []
        report = log.read_text(encoding="utf-8-sig")
        assert "Automatic maintenance not admitted" in report
        assert ("disabled" if mode == "maintenance-off" else "blocked") in report
        return
    if mode == "wait":
        assert observed[0] == ["launch-update", "bootstrap", "--defer-worktrees"]
        expected_stage = ["launch-update", "staged", "--status", str(stage)]
        assert observed[-1] == expected_stage
        assert ["launch-update", "reconcile"] in observed
        assert len(observed) == 3
    else:
        expected = ["launch-update", "automatic", "--detach"]
        if mode == "no-update":
            expected += ["--no-update"]
        if mode != "no-reconcile":
            expected += ["--with-reconcile"]
        assert observed == [expected]
    report = log.read_text(encoding="utf-8-sig")
    if mode == "old-engine":
        assert "unrecognized command: launch-update" in report
        assert "failed (exit 2)" in report
        assert all(call[0] == "launch-update" for call in observed)
    elif mode == "worker-refused":
        assert "worker launch failed: DaemonSpawnError" in report
        assert "failed (exit 1)" in report
        assert "accepted" not in report
    else:
        assert "native-progress" in report
        assert ("failed (exit 7)" if mode == "failed" else "done (exit 0)") in report
    assert "install.ps1" not in report and "install.sh" not in report


@pytest.mark.parametrize("shell", ["pwsh", "bash"])
@pytest.mark.parametrize("followup", ["coalesce", "other-project"])
@pytest.mark.timeout(120)  # Three 20s handoffs, 20s cold-owner handshake, 30s drain.
def test_native_handoffs_precede_real_driver_installer_completion(tmp_path, monkeypatch, shell, followup):
    if shell == "bash" and os.name != "posix":
        pytest.skip("POSIX native launcher")
    if shell == "pwsh" and (os.name != "nt" or not shutil.which(shell)):
        pytest.skip("Windows native launcher")
    root = Path(__file__).resolve().parents[1]
    worktrees_source = root.parent / "plugins/agent-worktrees/src"
    policy_source = root.parent / "libs/repository-policy/src"
    # This integration fixture supplies AW; standalone Manager does not depend on it.
    monkeypatch.syspath_prepend(str(policy_source))
    monkeypatch.syspath_prepend(str(worktrees_source))
    import agent_worktrees
    import update_driver as driver
    from agent_procutil import direct_python
    from agent_procutil.owned_process import run_caller_owned

    home = tmp_path / "home"
    home.mkdir()
    started, gate, finished = (tmp_path / name for name in ("started", "gate", "finished"))
    shell_entered, client_entered = (tmp_path / name for name in ("shell-entered", "client-entered"))
    installer = tmp_path / "installer.py"
    installer.write_text(
        "import time\nfrom pathlib import Path\n"
        f"Path({str(started)!r}).write_text('started')\n"
        "deadline=time.monotonic()+45\n"
        f"while not Path({str(gate)!r}).exists():\n"
        " if time.monotonic()>=deadline: raise TimeoutError('fixture installer gate')\n"
        " time.sleep(.02)\n"
        f"Path({str(finished)!r}).write_text('done')\n",
    )
    package = tmp_path / "agent_worktrees"
    package.mkdir()
    (package / "__init__.py").write_text(
        f"__path__.append({str(Path(agent_worktrees.__file__).parent)!r})\n",
    )
    (package / "__main__.py").write_text(
        "import argparse,sys\nfrom pathlib import Path\n"
        "_INVOCATION_CWD=Path.cwd()\n"
        f"Path({str(client_entered)!r}).write_text('entered')\n"
        "from agent_worktrees import config as cfg,update_cli,update_launch_cli as launch\n"
        "argv=sys.argv[1:]; project=None\n"
        "if argv[:1]==['--project']: project=argv[1]; argv=argv[2:]\n"
        "cfg.active_project=lambda:project\n"
        f"cfg.install_dir=lambda:Path({str(home / '.agent-worktrees')!r})\n"
        "update_cli._invocation_update_context=lambda:None\n"
        "launch.update_stage.stage=lambda:dict(stage_done=True,acquisition_ok=True,plugin_changed=False)\n"
        "def plan():\n"
        f" if Path({str(finished)!r}).exists(): return dict(action='continue')\n"
        f" return dict(action='self-update',updates=[dict(service='fixture-bootstrap',argv=[sys.executable,{str(installer)!r}])])\n"
        "update_cli._core_helper=lambda name,fallback:plan\n"
        "parser=argparse.ArgumentParser(); sub=parser.add_subparsers(dest='verb')\n"
        "launch.add_parsers(sub); args=parser.parse_args(argv)\n"
        "if __name__=='__main__': raise SystemExit(launch.cmd_launch_update(args))\n",
    )
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home))
    env["PYTHONPATH"] = os.pathsep.join([
        str(tmp_path), str(worktrees_source), str(policy_source), os.environ.get("PYTHONPATH", ""),
    ])
    env["WORKTREE_MANAGER_ROOT"] = str(home / ".worktree-manager")
    for name in (
        "WORKTREE_NO_UPDATE", "WORKTREE_NO_RECONCILE", "WORKTREE_BLOCKING_INSTALL",
        "COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT", "AGENT_WORKTREES_PAYLOAD_ROOT",
    ):
        env.pop(name, None)
    log = tmp_path / "setup.log"
    python, interpreter_environment = direct_python(sys.executable)
    env.update(interpreter_environment)
    if shell == "pwsh":
        source = (root / "bin" / "launch-session.ps1").read_text(encoding="utf-8")

        def function(name):
            start = source.index(f"function {name} {{")
            return source[start:source.index("\n}\n", start) + 3].replace(
                "$PSScriptRoot", f"'{root / 'bin'}'"
            )

        script = "\n".join([
            "$ErrorActionPreference='Stop'; $PSNativeCommandUseErrorActionPreference=$true",
            f"$VenvPython='{python}'; $script:SetupLog='{log}'",
            "$script:LaunchTimer=[Diagnostics.Stopwatch]::StartNew()",
            "$script:ShowLaunchStatus=$false; $script:UpdateApplied=$false",
            f"$script:LaunchAdmitted=$true; $script:LaunchPolicyPath='{tmp_path}'",
            f"function Assert-LaunchTarget {{ param($Path,$Operation); if($Path -cne '{tmp_path}'){{throw 'wrong admission target'}} }}",
            "$script:LaunchProject='fixture'; $noUpdate=$false",
            *(function(name) for name in (
                "Format-SetupLine", "Write-SetupLog", "Write-SetupStatus",
                "Test-AutomaticMaintenance", "Invoke-SetupCommand",
                "Invoke-UpdateApply", "Start-UpdateStage",
            )),
            "function Start-Job { throw 'healthy launch started shell-owned staging' }",
            "if($null -ne (Start-UpdateStage)){throw 'unexpected stage handle'}",
            f"[IO.File]::WriteAllText('{shell_entered}','entered')",
            "Invoke-UpdateApply",
        ])
        fixture = tmp_path / "launch.ps1"
        fixture.write_text(script, encoding="utf-8-sig")
        command = [shell, "-NoProfile", "-File", str(fixture)]
    else:
        source = (root / "bin" / "launch-session.sh").read_text()

        def function(name):
            start = source.index(f"{name}() {{")
            return source[start:source.index("\n}\n", start) + 3]

        script = "\n".join([
            "set -euo pipefail",
            f"PYTHON={shlex.quote(python)}; LAUNCH_PROJECT=fixture",
            f"_MANAGER_POLICY_DIR={shlex.quote(str(root / 'bin'))}",
            "_NO_UPDATE=''; _UPDATE_APPLIED=''; _STAGE_PID=''",
            f"SETUP_LOG={shlex.quote(str(log))}; _SETUP_LOG_DIR={shlex.quote(str(tmp_path))}",
            f"_LAUNCH_ADMITTED=1; _LAUNCH_POLICY_PATH={shlex.quote(str(tmp_path))}",
            f"admit_launch_target() {{ [[ \"$1\" == {shlex.quote(str(tmp_path))} ]]; }}",
            source[source.index("setup_log() {"):source.index("\n# Write header")],
            *(function(name) for name in (
                "automatic_maintenance_enabled", "_run_launch_request",
                "invoke_update_apply", "start_update_stage",
            )),
            "start_update_stage", "[[ -z \"$_STAGE_PID\" ]]",
            f"printf 'entered' > {shlex.quote(str(shell_entered))}",
            "invoke_update_apply",
        ])
        fixture = tmp_path / "launch.sh"
        fixture.write_text(script)
        command = ["/bin/bash", str(fixture)]
    queue_root = home / ".copilot-extensions" / "update-driver"
    requests = ()
    try:
        for cycle in range(3 if followup == "coalesce" else 2):
            shell_entered.unlink(missing_ok=True)
            client_entered.unlink(missing_ok=True)
            if cycle == 1 and followup == "other-project":
                source = fixture.read_text(encoding="utf-8-sig")
                source = source.replace(
                    "$script:LaunchProject='fixture'", "$script:LaunchProject='other-project'",
                ).replace("LAUNCH_PROJECT=fixture", "LAUNCH_PROJECT=other-project")
                fixture.write_text(source, encoding="utf-8-sig" if shell == "pwsh" else "utf-8")
            try:
                result = subprocess.run(
                    command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20,
                    **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
                )
            except subprocess.TimeoutExpired as error:
                state = [(row.state, row.error) for row in driver.status(root=queue_root)]
                report = log.read_text(encoding="utf-8-sig") if log.exists() else "no setup log"
                raise AssertionError(
                    f"handoff cycle {cycle + 1} exceeded 20s; shell={shell_entered.exists()}, "
                    f"client={client_entered.exists()}, installer={started.exists()}, "
                    f"finished={finished.exists()}, requests={state}\n{report}"
                ) from error
            assert result.returncode == 0, result.stdout + result.stderr
            report = log.read_text(encoding="utf-8-sig")
            assert "failed (exit" not in report, report
            assert not finished.exists(), "handoff joined the gated installer"
            if cycle == 0:
                deadline = time.monotonic() + 20
                while not started.exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                assert started.exists(), report
            requests = driver.status(root=queue_root)
        assert len(requests) == 2, "equivalent pending work coalesces; another project stays distinct"
        assert [request.state for request in requests] == ["running", "pending"]
        assert [request.spec.project for request in requests] == [
            "fixture", "fixture" if followup == "coalesce" else "other-project",
        ]
        assert all(request.spec.backend == "engine-launch-update" for request in requests)
    finally:
        gate.write_text("continue")
        # Canonical drain joins the same execution owner; no fixture worker is left behind.
        code = "import update_driver; update_driver.drain()"
        previous = os.environ.copy()
        try:
            os.environ.clear()
            os.environ.update(env)
            settled = run_caller_owned(
                [python, "-c", code], timeout=30,
                max_stdout_bytes=1024 * 1024, max_stderr_bytes=1024 * 1024,
            )
        finally:
            os.environ.clear()
            os.environ.update(previous)
        assert settled.returncode == 0, settled.stderr
    assert finished.is_file()
    assert all(row.state == "completed" and row.exit_code == 0 for row in driver.status(root=queue_root))
