"""The stdlib guardian observes exit without reaping on older macOS Python."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import venv
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack

import pytest
import agent_procutil
from agent_procutil import no_window_kwargs


@pytest.fixture
def guardian():
    source = Path(__file__).resolve().parents[3] / "libs" / "installer-engine" / "installer_engine_process.py"
    spec = importlib.util.spec_from_file_location("guardian_fixture", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("state,exited", [("S\n", False), ("Z\n", True), ("Z+\n", True)])
def test_missing_waitid_uses_nonreaping_process_state(guardian, monkeypatch, state, exited):
    monkeypatch.setattr(guardian, "os", SimpleNamespace())
    calls = []

    def observe(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(stdout=state)

    monkeypatch.setattr(guardian.subprocess, "run", observe)
    assert guardian._child_exited_unreaped(123) is exited
    assert calls[0][0] == ["ps", "-o", "stat=", "-p", "123"]
    assert calls[0][1]["check"] is True and calls[0][1]["timeout"] == 5


@pytest.mark.parametrize("state", ["", "Z\nS\n"])
def test_ambiguous_process_observation_is_not_exit(guardian, monkeypatch, state):
    monkeypatch.setattr(guardian, "os", SimpleNamespace())
    monkeypatch.setattr(guardian.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=state))
    with pytest.raises(RuntimeError, match="Cannot observe"):
        guardian._child_exited_unreaped(123)


@pytest.mark.parametrize("code,expected", [(23, 23), (-15, 143)])
def test_guardian_reaps_only_after_group_settlement(guardian, monkeypatch, code, expected):
    process = SimpleNamespace(pid=123, returncode=None)
    events = []
    observations = iter([False, True])
    monkeypatch.setattr(guardian, "os", SimpleNamespace(
        close=lambda fd: events.append(("closed", fd)),
    ))
    monkeypatch.setattr(guardian, "subprocess", SimpleNamespace(
        Popen=lambda *a, **k: process, DEVNULL=subprocess.DEVNULL,
    ))
    monkeypatch.setattr(guardian.select, "select", lambda *args: ([], [], []))

    def observe(pid):
        assert pid == 123 and process.returncode is None
        return next(observations)

    def settle(child):
        assert child is process and child.returncode is None
        events.append("settled")
        child.returncode = code

    monkeypatch.setattr(guardian, "_child_exited_unreaped", observe)
    monkeypatch.setattr(guardian, "_terminate_group", settle)
    assert guardian._supervise(17, ["fixture"]) == expected
    assert events == ["settled", ("closed", 17)]


@pytest.mark.parametrize("live_member", [False, True])
def test_zombie_only_group_eperm_settles_via_process_observation(guardian, monkeypatch, live_member):
    waits = []
    process = SimpleNamespace(pid=123, returncode=None, wait=lambda timeout: waits.append(timeout))

    def killpg(pgid, sig):
        raise PermissionError(1, "Operation not permitted")

    rows = "  123   123 Z\n" + ("  124   123 S\n" if live_member else "")
    clock = iter([0.0, 0.0, 11.0])
    monkeypatch.setattr(guardian, "os", SimpleNamespace(killpg=killpg))
    monkeypatch.setattr(guardian, "signal", SimpleNamespace(SIGKILL=9))
    monkeypatch.setattr(guardian, "subprocess", SimpleNamespace(
        run=lambda *a, **k: SimpleNamespace(stdout=rows), DEVNULL=subprocess.DEVNULL,
    ))
    monkeypatch.setattr(guardian, "time", SimpleNamespace(
        monotonic=lambda: next(clock), sleep=lambda seconds: None,
    ))
    if live_member:
        with pytest.raises(TimeoutError, match="live writers"):
            guardian._terminate_group(process)
        assert waits == []
    else:
        guardian._terminate_group(process)
        assert waits == [10]


@pytest.mark.skipif(os.name == "nt", reason="native POSIX ps/process-group guardian")
def test_real_guardian_without_waitid_preserves_child_exit(guardian, tmp_path):
    script = (
        "import importlib.util,os,sys;"
        "s=importlib.util.spec_from_file_location('guardian',sys.argv[1]);"
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
        "setattr(m.os,'waitid',None);r,w=os.pipe();"
        "result=m._supervise(r,[sys.executable,'-c','raise SystemExit(23)']);"
        "os.close(w);raise SystemExit(result)"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, guardian.__file__],
        cwd=tmp_path, capture_output=True, text=True, timeout=15,
        **no_window_kwargs(),
    )
    assert result.returncode == 23, result.stdout + result.stderr


@pytest.mark.skipif(os.name != "nt", reason="native Windows retained Job guardian")
@pytest.mark.parametrize("outcome", ["success", "exit", "timeout", "absolute-timeout", "launch"])
@pytest.mark.timeout(30)
def test_retained_windows_child_preserves_outcome_and_releases_custody(
    guardian, tmp_path, outcome,
):
    runtime_path = (
        Path(__file__).resolve().parents[3]
        / "libs" / "versioned-runtime" / "versioned_runtime.py"
    )
    runtime = guardian._load_resource("retained_guardian_test_runtime", runtime_path)
    root = tmp_path / "runtime"
    root.mkdir()
    slot = runtime.slot(root, "probe")
    commands = {
        "success": [sys.executable, "-I", "-c", "print('owned')"],
        "exit": [sys.executable, "-I", "-c", "raise SystemExit(23)"],
        "timeout": [sys.executable, "-I", "-c", "import time; time.sleep(60)"],
        "absolute-timeout": [sys.executable, "-I", "-c", "import time; time.sleep(60)"],
        "launch": [str(tmp_path / "missing-child.exe")],
    }
    errors = {
        "exit": subprocess.CalledProcessError,
        "timeout": guardian.ChildTimeoutExpired,
        "absolute-timeout": guardian.ChildTimeoutExpired,
        "launch": guardian.ChildLaunchError,
    }
    with runtime.construction_lease(root, "probe") as descriptor:
        def run():
            return guardian.run_owned(
                commands[outcome], cwd=slot, env=dict(os.environ),
                timeout=3, procutil=agent_procutil, construction_fd=descriptor,
                runtime_root=root, runtime_version="probe", runtime_path=runtime_path,
                **({"deadline": time.monotonic() + 3} if outcome == "absolute-timeout" else {}),
            )

        if outcome == "success":
            assert run().stdout == "owned\n"
        else:
            with pytest.raises(errors[outcome]) as caught:
                run()
            if outcome == "exit":
                assert caught.value.returncode == 23
        with runtime._cleanup_lease(root, "probe", allow_construction_owner=True) as admitted:
            assert admitted


_CONTROL_CHILD_PROBE = """
import ctypes,importlib.util,json,os,subprocess,sys,time
from ctypes import wintypes
spec=importlib.util.spec_from_file_location("lifetime_procutil",sys.argv[2])
agent_procutil=importlib.util.module_from_spec(spec);spec.loader.exec_module(agent_procutil)
kernel=ctypes.WinDLL("kernel32",use_last_error=True)
kernel.OpenProcess.argtypes=(wintypes.DWORD,wintypes.BOOL,wintypes.DWORD)
kernel.OpenProcess.restype=wintypes.HANDLE
kernel.GetCurrentProcess.restype=wintypes.HANDLE
kernel.DuplicateHandle.argtypes=(wintypes.HANDLE,wintypes.HANDLE,wintypes.HANDLE,
    ctypes.POINTER(wintypes.HANDLE),wintypes.DWORD,wintypes.BOOL,wintypes.DWORD)
