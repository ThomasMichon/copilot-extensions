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
def test_native_maintenance_reports_outcomes_and_replans(tmp_path, shell, current):
    if shell != "bash" and os.name != "nt":
        pytest.skip("Windows launcher contract")
    if shell != "bash" and not shutil.which(shell):
        pytest.skip("PowerShell unavailable")
    if shell == "bash" and os.name != "posix":
        pytest.skip("POSIX shell contract")
    root = Path(__file__).resolve().parents[1]
    # The fixture planner/actions need only stdlib, not the runner's site packages.
    fixture_python = getattr(sys, "_base_executable", sys.executable)
    child = tmp_path / "action.py"
    child.write_text(
        "import os,sys,time\nfrom pathlib import Path\n"
        "assert os.environ['MAINTENANCE_FIXTURE'] == 'child'\n"
        "assert 'MAINTENANCE_UNSET' not in os.environ\n"
        "print('progress-before-completion', flush=True)\n"
        "gate=Path(sys.argv[1]); deadline=time.monotonic()+10\n"
        "while not gate.exists() and time.monotonic()<deadline: time.sleep(.02)\n"
        "assert gate.exists(), 'progress was buffered'\n"
        "print('action-diagnostic', file=sys.stderr, flush=True)\n"
        "sys.exit(int(sys.argv[2]))\n"
    )
    gate = tmp_path / "progress-seen"
    package = tmp_path / "agent_worktrees"
    package.mkdir()
    (package / "__init__.py").write_text("")
    state = tmp_path / "passes"
    (package / "__main__.py").write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        f"state=Path({str(state)!r})\n"
        "n=int(state.read_text())+1 if state.exists() else 1\n"
        "state.write_text(str(n))\n"
        "assert os.environ['MAINTENANCE_FIXTURE'] == 'parent'\n"
        "assert os.environ['MAINTENANCE_UNSET'] == 'parent'\n"
        f"argv=[sys.executable,{str(child)!r},{str(gate)!r}]\n"
        "updates=[dict(service=label,argv=args,environment={'MAINTENANCE_FIXTURE':'child'},"
        "unset_environment=['MAINTENANCE_UNSET']) for label,args in "
        "[('failed',argv+['7']),"
        f"('spawn-error',[{str(tmp_path / 'missing-executable')!r}]),('success',argv+['0'])]]\n"
        f"print(json.dumps(dict(action='reconcile' if n==1 and not {current!r} else 'continue',updates=updates)))\n"
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
        block = text[text.index("    # (3) Plugin reconciliation"):text.index(
            "\n}\n", text.index("    # (3) Plugin reconciliation"))]
        script = "\n".join([
            "$ErrorActionPreference='Stop'; $PSNativeCommandUseErrorActionPreference=$true; $WithReconcile=$true",
            "$script:LaunchTimer=[Diagnostics.Stopwatch]::StartNew()",
            formatter,
            f"$VenvPython='{fixture_python}'",
            f"function Resolve-RuntimePython {{ '{fixture_python}' }}",
            "function Write-PlanDiagnostics { param($Plan,$Prefix) }",
            f"function Write-SetupLog {{ param($Message,$Level='INFO',$Seconds=0,$Scope='launch',$State='',[switch]$Completed) if($Message -like '*progress-before-completion*'){{[IO.File]::WriteAllText('{gate}','seen')}}; Format-SetupLine @PSBoundParameters | ForEach-Object {{ [Console]::Error.WriteLine($_) }} }}",
            "function Write-SetupStatus { param($Message,$Level='INFO',$Seconds=0,$Scope='launch',$State='',[switch]$Completed) Write-SetupLog @PSBoundParameters }",
            block,
            "if($env:MAINTENANCE_FIXTURE -ne 'parent' -or $env:MAINTENANCE_UNSET -ne 'parent'){throw 'environment leaked'}",
            "if($ErrorActionPreference -ne 'Stop'){throw 'error preference leaked'}",
            "if($PSNativeCommandUseErrorActionPreference -ne $true){throw 'native error preference leaked'}",
        ])
        fixture = tmp_path / "maintenance.ps1"
        fixture.write_text(script)
        command = [shell, "-NoProfile", "-File", str(fixture)]
    else:
        text = (root / "bin/launch-session.sh").read_text()
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
    if shell == "bash":
        assert sum("-m update_telemetry.shell " in line for line in spawns.read_text().splitlines()) == 1
        assert "unexpected per-line date spawn" not in report
    if current:
        assert state.read_text() == "1"
        assert not gate.exists()
        assert "no executable actions" in report
        assert "reconcile/pass-" not in report
        return
    assert state.read_text() == "2", "success/failure must not replace fresh replanning"
    assert gate.exists(), "child progress was not streamed"
    assert "action-diagnostic" in report
    assert re.search(r"failed.*exit 7.*continuing.*duration [0-9.]+s", report)
    assert re.search(r"success.*exit 0.*duration [0-9.]+s", report)
    assert re.search(r"spawn-error.*(exit 127|spawn failed).*continuing.*duration [0-9.]+s", report)
    assert "starting" in result.stderr


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
    persisted = log.read_text()
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
