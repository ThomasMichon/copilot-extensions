"""Bounded Windows owner queries, with no native API or process calls."""

from types import SimpleNamespace

import pytest

from agent_worktrees import windows_session_hold as hold


@pytest.fixture
def api(monkeypatch):
    calls = []

    def start(session, flags, key):
        calls.append("start")
        session._obj.value = 7
        return 0

    def register(*args):
        calls.append(("register", args[1], tuple(args[2]), args[3:]))
        return 0

    def query(session, needed, count, entries, reasons):
        calls.append("query")
        needed._obj.value = 1
        if entries is None:
            return 234
        count._obj.value = 1
        entries[0].process.pid = 123
        entries[0].process.started.dwLowDateTime = 456
        return 0

    def end(session):
        calls.append("end")
        return 0

    native = SimpleNamespace(
        RmStartSession=start, RmRegisterResources=register,
        RmGetList=query, RmEndSession=end, calls=calls,
    )
    monkeypatch.setattr(hold, "_api", lambda: native)
    return native


def test_only_exact_file_is_registered_then_session_is_closed(api, tmp_path):
    path = tmp_path / "hold"
    assert hold.owners(path) == {(123, "456")}
    assert api.calls == [
        "start", ("register", 1, (str(path),), (0, None, 0, None)),
        "query", "query", "end",
    ]


@pytest.mark.parametrize("boundary", ["start", "register", "query", "end"])
def test_native_errors_propagate_and_cleanup_is_attempted(api, tmp_path, boundary):
    def fail(*args):
        return 5

    name = {
        "start": "RmStartSession", "register": "RmRegisterResources",
        "query": "RmGetList", "end": "RmEndSession",
    }[boundary]
    setattr(api, name, fail)
    with pytest.raises(OSError):
        hold.owners(tmp_path / "hold")
    if boundary not in {"start", "end"}:
        assert api.calls[-1] == "end"
    if boundary == "start":
        assert api.calls == []


@pytest.mark.parametrize("size", [0, 1025])
def test_owner_count_is_bounded_and_cleanup_preserved(api, tmp_path, size):
    def query(session, needed, *args):
        needed._obj.value = size
        return 234

    api.RmGetList = query
    with pytest.raises(ValueError, match="budget"):
        hold.owners(tmp_path / "hold")
    assert api.calls[-1] == "end"


def test_changing_owner_list_never_retries(api, tmp_path):
    queries = []

    def query(session, needed, *args):
        queries.append(1)
        needed._obj.value = len(queries)
        return 234

    api.RmGetList = query
    with pytest.raises(OSError):
        hold.owners(tmp_path / "hold")
    assert len(queries) == 2
    assert api.calls[-1] == "end"
