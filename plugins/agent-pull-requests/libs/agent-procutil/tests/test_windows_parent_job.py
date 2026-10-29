"""Pure controls for exact-parent, strict-Job lifetime transfer ordering."""

from __future__ import annotations

import ctypes
import os
from types import SimpleNamespace

import pytest

from agent_procutil import _windows_parent_job as owner, _job_scopes


class Kernel:
    def __init__(self, fail: str = "", *, states: tuple[int, ...] = (0x102, 0x102)) -> None:
        self.fail = fail
        self.calls: list[tuple] = []
        self.states = iter(states)
        self.limits = None

    def result(self, name: str, *args: object) -> int:
        self.calls.append((name, *args))
        return 0 if self.fail == name else 1

    def OpenProcess(self, rights: int, inherit: bool, pid: int) -> int:
        return 101 if self.result("open-parent", rights, inherit, pid) else 0

    def GetCurrentProcess(self) -> int:
        return -1

    def WaitForSingleObject(self, handle: int, timeout: int) -> int:
        self.calls.append(("wait-parent", handle, timeout))
        return next(self.states)

    def CreateJobObjectW(self, security: object, name: object) -> int:
        return 202 if self.result("create-job", security, name) else 0

    def SetInformationJobObject(self, handle: int, kind: int, limits: object, size: int) -> int:
        self.limits = limits._obj
        return self.result("set-limits", handle, kind, size)

    def DuplicateHandle(
        self, source: int, handle: object, target: int, output: object,
        rights: int, inherit: bool, options: int,
    ) -> int:
        name = "reclaim-remote" if options & 1 else "duplicate-parent"
        result = self.result(name, source, handle, target, rights, inherit, options)
        if result:
            output._obj.value = 404 if options & 1 else 303
        return result

    def AssignProcessToJobObject(self, job: int, process: int) -> int:
        return self.result("assign-self", job, process)

    def CloseHandle(self, handle: object) -> int:
        value = handle.value if hasattr(handle, "value") else handle
        return self.result(f"close-{value}", value)


