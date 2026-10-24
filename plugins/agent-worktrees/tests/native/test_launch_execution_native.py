"""Explicit real-process/IPC qualification; no native Copilot acceptance claim."""

from __future__ import annotations

import json
import importlib.util
import os
from pathlib import Path
import subprocess
import shutil
import sys
import textwrap
import time

import pytest
import agent_procutil as procutil

from agent_worktrees import launch_execution, launch_seed_state

_FLAGS = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
_SETUP = """
import json, os, sys
from pathlib import Path
from agent_worktrees import config as cfg, launch_execution, launch_seed_exec, launch_seed_state, tracking, tracking_write
from agent_worktrees.sessions import LiveVerdict
root = Path(sys.argv[1])
project = cfg.project_dir("demo")
project.mkdir(parents=True, exist_ok=True)
(project / "config.yaml").write_text(json.dumps({
    "repo_name": "demo", "machine": "test",
    "repos": {"demo": {"anchor": str(root), "default_branch": "dev"}},
}), encoding="utf-8")
checkout = root / "checkout"
checkout.mkdir()
path = tracking.create_new_record(
    "wt-a", "worktree/wt-a", str(checkout), "demo", "test",
    cfg.detect_platform(), project / "worktrees",
).yaml_path
launch_seed_exec.sessions.verify_worktree_active = lambda *a: LiveVerdict()
"""


@pytest.mark.parametrize("shell", ["powershell", "bash"])
def test_actual_setup_function_forwards_exact_child_correlation(tmp_path, shell):
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    output = tmp_path / "captured.json"
    environment = {
        **os.environ, "FIXTURE_CAPTURE": str(output),
        "LAUNCH_SEED_RECORD": str(tmp_path / "record with spaces.yaml"), "LAUNCH_SEED_ID": "seed-a",
        "LAUNCH_ATTEMPT_ID": "a" * 32, "LAUNCH_RESOLUTION_ID": "b" * 32, "LAUNCH_PARENT_ID": "c" * 32,
    }
    if shell == "powershell":
        executable = shutil.which("pwsh")
        if not executable:
            pytest.skip("PowerShell unavailable")
        runtime = tmp_path / "capture runtime.ps1"
        runtime.write_text(
            "[IO.File]::WriteAllText($env:FIXTURE_CAPTURE, (ConvertTo-Json -InputObject @($args) -Compress))\n",
            encoding="utf-8",
        )
        script = """
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($args[0], [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Setup parse failed' }
$fn = $ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Invoke-CopilotBackend'}, $true)
. ([scriptblock]::Create($fn.Extent.Text))
$LaunchSeedRuntimePython = $args[1]
$LaunchSeedRecord = $env:LAUNCH_SEED_RECORD
$LaunchSeedId = $env:LAUNCH_SEED_ID
$LaunchAttemptId = $env:LAUNCH_ATTEMPT_ID
$LaunchResolutionId = $env:LAUNCH_RESOLUTION_ID
$LaunchParentId = $env:LAUNCH_PARENT_ID
$CopilotArgs = @('--resume=session-fixture')
Invoke-CopilotBackend 'copilot.exe'
"""
        driver = tmp_path / "forward.ps1"
        driver.write_text(script, encoding="utf-8")
        command = [executable, "-NoProfile", "-File", str(driver), str(scripts / "default-setup.ps1"), str(runtime)]
    else:
        if os.name == "nt":
            pytest.skip("native POSIX Bash lane")
        executable = shutil.which("bash")
        if not executable:
            pytest.skip("Bash unavailable")
        runtime = tmp_path / "capture-runtime"
        runtime.write_text(
            "#!" + sys.executable + "\nimport json,os,sys\n"
            "with open(os.environ['FIXTURE_CAPTURE'],'w') as output: json.dump(sys.argv[1:],output)\n",
            encoding="utf-8",
        )
        runtime.chmod(0o700)
        environment["LAUNCH_SEED_RUNTIME_PYTHON"] = str(runtime)
        source = (scripts / "default-setup.sh").read_text(encoding="utf-8")
        function = source.split("_exec_copilot_backend() {", 1)[1].split(
            '\nif [[ -n "$COPILOT_PATH_OVERRIDE" ]]', 1,
        )[0]
        command = [executable, "-c", "_exec_copilot_backend() {" + function + """
COPILOT_ARGS=('--resume=session-fixture')
_exec_copilot_backend copilot.exe
"""]
    result = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=20, **_FLAGS)
    assert result.returncode == 0, result.stdout + result.stderr
    expected = [
        "-I", "-m", "agent_worktrees.launch_seed_exec", "--invoke",
        "--record", environment["LAUNCH_SEED_RECORD"], "--seed-id", "seed-a",
        "--launch-attempt-id", "a" * 32, "--launch-resolution-id", "b" * 32,
        "--launch-parent-id", "c" * 32, "--", "copilot.exe", "--resume=session-fixture",
    ]
    if shell == "powershell":
        # The script probe consumes PowerShell's parameter delimiter; the real
        # native Python runtime receives it. Correlation values are unchanged.
        expected.remove("--")
    assert json.loads(output.read_text(encoding="utf-8-sig")) == expected


