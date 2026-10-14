"""Atomic route publication survives sharing conflicts and platform barriers."""

import json
import os
import stat
import sys
import pytest

from zdd import routing


@pytest.mark.parametrize("platform,directory_flushes", [
    pytest.param("linux", 1, marks=pytest.mark.skipif(
        sys.platform != "linux", reason="native Linux directory fsync",
    )),
    ("darwin", 0),
])
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
    from zdd import routing_persistence

    path = tmp_path / "active.json"
    old = {"active": {"bind": "127.0.0.1", "port": 9281, "pid": 111}}
    new = {"active": {"bind": "127.0.0.1", "port": 9290, "pid": 222}}
    path.write_text(json.dumps(old), encoding="utf-8")
    replacement = "windows_replace" if sys.platform == "win32" else "os.replace"
    real_replace = (
        routing_persistence.windows_replace if sys.platform == "win32"
        else routing_persistence.os.replace
    )
    calls = []
    sleeps = []

    def replace(source, target):
        calls.append(target)
        if len(calls) == 1:
            error = PermissionError("sharing conflict")
            error.winerror = winerror
            raise error
        real_replace(source, target)

    monkeypatch.setattr(f"zdd.routing_persistence.{replacement}", replace)
    monkeypatch.setattr(routing.time, "sleep", sleeps.append)
    routing._atomic_write(path, new)
    assert len(calls) == 3
    assert sleeps == [0.05]
    assert routing.read_table(tmp_path)["active"] == new["active"]
    history = routing.read_table(tmp_path)["publication_history"]
    assert not history["saturated"]
    assert [entry["pid"] for entry in history["entries"]] == [111, 222]


@pytest.mark.parametrize("winerror,attempts", [(5, 20), (87, 1), (None, 1)])
def test_permanent_failure_propagates_and_preserves_old_route(
    tmp_path, monkeypatch, winerror, attempts,
):
    path = tmp_path / "active.json"
    old = {"active": {"bind": "127.0.0.1", "port": 9281, "pid": 111}}
    new = {"active": {"bind": "127.0.0.1", "port": 9290, "pid": 222}}
    path.write_text(json.dumps(old), encoding="utf-8")
    calls = []
    error = PermissionError("permanent failure")
    if winerror is not None:
        error.winerror = winerror

    def replace(source, target):
        calls.append(target)
        raise error

    replacement = "windows_replace" if sys.platform == "win32" else "os.replace"
    monkeypatch.setattr(f"zdd.routing_persistence.{replacement}", replace)
    monkeypatch.setattr(routing.time, "sleep", lambda _: None)
    with pytest.raises(PermissionError, match="permanent failure"):
        routing._atomic_write(path, new)
    assert len(calls) == attempts
    assert routing.read_table(tmp_path) == old
