"""Injected creation-time Job ownership; no native child is launched."""

import ctypes
import subprocess
import sys
from types import SimpleNamespace

import pytest

from agent_procutil import _windows_caller_owned as native
from agent_procutil.owned_process import OwnedCleanupError


@pytest.fixture
def launch(monkeypatch):
    clock = [100.0]
    calls, streams, attributes, jobs = [], [], {}, []
    original_fdopen = native.os.fdopen
    monkeypatch.setattr(native, "os", SimpleNamespace(
        **(vars(native.os) | {"O_BINARY": 0, "set_handle_inheritable": lambda *a: None}),
    ))
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(get_osfhandle=lambda fd: fd))
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(ctypes, "WinError", lambda code: OSError(code, "injected native failure"), raising=False)

    class Job:
        closed = False

        def __init__(self, handle):
            self.handle = handle
            jobs.append(self)

        def terminate_and_wait(self, timeout):
            calls.append(("terminate", timeout))
            assert timeout > 0
            if kernel.cancel_cleanup is not None:
                raise kernel.cancel_cleanup
            self.closed = True
            clock[0] += 1

        def close(self):
            self.closed = True

        def try_terminate_and_confirm_empty(self):
            calls.append("zero-job")
            self.closed = True
            return True

    class Kernel:
        interrupt = False
        deny_attribute = False
        cancel_cleanup = None

        def CreateJobObjectW(self, *args):
            calls.append("job")
            return 501

        def SetInformationJobObject(self, handle, kind, value, size):
            assert ctypes.cast(value, ctypes.POINTER(
                native._build_job_extended_limit_info(),
            )).contents.BasicLimitInformation.LimitFlags == 0x2000
            return True

        def InitializeProcThreadAttributeList(self, buffer, count, flags, size):
            assert count == 2
            ctypes.cast(size, ctypes.POINTER(ctypes.c_size_t)).contents.value = 128
            return True

        def UpdateProcThreadAttribute(self, buffer, flags, key, value, size, *args):
            attributes[key] = (value, size)
            return not self.deny_attribute

        def DeleteProcThreadAttributeList(self, buffer):
            assert ctypes.cast(attributes[0x2000D][0], ctypes.POINTER(
                native.wintypes.HANDLE,
            ))[0] == 501 if 0x2000D in attributes else True
            calls.append("delete-attributes")

        def CreateProcessW(self, app, command, sa, ta, inherit, flags, env, cwd, startup, info):
            assert inherit and flags & 0x80000 and flags & 4 and flags & 0x8000000
            assert ctypes.cast(attributes[0x2000D][0], ctypes.POINTER(
                native.wintypes.HANDLE,
            ))[0] == 501
            assert attributes[0x20002][1] == ctypes.sizeof(native.wintypes.HANDLE) * 3
            calls.append(("create", env[:] if env is not None else None, cwd))
            process = ctypes.cast(info, ctypes.POINTER(native._ProcessInfo)).contents
            process.hProcess, process.hThread, process.dwProcessId = 601, 602, 603
            return True

        def ResumeThread(self, handle):
            if self.interrupt:
                clock[0] = 110
                raise KeyboardInterrupt("creation interrupted")
            calls.append("resume")
            return 0

        def CloseHandle(self, handle):
            calls.append(("close", handle))
            return True

        def WaitForSingleObject(self, handle, timeout):
            calls.append(("wait", timeout))
            return 0

        def GetExitCodeProcess(self, handle, code):
            ctypes.cast(code, ctypes.POINTER(native.wintypes.DWORD)).contents.value = 0
            return True

    kernel = Kernel()

    def fdopen(*args, **kwargs):
        stream = original_fdopen(*args, **kwargs)
        streams.append(stream)
        return stream

    monkeypatch.setattr(native, "_api", lambda: kernel)
    monkeypatch.setattr(native, "JobHandle", Job)
    monkeypatch.setattr(native.os, "fdopen", fdopen)
    monkeypatch.setattr(native.os, "set_handle_inheritable", lambda *a: None)
    # The production module's clock is injected without changing the shared time module.
    monkeypatch.setattr(native, "time", SimpleNamespace(monotonic=lambda: clock[0]), raising=False)
    yield SimpleNamespace(clock=clock, calls=calls, streams=streams, jobs=jobs, kernel=kernel)
    for stream in streams:
        if not stream.closed:
            stream.close()