@pytest.mark.parametrize("interruption", [None, "seed-claimed", "process-spawned", "finish"])
def test_real_daemon_and_backend_keep_two_exact_handoffs_separate(tmp_path, interruption):
    script = textwrap.dedent(_SETUP + """
interruption = sys.argv[2] or None
server = tracking_write.start_server(tracking_write.compute)
server.start()
endpoint = tracking_write.rendezvous_fields(server)
def dispatch(verb, args, **kwargs):
    return tracking_write.dispatch(
        verb, args, read_lock_data=lambda: endpoint, ensure_monitor=None,
        boot_wait_s=0, request_deadline_s=2, **kwargs,
    )
launch_seed_state._dispatch = dispatch
tracking_write.run_direct = lambda *a, **kw: sys.exit("unexpected direct fallback")
correlation = launch_execution.Correlation("a" * 32, "b" * 32)
append = launch_execution.Execution.append
def interrupted_append(self, phase, **details):
    append(self, phase, **details)
    if phase == interruption:
        raise KeyboardInterrupt()
launch_execution.Execution.append = interrupted_append
finish = launch_seed_state.finish
def interrupted_finish(*args, **kwargs):
    result = finish(*args, **kwargs)
    if interruption == "finish":
        raise KeyboardInterrupt()
    return result
launch_seed_state.finish = interrupted_finish
try:
    for kind in ("new", "resume"):
        seed = launch_seed_state.stage(path, kind=kind, text="synthetic quoted ' turn " + kind)
        child_exit = 0 if interruption else 7
        code = "import sys,time; assert sys.argv[1:]==['--interactive'," + repr(seed.text) + "]; time.sleep(.15); sys.exit(" + str(child_exit) + ")"
        result = launch_seed_exec.launch(
            path, [sys.executable, "-c", code], invoke=True,
            seed_id=seed.seed_id, correlation=correlation,
        )
        assert result == (3 if interruption else 7) and launch_seed_state.peek(path) is None
    files = list((path.parent / "launch-attempts" / correlation.attempt_id).glob("*.execution.json"))
    assert len(files) == 2
    histories = [launch_execution.read(path.parent, correlation.attempt_id, file.name.split(".")[0]) for file in files]
    pids = []
    for history in histories:
        assert [e["phase"] for e in history["events"]] == [
            "execution-requested", "seed-claimed", "process-spawned", "execution-returned",
        ]
        process = history["events"][2]["details"]
        assert process["pid"] != os.getpid()
        assert process["process_start_time"] and process["boundary"] == "backend-command"
        outcome = history["events"][-1]["details"]
        assert outcome["exit_code"] == (3 if interruption == "finish" else child_exit)
        assert outcome["process_exit_code"] == child_exit
        assert outcome["receipt_uncertain"] == (interruption in {"seed-claimed", "process-spawned"})
        assert "synthetic quoted" not in json.dumps(history)
        pids.append(process["pid"])
    assert len(set(pids)) == 2
    print("native-execution-receipts-pass")
finally:
    server.close()
""")
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), interruption or ""],
        capture_output=True, text=True, timeout=30, **_FLAGS,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "native-execution-receipts-pass"


