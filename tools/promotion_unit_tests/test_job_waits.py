"""Actor-free controls for the selected, single-request native wait adapter."""

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    import plugin_test_job_waits as waits
    import plugin_test_job_native as native_process
else:
    from tools import plugin_test_job_waits as waits
    from tools import plugin_test_job_native as native_process


class Value:
    def __init__(self, value: int = 0) -> None:
        self.value: int | None = value

    def __bool__(self) -> bool:
        return bool(self.value)


class Thread:
    def __init__(self) -> None:
        self.pid = self.tid = self.suspend_count = 0
        self.created = SimpleNamespace(dwLowDateTime=0, dwHighDateTime=0)


class Nodes:
    def __mul__(self, count: int) -> Any:
        return lambda: [SimpleNamespace(kind=0, status=0) for _ in range(count)]


class Native:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.entries = 1
        self.walked = 0
        self.pid = 17
        self.birth = 55
        self.error = 997
        self.capture_error = 0
        self.thread_close_ok = True
        self.session_close_ok = True
        self.now = 10.0
        self.capture_expires = False
        self.selected_tid = 0
        self.callback_on_close = False
        self.probe: waits.WindowsWaitProbe

    def WaitForSingleObject(self, handle: int, timeout: int) -> int:
        assert handle in (117, 88) and timeout == 0
        return 258

    def PssCaptureSnapshot(self, handle: int, flags: int, context: int, output: Any) -> int:
        assert (handle, flags, context) == (117, 0x80, 0)
        self.events.append("snapshot")
        if self.capture_expires:
            self.now = 11
        if not self.capture_error:
            output._obj.value = 90
        return self.capture_error

    def PssWalkMarkerCreate(self, allocation: Any, output: Any) -> int:
        assert allocation is None
        self.events.append("marker")
        output._obj.value = 91
        return 0

    def PssWalkSnapshot(
        self, snapshot: Any, kind: int, marker: Any, output: Any, size: int,
    ) -> int:
        assert snapshot.value == 90 and marker.value == 91 and kind == 3
        assert size == 64
        if self.walked == self.entries:
            return 259
        self.walked += 1
        entry = output._obj
        entry.pid, entry.tid = self.pid, 100 + self.walked
        entry.created.dwLowDateTime = 55
        entry.suspend_count = 1
        return 0

    def PssWalkMarkerFree(self, marker: Any) -> int:
        assert marker.value == 91
        self.events.append("marker-free")
        return 0

    def GetCurrentProcess(self) -> int:
        return -1

    def PssFreeSnapshot(self, current: int, snapshot: Any) -> int:
        assert current == -1 and snapshot.value == 90
        self.events.append("snapshot-free")
        return 0

    def OpenThread(self, access: int, inherit: bool, tid: int) -> int:
        assert access == 0x100000 | 0x800 and inherit is False
        self.selected_tid = tid
        self.events.append("thread-open")
        return 88

    def GetThreadTimes(self, handle: int, *values: Any) -> bool:
        assert handle == 88
        values[0]._obj.dwLowDateTime = self.birth
        return True

    def GetProcessIdOfThread(self, handle: int) -> int:
        assert handle == 88
        return 17

    def OpenThreadWaitChainSession(self, flags: int, callback: Any) -> int:
        assert flags == 1 and callable(callback)
        self.events.append("session-open")
        return 92

    def GetThreadWaitChain(
        self, session: int, context: int, flags: int, tid: int,
        count: Any, nodes: Any, cycle: Any,
    ) -> bool:
        assert (session, context, flags, tid) == (92, 0, 0, self.selected_tid)
        assert count._obj.value == 16 and len(nodes) == 16
        self.events.append("query")
        return False

    def CloseThreadWaitChainSession(self, session: int) -> None:
        assert session == 92
        request = self.probe.request
        assert request is not None
        assert request.callback is not None and request.buffers is not None and request.thread == 88
        self.events.append("session-close")
        if not self.session_close_ok:
            raise OSError("fixture session closure unconfirmed")
        if self.callback_on_close:
            request.receive(92, 0, 5, None, None, None)

    def CloseHandle(self, handle: int) -> bool:
        assert handle == 88
        self.events.append("thread-close")
        return self.thread_close_ok


@pytest.fixture
def native(monkeypatch: pytest.MonkeyPatch) -> Native:
    result = Native()
    probe = waits.WindowsWaitProbe.__new__(waits.WindowsWaitProbe)
    probe.kernel = probe.advapi = result
    probe.ct = SimpleNamespace(
        byref=lambda value: SimpleNamespace(_obj=value), sizeof=lambda value: 64,
        get_last_error=lambda: result.error,
    )
    probe.wt = SimpleNamespace(
        HANDLE=Value, DWORD=Value, BOOL=Value,
        FILETIME=lambda: SimpleNamespace(dwLowDateTime=0, dwHighDateTime=0),
    )
    probe.Thread, probe.Node = Thread, Nodes()
    probe.Callback = lambda callback: callback
    probe.request = None
    result.probe = probe
    monkeypatch.setattr(waits, "time", SimpleNamespace(monotonic=lambda: result.now))
    return result