kernel.DuplicateHandle.restype=wintypes.BOOL
kernel.CloseHandle.argtypes=(wintypes.HANDLE,)
receiver=kernel.OpenProcess(0x40,False,int(sys.argv[1]))
if not receiver:
    raise ctypes.WinError(ctypes.get_last_error())
def duplicate(process_handle):
    handle=wintypes.HANDLE()
    if not kernel.DuplicateHandle(kernel.GetCurrentProcess(),process_handle,
        receiver,ctypes.byref(handle),0,False,2):
        raise ctypes.WinError(ctypes.get_last_error())
    return handle.value
def start(options):
    process=subprocess.Popen(
        [sys.executable,"-I","-c","import time;time.sleep(10)"],
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        executable=sys.executable,**options)
    return duplicate(int(process._handle))
try:
    result={"ordinary":start(agent_procutil.no_window_kwargs())}
    try:
        result["durable"]=start(agent_procutil.windowless_daemon_kwargs(breakaway=True))
    except OSError as error:
        result["refused"]=error.winerror
    if len(sys.argv)>5:
        holder=kernel.OpenProcess(0x100000|0x1000,False,int(sys.argv[5]))
        if not holder:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            result["scope_guardian"]=duplicate(holder)
        finally:
            kernel.CloseHandle(holder)
    if len(sys.argv)>4:
        from pathlib import Path
        ready=Path(sys.argv[4]);temporary=ready.with_suffix(".tmp")
        temporary.write_text(json.dumps(result),encoding="utf-8")
        temporary.replace(ready)
    print(json.dumps(result),flush=True)
    if sys.argv[3]!="success":
        time.sleep(60)
