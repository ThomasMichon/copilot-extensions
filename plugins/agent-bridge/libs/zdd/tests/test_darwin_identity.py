from __future__ import annotations

import ctypes
import errno
from types import SimpleNamespace

import pytest

from zdd import darwin_identity, diagnostics, process_exit


@pytest.mark.parametrize("failure", [None, "short", "pid", "seconds", "microseconds"])
def test_native_bsd_birth_is_shared_with_diagnostics(monkeypatch, failure):
    class Query:
        def __call__(self, pid, flavor, argument, pointer, size):
            assert flavor == 3 and argument == 0
            info = ctypes.cast(pointer, ctypes.POINTER(darwin_identity._BsdInfo)).contents
            info.pid = pid + 1 if failure == "pid" else pid
            info.start_seconds = 0 if failure == "seconds" else 1001
            info.start_microseconds = 1_000_000 if failure == "microseconds" else 22
            return size - 1 if failure == "short" else size

    monkeypatch.setattr(
        darwin_identity.ctypes, "CDLL", lambda *a, **k: SimpleNamespace(proc_pidinfo=Query()),
    )
    monkeypatch.setattr(diagnostics.platform, "system", lambda: "Darwin")
    expected = "1001:22" if failure is None else None
    assert darwin_identity.birth_time(101) == expected
    assert diagnostics.process_start_time(101) == expected


def test_unavailable_native_bsd_query_is_unknown_not_a_birth(monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError("native query unavailable")

    monkeypatch.setattr(darwin_identity.ctypes, "CDLL", unavailable)
    assert darwin_identity.birth_time(101) is None
    assert darwin_identity.birth_time(0) is None


@pytest.mark.parametrize("state,expected", [
    ("absent", True), ("reused", True), ("zombie", True), ("live", False),
])
def test_original_exit_observation_reads_native_birth_and_state(monkeypatch, state, expected):
    class Query:
        def __call__(self, pid, flavor, argument, pointer, size):
            assert (pid, flavor, argument) == (101, 3, 0)
            if state == "absent":
                ctypes.set_errno(errno.ESRCH)
                return 0
            info = ctypes.cast(pointer, ctypes.POINTER(darwin_identity._BsdInfo)).contents
            info.pid = pid
            info.status = 5 if state == "zombie" else 2
            info.start_seconds = 1002 if state == "reused" else 1001
            info.start_microseconds = 22
            return size

    monkeypatch.setattr(
        darwin_identity.ctypes, "CDLL", lambda *a, **k: SimpleNamespace(proc_pidinfo=Query()),
    )
    monkeypatch.setattr(process_exit, "sys", SimpleNamespace(platform="darwin"))
    assert process_exit.observe_original_exit(101, "1001:22") is expected
    if state == "absent":
        with pytest.raises(ProcessLookupError) as caught:
            darwin_identity.native_identity(101)
        assert caught.value.errno == errno.ESRCH
        assert darwin_identity.birth_time(101) is None


@pytest.mark.parametrize("failure,error", [
    ("denied", PermissionError), ("unknown", OSError), ("short", ValueError),
    ("wrong-pid", ValueError), ("bad-birth", ValueError), ("missing-api", NotImplementedError),
])
def test_unavailable_native_evidence_never_confirms_original_exit(monkeypatch, failure, error):
    class Query:
        def __call__(self, pid, flavor, argument, pointer, size):
            if failure in {"denied", "unknown"}:
                ctypes.set_errno(errno.EPERM if failure == "denied" else 0)
                return 0
            info = ctypes.cast(pointer, ctypes.POINTER(darwin_identity._BsdInfo)).contents
            info.pid = pid + 1 if failure == "wrong-pid" else pid
            info.start_seconds = 0 if failure == "bad-birth" else 1001
            info.start_microseconds = 22
            # A stale ESRCH must never turn a partial response into proof of absence.
            ctypes.set_errno(errno.ESRCH)
            return size - 1 if failure == "short" else size

    library = SimpleNamespace() if failure == "missing-api" else SimpleNamespace(proc_pidinfo=Query())
    monkeypatch.setattr(darwin_identity.ctypes, "CDLL", lambda *a, **k: library)
    monkeypatch.setattr(process_exit, "sys", SimpleNamespace(platform="darwin"))
    with pytest.raises(error):
        process_exit.observe_original_exit(101, "1001:22")
    assert darwin_identity.birth_time(101) is None
