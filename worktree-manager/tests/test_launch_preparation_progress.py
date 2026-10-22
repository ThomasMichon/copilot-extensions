"""Slow foreground preparation remains visible and preserves the JSON seam."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from worktree_manager import engine_client as ec
from worktree_manager import engine_process


PLAN = {"action": "exec", "work_dir": "/checkout", "cmd": ["copilot"], "env": {}}


@pytest.mark.parametrize("selection", [{"new": True}, {"worktree_id": "wt-a"}, {"base": True}])
def test_foreground_plan_waits_without_aggregate_deadline(monkeypatch, selection):
    monkeypatch.setattr(ec, "engine_base_command", lambda: ["engine"])
    observed = []

    def run(argv, **kwargs):
        observed.append(kwargs)
        return subprocess.CompletedProcess(argv, 0, json.dumps(PLAN), "")

    monkeypatch.setattr(engine_process, "run", run)
    assert ec.resolve_launch_plan("demo", **selection).is_exec
    assert observed[0]["timeout"] is None
    assert observed[0]["stdout"] is subprocess.PIPE
    assert isinstance(observed[0]["stderr"], int)


@pytest.mark.parametrize("route", ["shell", "shell-resume", "direct", "ahp", "persisted-ahp", "remote"])
def test_manager_defers_only_shell_delegated_preparation(monkeypatch, route):
    from worktree_manager import __main__ as entrypoint
    from worktree_manager import ahp_provider, launcher

    events = []
    plan = dict(PLAN, worktree_id="wt-a")
    if route == "remote":
        plan["action"] = "remote"
    monkeypatch.setattr(ec, "engine_base_command", lambda: ["engine"])
    monkeypatch.setattr(
        entrypoint, "_relocated_launch_script",
        lambda: None if route == "direct" else Path("launch-session.sh"),
    )

    def execution_leg(project, worktree_id):
        events.append("inspect-leg")
        return {"execution_leg": {"state": "active", "provider": "ahp"} if route == "persisted-ahp" else None}

    monkeypatch.setattr(ec, "execution_leg_get", execution_leg)

    def run(argv, **kwargs):
        deferred = kwargs["env"].get("AGENT_WORKTREES_DEFER_PROJECTIONS") == "1"
        assert "--defer-projections" not in argv
        events.append("resolve-deferred" if deferred else "resolve-prepared")
        return subprocess.CompletedProcess(argv, 0, json.dumps(plan), "")

    monkeypatch.setattr(engine_process, "run", run)
    monkeypatch.setattr(
        entrypoint, "_run_relocated_mux_launch",
        lambda *a: events.extend(["maintenance", "final-publication", "shell-handoff"]) or 0,
    )
    monkeypatch.setattr(launcher, "launch", lambda *a, **kw: events.append("direct-handoff") or 0)
    monkeypatch.setattr(ahp_provider, "ensure_session", lambda *a: events.append("ahp-ensure"))
    monkeypatch.setattr(ahp_provider, "attach_plan", lambda plan, attachment: plan)
    resumed = route in {"shell-resume", "persisted-ahp"}
    request = SimpleNamespace(
        project="demo", worktree_id="wt-a" if resumed else None,
        mode="resume" if resumed else "new", ahp=route == "ahp",
        machine="remote-host" if route == "remote" else None,
    )
    monkeypatch.delenv("AGENT_WORKTREES_DEFER_PROJECTIONS", raising=False)
    assert entrypoint._run_launch(request) == 0
    if route in {"shell", "shell-resume"}:
        assert events == (
            (["inspect-leg"] if resumed else [])
            + ["resolve-deferred", "maintenance", "final-publication", "shell-handoff"]
        )
    else:
        assert events.count("resolve-prepared") == 1
        assert "resolve-deferred" not in events
        assert "final-publication" not in events
        assert events[-1] == "direct-handoff"
        assert events.count("inspect-leg") == (1 if resumed else 0)
    assert "AGENT_WORKTREES_DEFER_PROJECTIONS" not in os.environ


@pytest.mark.parametrize("deferred", [False, True])
def test_projection_deferral_reaches_only_the_selected_engine_call(monkeypatch, deferred):
    script = (
        "import json,os;"
        f"plan={PLAN!r};"
        "plan['deferred']=os.environ.get('AGENT_WORKTREES_DEFER_PROJECTIONS');"
        "print(json.dumps(plan))"
    )
    monkeypatch.setattr(ec, "engine_base_command", lambda: [sys.executable, "-c", script])
    monkeypatch.delenv("AGENT_WORKTREES_DEFER_PROJECTIONS", raising=False)
    plan = ec.resolve_launch_plan("demo", new=True, defer_projections=deferred)
    assert plan.raw["deferred"] == ("1" if deferred else None)
    assert "AGENT_WORKTREES_DEFER_PROJECTIONS" not in os.environ


def test_projection_deferral_survives_bare_resume_compatibility_retry(monkeypatch):
    calls = []

    def old_runtime(project, args, **kwargs):
        calls.append(kwargs)
        if "--bare-resume" in args:
            raise ec.EngineError("unrecognized arguments: --bare-resume")
        return PLAN

    monkeypatch.setattr(ec, "run_json", old_runtime)
    assert ec.resolve_launch_plan(
        "demo", worktree_id="wt-a", bare_resume=True, defer_projections=True,
    ).is_exec
    assert len(calls) == 2
    assert all(call["environment"] == {"AGENT_WORKTREES_DEFER_PROJECTIONS": "1"} for call in calls)


def test_progress_arrives_before_slow_real_engine_finishes(tmp_path, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    result = []
    failures = []

    class Progress:
        def write(self, text):
            if "projection starting" in text:
                started.set()
            return len(text)

        def flush(self):
            pass

    gate = tmp_path / "continue"
    script = (
        "import json,sys,time\nfrom pathlib import Path\n"
        "print('projection starting',file=sys.stderr,flush=True)\n"
        "deadline=time.monotonic()+15\n"
        f"while not Path({str(gate)!r}).exists() and time.monotonic()<deadline:\n"
        "    time.sleep(0.05)\n"
        f"print(json.dumps({PLAN!r}))\n"
    )
    monkeypatch.setattr(ec, "engine_base_command", lambda: [sys.executable, "-c", script])
    monkeypatch.setattr(sys, "stderr", Progress())

    def resolve():
        try:
            result.append(ec.resolve_launch_plan("demo", new=True))
        except BaseException as exc:
            failures.append(exc)
        finally:
            release.set()

    thread = threading.Thread(target=resolve)
    thread.start()
    try:
        assert started.wait(10), "preparation progress was hidden until completion"
        assert not release.is_set()
    finally:
        gate.write_text("continue", encoding="utf-8")
        thread.join(timeout=10)
    assert not thread.is_alive()
    assert failures == []
    assert result[0].is_exec


def test_streamed_error_retains_version_skew_detail(monkeypatch, capsys):
    script = (
        "import sys;"
        "print('unrecognized arguments: --stage-launch-seed',file=sys.stderr,flush=True);"
        "sys.exit(2)"
    )
    monkeypatch.setattr(ec, "engine_base_command", lambda: [sys.executable, "-c", script])
    with pytest.raises(ec.EngineFeatureUnavailable, match="predates staged"):
        ec.resolve_launch_plan("demo", new=True, seed="task")
    assert "unrecognized arguments" in capsys.readouterr().err


@pytest.mark.parametrize("shell", ["pwsh", "powershell", "bash"])
@pytest.mark.parametrize("current", [False, True])
@pytest.mark.parametrize("boundary", ["reconcile", "bootstrap"])
@pytest.mark.parametrize("admission", ["allowed", "managed", "old-engine", "malformed"])
def test_native_maintenance_reports_outcomes_and_replans(tmp_path, shell, current, boundary, admission):
    if shell != "bash" and os.name != "nt":
        pytest.skip("Windows launcher contract")
    if shell != "bash" and not shutil.which(shell):
        pytest.skip("PowerShell unavailable")
    if shell == "bash" and os.name != "posix":
        pytest.skip("POSIX shell contract")
    root = Path(__file__).resolve().parents[1]
    # The fixture planner/actions need only stdlib, not the runner's site packages.
    fixture_python = getattr(sys, "_base_executable", sys.executable)
    canonical = "[12:00:01.120Z] update/nested running preserved detail [elapsed 3.1s]"
    literal = "private-maintenance-fixture space ' ; $ ` &"
    child = tmp_path / "action.py"
    child.write_text(
        "import os,sys,time\nfrom pathlib import Path\n"
        "assert os.environ['MAINTENANCE_FIXTURE'] == 'child'\n"
        "assert 'MAINTENANCE_UNSET' not in os.environ\n"
        f"assert sys.argv[3] == {literal!r}\n"
        "print('progress-before-completion', flush=True)\n"
        "gate=Path(sys.argv[1]); deadline=time.monotonic()+10\n"
        "while not gate.exists() and time.monotonic()<deadline: time.sleep(.02)\n"
        "assert gate.exists(), 'progress was buffered'\n"
        "print('action-diagnostic', file=sys.stderr, flush=True)\n"
        f"print({canonical!r}, flush=True)\n"
        "print('native-last-line', end='', flush=True)\n"
        "sys.exit(int(sys.argv[2]))\n"
    )
    gate = tmp_path / "progress-seen"
    package = tmp_path / "agent_worktrees"
    package.mkdir()
    (package / "__init__.py").write_text("")
    state = tmp_path / "passes"
    policy = tmp_path / "policy-after-planning"
    independent = tmp_path / "independent-action"
    independent_script = tmp_path / "independent.py"
    independent_script.write_text(
        f"from pathlib import Path\nPath({str(independent)!r}).write_text('retained')\n",
    )
    runtime_root = tmp_path / "runtime caf\u00e9"
    (package / "__main__.py").write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        f"state=Path({str(state)!r})\n"
        "if '--check-update' in sys.argv:\n"
        "    update=json.load(sys.stdin)\n"
        "    assert update['phase']=='runtime'\n"
        f"    assert update['runtime_root']=={str(runtime_root)!r}, 'runtime identity was recoded'\n"
        f"    admission=Path({str(policy)!r}).read_text()\n"
        "    if admission=='old-engine':\n"
        "        print('unrecognized arguments: --check-update',file=sys.stderr); sys.exit(2)\n"
        "    if admission=='malformed':\n"
        "        print('{\"allowed\":\"true\",\"diagnostics\":[]}'); sys.exit(0)\n"
        "    denied=admission=='managed'\n"
        "    print(json.dumps(dict(allowed=not denied,diagnostics=[dict("
        "service=update['service'],reason='deployment-managed',message='policy changed after planning')] if denied else [])))\n"
        "    sys.exit(3 if denied else 0)\n"
        "n=int(state.read_text())+1 if state.exists() else 1\n"
        "state.write_text(str(n))\n"
        f"Path({str(policy)!r}).write_text({admission!r})\n"
        "assert os.environ['MAINTENANCE_FIXTURE'] == 'parent'\n"
        "assert os.environ['MAINTENANCE_UNSET'] == 'parent'\n"
        f"argv=[sys.executable,{str(child)!r},{str(gate)!r}]\n"
        "updates=[dict(service=label,phase='runtime',argv=args,environment={'MAINTENANCE_FIXTURE':'child'},"
        "unset_environment=['MAINTENANCE_UNSET']) for label,args in "
        f"[('failed',argv+['7',{literal!r}]),"
        f"('spawn-error',[{str(tmp_path / 'missing-executable')!r}]),('success',argv+['0',{literal!r}])]]\n"
        f"for update in updates: update['runtime_root']={str(runtime_root)!r}\n"
        "updates[2]['service']='agent-worktrees'; updates[2].pop('phase')\n"
        f"updates.append(dict(service='repository-bootstrap',argv=[sys.executable,{str(independent_script)!r}]))\n"
        "for update in updates: update['command']=' '.join(update['argv'])\n"
        f"print(json.dumps(dict(action={('self-update' if boundary == 'bootstrap' else 'reconcile')!r} "
        f"if n==1 and not {current!r} else 'continue',updates=updates)))\n",
        encoding="utf-8",
    )
    env = dict(
        os.environ, PYTHONPATH=str(tmp_path), MAINTENANCE_FIXTURE="parent",
        MAINTENANCE_UNSET="parent",
    )
    env.pop("WORKTREE_NO_RECONCILE", None)
    log = tmp_path / "setup.log"
    stop_watcher = threading.Event()
    watcher = None
    if shell != "bash":
        text = (root / "bin/launch-session.ps1").read_text()
        formatter_start = text.index("function Format-SetupLine {")
        formatter = text[formatter_start:text.index("\n}\n", formatter_start) + 3]
        helpers = text[text.index("function Push-UpdateEnvironment {"):text.index(
            "\nfunction Invoke-UpdateApply {")]
        if boundary == "bootstrap":
            start = text.index("        # (2) Pre-launch self-update")
            block = text[start:text.index(
                "\n    } else {\n        Write-SetupStatus 'Skipping plugin update", start)]
        else:
            start = text.index("    # (3) Plugin reconciliation")
            block = text[start:text.index("\n}\n", start)]
        script = "\n".join([
            "$ErrorActionPreference='Stop'; $PSNativeCommandUseErrorActionPreference=$true; $WithReconcile=$true",
            "$OutputEncoding=[Text.Encoding]::ASCII; $initialConsoleEncoding=[Console]::OutputEncoding",
            "$script:LaunchTimer=[Diagnostics.Stopwatch]::StartNew()",
            formatter,
            helpers,
            f"$VenvPython='{fixture_python}'",
            f"function Resolve-RuntimePython {{ '{fixture_python}' }}",
            "function Write-PlanDiagnostics { param($Plan,$Prefix) }",
            f"function Write-SetupLog {{ param($Message,$Level='INFO',$Seconds=0,$Scope='launch',$State='',[switch]$Completed) if($Message -like '*progress-before-completion*'){{[IO.File]::WriteAllText('{gate}','seen')}}; Format-SetupLine @PSBoundParameters | ForEach-Object {{ [Console]::Error.WriteLine($_) }} }}",
            "function Write-SetupStatus { param($Message,$Level='INFO',$Seconds=0,$Scope='launch',$State='',[switch]$Completed) Write-SetupLog @PSBoundParameters }",
            block,
            "if($env:MAINTENANCE_FIXTURE -ne 'parent' -or $env:MAINTENANCE_UNSET -ne 'parent'){throw 'environment leaked'}",
            "if($ErrorActionPreference -ne 'Stop'){throw 'error preference leaked'}",
            "if($PSNativeCommandUseErrorActionPreference -ne $true){throw 'native error preference leaked'}",
            "if($OutputEncoding.CodePage -ne 20127 -or [Console]::OutputEncoding.CodePage -ne $initialConsoleEncoding.CodePage){throw 'encoding leaked'}",
        ])
        fixture = tmp_path / "maintenance.ps1"
        fixture.write_text(script)
        command = [shell, "-NoProfile", "-File", str(fixture)]
    else:
        text = (root / "bin/launch-session.sh").read_text()
        if boundary == "bootstrap":
            start = text.index("        # (2) Pre-launch self-update")
            block = text[start:text.index(
                '\n    else\n        setup_status INFO \'Skipping plugin update', start)]
            block += "\n_stop_setup_progress"
        else:
            start = text.index('    if [[ "$with_reconcile" == "1"')
            block = text[start:text.index("\n}\n", start)]
        functions = text[text.index("setup_log() {"):text.index("\n# Write header")]
        library = root.parent / "libs/update-telemetry/src"
        env["PYTHONPATH"] = os.pathsep.join([str(tmp_path), str(library)])
        spawns = tmp_path / "python-spawns"
        python_wrapper = tmp_path / "python-fixture"
        python_wrapper.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$*\" >> {shlex.quote(str(spawns))}\n"
            f"exec {shlex.quote(fixture_python)} \"$@\"\n"
        )
        python_wrapper.chmod(0o700)
        script = "\n".join([
            "set -euo pipefail; with_reconcile=1",
            f"PYTHON={shlex.quote(str(python_wrapper))}",
            f"resolve_runtime_python() {{ printf '%s\\n' {shlex.quote(str(python_wrapper))}; }}",
            functions,
            f"_SETUP_LOG_DIR={shlex.quote(str(tmp_path))}; SETUP_LOG={shlex.quote(str(log))}",
            "_SETUP_PROGRESS_DIR=''; _SETUP_PROGRESS_PID=''; _SETUP_PROGRESS_FAILED=0; _SETUP_PROGRESS_SEQUENCE=0",
            "_SHOW_LAUNCH_STATUS=1; _start_setup_progress",
            "date() { printf 'unexpected per-line date spawn\\n' >&2; return 1; }",
            "_progress_directory=$_SETUP_PROGRESS_DIR",
            block,
            '! kill -0 "$_SETUP_PROGRESS_PID" 2>/dev/null',
            '[[ ! -d "$_progress_directory" ]]',
            '[[ "$MAINTENANCE_FIXTURE" == parent && "$MAINTENANCE_UNSET" == parent ]]',
        ])
        fixture = tmp_path / "maintenance.sh"
        fixture.write_text(script)
        command = ["/bin/bash", str(fixture)]

        def observe_progress():
            while not stop_watcher.wait(.01):
                if log.exists() and "progress-before-completion" in log.read_text():
                    gate.write_text("seen")
                    return

        watcher = threading.Thread(target=observe_progress)
        watcher.start()
    try:
        result = subprocess.run(
            command, env=env, cwd=tmp_path, capture_output=True, text=True, timeout=20,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )
    finally:
        stop_watcher.set()
        if watcher is not None:
            watcher.join(timeout=1)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""
    report = result.stderr + (log.read_text() if log.exists() else "")
    assert literal not in report, "launcher persisted raw action arguments"
    if shell == "bash":
        assert sum("-m update_telemetry.shell " in line for line in spawns.read_text().splitlines()) == 1
        assert "unexpected per-line date spawn" not in report
    if current:
        assert state.read_text() == "1"
        assert not gate.exists()
        assert ("Checking bootstrap services" if boundary == "bootstrap" else "no executable actions") in report
        assert ("launch/bootstrap/" if boundary == "bootstrap" else "reconcile/pass-") not in report
        return
    assert state.read_text() == "2", "success/failure must not replace fresh replanning"
    assert independent.exists(), report
    assert independent.read_text() == "retained"
    if admission != "allowed":
        assert not gate.exists(), "refused runtime action was executed"
        assert (
            "policy changed after planning" if admission == "managed"
            else "execution admission unavailable"
        ) in report
        return
    assert gate.exists(), report
    assert "action-diagnostic" in report
    assert report.splitlines().count(canonical) == 2
    assert report.count("native-last-line [elapsed ") == 2
    assert re.search(r"failed.*exit 7.*continuing.*duration [0-9.]+s", report)
    assert re.search(r"agent-worktrees.*exit 0.*duration [0-9.]+s", report)
    assert re.search(r"spawn-error.*(exit 127|spawn failed).*continuing.*duration [0-9.]+s", report)
    assert "starting" in result.stderr


@pytest.mark.parametrize("shell", ["pwsh", "powershell", "bash"])
@pytest.mark.parametrize("exit_code", [0, 7])
@pytest.mark.parametrize("admission", ["allowed", "managed", "old-engine", "malformed"])
@pytest.mark.parametrize("blocking", [True, False])
def test_blocking_marketplace_installer_keeps_native_frames_and_exit(tmp_path, shell, exit_code, admission, blocking):
    _exercise_marketplace_installer(tmp_path, shell, exit_code, admission, blocking)


@pytest.mark.parametrize("exit_code", [0, 7])
def test_detached_installer_recreates_sink_rotated_before_start(tmp_path, exit_code):
    _exercise_marketplace_installer(
        tmp_path, "pwsh", exit_code, "allowed", False, rotate_before_start=True,
    )


@pytest.mark.parametrize("exit_code", [0, 7])
def test_detached_bash_installer_retains_execution_with_legacy_formatter(tmp_path, exit_code):
    _exercise_marketplace_installer(
        tmp_path, "bash", exit_code, "allowed", False, legacy_observer=True,
    )


@pytest.mark.parametrize("exit_code", [0, 7])
def test_detached_bash_bootstrap_keeps_native_exit_without_telemetry(tmp_path, exit_code):
    if os.name != "posix":
        pytest.skip("POSIX detached bootstrap contract")
    root = Path(__file__).resolve().parents[1]
    source = (root / "bin/launch-session.sh").read_text(encoding="utf-8")
    marker = "local _observer_code='\n"
    assert source.count(marker) == 1
    bootstrap = source.split(marker, 1)[1].split("\n'", 1)[0]
    package = tmp_path / "update_telemetry"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    literal = "space ' \" ; $ ` &"
    producer = (
        "import sys; "
        f"assert sys.argv[1] == {literal!r}; print('original native output'); "
        f"sys.exit({exit_code})"
    )
    result = subprocess.run(
        [
            sys.executable, "-c", bootstrap, str(tmp_path / "unused-observer.log"),
            sys.executable, "-I", "-c", producer, literal,
        ],
        env=dict(os.environ, PYTHONPATH=str(tmp_path)),
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == exit_code, result.stderr
    assert result.stdout == "original native output\n"
    assert "detached installer timing unavailable" in result.stderr
    assert "observer-unavailable" in result.stderr
    assert not (tmp_path / "unused-observer.log").exists()


def _exercise_marketplace_installer(
    tmp_path, shell, exit_code, admission, blocking, *,
    rotate_before_start=False, legacy_observer=False,
):
    if shell == "bash" and os.name != "posix":
        pytest.skip("POSIX shell contract")
    if shell != "bash" and (os.name != "nt" or not shutil.which(shell)):
        pytest.skip("Windows launcher contract")
    if shell == "bash" and not blocking and admission == "allowed":
        if not hasattr(os, "pidfd_open") or not shutil.which("setsid"):
            pytest.skip("Linux detached process identity contract")
    root = Path(__file__).resolve().parents[1]
    python = getattr(sys, "_base_executable", sys.executable)
    package = tmp_path / "agent_worktrees"
    package.mkdir()
    (package / "__init__.py").write_text("")
    marker = tmp_path / "executed"
    gate = tmp_path / "installer-continue"
    detached_pid = tmp_path / "detached-pid"
    sink_path = tmp_path / "observer-sink-path"
    rotated = tmp_path / "sink-was-rotated"
    stage = tmp_path / "home" / ".agent-worktrees" / "updater-status.json"
    runtime_root = tmp_path / "runtime root caf\u00e9"
    (package / "__main__.py").write_text(
        "import json,sys\n"
        "if '--check-update' in sys.argv:\n"
        "    update=json.load(sys.stdin)\n"
        "    assert update['phase']=='runtime' and update['service']=='agent-worktrees'\n"
        "    assert update.get('trigger')=='stage-update'\n"
        f"    assert update['runtime_root']=={str(runtime_root)!r}, 'runtime identity was recoded'\n"
        f"    admission={admission!r}\n"
        "    if admission=='old-engine':\n"
        "        print('unrecognized arguments: --check-update',file=sys.stderr); sys.exit(2)\n"
        "    if admission=='malformed':\n"
        "        print('{\"allowed\":\"true\",\"diagnostics\":[]}'); sys.exit(0)\n"
        "    denied=admission=='managed'\n"
        "    print(json.dumps(dict(allowed=not denied,diagnostics=[dict("
        "reason='deployment-managed',message='policy changed after staging')] if denied else [])))\n"
        f"    if not denied: __import__('pathlib').Path({str(stage)!r}).write_text(json.dumps(dict("
        "stage_done=True,plugin_changed=True,plugin_dir='peer-payload',runtime_root='peer-root',"
        "environment={'MAINTENANCE_FIXTURE':'peer'},unset_environment=[])))\n"
        "    sys.exit(3 if denied else 0)\n"
        "print('{\"action\":\"continue\"}')\n",
        encoding="utf-8",
    )
    plugin = tmp_path / "installed plugin"
    scripts = plugin / "scripts"
    scripts.mkdir(parents=True)
    canonical = "[12:00:01.120Z] update/nested running preserved detail [elapsed 3.1s]"
    stage.parent.mkdir(parents=True)
    stage.write_text(json.dumps(dict(
        stage_done=True, plugin_changed=True, plugin_dir=str(plugin),
        runtime_root=str(runtime_root), environment={"MAINTENANCE_FIXTURE": "child"},
        unset_environment=["MAINTENANCE_UNSET"],
    )))
    env = dict(
        os.environ, HOME=str(stage.parent.parent), USERPROFILE=str(stage.parent.parent),
        PYTHONPATH=str(tmp_path),
        MAINTENANCE_FIXTURE="parent", MAINTENANCE_UNSET="parent",
    )
    env.pop("WORKTREE_BLOCKING_INSTALL", None)
    if blocking:
        env["WORKTREE_BLOCKING_INSTALL"] = "1"
    log = tmp_path / "setup.log"
    env["WORKTREE_SETUP_LOG"] = str(log)
    env.pop("APERTURE_SETUP_LOG", None)
    project = "space ' \" ; $ ` &" if not blocking else ""
    project_literal = project.replace("'", "''")
    if shell != "bash":
        source = (root / "bin/launch-session.ps1").read_text(encoding="utf-8")

        def function(name):
            start = source.index(f"function {name} {{")
            return source[start:source.index("\n}\n", start) + 3]

        wait_for_release = (
            f"& '{python}' -I -c 'import ctypes; k=ctypes.windll.kernel32; u=ctypes.windll.user32; "
            "k.GetConsoleWindow.restype=ctypes.c_void_p; u.IsWindowVisible.argtypes=[ctypes.c_void_p]; "
            "assert not u.IsWindowVisible(k.GetConsoleWindow()), \"installer console became visible\"'\n"
            "if($LASTEXITCODE -ne 0){throw 'headless descendant check failed'}\n"
            "$deadline=[DateTime]::UtcNow.AddSeconds(10)\n"
            f"while(-not [IO.File]::Exists('{gate}') -and [DateTime]::UtcNow -lt $deadline){{Start-Sleep -Milliseconds 20}}\n"
            f"if(-not [IO.File]::Exists('{gate}')){{throw 'detached gate was not released'}}\n"
            if not blocking else ""
        )
        (scripts / "install.ps1").write_text(
            "param([string]$Action,[string]$InstallDir,[string]$ProjectName)\n"
            f"[IO.File]::WriteAllText('{marker}','executed')\n"
            f"if($Action -ne 'update' -or $InstallDir -ne '{runtime_root}'){{throw 'arguments changed'}}\n"
            f"if($ProjectName -cne '{project_literal}'){{throw 'project argument changed'}}\n"
            "if($env:MAINTENANCE_FIXTURE -ne 'child' -or $env:MAINTENANCE_UNSET){throw 'environment changed'}\n"
            "Write-Host 'installer-detail'\n"
            "Write-Host 'installer-unicode caf\u00e9 \u03a9'\n"
            "[Console]::Error.WriteLine('installer-diagnostic')\n"
            f"Write-Output '{canonical}'\n"
            f"{wait_for_release}"
            "[Console]::Write('installer-last-line')\n"
            f"exit {exit_code}\n",
            encoding="utf-8-sig",
        )
        if not blocking and admission == "allowed":
            spawn = (
                "function Start-Process { param($FilePath,$ArgumentList,$WindowStyle)\n"
                "if($FilePath -ne 'conhost.exe' -or $ArgumentList[0] -ne '--headless' -or "
                "$ArgumentList[1] -ne 'pwsh.exe' -or $ArgumentList[2] -ne '-NoProfile' -or "
                "$ArgumentList[3] -ne '-EncodedCommand' -or $WindowStyle -ne 'Hidden'){throw 'detached launch changed'}\n"
                "if(($FilePath.Length + ($ArgumentList -join ' ').Length) -ge 32767){throw 'Windows command line limit exceeded'}\n"
                f"[IO.File]::WriteAllText('{sink_path}',$installerLog)\n"
                + (
                    "[IO.File]::Delete($installerLog)\n"
                    f"[IO.File]::WriteAllText('{rotated}','rotated')\n"
                    if rotate_before_start else ""
                ) +
                "$child=Microsoft.PowerShell.Management\\Start-Process -FilePath $FilePath "
                "-ArgumentList $ArgumentList -WindowStyle $WindowStyle -PassThru\n"
                f"[IO.File]::WriteAllText('{detached_pid}',[string]$child.Id)\n"
                "}"
            )
        else:
            spawn = f"function Start-Process {{ [IO.File]::WriteAllText('{marker}','spawned'); throw 'unexpected detached spawn' }}"
        script = "\n".join([
            "$ErrorActionPreference='Stop'; $PSNativeCommandUseErrorActionPreference=$true",
            "$OutputEncoding=[Text.Encoding]::ASCII; $initialConsoleEncoding=[Console]::OutputEncoding",
            "$script:LaunchTimer=[Diagnostics.Stopwatch]::StartNew()",
            f"$script:SetupLog='{log}'; $script:ShowLaunchStatus=$false",
            f"$script:UpdateApplied=$false; $script:LaunchProject='{project_literal}'; $noUpdate=$false",
            f"$VenvPython='{python}'",
            function("Format-SetupLine"), function("Write-SetupLog"),
            function("Write-SetupStatus"), function("Write-PlanDiagnostics"),
            function("Push-UpdateEnvironment"), function("Pop-UpdateEnvironment"),
            function("Test-UpdateAdmission"),
            function("Invoke-SetupCommand"), function("Invoke-SetupAction"),
            function("Start-DetachedSetupInstaller"), function("Invoke-UpdateApply"),
            spawn,
            "Invoke-UpdateApply",
            "if($env:MAINTENANCE_FIXTURE -ne 'parent' -or $env:MAINTENANCE_UNSET -ne 'parent'){throw 'environment leaked'}",
            "if($ErrorActionPreference -ne 'Stop' -or -not $PSNativeCommandUseErrorActionPreference){throw 'preference leaked'}",
            "if($OutputEncoding.CodePage -ne 20127 -or [Console]::OutputEncoding.CodePage -ne $initialConsoleEncoding.CodePage){throw 'encoding leaked'}",
        ])
        fixture = tmp_path / "blocking.ps1"
        fixture.write_text(script, encoding="utf-8-sig")
        command = [shell, "-NoProfile", "-File", str(fixture)]
    else:
        source = (root / "bin/launch-session.sh").read_text()
        start = source.index("invoke_update_apply() {")
        apply = source[start:source.index("\n}\n", start) + 3]
        functions = source[source.index("setup_log() {"):source.index("\n# Write header")]
        (scripts / "install.sh").write_text(
            "#!/bin/bash\nset -euo pipefail\n"
            f"printf executed > {shlex.quote(str(marker))}\n"
            f"[[ \"$1\" == update && \"$2\" == --install-dir && \"$3\" == {shlex.quote(str(runtime_root))} ]]\n"
            + (
                f"[[ \"$4\" == --project-name && \"$5\" == {shlex.quote(project)} ]]\n"
                if not blocking else ""
            ) +
            "[[ \"$MAINTENANCE_FIXTURE\" == child && -z \"${MAINTENANCE_UNSET+x}\" ]]\n"
            "printf '%s\\n' installer-detail\n"
            "printf '%s\\n' 'installer-unicode caf\u00e9 \u03a9'\n"
            "printf '%s\\n' installer-diagnostic >&2\n"
            f"printf '%s\\n' {shlex.quote(canonical)}\n"
            + (
                "deadline=$((SECONDS + 10))\n"
                f"while [[ ! -f {shlex.quote(str(gate))} && $SECONDS -lt $deadline ]]; do sleep .02; done\n"
                f"[[ -f {shlex.quote(str(gate))} ]] || exit 91\n"
                if not blocking else ""
            ) +
            "printf '%s' installer-last-line\n"
            f"exit {exit_code}\n",
            encoding="utf-8",
        )
        env["PYTHONPATH"] = os.pathsep.join([
            str(tmp_path), str(root.parent / "libs/update-telemetry/src"),
        ])
        if legacy_observer:
            library = root.parent / "libs/update-telemetry/src/update_telemetry"
            legacy = tmp_path / "update_telemetry"
            legacy.mkdir()
            (legacy / "__init__.py").write_text(
                f"__path__.append({str(library)!r})\n", encoding="utf-8",
            )
            code = (library / "shell.py").read_text(encoding="utf-8")
            assert code.count("def observe_command(") == 1
            (legacy / "shell.py").write_text(
                code.replace("def observe_command(", "def _future_observe_command("),
                encoding="utf-8",
            )
        script = "\n".join([
            "set -euo pipefail; _NO_UPDATE=0; _UPDATE_APPLIED=''; _STAGE_PID=''",
            f"LAUNCH_PROJECT={shlex.quote(project)}",
            f"PYTHON={shlex.quote(python)}",
            functions, apply,
            (
                f"setsid() {{ printf '%s' \"$BASHPID\" > {shlex.quote(str(detached_pid))}; "
                f"exec {shlex.quote(shutil.which('setsid'))} \"$@\"; }}"
                if not blocking and admission == "allowed"
                else f"setsid() {{ printf spawned > {shlex.quote(str(marker))}; }}"
            ),
            f"_SETUP_LOG_DIR={shlex.quote(str(tmp_path))}; SETUP_LOG={shlex.quote(str(log))}",
            "invoke_update_apply 0",
            '[[ "$MAINTENANCE_FIXTURE" == parent && "$MAINTENANCE_UNSET" == parent ]]',
        ])
        fixture = tmp_path / "blocking.sh"
        fixture.write_text(script)
        command = ["/bin/bash", str(fixture)]
    result = subprocess.run(
        command, env=env, cwd=tmp_path, capture_output=True, text=True, timeout=20,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
    installer_log = None
    if not blocking and admission == "allowed":
        if shell == "bash":
            installer_log = log
        else:
            installer_log = Path(sink_path.read_text(encoding="utf-8"))
            assert installer_log.parent == tmp_path
            assert re.fullmatch(r"setup\.[a-f0-9]{32}\.installer\.log", installer_log.name)
            assert rotated.exists() is rotate_before_start
            assert f"output: {installer_log}" in log.read_text(encoding="utf-8-sig")
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                report = (
                    installer_log.read_text(encoding="utf-8-sig")
                    if installer_log.exists() else ""
                )
                if ("installer-detail" if legacy_observer else "installer-detail [elapsed ") in report:
                    break
                time.sleep(.02)
            expected = "installer-detail" if legacy_observer else "installer-detail [elapsed "
            assert expected in report, "detached progress was not persisted"
            if shell != "bash":
                assert list(tmp_path.glob("setup.*.installer.log")) == [installer_log]
            assert "Installer update done" not in report and "Installer update failed" not in report
            assert not gate.exists(), "launcher waited for installer completion"
            if shell != "bash":
                with pytest.raises(PermissionError) as error:
                    installer_log.unlink()
                assert error.value.winerror in {32, 33}, "active sink was not protected against rotation"
        finally:
            gate.write_text("continue")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            report = installer_log.read_text(encoding="utf-8-sig")
            if ("installer-last-line" if legacy_observer else f"(exit {exit_code})") in report:
                break
            time.sleep(.02)
        # The log observer and its headless console must finish with the child,
        # not remain alive after the native operation has completed.
        if shell == "bash":
            import select

            try:
                handle = os.pidfd_open(int(detached_pid.read_text()))
            except ProcessLookupError:
                pass
            else:
                try:
                    assert select.select([handle], [], [], 5)[0], "detached observer leaked"
                finally:
                    os.close(handle)
        else:
            _wait_for_detached_windows_installer(detached_pid)
    report = log.read_text(encoding="utf-8-sig")
    if installer_log and installer_log != log:
        report += installer_log.read_text(encoding="utf-8-sig")
    if admission != "allowed":
        assert not marker.exists(), report
        assert (
            "policy changed after staging" if admission == "managed"
            else "execution admission unavailable"
        ) in report
        assert "Checking bootstrap services" in report
        return
    if legacy_observer:
        assert marker.read_text() == "executed"
        assert "detached installer timing unavailable" in report
        assert "installer-last-line" in report
        assert "Installer update done" not in report and "Installer update failed" not in report
        assert report.splitlines().count(canonical) == 1
        return
    assert "installer-detail [elapsed " in report
    assert "installer-unicode caf\u00e9 \u03a9 [elapsed " in report
    assert "installer-diagnostic [elapsed " in report
    assert "installer-last-line [elapsed " in report
    assert report.splitlines().count(canonical) == 1
    state = "failed" if exit_code else "done"
    assert re.search(rf"launch/installer {state} Installer update {state} \(exit {exit_code}\).*\[duration [0-9.]+s\]", report)
    assert "installer: [" not in report
    assert "Checking bootstrap services" in report


def _wait_for_detached_windows_installer(detached_pid):
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, int(detached_pid.read_text()))
    if handle:
        try:
            assert kernel.WaitForSingleObject(handle, 5000) == 0, "detached observer leaked"
        finally:
            kernel.CloseHandle(handle)
    else:
        assert ctypes.get_last_error() == 87, "detached liveness could not be verified"


@pytest.mark.parametrize("failure", ["sink", "command-limit", "spawn"])
def test_detached_installer_observer_surfaces_startup_failures(tmp_path, failure):
    if os.name != "nt" or not shutil.which("pwsh"):
        pytest.skip("Windows detached observer contract")
    root = Path(__file__).resolve().parents[1]
    source = (root / "bin/launch-session.ps1").read_text(encoding="utf-8")

    def function(name):
        start = source.index(f"function {name} {{")
        return source[start:source.index("\n}\n", start) + 3]

    marker = tmp_path / "spawned"
    log = (
        tmp_path / "missing-log-directory" / "setup.log"
        if failure == "sink" else tmp_path / "setup.log"
    )
    argument = "x" * 15000 if failure == "command-limit" else "unused-installer.ps1"
    fixture = tmp_path / "observer-failure.ps1"
    fixture.write_text("\n".join([
        "$ErrorActionPreference='Stop'",
        f"$script:SetupLog='{log}'",
        *(function(name) for name in (
            "Format-SetupLine", "Write-SetupLog", "Write-SetupStatus",
            "Invoke-SetupCommand", "Start-DetachedSetupInstaller",
        )),
        f"function Start-Process {{ [IO.File]::WriteAllText('{marker}','spawned'); throw 'synthetic spawn failure' }}",
        f"try {{ Start-DetachedSetupInstaller -CommandArgv @('pwsh.exe','-NoProfile','-File','{argument}'); throw 'unexpected observer success' }}",
        "catch { [Console]::Out.Write($_.Exception.Message) }",
    ]), encoding="utf-8-sig")
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(fixture)],
        cwd=tmp_path, capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    expected = {
        "sink": "missing-log-directory",
        "command-limit": "Windows command-line limit",
        "spawn": "synthetic spawn failure",
    }[failure]
    assert expected in result.stdout
    assert marker.exists() is (failure == "spawn")


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
@pytest.mark.parametrize("mode", ["quiet", "interactive", "stdio"])
def test_native_maintenance_status_uses_production_sinks(tmp_path, shell, mode):
    if os.name != "nt" or not shutil.which(shell):
        pytest.skip("Windows launcher contract")
    root = Path(__file__).resolve().parents[1]
    source = (root / "bin/launch-session.ps1").read_text()

    def function(name):
        start = source.index(f"function {name} {{")
        return source[start:source.index("\n}\n", start) + 3]

    start = source.index("if ($CopilotPassthrough -contains '--stdio') {")
    stdio = source[start:source.index("\n# Recovery fast-path", start)]
    log = tmp_path / "setup.log"
    fixture = tmp_path / "status.ps1"
    fixture.write_text("\n".join([
        "$ErrorActionPreference='Stop'",
        "$script:LaunchTimer=[Diagnostics.Stopwatch]::StartNew()",
        f"$script:SetupLog='{log}'",
        f"$script:ShowLaunchStatus=${str(mode != 'quiet').lower()}",
        "$CopilotPassthrough=@('--stdio')" if mode == "stdio" else "$CopilotPassthrough=@()",
        function("Format-SetupLine"),
        function("Write-SetupLog"),
        function("Write-SetupStatus"),
        stdio,
        "Write-SetupStatus 'Plugin reconcile: fixture starting' -State starting -Seconds 0",
        "Write-SetupStatus 'Plugin reconcile: fixture failed (exit 7); continuing' 'WARN' -State failed -Seconds 1 -Completed",
    ]))
    result = subprocess.run(
        [shell, "-NoProfile", "-File", str(fixture)],
        cwd=tmp_path, capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    persisted = log.read_text(encoding="utf-8-sig")
    assert re.search(r"\[\d\d:\d\d:\d\d\.\d{3}Z\] launch starting Plugin reconcile: fixture starting \[elapsed 0.0s\]", persisted)
    assert "launch failed Plugin reconcile: fixture failed (exit 7); continuing [duration 1.0s]" in persisted
    emitted = result.stdout if mode == "interactive" else result.stderr
    if mode != "quiet":
        for line in emitted.splitlines():
            assert line in persisted.splitlines(), "console and persistent log must share exactly one frame"
    if mode == "interactive":
        assert "fixture starting" in result.stdout
        assert "fixture failed" in result.stdout
        assert not result.stderr
    else:
        assert not result.stdout
        assert ("fixture starting" in result.stderr) is (mode == "stdio")
        assert ("fixture failed" in result.stderr) is (mode == "stdio")


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
def test_native_setup_formatter_has_independent_clocks_and_physical_lines(tmp_path, shell):
    if os.name != "nt" or not shutil.which(shell):
        pytest.skip("Windows launcher contract")
    root = Path(__file__).resolve().parents[1]
    source = (root / "bin/launch-session.ps1").read_text()
    start = source.index("function Format-SetupLine {")
    formatter = source[start:source.index("\n}\n", start) + 3]
    framed = "[01:02:03.004Z] child done ready [duration 2.0s]"
    fixture = tmp_path / "format.ps1"
    fixture.write_text("\n".join([
        "$ErrorActionPreference='Stop'",
        "[Threading.Thread]::CurrentThread.CurrentCulture='de-DE'",
        formatter,
        "$first=[DateTime]::Parse('2026-10-15T12:00:01.234Z').ToUniversalTime()",
        "$backward=$first.AddHours(-1)",
        "Format-SetupLine \"first`n`nlast`rnext\" -Timestamp $first -Seconds 1.26",
        "Format-SetupLine 'finished' -Timestamp $backward -Seconds 3.5 -State done -Completed",
        f"Format-SetupLine '{framed}' -Timestamp $backward -Seconds 99",
        "Format-SetupLine '[12:00:00.000Z] not a canonical envelope' -Timestamp $first -Seconds 4",
        "Format-SetupLine 'problem' 'ERROR' -Timestamp $first -Seconds 5",
        "Format-SetupLine 'notice' 'WARN' -Timestamp $first -Seconds 6",
    ]))
    result = subprocess.run(
        [shell, "-NoProfile", "-File", str(fixture)],
        cwd=tmp_path, capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    assert result.stdout.splitlines() == [
        "[12:00:01.234Z] launch running first [elapsed 1.3s]",
        "[12:00:01.234Z] launch running  [elapsed 1.3s]",
        "[12:00:01.234Z] launch running last [elapsed 1.3s]",
        "[12:00:01.234Z] launch running next [elapsed 1.3s]",
        "[11:00:01.234Z] launch done finished [duration 3.5s]",
        framed,
        "[12:00:01.234Z] launch running [12:00:00.000Z] not a canonical envelope [elapsed 4.0s]",
        "[12:00:01.234Z] launch failed problem [elapsed 5.0s]",
        "[12:00:01.234Z] launch warning notice [elapsed 6.0s]",
    ]


@pytest.mark.parametrize("failure", ["missing", "stopped"])
def test_bash_progress_failure_is_bounded_and_preserves_native_outcome(tmp_path, failure):
    if os.name != "posix":
        pytest.skip("POSIX native shell contract")
    root = Path(__file__).resolve().parents[1]
    source = (root / "bin/launch-session.sh").read_text()
    functions = source[source.index("setup_log() {"):source.index("\n# Write header")]
    library = root.parent / "libs/update-telemetry/src"
    python = getattr(sys, "_base_executable", sys.executable)
    fixture = tmp_path / "failure.sh"
    log = tmp_path / "setup.log"
    if failure == "missing":
        missing = tmp_path / "missing-helper"
        missing.write_text("#!/bin/sh\nexit 3\n")
        missing.chmod(0o700)
        python = str(missing)
    fixture.write_text("\n".join([
        "set -euo pipefail",
        functions,
        f"PYTHON={shlex.quote(python)}; _SETUP_LOG_DIR={shlex.quote(str(tmp_path))}",
        f"SETUP_LOG={shlex.quote(str(log))}; _SHOW_LAUNCH_STATUS=1",
        "_SETUP_PROGRESS_DIR=''; _SETUP_PROGRESS_PID=''; _SETUP_PROGRESS_FAILED=0; _SETUP_PROGRESS_SEQUENCE=0",
        "_start_setup_progress",
        'kill -STOP "$_SETUP_PROGRESS_PID"' if failure == "stopped" else ":",
        "setup_status WARN 'native command failed (exit 7); continuing'",
        "_stop_setup_progress",
        '! kill -0 "$_SETUP_PROGRESS_PID" 2>/dev/null',
        "printf '%s\\n' 'native-exit=7 continuation=preserved'",
    ]))
    result = subprocess.run(
        ["/bin/bash", str(fixture)], cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(library)),
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "native-exit=7 continuation=preserved\n"
    assert "retained private diagnostics" in result.stderr
    if failure == "missing":
        assert "did not become ready" in result.stderr
        assert "elapsed unavailable" in result.stderr
        assert "native command failed (exit 7); continuing" in log.read_text()


@pytest.mark.parametrize("replacement", ["unrelated-process", "different-job"])
def test_bash_progress_shutdown_does_not_signal_a_reused_pid(tmp_path, replacement):
    if os.name != "posix":
        pytest.skip("POSIX native shell contract")
    root = Path(__file__).resolve().parents[1]
    source = (root / "bin/launch-session.sh").read_text()
    functions = source[source.index("setup_log() {"):source.index("\n# Write header")]
    spool = tmp_path / "retained-progress"
    spool.mkdir()
    fixture = tmp_path / "stale-renderer.sh"
    fixture.write_text("\n".join([
        "set -euo pipefail",
        functions,
        f"_SETUP_PROGRESS_DIR={shlex.quote(str(spool))}",
        # A stale renderer PID now names a live non-renderer process. The
        # fixture itself is disposable; shutdown must not signal it.
        (
            "sleep 8 & unrelated=$!; _SETUP_PROGRESS_PID=$unrelated; _SETUP_PROGRESS_JOB=1"
            if replacement == "different-job"
            else "_SETUP_PROGRESS_PID=$$; _SETUP_PROGRESS_JOB=''"
        ),
        "_SETUP_PROGRESS_FAILED=1",
        "_stop_setup_progress",
        (
            'kill -0 "$unrelated"; kill "$unrelated"; wait "$unrelated" || true'
            if replacement == "different-job" else ":"
        ),
        "printf '%s\\n' 'unrelated process preserved'",
    ]))
    result = subprocess.run(
        ["/bin/bash", str(fixture)], cwd=tmp_path,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "unrelated process preserved\n"
    assert "retained private diagnostics" in result.stderr
    assert spool.exists()


def test_bash_spool_initialization_failure_does_not_abort_native_launch(tmp_path):
    if os.name != "posix":
        pytest.skip("POSIX native shell contract")
    root = Path(__file__).resolve().parents[1]
    source = (root / "bin/launch-session.sh").read_text()
    functions = source[source.index("setup_log() {"):source.index("\n# Write header")]
    spool = tmp_path / "unusable-spool"
    (spool / "events").mkdir(parents=True)
    fixture = tmp_path / "spool-failure.sh"
    fixture.write_text("\n".join([
        "set -euo pipefail",
        functions,
        f"mktemp() {{ printf '%s\\n' {shlex.quote(str(spool))}; }}",
        "_SETUP_LOG_DIR='unused'; _SETUP_PROGRESS_DIR=''; _SETUP_PROGRESS_PID=''; _SETUP_PROGRESS_JOB=''",
        "_SETUP_PROGRESS_FAILED=0; _SHOW_LAUNCH_STATUS=0",
        "_start_setup_progress",
        '[[ "$_SETUP_PROGRESS_FAILED" == 1 && -z "$_SETUP_PROGRESS_PID" ]]',
        "printf '%s\\n' 'native continuation preserved'",
    ]))
    result = subprocess.run(
        ["/bin/bash", str(fixture)], cwd=tmp_path,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "native continuation preserved\n"
    assert "could not initialize launch progress spool" in result.stderr


def test_bash_completed_renderer_cleanup_survives_shell_reaping(tmp_path):
    if os.name != "posix":
        pytest.skip("POSIX native shell contract")
    root = Path(__file__).resolve().parents[1]
    source = (root / "bin/launch-session.sh").read_text()
    functions = source[source.index("setup_log() {"):source.index("\n# Write header")]
    finish = '_setup_progress_emit finish "" "" || true'
    assert functions.count(finish) == 1
    # Remove the completed job from Bash's table before cleanup inspects it.
    functions = functions.replace(finish, finish + '\n    wait "$_SETUP_PROGRESS_PID"')
    fixture = tmp_path / "already-reaped.sh"
    fixture.write_text("\n".join([
        "set -euo pipefail",
        functions,
        f"PYTHON={shlex.quote(getattr(sys, '_base_executable', sys.executable))}",
        f"_SETUP_LOG_DIR={shlex.quote(str(tmp_path))}; SETUP_LOG={shlex.quote(str(tmp_path / 'setup.log'))}",
        "_SHOW_LAUNCH_STATUS=0; _SETUP_PROGRESS_DIR=''; _SETUP_PROGRESS_PID=''; _SETUP_PROGRESS_JOB=''",
        "_SETUP_PROGRESS_FAILED=0; _SETUP_PROGRESS_SEQUENCE=0",
        "_start_setup_progress",
        'spool=$_SETUP_PROGRESS_DIR',
        "_stop_setup_progress",
        '[[ ! -d "$spool" ]]',
    ]))
    result = subprocess.run(
        ["/bin/bash", str(fixture)], cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(root.parent / "libs/update-telemetry/src")),
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == result.stderr == ""