finally:
    kernel.CloseHandle(receiver)
"""


@pytest.mark.skipif(os.name != "nt", reason="native Windows explicit daemon breakaway")
@pytest.mark.parametrize("allow_breakaway", [False, True])
@pytest.mark.parametrize("timeout", [False, True])
@pytest.mark.timeout(30)
def test_retained_control_preserves_only_explicit_durable_children(
    guardian, tmp_path, allow_breakaway, timeout,
):
    import ctypes
    from ctypes import wintypes

    runtime_path = Path(__file__).resolve().parents[3] / "libs" / "versioned-runtime" / "versioned_runtime.py"
    runtime = guardian._load_resource("control_lifetime_runtime", runtime_path)
    root = tmp_path / "runtime"
    slot = runtime.slot(root, "control")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handles = []
    try:
        with runtime.construction_lease(root, "control") as descriptor:
            def run():
                return guardian.run_owned(
                    [sys._base_executable, "-I", "-B", "-c", _CONTROL_CHILD_PROBE, str(os.getpid()),
                     agent_procutil.__file__, "timeout" if timeout else "success"],
                    cwd=slot, env=dict(os.environ), timeout=5, procutil=agent_procutil,
                    construction_fd=descriptor, runtime_root=root, runtime_version="control",
                    runtime_path=runtime_path, allow_breakaway=allow_breakaway,
                )

            if timeout:
                with pytest.raises(guardian.ChildTimeoutExpired) as caught:
                    run()
                output = caught.value.stdout
            else:
                output = run().stdout
        evidence = json.loads(output)
        handles.extend(evidence[key] for key in ("ordinary", "durable") if key in evidence)
        assert kernel.WaitForSingleObject(evidence["ordinary"], 0) == 0, evidence
        if agent_procutil.contained_test_mode():
            assert "refused" not in evidence, evidence
            assert kernel.WaitForSingleObject(evidence["durable"], 0) == 0, evidence
        elif allow_breakaway:
            assert "refused" not in evidence, evidence
            assert kernel.WaitForSingleObject(evidence["durable"], 0) == 258, evidence
        else:
            assert evidence.get("refused") == 5 and "durable" not in evidence, evidence
    finally:
        for handle in handles:
            try:
                assert kernel.WaitForSingleObject(handle, 15000) == 0
            finally:
                kernel.CloseHandle(handle)


@pytest.mark.skipif(os.name != "nt", reason="native Windows caller-death retention fence")
@pytest.mark.timeout(30)
def test_mode_control_owner_death_cannot_release_runtime_before_writers_exit(guardian, tmp_path):
    import ctypes
    from ctypes import wintypes

    runtime_path = Path(__file__).resolve().parents[3] / "libs" / "versioned-runtime" / "versioned_runtime.py"
    runtime = guardian._load_resource("control_owner_death_runtime", runtime_path)
    root = tmp_path / "runtime"
    runtime.slot(root, "control")
    ready = tmp_path / "ready.json"
    child = [
        sys._base_executable, "-I", "-B", "-c", _CONTROL_CHILD_PROBE, str(os.getpid()),
        agent_procutil.__file__, "owner-death", str(ready),
    ]
    owner_code = """
