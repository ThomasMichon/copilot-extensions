"""Opt-in live visibility and cancellation proof for the native hook wrapper."""

from __future__ import annotations

import ctypes
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import agent_procutil as pu
import pytest

from test_cell_installer_slice import SCRIPTS
from test_cell_payload_upgrade import native_profile as native_profile


_PARENT = """
import ctypes, json, os, subprocess, sys
from pathlib import Path
from agent_procutil import no_window_kwargs
spec = json.loads(Path(sys.argv[1]).read_text('utf-8'))
state = Path(spec['state'])
console = ctypes.WinDLL('kernel32').GetConsoleWindow
console.restype = ctypes.c_void_p
(state / 'parent.json').write_text(json.dumps({
    'pid': os.getpid(), 'console': console() or 0,
}), encoding='utf-8')
results = []
for cycle in range(1 if spec['cancel'] else 2):
    environment = dict(os.environ, APR_VISIBILITY_CYCLE=str(cycle),
                       APR_VISIBILITY_CANCEL='1' if spec['cancel'] else '0')
    result = subprocess.run(spec['command'], env=environment, capture_output=True,
                            text=True, timeout=60, **no_window_kwargs())
    results.append({'code': result.returncode, 'out': result.stdout, 'err': result.stderr})
    (state / 'results.json').write_text(json.dumps(results), encoding='utf-8')
    if result.returncode:
        raise RuntimeError('native wrapper failed; inspect the recorded result')
"""

_CONSOLE_CHILD = """
import ctypes, json, os, time
from pathlib import Path
state = Path(os.environ['APR_VISIBILITY_STATE'])
cycle = os.environ['APR_VISIBILITY_CYCLE']
console = ctypes.WinDLL('kernel32').GetConsoleWindow
console.restype = ctypes.c_void_p
(state / ('console-' + cycle + '.json')).write_text(json.dumps({
    'pid': os.getpid(), 'console': console() or 0,
}), encoding='utf-8')
time.sleep(60 if os.environ['APR_VISIBILITY_CANCEL'] == '1' else 1)
"""


@pytest.mark.parametrize("program", [_PARENT, _CONSOLE_CHILD], ids=["parent", "console-child"])
def test_bootstrap_admission_visibility_fixture_programs_compile(program):
    compile(program, "<native-bootstrap-fixture>", "exec")


