from __future__ import annotations

import ctypes
from types import SimpleNamespace

import pytest

from zdd import darwin_identity, diagnostics


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