import importlib.util,json,os,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location("owner_guardian",sys.argv[1])
guardian=importlib.util.module_from_spec(spec);spec.loader.exec_module(guardian)
runtime=guardian._load_resource("owner_runtime",sys.argv[2])
procutil=guardian._load_resource("owner_procutil",sys.argv[3])
root=Path(sys.argv[4])
with runtime.retention_custody(root,"control") as (slot,descriptor):
    guardian.run_owned(json.loads(sys.argv[5]),cwd=slot,env=dict(os.environ),timeout=20,
        procutil=procutil,construction_fd=descriptor,runtime_root=root,
        runtime_version="control",runtime_path=Path(sys.argv[2]),allow_breakaway=True)
"""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    owner = subprocess.Popen(
        [sys._base_executable, "-I", "-B", "-c", owner_code, guardian.__file__,
         str(runtime_path), agent_procutil.__file__, str(root), json.dumps(child)],
        executable=sys._base_executable, cwd=tmp_path, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **no_window_kwargs(),
    )
    handles = []
    try:
        deadline = time.monotonic() + 10
        while not ready.exists():
            if owner.poll() is not None:
                output, error = owner.communicate(timeout=1)
                pytest.fail(f"Control owner exited before readiness:\n{output}\n{error}")
            assert time.monotonic() < deadline, "Control children did not become ready"
            time.sleep(0.02)
        evidence = json.loads(ready.read_text(encoding="utf-8"))
        handles.extend(evidence[key] for key in ("ordinary", "durable") if key in evidence)
        owner.kill()
        owner.communicate(timeout=10)
        deadline = time.monotonic() + 10
        while True:
            try:
                with runtime._kernel_lease(
                    root, "control", kind="retention", shared=False, timeout=0,
                ):
                    assert kernel.WaitForSingleObject(evidence["ordinary"], 0) == 0
                    break
            except runtime.RetentionTimeout:
                assert time.monotonic() < deadline, "Guardian did not settle the owned control scope"
                time.sleep(0.02)
        if agent_procutil.contained_test_mode():
            assert kernel.WaitForSingleObject(evidence["durable"], 0) == 0
        else:
            assert kernel.WaitForSingleObject(evidence["durable"], 0) == 258
    finally:
        if owner.poll() is None:
            owner.kill()
        owner.communicate(timeout=10)
        for handle in handles:
            try:
                assert kernel.WaitForSingleObject(handle, 15000) == 0
            finally:
                kernel.CloseHandle(handle)


@pytest.mark.parametrize("allow_breakaway", [None, 0, "true"])
def test_installer_breakaway_policy_refuses_nonboolean_values(guardian, tmp_path, allow_breakaway):
    with pytest.raises(ValueError, match="breakaway policy"):
        guardian.run_owned(
            ["unused"], cwd=tmp_path, env={}, timeout=1, procutil=None,
            construction_fd=-1, allow_breakaway=allow_breakaway,
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows finite mode-owner retention protocol")
def test_mode_scope_acknowledges_only_after_real_candidate_retention(guardian, tmp_path):
    runtime_path = Path(__file__).resolve().parents[3] / "libs" / "versioned-runtime" / "versioned_runtime.py"
    runtime = guardian._load_resource("scope_registration_runtime", runtime_path)
    root = tmp_path / "runtime"
    runtime.slot(root, "candidate")
    with ExitStack() as custody, ThreadPoolExecutor(max_workers=1) as executor:
        scope = guardian._RetentionScope(tmp_path / "scope", root, runtime, custody)
        waiting = executor.submit(
            guardian._register_retention, scope.environment(), root, "candidate", 3,
        )
        deadline = time.monotonic() + 3
        while not waiting.done():
            scope.retain_requests(1)
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert 0 < waiting.result() <= 3
        with pytest.raises(runtime.RetentionTimeout):
            with runtime._kernel_lease(root, "candidate", kind="retention", shared=False, timeout=0):
                pytest.fail("acknowledgement was published without guardian retention")
    with runtime._kernel_lease(root, "candidate", kind="retention", shared=False, timeout=0):
        pass


@pytest.mark.parametrize("failure", ["cancelled", "denied"])
def test_mode_scope_distinguishes_cancelled_request_from_storage_failure(
    guardian, tmp_path, monkeypatch, failure,
):
    with ExitStack() as custody:
        scope = guardian._RetentionScope(tmp_path / "scope", tmp_path, None, custody)
        request = scope.directory / "cancelled.request"
        guardian._scope_packet(request, {})
        read_packet = guardian._read_scope_packet

        def read(path):
            assert path == request
            if failure == "cancelled":
                path.unlink()
                return read_packet(path)
            raise PermissionError("request storage denied")

        monkeypatch.setattr(guardian, "_read_scope_packet", read)
        if failure == "cancelled":
            scope.retain_requests(1)
        else:
            with pytest.raises(PermissionError, match="request storage denied"):
                scope.retain_requests(1)
        assert not scope.handled and not scope.versions
        assert not request.with_suffix(".ack").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows atomic-rename DELETE sharing")
def test_mode_scope_packet_reads_while_publisher_retains_delete_access(guardian, tmp_path):
    import ctypes
    from ctypes import wintypes

    packet = tmp_path / "published.ack"
    value = {"retained": True, "nonce": "fixture"}
    guardian._scope_packet(packet, value)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    publisher = kernel.CreateFileW(str(packet), 0x10000, 0x7, None, 3, 0x80, None)
    assert publisher != wintypes.HANDLE(-1).value, ctypes.WinError(ctypes.get_last_error())
    try:
        with pytest.raises(PermissionError):
            packet.open("rb")
        assert guardian._read_scope_packet(packet) == value
    finally:
        assert kernel.CloseHandle(publisher)
    assert packet.read_text(encoding="utf-8") == json.dumps(value)


@pytest.mark.skipif(os.name != "nt", reason="Windows finite mode-owner retention protocol")
def test_mode_scope_rejects_success_shaped_integer_acknowledgement(guardian, tmp_path):
    runtime_path = Path(__file__).resolve().parents[3] / "libs" / "versioned-runtime" / "versioned_runtime.py"
    runtime = guardian._load_resource("scope_invalid_ack_runtime", runtime_path)
    with ExitStack() as custody, ThreadPoolExecutor(max_workers=1) as executor:
        scope = guardian._RetentionScope(tmp_path / "scope", tmp_path, runtime, custody)
        waiting = executor.submit(
            guardian._register_retention, scope.environment(), tmp_path, "candidate", 3,
        )
        deadline = time.monotonic() + 3
        while not (requests := list(scope.directory.glob("*.request"))):
            assert time.monotonic() < deadline
            time.sleep(0.01)
        request = requests[0]
        packet = guardian._read_scope_packet(request)
        guardian._scope_packet(request.with_suffix(".ack"), {**packet, "retained": 1})
        with pytest.raises(guardian.ChildLaunchError, match="requested retention"):
            waiting.result(timeout=3)


@pytest.mark.skipif(os.name != "nt", reason="Windows finite mode-owner retention protocol")
@pytest.mark.parametrize("failure", ["foreign-root", "invalid-version", "storage"])
def test_mode_scope_refuses_invalid_or_unavailable_candidate_custody(
    guardian, tmp_path, monkeypatch, failure,
):
    runtime_path = Path(__file__).resolve().parents[3] / "libs" / "versioned-runtime" / "versioned_runtime.py"
    runtime = guardian._load_resource("scope_refusal_runtime", runtime_path)
    with ExitStack() as custody:
        scope = guardian._RetentionScope(tmp_path / "scope", tmp_path, runtime, custody)
        packet = {
            "nonce": scope.nonce, "root": str(tmp_path.resolve()),
            "runtime_version": "candidate",
        }
        if failure == "foreign-root":
            packet["root"] = str(tmp_path / "foreign")
        elif failure == "invalid-version":
            packet["runtime_version"] = "../foreign"
        else:
            def denied(*_args, **_kwargs):
                raise PermissionError("retention storage denied")

            monkeypatch.setattr(runtime, "retention_lease", denied)
        request = scope.directory / "fixture.request"
        guardian._scope_packet(request, packet)
        expected = {
            "foreign-root": RuntimeError, "invalid-version": ValueError, "storage": PermissionError,
        }[failure]
        with pytest.raises(expected):
            scope.retain_requests(1)
        assert not request.with_suffix(".ack").exists()


@pytest.mark.skipif(os.name != "nt", reason="native Windows nested Job retention accounting")
@pytest.mark.timeout(30)
def test_mode_scope_coretains_nested_writers_when_caller_job_closes(guardian, tmp_path):
    import ctypes
    from ctypes import wintypes

    runtime_path = Path(__file__).resolve().parents[3] / "libs" / "versioned-runtime" / "versioned_runtime.py"
    runtime = guardian._load_resource("nested_mode_retention_runtime", runtime_path)
    root = tmp_path / "runtime"
    runtime.slot(root, "control")
    ready = tmp_path / "ready.json"
    writer = [
        sys._base_executable, "-I", "-B", "-c", _CONTROL_CHILD_PROBE, str(os.getpid()),
        agent_procutil.__file__, "owner-death", str(ready),
    ]
    builder_code = """
