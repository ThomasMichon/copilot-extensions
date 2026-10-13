"""Atomic route publication survives sharing conflicts and platform barriers."""

import json
import os
import stat
import pytest

from zdd import routing


@pytest.mark.parametrize("platform,directory_flushes", [("linux", 1), ("darwin", 0)])
def test_directory_barrier_matches_supported_platform(
    tmp_path, monkeypatch, platform, directory_flushes,
):
    from zdd import routing_persistence

    real_fsync = os.fsync
    calls = []

    def fsync(descriptor):
        directory = stat.S_ISDIR(os.fstat(descriptor).st_mode)
        calls.append(directory)
        if directory and platform == "darwin":
            raise OSError("fixture unsupported directory fsync")
        real_fsync(descriptor)

    monkeypatch.setattr(routing_persistence.sys, "platform", platform)
    monkeypatch.setattr(routing_persistence.os, "fsync", fsync)
    path = tmp_path / "active.json"
    routing_persistence.write(path, {"active": "new"})
    assert json.loads(path.read_text()) == {"active": "new"}
    assert calls.count(False) == 1
    assert calls.count(True) == directory_flushes


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
    assert len(calls) == 3
    assert sleeps == [0.05]
    assert routing.read_table(tmp_path)["active"] == "new"
    assert routing.read_table(tmp_path)["publication_history"]["saturated"] is True


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