def test_async_request_retains_buffers_until_close_and_never_issues_a_second_query(native: Native) -> None:
    probe = native.probe
    assert probe.poll((17, 117, 123, 0), 11) is None and probe.pending
    assert probe.poll((18, 118, 124, 0), 11) is None
    assert native.events.count("query") == 1
    request = probe.request
    assert request is not None
    request.receive(92, 0, 5, None, None, None)
    row = probe.poll(None, 11)
    assert row is not None and row["wait_status"] == 5 and not row["cancelled_pending"]
    assert not probe.pending
    assert native.events.index("snapshot-free") < native.events.index("query")
    assert native.events[-2:] == ["session-close", "thread-close"]


def test_deadline_after_capture_only_releases_owned_snapshot(native: Native) -> None:
    native.capture_expires = True
    row = native.probe.poll((17, 117, 123, 0), 11)
    assert row is not None and row["budget_exhausted"]
    assert native.events == ["snapshot", "snapshot-free"]
    assert not native.probe.pending


def test_callback_delivered_during_cancellation_settles_before_buffers_are_released(native: Native) -> None:
    native.probe.poll((17, 117, 123, 0), 11)
    native.callback_on_close = True
    row = native.probe.finish()
    assert row is not None and row["callback_received"] and row["query_issued"]
    assert row["wait_status"] == 5 and not row["cancelled_pending"]
    assert not native.probe.pending


def test_query_limited_capture_denial_is_numeric_without_access_fallback(native: Native) -> None:
    native.capture_error = 5
    row = native.probe.poll((17, 117, 123, 0), 11)
    assert row is not None and row["error_code"] == 5 and row["stage"] == 2
    assert native.events == ["snapshot"] and not native.probe.pending


@pytest.mark.parametrize("mismatch", ["process", "thread-birth"])
def test_identity_change_never_queries_a_foreign_or_reused_thread(native: Native, mismatch: str) -> None:
    if mismatch == "process":
        native.pid = 18
    else:
        native.birth = 56
    row = native.probe.poll((17, 117, 123, 0), 11)
    assert row is not None and row["error_code"] == 13
    assert "query" not in native.events and not native.probe.pending
    assert ("thread-close" in native.events) is (mismatch == "thread-birth")


def test_thread_walk_and_cursor_are_bounded_and_pending_cancel_is_explicit(native: Native) -> None:
    native.entries = 100
    assert native.probe.poll((17, 117, 123, 31), 11) is None
    row = native.probe.finish()
    assert row is not None and row["threads_truncated"] and row["threads_observed"] == 16
    assert row["suspended_threads"] == 16 and row["cancelled_pending"]
    assert native.walked == 17 and native.selected_tid == 116


def test_callback_emits_only_bounded_numeric_categories_and_omits_foreign_thread(native: Native) -> None:
    native.probe.poll((17, 117, 123, 0), 11)
    request = native.probe.request
    assert request is not None
    own = SimpleNamespace(kind=8, status=3, detail=SimpleNamespace(thread=SimpleNamespace(pid=17)))
    foreign = SimpleNamespace(kind=8, status=4, detail=SimpleNamespace(thread=SimpleNamespace(pid=18)))
    lock = SimpleNamespace(kind=2, status=4, detail=SimpleNamespace(name="not-for-output"))
    nodes = [foreign, own, lock] + [SimpleNamespace(kind=999, status=999)] * 13
    request.receive(92, 0, 234, [100], nodes, [False])
    row = native.probe.finish()
    assert row is not None and row["foreign_nodes_omitted"] == 1 and row["nodes_truncated"]
    assert row["kind_8"] == 1 and row["kind_2"] == 1 and row["state_4"] == 1
    assert row["unknown_category"] and not row["cycle"]
    assert all(type(value) in (int, bool, type(None)) for value in row.values())
    assert not any(key in row for key in ("name", "tid", "address", "nodes"))


@pytest.mark.parametrize("failure", ["session", "thread"])
def test_unconfirmed_closure_retains_exact_request_and_native_witnesses(native: Native, failure: str) -> None:
    native.probe.poll((17, 117, 123, 0), 11)
    request = native.probe.request
    native.session_close_ok = failure != "session"
    native.thread_close_ok = failure != "thread"
    with pytest.raises(OSError, match="closure"):
        native.probe.finish()
    assert native.probe.request is request and request is not None and request.thread == 88
    if failure == "session":
        assert request.session == 92 and request.callback is not None and request.buffers is not None
    native.session_close_ok = native.thread_close_ok = True
    native.probe.finish()
    assert not native.probe.pending


def test_native_member_open_declares_metadata_query_rights_without_fallback() -> None:
    calls: list[tuple[int, bool, int]] = []

    def open_process(access: int, inherit: bool, pid: int) -> int:
        calls.append((access, inherit, pid))
        return 0

    api = native_process.WindowsJobAPI.__new__(native_process.WindowsJobAPI)
    api.kernel = SimpleNamespace(OpenProcess=open_process)
    assert api.open(17) == 0
    assert calls == [(0x400 | 0x100000, False, 17)]
