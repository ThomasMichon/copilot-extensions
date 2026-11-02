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


@pytest.fixture(autouse=True)
def _admit_fixture_owner(monkeypatch):
    from worktree_manager import hosting_admission

    monkeypatch.setattr(hosting_admission, "require_owner_hosting", lambda *a, **k: None)


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
def test_manager_defers_only_shell_delegated_preparation(tmp_path, monkeypatch, route):
    from worktree_manager import __main__ as entrypoint
    from worktree_manager import ahp_provider, launcher

    events = []
    plan = dict(PLAN, worktree_id="wt-a", work_dir=str(tmp_path))
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
    original_query = ec.run_json
    owner_reads = []

    def query(project, args, **kwargs):
        if args[:2] != ["agent-policy", "check"]:
            return original_query(project, args, **kwargs)
        assert project is None
        assert args == [
            "agent-policy", "check", "--path", str(tmp_path), "--operation", "launch", "--json",
        ]
        owner_reads.append(str(tmp_path))
        return {
            "version": 1, "repository": str(tmp_path), "expose_agent": None,
            "sources": [], "denied_by": [], "allowed": True,
        }

    monkeypatch.setattr(ec, "run_json", query)
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
    assert owner_reads == ([str(tmp_path)] if route in {"ahp", "persisted-ahp"} else [])


@pytest.mark.parametrize("deferred", [False, True])
def test_projection_deferral_reaches_only_the_selected_engine_call(tmp_path, monkeypatch, deferred):
    profile = str(tmp_path / "profile")
    monkeypatch.setenv("HOME", profile)
    monkeypatch.setenv("USERPROFILE", profile)
    monkeypatch.setenv("HOMEDRIVE", "C:")
    monkeypatch.setenv("HOMEPATH", r"\Users\fixture")
    monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", str(tmp_path / "selected-payload"))
    expected_path = os.environ["PATH"]
    script = (
        "import json,ntpath,os;from pathlib import Path;"
        f"plan={PLAN!r};"
        "plan['deferred']=os.environ.get('AGENT_WORKTREES_DEFER_PROJECTIONS');"
        "plan['home']=str(Path.home());"
        "plan['windows_home']=ntpath.expanduser('~');"
        "plan['profile']=os.environ.get('USERPROFILE');"
        "plan['path']=os.environ.get('PATH');"
        "plan['payload']=os.environ.get('AGENT_WORKTREES_PAYLOAD_ROOT');"
        "plan['parent_python']=os.environ.get('PYTHONHOME');"
        "print(json.dumps(plan))"
    )
    monkeypatch.setattr(ec, "engine_base_command", lambda: [sys.executable, "-c", script])
    monkeypatch.setattr(ec, "installed_engine_command", lambda *a:
                        pytest.fail("projection deferral replaced the selected engine provider"))
    monkeypatch.setenv("PYTHONHOME", str(tmp_path / "foreign-python"))
    monkeypatch.delenv("AGENT_WORKTREES_DEFER_PROJECTIONS", raising=False)
    plan = ec.resolve_launch_plan("demo", new=True, defer_projections=deferred)
    assert plan.raw["deferred"] == ("1" if deferred else None)
    assert plan.raw["home"] == plan.raw["windows_home"] == plan.raw["profile"] == profile
    assert plan.raw["path"] == expected_path
    assert plan.raw["payload"] == str(tmp_path / "selected-payload")
    assert plan.raw["parent_python"] is None
    assert "AGENT_WORKTREES_DEFER_PROJECTIONS" not in os.environ


def test_projection_deferral_survives_bare_resume_compatibility_retry(monkeypatch):
    calls = []
    monkeypatch.setattr(ec, "engine_base_command", lambda: ["selected-engine"])
    monkeypatch.setenv("USERPROFILE", "selected-profile")

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
    assert all(call["environment"]["AGENT_WORKTREES_DEFER_PROJECTIONS"] == "1" for call in calls)
    assert all(call["environment"]["USERPROFILE"] == "selected-profile" for call in calls)
    assert all(call["engine_command"] == ["selected-engine"] for call in calls)


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
        "$script:LaunchAdmitted=$true",
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
        "Format-SetupLine \"  [OK] ready`n  [SKIP] unchanged`n  [WARN] notice`n  [FAIL] bad`n  ...    resolving`nplain after failure\" -Timestamp $first -Seconds 2",
        "Format-SetupLine '  [MAYBE] literal' -Timestamp $first -Seconds 2",
        "Format-SetupLine 'embedded [OK] literal' -Timestamp $first -Seconds 2",
        "Format-SetupLine '  [OK] explicit owner' -State warning -Timestamp $first -Seconds 2",
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
        "[12:00:01.234Z] launch done ready [elapsed 2.0s]",
        "[12:00:01.234Z] launch skipped unchanged [elapsed 2.0s]",
        "[12:00:01.234Z] launch warning notice [elapsed 2.0s]",
        "[12:00:01.234Z] launch failed bad [elapsed 2.0s]",
        "[12:00:01.234Z] launch running resolving [elapsed 2.0s]",
        "[12:00:01.234Z] launch running plain after failure [elapsed 2.0s]",
        "[12:00:01.234Z] launch running   [MAYBE] literal [elapsed 2.0s]",
        "[12:00:01.234Z] launch running embedded [OK] literal [elapsed 2.0s]",
        "[12:00:01.234Z] launch warning   [OK] explicit owner [elapsed 2.0s]",
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
        "_LAUNCH_ADMITTED=1",
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
        "_LAUNCH_ADMITTED=1",
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
        "_LAUNCH_ADMITTED=1",
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
        "_LAUNCH_ADMITTED=1",
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