import importlib.util,json,os,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location("builder_guardian",sys.argv[1])
guardian=importlib.util.module_from_spec(spec);spec.loader.exec_module(guardian)
runtime=guardian._load_resource("builder_runtime",sys.argv[2])
procutil=guardian._load_resource("builder_procutil",sys.argv[3])
root=Path(sys.argv[4]);slot=runtime.slot(root,"candidate")
child=json.loads(sys.argv[5])
scope=json.loads(os.environ[guardian._RETENTION_SCOPE_ENV])
child.append(str(scope["guardian_pid"]))
with runtime.construction_lease(root,"candidate") as descriptor:
    guardian.run_owned(child,cwd=slot,env=dict(os.environ),timeout=20,
        procutil=procutil,construction_fd=descriptor,runtime_root=root,
        runtime_version="candidate",runtime_path=Path(sys.argv[2]))
"""
    builder = [
        sys._base_executable, "-I", "-B", "-c", builder_code, guardian.__file__,
        str(runtime_path), agent_procutil.__file__, str(root), json.dumps(writer),
    ]
    caller_code = """
import importlib.util,json,os,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location("caller_guardian",sys.argv[1])
guardian=importlib.util.module_from_spec(spec);spec.loader.exec_module(guardian)
runtime=guardian._load_resource("caller_runtime",sys.argv[2])
procutil=guardian._load_resource("caller_procutil",sys.argv[3])
root=Path(sys.argv[4])
with runtime.retention_custody(root,"control") as (slot,descriptor):
    guardian.run_owned(json.loads(sys.argv[5]),cwd=slot,env=dict(os.environ),timeout=20,
        procutil=procutil,construction_fd=descriptor,runtime_root=root,
        runtime_version="control",runtime_path=Path(sys.argv[2]),allow_breakaway=True,
        retention_scope=Path(sys.argv[6]))
