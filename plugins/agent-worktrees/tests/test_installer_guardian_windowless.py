"""Opt-in native retained-installer observation; never part of the fast lane.

Set COPILOT_EXTENSIONS_NATIVE_WINDOWS=1 and select this file explicitly.
Requires an interactive Windows desktop and a real base pythonw.exe. Reuses
the procutil desktop observer; no daemon escape/survival contract is involved.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import agent_procutil as pu
import pytest

pytestmark = [
    pytest.mark.real_installer_processes,
    pytest.mark.skipif(sys.platform != "win32", reason="native Windows desktop observation"),
    pytest.mark.skipif(
        os.environ.get("COPILOT_EXTENSIONS_NATIVE_WINDOWS") != "1",
        reason="opt in with COPILOT_EXTENSIONS_NATIVE_WINDOWS=1",
    ),
]

_CHILD = """
import importlib.util, json, os, subprocess, sys, time
from pathlib import Path
spec = json.loads(sys.argv[1])
root = Path(spec["cycle"])
s = importlib.util.spec_from_file_location("child_procutil", spec["procutil"])
pu = importlib.util.module_from_spec(s)
sys.modules[s.name] = pu
s.loader.exec_module(pu)
options = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
               stderr=subprocess.DEVNULL, **pu.no_window_kwargs())
try:
    escaped = subprocess.Popen(
        [spec["console"], "-I", "-c", "raise SystemExit(91)"],
        **{**options, "creationflags": options["creationflags"] | 0x01000000},
    )
except OSError as exc:
    assert exc.winerror == 5, exc
else:
    escaped.wait(timeout=5)
    raise AssertionError("bounded installer Job permitted breakaway")
descendant = subprocess.Popen(
    [spec["console"], "-I", "-c",
     "import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); "
     "exec('while not p.exists(): time.sleep(0.02)')",
     str(root / "release")],
    **options,
)
(root / "pids.json").write_text(
    json.dumps([os.getppid(), os.getpid(), descendant.pid]), encoding="utf-8",
)
print("owned stdout", flush=True)
print("owned stderr", file=sys.stderr, flush=True)
descendant.wait(timeout=30)
"""

_PARENT = """
import ctypes, importlib.util, json, os, sys, time, traceback
from ctypes import wintypes
from pathlib import Path
spec = json.loads(sys.argv[1])
root = Path(spec["root"])
report = {"cycles": 0}
try:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetConsoleWindow.restype = wintypes.HWND
    assert not kernel.GetConsoleWindow(), "parent unexpectedly has a console"
    kernel.GetModuleFileNameW.argtypes = (
        wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD,
    )
    kernel.GetModuleFileNameW.restype = wintypes.DWORD
    image = ctypes.create_unicode_buffer(32768)
    size = kernel.GetModuleFileNameW(None, image, len(image))
    assert 0 < size < len(image) and Path(image.value).name.lower() == "pythonw.exe"
    s = importlib.util.spec_from_file_location("guardian", spec["guardian"])
    guardian = importlib.util.module_from_spec(s)
    s.loader.exec_module(guardian)
    pu = guardian._load_resource("parent_procutil", spec["procutil"])
    runtime = guardian._load_resource("parent_runtime", spec["runtime"])
    runtime_root = root / "runtime"
    runtime_root.mkdir()
    slot = runtime.slot(runtime_root, "probe")
    with runtime.construction_lease(runtime_root, "probe") as descriptor:
        for cycle in range(3):
            state = root / str(cycle)
            state.mkdir()
            child_spec = {**spec, "cycle": str(state)}
            try:
                result = guardian.run_owned(
                    [spec["console"], "-I", spec["child"], json.dumps(child_spec)],
                    cwd=slot, env=dict(os.environ),
                    timeout=3 if cycle == 2 and spec["failure"] == "timeout" else 10,
                    procutil=pu, construction_fd=descriptor,
                    runtime_root=runtime_root, runtime_version="probe",
                    runtime_path=Path(spec["runtime"]),
                )
            except guardian.ChildTimeoutExpired as exc:
                assert cycle == 2 and spec["failure"] == "timeout"
                assert exc.stdout == "owned stdout\\n"
                assert exc.stderr == "owned stderr\\n"
                report["timed_out"] = True
            else:
                assert cycle < 2, "failure cycle unexpectedly completed"
                assert (result.returncode, result.stdout, result.stderr) == (
                    0, "owned stdout\\n", "owned stderr\\n",
                )
                report["cycles"] += 1
            with runtime._cleanup_lease(
                runtime_root, "probe", allow_construction_owner=True,
            ) as admitted:
                assert admitted, "guardian retained settled candidate custody"
            (root / "progress.json").write_text(json.dumps(report), encoding="utf-8")
            # Periodic cadence only; process settlement above is a lease witness.
            if cycle < 2:
                time.sleep(0.1)
except BaseException:
    report["error"] = traceback.format_exc()