def test_process_loss_after_claim_keeps_seed_fence_and_no_success_receipt(tmp_path):
    script = textwrap.dedent(_SETUP + """
import os, time
dispatch = launch_seed_state._dispatch
def direct(verb, args, **kwargs):
    return launch_execution._apply(args) if verb == "launch_attempt_execution" else dispatch(verb, args, **kwargs)
launch_seed_state._dispatch = direct
seed = launch_seed_state.stage(path, kind="new", text="synthetic retained intent")
(root / "record-path").write_text(str(path), encoding="utf-8")
popen = launch_seed_exec.subprocess.Popen
def before_spawn(argv, **kwargs):
    if argv[0] == sys.executable:
        (root / "claimed").write_text("claimed", encoding="utf-8")
        time.sleep(60)
        raise AssertionError("test parent did not interrupt")
    return popen(argv, **kwargs)
launch_seed_exec.subprocess.Popen = before_spawn
launch_seed_exec.launch(
    path, [sys.executable, "-c", "raise AssertionError('must not run')"], invoke=True,
    seed_id=seed.seed_id, correlation=launch_execution.Correlation("a" * 32, "b" * 32),
)
""")
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_FLAGS,
    )
    try:
        deadline = time.monotonic() + 20
        while not (tmp_path / "claimed").exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail(str(process.communicate()))
            time.sleep(0.02)
        assert (tmp_path / "claimed").exists()
    finally:
        process.kill()
        process.communicate(timeout=5)
    record = Path((tmp_path / "record-path").read_text(encoding="utf-8"))
    file, = (record.parent / "launch-attempts" / ("a" * 32)).glob("*.execution.json")
    saved = launch_execution.read(record.parent, "a" * 32, file.name.split(".")[0])
    assert [event["phase"] for event in saved["events"]] == ["execution-requested", "seed-claimed"]
    seed = launch_seed_state.peek(record, read_only=True)
    assert seed is not None and seed.handoff_id == saved["events"][1]["details"]["handoff_id"]
    assert "synthetic retained" not in json.dumps(saved)


@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("COPILOT_EXTENSIONS_NATIVE_WINDOWS") != "1",
    reason="opt-in native Windows desktop observation",
)
def test_consoleless_parent_records_two_backend_cycles_without_visible_windows(tmp_path):
    root = Path(__file__).resolve().parents[4]
    spec = importlib.util.spec_from_file_location(
        "launch_execution_window_observer",
        root / "libs" / "agent-procutil" / "tests" / "test_spawn_daemon_windows_integration.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    user32, kernel32, callback_type = module._configure_win32()
    if not user32.GetForegroundWindow():
        pytest.skip("no observable interactive desktop")
    pythonw = procutil.windowless_python(sys.executable)
    assert Path(pythonw).is_file() and pythonw.lower().endswith("pythonw.exe")
    driver = tmp_path / "windowless-driver.py"
    driver.write_text(
        "import ctypes,json,os,sys\n"
        "sys.path[:0] = json.loads(sys.argv[2])\n"
        "sys.stdin = open(os.devnull)\n"
        "sys.stdout = sys.stderr = open(os.path.join(sys.argv[1], 'driver.log'), 'w')\n"
        "assert not ctypes.windll.kernel32.GetConsoleWindow()\n"
        + textwrap.dedent(_SETUP)
        + textwrap.dedent("""
import time
for cycle in range(2):
    seed = launch_seed_state.stage(path, kind="new" if cycle == 0 else "resume", text="synthetic windowless turn")
    result = launch_seed_exec.launch(
        path, [sys.argv[3], "-c", "import time; time.sleep(.25)"],
        invoke=True, seed_id=seed.seed_id,
        correlation=launch_execution.Correlation("a" * 32, "b" * 32),
    )
    assert result == 0 and launch_seed_state.peek(path) is None
    time.sleep(.1)
(root / "cycles.json").write_text(json.dumps({"cycles": 2}), encoding="utf-8")
"""),
        encoding="utf-8",
    )
    with module._WindowObserver(user32, kernel32, callback_type) as observer:
        process, job = procutil.spawn_sync_in_kill_on_close_job(
            [pythonw, "-I", str(driver), str(tmp_path), json.dumps(sys.path), sys.executable],
            require_job=True, allow_breakaway=False, cwd=tmp_path,
            env={**os.environ, **procutil.windowless_python_env(sys.executable)},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **procutil.no_window_kwargs(),
        )
        try:
            assert job is not None
            assert process.wait(timeout=30) == 0, (tmp_path / "driver.log").read_text(errors="replace")
            assert json.loads((tmp_path / "cycles.json").read_text()) == {"cycles": 2}
        finally:
            job.terminate_and_wait(10)
            process.wait(timeout=10)
    assert observer.surfaced == {}, observer.surfaced
    assert observer.focus_changes == {}, observer.focus_changes