def _visibility_probe():
    path = SCRIPTS.parents[2] / "libs" / "agent-procutil" / "tests" / "test_spawn_daemon_windows_integration.py"
    spec = importlib.util.spec_from_file_location("apr_native_window_observer", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_owned_probe(root, *, cancel, host, probe, kernel32):
    state = root / "state"
    scripts = root / "payload with spaces" / "scripts"
    scripts.mkdir(parents=True)
    state.mkdir()
    shutil.copyfile(SCRIPTS / "bootstrap-check.admission.ps1", scripts / "bootstrap-check.admission.ps1")
    (scripts / "payload-mode.ps1").write_text(
        "[IO.File]::WriteAllText((Join-Path $env:APR_VISIBILITY_STATE "
        "('wrapper-' + $env:APR_VISIBILITY_CYCLE + '.json')), "
        "('{\"pid\":' + $PID + '}'))\n'legacy'\n",
        encoding="utf-8",
    )
    (scripts / "bootstrap-check.ps1").write_text(
        "[IO.File]::WriteAllText((Join-Path $env:APR_VISIBILITY_STATE "
        "('delegate-' + $env:APR_VISIBILITY_CYCLE + '.json')), "
        "('{\"pid\":' + $PID + '}'))\n"
        "# Exercise real console inheritance, not another descendant-specific flag.\n"
        "& $env:APR_VISIBILITY_PYTHON -I $env:APR_VISIBILITY_CHILD\n"
        "exit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    parent_script = root / "parent.py"
    parent_script.write_text(_PARENT, encoding="utf-8")
    console_script = root / "console_child.py"
    console_script.write_text(_CONSOLE_CHILD, encoding="utf-8")
    powershell = shutil.which(host)
    assert powershell, f"native {host} is required"
    spec_path = root / "spec.json"
    spec_path.write_text(json.dumps({
        "state": str(state), "cancel": cancel,
        "command": [
            powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(scripts / "bootstrap-check.admission.ps1"),
        ],
    }), encoding="utf-8")
    environment = dict(
        os.environ, APR_VISIBILITY_STATE=str(state),
        APR_VISIBILITY_PYTHON=probe._console_python(), APR_VISIBILITY_CHILD=str(console_script),
        PYTHONPATH=str(Path(pu.__file__).resolve().parents[1]),
    )
    pythonw = pu.windowless_python(probe._console_python())
    assert pythonw.lower().endswith("pythonw.exe"), "a real windowless parent is required"
    parent, job = pu.spawn_sync_in_kill_on_close_job(
        [pythonw, str(parent_script), str(spec_path)], env=environment,
        require_job=True, allow_breakaway=False,
    )
    handles = []
    try:
        assert job is not None, "the complete native tree must be caller-contained"
        if cancel:
            def participants():
                documents = [
                    probe._read_json(state / name)
                    for name in ("parent.json", "wrapper-0.json", "delegate-0.json", "console-0.json")
                ]
                return documents if all(documents) else None

            documents = probe._wait_for(
                participants, 15, "all four live native participants", state / "results.json",
            )
            assert documents[0]["console"] == 0
            assert len({document["pid"] for document in documents}) == 4
            for document in documents:
                handle = kernel32.OpenProcess(
                    probe._SYNCHRONIZE | probe._PROCESS_QUERY_LIMITED_INFORMATION,
                    False, document["pid"],
                )
                assert handle, "a recorded native participant exited before cancellation"
                handles.append(handle)
                in_job = probe.wintypes.BOOL()
                assert kernel32.IsProcessInJob(handle, job.handle, ctypes.byref(in_job))
                assert in_job.value, "a native descendant escaped the owning Job"
            with pytest.raises(subprocess.TimeoutExpired):
                parent.wait(timeout=0.01)
            job.terminate_and_wait(5)
            for handle in handles:
                assert kernel32.WaitForSingleObject(handle, 5000) == probe._WAIT_OBJECT_0
            assert parent.wait(timeout=5) != 0
            completed = len(handles)
        else:
            assert parent.wait(timeout=30) == 0, probe._read_json(state / "results.json")
            assert probe._read_json(state / "parent.json")["console"] == 0
            results = probe._read_json(state / "results.json")
            assert len(results) == 2
            assert all(result == {"code": 0, "out": "{}", "err": ""} for result in results)
            for cycle in range(2):
                for role in ("wrapper", "delegate", "console"):
                    assert probe._read_json(state / f"{role}-{cycle}.json")["pid"] > 0
            job.terminate_and_wait(5)
            completed = len(results)
        return completed
    finally:
        try:
            if job is not None and job.handle is not None:
                job.terminate_and_wait(5)
        finally:
            if job is not None:
                job.close()
            for handle in handles:
                kernel32.CloseHandle(handle)
            parent.wait(timeout=5)


@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("process")
@pytest.mark.timeout(120)
@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("APR_NATIVE_OS_ACCOUNT_ACCEPTANCE") != "1",
    reason="live window/focus proof is opt-in on a disposable native Windows account",
)
@pytest.mark.parametrize("host", ["powershell", "pwsh"])
def test_bootstrap_admission_windowless_descendants_and_timeout_cleanup(
    tmp_path, native_profile, monkeypatch, request, host,
):
    assert native_profile is not None
    probe = _visibility_probe()
    probe._SURFACED_PROCESS_NAMES.add("pwsh.exe")
    user32, kernel32, callback_type = probe._configure_win32()
    enumerate_available = callback_type(lambda _hwnd, _data: True)
    assert user32.EnumWindows(enumerate_available, 0), "native window enumeration is unavailable"
    initial_windows = probe._visible_windows(user32, callback_type)
    get_foreground = user32.GetForegroundWindow
    initial_foreground = get_foreground()
    visible_changes = set()
    foreground_changes = set()
    samples = []

    def track_foreground():
        visible_changes.update(probe._visible_windows(user32, callback_type) - initial_windows)
        current = get_foreground()
        samples.append(current)
        if current != initial_foreground:
            foreground_changes.add(current)
        return current

    # Keep the native result unchanged, including transitions to baseline windows.
    monkeypatch.setattr(user32, "GetForegroundWindow", track_foreground)
    with probe._WindowObserver(user32, kernel32, callback_type) as observer:
        normal_invocations = _run_owned_probe(
            tmp_path / "normal", cancel=False, host=host, probe=probe, kernel32=kernel32,
        )
        assert observer._thread.is_alive(), "the native observer stopped during the normal cycles"
        cancelled_participants = _run_owned_probe(
            tmp_path / "cancel", cancel=True, host=host, probe=probe, kernel32=kernel32,
        )
        assert observer._thread.is_alive(), "the native observer stopped during cancellation"
        time.sleep(0.25)
    assert not observer._thread.is_alive(), "the native observer did not stop"
    assert len(samples) >= 20, "the actual foreground observer did not sample both cycles"
    assert visible_changes == set(), f"new visible windows: {visible_changes}"
    assert observer.surfaced == {}, f"visible console/Default Terminal windows: {observer.surfaced}"
    assert observer.focus_changes == {}, f"new foreground windows: {observer.focus_changes}"
    assert foreground_changes == set(), f"foreground transitions: {foreground_changes}"
    assert normal_invocations == 2 and cancelled_participants == 4
    reporter = request.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line("native bootstrap visibility: " + json.dumps({
            "host": host, "normalInvocations": normal_invocations,
            "visibleWindows": len(visible_changes), "foregroundTransitions": len(foreground_changes),
            "observerSamples": len(samples), "timeoutParticipantsExited": cancelled_participants,
        }, sort_keys=True))
