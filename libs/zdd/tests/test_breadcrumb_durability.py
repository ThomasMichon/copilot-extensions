"""Retirement witnesses use the routing writer's crash-durable boundary."""

import os

import pytest

from zdd import breadcrumb, routing_persistence


@pytest.mark.parametrize("platform", ["win32", "linux", "darwin"])
@pytest.mark.parametrize("state", ["started", "flipped", "committed"])
def test_witness_flushes_before_replace_and_confirms_directory(
    tmp_path, monkeypatch, platform, state,
):
    path = breadcrumb.breadcrumb_path(tmp_path)
    events = []
    real_fsync = os.fsync
    real_open = os.open
    real_close = os.close
    directory_fd = -100

    def fsync(fd):
        if fd == directory_fd:
            events.append("directory-flush")
        else:
            real_fsync(fd)
            events.append("file-flush")

    def replace(source, target):
        assert events == ["file-flush"]
        assert not path.exists()
        os.replace(source, target)
        events.append("replace")

    def open_directory(target, flags, *args, **kwargs):
        if target == path.parent:
            events.append("directory-open")
            return directory_fd
        return real_open(target, flags, *args, **kwargs)

    def close(fd):
        if fd == directory_fd:
            events.append("directory-close")
        else:
            real_close(fd)

    monkeypatch.setattr(routing_persistence.sys, "platform", platform)
    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "O_DIRECTORY", 0, raising=False)
    monkeypatch.setattr(os, "open", open_directory)
    monkeypatch.setattr(os, "close", close)
    if platform == "win32":
        monkeypatch.setattr(routing_persistence, "windows_replace", replace)
    else:
        real_replace = os.replace

        def posix_replace(source, target):
            assert events == ["file-flush"]
            real_replace(source, target)
            events.append("replace")

        monkeypatch.setattr(os, "replace", posix_replace)

    record = breadcrumb.write_breadcrumb(tmp_path, state=state, self_retirement=True)
    assert breadcrumb.read_breadcrumb(tmp_path) == record
    assert events == (
        ["file-flush", "replace"] if platform == "win32" else
        ["file-flush", "replace", "directory-open", "directory-flush", "directory-close"]
    )


def test_failed_file_flush_preserves_previous_witness(tmp_path, monkeypatch):
    initial = breadcrumb.write_breadcrumb(tmp_path, state="started", self_retirement=True)

    def fail(fd):
        raise OSError("fixture file flush failed")

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(OSError, match="file flush failed"):
        breadcrumb.write_breadcrumb(tmp_path, state="flipped", self_retirement=True)
    assert breadcrumb.read_breadcrumb(tmp_path) == initial


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_unconfirmed_directory_flush_retains_marked_phase(tmp_path, monkeypatch, platform):
    path = breadcrumb.breadcrumb_path(tmp_path)
    breadcrumb.write_breadcrumb(tmp_path, state="started", self_retirement=True)
    real_fsync = os.fsync
    real_open = os.open
    real_close = os.close
    directory_fd = -100

    def fsync(fd):
        if fd == directory_fd:
            raise OSError("fixture directory flush failed")
        return real_fsync(fd)

    monkeypatch.setattr(routing_persistence.sys, "platform", platform)
    monkeypatch.setattr(os, "O_DIRECTORY", 0, raising=False)
    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(
        os, "open",
        lambda target, flags, *a, **kw: directory_fd if target == path.parent else real_open(target, flags, *a, **kw),
    )
    monkeypatch.setattr(os, "close", lambda fd: None if fd == directory_fd else real_close(fd))
    with pytest.raises(routing_persistence.PublicationCommitUncertain):
        breadcrumb.write_breadcrumb(tmp_path, state="flipped", self_retirement=True)
    record = breadcrumb.read_breadcrumb(tmp_path)
    assert record["state"] == "flipped" and record["self_retirement"] is True
    assert breadcrumb.recovery_refusal(tmp_path, record) == "asynchronous cutover requires resident completion"