finally:
    (root / "result.json").write_text(json.dumps(report), encoding="utf-8")
if "error" in report:
    raise SystemExit(1)
"""


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("failure", ["timeout", "cancel"])
def test_retained_guardian_from_pythonw_settles_without_desktop_side_effects(tmp_path, failure):
    repo = Path(__file__).resolve().parents[3]
    observer_module = _load(
        "installer_guardian_window_observer",
        repo / "libs" / "agent-procutil" / "tests" / "test_spawn_daemon_windows_integration.py",
    )
    user32, kernel32, callback_type = observer_module._configure_win32()
    if not user32.GetForegroundWindow():
        pytest.skip("desktop observation requires an observable foreground window")
    pythonw = pu.windowless_python(sys.executable)
    if not pythonw.lower().endswith("pythonw.exe") or not Path(pythonw).is_file():
        pytest.skip("this interpreter has no real windowless parent")
    guardian = _load(
        "windowless_installer_guardian",
        repo / "libs" / "installer-engine" / "installer_engine_process.py",
    )
    runtime_path = repo / "libs" / "versioned-runtime" / "versioned_runtime.py"
    runtime = guardian._load_resource("windowless_guardian_runtime", runtime_path)
    child = tmp_path / "console-child.py"
    child.write_text(textwrap.dedent(_CHILD), encoding="utf-8")
    parent = tmp_path / "windowless-parent.py"
    parent.write_text(textwrap.dedent(_PARENT), encoding="utf-8")
    spec = {
        "root": str(tmp_path),
        "guardian": guardian.__file__,
        "runtime": str(runtime_path),
        "procutil": str(
            repo / "libs" / "agent-procutil" / "src" / "agent_procutil" / "__init__.py"
        ),
        "console": observer_module._console_python(),
        "child": str(child),
        "failure": failure,
    }
    handles = []
    log_path = tmp_path / "parent.log"
    with observer_module._WindowObserver(user32, kernel32, callback_type) as observer:
        with log_path.open("wb") as log:
            process, job = pu.spawn_sync_in_kill_on_close_job(
                [pythonw, "-I", "-B", str(parent), json.dumps(spec)],
                require_job=True, allow_breakaway=False,
                cwd=tmp_path,
                env={**os.environ, **pu.windowless_python_env(sys.executable)},
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                **pu.no_window_kwargs(),
            )
            try:
                assert job is not None, "windowless parent needs strict outer containment"
                for cycle in range(3):
                    state = tmp_path / str(cycle)
                    def started(state=state):
                        identities = observer_module._read_json(state / "pids.json")
                        if identities:
                            return identities
                        if process.poll() is not None:
                            pytest.fail(
                                "windowless parent exited before child startup: "
                                + json.dumps(observer_module._read_json(tmp_path / "result.json"))
                            )
                        return None

                    pids = observer_module._wait_for(
                        started,
                        15, f"cycle {cycle} owned process identities", log_path,
                    )
                    cycle_handles = []
                    for pid in pids:
                        handle = kernel32.OpenProcess(0x00100000, False, pid)
                        assert handle, f"owned process {pid} exited before handle witness"
                        handles.append(handle)
                        cycle_handles.append(handle)
                        assert kernel32.WaitForSingleObject(handle, 0) == 0x102
                    # Probe retention alone: construction is owned by the pythonw
                    # parent and must not mask a missing guardian retention lease.
                    with pytest.raises(runtime.RetentionTimeout):
                        with runtime._kernel_lease(
                            tmp_path / "runtime", "probe",
                            kind="retention", shared=False, timeout=0,
                        ):
                            pass
                    if cycle < 2:
                        (state / "release").touch()
                    elif failure == "cancel":
                        progress = observer_module._read_json(tmp_path / "progress.json")
                        assert progress == {"cycles": 2}
                        # Cancel only this pinned owner, leaving its guardian alive
                        # to observe EOF and settle its strict child Job itself.
                        process.terminate()
                    for handle in cycle_handles:
                        assert kernel32.WaitForSingleObject(handle, 15000) == 0, (
                            f"cycle {cycle} owned process survived settlement"
                        )
                returncode = process.wait(timeout=15)
                if failure == "timeout":
                    assert returncode == 0, log_path.read_text(errors="replace")
                    assert observer_module._read_json(tmp_path / "result.json") == {
                        "cycles": 2, "timed_out": True,
                    }
                else:
                    assert returncode != 0
                with runtime._cleanup_lease(tmp_path / "runtime", "probe") as admitted:
                    assert admitted, "live construction/retention remained after failure"
            finally:
                job.terminate_and_wait(10)
                process.wait(timeout=10)
                for handle in handles:
                    assert kernel32.CloseHandle(handle)
    assert observer.surfaced == {}, f"visible/Default Terminal windows: {observer.surfaced}"
    assert observer.focus_changes == {}, f"foreground transitions: {observer.focus_changes}"