@pytest.mark.parametrize("deadline", [float("nan"), float("inf"), True, "110"])
def test_invalid_observation_deadline_refuses_before_native_resources(launch, deadline):
    with pytest.raises(ValueError):
        native.spawn(["git"], cleanup_deadline=deadline, stdin=subprocess.DEVNULL,
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert launch.calls == []


def test_expired_observation_deadline_starts_nothing(launch):
    with pytest.raises(subprocess.TimeoutExpired):
        native.spawn(["git"], cleanup_deadline=99, stdin=subprocess.DEVNULL,
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert launch.calls == []


@pytest.mark.parametrize("environment", [{}, {"Z": "é", "A": "值"}])
def test_atomic_attributes_keep_storage_and_exact_environment_cwd(launch, environment):
    process, job = native.spawn(
        ["git", "status"], cleanup_deadline=110, cwd="C:\\fixture\\café",
        env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    created = next(event for event in launch.calls if isinstance(event, tuple) and event[0] == "create")
    assert created[1] == native._environment_block(environment)[:]
    assert created[2] == "C:\\fixture\\café"
    assert launch.calls.index("job") < launch.calls.index(created) < launch.calls.index("resume")
    assert launch.calls.index("delete-attributes") > launch.calls.index("resume")
    assert process.stdout is launch.streams[0] and not process.stdout.closed
    job.close()
    process.close()


def test_attribute_capability_failure_has_no_create(launch):
    launch.kernel.deny_attribute = True
    with pytest.raises(OSError):
        native.spawn(["git"], cleanup_deadline=110, stdin=subprocess.DEVNULL,
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert not any(isinstance(event, tuple) and event[0] == "create" for event in launch.calls)


def test_post_create_interruption_at_D_retains_exact_streams_job_and_original_cause(launch):
    launch.kernel.interrupt = True
    error = None
    try:
        native.spawn(["git"], cleanup_deadline=110, stdin=subprocess.DEVNULL,
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except BaseException as captured:
        error = captured
    assert isinstance(error, OwnedCleanupError)
    assert error.job is launch.jobs[0] and not error.job.closed
    assert error.process._handle == 601
    assert error.process.stdout is launch.streams[0] and not error.process.stdout.closed
    assert error.process.stderr is launch.streams[1] and not error.process.stderr.closed
    assert isinstance(error.__cause__, KeyboardInterrupt)
    assert error.operation_error is error.__cause__
    assert not any(isinstance(event, tuple) and event[0] in {"wait", "terminate"} for event in launch.calls)


def test_no_opt_in_keeps_legacy_failure_cleanup_allowances(launch):
    launch.kernel.interrupt = True
    with pytest.raises(KeyboardInterrupt):
        native.spawn(["git"], stdin=subprocess.DEVNULL,
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert ("terminate", 2.0) in launch.calls and ("wait", 2000) in launch.calls
    assert all(stream.closed for stream in launch.streams)


def test_opt_in_failure_uses_remaining_budget_not_fixed_cleanup_allowance(launch):
    launch.kernel.interrupt = True
    with pytest.raises(KeyboardInterrupt):
        native.spawn(["git"], cleanup_deadline=113, stdin=subprocess.DEVNULL,
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert ("terminate", 3) in launch.calls and ("wait", 2000) in launch.calls
    assert all(stream.closed for stream in launch.streams)


@pytest.mark.parametrize("phase", ["attributes", "created"])
def test_command_cutoff_never_spends_reserved_cleanup_on_create_or_resume(launch, monkeypatch, phase):
    method = "UpdateProcThreadAttribute" if phase == "attributes" else "CreateProcessW"
    original = getattr(launch.kernel, method)

    def consume_budget(*args):
        result = original(*args)
        launch.clock[0] = 106
        return result

    monkeypatch.setattr(launch.kernel, method, consume_budget)
    with pytest.raises(subprocess.TimeoutExpired):
        native.spawn(["git"], command_deadline=105, cleanup_deadline=110,
                     stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert "resume" not in launch.calls
    created = any(isinstance(item, tuple) and item[0] == "create" for item in launch.calls)
    assert created is (phase == "created")
    assert launch.jobs[0].closed and all(stream.closed for stream in launch.streams)
    if created:
        assert ("terminate", 4) in launch.calls
        assert ("close", 601) in launch.calls
