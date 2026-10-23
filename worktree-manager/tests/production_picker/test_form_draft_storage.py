"""Publication barriers precede acknowledgement on first touch and replacement."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from worktree_manager import form_draft_storage as storage

pytestmark = pytest.mark.contract("worktree_manager.form_requests")


def test_native_first_touch_and_replacement_publish_complete_records(tmp_path):
    path = tmp_path / "new-parent" / "new-child" / "request.json"
    for value in ("original", "replacement"):
        storage.write_record(path, {"id": value})
        assert json.loads(path.read_text(encoding="utf-8")) == {"id": value}
    assert list(path.parent.iterdir()) == [path]
    assert list(path.parent.parent.iterdir()) == [path.parent]
    assert list(tmp_path.iterdir()) == [path.parent.parent]


@pytest.mark.parametrize("failed_parent", [None, 0, 1])
def test_posix_file_and_all_parent_barriers_are_ordered_and_fail_closed(
    tmp_path, monkeypatch, failed_parent,
):
    path = tmp_path / "new-parent" / "new-child" / "request.json"
    events = []
    fsync, replace = os.fsync, os.replace

    def file_barrier(fd):
        fsync(fd)
        events.append("file")

    def publish(source, destination):
        assert events == ["file"]
        replace(source, destination)
        events.append("rename")

    def directory_barrier(directory):
        assert path.exists()
        events.append(directory)
        if failed_parent is not None and directory == path.parents[failed_parent]:
            raise OSError("directory flush failed")

    monkeypatch.setattr(storage, "os", SimpleNamespace(
        name="posix", fsync=file_barrier, replace=publish, unlink=os.unlink,
    ))
    monkeypatch.setattr(storage, "_sync_directory", directory_barrier)
    if failed_parent is None:
        storage.write_record(path, {"id": "original"})
        assert events == ["file", "rename", *path.parents]
    else:
        with pytest.raises(OSError, match="directory flush failed"):
            storage.write_record(path, {"id": "original"})
        assert events == ["file", "rename", *list(path.parents)[:failed_parent + 1]]
        # Existing directories left by an uncertain write still receive barriers.
        events.clear()
        failed_parent = None
        storage.write_record(path, {"id": "retry"})
        assert events == ["file", "rename", *path.parents]
    assert list(path.parent.iterdir()) == [path]


def test_posix_directory_handle_is_flushed_and_closed_even_on_failure(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(storage.os, "open", lambda path, flags: calls.append(("open", path, flags)) or 7)
    monkeypatch.setattr(storage.os, "close", lambda fd: calls.append(("close", fd)))

    def denied(fd):
        calls.append(("fsync", fd))
        raise OSError("flush denied")

    monkeypatch.setattr(storage.os, "fsync", denied)
    with pytest.raises(OSError, match="flush denied"):
        storage._sync_directory(tmp_path)
    assert calls == [
        ("open", tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)),
        ("fsync", 7), ("close", 7),
    ]


@pytest.mark.parametrize("failure", [None, "directory", "file"])
def test_windows_publishes_new_directories_and_file_using_write_through(
    tmp_path, monkeypatch, failure,
):
    path = tmp_path / "new-parent" / "new-child" / "request.json"
    calls = []

    def move(source, destination, *, replace):
        calls.append((Path(destination), replace))
        if failure == ("file" if replace else "directory"):
            raise OSError("write-through failed")
        os.replace(source, destination)

    monkeypatch.setattr(storage, "os", SimpleNamespace(name="nt", fsync=os.fsync, unlink=os.unlink))
    monkeypatch.setattr(storage, "_windows_flush_directory", lambda _path: None)
    monkeypatch.setattr(storage, "_windows_move", move)
    if failure:
        with pytest.raises(OSError, match="write-through failed"):
            storage.write_record(path, {"id": "original"})
        assert not path.exists()
        assert not list(tmp_path.rglob("tmp*"))
    else:
        storage.write_record(path, {"id": "original"})
        assert calls == [(path.parents[1], False), (path.parent, False), (path, True)]
        assert json.loads(path.read_text(encoding="utf-8")) == {"id": "original"}


def test_windows_directory_does_not_swallow_a_failed_barrier_after_move(tmp_path, monkeypatch):
    path = tmp_path / "new-parent"

    def failed_after_move(source, destination, **_kwargs):
        os.replace(source, destination)
        raise OSError("barrier failed after rename")

    monkeypatch.setattr(storage, "_windows_move", failed_after_move)
    monkeypatch.setattr(storage, "_windows_flush_directory", lambda _path: None)
    with pytest.raises(OSError, match="barrier failed after rename"):
        storage._windows_directory(path)
    assert path.is_dir()
    pending = path / storage._PENDING_DIRECTORY
    assert pending.read_bytes() == storage._PENDING_CONTENT

    def still_failing(_path):
        raise OSError("parent barrier still failing")

    monkeypatch.setattr(storage, "_windows_flush_directory", still_failing)
    for _ in range(2):
        with pytest.raises(OSError, match="parent barrier still failing"):
            storage._windows_directory(path)
        assert pending.exists()
    flushed = []
    monkeypatch.setattr(storage, "_windows_flush_directory", lambda path: flushed.append(path))
    storage._windows_directory(path)
    assert flushed == [path.parent] and not pending.exists()


def test_windows_pending_marker_is_verified_and_never_silently_discarded(tmp_path, monkeypatch):
    marker = tmp_path / storage._PENDING_DIRECTORY
    marker.write_bytes(b"not owned publication data")
    with pytest.raises(OSError, match="Unrecognized directory publication marker"):
        storage._windows_directory(tmp_path)
    assert marker.read_bytes() == b"not owned publication data"


@pytest.mark.skipif(os.name != "nt", reason="native Windows directory publication")
def test_native_windows_restart_retains_failed_directory_barrier(tmp_path):
    import subprocess
    import sys
    path = tmp_path / "new-parent" / "new-child" / "request.json"
    program = """