"""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.IsProcessInJob.argtypes = (
        wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL),
    )
    kernel.IsProcessInJob.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    caller, job = agent_procutil.spawn_sync_in_kill_on_close_job(
        [sys._base_executable, "-I", "-B", "-c", caller_code, guardian.__file__,
         str(runtime_path), agent_procutil.__file__, str(root), json.dumps(builder),
         str(tmp_path / "scope")],
        require_job=True, allow_breakaway=True, executable=sys._base_executable,
        cwd=tmp_path, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, **no_window_kwargs(),
    )
    handles = []
    try:
        deadline = time.monotonic() + 10
        while not ready.exists():
            if caller.poll() is not None:
                output, error = caller.communicate(timeout=1)
                pytest.fail(f"Scoped caller exited before readiness:\n{output}\n{error}")
            assert time.monotonic() < deadline, "Nested retained writer did not become ready"
            time.sleep(0.02)
        evidence = json.loads(ready.read_text(encoding="utf-8"))
        handles.extend(evidence[key] for key in ("ordinary", "durable", "scope_guardian") if key in evidence)
        in_job = wintypes.BOOL()
        assert kernel.IsProcessInJob(
            evidence["scope_guardian"], job.handle, ctypes.byref(in_job),
        )
        assert bool(in_job.value) is agent_procutil.contained_test_mode(), {
            "in_caller_job": bool(in_job.value), "contained": agent_procutil.contained_test_mode(),
        }
        if agent_procutil.contained_test_mode():
            caller.kill()
        else:
            job.terminate_and_wait(5)
        caller.communicate(timeout=10)
        deadline = time.monotonic() + 10
        while True:
            try:
                with runtime._kernel_lease(
                    root, "candidate", kind="retention", shared=False, timeout=0,
                ):
                    assert kernel.WaitForSingleObject(evidence["ordinary"], 0) == 0
                    break
            except runtime.RetentionTimeout:
                assert time.monotonic() < deadline, "Nested writer custody did not settle"
                time.sleep(0.02)
    finally:
        if caller.poll() is None:
            caller.kill()
        caller.communicate(timeout=10)
        for handle in handles:
            try:
                assert kernel.WaitForSingleObject(handle, 15000) == 0
            finally:
                kernel.CloseHandle(handle)
        if job.handle is not None:
            job.terminate_and_wait(5)


@pytest.mark.skipif(os.name != "nt", reason="native Windows executable-token limit")
@pytest.mark.timeout(30)
def test_retained_windows_guardian_accepts_a_long_owning_interpreter(guardian, tmp_path):
    environment = tmp_path / "owner"
    while len(str(environment / "Scripts" / "python.exe")) <= 270:
        environment /= "interpreter-" + "x" * 30
    venv.EnvBuilder(with_pip=False, symlinks=False).create(environment)
    python = environment / "Scripts" / "python.exe"
    repo = Path(__file__).resolve().parents[3]
    code = """
