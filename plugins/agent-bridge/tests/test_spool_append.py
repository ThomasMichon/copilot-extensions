"""Shared append contract through native files, threads and fresh processes."""

from __future__ import annotations

import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from process_telemetry import append


def _record(writer, index):
    return {"writer": writer, "index": index, "text": "雪é" * (1, 4097, 32769)[index % 3]}


def _assert_records(path, writers, count):
    raw = path.read_bytes()
    assert raw.endswith(b"\n")
    rows = [json.loads(line) for line in raw.splitlines()]
    assert len(rows) == writers * count
    assert {(row["writer"], row["index"]) for row in rows} == {
        (writer, index) for writer in range(writers) for index in range(count)
    }
    assert all(row == _record(row["writer"], row["index"]) for row in rows)


def test_native_thread_appends_are_complete(tmp_path):
    path = tmp_path / "雪-spool.jsonl"
    writers, count = 4, 60
    barrier = threading.Barrier(writers)

    def write(writer):
        for index in range(count):
            data = (json.dumps(_record(writer, index), ensure_ascii=False) + "\n").encode()
            barrier.wait(timeout=10)
            append.append_bytes(path, data)

    with concurrent.futures.ThreadPoolExecutor(max_workers=writers) as pool:
        list(pool.map(write, range(writers)))
    _assert_records(path, writers, count)


def test_native_process_appends_are_complete(tmp_path):
    path = tmp_path / "雪-processes.jsonl"
    source = Path(append.__file__).resolve().parents[1]
    writers, count = 3, 60
    code = """
import json, sys
sys.path.insert(0, sys.argv[1])
from process_telemetry.append import append_bytes
print("ready", flush=True)
assert sys.stdin.readline() == "go\\n"
for index in range(int(sys.argv[4])):
    record = {"writer": int(sys.argv[3]), "index": index,
              "text": "雪é" * (1, 4097, 32769)[index % 3]}
    append_bytes(sys.argv[2], (json.dumps(record, ensure_ascii=False) + "\\n").encode())
"""
    children = []
    try:
        for writer in range(writers):
            children.append(subprocess.Popen(
                [sys.executable, "-I", "-S", "-c", code, str(source), str(path),
                 str(writer), str(count)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            ))
        for child in children:
            assert child.stdout.readline() == "ready\n"
        for child in children:
            child.stdin.write("go\n")
            child.stdin.flush()
        for child in children:
            _, error = child.communicate(timeout=20)
            assert child.returncode == 0, error
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate()
    _assert_records(path, writers, count)


@pytest.mark.parametrize("failure", ["short", "error"])
def test_failed_append_does_not_retry_and_closes_handle(tmp_path, monkeypatch, failure):
    calls = []
    closed = []
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        def short_write(handle, data, size, written, overlapped):
            calls.append(size)
            if failure == "error":
                ctypes.set_last_error(112)  # ERROR_DISK_FULL
                return False
            ctypes.cast(written, ctypes.POINTER(wintypes.DWORD)).contents.value = size - 1
            return True

        monkeypatch.setattr(append._kernel, "WriteFile", short_write)
        close = append._kernel.CloseHandle

        def close_handle(handle):
            closed.append(handle)
            return close(handle)

        monkeypatch.setattr(append._kernel, "CloseHandle", close_handle)
    else:
        def short_write(fd, data):
            calls.append(len(data))
            if failure == "error":
                raise OSError("synthetic disk full")
            return len(data) - 1

        monkeypatch.setattr(append.os, "write", short_write)
        close = append.os.close

        def close_handle(handle):
            closed.append(handle)
            return close(handle)

        monkeypatch.setattr(append.os, "close", close_handle)
    with pytest.raises(OSError) as raised:
        append.append_bytes(tmp_path / "short.jsonl", b'{"id":1}\n')
    if failure == "short":
        assert "partial record may remain" in str(raised.value)
    assert calls == [9]
    assert len(closed) == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows native append access")
def test_windows_uses_append_only_shared_handle(tmp_path, monkeypatch):
    original = append._kernel.CreateFileW
    opened = []

    def create(*args):
        opened.append(args)
        return original(*args)

    monkeypatch.setattr(append._kernel, "CreateFileW", create)
    append.append_bytes(tmp_path / "雪.jsonl", b"one\n")
    append.append_bytes(tmp_path / "雪.jsonl", b"two\n")
    assert (tmp_path / "雪.jsonl").read_bytes() == b"one\ntwo\n"
    assert len(opened) == 2
    assert all(args[1:3] == (0x0004, 0x0007) for args in opened)


def test_append_does_not_create_parent(tmp_path):
    parent = tmp_path / "absent"
    with pytest.raises(OSError):
        append.append_bytes(parent / "spool.jsonl", b"{}\n")
    assert not parent.exists()


def test_embedded_null_path_is_not_truncated(tmp_path):
    path = tmp_path / "must-not-exist"
    with pytest.raises(ValueError, match="null"):
        append.append_bytes(str(path) + "\0ignored", b"{}\n")
    assert not path.exists()
