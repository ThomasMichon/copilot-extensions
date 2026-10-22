"""Opt-in actual Windows caller-Job closure, with pinned cleanup and controls."""

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import agent_procutil as pu
import single_instance_lease
import update_driver
from update_driver import RequestSpec, TrustedRunner, enqueue, status, wait

pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("UPDATE_DRIVER_NATIVE_LIFETIME") != "1",
    reason="explicit Windows production-lifetime qualification only",
)


def observe_until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError("native lifetime observation deadline expired")


def source_environment():
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        str(Path(package.__file__).resolve().parent.parent)
        for package in (update_driver, pu, single_instance_lease)
    )
    return env


def window_observer():
    # Reuse the canonical procutil observer rather than inventing a competing
    # visibility/foreground measurement. This native lane runs from repo source.
    path = (
        Path(__file__).resolve().parents[2] / "agent-procutil" / "tests"
        / "test_spawn_daemon_windows_integration.py"
    )
    spec = importlib.util.spec_from_file_location("_driver_native_windows_observer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    user, kernel, callback = module._configure_win32()
    return module._WindowObserver(user, kernel, callback)


@pytest.mark.parametrize("mode", ["production", "contained"])
def test_native_worker_survives_submitter_job_only_in_production(area, mode):
    from agent_procutil.daemon_broker import WindowsDaemonProcess
    import ctypes
    from ctypes import wintypes

    console = getattr(sys, "_base_executable", None) or sys.executable
    script = Path(__file__).with_name("fixture_native_lifetime.py").resolve()
    env = source_environment()
    with window_observer() as observation:
        for cycle in range(2):
            root = area / f"{mode}-{cycle}"
            root.mkdir()
            request = enqueue(
                RequestSpec("native-fixture", argv=(str(cycle),)),
                runner=TrustedRunner(
                    (console, str(script), "product", str(root), console, mode),
                    str(area), timeout=25, env=(("PYTHONPATH", env["PYTHONPATH"]),),
                ), root=root,
            )
            pythonw = pu.windowless_python(sys.executable)
            assert pythonw.lower().endswith("pythonw.exe")
            parent, job = pu.spawn_sync_in_kill_on_close_job(
                [pythonw, str(script), "submit", str(root), console, mode],
                cwd=area, env=env, require_job=True, allow_breakaway=False,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                **pu.no_window_kwargs(),
            )
            worker = control = None
            try:
                assert job is not None
                assert parent.wait(timeout=30) == 0, (
                    (root / "worker.log").read_text(errors="replace")
                    if (root / "worker.log").exists() else "submitter startup failed"
                )
                details = json.loads((root / "submitter.json").read_text())
                worker = WindowsDaemonProcess(details["worker"], ["fixture-worker"])
                control = WindowsDaemonProcess(details["control"], ["ordinary-contained-control"])
                observe_until(lambda: (root / "started").exists())
                assert status(request.id, root=root).state == "running"

                kernel = pu._kernel32()
                kernel.IsProcessInJob.argtypes = [
                    wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL),
                ]
                membership = wintypes.BOOL()
                assert kernel.IsProcessInJob(worker._handle, job.handle, ctypes.byref(membership))
                assert bool(membership.value) is (mode == "contained")
                assert kernel.IsProcessInJob(control._handle, job.handle, ctypes.byref(membership))
                assert membership.value
                assert control.poll() is None

                job.close()
                job = None
                # KILL_ON_JOB_CLOSE may report zero. Actual pre-close liveness,
                # membership and prompt handle-signalled exit prove retirement.
                control.wait(timeout=5)
                if mode == "contained":
                    worker.wait(timeout=5)
                    assert status(request.id, root=root).state == "running"
                else:
                    assert worker.poll() is None
                    (root / "continue").write_text("go", encoding="ascii")
                    outcome = wait(request.id, root=root, timeout=15)
                    assert outcome.state == "completed" and outcome.exit_code == 7
                    assert worker.wait(timeout=10) == 0
                    log = outcome.log_path.read_text()
                    assert "post-Job console child" in log
                    assert "post-Job installation stage" in log
            finally:
                # These handles were pinned while both exact fixture children
                # were live. Never reopen/kill an unknown PID after a timeout.
                for child in (worker, control):
                    if child is not None:
                        try:
                            if child.poll() is None:
                                try:
                                    child.wait(timeout=.2)
                                except subprocess.TimeoutExpired:
                                    try:
                                        child.terminate()
                                    except PermissionError:
                                        pass  # Job termination may already be in progress.
                                    child.wait(timeout=5)
                        finally:
                            child.close()
                if job is not None:
                    job.close()
                if parent.poll() is None:
                    parent.kill()
                    parent.wait(timeout=5)
        time.sleep(.1)
    assert observation.surfaced == {}, observation.surfaced
    assert observation.focus_changes == {}, observation.focus_changes