@pytest.fixture
def native(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    kernel = Kernel()
    monkeypatch.setattr(owner, "_api", lambda: kernel)
    monkeypatch.setattr(owner, "_is_windows", lambda: True)
    monkeypatch.setattr(owner, "_transferred", False)
    monkeypatch.setattr(owner.threading, "active_count", lambda: 1)
    monkeypatch.setattr(owner, "_windows_birth", lambda handle: "windows:12" if handle == 101 else "windows:34")
    monkeypatch.setattr(ctypes, "WinError", lambda *args: OSError("native API failed"), raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(_job_scopes, "new_name", lambda: "Local\\ce-owned-job-" + "0" * 32)
    monkeypatch.setattr(_job_scopes, "publish_current", lambda name: None)
    return SimpleNamespace(kernel=kernel, pid=os.getppid())


def transfer(native: SimpleNamespace) -> owner.ParentJob:
    return owner.transfer_lifetime_to_parent(parent_pid=native.pid, parent_birth="windows:12")


def test_parent_alone_retains_the_strict_job_before_return(native: SimpleNamespace) -> None:
    receipt = transfer(native)
    assert receipt == owner.ParentJob(native.pid, "windows:12", os.getpid(), "windows:34", 303)
    assert native.kernel.limits.BasicLimitInformation.LimitFlags == 0x2000
    assert native.kernel.calls == [
        ("open-parent", 0x1000 | 0x100000 | 0x40, False, native.pid),
        ("wait-parent", 101, 0),
        ("create-job", None, "Local\\ce-owned-job-" + "0" * 32),
        ("set-limits", 202, 9, ctypes.sizeof(native.kernel.limits)),
        ("duplicate-parent", -1, 202, 101, 0, False, 2),
        ("wait-parent", 101, 0),
        ("assign-self", 202, -1),
        ("close-202", 202),
        ("close-101", 101),
    ]
    with pytest.raises(RuntimeError, match="already transferred"):
        transfer(native)


@pytest.mark.parametrize("pid", [False, 0, -1, 1 << 32, "12", None])
def test_invalid_parent_refuses_before_native_access(native: SimpleNamespace, pid: object) -> None:
    with pytest.raises(ValueError, match="original direct parent"):
        owner.transfer_lifetime_to_parent(parent_pid=pid, parent_birth="windows:12")
    assert native.kernel.calls == []


def test_foreign_or_self_parent_refuses_before_native_access(native: SimpleNamespace) -> None:
    for pid in (os.getpid(), native.pid + 100000):
        with pytest.raises(ValueError, match="original direct parent"):
            owner.transfer_lifetime_to_parent(parent_pid=pid, parent_birth="windows:12")
    assert native.kernel.calls == []


@pytest.mark.parametrize("birth", [
    None, 12, "", "windows:0", "windows:01", "windows:-1", "windows:1x",
    "linux:12", f"windows:{1 << 64}", "windows:123456789012345678901",
])
def test_invalid_birth_refuses_before_native_access(native: SimpleNamespace, birth: object) -> None:
    with pytest.raises(ValueError, match="birth identity"):
        owner.transfer_lifetime_to_parent(parent_pid=native.pid, parent_birth=birth)
    assert native.kernel.calls == []


def test_changed_birth_never_creates_a_job(native: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(owner, "_windows_birth", lambda _handle: "windows:13")
    with pytest.raises(ProcessLookupError, match="birth identity changed"):
        transfer(native)
    assert [call[0] for call in native.kernel.calls] == ["open-parent", "close-101"]


@pytest.mark.parametrize("states", [(0,), (0xFFFFFFFF,), (0x102, 0), (0x102, 0xFFFFFFFF)])
def test_parent_exit_or_unknown_wait_never_assigns_self(
    native: SimpleNamespace, states: tuple[int, ...],
) -> None:
    native.kernel.states = iter(states)
    with pytest.raises((ProcessLookupError, OSError)):
        transfer(native)
    names = [call[0] for call in native.kernel.calls]
    assert "assign-self" not in names
    if len(states) == 2:
        assert names[-4:] == ["reclaim-remote", "close-404", "close-202", "close-101"]
    assert not owner._transferred


@pytest.mark.parametrize("failure", [
    "open-parent", "create-job", "set-limits", "duplicate-parent", "assign-self",
    "close-202", "close-101",
])
def test_native_failure_never_returns_a_receipt(native: SimpleNamespace, failure: str) -> None:
    native.kernel.fail = failure
    with pytest.raises(OSError, match="native API failed"):
        transfer(native)
    names = [call[0] for call in native.kernel.calls]
    if failure == "assign-self":
        assert names[-4:] == ["reclaim-remote", "close-404", "close-202", "close-101"]
    if failure in ("close-202", "close-101"):
        assert "reclaim-remote" not in names
        before = list(native.kernel.calls)
        with pytest.raises(RuntimeError, match="already transferred"):
            transfer(native)
        assert native.kernel.calls == before


def test_failed_duplication_output_is_not_handle_custody(
    native: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed(*args: object) -> int:
        args[3]._obj.value = 999
        return 0

    monkeypatch.setattr(native.kernel, "DuplicateHandle", failed)
    with pytest.raises(OSError, match="native API failed"):
        transfer(native)
    assert [call[0] for call in native.kernel.calls][-2:] == ["close-202", "close-101"]


def test_empty_success_handle_refuses_before_assignment(
    native: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(native.kernel, "DuplicateHandle", lambda *args: 1)
    with pytest.raises(RuntimeError, match="empty parent handle"):
        transfer(native)
    names = [call[0] for call in native.kernel.calls]
    assert "assign-self" not in names
    assert names[-2:] == ["close-202", "close-101"]


def test_native_observer_preserves_the_exact_filetime(monkeypatch: pytest.MonkeyPatch) -> None:
    import test_windows_parent_job_native as native_tests

    monkeypatch.setattr(native_tests, "PinnedProcess", lambda identity: identity)
    assert native_tests.pin_process({"pid": 42, "birth": "windows:130000000000000001"}) == {
        "pid": 42, "birth": 130000000000000001,
    }


@pytest.mark.parametrize("birth", [130000000000000001, "130000000000000001", "linux:12", "windows:0"])
def test_native_observer_rejects_another_identity_format(
    monkeypatch: pytest.MonkeyPatch, birth: object,
) -> None:
    import test_windows_parent_job_native as native_tests

    def forbidden(*args: object) -> None:
        pytest.fail("invalid identity reached native observation")

    monkeypatch.setattr(native_tests, "PinnedProcess", forbidden)
    with pytest.raises(ValueError, match="FILETIME birth"):
        native_tests.pin_process({"pid": 42, "birth": birth})


def test_unavailable_platform_never_uses_native_api(native: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(owner, "_is_windows", lambda: False)
    with pytest.raises(OSError, match="native Windows"):
        transfer(native)
    assert native.kernel.calls == []


@pytest.mark.parametrize("threads", [0, 2])
def test_multithreaded_startup_refuses(
    native: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, threads: int,
) -> None:
    monkeypatch.setattr(owner.threading, "active_count", lambda: threads)
    with pytest.raises(RuntimeError, match="single-threaded startup"):
        transfer(native)
    assert native.kernel.calls == []