import sys
from pathlib import Path
from worktree_manager import form_draft_storage as storage
path = Path(sys.argv[1])
if sys.argv[2] == "first":
    move = storage._windows_move
    def fail_after_move(*args, **kwargs):
        move(*args, **kwargs)
        raise OSError("publication result lost")
    storage._windows_move = fail_after_move
elif sys.argv[2] == "retry-failing":
    def denied(_path):
        raise OSError("parent barrier still failing")
    storage._windows_flush_directory = denied
try:
    storage.write_record(path, {"id": "original"})
except OSError as exc:
    print(str(exc))
    sys.exit(2)
"""
    for mode, status in (("first", 2), ("retry-failing", 2), ("retry-success", 0)):
        result = subprocess.run(
            [sys.executable, "-c", program, str(path), mode], capture_output=True, text=True,
            timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert result.returncode == status, result.stderr
        pending = path.parents[1] / storage._PENDING_DIRECTORY
        if status:
            assert not path.exists() and pending.read_bytes() == storage._PENDING_CONTENT
        else:
            assert not pending.exists()
            assert json.loads(path.read_text(encoding="utf-8")) == {"id": "original"}


def test_windows_unavailable_root_raises_a_filesystem_error_without_recursing(tmp_path, monkeypatch):
    root = Path(tmp_path.anchor)
    monkeypatch.setattr(Path, "is_dir", lambda _path: False)
    with pytest.raises(FileNotFoundError, match="root is unavailable"):
        storage._windows_directory(root / "drafts")


@pytest.mark.parametrize("replace", [False, True])
@pytest.mark.parametrize("success", [False, True])
def test_windows_api_requires_write_through_and_propagates_native_error(
    monkeypatch, replace, success,
):
    import ctypes
    calls = []

    class Move:
        def __call__(self, source, destination, flags):
            calls.append((source, destination, flags))
            return success

    move = Move()
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: SimpleNamespace(MoveFileExW=move), raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(ctypes, "WinError", lambda code: OSError(code, "native move failed"), raising=False)
    if success:
        storage._windows_move("source", "destination", replace=replace)
    else:
        with pytest.raises(OSError, match="native move failed"):
            storage._windows_move("source", "destination", replace=replace)
    assert calls == [("source", "destination", 0x8 | (0x1 if replace else 0))]
    assert move.argtypes and move.restype


@pytest.mark.parametrize("failure", [None, "open", "flush"])
def test_windows_directory_flush_requires_write_access_and_closes_handle(monkeypatch, tmp_path, failure):
    import ctypes
    calls = []

    class Function:
        def __init__(self, result, name):
            self.result, self.name = result, name

        def __call__(self, *args):
            calls.append((self.name, args))
            return self.result

    kernel = SimpleNamespace(
        CreateFileW=Function(ctypes.c_void_p(-1).value if failure == "open" else 7, "open"),
        FlushFileBuffers=Function(failure != "flush", "flush"),
        CloseHandle=Function(True, "close"),
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(ctypes, "WinError", lambda code: OSError(code, "directory flush failed"), raising=False)
    if failure:
        with pytest.raises(OSError, match="directory flush failed"):
            storage._windows_flush_directory(tmp_path)
    else:
        storage._windows_flush_directory(tmp_path)
    assert calls[0] == ("open", (str(tmp_path), 0x40000000, 0x7, None, 3, 0x02000000, None))
    assert calls[1:] == ([] if failure == "open" else [("flush", (7,)), ("close", (7,))])