import importlib.util,json,sys
from pathlib import Path
s=importlib.util.spec_from_file_location("long_owner_guardian",sys.argv[1])
guardian=importlib.util.module_from_spec(s);s.loader.exec_module(guardian)
runtime=guardian._load_resource("long_owner_runtime",sys.argv[2])
procutil=guardian._load_resource("long_owner_procutil",sys.argv[3])
root=Path(sys.argv[4]);root.mkdir();slot=runtime.slot(root,"probe")
assert len(sys.executable)>260
with runtime.construction_lease(root,"probe") as descriptor:
    result=guardian.run_owned(
        [sys._base_executable,"-I","-c","print('owned')"],
        cwd=slot,env=dict(__import__("os").environ),timeout=3,
        procutil=procutil,construction_fd=descriptor,
        runtime_root=root,runtime_version="probe",runtime_path=Path(sys.argv[2]),
    )
    with runtime._cleanup_lease(root,"probe",allow_construction_owner=True) as admitted:
        assert admitted
print(json.dumps({"owner":sys.executable,"output":result.stdout}))
"""
    result = subprocess.run(
        [str(python), "-I", "-B", "-c", code, guardian.__file__,
         str(repo / "libs" / "versioned-runtime" / "versioned_runtime.py"),
         str(repo / "libs" / "agent-procutil" / "src" / "agent_procutil" / "__init__.py"),
         str(tmp_path / "runtime")],
        executable=str(python), cwd=tmp_path, capture_output=True, text=True, timeout=20,
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert Path(report["owner"]) == python and report["output"] == "owned\n"
