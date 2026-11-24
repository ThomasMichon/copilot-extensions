"""The stdlib guardian observes exit without reaping on older macOS Python."""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import venv
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable
from contextlib import ExitStack
from typing import Any, NoReturn, cast
import pytest
import agent_procutil
from agent_procutil import no_window_kwargs


def _load(name: str, path: "Path") -> "Any":
    spec: Any = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.real_installer_processes
def test_generated_windowless_parent_starts_with_fresh_annotation_semantics(tmp_path: Path) -> None:
    from test_installer_guardian_windowless import _parent_source

    prelude, marker, _ = _parent_source().partition("spec = json.loads(sys.argv[1])")
    assert marker
    sibling = tmp_path / "sibling.py"
    sibling.write_text("VALUE = 37\n", encoding="utf-8")
    parent = tmp_path / "parent-prelude.py"
    parent.write_text(
        prelude + '\nloaded = _load("windowless_parent_probe", Path(sys.argv[1]))\n'
        "assert loaded.VALUE == 37\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(parent), str(sibling)],
        capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture
def guardian() -> Any:
    source = Path(__file__).resolve().parents[3] / "libs" / "installer-engine" / "installer_engine_process.py"
    spec: Any = importlib.util.spec_from_file_location("guardian_fixture", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("with_deadline,with_capture", [
    (False, False), (False, True), (True, False), (True, True),
])
def test_retained_dispatch_preserves_deadline_and_capture_options(
    guardian: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, with_deadline: bool, with_capture: bool,
) -> None:
    monkeypatch.setattr(guardian, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(guardian.time, "monotonic", lambda: 10)
    calls = []
    result = subprocess.CompletedProcess(["owned-child"], 0, "owned output", "")

    def retained(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(kwargs)
        return result

    monkeypatch.setattr(guardian, "_retained_windows", retained)
    helper = tmp_path / "installer_diagnostics.py"
    assert guardian.run_owned(
        ["owned-child"], cwd=tmp_path, env={}, timeout=30,
        procutil=SimpleNamespace(), construction_fd=0, runtime_root=tmp_path,
        runtime_version="probe", runtime_path=tmp_path / "runtime.py",
        deadline=15 if with_deadline else None,
        diagnostics_path=helper if with_capture else None,
    ) is result
    assert calls[0]["timeout"] == (5 if with_deadline else 30)
    assert calls[0].get("deadline") == (15 if with_deadline else None)
    assert calls[0].get("diagnostics_path") == (helper if with_capture else None)
    assert ("deadline" in calls[0]) is with_deadline
    assert ("diagnostics_path" in calls[0]) is with_capture


@pytest.mark.parametrize("damage", ["syntax", "runtime"])
def test_optional_capture_import_failure_does_not_catch_native_body(
    guardian: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], damage: str,
) -> None:
    helper = tmp_path / "broken-diagnostics.py"
    helper.write_text("def invalid(\n" if damage == "syntax" else
                      "raise RuntimeError('optional import failed')\n", encoding="utf-8")
    monkeypatch.delenv("COPILOT_EXTENSIONS_DIAGNOSTICS_MISSING", raising=False)
    primary = RuntimeError("native body failure")
    with pytest.raises(RuntimeError) as caught:
        with guardian._diagnostic_capture(helper) as (destinations, warn):
            assert destinations == {}
            warn()
            raise primary
    assert caught.value is primary
    assert os.environ["COPILOT_EXTENSIONS_DIAGNOSTICS_MISSING"] == "1"
    assert capsys.readouterr().err.count("continuing with missing diagnostics") == 1


@pytest.mark.skipif(os.name != "nt", reason="native Windows retained Job guardian")
@pytest.mark.parametrize("boundary", ["guardian", "parent"])
@pytest.mark.parametrize("damage", ["syntax", "runtime"])
@pytest.mark.timeout(30)
def test_retained_guardian_optional_import_failure_preserves_native_exit(
    guardian: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], boundary: str, damage: str,
) -> None:
    valid = SCRIPTS / "installer_diagnostics.py"
    helper = guardian._load_resource("import_failure_diagnostics_fixture", valid)
    environment = helper.reserve_launch(dict(os.environ, USERPROFILE=str(tmp_path), HOME=str(tmp_path)))
    for name in (
        "HOME", "USERPROFILE", "COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT",
        "COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY",
    ):
        monkeypatch.setenv(name, environment[name])
    monkeypatch.delenv("COPILOT_EXTENSIONS_DIAGNOSTICS_MISSING", raising=False)
    broken = tmp_path / "broken-diagnostics.py"
    broken.write_text("def invalid(\n" if damage == "syntax" else
                      "raise RuntimeError('optional import failed')\n", encoding="utf-8")
    if boundary == "parent":
        original = guardian._load_resource

        def load(name: str, path: Path) -> Any:
            if name == "installer_parent_diagnostics":
                raise SyntaxError("invalid helper") if damage == "syntax" else RuntimeError("helper failed")
            return original(name, path)

        monkeypatch.setattr(guardian, "_load_resource", load)
        broken.unlink()  # The guardian first reports unavailable capture to its parent.
    runtime_path = Path(__file__).resolve().parents[3] / "libs" / "versioned-runtime" / "versioned_runtime.py"
    runtime = guardian._load_resource("optional_import_runtime", runtime_path)
    root = tmp_path / "runtime"
    root.mkdir()
    slot = runtime.slot(root, "probe")
    with runtime.construction_lease(root, "probe") as descriptor:
        with pytest.raises(subprocess.CalledProcessError) as caught:
            guardian.run_owned(
                [sys.executable, "-I", "-c", "print('native child entered',flush=True); raise SystemExit(23)"],
                cwd=slot, env=environment, timeout=3, procutil=agent_procutil,
                construction_fd=descriptor, runtime_root=root, runtime_version="probe",
                runtime_path=runtime_path, diagnostics_path=broken,
            )
        assert caught.value.returncode == 23 and caught.value.stdout == "native child entered\n"
        with runtime._cleanup_lease(root, "probe", allow_construction_owner=True) as admitted:
            assert admitted
    assert os.environ["COPILOT_EXTENSIONS_DIAGNOSTICS_MISSING"] == "1"
    assert "continuing with missing diagnostics" in capsys.readouterr().err
    assert helper.launch_reference(environment)["missing_diagnostics"]


@pytest.mark.parametrize("state,exited", [("S\n", False), ("Z\n", True), ("Z+\n", True)])
def test_missing_waitid_uses_nonreaping_process_state(guardian: Any, monkeypatch: pytest.MonkeyPatch, state: str, exited: bool) -> None:
    monkeypatch.setattr(guardian, "os", SimpleNamespace())
    calls = []

    def observe(argv: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append((argv, kwargs))
        return SimpleNamespace(stdout=state)

    monkeypatch.setattr(guardian.subprocess, "run", observe)
    assert guardian._child_exited_unreaped(123) is exited
    assert calls[0][0] == ["ps", "-o", "stat=", "-p", "123"]
    assert calls[0][1]["check"] is True and calls[0][1]["timeout"] == 5


@pytest.mark.parametrize("state", ["", "Z\nS\n"])
def test_ambiguous_process_observation_is_not_exit(guardian: Any, monkeypatch: pytest.MonkeyPatch, state: str) -> None:
    monkeypatch.setattr(guardian, "os", SimpleNamespace())
    monkeypatch.setattr(guardian.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=state))
    with pytest.raises(RuntimeError, match="Cannot observe"):
        guardian._child_exited_unreaped(123)


@pytest.mark.parametrize("code,expected", [(23, 23), (-15, 143)])
def test_guardian_reaps_only_after_group_settlement(guardian: Any, monkeypatch: pytest.MonkeyPatch, code: int, expected: int) -> None:
    process = SimpleNamespace(pid=123, returncode=None)
    events: list[str | tuple[str, int]] = []
    observations = iter([False, True])
    monkeypatch.setattr(guardian, "os", SimpleNamespace(
        close=lambda fd: events.append(("closed", fd)),
    ))
    monkeypatch.setattr(guardian, "subprocess", SimpleNamespace(
        Popen=lambda *a, **k: process, DEVNULL=subprocess.DEVNULL,
    ))
    monkeypatch.setattr(guardian.select, "select", lambda *args: ([], [], []))

    def observe(pid: int) -> bool:
        assert pid == 123 and process.returncode is None
        return next(observations)

    def settle(child: SimpleNamespace) -> None:
        assert child is process and child.returncode is None
        events.append("settled")
        child.returncode = code

    monkeypatch.setattr(guardian, "_child_exited_unreaped", observe)
    monkeypatch.setattr(guardian, "_terminate_group", settle)
    assert guardian._supervise(17, ["fixture"]) == expected
    assert events == ["settled", ("closed", 17)]


@pytest.mark.parametrize("live_member", [False, True])
def test_zombie_only_group_eperm_settles_via_process_observation(guardian: Any, monkeypatch: pytest.MonkeyPatch, live_member: bool) -> None:
    waits = []
    process = SimpleNamespace(pid=123, returncode=None, wait=lambda timeout: waits.append(timeout))

    def killpg(pgid: int, sig: int) -> NoReturn:
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
def test_real_guardian_without_waitid_preserves_child_exit(guardian: Any, tmp_path: Path) -> None:
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
    guardian: Any, tmp_path: Path, outcome: str,
) -> None:
    runtime_path = (
        Path(__file__).resolve().parents[3]
        / "libs" / "versioned-runtime" / "versioned_runtime.py"
    )
    runtime = _load("retained_guardian_test_runtime", runtime_path)
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
        def run() -> Any:
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
    guardian: Any, tmp_path: Path, allow_breakaway: bool, timeout: bool,
) -> None:
    import ctypes
    from ctypes import wintypes

    runtime_path = Path(__file__).resolve().parents[3] / "libs" / "versioned-runtime" / "versioned_runtime.py"
    runtime = _load("control_lifetime_runtime", runtime_path)
    root = tmp_path / "runtime"
    slot = runtime.slot(root, "control")
    kernel = cast(Any, ctypes).WinDLL("kernel32", use_last_error=True)
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handles: list[int] = []
    try:
        with runtime.construction_lease(root, "control") as descriptor:
            def run() -> Any:
                return guardian.run_owned(
                    [cast(Any, sys)._base_executable, "-I", "-B", "-c", _CONTROL_CHILD_PROBE, str(os.getpid()),
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
def test_mode_control_owner_death_cannot_release_runtime_before_writers_exit(guardian: Any, tmp_path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    runtime_path = Path(__file__).resolve().parents[3] / "libs" / "versioned-runtime" / "versioned_runtime.py"
    runtime = _load("control_owner_death_runtime", runtime_path)
    root = tmp_path / "runtime"
    runtime.slot(root, "control")
    ready = tmp_path / "ready.json"
    child = [
        cast(Any, sys)._base_executable, "-I", "-B", "-c", _CONTROL_CHILD_PROBE, str(os.getpid()),
        agent_procutil.__file__, "owner-death", str(ready),
    ]
    owner_code = inspect.getsource(_load) + """
import importlib.util,json,os,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location("owner_guardian",sys.argv[1])
guardian=importlib.util.module_from_spec(spec);spec.loader.exec_module(guardian)
runtime=_load("owner_runtime",sys.argv[2])
procutil=_load("owner_procutil",sys.argv[3])
root=Path(sys.argv[4])
with runtime.retention_custody(root,"control") as (slot,descriptor):
    guardian.run_owned(json.loads(sys.argv[5]),cwd=slot,env=dict(os.environ),timeout=20,
        procutil=procutil,construction_fd=descriptor,runtime_root=root,
        runtime_version="control",runtime_path=Path(sys.argv[2]),allow_breakaway=True)
"""
    kernel = cast(Any, ctypes).WinDLL("kernel32", use_last_error=True)
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    owner = subprocess.Popen(
        [cast(Any, sys)._base_executable, "-I", "-B", "-c", owner_code, guardian.__file__,
         str(runtime_path), agent_procutil.__file__, str(root), json.dumps(child)],
        executable=cast(Any, sys)._base_executable, cwd=tmp_path, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **no_window_kwargs(),
    )
    handles: list[int] = []
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
def test_installer_breakaway_policy_refuses_nonboolean_values(guardian: Any, tmp_path: Path, allow_breakaway: None | int | str) -> None:
    with pytest.raises(ValueError, match="breakaway policy"):
        guardian.run_owned(
            ["unused"], cwd=tmp_path, env={}, timeout=1, procutil=None,
            construction_fd=-1, allow_breakaway=allow_breakaway,
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows finite mode-owner retention protocol")
def test_mode_scope_acknowledges_only_after_real_candidate_retention(guardian: Any, tmp_path: Path) -> None:
    runtime_path = Path(__file__).resolve().parents[3] / "libs" / "versioned-runtime" / "versioned_runtime.py"
    runtime = _load("scope_registration_runtime", runtime_path)
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
    guardian: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    with ExitStack() as custody:
        scope = guardian._RetentionScope(tmp_path / "scope", tmp_path, None, custody)
        request = scope.directory / "cancelled.request"
        guardian._scope_packet(request, {})
        read_packet = guardian._read_scope_packet

        def read(path: Path) -> Any:
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
def test_mode_scope_packet_reads_while_publisher_retains_delete_access(guardian: Any, tmp_path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    packet = tmp_path / "published.ack"
    value = {"retained": True, "nonce": "fixture"}
    guardian._scope_packet(packet, value)
    kernel = cast(Any, ctypes).WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    publisher = kernel.CreateFileW(str(packet), 0x10000, 0x7, None, 3, 0x80, None)
    assert publisher != wintypes.HANDLE(-1).value, cast(Any, ctypes).WinError(cast(Any, ctypes).get_last_error())
    try:
        with pytest.raises(PermissionError):
            packet.open("rb")
        assert guardian._read_scope_packet(packet) == value
    finally:
        assert kernel.CloseHandle(publisher)
    assert packet.read_text(encoding="utf-8") == json.dumps(value)


@pytest.mark.skipif(os.name != "nt", reason="Windows finite mode-owner retention protocol")
def test_mode_scope_rejects_success_shaped_integer_acknowledgement(guardian: Any, tmp_path: Path) -> None:
    runtime_path = Path(__file__).resolve().parents[3] / "libs" / "versioned-runtime" / "versioned_runtime.py"
    runtime = _load("scope_invalid_ack_runtime", runtime_path)
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
    guardian: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    runtime_path = Path(__file__).resolve().parents[3] / "libs" / "versioned-runtime" / "versioned_runtime.py"
    runtime = _load("scope_refusal_runtime", runtime_path)
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
            def denied(*_args: Any, **_kwargs: Any) -> NoReturn:
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
def test_mode_scope_coretains_nested_writers_when_caller_job_closes(guardian: Any, tmp_path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    runtime_path = Path(__file__).resolve().parents[3] / "libs" / "versioned-runtime" / "versioned_runtime.py"
    runtime = _load("nested_mode_retention_runtime", runtime_path)
    root = tmp_path / "runtime"
    runtime.slot(root, "control")
    ready = tmp_path / "ready.json"
    writer = [
        cast(Any, sys)._base_executable, "-I", "-B", "-c", _CONTROL_CHILD_PROBE, str(os.getpid()),
        agent_procutil.__file__, "owner-death", str(ready),
    ]
    builder_code = inspect.getsource(_load) + """
import importlib.util,json,os,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location("builder_guardian",sys.argv[1])
guardian=importlib.util.module_from_spec(spec);spec.loader.exec_module(guardian)
runtime=_load("builder_runtime",sys.argv[2])
procutil=_load("builder_procutil",sys.argv[3])
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
        cast(Any, sys)._base_executable, "-I", "-B", "-c", builder_code, guardian.__file__,
        str(runtime_path), agent_procutil.__file__, str(root), json.dumps(writer),
    ]
    caller_code = inspect.getsource(_load) + """
import importlib.util,json,os,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location("caller_guardian",sys.argv[1])
guardian=importlib.util.module_from_spec(spec);spec.loader.exec_module(guardian)
runtime=_load("caller_runtime",sys.argv[2])
procutil=_load("caller_procutil",sys.argv[3])
root=Path(sys.argv[4])
with runtime.retention_custody(root,"control") as (slot,descriptor):
    guardian.run_owned(json.loads(sys.argv[5]),cwd=slot,env=dict(os.environ),timeout=20,
        procutil=procutil,construction_fd=descriptor,runtime_root=root,
        runtime_version="control",runtime_path=Path(sys.argv[2]),allow_breakaway=True,
        retention_scope=Path(sys.argv[6]))
"""
    kernel = cast(Any, ctypes).WinDLL("kernel32", use_last_error=True)
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.IsProcessInJob.argtypes = (
        wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL),
    )
    kernel.IsProcessInJob.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    caller, job = agent_procutil.spawn_sync_in_kill_on_close_job(
        [cast(Any, sys)._base_executable, "-I", "-B", "-c", caller_code, guardian.__file__,
         str(runtime_path), agent_procutil.__file__, str(root), json.dumps(builder),
         str(tmp_path / "scope")],
        require_job=True, allow_breakaway=True, executable=cast(Any, sys)._base_executable,
        cwd=tmp_path, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, **no_window_kwargs(),
    )
    handles: list[int] = []
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
            evidence["scope_guardian"], cast(agent_procutil.JobHandle, job).handle, ctypes.byref(in_job),
        )
        assert bool(in_job.value) is agent_procutil.contained_test_mode(), {
            "in_caller_job": bool(in_job.value), "contained": agent_procutil.contained_test_mode(),
        }
        if agent_procutil.contained_test_mode():
            caller.kill()
        else:
            cast(agent_procutil.JobHandle, job).terminate_and_wait(5)
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
        if cast(agent_procutil.JobHandle, job).handle is not None:
            cast(agent_procutil.JobHandle, job).terminate_and_wait(5)


@pytest.mark.skipif(os.name != "nt", reason="native Windows retained Job guardian")
@pytest.mark.parametrize("outcome", ["success", "exit", "timeout", "launch", "unavailable"])
@pytest.mark.timeout(30)
def test_retained_guardian_archives_child_not_protocol_and_preserves_outcome(
    guardian: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str, capsys: pytest.CaptureFixture[str],
) -> None:
    helper_path = SCRIPTS / "installer_diagnostics.py"
    diagnostics = guardian._load_resource("guardian_capture_fixture", helper_path)
    environment = dict(os.environ, USERPROFILE=str(tmp_path), HOME=str(tmp_path))
    environment = diagnostics.reserve_launch(environment)
    directory = Path(environment["COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY"])
    runtime_path = (
        Path(__file__).resolve().parents[3]
        / "libs" / "versioned-runtime" / "versioned_runtime.py"
    )
    runtime = guardian._load_resource("capture_guardian_runtime", runtime_path)
    root = tmp_path / "runtime"
    root.mkdir()
    slot = runtime.slot(root, "probe")
    credential = "gh" + "p_" + "g" * 24
    child_error = credential + "\nchild-error\n"
    script = (
        "import os,sys,time;"
        "os.write(1,b'out\\r\\nprogress\\rcomplete\\n'+b'x'*300000+b'\\n');"
        f"os.write(2,{child_error.encode()!r}+b'y'*300000+b'\\n');"
        + ("time.sleep(60)" if outcome == "timeout" else
           "raise SystemExit(23)" if outcome == "exit" else "")
    )
    command = ([str(tmp_path / "missing.exe")] if outcome == "launch" else
               [sys.executable, "-I", "-c", script])
    if outcome == "unavailable":
        helper_path = tmp_path / "missing-diagnostics.py"
        for name in (
            "HOME", "USERPROFILE", "COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT",
            "COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY",
        ):
            monkeypatch.setenv(name, environment[name])
        monkeypatch.delenv("COPILOT_EXTENSIONS_DIAGNOSTICS_MISSING", raising=False)
    errors = {
        "exit": subprocess.CalledProcessError,
        "timeout": guardian.ChildTimeoutExpired,
        "launch": guardian.ChildLaunchError,
    }
    with runtime.construction_lease(root, "probe") as descriptor:
        def run() -> Any:
            return guardian.run_owned(
                command, cwd=slot, env=environment, timeout=3,
                procutil=agent_procutil, construction_fd=descriptor,
                runtime_root=root, runtime_version="probe", runtime_path=runtime_path,
                diagnostics_path=helper_path,
            )

        if outcome in errors:
            with pytest.raises(errors[outcome]) as caught:
                run()
            result = caught.value
            if outcome == "exit":
                assert result.returncode == 23
        else:
            result = run()
            assert result.returncode == 0
        if outcome != "launch":
            assert result.stdout == "out\nprogress\ncomplete\n" + "x" * 300000 + "\n"
            assert result.stderr == child_error + "y" * 300000 + "\n"
        with runtime._cleanup_lease(root, "probe", allow_construction_owner=True) as admitted:
            assert admitted
    if outcome == "unavailable":
        assert os.environ["COPILOT_EXTENSIONS_DIAGNOSTICS_MISSING"] == "1"
        assert "WARNING: installer diagnostics unavailable" in capsys.readouterr().err
        assert (directory / "missing-diagnostics.json").exists()
        assert diagnostics.launch_reference(environment)["missing_diagnostics"]
        assert not list(directory.glob("*.raw"))
        return
    archives = list(directory.glob("*.log"))
    assert len(archives) == 1
    archive = archives[0].read_text(encoding="utf-8")
    assert '"returncode"' not in archive and '"launch_error"' not in archive
    assert credential not in archive
    assert len(list(directory.glob("*.pending"))) == 1
    if outcome != "launch":
        assert "child-error" in archive and "complete" in archive
        assert any(credential.encode() in path.read_bytes() for path in directory.glob("*.raw"))


@pytest.mark.skipif(os.name != "nt", reason="native Windows retained Job guardian")
@pytest.mark.parametrize("inherit_output", [True, False])
@pytest.mark.timeout(30)
def test_durable_guardian_keeps_existing_descendant_pipe_completion(
    guardian: Any, tmp_path: Path, inherit_output: bool,
) -> None:
    runtime_path = (
        Path(__file__).resolve().parents[3]
        / "libs" / "versioned-runtime" / "versioned_runtime.py"
    )
    runtime = guardian._load_resource("pipe_contract_runtime", runtime_path)
    root = tmp_path / "runtime"
    root.mkdir()
    slot = runtime.slot(root, "probe")
    helper = SCRIPTS / "installer_diagnostics.py"
    diagnostics = guardian._load_resource("pipe_contract_diagnostics", helper)
    environment = diagnostics.reserve_launch(dict(os.environ, USERPROFILE=str(tmp_path)))
    redirection = (
        ",stdout=sys.stdout,stderr=sys.stderr" if inherit_output else
        ",stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL"
    )
    script = (
        "import subprocess,sys;"
        "subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(60)'],"
        f"creationflags=subprocess.CREATE_NO_WINDOW{redirection});"
        "print('root complete',flush=True)"
    )
    with runtime.construction_lease(root, "probe") as descriptor:
        def run() -> Any:
            return guardian.run_owned(
                [sys.executable, "-I", "-c", script], cwd=slot, env=environment, timeout=3,
                procutil=agent_procutil, construction_fd=descriptor,
                runtime_root=root, runtime_version="probe", runtime_path=runtime_path,
                diagnostics_path=helper,
            )

        if inherit_output:
            with pytest.raises(guardian.ChildTimeoutExpired) as caught:
                run()
            assert caught.value.stdout == "root complete\n"
        else:
            assert run().stdout == "root complete\n"
        with runtime._cleanup_lease(root, "probe", allow_construction_owner=True) as admitted:
            assert admitted


@pytest.mark.skipif(os.name != "nt", reason="native Windows owned capture")
@pytest.mark.parametrize("sink", ["failed-file", "pipe"])
def test_capture_write_failure_still_drains_both_native_streams(guardian: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sink: str) -> None:
    from agent_procutil import streaming

    archive = (tmp_path / "stdout.raw").open("w+b", buffering=0)
    pipe = os.pipe() if sink == "pipe" else None
    original = os.write
    writes = []

    def fail_archive(descriptor: int, data: bytes) -> int:
        if descriptor != archive.fileno():
            return original(descriptor, data)
        writes.append(True)
        if len(writes) == 1:
            return original(descriptor, data[:1])
        raise OSError("capture storage unavailable")

    monkeypatch.setattr(cast(Any, streaming).os, "write", fail_archive)
    process, job = agent_procutil.spawn_sync_in_kill_on_close_job(
        [sys.executable, "-I", "-c",
         "import os;os.write(1,b'o'*300000);os.write(2,b'e'*300000)"],
        require_job=True, allow_breakaway=False, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        **no_window_kwargs(),
    )
    try:
        output = guardian._output_capture(
            process, agent_procutil, None,
            archive_fds={"stdout": archive.fileno() if pipe is None else pipe[1]},
        )
        output.wait(10, wait_for_eof=False)
        cast(agent_procutil.JobHandle, job).terminate_and_wait(10)
        process.wait(timeout=10)
        stdout, stderr = guardian._settle_output(output.finish, 10, capture=output)
        assert process.returncode == 0
        assert stdout == b"o" * 300000 and stderr == b"e" * 300000
        assert output.archive_failed and len(writes) == (2 if pipe is None else 0)
    finally:
        if cast(agent_procutil.JobHandle, job).handle is not None:
            cast(agent_procutil.JobHandle, job).terminate_and_wait(10)
        process.wait(timeout=10)
        archive.close()
        if pipe is not None:
            for descriptor in pipe:
                os.close(descriptor)


@pytest.mark.skipif(os.name != "nt", reason="native Windows owned capture")
@pytest.mark.parametrize("extra", [0, 2 * 1024 * 1024])
@pytest.mark.timeout(30)
def test_capture_archives_before_child_exit_and_before_caller_waits(
    guardian: Any, tmp_path: Path, extra: int,
) -> None:
    release = tmp_path / "release"
    archive = tmp_path / "stdout.raw"
    expected = b"durable-before-exit\x00\xff" + b"x" * extra
    script = (
        "import os,sys,time;from pathlib import Path\n"
        f"os.write(1,b'durable-before-exit\\x00\\xff'+b'x'*{extra})\n"
        "deadline=time.monotonic()+10\n"
        "while not Path(sys.argv[1]).exists():\n"
        " if time.monotonic()>deadline: raise SystemExit(91)\n"
        " time.sleep(.01)\n"
    )
    with archive.open("w+b", buffering=0) as destination:
        process, job = agent_procutil.spawn_sync_in_kill_on_close_job(
            [sys.executable, "-I", "-c", script, str(release)],
            require_job=True, allow_breakaway=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window_kwargs(),
        )
        try:
            capture = guardian._output_capture(
                process, agent_procutil, None, archive_fds={"stdout": destination.fileno()},
            )
            deadline = time.monotonic() + 5
            while archive.read_bytes() != expected:
                assert process.poll() is None
                assert time.monotonic() < deadline, "archive did not retain the live child bytes"
                time.sleep(.01)
            assert process.poll() is None
            release.touch()
            capture.wait(10, wait_for_eof=False)
            cast(agent_procutil.JobHandle, job).terminate_and_wait(10)
            process.wait(timeout=10)
            stdout, stderr = guardian._settle_output(capture.finish, 10, capture=capture)
            assert stdout == expected and stderr == b""
            assert not capture.archive_failed
        finally:
            release.touch()
            if cast(agent_procutil.JobHandle, job).handle is not None:
                cast(agent_procutil.JobHandle, job).terminate_and_wait(10)
            process.wait(timeout=10)


@pytest.mark.parametrize("archived,size,blocked", [
    (True, 8192, True), (True, 2 * 1024 * 1024, True),
    (False, 2 * 1024 * 1024, True), (False, 2 * 1024 * 1024, False),
])
@pytest.mark.timeout(30)
def test_archive_draining_is_independent_of_unread_presentation(
    guardian: Any, tmp_path: Path, archived: bool, size: int, blocked: bool,
) -> None:
    set_blocking: Callable[[int, bool], None] | None = getattr(os, "set_blocking", None)
    if set_blocking is None:
        pytest.skip("pipe blocking controls require Python 3.12 or newer on Windows")
    reader, writer = os.pipe()
    try:
        set_blocking(writer, False)
        while True:
            try:
                os.write(writer, b"x" * 1024)
            except BlockingIOError:
                break
        set_blocking(writer, True)
        with ExitStack() as stack:
            paths = {name: tmp_path / f"{name}.raw" for name in ("stdout", "stderr")}
            archives = {
                name: stack.enter_context(path.open("w+b", buffering=0))
                for name, path in paths.items()
            }
            live = stack.enter_context((tmp_path / "live").open("w+b", buffering=0))
            process, job = agent_procutil.spawn_sync_in_kill_on_close_job(
                [sys.executable, "-I", "-c",
                 f"import os;os.write(1,b'o'*{size});os.write(2,b'e'*{size});raise SystemExit(37)"],
                require_job=os.name == "nt", allow_breakaway=False, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window_kwargs(),
            )
            capture = guardian._output_capture(
                process, agent_procutil, None,
                output_fds={name: writer if blocked else live.fileno() for name in ("stdout", "stderr")},
                archive_fds={name: stream.fileno() for name, stream in archives.items()}
                if archived else None,
            )
            try:
                if archived or not blocked:
                    capture.wait(5, deadline=time.monotonic() + 10)
                else:
                    with pytest.raises(subprocess.TimeoutExpired):
                        capture.wait(5, deadline=time.monotonic() + 10)
            finally:
                if job is not None:
                    job.terminate_and_wait(5)
                elif process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                stdout, stderr = capture.finish(5)
            if archived or not blocked:
                assert process.returncode == 37
                assert stdout == b"o" * size and stderr == b"e" * size
            if archived:
                assert paths["stdout"].read_bytes() == b"o" * size
                assert paths["stderr"].read_bytes() == b"e" * size
                assert not capture.archive_failed
                assert capture.presentation_incomplete
            elif not blocked:
                live.seek(0)
                presented = live.read()
                assert presented.count(b"o") == presented.count(b"e") == size
                assert not capture.presentation_incomplete
            assert all(relay.process.poll() is not None for relay in capture._relays.values())
    finally:
        os.close(writer)
        os.close(reader)


@pytest.mark.parametrize("mode,failure", [
    ("archive", "launch"), ("best-effort", "launch"), ("strict", "launch"),
    ("archive", "thread-construction"), ("strict", "thread-construction"),
    ("archive", "started-thread"), ("strict", "started-thread"),
])
@pytest.mark.timeout(30)
def test_optional_relay_startup_failure_preserves_capture_and_native_exit(
    guardian: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, failure: str,
) -> None:
    original_spawn = agent_procutil.spawn_sync_in_kill_on_close_job
    original_init = threading.Thread.__init__
    original_start = threading.Thread.start
    primary = (
        agent_procutil.SyncProcessLaunchError(OSError("relay launch refused"))
        if failure == "launch" else RuntimeError("relay thread setup failed")
    )
    relays: list[tuple[subprocess.Popen[bytes], Any]] = []
    calls = 0
    receipts = 0
    size = 300000
    marker = tmp_path / "native-runs"
    with ExitStack() as stack:
        archives = {
            name: stack.enter_context((tmp_path / f"{name}.raw").open("w+b", buffering=0))
            for name in ("stdout", "stderr")
        }
        live = stack.enter_context((tmp_path / "live").open("w+b", buffering=0))
        process, job = original_spawn(
            [sys.executable, "-I", "-c",
             "import os,sys;from pathlib import Path;"
             "p=Path(sys.argv[1]);p.open('ab').write(b'run\\n');"
             f"os.write(1,b'o'*{size});os.write(2,b'e'*{size});raise SystemExit(37)",
             str(marker)],
            require_job=os.name == "nt", allow_breakaway=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window_kwargs(),
        )

        def spawn(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            if calls == 2 and failure == "launch":
                raise primary
            result = original_spawn(*args, **kwargs)
            relays.append(result)
            return result

        def initialize(worker: threading.Thread, *args: Any, **kwargs: Any) -> None:
            nonlocal receipts
            if str(kwargs.get("name", "")).startswith("output-receipt-"):
                receipts += 1
                if receipts == 2 and failure == "thread-construction":
                    raise primary
            original_init(worker, *args, **kwargs)

        def start(worker: threading.Thread) -> None:
            original_start(worker)
            if (
                failure == "started-thread" and worker.name.startswith("output-receipt-")
                and len(relays) == 2
            ):
                raise primary

        monkeypatch.setattr(agent_procutil, "spawn_sync_in_kill_on_close_job", spawn)
        monkeypatch.setattr(threading.Thread, "__init__", initialize)
        monkeypatch.setattr(threading.Thread, "start", start)
        capture = None
        try:
            options = {
                "output_fds": {name: live.fileno() for name in ("stdout", "stderr")},
                "archive_fds": {name: stream.fileno() for name, stream in archives.items()}
                if mode == "archive" else None,
                "presentation_best_effort": mode == "best-effort",
            }
            if mode == "strict":
                with pytest.raises(type(primary)) as caught:
                    guardian._output_capture(process, agent_procutil, None, **options)
                assert caught.value is primary
            else:
                capture = guardian._output_capture(process, agent_procutil, None, **options)
                capture.wait(5, deadline=time.monotonic() + 10)
                stdout, stderr = capture.finish(5)
                assert process.returncode == 37
                assert stdout == b"o" * size and stderr == b"e" * size
                assert capture.presentation_incomplete and not capture.archive_failed
                assert marker.read_bytes() == b"run\n"
                if mode == "archive":
                    for name, expected in (("stdout", b"o"), ("stderr", b"e")):
                        archives[name].seek(0)
                        assert archives[name].read() == expected * size
            assert calls == 2
            assert all(child.poll() is not None for child, _ in relays)
        finally:
            if job is not None:
                job.terminate_and_wait(5)
            elif process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            if capture is not None:
                capture.finish(5)
            for child, relay_job in relays:
                if relay_job is not None and relay_job.handle is not None:
                    relay_job.terminate_and_wait(5)
                elif child.poll() is None:
                    child.kill()
                child.wait(timeout=5)


@pytest.mark.parametrize("failure", ["os-error", "runtime-error", "cleanup", "cancel"])
@pytest.mark.timeout(30)
def test_archived_relay_startup_preserves_unverified_failure_custody(
    guardian: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    from agent_procutil._output_relay import OutputRelayCleanupError

    retained = SimpleNamespace()
    errors = {
        "os-error": OSError("ambiguous native spawn"),
        "runtime-error": RuntimeError("ambiguous native spawn"),
        "cleanup": OutputRelayCleanupError(cast(Any, retained)),
        "cancel": KeyboardInterrupt(),
    }
    primary = errors[failure]
    with (tmp_path / "archive").open("w+b", buffering=0) as archive:
        process, job = agent_procutil.spawn_sync_in_kill_on_close_job(
            [sys.executable, "-I", "-c", "import time;time.sleep(10)"],
            require_job=os.name == "nt", allow_breakaway=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window_kwargs(),
        )

        def refuse(*args: Any, **kwargs: Any) -> NoReturn:
            raise primary

        monkeypatch.setattr(agent_procutil, "spawn_sync_in_kill_on_close_job", refuse)
        try:
            with pytest.raises(type(primary)) as caught:
                guardian._output_capture(
                    process, agent_procutil, None, output_fds={"stdout": archive.fileno()},
                    archive_fds={"stdout": archive.fileno()},
                )
            assert caught.value is primary
            if failure == "cleanup":
                assert cast(Any, caught.value).relay is retained
        finally:
            if job is not None:
                job.terminate_and_wait(5)
            elif process.poll() is None:
                process.kill()
            process.wait(timeout=5)


@pytest.mark.timeout(30)
@pytest.mark.parametrize("failure", ["delivery", "cancel", "retirement"])
def test_archived_capture_late_relay_close_preserves_native_result_or_custody(
    guardian: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    from agent_procutil._output_relay import ByteRelay, OutputRelayCleanupError

    original_close, original_abort = ByteRelay.close, ByteRelay.abort
    primary = KeyboardInterrupt() if failure == "cancel" else OSError("late delivery failure")
    with ExitStack() as stack:
        archives = {
            name: stack.enter_context((tmp_path / f"{name}.raw").open("w+b", buffering=0))
            for name in ("stdout", "stderr")
        }
        live = stack.enter_context((tmp_path / "live").open("w+b", buffering=0))
        process, job = agent_procutil.spawn_sync_in_kill_on_close_job(
            [sys.executable, "-I", "-c",
             "import os;os.write(1,b'out');os.write(2,b'err');raise SystemExit(37)"],
            require_job=os.name == "nt", allow_breakaway=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window_kwargs(),
        )
        capture = guardian._output_capture(
            process, agent_procutil, None, output_fds={"stdout": live.fileno()},
            archive_fds={name: stream.fileno() for name, stream in archives.items()},
        )
        relay = capture._relays["stdout"]
        cleanup = OutputRelayCleanupError(relay)
        try:
            capture.wait(5, deadline=time.monotonic() + 10)
            deadline = time.monotonic() + 5
            while not relay.end():
                assert time.monotonic() < deadline
                time.sleep(.005)

            def close(selected: ByteRelay) -> None:
                selected._errors.append(primary)
                original_close(selected)

            def abort(selected: ByteRelay, timeout: float) -> None:
                original_abort(selected, timeout)
                raise cleanup

            monkeypatch.setattr(ByteRelay, "close", close)
            if failure == "retirement":
                monkeypatch.setattr(ByteRelay, "abort", abort)
            if failure == "delivery":
                assert capture.finish(5) == (b"out", b"err")
                assert capture.presentation_incomplete and not capture.archive_failed
            else:
                expected = cleanup if failure == "retirement" else primary
                with pytest.raises(type(expected)) as caught:
                    capture.finish(5)
                assert caught.value is expected
            assert process.returncode == 37
        finally:
            original_abort(relay, 5)
            if job is not None:
                job.terminate_and_wait(5)
            elif process.poll() is None:
                process.kill()
            process.wait(timeout=5)


@pytest.mark.timeout(30)
def test_archived_relay_ambiguous_thread_start_retains_exact_relay(
    guardian: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_procutil._output_relay import OutputRelayCleanupError

    original_start = threading.Thread.start
    with (tmp_path / "archive").open("w+b", buffering=0) as archive:
        process, job = agent_procutil.spawn_sync_in_kill_on_close_job(
            [sys.executable, "-I", "-c", "import time;time.sleep(10)"],
            require_job=os.name == "nt", allow_breakaway=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window_kwargs(),
        )

        def start(worker: threading.Thread) -> None:
            if worker.name.startswith("output-receipt-"):
                raise RuntimeError("unattested thread start")
            original_start(worker)

        monkeypatch.setattr(threading.Thread, "start", start)
        try:
            with pytest.raises(OutputRelayCleanupError) as caught:
                guardian._output_capture(
                    process, agent_procutil, None, output_fds={"stdout": archive.fileno()},
                    archive_fds={"stdout": archive.fileno()},
                )
            relay = caught.value.relay
            assert relay.process.poll() is not None
            assert not relay._closed and relay._startup_unverified
            assert isinstance(caught.value.__cause__, RuntimeError)
            cast(Any, relay.process.stdin).close()
            cast(Any, relay.process.stderr).close()
        finally:
            if job is not None:
                job.terminate_and_wait(5)
            elif process.poll() is None:
                process.kill()
            process.wait(timeout=5)


@pytest.mark.skipif(os.name != "nt", reason="native retained Windows guardian")
@pytest.mark.timeout(30)
def test_retained_guardian_unread_parent_keeps_large_archives_and_native_exit(
    guardian: Any, tmp_path: Path,
) -> None:
    set_blocking: Callable[[int, bool], None] | None = getattr(os, "set_blocking", None)
    if set_blocking is None:
        pytest.skip("pipe blocking controls require Python 3.12 or newer on Windows")
    runtime_path = Path(__file__).resolve().parents[3] / "libs/versioned-runtime/versioned_runtime.py"
    runtime = guardian._load_resource("unread_archive_runtime", runtime_path)
    root = tmp_path / "runtime"
    root.mkdir()
    slot = runtime.slot(root, "probe")
    helper = SCRIPTS / "installer_diagnostics.py"
    diagnostics = guardian._load_resource("unread_archive_diagnostics", helper)
    environment = diagnostics.reserve_launch(dict(os.environ, USERPROFILE=str(tmp_path)))
    directory = Path(environment["COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY"])
    size = 1024 * 1024
    reader, writer = os.pipe()
    try:
        set_blocking(writer, False)
        while True:
            try:
                os.write(writer, b"x" * 1024)
            except BlockingIOError:
                break
        set_blocking(writer, True)
        with runtime.construction_lease(root, "probe") as descriptor:
            with pytest.raises(subprocess.CalledProcessError) as caught:
                guardian.run_owned(
                    [sys.executable, "-I", "-c",
                     f"import os;os.write(1,b'o'*{size});os.write(2,b'e'*{size});raise SystemExit(37)"],
                    cwd=slot, env=environment, timeout=10,
                    procutil=agent_procutil, construction_fd=descriptor,
                    runtime_root=root, runtime_version="probe", runtime_path=runtime_path,
                    diagnostics_path=helper, output_fds={"stderr": writer},
                )
            assert caught.value.returncode == 37
            assert caught.value.stdout == "o" * size
            assert caught.value.stderr == "e" * size
            assert any(path.read_bytes() == b"o" * size for path in directory.glob("*.stdout.raw"))
            assert any(path.read_bytes() == b"e" * size for path in directory.glob("*.stderr.raw"))
            with runtime._cleanup_lease(root, "probe", allow_construction_owner=True) as admitted:
                assert admitted
    finally:
        os.close(writer)
        os.close(reader)


@pytest.mark.skipif(os.name != "nt", reason="native Windows executable-token limit")
@pytest.mark.timeout(30)
def test_retained_windows_guardian_accepts_a_long_owning_interpreter(guardian: Any, tmp_path: Path) -> None:
    environment = tmp_path / "owner"
    while len(str(environment / "Scripts" / "python.exe")) <= 270:
        environment /= "interpreter-" + "x" * 30
    venv.EnvBuilder(with_pip=False, symlinks=False).create(environment)
    python = environment / "Scripts" / "python.exe"
    repo = Path(__file__).resolve().parents[3]
    code = inspect.getsource(_load) + """
import importlib.util,json,sys
from pathlib import Path
s=importlib.util.spec_from_file_location("long_owner_guardian",sys.argv[1])
guardian=importlib.util.module_from_spec(s);s.loader.exec_module(guardian)
runtime=_load("long_owner_runtime",sys.argv[2])
procutil=_load("long_owner_procutil",sys.argv[3])
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
