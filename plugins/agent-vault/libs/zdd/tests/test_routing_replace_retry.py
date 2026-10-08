"""Atomic route publication survives brief Windows sharing conflicts."""

import pytest

from zdd import routing


@pytest.mark.parametrize("winerror", [5, 32, 33])
def test_transient_windows_sharing_conflict_retries(tmp_path, monkeypatch, winerror):
    path = tmp_path / "active.json"
    path.write_text('{"active":"old"}', encoding="utf-8")
    real_replace = routing.os.replace
    calls = []
    sleeps = []

    def replace(source, target):
        calls.append(target)
        if len(calls) == 1:
            error = PermissionError("sharing conflict")
            error.winerror = winerror
            raise error
        real_replace(source, target)

    monkeypatch.setattr(routing.os, "replace", replace)
    monkeypatch.setattr(routing.time, "sleep", sleeps.append)
    routing._atomic_write(path, {"active": "new"})
    assert len(calls) == 2
    assert sleeps == [0.05]
    assert routing.read_table(tmp_path) == {"active": "new"}


@pytest.mark.parametrize("winerror,attempts", [(5, 20), (87, 1), (None, 1)])
def test_permanent_failure_propagates_and_preserves_old_route(
    tmp_path, monkeypatch, winerror, attempts,
):
    path = tmp_path / "active.json"
    path.write_text('{"active":"old"}', encoding="utf-8")
    calls = []
    error = PermissionError("permanent failure")
    if winerror is not None:
        error.winerror = winerror

    def replace(source, target):
        calls.append(target)
        raise error

    monkeypatch.setattr(routing.os, "replace", replace)
    monkeypatch.setattr(routing.time, "sleep", lambda _: None)
    with pytest.raises(PermissionError, match="permanent failure"):
        routing._atomic_write(path, {"active": "new"})
    assert len(calls) == attempts
    assert routing.read_table(tmp_path) == {"active": "old"}
